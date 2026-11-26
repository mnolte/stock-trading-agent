"""Sync-run records and the Universe → Data sync API (item 077).

All fakes, no network: the ``SyncRuns`` writer is exercised directly on a
scratch database, the tracked fetch wrappers on scripted results, and the
two endpoints on the simulated app. A partial run must keep its failures,
a running job must show its own progress, and Retry must refuse during a
cycle.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from app.config import AppConfig
from app.data.models import DataResult
from app.db.base import get_session_maker
from app.db.models import Cycle, SyncRunRow
from app.market.clock import FakeClock
from app.syncs import (
    SyncRuns,
    tracked_fetch,
    tracked_fetches,
    tracked_symbols,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

# Thursday 2026-10-15, 08:30 New York.
T0 = datetime(2026, 10, 15, 12, 30, tzinfo=UTC)


def make_syncs(
    session_factory: sessionmaker[Session],
) -> tuple[SyncRuns, FakeClock]:
    clock = FakeClock(T0)
    return SyncRuns(session_factory, clock, AppConfig()), clock


def latest_run(
    session_factory: sessionmaker[Session], job: str
) -> SyncRunRow | None:
    with session_factory() as session:
        return session.execute(
            select(SyncRunRow)
            .where(SyncRunRow.job == job)
            .order_by(SyncRunRow.id.desc())
            .limit(1)
        ).scalar_one_or_none()


def test_partial_run_stores_failures(
    session_factory: sessionmaker[Session],
) -> None:
    """A run with some failed symbols ends ``partial`` and keeps each
    failure's symbol, plain-words error and retry time."""
    syncs, _ = make_syncs(session_factory)
    run = syncs.begin("history", total=3)
    run.progress(1, 3)
    run.fail("AAA", "Alpaca: symbol not found")
    run.progress(3, 3)
    run.finish()

    row = latest_run(session_factory, "history")
    assert row is not None
    assert row.status == "partial"
    assert row.done == 3
    assert row.total == 3
    assert row.finished_at is not None
    # "history" is on demand: no scheduled retry time.
    assert row.failures == [
        {
            "what": "AAA",
            "error": "Alpaca: symbol not found",
            "next_retry": None,
        }
    ]


def test_all_failed_run_is_failed(
    session_factory: sessionmaker[Session],
) -> None:
    syncs, _ = make_syncs(session_factory)
    run = syncs.begin("daily_bars", total=2)
    run.progress(2, 2)
    run.fail("AAA", "rate limited")
    run.fail("BBB", "rate limited")
    run.finish()

    row = latest_run(session_factory, "daily_bars")
    assert row is not None
    assert row.status == "failed"
    # daily_bars runs at the calendar refresh — a failure names the next
    # scheduled New York time.
    assert row.failures is not None
    assert row.failures[0]["next_retry"] is not None


def test_abort_marks_run_failed(
    session_factory: sessionmaker[Session],
) -> None:
    syncs, _ = make_syncs(session_factory)
    run = syncs.begin("fred", total=1)
    run.abort("FRED: request timed out")
    run.finish()

    row = latest_run(session_factory, "fred")
    assert row is not None
    assert row.status == "failed"
    assert row.failures is not None
    assert row.failures[0]["what"] == "fred"
    assert "timed out" in row.failures[0]["error"]


def test_running_row_reports_progress(
    session_factory: sessionmaker[Session],
) -> None:
    """While a job runs its row shows done/total and status ``running``;
    the history ETA stays null until five symbols are done, then comes
    from the job's own pace."""
    syncs, clock = make_syncs(session_factory)
    run = syncs.begin("history", total=10)
    run.progress(2, 10)

    row = latest_run(session_factory, "history")
    assert row is not None
    assert row.status == "running"
    assert row.done == 2
    assert row.total == 10
    assert row.finished_at is None

    progress = syncs.history_progress()
    assert progress == {"ready": 2, "total": 10, "eta_seconds": None}

    # Five done with a measurable pace → a time left appears. flush()
    # pushes the throttled progress write now.
    clock.advance(timedelta(seconds=30))
    run.progress(6, 10)
    run.flush()
    progress = syncs.history_progress()
    assert progress is not None
    assert progress["ready"] == 6
    assert progress["eta_seconds"] == pytest.approx(20.0)

    run.finish()
    assert syncs.history_progress() is None


def test_runs_pruned_after_30_days(
    session_factory: sessionmaker[Session],
) -> None:
    syncs, clock = make_syncs(session_factory)
    run = syncs.begin("ecb", total=1)
    run.finish()
    old = latest_run(session_factory, "ecb")
    assert old is not None
    with session_factory() as session:
        stored = session.get(SyncRunRow, old.id)
        assert stored is not None
        stored.started_at = stored.started_at - timedelta(days=31)
        session.commit()
    syncs.begin("ecb", total=1)
    assert latest_run(session_factory, "ecb") is not None
    with session_factory() as session:
        remaining = session.execute(
            select(SyncRunRow).where(SyncRunRow.job == "ecb")
        ).scalars().all()
    # The 31-day-old row is gone; only the fresh run remains.
    assert len(remaining) == 1


