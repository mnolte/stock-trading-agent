"""DB-level and API tests for the insights snapshots (PR 22 review).

The pure numbers are hand-checked in test_insights_compute; this file
covers the storage path: write → latest reads the row, the tick
backfills missed days, a second run does not duplicate, and finding
history carries over. Plus the two endpoints through the real app.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.config import AppConfig
from app.db.base import get_session_maker
from app.db.models import InsightsSnapshot, Run, Strategy
from app.insights.service import (
    _with_finding_history,
    latest_snapshot,
    tick,
    write_snapshots,
)
from app.market.calendar import WeekdayCalendarProvider
from app.market.clock import NY_TZ, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

# A weekday (Monday) and the following days for backfill scenarios.
RUN_START = datetime(2026, 12, 14, 13, 0)  # naive UTC, Monday
DAY1 = date(2026, 12, 14)  # Monday
DAY2 = date(2026, 12, 15)  # Tuesday


def _seed_run(session: Session) -> None:
    if session.execute(select(Run)).first() is None:
        session.add(Run(started_at=RUN_START, starting_equity=Decimal("10000")))
        session.commit()


def _write(
    session: Session, session_date: date, clock_time: datetime
) -> int:
    _seed_run(session)
    return write_snapshots(
        session,
        config=AppConfig(),
        calendar=WeekdayCalendarProvider(),
        clock=FakeClock(clock_time),
        session_date=session_date,
    )


def test_write_then_latest_returns_the_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        written = _write(session, DAY1, datetime(2026, 12, 15, 9, 0))
        assert written == 3  # live × 3 periods
        latest = latest_snapshot(session, strategy_id=None, period="run")
        assert latest is not None
        assert latest.session_date == DAY1
        assert latest.period == "run"
        data = latest.data or {}
        assert data["trades"] == 0
        assert "headline" in data and "funnel" in data and "slices" in data
        # all three periods exist
        for period in ("run", "last20", "all"):
            assert (
                latest_snapshot(session, strategy_id=None, period=period)
                is not None
            )


def test_tick_backfills_missed_days(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _seed_run(session)
        # Clock is Wednesday after the close: all three days are due.
        clock = FakeClock(datetime(2026, 12, 16, 21, 0, tzinfo=NY_TZ))
        written = tick(
            config=AppConfig(),
            calendar=WeekdayCalendarProvider(),
            clock=clock,
            session_factory=session_factory,
        )
        assert written == 9  # 3 days × 3 periods
        have = {
            r[0]
            for r in session.execute(
                select(InsightsSnapshot.session_date).distinct()
            ).all()
        }
        assert have == {DAY1, DAY2, date(2026, 12, 16)}


def test_second_run_does_not_duplicate(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _write(session, DAY1, datetime(2026, 12, 15, 9, 0))
        before = session.execute(
            select(InsightsSnapshot)
        ).scalars().all()
        assert len(before) == 3
        written = _write(session, DAY1, datetime(2026, 12, 15, 10, 0))
        assert written == 0
        after = session.execute(
            select(InsightsSnapshot)
        ).scalars().all()
        assert len(after) == 3


def test_finding_history_carries_over(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(
            InsightsSnapshot(
                session_date=DAY1,
                strategy_id=None,
                period="run",
                computed_at=datetime(2026, 12, 14, 21, 0),
                data={
                    "findings": [
                        {
                            "dimension": "sector",
                            "slice": "Energy",
                            "status": "finding",
                        }
                    ]
                },
            )
        )
        session.commit()
        data = {
            "findings": [
                {
                    "dimension": "sector",
                    "slice": "Energy",
                    "status": "finding",
                },
                {
                    "dimension": "weekday",
                    "slice": "Mon",
                    "status": "finding",
                },
            ]
        }
        out = _with_finding_history(
            session,
            strategy_id=None,
            period="run",
            session_date=DAY2,
            data=data,
        )
        kept = next(f for f in out["findings"] if f["slice"] == "Energy")
        new = next(f for f in out["findings"] if f["slice"] == "Mon")
        assert kept["first_seen"] == DAY1.isoformat()
        assert kept["held_days"] == 1
        assert kept["new"] is False
        assert new["first_seen"] == DAY2.isoformat()
        assert new["new"] is True


# ----------------------------------------------------------------------
# API


def test_insights_endpoint_normal_and_empty(client: TestClient) -> None:
    with get_session_maker()() as session:
        session.add(
            InsightsSnapshot(
                session_date=DAY1,
                strategy_id=None,
                period="run",
                computed_at=datetime(2026, 12, 14, 21, 0),
                data={"trades": 5, "headline": {}, "findings": [], "slices": {}},
            )
        )
        session.commit()
    ok = client.get("/api/insights?strategy=live&period=run")
    assert ok.status_code == 200
    body = ok.json()
    assert body["empty"] is False
    assert body["snapshot"]["trades"] == 5
    assert body["snapshot"]["session_date"] == DAY1.isoformat()
    assert {s["id"] for s in body["strategies"]} >= {"live"}

    # No snapshot for another period → empty, not an error.
    empty = client.get("/api/insights?strategy=live&period=all")
    assert empty.status_code == 200
    assert empty.json()["empty"] is True
    assert empty.json()["snapshot"] is None


def test_insights_endpoint_unknown_strategy(client: TestClient) -> None:
    missing = client.get("/api/insights?strategy=99999")
    assert missing.status_code == 404
    assert "detail" in missing.json()

    bad_period = client.get("/api/insights?period=week")
    assert bad_period.status_code == 422
    assert isinstance(bad_period.json()["detail"], list)


def test_insights_trades_endpoint(client: TestClient) -> None:
    with get_session_maker()() as session:
        session.add(
            InsightsSnapshot(
                session_date=DAY1,
                strategy_id=None,
                period="run",
                computed_at=datetime(2026, 12, 14, 21, 0),
                data={
                    "trades": 1,
                    "slices": {
                        "sector": {
                            "label": "Sector",
                            "hidden": 0,
                            "rows": [
                                {
                                    "slice": "Energy",
                                    "trades": 1,
                                    "status": "counted",
                                    "trade_ids": [1],
                                }
                            ],
                        }
                    },
                },
            )
        )
        session.commit()
    ok = client.get(
        "/api/insights/trades?strategy=live&period=run"
        "&dimension=sector&slice=Energy"
    )
    assert ok.status_code == 200
    assert ok.json()["trades"] == []

    bad = client.get(
        "/api/insights/trades?strategy=live&period=run"
        "&dimension=sector&slice=Nope"
    )
    assert bad.status_code == 404


def test_insights_shadow_strategy_by_id(
    session_factory: sessionmaker[Session],
) -> None:
    """A strategy's snapshots are stored under its id and served by it."""
    with session_factory() as session:
        _seed_run(session)
        strategy = Strategy(
            name="B · Bigger model",
            kind="rules_only",
            status="active",
            created_at=RUN_START,
        )
        session.add(strategy)
        session.commit()
        sid = strategy.id
        _write(session, DAY1, datetime(2026, 12, 15, 9, 0))
        latest = latest_snapshot(session, strategy_id=sid, period="run")
        assert latest is not None
        # rules only: no calibration or skipped setups
        assert "na" in (latest.data or {}).get("calibration", {})
        assert (latest.data or {}).get("skipped", {}).get("total") == 0
