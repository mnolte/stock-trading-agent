from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

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
from app.market.clock import NY_TZ, UTC, Clock

_SESSION_OPEN = time(9, 30)
_SESSION_CLOSE = time(16, 0)

_BASE_PRICES: dict[str, Decimal] = {
    "SPY": Decimal("580"),
    "QQQ": Decimal("490"),
    "AAPL": Decimal("225"),
    "MSFT": Decimal("420"),
    "NVDA": Decimal("135"),
    "AMZN": Decimal("190"),
    "GOOGL": Decimal("165"),
    "META": Decimal("585"),
    "JPM": Decimal("230"),
    "XOM": Decimal("120"),
}

# Slow per-bar drift (~30% over 60 sessions). It keeps these symbols in a
# gentle uptrend so simulated mode always has rules-passing candidates, and
# keeps SPY out of the Down + High regime that blocks all entries.
_DRIFT_PER_BAR: dict[str, float] = {
    "SPY": 0.00006,
    "AAPL": 0.00006,
    "MSFT": 0.00006,
    "JPM": 0.00006,
}


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


_FIVE_MIN = timedelta(minutes=5)


def _bars_for_session(
    symbol: str,
    day: date,
    session_open: datetime,
    session_close: datetime,
    drift: float = 0.0,
) -> list[Bar5m]:
    """Generate deterministic 5-minute bars for one session."""
    seed = f"{symbol}:{day.isoformat()}"
    rng = random.Random(seed)  # noqa: S311 - deterministic fake data, not crypto
    base = float(_BASE_PRICES.get(symbol, Decimal("100")))
    uniform = rng.uniform
    randint = rng.randint

    bars: list[Bar5m] = []
    # Stepping in UTC gives the same instants as stepping in New York and
    # converting each bar — a session never contains a DST transition.
    utc_close = session_close.astimezone(UTC)
    current = session_open.astimezone(UTC)
    previous_close = base + uniform(-base * 0.005, base * 0.005)
    while current < utc_close:
        nxt = current + _FIVE_MIN
        if nxt > utc_close:
            nxt = utc_close
        open_p = previous_close
        noise = uniform(-0.002, 0.002)
        close_p = open_p * (1 + noise + drift)
        high_p = max(open_p, close_p) * (1 + abs(uniform(0, 0.001)))
        low_p = min(open_p, close_p) * (1 - abs(uniform(0, 0.001)))
        volume = randint(1_000, 50_000)
        bars.append(
            Bar5m(
                symbol=symbol,
                start=current,
                open=_to_decimal(open_p),
                high=_to_decimal(high_p),
                low=_to_decimal(low_p),
                close=_to_decimal(close_p),
                volume=volume,
                feed="fake",
            )
        )
        previous_close = close_p
        current = nxt
    return bars


def _daily_for_session(
    symbol: str,
    day: date,
    session_open: datetime,
    session_close: datetime,
    drift: float = 0.0,
) -> DailyBar | None:
    """The session's daily bar without building 78 5-minute bars.

    Runs the same random walk as ``_bars_for_session`` and keeps only the
    session's open/high/low/close/volume — identical to
    ``aggregate_daily(_bars_for_session(...))`` because ``_to_decimal``
    is monotonic, so rounding the session's extremes equals taking the
    extremes of the rounded bars.
    """
    seconds = (session_close - session_open).total_seconds()
    if seconds <= 0:
        return None
    rng = random.Random(f"{symbol}:{day.isoformat()}")  # noqa: S311 - deterministic fake data, not crypto
    base = float(_BASE_PRICES.get(symbol, Decimal("100")))
    uniform = rng.uniform
    randint = rng.randint

    previous_close = base + uniform(-base * 0.005, base * 0.005)
    open_p = previous_close
    high_p = low_p = close_p = previous_close
    volume = 0
    for _ in range(math.ceil(seconds / 300)):
        open_i = previous_close
        close_i = open_i * (1 + uniform(-0.002, 0.002) + drift)
        high_i = max(open_i, close_i) * (1 + abs(uniform(0, 0.001)))
        low_i = min(open_i, close_i) * (1 - abs(uniform(0, 0.001)))
        volume += randint(1_000, 50_000)
        if high_i > high_p:
            high_p = high_i
        if low_i < low_p:
            low_p = low_i
        close_p = close_i
        previous_close = close_i
    return DailyBar(
        symbol=symbol,
        date=day,
        open=_to_decimal(open_p),
        high=_to_decimal(high_p),
        low=_to_decimal(low_p),
        close=_to_decimal(close_p),
        volume=volume,
    )


