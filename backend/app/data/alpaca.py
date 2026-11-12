from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast

from alpaca.common.rest import RESTClient
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical.corporate_actions import CorporateActionsClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    CorporateActionsRequest,
    NewsRequest,
    StockBarsRequest,
    StockLatestQuoteRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetCalendarRequest
from requests.adapters import HTTPAdapter

from app.data.models import (
    Bar5m,
    CalendarDay,
    CorporateActionEvent,
    DailyBar,
    DataResult,
    MarketClock,
    NewsItem,
    Quote,
)
from app.data.rate_limiter import RateLimiter
from app.errors import safe_error
from app.market.clock import NY_TZ, UTC, Clock, RealClock

logger = logging.getLogger(__name__)

# alpaca-py gives each REST client its own requests.Session, whose
# urllib3 pool keeps 10 connections per host. The daily-bar refresh and
# the universe screen already run 10 reads at once, so any overlapping
# call overflowed the pool ("Connection pool is full, discarding
# connection"). Item 078 sizes the pool to ``max(max_requests, 20)`` per
# host; 20 stays the floor.
_HTTP_POOL_MAXSIZE = 20


def _mount_pool(client: RESTClient, maxsize: int) -> None:
    """Widen the client's urllib3 per-host pool (default 10)."""
    client._session.mount("https://", HTTPAdapter(pool_maxsize=maxsize))


def _parse_timestamp(value: Any) -> datetime:
    """Parse an Alpaca timestamp into an aware UTC datetime."""
    ts = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _field(item: Any, key: str) -> Any:
    """Get a field from a raw dict or an alpaca-py response model."""
    if isinstance(item, dict):
        return item[key]
    return getattr(item, key)


def _decimal_or_zero(value: Any) -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal(0)
    except (ArithmeticError, ValueError):
        return Decimal(0)


def parse_calendar(raw: list[Any]) -> list[CalendarDay]:
    """Alpaca /v2/calendar response -> CalendarDay list (NY wall clock)."""
    days: list[CalendarDay] = []
    for item in raw:
        day = date.fromisoformat(str(_field(item, "date")))
        days.append(
            CalendarDay(
                date=day,
                open=datetime.combine(
                    day, time.fromisoformat(str(_field(item, "open"))), tzinfo=NY_TZ
                ),
                close=datetime.combine(
                    day, time.fromisoformat(str(_field(item, "close"))), tzinfo=NY_TZ
                ),
            )
        )
    return days


def _ohlcv(bar: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal, Decimal, int] | None:
    """Validated (open, high, low, close, volume) or None on corrupt data.

    A bar with non-finite or non-positive prices, high < low, an open or
    close outside [low, high], or a negative volume is dropped — one bad
    bar must not reach the indicator math downstream.
    """
    try:
        o = Decimal(str(bar["o"]))
        h = Decimal(str(bar["h"]))
        lo = Decimal(str(bar["l"]))
        c = Decimal(str(bar["c"]))
        v = int(bar["v"])
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    prices = (o, h, lo, c)
    if not all(p.is_finite() and p > 0 for p in prices):
        return None
    if h < lo or not (lo <= o <= h) or not (lo <= c <= h) or v < 0:
        return None
    return o, h, lo, c, v


def parse_bars(raw: dict[str, Any], symbol: str) -> list[Bar5m]:
    """Alpaca /v2/stocks/{symbol}/bars response -> Bar5m list."""
    bars: list[Bar5m] = []
    for bar in raw.get(symbol, []):
        ohlcv = _ohlcv(bar)
        if ohlcv is None or bar.get("t") is None:
            logger.warning("%s: dropped malformed 5m bar %r", symbol, bar)
            continue
        o, h, lo, c, v = ohlcv
        bars.append(
            Bar5m(
                symbol=symbol,
                start=_parse_timestamp(bar["t"]),
                open=o, high=h, low=lo, close=c, volume=v,
                feed="sip",
            )
        )
    return bars


