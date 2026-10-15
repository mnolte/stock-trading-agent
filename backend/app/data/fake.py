from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from app.data.models import (
    Bar5m,
    CalendarDay,
    CorporateActionEvent,
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


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def _bars_for_session(
    symbol: str,
    day: date,
    session_open: datetime,
    session_close: datetime,
) -> list[Bar5m]:
    """Generate deterministic 5-minute bars for one session."""
    seed = f"{symbol}:{day.isoformat()}"
    rng = random.Random(seed)
    base = float(_BASE_PRICES.get(symbol, Decimal("100")))

    bars: list[Bar5m] = []
    current = session_open
    previous_close = base + rng.uniform(-base * 0.005, base * 0.005)
    while current < session_close:
        nxt = current + timedelta(minutes=5)
        if nxt > session_close:
            nxt = session_close
        open_p = previous_close
        noise = rng.uniform(-0.002, 0.002)
        close_p = open_p * (1 + noise)
        high_p = max(open_p, close_p) * (1 + abs(rng.uniform(0, 0.001)))
        low_p = min(open_p, close_p) * (1 - abs(rng.uniform(0, 0.001)))
        volume = rng.randint(1_000, 50_000)
        bars.append(
            Bar5m(
                symbol=symbol,
                start=current.astimezone(UTC),
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


class FakeBarSource:
    """Deterministic 5-minute bars for all configured sessions in the range."""

    def __init__(self, calendar: FakeCalendarSource | None = None) -> None:
        self.calendar = calendar or FakeCalendarSource()

    async def fetch_5m_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> DataResult[list[Bar5m]]:
        start_date = start.astimezone(NY_TZ).date()
        end_date = end.astimezone(NY_TZ).date()
        calendar = await self.calendar.fetch_calendar(start_date, end_date)
        bars: list[Bar5m] = []
        for day in calendar.data:
            # Skip sessions fully outside the requested UTC interval.
            if day.close < start or day.open > end:
                continue
            bars.extend(_bars_for_session(symbol, day.date, day.open, day.close))
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
            last = base * (1 + random.Random(symbol).uniform(-0.005, 0.005))
            spread = last * 0.0002
            quotes[symbol] = Quote(
                symbol=symbol,
                bid=_to_decimal(last - spread / 2),
                ask=_to_decimal(last + spread / 2),
                last=_to_decimal(last),
                timestamp=now,
                feed="fake",
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
