"""Dashboard "today" = the current or last New York session
(docs/SPEC.md, GUI — Dashboard at scale and off-hours).

On a Saturday the dashboard shows Friday's session: the feed, the
model-call tile, the latest decision (marked out-of-session) and the
cycle strip's session summary.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, cast

from app.data.models import CalendarDay
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Outcome, Position
from app.market.calendar import StaticCalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import select


def _clock(client: TestClient) -> FakeClock:
    return cast(FakeClock, cast(Any, client.app).state.clock)


def _calendar(client: TestClient, days: list[CalendarDay]) -> None:
    cast(Any, client.app).state.calendar_store = StaticCalendarProvider(days)


def _day(day: date) -> CalendarDay:
    return CalendarDay(
        date=day,
        open=datetime.combine(day, time(9, 30), tzinfo=NY_TZ),
        close=datetime.combine(day, time(16, 0), tzinfo=NY_TZ),
    )


_SKIP_OUTPUT: dict[str, object] = {
    "action": "skip",
    "confidence": 0.4,
    "thesis": "flat tape",
}


def _decision(
    at_utc: datetime,
    *,
    symbol: str = "XOM",
    final_action: str = "skip",
    model: str = "fake-decision",
    no_call_reason: str | None = None,
    model_output: dict[str, object] | None = _SKIP_OUTPUT,
    cost: str | None = None,
) -> int:
    naive = at_utc.astimezone(UTC).replace(tzinfo=None)
    with get_session_maker()() as session:
        cycle = session.execute(
            select(Cycle).where(Cycle.scheduled_time == naive)
        ).scalar_one_or_none()
        if cycle is None:
            cycle = Cycle(
                kind="cycle",
                scheduled_time=naive,
                started_at=naive,
                finished_at=naive,
                status="done",
            )
            session.add(cycle)
            session.flush()
        decision = Decision(
            cycle_id=cycle.id,
            symbol=symbol,
            context="candidate",
            input_snapshot={"symbol": symbol, "price": 120.5},
            rules_result={"status": "pass", "reason": None, "checks": []},
            model_output=model_output,
            validation_result={
                "valid": True,
                "model_action": "skip",
                "final_action": final_action,
            },
            final_action=final_action,
            no_call_reason=no_call_reason,
            prompt_version="decision.v1",
            prompt_hash="abc123",
            model=model,
            cost=cost,
        )
        session.add(decision)
        session.flush()
        session.add(Outcome(decision_id=decision.id))
        session.commit()
        return decision.id


def _position(symbol: str = "MSFT") -> None:
    with get_session_maker()() as session:
        session.add(
            Position(
                symbol=symbol,
                qty=4,
                avg_entry_price=Decimal("418.20"),
                opened_at=datetime(2026, 10, 16, 13, 46),
                current_stop_price=Decimal("409.60"),
            )
        )
        session.commit()


def test_saturday_shows_fridays_session(client: TestClient) -> None:
    # Friday 16 Oct had two cycles; Saturday 17 Oct 12:00 UTC the dashboard
    # falls back to Friday's session.
    _calendar(
        client,
        [_day(date(2026, 10, 16)), _day(date(2026, 10, 19))],
    )
    _clock(client).set(datetime(2026, 10, 17, 12, 0, tzinfo=UTC))
    _decision(datetime(2026, 10, 16, 14, 46, tzinfo=UTC), model="none",
              final_action="no_call", no_call_reason="rules",
              model_output=None)
    _decision(datetime(2026, 10, 16, 15, 46, tzinfo=UTC), symbol="AAPL",
              cost="0.001")
    _position()

    body = client.get("/api/dashboard?symbol=AAPL").json()

    assert body["session"]["state"] == "closed"
    assert body["session"]["shown_date"] == "2026-10-16"
    assert body["session"]["shown_is_today"] is False

    # The feed carries Friday's decisions, grouped per cycle client-side.
    assert [d["symbol"] for d in body["feed"]] == ["AAPL", "XOM"]
    assert all(d["cycle_index"] for d in body["feed"])

    # The model-call tile counts the shown session: 1 call, of 6 × cap.
    assert body["stats"]["calls_today"] == 1
    assert body["stats"]["cycles_today"] == 6
    assert Decimal(body["stats"]["calls_cost_today"]) == Decimal("0.001")

    # The strip summarises the session instead of one cycle.
    assert body["cycle_line"]["label"] == "Friday 16 Oct"
    assert body["cycle_line"]["text"].startswith("Last session: 2 cycles, 2 decisions")
    assert "protected by stops at Alpaca" in body["cycle_line"]["text"]

    # Latest decision falls back to the most recent one, marked off-session.
    card = body["latest_decision"]
    assert card["symbol"] == "AAPL"
    assert card["final_action"] == "skip"
    assert card["in_session"] is False

    # The market-closed card is present with the stops + summary.
    assert body["market_closed"]["open_count"] == 1
    assert body["market_closed"]["stops"] == [
        {"symbol": "MSFT", "stop": "409.60000000"}
    ]
    assert Decimal(body["market_closed"]["stops"][0]["stop"]) == Decimal(
        "409.60"
    )
    assert body["cycle_counter"]["session_is_today"] is False


def test_in_session_shows_today(client: TestClient) -> None:
    # Thursday 12:52 New York, session open: "today" is today's session.
    _clock(client).set(datetime(2026, 10, 15, 16, 52, tzinfo=UTC))
    _decision(datetime(2026, 10, 15, 16, 46, tzinfo=UTC), symbol="AAPL")

    body = client.get("/api/dashboard?symbol=AAPL").json()

    assert body["session"]["shown_is_today"] is True
    assert body["latest_decision"]["in_session"] is True
    assert body["stats"]["calls_today"] == 1
    # The in-session strip keeps the per-cycle line.
    assert body["cycle_line"]["label"].endswith("cycle")


def test_no_call_why_for_call_cap(client: TestClient) -> None:
    # A symbol past the call cap explains why the model was not asked.
    _clock(client).set(datetime(2026, 10, 15, 16, 52, tzinfo=UTC))
    _decision(
        datetime(2026, 10, 15, 16, 46, tzinfo=UTC),
        symbol="NVDA",
        final_action="no_call",
        no_call_reason="call_cap",
        model="none",
        model_output=None,
    )

    card = client.get("/api/dashboard?symbol=NVDA").json()["latest_decision"]
    assert card["final_action"] == "no_call"
    assert card["why"] == "call cap reached — the model was not asked"