def parse_daily_bars(raw: dict[str, Any], symbol: str) -> list[DailyBar]:
    """Alpaca daily bars response -> DailyBar list (date = NY session date).

    The session date comes from the date part of Alpaca's timestamp, not
    from a timezone conversion: a timestamp earlier than 05:00 UTC maps to
    the previous New York evening, which would file the bar under the
    wrong session in winter (EST).
    """
    bars: list[DailyBar] = []
    for bar in raw.get(symbol, []):
        ohlcv = _ohlcv(bar)
        if ohlcv is None or bar.get("t") is None:
            logger.warning("%s: dropped malformed daily bar %r", symbol, bar)
            continue
        o, h, lo, c, v = ohlcv
        bars.append(
            DailyBar(
                symbol=symbol,
                date=date.fromisoformat(str(bar["t"])[:10]),
                open=o, high=h, low=lo, close=c, volume=v,
            )
        )
    return bars


def parse_quotes(
    raw_quotes: dict[str, Any],
    raw_trades: dict[str, Any],
    symbols: list[str],
    now: datetime,
) -> dict[str, Quote]:
    """Alpaca latest quote + latest trade responses -> Quote map."""
    quotes: dict[str, Quote] = {}
    for symbol in symbols:
        q = raw_quotes.get(symbol, {})
        t = raw_trades.get(symbol, {})
        ts_value = q.get("t")
        ts = _parse_timestamp(ts_value) if ts_value is not None else None
        quotes[symbol] = Quote(
            symbol=symbol,
            bid=_decimal_or_zero(q.get("bp")),
            ask=_decimal_or_zero(q.get("ap")),
            last=_decimal_or_zero(t.get("p")),
            timestamp=ts,
            feed="iex",
            trade_timestamp=_parse_timestamp(t["t"]) if t.get("t") else None,
            fetched_at=now,
        )
    return quotes


def parse_news(
    raw: dict[str, Any], symbols: list[str], now: datetime
) -> list[NewsItem]:
    """Alpaca /v1beta1/news response -> NewsItem list (one row per symbol)."""
    items: list[NewsItem] = []
    requested = set(symbols)
    for item in raw.get("news", []):
        created = item.get("created_at")
        created_at = _parse_timestamp(created) if created is not None else now
        for sym in item.get("symbols", []):
            if sym in requested:
                items.append(
                    NewsItem(
                        id=str(item.get("id", "")),
                        symbol=sym,
                        headline=str(item.get("headline", "")),
                        source=str(item.get("source", "")),
                        created_at=created_at,
                    )
                )
    return items


def parse_clock(raw: dict[str, Any]) -> MarketClock:
    """Alpaca /v2/clock response -> MarketClock."""
    return MarketClock(
        is_open=bool(raw.get("is_open", False)),
        timestamp=_parse_timestamp(raw["timestamp"]),
        next_open=_parse_timestamp(raw["next_open"]),
        next_close=_parse_timestamp(raw["next_close"]),
    )


_DIVIDEND_KEYS = ("cash_dividends", "stock_dividends")
_SPLIT_KEYS = ("forward_splits", "reverse_splits", "unit_splits")


def parse_corporate_actions(
    raw: dict[str, Any], fetched_at: datetime
) -> list[CorporateActionEvent]:
    """Alpaca corporate actions response -> split/dividend events."""
    actions: list[CorporateActionEvent] = []
    grouped = raw.get("corporate_actions", {})
    for key, rows in grouped.items():
        if key in _DIVIDEND_KEYS:
            action_type = "dividend"
        elif key in _SPLIT_KEYS:
            action_type = "split"
        else:
            continue
        for row in rows:
            ex_date = row.get("ex_date")
            if isinstance(ex_date, str):
                ex_date = date.fromisoformat(ex_date)
            if ex_date is None:
                continue
            amount = row.get("rate") if action_type == "dividend" else row.get("ratio")
            actions.append(
                CorporateActionEvent(
                    symbol=str(row.get("symbol", "")),
                    action_type=action_type,
                    ex_date=ex_date,
                    amount_or_ratio=Decimal(str(amount)) if amount is not None else None,
                    fetched_at=fetched_at,
                )
            )
    return actions


