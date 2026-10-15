from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class Bar5m:
    """One 5-minute SIP bar."""

    symbol: str
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    feed: str


@dataclass(frozen=True)
class Bar1h:
    """One session-aligned 1-hour bar."""

    symbol: str
    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    feed: str
    complete: bool  # False for the final partial bar on early-close days


@dataclass(frozen=True)
class Quote:
    """Latest IEX quote plus last trade for a symbol."""

    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    timestamp: datetime
    feed: str


@dataclass(frozen=True)
class NewsItem:
    """One Alpaca news item."""

    id: str
    symbol: str
    headline: str
    source: str
    created_at: datetime


@dataclass(frozen=True)
class EarningsEvent:
    """One Finnhub earnings calendar event."""

    symbol: str
    date: date
    time_of_day: str  # bmo | amc
    fetched_at: datetime


@dataclass(frozen=True)
class CorporateActionEvent:
    """One split or dividend corporate action."""

    symbol: str
    action_type: str  # split | dividend
    ex_date: date
    amount_or_ratio: Decimal | None
    fetched_at: datetime


@dataclass(frozen=True)
class FXRate:
    """ECB EUR/USD reference rate for display only."""

    base: str  # EUR
    quote: str  # USD
    usd_per_eur: Decimal
    fetched_at: datetime


@dataclass(frozen=True)
class CalendarDay:
    """One trading day from Alpaca's calendar."""

    date: date
    open: datetime  # timezone-aware in America/New_York
    close: datetime  # timezone-aware in America/New_York


@dataclass(frozen=True)
class MarketClock:
    """Alpaca /v2/clock response: the source of truth for "is the market open"."""

    is_open: bool
    timestamp: datetime
    next_open: datetime
    next_close: datetime


@dataclass(frozen=True)
class DataResult[T]:
    """Result wrapper so a source failure is explicit and named."""

    data: T
    error: str | None = None
    source: str = ""
