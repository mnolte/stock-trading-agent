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


_EARNINGS_HOUR_MAP = {
    "bmo": "before_open",
    "amc": "after_close",
    "dmh": "during_market",
}


def normalise_earnings_hour(raw: str) -> str:
    """Map Finnhub's bmo/amc/dmh hour to a normalised time of day."""
    return _EARNINGS_HOUR_MAP.get(raw.strip().lower(), "unknown")


def earnings_counts_as_before_open(time_of_day: str) -> bool:
    """For the earnings-exit rule, an unknown time of day counts as before the open."""
    return time_of_day in ("before_open", "unknown")


@dataclass(frozen=True)
class EarningsEvent:
    """One Finnhub earnings calendar event."""

    symbol: str
    date: date
    time_of_day: str  # before_open | after_close | during_market | unknown
    hour_raw: str  # Finnhub's raw hour value, e.g. bmo | amc | dmh
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
    for_date: date  # the ECB observation date
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
