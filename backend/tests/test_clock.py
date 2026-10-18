from datetime import datetime, timedelta

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
