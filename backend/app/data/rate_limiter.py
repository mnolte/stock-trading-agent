from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypeVar

from app.concurrency import SourceGate

T = TypeVar("T")


@dataclass
class RateLimitConfig:
    max_calls: int = 150
    window_seconds: float = 60
    backoff_base_seconds: float = 1.0
    max_retries: int = 3


class RateLimiter:
    """Sliding-window rate limiter for outbound Alpaca calls.

    Keeps total calls under ``max_calls`` per ``window_seconds``. On 429 or
    timeout, retries reads with exponential backoff; order calls are never
    retried (handled by the broker layer in PR 5).

    ``gate`` is the process-wide per-source priority queue (item 078):
    requests wait there — live cycle requests first — before touching
    the window below, which is unchanged.
    """

    def __init__(
        self,
        config: RateLimitConfig | None = None,
        *,
        gate: SourceGate | None = None,
    ) -> None:
        self.config = config or RateLimitConfig()
        self._gate = gate
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def _acquire(self) -> None:
        now = time.monotonic()
        cutoff = now - self.config.window_seconds
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()
        if len(self._calls) >= self.config.max_calls:
            sleep_until = self._calls[0] + self.config.window_seconds
            sleep_for = max(sleep_until - now, 0.0)
            await asyncio.sleep(sleep_for)
        self._calls.append(time.monotonic())

    @asynccontextmanager
    async def call_slot(self, priority: str | None = None) -> AsyncIterator[None]:
        if self._gate is None:
            async with self._lock:
                await self._acquire()
            try:
                yield
            finally:
                pass
            return
        async with self._gate.slot(priority):
            async with self._lock:
                await self._acquire()
            try:
                yield
            finally:
                pass

    # Backwards-compatible alias used by the data client.
    read_call = call_slot

    async def run_read(
        self,
        fn: Callable[..., T],
        *args: object,
        **kwargs: object,
    ) -> T:
        """Run a synchronous SDK read call under the rate limit.

        Only transient failures are retried — timeouts, connection
        errors and 5xx/429 responses. A permanent 4xx (bad request,
        404, auth) surfaces at once.
        """
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            async with self.call_slot():
                try:
                    return await asyncio.to_thread(fn, *args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    status = getattr(exc, "status_code", None) or getattr(
                        exc, "code", None
                    )
                    if (
                        isinstance(status, int)
                        and 400 <= status < 500
                        and status != 429
                    ):
                        raise
                    if attempt == self.config.max_retries:
                        raise
                    delay = self.config.backoff_base_seconds * (2**attempt)
                    await asyncio.sleep(delay)
        raise last_error or RuntimeError("rate limiter exhausted retries")

    async def run_write(
        self,
        fn: Callable[..., T],
        *args: object,
        **kwargs: object,
    ) -> T:
        """Run one order call under the rate limit. Never retried: after a
        timeout or error, reconciliation decides what happened (spec: an
        order is never resent automatically). Orders always count as
        live: protection never queues behind a sync batch."""
        async with self.call_slot(priority="live"):
            return await asyncio.to_thread(fn, *args, **kwargs)
