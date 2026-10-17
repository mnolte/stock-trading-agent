from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from app.api.main import retry_calendar_load
from app.config import AppConfig
from app.data.fake import FakeCalendarSource
from app.market.calendar import CalendarStore
from app.market.clock import UTC, FakeClock, SessionClock

NY = ZoneInfo("America/New_York")
AMS = ZoneInfo("Europe/Amsterdam")

pytestmark = pytest.mark.anyio


def _clock(ny_dt: datetime, calendar: FakeCalendarSource) -> SessionClock:
    return SessionClock(FakeClock(ny_dt.astimezone(UTC)), AppConfig(), calendar)


def test_early_close_state_and_cycles() -> None:
    # Thanksgiving Friday 2026: early close at 13:00 ET.
    early = {date(2026, 11, 27): time(13, 0)}
    calendar = FakeCalendarSource(early_closes=early)
    clock = _clock(datetime(2026, 11, 27, 11, 0, tzinfo=NY), calendar)
    state = clock.state()
    assert state.state == "early_close"
    assert state.next_close == datetime(2026, 11, 27, 13, 0, tzinfo=NY).astimezone(UTC)


def test_early_close_active_cycles() -> None:
    early = {date(2026, 11, 27): time(13, 0)}
    calendar = FakeCalendarSource(early_closes=early)
    session = calendar.session_for(date(2026, 11, 27))
    assert session is not None
    clock = _clock(session.open, calendar)
    cycles = clock.active_cycles(session)
    # Only cycles whose bar closes before the 13:00 close run.
    assert [c.strftime("%H:%M") for c in cycles] == ["10:46", "11:46", "12:46"]


def test_early_close_until_last_cycle_then_last_cycle_done() -> None:
    # Early close shows for the whole session until its last cycle has run.
    early = {date(2026, 11, 27): time(13, 0)}
    calendar = FakeCalendarSource(early_closes=early)
    clock = _clock(datetime(2026, 11, 27, 12, 0, tzinfo=NY), calendar)
    assert clock.state().state == "early_close"
    # After the 12:46 cycle the state is last_cycle_done, like a normal day.
    clock = _clock(datetime(2026, 11, 27, 12, 50, tzinfo=NY), calendar)
    assert clock.state().state == "last_cycle_done"


def test_early_close_closed_after_close() -> None:
    early = {date(2026, 11, 27): time(13, 0)}
    calendar = FakeCalendarSource(early_closes=early)
    clock = _clock(datetime(2026, 11, 27, 13, 30, tzinfo=NY), calendar)
    state = clock.state()
    assert state.state == "closed"
    # Next open is the following trading day (Monday, regular session).
    assert state.next_open == datetime(2026, 11, 30, 9, 30, tzinfo=NY).astimezone(UTC)


def test_holiday_session_is_closed() -> None:
    # Christmas 2026 (Friday) is a holiday.
    calendar = FakeCalendarSource(holidays={date(2026, 12, 25)})
    clock = _clock(datetime(2026, 12, 25, 10, 0, tzinfo=NY), calendar)
    state = clock.state()
    assert state.state == "closed"
    assert state.next_open == datetime(2026, 12, 28, 9, 30, tzinfo=NY).astimezone(UTC)


def test_weekend_next_open_skips_to_monday() -> None:
    calendar = FakeCalendarSource()
    ny = datetime(2026, 10, 17, 12, 0, tzinfo=NY)  # Saturday
    clock = _clock(ny, calendar)
    state = clock.state()
    assert state.state == "closed"
    assert state.next_open == datetime(2026, 10, 19, 9, 30, tzinfo=NY).astimezone(UTC)


def test_dst_offset_difference_week() -> None:
    # Week where the EU and US are on different offsets (EU changed 25 Oct,
    # US changes 1 Nov 2026): New York is UTC-4, Amsterdam UTC+1.
    calendar = FakeCalendarSource()
    clock = _clock(datetime(2026, 10, 28, 10, 46, tzinfo=NY), calendar)
    state = clock.state()
    assert state.state == "open"
    # 11:46 New York = 15:46 UTC = 16:46 Amsterdam during that week.
    ny_next = datetime(2026, 10, 28, 11, 46, tzinfo=NY)
    assert state.next_cycle == ny_next.astimezone(UTC)
    assert ny_next.astimezone(UTC).hour == 15
    assert ny_next.astimezone(AMS).hour == 16


def test_dst_after_us_falls_back() -> None:
    # After the US falls back on 1 Nov 2026, New York is UTC-5.
    calendar = FakeCalendarSource()
    clock = _clock(datetime(2026, 11, 3, 10, 46, tzinfo=NY), calendar)
    state = clock.state()
    ny_next = datetime(2026, 11, 3, 11, 46, tzinfo=NY)
    assert state.next_cycle == ny_next.astimezone(UTC)
    assert ny_next.astimezone(UTC).hour == 16