def _next_weekday(day: date) -> date:
    """The next calendar day, skipping Saturday and Sunday."""
    nxt = day + timedelta(days=1)
    if nxt.weekday() == 5:
        nxt += timedelta(days=2)
    elif nxt.weekday() == 6:
        nxt += timedelta(days=1)
    return nxt


def _prev_weekday(day: date) -> date:
    """The previous calendar day, skipping Saturday and Sunday."""
    prev = day - timedelta(days=1)
    if prev.weekday() == 5:
        prev -= timedelta(days=1)
    elif prev.weekday() == 6:
        prev -= timedelta(days=2)
    return prev


class FakeCalendarSource:
    """In-memory calendar for tests and simulated mode."""

    def __init__(
        self,
        holidays: set[date] | None = None,
        early_closes: dict[date, time] | None = None,
        fail_remaining: int = 0,
    ) -> None:
        self.holidays = holidays or set()
        self.early_closes = early_closes or {}
        # Number of fetches that fail before succeeding (retry tests).
        self.fail_remaining = fail_remaining

    async def fetch_calendar(
        self,
        start: date,
        end: date,
    ) -> DataResult[list[CalendarDay]]:
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            return DataResult(data=[], error="calendar unavailable", source="fake")
        days: list[CalendarDay] = []
        current = start
        while current <= end:
            if current.weekday() < 5 and current not in self.holidays:
                open_time = _SESSION_OPEN
                close_time = self.early_closes.get(current, _SESSION_CLOSE)
                open_dt = datetime.combine(current, open_time, tzinfo=NY_TZ)
                close_dt = datetime.combine(current, close_time, tzinfo=NY_TZ)
                days.append(CalendarDay(date=current, open=open_dt, close=close_dt))
            current += timedelta(days=1)
        return DataResult(data=days, source="fake")

    def session_for(self, day: date) -> CalendarDay | None:
        if day.weekday() >= 5 or day in self.holidays:
            return None
        open_dt = datetime.combine(day, _SESSION_OPEN, tzinfo=NY_TZ)
        close_dt = datetime.combine(day, self.early_closes.get(day, _SESSION_CLOSE), tzinfo=NY_TZ)
        return CalendarDay(date=day, open=open_dt, close=close_dt)

    def next_trading_day(self, day: date) -> CalendarDay | None:
        current = day
        # Same ~one-year horizon as the old day-at-a-time scan.
        while (current - day).days < 370:
            current = _next_weekday(current)
            session = self.session_for(current)
            if session is not None:
                return session
        return None

    def previous_trading_day(self, day: date) -> CalendarDay | None:
        current = day
        while (day - current).days < 370:
            current = _prev_weekday(current)
            session = self.session_for(current)
            if session is not None:
                return session
        return None


