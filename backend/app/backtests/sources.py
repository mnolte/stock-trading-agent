"""Backtest data sources (docs/SPEC.md, Backtesting, Data).

Every source reads the run's local stores — ``bars.db`` for bars, the
run's ``market.db`` for earnings, news, corporate actions and calendar —
and cuts its answer at the injected clock with the same delays live has
(5m bars: SIP 16-minute minimum age; news: published before the decision
time). No lookahead: a run can never see a later bar, headline or event.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.backtests.store import (
    MARKET_CALENDAR,
    MARKET_COVERAGE,
    MARKET_NEWS,
)
from app.config import AppConfig
from app.data.cache import BarCache
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
from app.db.models import CorporateAction, Earning
from app.market.calendar import CalendarProvider, StaticCalendarProvider
from app.market.clock import NY_TZ, Clock

SIP_MIN_AGE = timedelta(minutes=16)


def _aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class BacktestBarSource:
    """5-minute and daily bars from the shared store, cut at now - 16 min."""

    def __init__(self, store: BarCache, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def fetch_5m_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> DataResult[list[Bar5m]]:
        end = min(end, self._clock.now() - SIP_MIN_AGE)
        if not symbols or end <= start:
            return DataResult(data=[], source="bars.db")
        bars = [
            bar
            for symbol in symbols
            for bar in self._store.get(symbol, start, end)
        ]
        return DataResult(data=bars, source="bars.db")

    async def fetch_daily_bars(
        self, symbol: str, start: date, end: date
    ) -> DataResult[list[DailyBar]]:
        # A daily bar is usable once the session close is 16 minutes old —
        # the same delay as the 5-minute bars. Early closes count as 16:00:
        # conservative, never a lookahead.
        now = self._clock.now()
        last_day = now.astimezone(NY_TZ).date()
        ready_at = datetime.combine(
            last_day, datetime.min.time(), tzinfo=NY_TZ
        ).replace(hour=16) + SIP_MIN_AGE
        if now < ready_at:
            last_day -= timedelta(days=1)
        end = min(end, last_day)
        if end < start:
            return DataResult(data=[], source="bars.db")
        return DataResult(
            data=self._store.get_daily(symbol, start, end), source="bars.db"
        )


# ``fetch_quotes`` checks the newest few bars first, like
# ``OutcomeWorker._price_at``: a bar not yet closed can only start inside
# its final minutes, so more candidates than this are never closed anyway.
_QUOTE_CANDIDATES = 8
_QUOTE_LOOKBACK = timedelta(days=7)


class BacktestQuoteSource:
    """Synthesised quotes: the last fully-closed 5-min bar's close, with the
    assumed spread around it. Stamped at ``now`` — in a replayed world the
    quote is available at decision time by definition."""

    def __init__(
        self,
        store: BarCache,
        clock: Clock,
        config: AppConfig,
        calendar: CalendarProvider | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._calendar = calendar
        self._spread = Decimal(
            str(config.get("backtest", "assumed_spread_pct", default=0.05))
        )

    def _bar_close(self, bar: Bar5m) -> datetime:
        """When the bar is closed: start + 5m, never past the session
        close — the same rule the outcome worker applies, so an early
        close doesn't leave the last bar looking unclosed."""
        end = bar.start + timedelta(minutes=5)
        if self._calendar is not None:
            session = self._calendar.session_for(
                bar.start.astimezone(NY_TZ).date()
            )
            if session is not None and session.close.astimezone(UTC) < end:
                end = session.close.astimezone(UTC)
        return end

    def _closed_bars(self, symbol: str, now: datetime) -> list[Bar5m]:
        """Closed bars for a symbol in the lookback window, newest first."""
        start = now - _QUOTE_LOOKBACK
        bars = self._store.get_latest(symbol, start, now, _QUOTE_CANDIDATES)
        closed = [b for b in bars if self._bar_close(b) <= now]
        if closed or len(bars) < _QUOTE_CANDIDATES:
            return closed
        # More than a handful of consecutive unclosed bars — only possible
        # with misaligned data; the full window scan settles it.
        return [
            b
            for b in reversed(self._store.get(symbol, start, now))
            if self._bar_close(b) <= now
        ]

    async def fetch_quotes(
        self, symbols: list[str]
    ) -> DataResult[dict[str, Quote]]:
        now = self._clock.now()
        out: dict[str, Quote] = {}
        for symbol in symbols:
            closed = self._closed_bars(symbol, now)
            if not closed:
                continue
            last = closed[0].close
            half = last * self._spread / Decimal(200)
            out[symbol] = Quote(
                symbol=symbol,
                bid=last - half,
                ask=last + half,
                last=last,
                timestamp=now,
                feed="backtest",
                trade_timestamp=self._bar_close(closed[0]),
                fetched_at=now,
            )
        return DataResult(data=out, source="backtest")