def test_pre_market_next_cycle_is_today() -> None:
    calendar = FakeCalendarSource()
    clock = _clock(datetime(2026, 10, 15, 8, 0, tzinfo=NY), calendar)
    state = clock.state()
    assert state.state == "closed"
    assert state.next_cycle == datetime(2026, 10, 15, 10, 46, tzinfo=NY).astimezone(UTC)
    assert state.next_open == datetime(2026, 10, 15, 9, 30, tzinfo=NY).astimezone(UTC)


def test_after_close_next_cycle_is_next_session() -> None:
    calendar = FakeCalendarSource()
    ny = datetime(2026, 10, 16, 17, 0, tzinfo=NY)  # Friday evening
    clock = _clock(ny, calendar)
    state = clock.state()
    assert state.state == "closed"
    assert state.next_cycle == datetime(2026, 10, 19, 10, 46, tzinfo=NY).astimezone(UTC)


def test_next_check_during_session() -> None:
    # Position checks run between cycles (config: every ~15 minutes).
    calendar = FakeCalendarSource()
    clock = _clock(datetime(2026, 10, 15, 10, 0, tzinfo=NY), calendar)
    state = clock.state()
    assert state.state == "open"
    assert state.next_check == datetime(2026, 10, 15, 10, 1, tzinfo=NY).astimezone(UTC)


def test_next_check_none_after_last_check() -> None:
    # The last scheduled check is 15:31 New York; after it there is none today.
    calendar = FakeCalendarSource()
    clock = _clock(datetime(2026, 10, 15, 15, 40, tzinfo=NY), calendar)
    state = clock.state()
    assert state.state == "open"  # the 15:46 cycle is still pending
    assert state.next_check is None


def test_next_check_stops_at_early_close() -> None:
    # Checks at 13:01+ fall outside a 13:00 close; 12:31 is the last one.
    early = {date(2026, 11, 27): time(13, 0)}
    calendar = FakeCalendarSource(early_closes=early)
    clock = _clock(datetime(2026, 11, 27, 12, 20, tzinfo=NY), calendar)
    state = clock.state()
    assert state.state == "early_close"
    assert state.next_check == datetime(2026, 11, 27, 12, 31, tzinfo=NY).astimezone(UTC)
    clock = _clock(datetime(2026, 11, 27, 12, 45, tzinfo=NY), calendar)
    assert clock.state().next_check is None


def test_next_check_on_closed_day_is_next_session_first() -> None:
    calendar = FakeCalendarSource()
    clock = _clock(datetime(2026, 10, 17, 12, 0, tzinfo=NY), calendar)  # Saturday
    state = clock.state()
    assert state.state == "closed"
    assert state.next_check == datetime(2026, 10, 19, 9, 46, tzinfo=NY).astimezone(UTC)


async def test_calendar_load_failure_means_market_closed() -> None:
    # If the calendar can't be loaded the market is treated as closed: no
    # sessions, no cycles, no next open.
    source = FakeCalendarSource(fail_remaining=1)
    store = CalendarStore(source)
    result = await store.load(date(2026, 10, 1), date(2026, 12, 31))
    assert result.error is not None
    assert not store.is_loaded
    clock = SessionClock(
        FakeClock(datetime(2026, 10, 15, 14, 0, tzinfo=UTC)), AppConfig(), store
    )
    state = clock.state()
    assert state.state == "closed"
    assert state.next_open is None
    assert state.next_cycle is None


async def test_calendar_retry_recovers_market() -> None:
    # The 5-minute retry job loads the calendar once the source recovers.
    source = FakeCalendarSource(fail_remaining=1)
    store = CalendarStore(source)
    config = AppConfig()
    clock = FakeClock(datetime(2026, 10, 15, 14, 0, tzinfo=UTC))  # 10:00 NY
    await retry_calendar_load(store, config, clock)
    loaded_after_failure = store.is_loaded
    assert not loaded_after_failure
    await retry_calendar_load(store, config, clock)
    assert store.is_loaded
    state = SessionClock(clock, config, store).state()
    assert state.state == "open"


async def test_calendar_retry_skips_when_loaded() -> None:
    source = FakeCalendarSource()
    store = CalendarStore(source)
    config = AppConfig()
    clock = FakeClock(datetime(2026, 10, 15, 14, 0, tzinfo=UTC))
    await retry_calendar_load(store, config, clock)
    assert store.is_loaded
    source.fail_remaining = 1
    # Already loaded: the retry job is a no-op, no fetch happens.
    await retry_calendar_load(store, config, clock)
    assert source.fail_remaining == 1
