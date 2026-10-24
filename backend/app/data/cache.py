from __future__ import annotations

from datetime import date, datetime
from itertools import batched
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
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
        bars = list({(b.symbol, _naive_utc(b.start)): b for b in bars}.values())
        symbols = {b.symbol for b in bars}
        naive_starts = list({_naive_utc(b.start) for b in bars})
        with self._session_factory() as session:
            existing = {
                (symbol, start)
                for symbol_chunk in batched(symbols, 400)
                for time_chunk in batched(naive_starts, 400)
                for symbol, start in session.execute(
                    select(Bar5mRow.symbol, Bar5mRow.start).where(
                        Bar5mRow.symbol.in_(symbol_chunk), Bar5mRow.start.in_(time_chunk),
                    )
                ).all()
            }
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

    def prune_5m(self, before: datetime) -> int:
        """Delete every 5m bar starting before ``before``; returns count."""
        with self._session_factory() as session:
            count = session.execute(
                select(func.count())
                .select_from(Bar5mRow)
                .where(Bar5mRow.start < _naive_utc(before))
            ).scalar_one()
            session.execute(
                delete(Bar5mRow).where(Bar5mRow.start < _naive_utc(before))
            )
            session.commit()
        return count

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
        bars = list({(b.symbol, b.date): b for b in bars}.values())
        symbols = {b.symbol for b in bars}
        dates = list({b.date for b in bars})
        with self._session_factory() as session:
            existing = {
                (symbol, day)
                for symbol_chunk in batched(symbols, 400)
                for date_chunk in batched(dates, 400)
                for symbol, day in session.execute(
                    select(Bar1dRow.symbol, Bar1dRow.date).where(
                        Bar1dRow.symbol.in_(symbol_chunk), Bar1dRow.date.in_(date_chunk),
                    )
                ).all()
            }
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

    def covered_session_counts(
        self, symbols: list[str], sessions: list[CalendarDay]
    ) -> dict[str, int]:
        """Covered New York sessions per symbol, in one grouped query.

        Same coverage as ``covered_session_dates`` but batched: cached bars
        only ever span regular sessions (13:30–21:00 UTC), so a bar's UTC
        date is always its New York session date and SQL ``date(start)``
        can do the grouping instead of converting every bar in Python.
        """
        counts = dict.fromkeys(symbols, 0)
        if not symbols or not sessions:
            return counts
        start = min(s.open for s in sessions)
        end = max(s.close for s in sessions)
        wanted = {s.date for s in sessions}
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(Bar5mRow.symbol, func.date(Bar5mRow.start))
                    .where(
                        Bar5mRow.symbol.in_(symbols),
                        Bar5mRow.start >= _naive_utc(start),
                        Bar5mRow.start <= _naive_utc(end),
                    )
                    .distinct()
                )
                .all()
            )
        for symbol, day in rows:
            if date.fromisoformat(day) in wanted:
                counts[symbol] += 1
        return counts

    def covered_daily_dates(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> set[date]:
        """Session dates with a cached daily bar, intersected with the
        wanted range — the daily twin of ``covered_session_dates``."""
        if not sessions:
            return set()
        wanted = {s.date for s in sessions}
        with self._session_factory() as session:
            dates = (
                session.execute(
                    select(Bar1dRow.date).where(
                        Bar1dRow.symbol == symbol,
                        Bar1dRow.date >= min(wanted),
                        Bar1dRow.date <= max(wanted),
                    )
                )
                .scalars()
                .all()
            )
        return set(dates) & wanted

    def daily_bar_counts(
        self, symbols: list[str], sessions: list[CalendarDay]
    ) -> dict[str, int]:
        """Cached daily bars per symbol on the given session dates."""
        counts = dict.fromkeys(symbols, 0)
        if not symbols or not sessions:
            return counts
        wanted = {s.date for s in sessions}
        start = min(wanted)
        end = max(wanted)
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(Bar1dRow.symbol, Bar1dRow.date).where(
                        Bar1dRow.symbol.in_(symbols),
                        Bar1dRow.date >= start,
                        Bar1dRow.date <= end,
                    )
                )
                .all()
            )
        for symbol, day in rows:
            if day in wanted:
                counts[symbol] += 1
        return counts

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

    def latest(self, symbol: str, before: datetime) -> Bar5m | None:
        with self._session_factory() as session:
            row = session.execute(
                select(Bar5mRow).where(
                    Bar5mRow.symbol == symbol,
                    Bar5mRow.start <= _naive_utc(before),
                ).order_by(Bar5mRow.start.desc()).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            return Bar5m(
                symbol=row.symbol, start=_aware_utc(row.start), open=row.open,
                high=row.high, low=row.low, close=row.close,
                volume=row.volume, feed=row.feed,
            )

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
