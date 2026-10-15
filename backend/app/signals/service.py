"""Signals service: turns cached bars and stored state into snapshots,
regime and per-symbol rules results for the watchlist and the cycle runner.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.aggregate import aggregate_1h, bars_per_session
from app.data.models import Bar1h, Bar5m, CalendarDay, DailyBar
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Earning, Order, Position
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, Clock, SessionClock
from app.rules.prefilter import RulesResult, evaluate_entry_rules
from app.signals.indicators import opening_gap_pct
from app.signals.regime import MarketRegime, compute_regime
from app.signals.snapshot import IndicatorSnapshot, compute_snapshot

BAR_5M = timedelta(minutes=5)

# Alpaca order statuses that mean the order is still live.
OPEN_ORDER_STATUSES = (
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "held",
    "open",
)


def trading_days_until(
    calendar: CalendarProvider, today: date, event_date: date
) -> int | None:
    """Trading days from ``today`` (exclusive) to ``event_date`` (inclusive).

    Earnings today is 0; on the next session it is 1. Returns None when the
    event is in the past.
    """
    if event_date < today:
        return None
    count = 0
    day = today
    while day < event_date:
        day += timedelta(days=1)
        if calendar.session_for(day) is not None:
            count += 1
    return count


@dataclass(frozen=True)
class WatchlistRow:
    symbol: str
    price: Decimal | None
    day_change_pct: Decimal | None
    rules: RulesResult
    earnings_date: date | None
    last_action: str | None
    last_action_time: datetime | None


@dataclass(frozen=True)
class Watchlist:
    computed_at: datetime
    regime: MarketRegime
    rows: list[WatchlistRow]


class SignalService:
    """Reads the bar cache and the database; computes snapshots and rules."""

    def __init__(
        self,
        market_data: MarketDataService,
        calendar: CalendarProvider,
        config: AppConfig,
        clock: Clock,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.market_data = market_data
        self.calendar = calendar
        self.config = config
        self.clock = clock
        self.session_clock = SessionClock(clock, config, calendar)
        self._session_factory = session_factory or get_session_maker()
        # The stored snapshot the GUI serves; refreshed at startup, after each
        # cycle and after each daily refresh — never recomputed per request.
        self._latest: Watchlist | None = None

    @property
    def latest(self) -> Watchlist | None:
        return self._latest

    async def refresh(self) -> None:
        """Recompute the stored snapshot in a worker thread.

        Indicator math and cache reads are blocking; ``asyncio.to_thread``
        keeps them off the event loop.
        """
        self._latest = await asyncio.to_thread(self.watchlist, self.clock.now())

    def hourly_bars(
        self, symbol: str, sessions: list[CalendarDay], now: datetime
    ) -> list[Bar1h]:
        """Closed session-aligned 1h bars for a symbol (bar end <= now)."""
        bars = self.market_data.get_1h_bars_range(symbol, sessions)
        return [b for b in bars if b.end <= now]

    def daily_bars(
        self, symbol: str, sessions: list[CalendarDay], now: datetime
    ) -> list[DailyBar]:
        """Daily bars for sessions that have already closed."""
        completed = [s for s in sessions if s.close <= now]
        return self.market_data.get_daily_bars(symbol, completed)

    def today_5m(self, symbol: str, today: date, now: datetime) -> list[Bar5m]:
        """Today's 5-minute bars whose bar has closed by ``now``."""
        session = self.calendar.session_for(today)
        if session is None:
            return []
        bars = self.market_data.get_5m_bars(symbol, session.open, session.close)
        return [b for b in bars if b.start + BAR_5M <= now]

    def next_earnings(
        self, symbols: list[str], today: date
    ) -> dict[str, date]:
        """Next earnings date (>= today) per symbol, from the earnings table."""
        start_of_today = datetime.combine(today, time.min)
        with self._session_factory() as session:
            rows = session.execute(
                select(Earning.symbol, func.min(Earning.date))
                .where(Earning.symbol.in_(symbols), Earning.date >= start_of_today)
                .group_by(Earning.symbol)
            ).all()
        return {symbol: d.date() for symbol, d in rows}

    def _symbol_bars(
        self,
        symbol: str,
        sessions: list[CalendarDay],
        daily_sessions: list[CalendarDay],
        today: date,
        now: datetime,
    ) -> tuple[list[Bar1h], list[DailyBar], list[Bar5m]]:
        """One cache read → closed 1h bars, completed daily bars, today's 5m."""
        bars_5m = self.market_data.get_5m_bars_sessions(symbol, sessions)
        per = bars_per_session(bars_5m, sessions)
        bars_1h = [
            b
            for s in sessions
            for b in aggregate_1h(symbol, per[s.date], s)
            if b.end <= now
        ]
        daily = self.daily_bars(symbol, daily_sessions, now)
        todays = [b for b in per.get(today, []) if b.start + BAR_5M <= now]
        return bars_1h, daily, todays

    def _snapshot_from_bars(
        self,
        symbol: str,
        bars_1h: list[Bar1h],
        daily: list[DailyBar],
        todays: list[Bar5m],
        spy_1h: list[Bar1h],
        earnings_available: bool,
        earnings_next: dict[str, date],
        today: date,
    ) -> IndicatorSnapshot:
        next_date = earnings_next.get(symbol)
        days_to_earnings = (
            trading_days_until(self.calendar, today, next_date)
            if next_date is not None
            else None
        )
        return compute_snapshot(
            symbol,
            bars_1h=bars_1h,
            daily=daily,
            today_5m=todays,
            spy_1h=spy_1h,
            days_to_earnings=days_to_earnings,
            earnings_available=earnings_available,
            config=self.config,
        )

    def snapshot(
        self,
        symbol: str,
        now: datetime,
        spy_1h: list[Bar1h],
        sessions: list[CalendarDay],
        daily_sessions: list[CalendarDay],
        earnings_available: bool,
        earnings_next: dict[str, date],
        today: date,
    ) -> IndicatorSnapshot:
        bars_1h, daily, todays = self._symbol_bars(
            symbol, sessions, daily_sessions, today, now
        )
        return self._snapshot_from_bars(
            symbol,
            bars_1h,
            daily,
            todays,
            spy_1h,
            earnings_available,
            earnings_next,
            today,
        )

    def regime(self, now: datetime) -> MarketRegime:
        today = now.astimezone(NY_TZ).date()
        sessions = self._daily_sessions_for(today)
        spy_daily = self.daily_bars("SPY", sessions, now)
        return compute_regime(spy_daily, self.config)

    def is_last_cycle(self, today: date, now: datetime) -> bool:
        """True once the session's last active cycle time has been reached."""
        session = self.calendar.session_for(today)
        if session is None:
            return False
        cycles = self.session_clock.active_cycles(session)
        if not cycles:
            return True
        return now >= cycles[-1]

    def _sessions_for(self, today: date) -> list[CalendarDay]:
        count = int(self.config.get("schedule", "history_sessions", default=60))
        return self.market_data.recent_sessions(today, count)

    def _daily_sessions_for(self, today: date) -> list[CalendarDay]:
        count = int(
            self.config.get("schedule", "daily_history_sessions", default=260)
        )
        return self.market_data.recent_sessions(today, count)

    def _held_and_pending(self) -> tuple[set[str], set[str]]:
        """Open-position symbols and symbols with a live entry order."""
        with self._session_factory() as session:
            held = {
                row[0]
                for row in session.execute(
                    select(Position.symbol).where(Position.closed_at.is_(None))
                ).all()
            }
            pending = {
                row[0]
                for row in session.execute(
                    select(Decision.symbol)
                    .join(Order, Order.decision_id == Decision.id)
                    .where(
                        Order.leg == "entry",
                        Order.status.in_(OPEN_ORDER_STATUSES),
                    )
                ).all()
            }
        return held, pending

    def _last_actions(self, symbols: list[str]) -> dict[str, tuple[str, datetime]]:
        """Most recent stored decision per symbol (action, scheduled time)."""
        result: dict[str, tuple[str, datetime]] = {}
        with self._session_factory() as session:
            for symbol in symbols:
                row = session.execute(
                    select(Decision.final_action, Cycle.scheduled_time)
                    .join(Cycle, Decision.cycle_id == Cycle.id)
                    .where(Decision.symbol == symbol)
                    .order_by(Decision.id.desc())
                    .limit(1)
                ).first()
                if row is not None:
                    result[symbol] = (row[0], row[1].replace(tzinfo=UTC))
        return result

    def watchlist(self, now: datetime) -> Watchlist:
        today = now.astimezone(NY_TZ).date()
        sessions = self._sessions_for(today)
        daily_sessions = self._daily_sessions_for(today)
        symbols = self.market_data.universe()
        regime = compute_regime(
            self.daily_bars("SPY", daily_sessions, now), self.config
        )
        spy_1h = self.hourly_bars("SPY", sessions, now)
        held, pending = self._held_and_pending()
        earnings_next = self.next_earnings(symbols, today)
        earnings_status = self.market_data.latest_earnings
        earnings_available = (
            earnings_status is not None and earnings_status.error is None
        )
        last_cycle = self.is_last_cycle(today, now.astimezone(NY_TZ))
        last_actions = self._last_actions(symbols)

        rows: list[WatchlistRow] = []
        for symbol in symbols:
            bars_1h, daily, todays = self._symbol_bars(
                symbol, sessions, daily_sessions, today, now
            )
            snap = self._snapshot_from_bars(
                symbol,
                bars_1h,
                daily,
                todays,
                spy_1h,
                earnings_available,
                earnings_next,
                today,
            )
            rules = evaluate_entry_rules(
                symbol,
                snapshot=snap,
                regime=regime,
                held=symbol in held,
                open_entry_order=symbol in pending,
                is_last_cycle=last_cycle,
                config=self.config,
            )
            price: Decimal | None
            change: Decimal | None
            if todays:
                price = todays[-1].close
                base = daily[-1].close if daily else None
            else:
                price = daily[-1].close if daily else None
                base = daily[-2].close if len(daily) >= 2 else None
            change = (
                opening_gap_pct(price, base)
                if price is not None and base is not None
                else None
            )
            action, action_time = last_actions.get(symbol, (None, None))
            rows.append(
                WatchlistRow(
                    symbol=symbol,
                    price=price,
                    day_change_pct=change,
                    rules=rules,
                    earnings_date=earnings_next.get(symbol),
                    last_action=action,
                    last_action_time=action_time,
                )
            )
        return Watchlist(computed_at=now, regime=regime, rows=rows)
