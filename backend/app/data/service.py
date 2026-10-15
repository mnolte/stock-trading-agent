from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.aggregate import aggregate_1h
from app.data.cache import BarCache, _naive_utc
from app.data.factory import DataSources
from app.data.models import Bar1h, Bar5m, CalendarDay, DataResult, FXRate
from app.db.base import get_session_maker
from app.db.models import CorporateAction as CorporateActionRow
from app.db.models import Earning as EarningRow
from app.market.calendar import CalendarProvider

UTC = ZoneInfo("UTC")


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

    def universe(self) -> list[str]:
        return list(self.config.get("universe", "symbols", default=[]))

    async def refresh_bars(self, start: datetime, end: datetime) -> dict[str, DataResult[int]]:
        """Fetch and cache 5-minute bars for every symbol in the universe."""
        results: dict[str, DataResult[int]] = {}
        for symbol in self.universe():
            fetched = await self.sources.bars.fetch_5m_bars(symbol, start, end)
            if fetched.error is not None:
                results[symbol] = DataResult(data=0, error=fetched.error, source=fetched.source)
                continue
            inserted = self.bars_cache.store(fetched.data)
            results[symbol] = DataResult(data=inserted, source=fetched.source)
        return results

    async def refresh_history(self, today: date) -> dict[str, DataResult[int]]:
        """Fill the cache with ``history_sessions`` sessions of 5-minute bars."""
        sessions = int(self.config.get("schedule", "history_sessions", default=60))
        days: list[CalendarDay] = []
        cursor = today
        # Walk back through the trading calendar until we have enough sessions.
        while len(days) < sessions:
            session = self.calendar.session_for(cursor)
            if session is not None:
                days.append(session)
            prev = self.calendar.previous_trading_day(cursor)
            if prev is None:
                break
            cursor = prev.date
        if not days:
            return {
                symbol: DataResult(data=0, error="no calendar days loaded", source="calendar")
                for symbol in self.universe()
            }
        start = min(days, key=lambda d: d.open).open.astimezone(UTC)
        end = max(days, key=lambda d: d.close).close.astimezone(UTC)
        return await self.refresh_bars(start, end)

    def get_5m_bars(self, symbol: str, start: datetime, end: datetime) -> list[Bar5m]:
        return self.bars_cache.get(symbol, start, end)

    def get_1h_bars(self, symbol: str, session: CalendarDay) -> list[Bar1h]:
        bars_5m = self.get_5m_bars(symbol, session.open, session.close)
        return aggregate_1h(symbol, bars_5m, session)

    async def refresh_earnings(self, start: date, end: date) -> DataResult[int]:
        result = await self.sources.earnings.fetch_earnings(self.universe(), start, end)
        if result.error is not None:
            return DataResult(data=0, error=result.error, source=result.source)
        stored = 0
        with self._session_factory() as session:
            for event in result.data:
                exists = session.execute(
                    select(EarningRow).where(
                        EarningRow.symbol == event.symbol,
                        EarningRow.date == datetime.combine(event.date, time.min),
                    )
                ).scalar_one_or_none()
                if exists is None:
                    session.add(
                        EarningRow(
                            symbol=event.symbol,
                            date=datetime.combine(event.date, time.min),
                            time_of_day=event.time_of_day,
                            fetched_at=_naive_utc(event.fetched_at),
                        )
                    )
                    stored += 1
            session.commit()
        return DataResult(data=stored, source=result.source)

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