class BacktestNewsSource:
    """Headlines from market.db, published before the decision time."""

    def __init__(self, sessions: sessionmaker[Session], clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    async def fetch_news(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> DataResult[list[NewsItem]]:
        end = min(end, self._clock.now())
        if not symbols or end <= start:
            return DataResult(data=[], source="market.db")
        with self._sessions() as session:
            rows = session.execute(
                select(MARKET_NEWS)
                .where(
                    MARKET_NEWS.c.symbol.in_(symbols),
                    MARKET_NEWS.c.created_at >= _aware_utc(start).replace(tzinfo=None),
                    MARKET_NEWS.c.created_at <= _aware_utc(end).replace(tzinfo=None),
                )
                .order_by(MARKET_NEWS.c.created_at)
            ).all()
            coverage = dict(session.execute(select(
                MARKET_COVERAGE.c.symbol, MARKET_COVERAGE.c.status,
            ).where(MARKET_COVERAGE.c.kind == "news", MARKET_COVERAGE.c.symbol.in_(symbols))).all())
        statuses = {
            symbol: ("complete" if any(r.symbol == symbol for r in rows) else "empty")
            if coverage.get(symbol) in ("complete", "empty") else coverage.get(symbol, "failed")
            for symbol in symbols
        }
        return DataResult(
            data=[
                NewsItem(
                    id=r.news_id,
                    symbol=r.symbol,
                    headline=r.headline,
                    source=r.source,
                    created_at=_aware_utc(r.created_at),
                )
                for r in rows
            ],
            source="market.db", statuses=statuses,
        )


class BacktestEarningsSource:
    """Earnings rows from market.db; symbols without coverage come back in
    ``missing`` so the run can block their entries and report it."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    async def fetch_earnings(
        self, symbols: list[str], start: date, end: date
    ) -> DataResult[list[EarningsEvent]]:
        with self._sessions() as session:
            rows = session.execute(
                select(Earning).where(
                    Earning.symbol.in_(symbols),
                    Earning.date >= datetime.combine(start, datetime.min.time()),
                    Earning.date <= datetime.combine(end, datetime.max.time()),
                )
            ).scalars().all()
        return DataResult(
            data=[
                EarningsEvent(
                    symbol=r.symbol,
                    date=r.date.date(),
                    time_of_day=r.time_of_day,
                    hour_raw=r.hour_raw,
                    fetched_at=_aware_utc(r.fetched_at),
                )
                for r in rows
            ],
            source="market.db",
        )

    def missing_symbols(self, symbols: list[str]) -> set[str]:
        """Symbols whose earnings coverage for the run failed to download."""
        with self._sessions() as session:
            rows = session.execute(
                select(MARKET_COVERAGE.c.symbol).where(
                    MARKET_COVERAGE.c.kind == "earnings",
                    MARKET_COVERAGE.c.symbol.in_(symbols),
                    MARKET_COVERAGE.c.status == "missing",
                )
            ).scalars().all()
        return set(rows)


class BacktestCorporateActionSource:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    async def fetch_corporate_actions(
        self, symbols: list[str], start: date, end: date
    ) -> DataResult[list[CorporateActionEvent]]:
        with self._sessions() as session:
            rows = session.execute(
                select(CorporateAction).where(
                    CorporateAction.symbol.in_(symbols),
                    CorporateAction.date >= datetime.combine(start, datetime.min.time()),
                    CorporateAction.date <= datetime.combine(end, datetime.max.time()),
                )
            ).scalars().all()
        return DataResult(
            data=[
                CorporateActionEvent(
                    symbol=r.symbol,
                    action_type=r.action_type,
                    ex_date=r.date.date(),
                    amount_or_ratio=r.ratio_or_amount,
                    fetched_at=datetime.min.replace(tzinfo=UTC),
                )
                for r in rows
            ],
            source="market.db",
        )


def _calendar_days(
    market_sessions: sessionmaker[Session], start: date, end: date
) -> list[CalendarDay]:
    """The market.db calendar rows for [start, end], in date order."""
    with market_sessions() as session:
        rows = session.execute(
            select(MARKET_CALENDAR)
            .where(
                MARKET_CALENDAR.c.date >= start,
                MARKET_CALENDAR.c.date <= end,
            )
            .order_by(MARKET_CALENDAR.c.date)
        ).all()
    return [
        CalendarDay(
            date=r.date,
            open=datetime.fromisoformat(r.open),
            close=datetime.fromisoformat(r.close),
        )
        for r in rows
    ]


class BacktestCalendarSource:
    """The run's trading calendar from market.db."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    async def fetch_calendar(
        self, start: date, end: date
    ) -> DataResult[list[CalendarDay]]:
        return DataResult(
            data=_calendar_days(self._sessions, start, end),
            source="market.db",
        )


class FixedFXSource:
    """FX for display only; a backtest keeps the run's value simple."""

    async def fetch_fx(self, for_date: date) -> DataResult[FXRate | None]:
        return DataResult(
            data=FXRate(
                base="EUR",
                quote="USD",
                usd_per_eur=Decimal("1.08"),
                for_date=for_date,
                fetched_at=datetime.min.replace(tzinfo=UTC),
            ),
            source="backtest",
        )


def make_backtest_sources(
    store: BarCache,
    market_sessions: sessionmaker[Session],
    clock: Clock,
    config: AppConfig,
) -> Any:
    """A ``DataSources`` for a backtest engine — every leg local."""
    from app.data.factory import DataSources

    # Session closes let the quote source mark the partial last bar of an
    # early-close session as closed; the run's calendar lives in market.db.
    provider = StaticCalendarProvider(
        _calendar_days(market_sessions, date.min, date.max)
    )
    return DataSources(
        bars=BacktestBarSource(store, clock),
        quotes=BacktestQuoteSource(store, clock, config, provider),
        news=BacktestNewsSource(market_sessions, clock),
        earnings=BacktestEarningsSource(market_sessions),
        fx=FixedFXSource(),
        corporate_actions=BacktestCorporateActionSource(market_sessions),
        calendar=BacktestCalendarSource(market_sessions),
        alpaca=None,
    )