async def test_tracked_symbols_partial(
    session_factory: sessionmaker[Session],
) -> None:
    """The per-symbol wrapper writes progress as each symbol lands and
    keeps the failed symbol's error."""
    syncs, _ = make_syncs(session_factory)

    async def fetch(
        symbols: list[str],
        on_symbol: Callable[[str, DataResult[int]], None] | None,
    ) -> dict[str, DataResult[int]]:
        results = {}
        for sym in symbols:
            res = DataResult(
                data=0 if sym == "BAD" else 1,
                error="no data" if sym == "BAD" else None,
                source="fake",
            )
            results[sym] = res
            if on_symbol is not None:
                on_symbol(sym, res)
        return results

    await tracked_symbols(
        syncs, "daily_bars", ["OK1", "BAD", "OK2"], fetch, "daily bars"
    )

    row = latest_run(session_factory, "daily_bars")
    assert row is not None
    assert row.status == "partial"
    assert row.done == 3
    assert row.total == 3
    assert row.failures is not None
    assert row.failures[0]["what"] == "BAD"
    assert row.failures[0]["error"] == "no data"


async def test_tracked_fetch_error_fails_run(
    session_factory: sessionmaker[Session],
) -> None:
    syncs, _ = make_syncs(session_factory)

    async def fetch() -> DataResult[int]:
        return DataResult(data=0, error="ECB: timeout", source="ecb")

    await tracked_fetch(syncs, "ecb", fetch)

    row = latest_run(session_factory, "ecb")
    assert row is not None
    assert row.status == "failed"
    assert row.failures is not None
    assert row.failures[0]["what"] == "ecb"


async def test_tracked_fetches_one_failure_is_partial(
    session_factory: sessionmaker[Session],
) -> None:
    syncs, _ = make_syncs(session_factory)

    async def ok() -> DataResult[int]:
        return DataResult(data=1, source="fred")

    async def bad() -> DataResult[int]:
        return DataResult(data=0, error="FRED: 503", source="fred")

    await tracked_fetches(
        syncs,
        "fred",
        [("VIX (FRED)", ok), ("release calendar", bad)],
    )

    row = latest_run(session_factory, "fred")
    assert row is not None
    assert row.status == "partial"
    assert row.done == 2
    assert row.failures is not None
    assert row.failures[0]["what"] == "release calendar"


def _insert_run(
    job: str,
    status: str,
    failures: list[dict[str, object]] | None,
) -> None:
    with get_session_maker()() as session:
        now = datetime.now(UTC).replace(tzinfo=None)
        session.add(
            SyncRunRow(
                job=job,
                started_at=now - timedelta(minutes=5),
                finished_at=now,
                status=status,
                done=10,
                total=10,
                failures=failures,
            )
        )
        session.commit()


def test_sync_endpoint_lists_latest_runs(client: TestClient) -> None:
    """GET /api/universe/sync returns the latest run per job plus the
    history progress block — the simulated boot already ran the daily
    refresh, so rows exist."""
    response = client.get("/api/universe/sync")
    assert response.status_code == 200
    body = response.json()
    assert "jobs" in body
    assert "history" in body
    assert "max_requests" in body
    jobs = {j["job"]: j for j in body["jobs"]}
    assert "daily_bars" in jobs
    for job in jobs.values():
        assert job["status"] in ("running", "ok", "partial", "failed")
        assert "started_at" in job and "finished_at" in job


def test_sync_retry_unknown_job(client: TestClient) -> None:
    response = client.post("/api/universe/sync/bogus/retry")
    assert response.status_code == 422


def test_sync_retry_without_failures(client: TestClient) -> None:
    _insert_run("fred", "ok", None)
    response = client.post("/api/universe/sync/fred/retry")
    assert response.status_code == 422


def test_sync_retry_refused_during_cycle(client: TestClient) -> None:
    """A running cycle makes Retry answer 409 — refused, never queued."""
    _insert_run(
        "fred",
        "failed",
        [{"what": "VIX (FRED)", "error": "FRED: down", "next_retry": None}],
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    with get_session_maker()() as session:
        session.add(
            Cycle(
                kind="cycle",
                scheduled_time=now,
                started_at=now,
                status="running",
            )
        )
        session.commit()
    response = client.post("/api/universe/sync/fred/retry")
    assert response.status_code == 409


def test_sync_retry_reruns_failed_symbols(client: TestClient) -> None:
    """Retry accepts, then a fresh run row appears for the job — the
    background fetch goes through the same tracked wrapper."""
    _insert_run(
        "daily_bars",
        "partial",
        [
            {
                "what": "FAKE1",
                "error": "Alpaca: symbol not found",
                "next_retry": None,
            }
        ],
    )
    response = client.post("/api/universe/sync/daily_bars/retry")
    assert response.status_code == 202
    assert response.json()["accepted"] is True

    # The spawned retry writes its own row; poll briefly for it.
    import time

    deadline = time.monotonic() + 15
    row = None
    while time.monotonic() < deadline:
        with get_session_maker()() as session:
            rows = session.execute(
                select(SyncRunRow)
                .where(SyncRunRow.job == "daily_bars")
                .order_by(SyncRunRow.id.desc())
                .limit(2)
            ).scalars().all()
        if len(rows) >= 2:
            row = rows[0]
            break
        time.sleep(0.2)
    assert row is not None, "retry never wrote a sync_runs row"
    assert row.status in ("running", "ok", "partial", "failed")