class AlpacaDataClient:
    """Alpaca market data client. Always created with ``paper=True``."""

    # The free plan rejects SIP bar requests for the last ~15 minutes, so
    # every request ends at least this far in the past.
    SIP_MIN_AGE = timedelta(minutes=16)

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        rate_limiter: RateLimiter | None = None,
        clock: Clock | None = None,
        adjustment: str = "split",
        http_pool_size: int | None = None,
    ) -> None:
        self.limiter = rate_limiter or RateLimiter()
        self.clock = clock or RealClock()
        # All bar requests use the configured price adjustment (spec: split).
        self.adjustment = Adjustment(adjustment)
        self._pool_size = http_pool_size or _HTTP_POOL_MAXSIZE
        self.update_credentials(api_key, secret_key)

    def update_credentials(self, api_key: str, secret_key: str) -> None:
        """Swap keys in place after a Settings save; callers keep the same
        client object and the shared rate limiter."""
        self.trading = TradingClient(api_key, secret_key, paper=True, raw_data=True)
        self.market_data = StockHistoricalDataClient(api_key, secret_key, raw_data=True)
        self.news = NewsClient(api_key, secret_key, raw_data=True)
        self.corporate_actions = CorporateActionsClient(
            api_key, secret_key, raw_data=True
        )
        for client in (
            self.trading,
            self.market_data,
            self.news,
            self.corporate_actions,
        ):
            _mount_pool(client, self._pool_size)

    async def check_paper_account(self) -> DataResult[dict[str, Any]]:
        try:
            account = await self.limiter.run_read(self.trading.get_account)
            if isinstance(account, dict):
                data = account
            else:
                data = {"account_number": str(getattr(account, "account_number", ""))}
            number = str(data.get("account_number", ""))
            if not number.startswith("PA"):
                return DataResult(
                    data=data,
                    error="account is not a paper account",
                    source="alpaca",
                )
            return DataResult(data=data, source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data={}, error=safe_error(exc), source="alpaca")

    async def fetch_calendar(
        self, start: date, end: date
    ) -> DataResult[list[CalendarDay]]:
        try:
            raw = await self.limiter.run_read(
                self.trading.get_calendar,
                GetCalendarRequest(start=start, end=end),
            )
            rows = list(raw) if isinstance(raw, list) else []
            return DataResult(data=parse_calendar(rows), source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=[], error=safe_error(exc), source="alpaca")

    async def fetch_clock(self) -> DataResult[MarketClock | None]:
        try:
            raw = await self.limiter.run_read(self.trading.get_clock)
            if isinstance(raw, dict):
                return DataResult(data=parse_clock(raw), source="alpaca")
            clock = MarketClock(
                is_open=bool(getattr(raw, "is_open", False)),
                timestamp=_parse_timestamp(raw.timestamp),
                next_open=_parse_timestamp(raw.next_open),
                next_close=_parse_timestamp(raw.next_close),
            )
            return DataResult(data=clock, source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=None, error=safe_error(exc), source="alpaca")

    async def fetch_5m_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> DataResult[list[Bar5m]]:
        """One multi-symbol request — the screened eligible list is too
        long for per-symbol calls (docs/SPEC.md, Cycles at scale)."""
        try:
            end = min(end, self.clock.now() - self.SIP_MIN_AGE)
            if not symbols or end <= start:
                return DataResult(data=[], source="alpaca")
            request = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                start=start,
                end=end,
                feed=DataFeed.SIP,
                adjustment=self.adjustment,
            )
            raw = await self.limiter.run_read(
                self.market_data.get_stock_bars, request
            )
            raw_bars = dict(raw) if isinstance(raw, dict) else {}
            bars = [
                bar
                for symbol in symbols
                for bar in parse_bars(raw_bars, symbol)
            ]
            return DataResult(data=bars, source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=[], error=safe_error(exc), source="alpaca")

    async def fetch_daily_bars(
        self, symbol: str, start: date, end: date
    ) -> DataResult[list[DailyBar]]:
        try:
            end_dt = min(
                datetime.combine(end, time.max, tzinfo=NY_TZ),
                self.clock.now() - self.SIP_MIN_AGE,
            )
            start_dt = datetime.combine(start, time.min, tzinfo=NY_TZ)
            if end_dt <= start_dt:
                return DataResult(data=[], source="alpaca")
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start_dt,
                end=end_dt,
                feed=DataFeed.SIP,
                adjustment=self.adjustment,
            )
            raw = await self.limiter.run_read(
                self.market_data.get_stock_bars, request
            )
            raw_bars = dict(raw) if isinstance(raw, dict) else {}
            return DataResult(data=parse_daily_bars(raw_bars, symbol), source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=[], error=safe_error(exc), source="alpaca")

    async def fetch_quotes(
        self, symbols: list[str]
    ) -> DataResult[dict[str, Quote]]:
        try:
            raw_quotes = await self.limiter.run_read(
                self.market_data.get_stock_latest_quote,
                StockLatestQuoteRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX),
            )
            raw_trades = await self.limiter.run_read(
                self.market_data.get_stock_latest_trade,
                StockLatestTradeRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX),
            )
            quotes_raw = dict(raw_quotes) if isinstance(raw_quotes, dict) else {}
            trades_raw = dict(raw_trades) if isinstance(raw_trades, dict) else {}
            return DataResult(
                data=parse_quotes(quotes_raw, trades_raw, symbols, self.clock.now()),
                source="alpaca",
            )
        except Exception as exc:  # noqa: BLE001
            return DataResult(data={}, error=safe_error(exc), source="alpaca")

    async def fetch_news(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> DataResult[list[NewsItem]]:
        items: dict[tuple[str, str], NewsItem] = {}
        statuses: dict[str, str] = {}
        for symbol in symbols:
            token: str | None = None
            seen: set[str] = set()
            received = False
            while True:
                try:
                    params = NewsRequest(symbols=symbol, start=start, end=end, page_token=token)
                    raw_response = await self.limiter.run_read(
                        self.news.get, path="/news", data=params.to_request_fields(),
                    )
                    raw = cast(
                        dict[str, Any],
                        raw_response if isinstance(raw_response, dict) else {},
                    )
                    received = True
                    for item in parse_news(raw, [symbol], self.clock.now()):
                        items[(symbol, item.id)] = item
                    token = raw.get("next_page_token")
                    if token is None:
                        statuses[symbol] = (
                            "complete" if any(s == symbol for s, _ in items) else "empty"
                        )
                        break
                    if token in seen:
                        statuses[symbol] = "truncated"
                        break
                    seen.add(token)
                except Exception:  # noqa: BLE001
                    statuses[symbol] = "truncated" if received else "failed"
                    break
        return DataResult(data=list(items.values()), source="alpaca", statuses=statuses)

    async def fetch_corporate_actions(
        self, symbols: list[str], start: date, end: date
    ) -> DataResult[list[CorporateActionEvent]]:
        try:
            raw = await self.limiter.run_read(
                self.corporate_actions.get_corporate_actions,
                CorporateActionsRequest(symbols=symbols, start=start, end=end),
            )
            ca_raw = dict(raw) if isinstance(raw, dict) else {}
            return DataResult(
                data=parse_corporate_actions(ca_raw, self.clock.now()),
                source="alpaca",
            )
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=[], error=safe_error(exc), source="alpaca")
