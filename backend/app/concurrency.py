"""Concurrent request batches (docs/SPEC.md, Concurrent requests).

A batch is a set of requests whose answers don't depend on each other —
the model calls of one wave of a cycle, the daily-bar fetches of one
refresh. ``run_batch`` keeps at most ``concurrency.max_requests`` sends
in flight, returns the results in the order of ``items`` (a value or the
exception it raised — one failure never cancels the others) and logs one
INFO line per batch.

Live goes first: every send carries a priority (a context variable
``run_batch`` sets per item). A process-wide :class:`SourceGate` per
source sits in front of the source's rate limiter — the limiter itself
is unchanged — so a live cycle request is sent before a waiting sync,
shadow, review, Ask or backtest request to the same source. Requests
that are not in a batch (orders, single fetches, position checks) run
at the default priority, below live batches; the cycle scopes itself to
``live`` so its own unbatched calls count as live too.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Sequence,
)
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TypeVar, cast

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Batch priorities, highest first. Only "live" outranks the rest: the
# spec orders live cycle requests before sync, shadow, review, Ask and
# backtest ones and leaves those unordered among themselves.
_PRIORITY_RANK = {"live": 0}
_DEFAULT_PRIORITY = "normal"

# The priority of the send currently running on this task; read by the
# source gates inside the rate limiter / model client.
_PRIORITY: ContextVar[str] = ContextVar(
    "sa_request_priority", default=_DEFAULT_PRIORITY
)


def current_priority() -> str:
    """The priority of the request this task is sending."""
    return _PRIORITY.get()


@contextmanager
def priority_scope(priority: str) -> Iterator[None]:
    """Mark every request sent inside the block with ``priority``."""
    token = _PRIORITY.set(priority)
    try:
        yield
    finally:
        _PRIORITY.reset(token)


def _rank(priority: str) -> int:
    return _PRIORITY_RANK.get(priority, 1)


class SourceGate:
    """Per-source priority queue in front of the source's rate limiter.

    At most ``limit`` requests are inside ( limiter + send ) at once —
    the same number that sizes the HTTP pools, so parallel requests never
    overflow them. Waiters are served live-first, then in arrival order.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._in_flight = 0
        self._seq = 0
        self._waiters: list[tuple[int, int]] = []
        self._cond = asyncio.Condition()

    async def acquire(self, priority: str | None = None) -> None:
        rank = _rank(priority or current_priority())
        async with self._cond:
            self._seq += 1
            seq = self._seq
            self._waiters.append((rank, seq))
            while True:
                # Only the best waiter may take a slot: lowest rank
                # (live first), then earliest arrival.
                if (
                    self._waiters
                    and min(self._waiters) == (rank, seq)
                    and self._in_flight < self.limit
                ):
                    self._waiters.remove((rank, seq))
                    self._in_flight += 1
                    return
                await self._cond.wait()

    async def release(self) -> None:
        async with self._cond:
            self._in_flight -= 1
            self._cond.notify_all()

    @asynccontextmanager
    async def slot(self, priority: str | None = None) -> AsyncIterator[None]:
        await self.acquire(priority)
        try:
            yield
        finally:
            await self.release()


_GATES: dict[str, SourceGate] = {}


def gate_for(name: str, *, limit: int) -> SourceGate:
    """The process-wide gate for one source (e.g. "alpaca", "model:openai").

    ``limit`` applies when the gate is first created; the setting is read
    at start, so a later change needs a restart anyway.
    """
    gate = _GATES.get(name)
    if gate is None:
        gate = SourceGate(limit)
        _GATES[name] = gate
    return gate


def reset_gates() -> None:
    """Drop every gate — tests only."""
    _GATES.clear()


@dataclass(frozen=True)
class BatchStats:
    """What one batch did; stored on the cycle and shown in the GUI."""

    kind: str
    size: int
    max_in_flight: int
    wall_seconds: float
    failures: int


class BatchResults(list[T]):
    """``run_batch``'s return: results in item order, plus the stats."""

    def __init__(self, items: list[T], stats: BatchStats) -> None:
        super().__init__(items)
        self.stats = stats


async def run_batch[T](
    items: Sequence[T],
    send: Callable[[T], Awaitable[object]],
    *,
    limit: int,
    kind: str = "batch",
    priority: str = _DEFAULT_PRIORITY,
) -> BatchResults[T]:
    """Send every item at once, at most ``limit`` in flight.

    ``send`` is the whole request — the source's rate limiter and
    timeout stay inside it. Returns a value or the exception per item,
    in ``items`` order; a failure never cancels the others.
    """
    results: list[object] = [None] * len(items)
    semaphore = asyncio.Semaphore(max(1, limit))
    in_flight = 0
    max_in_flight = 0
    failures = 0
    started = time.monotonic()

    async def worker(index: int, item: T) -> None:
        nonlocal in_flight, max_in_flight, failures
        async with semaphore:
            token = _PRIORITY.set(priority)
            try:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                try:
                    results[index] = await send(item)
                except Exception as exc:  # noqa: BLE001 - isolated per item
                    failures += 1
                    results[index] = exc
                    logger.exception(
                        "batch %s: item %d failed", kind, index
                    )
                finally:
                    in_flight -= 1
            finally:
                _PRIORITY.reset(token)

    await asyncio.gather(*(worker(i, item) for i, item in enumerate(items)))
    wall = time.monotonic() - started
    stats = BatchStats(
        kind=kind,
        size=len(items),
        max_in_flight=max_in_flight,
        wall_seconds=wall,
        failures=failures,
    )
    logger.info(
        "batch %s: %d items, up to %d in flight, %.2fs, %d failures",
        kind,
        stats.size,
        stats.max_in_flight,
        stats.wall_seconds,
        stats.failures,
    )
    return BatchResults(cast("list[T]", results), stats)
