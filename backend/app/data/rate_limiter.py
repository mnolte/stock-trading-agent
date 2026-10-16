from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass
class RateLimitConfig:
    max_calls: int = 150
    window_seconds: int = 60
    backoff_base_seconds: float = 1.0
    max_retries: int = 3


class RateLimiter:
    """Sliding-window rate limiter for outbound Alpaca calls.

    Keeps total calls under ``max_calls`` per ``window_seconds``. On 429 or
    timeout, retries reads with exponential backoff; order calls are never
    retried (handled by the broker layer in PR 5).
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
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
    async def call_slot(self) -> AsyncIterator[None]:
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
        """Run a synchronous SDK read call under the rate limit, retrying on failure."""
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            async with self.call_slot():
                try:
                    return await asyncio.to_thread(fn, *args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
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
        order is never resent automatically)."""
        async with self.call_slot():
            return await asyncio.to_thread(fn, *args, **kwargs)
