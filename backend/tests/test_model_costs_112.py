"""Item 112 — Cost per session buckets: the 31-bar rule (session / ISO
week / month / quarter), a row for every bucket with $0 included, the
bucket's first and last session dates and session count, and the
per-session summary (docs/SPEC.md → Model costs → Cost per session)."""

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import Bar1d, LlmCall
from app.insights.model_costs import bucket_unit, model_costs
from fastapi.testclient import TestClient
from sqlalchemy import delete


def _weekdays(first: date, count: int) -> list[date]:
    """``count`` consecutive weekdays from ``first`` (a Monday)."""
    days: list[date] = []
    day = first
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


class TestBucketUnit:
    def test_31_sessions_is_session_32_is_week(self) -> None:
        days = _weekdays(date(2026, 9, 7), 40)
        assert bucket_unit(days[:31]) == "session"
        assert bucket_unit(days[:32]) == "week"

    def test_empty_is_session(self) -> None:
        assert bucket_unit([]) == "session"

    def test_31_weeks_is_week_32_is_month(self) -> None:
        # 32 dated sessions is over the session cap; the span decides.
        first = date(2026, 1, 5)  # a Monday
        for weeks, expected in ((31, "week"), (32, "month")):
            last = first + timedelta(weeks=weeks - 1)
            sessions = [first, last] + [
                first + timedelta(days=i) for i in range(1, 31)
            ]
            assert len(sessions) > 31
            assert bucket_unit(sessions) == expected

    def test_past_31_months_is_quarter(self) -> None:
        first = date(2026, 1, 1)
        for months, expected in ((31, "month"), (32, "quarter")):
            last = date(first.year + (first.month - 1 + months - 1) // 12,
                        (first.month - 1 + months - 1) % 12 + 1, 28)
            span = (last - first).days
            sessions = [first, last] + [
                first + timedelta(days=span * i // 31) for i in range(1, 31)
            ]
            assert bucket_unit(sessions) == expected


def _seed_session(session: Any, day: date) -> None:
    session.add(
        Bar1d(
            symbol="SPY", date=day,
            open=Decimal(1), high=Decimal(1), low=Decimal(1),
            close=Decimal(1), volume=1,
        )
    )


def _seed_call(
    session: Any,
    day: date,
    cost: str,
    *,
    source: str = "live",
    kind: str = "decision",
    model: str = "gpt-4.1-mini",
) -> None:
    session.add(
        LlmCall(
            sent_at=datetime(day.year, day.month, day.day, 15, 0),
            session_date=day,
            source=source,
            kind=kind,
            model=model,
            prompt_version="decision.v1",
            tokens_in=1000,
            tokens_out=100,
            cost=Decimal(cost),
            cost_source="priced",
            outcome="success",
            reasons=[],
        )
    )


@pytest.fixture()
def clean_costs(client: TestClient) -> Iterator[None]:
    with get_session_maker()() as session:
        session.execute(delete(LlmCall))
        session.execute(delete(Bar1d))
        session.commit()
    yield


class TestSessionBuckets:
    def test_every_session_gets_a_row_zero_included(
        self, client: TestClient, clean_costs: None
    ) -> None:
        days = [date(2026, 12, 9), date(2026, 12, 10), date(2026, 12, 11)]
        with get_session_maker()() as session:
            for d in days:
                _seed_session(session, d)
            _seed_call(session, days[0], "0.10")
            _seed_call(session, days[2], "0.30", source="ask", kind="ask")
            session.commit()
            data = model_costs(
                get_config(), session, "all", today=date(2026, 12, 15)
            )
        assert data["per_session_unit"] == "session"
        rows = data["per_session"]
        assert [r["key"] for r in rows] == [d.isoformat() for d in days]
        assert [r["label"] for r in rows] == ["9", "10", "11"]
        # The middle session has no calls and still gets a $0 row.
        assert rows[1]["live"] == "0" and rows[1]["sessions"] == 1
        assert rows[0]["from"] == rows[0]["to"] == "2026-12-09"
        assert Decimal(rows[0]["live"]) == Decimal("0.10")
        assert Decimal(rows[2]["ask"]) == Decimal("0.30")
        # Summary: average per session and the highest bucket.
        summary = data["per_session_summary"]
        assert Decimal(summary["average_per_session"]) == Decimal("0.1333")
        assert summary["highest"]["key"] == "2026-12-11"
        assert Decimal(summary["highest"]["total"]) == Decimal("0.30")
        assert summary["highest"]["largest_part"] == "ask"

    def test_week_buckets_cover_the_span_with_zero_rows(
        self, client: TestClient, clean_costs: None
    ) -> None:
        # 32 weekdays Mon 7 Sep – Tue 20 Oct → 7 ISO weeks of sessions,
        # unit "week".
        days = _weekdays(date(2026, 9, 7), 32)
        with get_session_maker()() as session:
            for d in days:
                _seed_session(session, d)
            _seed_call(session, days[0], "0.20")
            _seed_call(session, days[-1], "0.50", source="shadow", kind="decision")
            _seed_call(
                session, days[10], "0.10",
                source="review", kind="weekly",
            )
            # A call whose session_date has no SPY bar still lands in
            # its week — no cost is dropped, and the week gets a row.
            _seed_call(session, date(2026, 10, 26), "0.05")
            session.commit()
            data = model_costs(
                get_config(), session, "all", today=date(2026, 10, 27)
            )
        assert data["per_session_unit"] == "week"
        rows = data["per_session"]
        # Sessions span W37–W43; the stray call adds W44.
        assert len(rows) <= 31
        assert [r["key"] for r in rows] == [
            "2026-W37", "2026-W38", "2026-W39", "2026-W40",
            "2026-W41", "2026-W42", "2026-W43", "2026-W44",
        ]
        first, last = rows[0], rows[-1]
        assert first["label"] == "W37" and last["label"] == "W44"
        # First/last dates are the bucket's first and last session;
        # a bucket without sessions falls back to its calendar bounds.
        assert first["from"] == "2026-09-07" and first["to"] == "2026-09-11"
        assert first["sessions"] == 5
        assert rows[6]["sessions"] == 2  # Mon 19 + Tue 20 Oct
        assert last["sessions"] == 0
        assert last["from"] == "2026-10-26" and last["to"] == "2026-11-01"
        assert Decimal(last["live"]) == Decimal("0.05")
        # Every week in the span has a row, $0 included.
        assert all(set(r) >= {"live", "shadows", "reviews", "ask"} for r in rows)
        w39 = next(r for r in rows if r["key"] == "2026-W39")
        assert Decimal(w39["reviews"]) == Decimal("0.10")
        # Summary over all 32 sessions.
        summary = data["per_session_summary"]
        total = Decimal("0.20") + Decimal("0.50") + Decimal("0.10") + Decimal("0.05")
        assert Decimal(summary["average_per_session"]) == (
            total / 32
        ).quantize(Decimal("0.0001"))
        assert summary["highest"]["key"] == "2026-W43"
        assert Decimal(summary["highest"]["total"]) == Decimal("0.50")
        assert summary["highest"]["largest_part"] == "shadows"

    def test_month_buckets(
        self, client: TestClient, clean_costs: None
    ) -> None:
        # >31 weeks of sessions → unit "month"; sparse calls.
        days = [
            date(2026, 1, 5) + timedelta(days=7 * i)
            for i in range(40)
        ]  # Mondays Jan – Oct, 40 sessions
        with get_session_maker()() as session:
            for d in days:
                _seed_session(session, d)
            _seed_call(session, days[0], "1.00")
            _seed_call(session, days[-1], "2.00", source="ask", kind="ask")
            session.commit()
            data = model_costs(
                get_config(), session, "all", today=date(2026, 10, 20)
            )
        assert data["per_session_unit"] == "month"
        rows = data["per_session"]
        assert len(rows) == 10  # Jan – Oct
        assert rows[0]["label"] == "Jan" and rows[-1]["label"] == "Oct"
        assert all(r["sessions"] >= 1 for r in rows)
        # A month with no calls is still a $0 row.
        assert any(
            Decimal(r["live"]) == 0 and Decimal(r["ask"]) == 0
            for r in rows[1:-1]
        )
        summary = data["per_session_summary"]
        assert summary["highest"]["key"] == rows[-1]["key"]
        assert summary["highest"]["largest_part"] == "ask"

    def test_no_calls_still_lists_buckets(
        self, client: TestClient, clean_costs: None
    ) -> None:
        days = [date(2026, 12, 9), date(2026, 12, 10)]
        with get_session_maker()() as session:
            for d in days:
                _seed_session(session, d)
            session.commit()
            data = model_costs(
                get_config(), session, "all", today=date(2026, 12, 15)
            )
        assert len(data["per_session"]) == 2
        summary = data["per_session_summary"]
        assert summary["average_per_session"] == "0.0000"
        assert summary["highest"] is None
