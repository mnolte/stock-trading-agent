from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from app.data.models import (
    Bar5m,
    CalendarDay,
    CorporateActionEvent,
    DailyBar,
    DataResult,
    EarningsEvent,
    FXRate,
    NewsItem,
    Quote,
)


class BarSource(Protocol):
    async def fetch_5m_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> DataResult[list[Bar5m]]:
        """Fetch 5-minute bars for one symbol in [start, end]."""
        ...

    async def fetch_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> DataResult[list[DailyBar]]:
        """Fetch daily bars (one per session) for one symbol in [start, end]."""
        ...


class QuoteSource(Protocol):
    async def fetch_quotes(self, symbols: list[str]) -> DataResult[dict[str, Quote]]:
        """Fetch latest IEX quote and last trade for the given symbols."""
        ...


class NewsSource(Protocol):
    async def fetch_news(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> DataResult[list[NewsItem]]:
        """Fetch headlines for the given symbols in [start, end]."""
        ...


class EarningsSource(Protocol):
    async def fetch_earnings(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> DataResult[list[EarningsEvent]]:
        """Fetch earnings calendar events for the given symbols."""
        ...


class FXSource(Protocol):
    async def fetch_fx(self, for_date: date) -> DataResult[FXRate | None]:
        """Fetch the ECB EUR/USD reference rate for a date."""
        ...


class CorporateActionSource(Protocol):
    async def fetch_corporate_actions(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> DataResult[list[CorporateActionEvent]]:
        """Fetch splits and dividends for the given symbols."""
        ...


class CalendarSource(Protocol):
    async def fetch_calendar(
        self,
        start: date,
        end: date,
    ) -> DataResult[list[CalendarDay]]:
        """Fetch market calendar days in [start, end]."""
        ...
