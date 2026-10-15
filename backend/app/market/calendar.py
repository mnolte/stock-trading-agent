from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.data.models import CalendarDay, DataResult
from app.data.protocols import CalendarSource

NY_TZ = ZoneInfo("America/New_York")


class CalendarProvider(Protocol):
    """Synchronous access to loaded market calendar days."""

    def session_for(self, day: date) -> CalendarDay | None: ...

    def next_trading_day(self, day: date) -> CalendarDay | None: ...

    def previous_trading_day(self, day: date) -> CalendarDay | None: ...


class StaticCalendarProvider:
    """Calendar provider backed by an in-memory list (tests, simulated mode)."""

    def __init__(self, days: list[CalendarDay] | None = None) -> None:
        self._days = {d.date: d for d in (days or [])}
        self._sorted = sorted(self._days.values(), key=lambda d: d.date)

    def session_for(self, day: date) -> CalendarDay | None:
        return self._days.get(day)

    def next_trading_day(self, day: date) -> CalendarDay | None:
        for d in self._sorted:
            if d.date > day:
                return d
        return None

    def previous_trading_day(self, day: date) -> CalendarDay | None:
        for d in reversed(self._sorted):
            if d.date < day:
                return d
        return None


class WeekdayCalendarProvider:
    """Fallback provider treating every weekday as a regular session."""

    def session_for(self, day: date) -> CalendarDay | None:
        if day.weekday() >= 5:
            return None
        return CalendarDay(
            date=day,
            open=datetime.combine(day, time(9, 30), tzinfo=NY_TZ),
            close=datetime.combine(day, time(16, 0), tzinfo=NY_TZ),
        )

    def next_trading_day(self, day: date) -> CalendarDay | None:
        current = day
        for _ in range(370):
            current += timedelta(days=1)
            session = self.session_for(current)
            if session is not None:
                return session
        return None

    def previous_trading_day(self, day: date) -> CalendarDay | None:
        current = day
        for _ in range(370):
            current -= timedelta(days=1)
            session = self.session_for(current)
            if session is not None:
                return session
        return None


class CalendarStore:
    """Loads the Alpaca trading calendar into memory at startup/daily refresh."""

    def __init__(self, source: CalendarSource) -> None:
        self.source = source
        self._provider = StaticCalendarProvider()
        self._loaded_range: tuple[date, date] | None = None
        self.last_error: str | None = None

    async def load(self, start: date, end: date) -> DataResult[list[CalendarDay]]:
        result = await self.source.fetch_calendar(start, end)
        if result.error is None:
            merged = dict(self._provider._days)
            for day in result.data:
                merged[day.date] = day
            self._provider = StaticCalendarProvider(list(merged.values()))
            self._loaded_range = (start, end)
            self.last_error = None
        else:
            self.last_error = result.error
        return result

    def session_for(self, day: date) -> CalendarDay | None:
        return self._provider.session_for(day)

    def next_trading_day(self, day: date) -> CalendarDay | None:
        return self._provider.next_trading_day(day)

    def previous_trading_day(self, day: date) -> CalendarDay | None:
        return self._provider.previous_trading_day(day)

    @property
    def loaded_range(self) -> tuple[date, date] | None:
        return self._loaded_range

    @property
    def is_loaded(self) -> bool:
        """True once at least one calendar load has succeeded."""
        return self._loaded_range is not None
