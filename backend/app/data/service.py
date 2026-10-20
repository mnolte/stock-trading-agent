from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.models import OPEN_ORDER_STATUSES
from app.config import AppConfig
from app.data.aggregate import aggregate_1h, bars_per_session
from app.data.cache import BarCache, _naive_utc
from app.data.factory import DataSources
from app.data.models import Bar1h, Bar5m, CalendarDay, DailyBar, DataResult, FXRate
from app.db.base import get_session_maker
from app.db.models import CorporateAction as CorporateActionRow
from app.db.models import Earning as EarningRow
from app.db.models import Event, Order, Position, UniverseScreenRow
from app.market.calendar import CalendarProvider

UTC = ZoneInfo("UTC")
NY_TZ = ZoneInfo("America/New_York")


class MarketDataService:
    """Orchestrates data fetches, the bar cache and stored calendar data."""

    def __init__(
        self,
        sources: DataSources,
        calendar: CalendarProvider,
        config: AppConfig,
        session_factory: sessionmaker[Session] | None = None,
        bar_cache: BarCache | None = None,
    ) -> None:
        self.sources = sources
        self.calendar = calendar
        self.config = config
        self._session_factory = session_factory or get_session_maker()
        self.bars_cache = bar_cache or BarCache(self._session_factory)
        self.latest_fx: DataResult[FXRate | None] | None = None
        self.latest_earnings: DataResult[int] | None = None

    def universe(self) -> list[str]:
        """The cycle universe — the list every data refresh and cycle runs.

        In ``screened`` mode the newest successful screen hands its
        eligible rows to cycles in rank order; in ``fixed`` mode (or
        before the first screened screen has run) the configured symbols
        are used. Held symbols and SPY are always added on top, so a
        position is still managed outside the eligible list and the
        regime / shock-brake baseline never disappears (docs/SPEC.md,
        Larger universe).
        """
        screened = self.screened_rows()
        base = (
            [row.symbol for row in screened]
            if screened is not None
            else list(self.config.get("universe", "symbols", default=[]))
        )
        extras = self._held_symbols() | {"SPY"}
        return base + sorted(extras - set(base))

    def screened_rows(self) -> list[UniverseScreenRow] | None:
        """Eligible rows of the screen currently in effect, in rank order.

        Returns ``None`` while cycles run the fixed list: no screen has
        run yet, or the newest successful screen ran in ``fixed`` mode.
        A failed screen writes no success event, so the previous list
        stays in effect (docs/SPEC.md, Larger universe — a switch applies
        at the next daily screen, never mid-session).
        """
        with self._session_factory() as session:
            event = session.execute(
                select(Event)
                .where(Event.event_type == "universe_screen")
                .order_by(Event.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            details = dict(event.details or {}) if event is not None else {}
            if event is None or details.get("mode") != "screened":
                return None
            try:
                session_date = date.fromisoformat(str(details["session_date"]))
            except (KeyError, ValueError):
                return None
            rows = session.execute(
                select(UniverseScreenRow)
                .where(
                    UniverseScreenRow.session_date == session_date,
                    UniverseScreenRow.status == "eligible",
                )
                .order_by(UniverseScreenRow.rank)
            ).scalars()
            return list(rows)

    def universe_mode(self) -> str:
        """Which list cycles run right now — ``screened`` or ``fixed``."""
        return "screened" if self.screened_rows() is not None else "fixed"

    def _held_symbols(self) -> set[str]:
        """Open-position symbols plus symbols with a live entry order."""
        with self._session_factory() as session:
            held = {
                row[0]
                for row in session.execute(
                    select(Position.symbol).where(Position.closed_at.is_(None))
                ).all()
            }
            held.update(
                row[0]
                for row in session.execute(
                    select(Order.symbol).where(
                        Order.leg == "entry",
                        Order.status.in_(OPEN_ORDER_STATUSES),
                    )
                ).all()
            )
        return held

    def recent_sessions(self, today: date, count: int) -> list[CalendarDay]:
        """The ``count`` most recent trading sessions ending at ``today``."""
        days: list[CalendarDay] = []
        cursor = today
        while len(days) < count:
            session = self.calendar.session_for(cursor)
            if session is not None:
                days.append(session)
            prev = self.calendar.previous_trading_day(cursor)
            if prev is None:
                break
            cursor = prev.date
        return sorted(days, key=lambda d: d.date)

    async def refresh_bars(
        self,
        start: datetime,
        end: datetime,
        symbols: list[str] | None = None,
    ) -> dict[str, DataResult[int]]:
        """Fetch and cache 5-minute bars — one multi-symbol request.

        The eligible list at scale is too long for per-symbol calls; the
        bar source takes the whole universe in one request (docs/SPEC.md,
        Cycles at scale). ``symbols`` defaults to ``universe()``.
        """
        wanted = symbols if symbols is not None else self.universe()
        if not wanted:
            return {}
        fetched = await self.sources.bars.fetch_5m_bars(wanted, start, end)
        if fetched.error is not None:
            return {
                symbol: DataResult(
                    data=0, error=fetched.error, source=fetched.source
                )
                for symbol in wanted
            }
        grouped: dict[str, list[Bar5m]] = {symbol: [] for symbol in wanted}
        for bar in fetched.data or []:
            grouped.setdefault(bar.symbol, []).append(bar)
        return {
            symbol: DataResult(
                data=self.bars_cache.store(grouped[symbol]),
                source=fetched.source,
            )
            for symbol in wanted
        }

    async def refresh_history(self, today: date) -> dict[str, DataResult[int]]:
        """Fill the cache with ``history_sessions`` sessions of 5-minute bars."""
        sessions = int(self.config.get("schedule", "history_sessions", default=60))
        # Walk back through the trading calendar until we have enough sessions.
        days = self.recent_sessions(today, sessions)
        if not days:
            return {
                symbol: DataResult(data=0, error="no calendar days loaded", source="calendar")
                for symbol in self.universe()
            }
        start = min(days, key=lambda d: d.open).open.astimezone(UTC)
        end = max(days, key=lambda d: d.close).close.astimezone(UTC)
        return await self.refresh_bars(start, end)

    def prune_5m_bars(self, now: datetime) -> int:
        """Drop cached 5m bars older than ``bars_5m_keep_sessions`` sessions.

        Called after the daily backup: bars older than the kept window
        (default 120 sessions) are removed from the cache — daily bars
        are untouched (docs/SPEC.md, Cycles at scale).
        """
        keep = int(
            self.config.get("schedule", "bars_5m_keep_sessions", default=120)
        )
        sessions = self.recent_sessions(now.astimezone(NY_TZ).date(), keep)
        if not sessions:
            return 0
        return self.bars_cache.prune_5m(min(s.open for s in sessions))

    def get_5m_bars(self, symbol: str, start: datetime, end: datetime) -> list[Bar5m]:
        return self.bars_cache.get(symbol, start, end)

    def get_1h_bars(self, symbol: str, session: CalendarDay) -> list[Bar1h]:
        bars_5m = self.get_5m_bars(symbol, session.open, session.close)
        return aggregate_1h(symbol, bars_5m, session)

    def get_5m_bars_sessions(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> list[Bar5m]:
        """One cache read covering the whole span of the given sessions."""
        if not sessions:
            return []
        start = min(s.open for s in sessions)
        end = max(s.close for s in sessions)
        return self.get_5m_bars(symbol, start, end)

    def get_1h_bars_range(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> list[Bar1h]:
        """Session-aligned 1h bars across several sessions, in order."""
        per_session = bars_per_session(
            self.get_5m_bars_sessions(symbol, sessions), sessions
        )
        return [
            bar
            for session in sessions
            for bar in aggregate_1h(symbol, per_session[session.date], session)
        ]

    async def refresh_daily(self, today: date) -> dict[str, DataResult[int]]:
        """Fetch and cache daily bars for ``daily_history_sessions`` sessions."""
        count = int(
            self.config.get("schedule", "daily_history_sessions", default=260)
        )
        sessions = self.recent_sessions(today, count)
        if not sessions:
            return {
                symbol: DataResult(data=0, error="no calendar days loaded", source="calendar")
                for symbol in self.universe()
            }
        start, end = sessions[0].date, sessions[-1].date
        results: dict[str, DataResult[int]] = {}
        for symbol in self.universe():
            fetched = await self.sources.bars.fetch_daily_bars(symbol, start, end)
            if fetched.error is not None:
                results[symbol] = DataResult(data=0, error=fetched.error, source=fetched.source)
                continue
            inserted = self.bars_cache.store_daily(fetched.data)
            results[symbol] = DataResult(data=inserted, source=fetched.source)
        return results

    def get_daily_bars(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> list[DailyBar]:
        """Daily bars from the bars_1d cache, one per given session."""
        if not sessions:
            return []
        wanted = {s.date for s in sessions}
        return [
            bar
            for bar in self.bars_cache.get_daily(
                symbol, sessions[0].date, sessions[-1].date
            )
            if bar.date in wanted
        ]

    async def refresh_earnings(self, start: date, end: date) -> DataResult[int]:
        result = await self.sources.earnings.fetch_earnings(self.universe(), start, end)
        if result.error is not None:
            self.latest_earnings = DataResult(
                data=0, error=result.error, source=result.source
            )
            return self.latest_earnings
        stored = 0
        with self._session_factory() as session:
            # Upsert: a Finnhub revision changes time_of_day in place, and a
            # report cancelled or moved out of the window is pruned — a stale
            # row would still trigger blackouts and earnings exits.
            seen: set[tuple[str, datetime]] = set()
            for event in result.data:
                day = datetime.combine(event.date, time.min)
                seen.add((event.symbol, day))
                exists = session.execute(
                    select(EarningRow).where(
                        EarningRow.symbol == event.symbol,
                        EarningRow.date == day,
                    )
                ).scalar_one_or_none()
                if exists is None:
                    session.add(
                        EarningRow(
                            symbol=event.symbol,
                            date=day,
                            time_of_day=event.time_of_day,
                            hour_raw=event.hour_raw,
                            fetched_at=_naive_utc(event.fetched_at),
                        )
                    )
                    stored += 1
                else:
                    exists.time_of_day = event.time_of_day
                    exists.hour_raw = event.hour_raw
                    exists.fetched_at = _naive_utc(event.fetched_at)
            stale = session.execute(
                select(EarningRow).where(
                    EarningRow.symbol.in_(self.universe()),
                    EarningRow.date >= datetime.combine(start, time.min),
                    EarningRow.date <= datetime.combine(end, time.max),
                )
            ).scalars().all()
            for row in stale:
                if (row.symbol, row.date) not in seen:
                    session.delete(row)
            session.commit()
        self.latest_earnings = DataResult(data=stored, source=result.source)
        return self.latest_earnings

    async def refresh_corporate_actions(self, start: date, end: date) -> DataResult[int]:
        result = await self.sources.corporate_actions.fetch_corporate_actions(
            self.universe(), start, end
        )
        if result.error is not None:
            return DataResult(data=0, error=result.error, source=result.source)
        stored = 0
        with self._session_factory() as session:
            for action in result.data:
                day = datetime.combine(action.ex_date, time.min)
                exists = session.execute(
                    select(CorporateActionRow).where(
                        CorporateActionRow.symbol == action.symbol,
                        CorporateActionRow.action_type == action.action_type,
                        CorporateActionRow.date == day,
                    )
                ).scalar_one_or_none()
                if exists is None:
                    session.add(
                        CorporateActionRow(
                            symbol=action.symbol,
                            action_type=action.action_type,
                            date=day,
                            ratio_or_amount=action.amount_or_ratio,
                        )
                    )
                    stored += 1
            session.commit()
        return DataResult(data=stored, source=result.source)
