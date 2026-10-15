from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.config import AppConfig, get_config

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
    """Computes market session state from a configurable clock."""

    def __init__(self, clock: Clock, config: AppConfig | None = None) -> None:
        self.clock = clock
        self.config = config or get_config()
        self.cycle_times = self._parse_cycles()
        self.display_tz = ZoneInfo(self.config.get("app", "timezone", default="Europe/Amsterdam"))

    def _parse_cycles(self) -> list[time]:
        raw = self.config.get(
            "schedule", "cycles", default=["10:46", "11:46", "12:46", "13:46", "14:46", "15:46"]
        )
        return [datetime.strptime(t, "%H:%M").time() for t in raw]  # noqa: DTZ007

    def now(self) -> datetime:
        return self.clock.now()

    def state(self) -> SessionState:
        now = self.clock.now()
        ny_now = self.clock.to_zone(now, NY_TZ)
        today = ny_now.date()
        current_time = ny_now.time()

        open_dt = datetime.combine(today, SESSION_OPEN, tzinfo=NY_TZ)
        close_dt = datetime.combine(today, SESSION_CLOSE, tzinfo=NY_TZ)

        last_cycle = max(self.cycle_times)

        if current_time < SESSION_OPEN:
            session_state = "closed"
            next_cycle = self._next_cycle_after(ny_now, today)
            next_open = open_dt
            next_close = close_dt
        elif current_time >= SESSION_CLOSE:
            session_state = "closed"
            next_cycle = self._next_cycle_after(ny_now, today)
            next_open = open_dt + timedelta(days=1)
            next_close = close_dt + timedelta(days=1)
        elif current_time >= last_cycle:
            session_state = "last_cycle_done"
            next_cycle = None
            next_open = None
            next_close = close_dt
        else:
            session_state = "open"
            next_cycle = self._next_cycle_after(ny_now, today)
            next_open = None
            next_close = close_dt

        display_now = self.clock.to_zone(now, self.display_tz)
        display_time = display_now.strftime("%A %d %B · %H:%M Amsterdam")

        return SessionState(
            state=session_state,
            next_cycle=next_cycle.astimezone(UTC) if next_cycle else None,
            next_open=next_open.astimezone(UTC) if next_open else None,
            next_close=next_close.astimezone(UTC) if next_close else None,
            display_time=display_time,
        )

    def _next_cycle_after(self, ny_now: datetime, today: date) -> datetime | None:
        for cycle in sorted(self.cycle_times):
            cycle_dt = datetime.combine(today, cycle, tzinfo=NY_TZ)
            if cycle_dt > ny_now and cycle >= SESSION_OPEN and cycle <= SESSION_CLOSE:
                return cycle_dt
        # Look at tomorrow (no holiday calendar yet)
        tomorrow = today + timedelta(days=1)
        first_cycle = min(self.cycle_times)
        return datetime.combine(tomorrow, first_cycle, tzinfo=NY_TZ)


def make_clock(config: AppConfig | None = None) -> Clock:
    cfg = config or get_config()
    if cfg.env.mode == "simulated":
        # Simulated mode starts at a fixed session-open time for determinism.
        return FakeClock(datetime(2026, 10, 15, 13, 30, tzinfo=UTC))
    return RealClock()
