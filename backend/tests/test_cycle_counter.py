"""Dashboard cycle counter (docs/SPEC.md, GUI — Cycle counter).

The simulated app boots on a frozen clock at Thursday 2026-10-15 13:30 UTC
(09:30 New York). Tests move that clock and swap in a static calendar; the
default is every weekday 09:30–16:00, six cycles at :46.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, cast

from app.data.models import CalendarDay
from app.db.base import get_session_maker
from app.db.models import Cycle, Run
from app.market.calendar import StaticCalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from fastapi.testclient import TestClient


def _clock(client: TestClient) -> FakeClock:
    return cast(FakeClock, cast(Any, client.app).state.clock)


def _calendar(client: TestClient, days: list[CalendarDay]) -> None:
    cast(Any, client.app).state.calendar_store = StaticCalendarProvider(days)


def _day(day: date, close: time = time(16, 0)) -> CalendarDay:
    return CalendarDay(
        date=day,
        open=datetime.combine(day, time(9, 30), tzinfo=NY_TZ),
        close=datetime.combine(day, close, tzinfo=NY_TZ),
    )


def _cycle(at_utc: datetime, status: str = "done", kind: str = "cycle") -> None:
    naive = at_utc.astimezone(UTC).replace(tzinfo=None)
    with get_session_maker()() as session:
        session.add(
            Cycle(
                kind=kind,
                scheduled_time=naive,
                started_at=naive,
                finished_at=naive if status != "running" else None,
                status=status,
            )
        )
        session.commit()


def _counter(client: TestClient) -> dict[str, Any]:
    return cast(dict[str, Any], client.get("/api/dashboard").json()["cycle_counter"])


def test_counter_normal_day_segments_and_index(client: TestClient) -> None:
    # Thursday 12:52 New York: three cycles done, 13:46 up next.
    _clock(client).set(datetime(2026, 10, 15, 16, 52, tzinfo=UTC))
    for hour in (14, 15, 16):  # 10:46, 11:46, 12:46 New York, in UTC
        _cycle(datetime(2026, 10, 15, hour, 46, tzinfo=UTC))
    # A cycle from before the run started does not count towards either
    # number (yesterday's last cycle, run began today at startup).
    _cycle(datetime(2026, 10, 14, 19, 46, tzinfo=UTC))

    body = _counter(client)

    assert body["cycles_today"] == 6
    assert body["cycle_index"] == 3
    assert [s["status"] for s in body["segments"]] == [
        "done",
        "done",
        "done",
        "next",
        "later",
        "later",
    ]
    assert [s["time"] for s in body["segments"]][3] == "2026-10-15T17:46:00+00:00"
    assert body["next_cycle"] == "2026-10-15T17:46:00+00:00"  # 13:46 New York
    assert body["next_cycle_today"] is True
    assert body["next_check"] == "2026-10-15T17:01:00+00:00"  # 13:01 New York
    assert body["next_check_today"] is True
    assert body["running"] is False
    assert body["kill_switch"] is False
    assert body["run_cycle_number"] == 3


def test_counter_early_close_fewer_segments(client: TestClient) -> None:
    # A 13:00 New York close keeps only the 10:46, 11:46 and 12:46 cycles.
    _calendar(
        client,
        [_day(date(2026, 10, 15), close=time(13, 0)), _day(date(2026, 10, 16))],
    )
    _clock(client).set(datetime(2026, 10, 15, 15, 0, tzinfo=UTC))  # 11:00 NY
    _cycle(datetime(2026, 10, 15, 14, 46, tzinfo=UTC))  # 10:46 ran

    body = _counter(client)

    assert body["cycles_today"] == 3
    assert body["cycle_index"] == 1
    assert [s["status"] for s in body["segments"]] == ["done", "next", "later"]
    assert body["next_cycle"] == "2026-10-15T15:46:00+00:00"  # 11:46 New York


def test_counter_market_closed_next_session_first_cycle(client: TestClient) -> None:
    # Saturday: the counter points at Monday's first cycle, not today.
    _calendar(client, [_day(date(2026, 10, 16)), _day(date(2026, 10, 19))])
    _clock(client).set(datetime(2026, 10, 17, 12, 0, tzinfo=UTC))

    body = _counter(client)

    assert body["segments"] == []
    assert body["cycles_today"] == 0
    assert body["cycle_index"] == 0
    assert body["next_cycle"] == "2026-10-19T14:46:00+00:00"  # Mon 10:46 NY
    assert body["next_cycle_today"] is False
    # The next position check comes from the same next session.
    assert body["next_check"] == "2026-10-19T13:46:00+00:00"  # Mon 09:46 NY
    assert body["next_check_today"] is False


def test_counter_kill_switch(client: TestClient) -> None:
    cast(Any, client.app).state.exec_state.kill_switch_on = True

    body = _counter(client)

    assert body["kill_switch"] is True


def test_counter_running_cycle(client: TestClient) -> None:
    _clock(client).set(datetime(2026, 10, 15, 16, 47, tzinfo=UTC))  # 12:47 NY
    _cycle(datetime(2026, 10, 15, 16, 46, tzinfo=UTC), status="running")

    body = _counter(client)

    assert body["running"] is True


def test_counter_running_check_is_not_a_cycle(client: TestClient) -> None:
    # A running position check does not light the "running" state — the
    # counter only watches kind='cycle' rows.
    _clock(client).set(datetime(2026, 10, 15, 16, 47, tzinfo=UTC))
    _cycle(
        datetime(2026, 10, 15, 16, 46, tzinfo=UTC),
        status="running",
        kind="position_check",
    )

    body = _counter(client)

    assert body["running"] is False
    assert body["cycle_index"] == 0


def test_counter_run_count_resets_with_new_run(client: TestClient) -> None:
    _clock(client).set(datetime(2026, 10, 15, 16, 52, tzinfo=UTC))
    for hour in (14, 15, 16):
        _cycle(datetime(2026, 10, 15, hour, 46, tzinfo=UTC))
    assert _counter(client)["run_cycle_number"] == 3

    # "Start new run" (paper reset) inserts a new run row; the count
    # starts over from it.
    with get_session_maker()() as session:
        session.add(
            Run(
                started_at=datetime(2026, 10, 15, 17, 0),
                starting_equity=Decimal("10000"),
            )
        )
        session.commit()

    assert _counter(client)["run_cycle_number"] == 0
    # "Cycle n of 6 today" is unaffected: it counts today's cycles, not
    # the run's.
    assert _counter(client)["cycle_index"] == 3
