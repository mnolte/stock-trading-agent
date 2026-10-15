from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.config import AppConfig, get_config
from app.data.models import CalendarDay
from app.market.calendar import CalendarProvider, WeekdayCalendarProvider

NY_TZ = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


class Clock(Protocol):
    """Injectable time source."""

    def now(self) -> datetime:
        """Return current time in UTC."""
        ...

    def to_zone(self, dt: datetime, tz: ZoneInfo) -> datetime: ...


class RealClock:
    """Production clock. The only place that reads the system clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def to_zone(self, dt: datetime, tz: ZoneInfo) -> datetime:
        return dt.astimezone(tz)


class FakeClock:
    """Frozen clock for tests and simulated mode."""

    def __init__(self, when: datetime) -> None:
        self._when = when.replace(tzinfo=UTC)

    def now(self) -> datetime:
        return self._when

    def set(self, when: datetime) -> None:
        self._when = when.replace(tzinfo=UTC)

    def advance(self, delta: timedelta) -> None:
        self._when += delta

    def to_zone(self, dt: datetime, tz: ZoneInfo) -> datetime:
        return dt.astimezone(tz)


@dataclass
class SessionState:
    state: str  # closed, open, last_cycle_done, early_close
    next_cycle: datetime | None  # UTC
    next_open: datetime | None  # UTC
    next_close: datetime | None  # UTC
    display_time: str  # Amsterdam, e.g. "Thursday 15 October · 18:52 Amsterdam"


class SessionClock:
    """Computes market session state from a configurable clock and calendar."""

    # Cycles run 16 minutes after the corresponding session-aligned 1h bar
    # closes (15-minute SIP delay plus one minute of slack).
    BAR_DELAY = timedelta(minutes=16)

    def __init__(
        self,
        clock: Clock,
        config: AppConfig | None = None,
        calendar: CalendarProvider | None = None,
    ) -> None:
        self.clock = clock
        self.config = config or get_config()
        self.cycle_times = self._parse_cycles()
        self.calendar = calendar or WeekdayCalendarProvider()
        self.display_tz = ZoneInfo(
            self.config.get("schedule", "display_timezone", default="Europe/Amsterdam")
        )

    def _parse_cycles(self) -> list[time]:
        raw = self.config.get(
            "schedule", "cycles", default=["10:46", "11:46", "12:46", "13:46", "14:46", "15:46"]
        )
        return [datetime.strptime(t, "%H:%M").time() for t in raw]  # noqa: DTZ007

    def now(self) -> datetime:
        return self.clock.now()

    def active_cycles(self, session: CalendarDay) -> list[datetime]:
        """Cycles whose 1h bar closes before the session close."""
        result: list[datetime] = []
        for cycle in sorted(self.cycle_times):
            cycle_dt = datetime.combine(session.date, cycle, tzinfo=NY_TZ)
            bar_close = cycle_dt - self.BAR_DELAY
            if bar_close < session.close and session.open <= cycle_dt <= session.close:
                result.append(cycle_dt)
        return result

    def state(self) -> SessionState:
        now = self.clock.now()
        ny_now = self.clock.to_zone(now, NY_TZ)
        today = ny_now.date()
        session = self.calendar.session_for(today)

        if session is None:
            # Holiday or weekend: next open/close come from the next trading day.
            next_session = self.calendar.next_trading_day(today)
            return self._closed(None, next_session)

        if ny_now < session.open:
            return self._closed(session, None)

        if ny_now >= session.close:
            next_session = self.calendar.next_trading_day(today)
            return self._closed(None, next_session)

        cycles = self.active_cycles(session)
        next_cycle = next((c for c in cycles if c > ny_now), None)

        if session.close.time() < SESSION_CLOSE:
            state = "early_close"
        elif next_cycle is None:
            state = "last_cycle_done"
        else:
            state = "open"

        return SessionState(
            state=state,
            next_cycle=next_cycle.astimezone(UTC) if next_cycle else None,
            next_open=None,
            next_close=session.close.astimezone(UTC),
            display_time=self._display_time(now),
        )

    def _closed(
        self,
        today_session: CalendarDay | None,
        next_session: CalendarDay | None,
    ) -> SessionState:
        now = self.clock.now()
        next_open: datetime | None = None
        next_close: datetime | None = None
        next_cycle: datetime | None = None
        shown_session = today_session or next_session
        if shown_session is not None:
            next_open = shown_session.open
            next_close = shown_session.close
            cycles = self.active_cycles(shown_session)
            next_cycle = cycles[0] if cycles else None

        return SessionState(
            state="closed",
            next_cycle=next_cycle.astimezone(UTC) if next_cycle else None,
            next_open=next_open.astimezone(UTC) if next_open else None,
            next_close=next_close.astimezone(UTC) if next_close else None,
            display_time=self._display_time(now),
        )

    def _display_time(self, now: datetime) -> str:
        display_now = self.clock.to_zone(now, self.display_tz)
        return display_now.strftime("%A %d %B · %H:%M Amsterdam")


def make_clock(config: AppConfig | None = None) -> Clock:
    cfg = config or get_config()
    if cfg.env.mode == "simulated":
        # Simulated mode starts at a fixed session-open time for determinism.
        return FakeClock(datetime(2026, 10, 15, 13, 30, tzinfo=UTC))
    return RealClock()
