from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.data.models import Bar5m, CalendarDay, DailyBar
from app.db.base import get_session_maker
from app.db.models import Bar1d as Bar1dRow
from app.db.models import Bar5m as Bar5mRow

UTC = ZoneInfo("UTC")
NY_TZ = ZoneInfo("America/New_York")


def _naive_utc(dt: datetime) -> datetime:
    """SQLite stores naive datetimes; keep them as naive UTC."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class BarCache:
    """SQLite cache for 5-minute SIP bars (table ``bars_5m``)."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_maker()

    def store(self, bars: list[Bar5m]) -> int:
        """Upsert bars; returns number of new rows inserted."""
        if not bars:
            return 0
        symbols = {b.symbol for b in bars}
        naive_starts = [_naive_utc(b.start) for b in bars]
        with self._session_factory() as session:
            existing = set(
                session.execute(
                    select(Bar5mRow.symbol, Bar5mRow.start).where(
                        Bar5mRow.symbol.in_(symbols),
                        Bar5mRow.start.in_(naive_starts),
                    )
                ).all()
            )
            new_rows = [
                Bar5mRow(
                    symbol=b.symbol,
                    start=_naive_utc(b.start),
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                    feed=b.feed,
                )
                for b in bars
                if (b.symbol, _naive_utc(b.start)) not in existing
            ]
            session.add_all(new_rows)
            session.commit()
            return len(new_rows)

    def count(self, symbol: str) -> int:
        """Total cached bars for a symbol."""
        with self._session_factory() as session:
            return session.execute(
                select(func.count())
                .select_from(Bar5mRow)
                .where(Bar5mRow.symbol == symbol)
            ).scalar_one()

    def store_daily(self, bars: list[DailyBar]) -> int:
        """Upsert daily bars; returns number of new rows inserted."""
        if not bars:
            return 0
        symbols = {b.symbol for b in bars}
        dates = [b.date for b in bars]
        with self._session_factory() as session:
            existing = set(
                session.execute(
                    select(Bar1dRow.symbol, Bar1dRow.date).where(
                        Bar1dRow.symbol.in_(symbols),
                        Bar1dRow.date.in_(dates),
                    )
                ).all()
            )
            new_rows = [
                Bar1dRow(
                    symbol=b.symbol,
                    date=b.date,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                )
                for b in bars
                if (b.symbol, b.date) not in existing
            ]
            session.add_all(new_rows)
            session.commit()
            return len(new_rows)

    def count_daily(self, symbol: str) -> int:
        """Total cached daily bars for a symbol."""
        with self._session_factory() as session:
            return session.execute(
                select(func.count())
                .select_from(Bar1dRow)
                .where(Bar1dRow.symbol == symbol)
            ).scalar_one()

    def covered_session_dates(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> set[date]:
        """New York session dates with at least one cached 5m bar.

        ``sessions`` are ``CalendarDay``-like (open/close attributes); the
        result is intersected with their dates so stray bars outside the
        wanted range never count.
        """
        if not sessions:
            return set()
        start = min(s.open for s in sessions)
        end = max(s.close for s in sessions)
        with self._session_factory() as session:
            starts = (
                session.execute(
                    select(Bar5mRow.start)
                    .where(
                        Bar5mRow.symbol == symbol,
                        Bar5mRow.start >= _naive_utc(start),
                        Bar5mRow.start <= _naive_utc(end),
                    )
                    .distinct()
                )
                .scalars()
                .all()
            )
        wanted = {s.date for s in sessions}
        return {
            _aware_utc(s).astimezone(NY_TZ).date() for s in starts
        } & wanted

    def get_daily(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        """Return cached daily bars for a symbol in [start, end], by date."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(Bar1dRow)
                    .where(
                        Bar1dRow.symbol == symbol,
                        Bar1dRow.date >= start,
                        Bar1dRow.date <= end,
                    )
                    .order_by(Bar1dRow.date)
                )
                .scalars()
                .all()
            )
            return [
                DailyBar(
                    symbol=r.symbol,
                    date=r.date,
                    open=r.open,
                    high=r.high,
                    low=r.low,
                    close=r.close,
                    volume=r.volume,
                )
                for r in rows
            ]

    def get(self, symbol: str, start: datetime, end: datetime) -> list[Bar5m]:
        """Return cached bars for a symbol in [start, end], sorted by start."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(Bar5mRow)
                    .where(
                        Bar5mRow.symbol == symbol,
                        Bar5mRow.start >= _naive_utc(start),
                        Bar5mRow.start <= _naive_utc(end),
                    )
                    .order_by(Bar5mRow.start)
                )
                .scalars()
                .all()
            )
            return [
                Bar5m(
                    symbol=r.symbol,
                    start=_aware_utc(r.start),
                    open=r.open,
                    high=r.high,
                    low=r.low,
                    close=r.close,
                    volume=r.volume,
                    feed=r.feed,
                )
                for r in rows
            ]