class FakeBarSource:
    """Deterministic 5-minute bars for all configured sessions in the range."""

    def __init__(
        self,
        calendar: FakeCalendarSource | None = None,
        drift: dict[str, float] | None = None,
    ) -> None:
        self.calendar = calendar or FakeCalendarSource()
        self.drift = _DRIFT_PER_BAR if drift is None else drift

    async def fetch_5m_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> DataResult[list[Bar5m]]:
        """One call for the whole universe — mirrors the multi-symbol
        Alpaca request; bars for every symbol come back together."""
        start_date = start.astimezone(NY_TZ).date()
        end_date = end.astimezone(NY_TZ).date()
        calendar = await self.calendar.fetch_calendar(start_date, end_date)
        bars: list[Bar5m] = []
        for day in calendar.data:
            # Skip sessions fully outside the requested UTC interval.
            if day.close < start or day.open > end:
                continue
            for symbol in symbols:
                bars.extend(
                    _bars_for_session(
                        symbol,
                        day.date,
                        day.open,
                        day.close,
                        self.drift.get(symbol, 0.0),
                    )
                )
        return DataResult(data=bars, source="fake")

    async def fetch_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> DataResult[list[DailyBar]]:
        """Daily bars rolled up from the same deterministic 5m generator."""
        calendar = await self.calendar.fetch_calendar(start, end)
        bars: list[DailyBar] = []
        for day in calendar.data:
            if not start <= day.date <= end:
                continue
            bar = _daily_for_session(
                symbol, day.date, day.open, day.close, self.drift.get(symbol, 0.0)
            )
            if bar is not None:
                bars.append(bar)
        return DataResult(data=bars, source="fake")


class FakeQuoteSource:
    """Deterministic latest quote per symbol."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock

    async def fetch_quotes(self, symbols: list[str]) -> DataResult[dict[str, Quote]]:
        quotes: dict[str, Quote] = {}
        now = self._clock.now() if self._clock else datetime(2026, 10, 15, 13, 30, tzinfo=UTC)
        for symbol in symbols:
            base = float(_BASE_PRICES.get(symbol, Decimal("100")))
            last = base * (1 + random.Random(symbol).uniform(-0.005, 0.005))  # noqa: S311 - deterministic fake data, not crypto
            spread = last * 0.0002
            quotes[symbol] = Quote(
                symbol=symbol,
                bid=_to_decimal(last - spread / 2),
                ask=_to_decimal(last + spread / 2),
                last=_to_decimal(last),
                timestamp=now,
                feed="fake",
                trade_timestamp=now,
                fetched_at=now,
            )
        return DataResult(data=quotes, source="fake")


class FakeNewsSource:
    """Deterministic news; returns one item per symbol in the range."""

    async def fetch_news(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> DataResult[list[NewsItem]]:
        items: list[NewsItem] = []
        midpoint = start + (end - start) / 2
        for symbol in symbols:
            items.append(
                NewsItem(
                    id=f"fake-{symbol}-{midpoint.isoformat()}",
                    symbol=symbol,
                    headline=f"{symbol}: market update",
                    source="fake",
                    created_at=midpoint,
                )
            )
        return DataResult(data=items, source="fake")


class FakeEarningsSource:
    """Deterministic earnings calendar; empty by default."""

    def __init__(self, events: list[EarningsEvent] | None = None) -> None:
        self.events = events or []

    async def fetch_earnings(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> DataResult[list[EarningsEvent]]:
        result = [
            e
            for e in self.events
            if e.symbol in symbols and start <= e.date <= end
        ]
        return DataResult(data=result, source="fake")


class FakeFXSource:
    """Fixed ECB rate for tests."""

    async def fetch_fx(self, for_date: date) -> DataResult[FXRate | None]:
        return DataResult(
            data=FXRate(
                base="EUR",
                quote="USD",
                usd_per_eur=Decimal("1.1200"),
                for_date=for_date,
                fetched_at=datetime.combine(for_date, time.min, tzinfo=UTC),
            ),
            source="fake",
        )


class FakeCorporateActionSource:
    """Deterministic corporate actions; empty by default."""

    def __init__(self, actions: list[CorporateActionEvent] | None = None) -> None:
        self.actions = actions or []

    async def fetch_corporate_actions(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> DataResult[list[CorporateActionEvent]]:
        result = [
            a
            for a in self.actions
            if a.symbol in symbols and start <= a.ex_date <= end
        ]
        return DataResult(data=result, source="fake")
