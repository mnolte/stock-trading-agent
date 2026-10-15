"""Scheduler wiring: misfire grace, coalescing and startup catch-up rules."""

from typing import cast

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


def test_startup_catch_up_only_in_simulated_mode() -> None:
    """Paper mode never runs a missed cycle late, not even at startup."""
    config = AppConfig()
    config.env.mode = "paper"
    assert not startup_catch_up_allowed(config)
    config.env.mode = "simulated"
    assert startup_catch_up_allowed(config)
