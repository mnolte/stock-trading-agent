"""Scheduler wiring: misfire grace, coalescing and startup catch-up rules."""

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from app.api.main import startup_catch_up_allowed
from app.config import AppConfig, get_config
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_cycle_jobs_coalesce_with_configured_misfire_grace(
    client: TestClient,
) -> None:
    """Every scheduled cycle job carries the spec's misfire handling."""
    scheduler = cast(FastAPI, client.app).state.scheduler
    grace = int(get_config().get("schedule", "misfire_grace_seconds"))
    jobs = [j for j in scheduler.get_jobs() if j.id.startswith("cycle-")]
    assert len(jobs) == len(get_config().get("schedule", "cycles"))
    assert jobs, "expected at least one scheduled cycle job"
    for job in jobs:
        assert job.misfire_grace_time == grace
        assert job.coalesce is True


def test_backtests_tick_tolerates_a_late_wakeup(client: TestClient) -> None:
    """The 10-second tick still runs when up to one interval late."""
    scheduler = cast(FastAPI, client.app).state.scheduler
    job = next(j for j in scheduler.get_jobs() if j.id == "backtests-tick")
    assert job.misfire_grace_time == 10
    assert job.coalesce is True


def test_every_job_runs_late_within_the_misfire_grace(
    client: TestClient,
) -> None:
    """A stalled loop delays a run instead of silently skipping it: the
    configured grace replaces APScheduler's 1-second default everywhere."""
    scheduler = cast(FastAPI, client.app).state.scheduler
    grace = int(get_config().get("schedule", "misfire_grace_seconds"))
    jobs = scheduler.get_jobs()
    assert jobs, "expected at least one scheduled job"
    for job in jobs:
        expected = 10 if job.id == "backtests-tick" else grace
        assert job.misfire_grace_time == expected, job.id
        assert job.coalesce is True, job.id


def test_startup_catch_up_only_in_simulated_mode() -> None:
    """Paper mode never runs a missed cycle late, not even at startup."""
    config = AppConfig()
    config.env.mode = "paper"
    assert not startup_catch_up_allowed(config)
    config.env.mode = "simulated"
    assert startup_catch_up_allowed(config)


@pytest.mark.anyio
@pytest.mark.parametrize("failed", [None, "readiness", "insights"])
async def test_after_close_snapshots_do_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failed: str | None,
) -> None:
    from app.api.main import run_after_close_jobs

    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    order: list[str] = []
    worker_threads: list[int] = []
    responsive: list[bool] = []
    published: list[set[str]] = []

    def tick(name: str) -> Callable[..., None]:
        def run(**kwargs: Any) -> None:
            order.append(name)
            worker_threads.append(threading.get_ident())
            heartbeat = threading.Event()
            loop.call_soon_threadsafe(heartbeat.set)
            responsive.append(heartbeat.wait(timeout=0.5))
            if failed == name:
                raise RuntimeError(f"{name} failed")
            kwargs["publish"]({name})

        return run

    def publish(topics: set[str]) -> None:
        assert threading.get_ident() == loop_thread
        published.append(topics)

    async def fill() -> None:
        assert threading.get_ident() == loop_thread
        order.append("after_exit")

    after_exit = AsyncMock()
    after_exit.fill.side_effect = fill
    monkeypatch.setattr("app.readiness.service.tick", tick("readiness"))
    monkeypatch.setattr("app.insights.service.tick", tick("insights"))
    with caplog.at_level(logging.ERROR):
        await run_after_close_jobs(
            AppConfig(), cast(Any, None), cast(Any, None),
            cast(Any, None), publish, after_exit,
        )

    assert order == ["readiness", "insights", "after_exit"]
    assert all(thread != loop_thread for thread in worker_threads)
    assert responsive == [True, True]
    assert published == [{name} for name in ("readiness", "insights") if name != failed]
    after_exit.fill.assert_awaited_once()
    if failed is not None:
        assert f"{failed} {'tick' if failed == 'readiness' else 'job'} failed" in caplog.text
    else:
        assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
