"""run_batch and the per-source priority gate (item 078).

No network: sends are plain coroutines.
"""

import asyncio
import logging
import random
from collections.abc import Generator

import pytest
from app.concurrency import (
    SourceGate,
    gate_for,
    reset_gates,
    run_batch,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean_gates() -> Generator[None, None, None]:
    reset_gates()
    yield
    reset_gates()


async def test_run_batch_preserves_order_with_random_latency() -> None:
    rng = random.Random(78)
    latencies = [rng.random() * 0.05 for _ in range(12)]

    async def send(item: int) -> int:
        await asyncio.sleep(latencies[item])
        return item * 10

    results = await run_batch(list(range(12)), send, limit=4, kind="t.order")
    assert list(results) == [i * 10 for i in range(12)]
    assert results.stats.size == 12
    assert 1 < results.stats.max_in_flight <= 4
    assert results.stats.failures == 0
    assert results.stats.wall_seconds > 0


async def test_run_batch_never_exceeds_limit() -> None:
    in_flight = 0
    max_seen = 0

    async def send(item: int) -> int:
        nonlocal in_flight, max_seen
        in_flight += 1
        max_seen = max(max_seen, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return item

    results = await run_batch(list(range(10)), send, limit=3, kind="t.limit")
    assert list(results) == list(range(10))
    assert max_seen == 3
    assert results.stats.max_in_flight == 3


async def test_run_batch_isolates_failures() -> None:
    async def send(item: int) -> int:
        if item == 2:
            raise ValueError("boom")
        await asyncio.sleep(0.005)
        return item

    results = await run_batch(list(range(5)), send, limit=2, kind="t.fail")
    items: list[object] = list(results)
    assert items[0] == 0
    assert items[1] == 1
    assert isinstance(items[2], ValueError)
    assert items[3] == 3
    assert items[4] == 4
    assert results.stats.failures == 1


async def test_run_batch_logs_one_info_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def send(item: int) -> int:
        return item

    with caplog.at_level(logging.INFO, logger="app.concurrency"):
        await run_batch([1, 2, 3], send, limit=2, kind="t.log")
    lines = [r for r in caplog.records if "t.log" in r.getMessage()]
    assert len(lines) == 1
    message = lines[0].getMessage()
    assert "3 items" in message
    assert "in flight" in message
    assert "failures" in message


async def test_source_gate_serves_live_before_normal() -> None:
    """A live request waiting on the gate goes before a normal one that
    arrived earlier (docs/SPEC.md, Concurrent requests — live first)."""
    gate = SourceGate(limit=1)
    order: list[str] = []

    async def hold() -> None:
        async with gate.slot(priority="normal"):
            order.append("held")
            await asyncio.sleep(0.05)

    async def normal() -> None:
        async with gate.slot(priority="normal"):
            order.append("normal")

    async def live() -> None:
        async with gate.slot(priority="live"):
            order.append("live")

    await asyncio.gather(hold(), normal(), live())
    # The slot frees once; the live waiter was queued behind the normal
    # one but wins the next slot.
    assert order == ["held", "live", "normal"]


async def test_source_gate_limits_in_flight() -> None:
    gate = gate_for("test-source", limit=2)
    in_flight = 0
    max_seen = 0

    async def send() -> None:
        nonlocal in_flight, max_seen
        async with gate.slot():
            in_flight += 1
            max_seen = max(max_seen, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

    await asyncio.gather(*(send() for _ in range(6)))
    assert max_seen == 2
