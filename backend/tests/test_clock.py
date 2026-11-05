from datetime import date, datetime, timedelta

import pytest
from app.config import AppConfig
from app.market.clock import UTC, FakeClock, SessionClock


def test_fake_clock_returns_set_time() -> None:
    when = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    clock = FakeClock(when)
    assert clock.now() == when


def test_fake_clock_advances() -> None:
    when = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    clock = FakeClock(when)
    clock.advance(timedelta(hours=1))
    assert clock.now() == when + timedelta(hours=1)


def test_fake_clock_converts_aware_non_utc() -> None:
    """An aware non-UTC datetime is converted, not relabelled as UTC."""
    from app.market.clock import NY_TZ

    # 13:00 New York (EDT) == 17:00 UTC — not 13:00 UTC.
    clock = FakeClock(datetime(2026, 10, 15, 13, 0, tzinfo=NY_TZ))
    assert clock.now() == datetime(2026, 10, 15, 17, 0, tzinfo=UTC)
    clock.set(datetime(2026, 10, 15, 9, 30, tzinfo=NY_TZ))
    assert clock.now() == datetime(2026, 10, 15, 13, 30, tzinfo=UTC)
    # Naive still means UTC.
    clock.set(datetime(2026, 10, 15, 12, 0))
    assert clock.now() == datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def test_session_open_during_regular_hours() -> None:
    # 14:00 UTC = 10:00 New York (EDT on 15 Oct 2026)
    clock = FakeClock(datetime(2026, 10, 15, 14, 0, tzinfo=UTC))
    session = SessionClock(clock, AppConfig())
    state = session.state()
    assert state.state == "open"


def test_session_closed_before_open() -> None:
    # 12:00 UTC = 08:00 New York
    clock = FakeClock(datetime(2026, 10, 15, 12, 0, tzinfo=UTC))
    session = SessionClock(clock, AppConfig())
    state = session.state()
    assert state.state == "closed"


def test_session_last_cycle_done() -> None:
    # 19:47 UTC = 15:47 New York
    clock = FakeClock(datetime(2026, 10, 15, 19, 47, tzinfo=UTC))
    session = SessionClock(clock, AppConfig())
    state = session.state()
    assert state.state == "last_cycle_done"


def test_session_clock_uses_configured_timezones() -> None:
    # app.timezone drives the GUI display; schedule.market_timezone the market.
    config = AppConfig()
    clock = SessionClock(FakeClock(datetime(2026, 10, 15, 14, 0, tzinfo=UTC)), config)
    assert str(clock.market_tz) == "America/New_York"
    assert str(clock.display_tz) == "Europe/Amsterdam"


def test_simulated_mode_uses_fake_clock() -> None:
    from app.market.clock import make_clock

    config = AppConfig()
    config.env.mode = "simulated"
    clock = make_clock(config)
    assert isinstance(clock, FakeClock)


def test_session_cycles_follow_ny_offset_across_dst() -> None:
    """The week US winter time starts (Nov 2 2026): the same 10:46 NY
    cycle is 14:46 UTC under EDT and 15:46 UTC under EST — the schedule
    follows America/New_York, never a fixed offset (hard rule 5)."""
    from app.data.models import CalendarDay
    from app.market.calendar import StaticCalendarProvider
    from app.market.clock import NY_TZ

    days = [
        CalendarDay(
            date=d,
            open=datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY_TZ),
            close=datetime(d.year, d.month, d.day, 16, 0, tzinfo=NY_TZ),
        )
        for d in (date(2026, 10, 30), date(2026, 11, 2))
    ]
    for session, expect_utc in zip(days, (14, 15), strict=True):
        config = AppConfig().overlay(
            {"schedule": {"cycles": ["10:46"], "position_checks": []}}
        )
        clock = SessionClock(
            FakeClock(
                datetime(
                    session.date.year, session.date.month, session.date.day,
                    15, 0, tzinfo=UTC,
                )
            ),
            config,
            calendar=StaticCalendarProvider([session]),
        )
        (cycle,) = clock.active_cycles(session)
        assert cycle.tzinfo is not None
        assert cycle.astimezone(UTC).hour == expect_utc
        assert cycle.astimezone(NY_TZ).hour == 10 and cycle.astimezone(NY_TZ).minute == 46


def test_session_clock_at_exact_open_and_close() -> None:
    """Boundary instants: state() is open at exactly the open, closed at
    exactly the close."""
    from app.data.models import CalendarDay
    from app.market.calendar import StaticCalendarProvider
    from app.market.clock import NY_TZ

    day = date(2026, 10, 15)
    session = CalendarDay(
        date=day,
        open=datetime(2026, 10, 15, 9, 30, tzinfo=NY_TZ),
        close=datetime(2026, 10, 15, 16, 0, tzinfo=NY_TZ),
    )
    cal = StaticCalendarProvider([session])
    for instant, expect in (
        (session.open, "open"),
        (session.close, "closed"),
    ):
        clock = SessionClock(
            FakeClock(instant.astimezone(UTC)), AppConfig(), calendar=cal
        )
        assert clock.state().state == expect, instant


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026-10-15T09:30:00-04:00", datetime(2026, 10, 15, 13, 30, tzinfo=UTC)),
     ("2026-10-15T12:34:00", datetime(2026, 10, 15, 12, 34, tzinfo=UTC))],
)
def test_make_clock_parses_sim_now(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: datetime,
) -> None:
    from app.market.clock import make_clock

    monkeypatch.setenv("SA_SIM_NOW", raw)
    config = AppConfig()
    config.env.mode = "simulated"
    clock = make_clock(config)
    assert isinstance(clock, FakeClock)
    assert clock.now() == expected


def test_make_clock_paper_uses_real_clock() -> None:
    from app.market.clock import RealClock, make_clock

    config = AppConfig()
    config.env.mode = "paper"
    assert isinstance(make_clock(config), RealClock)
