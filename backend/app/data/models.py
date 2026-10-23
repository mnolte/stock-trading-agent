from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
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
class DailyBar:
    """One daily bar for a single trading session, aggregated from 5m bars."""

    symbol: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class Quote:
    """Latest IEX quote plus last trade for a symbol."""

    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    timestamp: datetime | None
    feed: str
    trade_timestamp: datetime | None = None
    fetched_at: datetime | None = None

    def price_reason(
        self, *, now: datetime, max_age_seconds: float,
        bar_close: Decimal | None, max_deviation_pct: Decimal,
    ) -> str | None:
        """Market-price quality: the IEX last trade is the market price
        (docs/SPEC.md, Entries — Market price)."""
        ts = self.trade_timestamp
        if ts is None or not self.last.is_finite() or self.last <= 0:
            return "price_stale"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if (now - ts).total_seconds() > max_age_seconds:
            return "price_stale"
        if (
            bar_close is not None and bar_close.is_finite() and bar_close > 0
            and abs(self.last - bar_close) / bar_close * 100 > max_deviation_pct
        ):
            return "price_suspect"
        return None

    def quote_plausible(self, max_deviation_pct: Decimal) -> bool:
        """Both sides of the IEX book present, timestamped and within the
        deviation band of the market price (docs/SPEC.md, Entries — IEX
        bid and ask)."""
        if (
            self.timestamp is None
            or not self.bid.is_finite() or self.bid <= 0
            or not self.ask.is_finite() or self.ask <= 0
            or not self.last.is_finite() or self.last <= 0
        ):
            return False
        return (
            abs(self.bid - self.last) / self.last * 100 <= max_deviation_pct
            and abs(self.ask - self.last) / self.last * 100 <= max_deviation_pct
        )

    def stop_reference(
        self, bar_close: Decimal | None, atr: Decimal | None, *,
        entry: bool, max_deviation_pct: Decimal,
    ) -> Decimal | None:
        """Lowest of market price, the plausible book side (ask for
        entries, bid for tighten) and the last closed bar."""
        required = (self.last, bar_close, atr)
        if any(
            value is None or not value.is_finite() or value <= 0 for value in required
        ):
            return None
        assert bar_close is not None
        values = [self.last, bar_close]
        if self.quote_plausible(max_deviation_pct):
            values.append(self.ask if entry else self.bid)
        return min(values)

    def provenance(self, plausible: bool) -> dict[str, str | bool | None]:
        return {
            "last": str(self.last),
            "trade_timestamp": (
                self.trade_timestamp.isoformat() if self.trade_timestamp else None
            ),
            "bid": str(self.bid), "ask": str(self.ask),
            "quote_timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "plausible": plausible,
            "spread_unknown": not plausible,
            "feed": self.feed,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


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
    statuses: dict[str, str] | None = None
