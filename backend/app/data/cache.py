from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from itertools import batched
from typing import NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session, sessionmaker

from app.data.models import Bar5m, CalendarDay, DailyBar
from app.db.base import get_session_maker
from app.db.models import Bar1d as Bar1dRow
from app.db.models import Bar5m as Bar5mRow

UTC = ZoneInfo("UTC")
NY_TZ = ZoneInfo("America/New_York")


class RawBar5m(NamedTuple):
    """A cached 5m bar as stored: floats, no Decimal construction.

    Aggregation paths (1h bars, bucketing) read this instead of ``Bar5m``
    so the per-row ``Decimal(str(...))`` work happens only on the handful
    of values their output actually keeps. Prices round-trip through
    SQLite REAL, so ``Decimal(str(x))`` here gives the same value the
    ``Bar5m`` path produces.
    """

    symbol: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    feed: str

    def to_bar5m(self) -> Bar5m:
        return Bar5m(
            symbol=self.symbol,
            start=self.start,
            open=Decimal(str(self.open)),
            high=Decimal(str(self.high)),
            low=Decimal(str(self.low)),
            close=Decimal(str(self.close)),
            volume=self.volume,
            feed=self.feed,
        )


def _naive_utc(dt: datetime) -> datetime:
    """SQLite stores naive datetimes; keep them as naive UTC."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _window(start: datetime, end: datetime) -> tuple[str, str]:
    """SQLite text bounds for a [start, end] window — stored starts are
    'YYYY-MM-DD HH:MM:SS.ffffff', so lexicographic bounds compare right."""
    return (
        _naive_utc(start).strftime("%Y-%m-%d %H:%M:%S.%f"),
        _naive_utc(end).strftime("%Y-%m-%d %H:%M:%S.%f"),
    )


class BarCache:
    """SQLite cache for 5-minute SIP bars (table ``bars_5m``)."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_maker()

    def store(self, bars: list[Bar5m]) -> int:
        """Upsert bars; returns number of new rows inserted."""
        if not bars:
            return 0
        keyed = [(b, b.symbol, _naive_utc(b.start)) for b in bars]
        keyed = list({(sym, start): (b, sym, start) for b, sym, start in keyed}.values())
        symbols = {sym for _b, sym, _start in keyed}
        naive_starts = list({start for _b, _sym, start in keyed})
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
                {
                    "symbol": sym,
                    "start": start,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "feed": b.feed,
                }
                for b, sym, start in keyed
                if (sym, start) not in existing
            ]
            if new_rows:
                session.execute(insert(Bar5mRow), new_rows)
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
                {
                    "symbol": b.symbol,
                    "date": b.date,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars
                if (b.symbol, b.date) not in existing
            ]
            if new_rows:
                session.execute(insert(Bar1dRow), new_rows)
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
                session.connection()
                .exec_driver_sql(
                    "SELECT date, open, high, low, close, volume FROM bars_1d"
                    " WHERE symbol = ? AND date >= ? AND date <= ?"
                    " ORDER BY date",
                    (symbol, start.isoformat(), end.isoformat()),
                )
                .fetchall()
            )
            return [
                DailyBar(
                    symbol=symbol,
                    date=date.fromisoformat(r[0]),
                    open=Decimal(str(r[1])),
                    high=Decimal(str(r[2])),
                    low=Decimal(str(r[3])),
                    close=Decimal(str(r[4])),
                    volume=r[5],
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

    def get_latest(
        self, symbol: str, start: datetime, end: datetime, limit: int
    ) -> list[Bar5m]:
        """The ``limit`` newest cached bars in [start, end], newest first."""
        lo, hi = _window(start, end)
        with self._session_factory() as session:
            rows = (
                session.connection()
                .exec_driver_sql(
                    "SELECT start, open, high, low, close, volume, feed"
                    " FROM bars_5m WHERE symbol = ? AND start >= ? AND start <= ?"
                    " ORDER BY start DESC LIMIT ?",
                    (symbol, lo, hi, limit),
                )
                .fetchall()
            )
            return [
                Bar5m(
                    symbol=symbol,
                    start=datetime.fromisoformat(r[0]).replace(tzinfo=UTC),
                    open=Decimal(str(r[1])),
                    high=Decimal(str(r[2])),
                    low=Decimal(str(r[3])),
                    close=Decimal(str(r[4])),
                    volume=r[5],
                    feed=r[6],
                )
                for r in rows
            ]

    def get_extremes(
        self, symbol: str, start: datetime, end: datetime
    ) -> tuple[tuple[datetime, Decimal], tuple[datetime, Decimal]] | None:
        """(start, high) of the highest bar and (start, low) of the lowest
        in [start, end] — the earliest bar wins a tie, matching
        ``max(bars, key=high)``/``min(bars, key=low)`` on an ordered list.
        """
        lo, hi = _window(start, end)
        with self._session_factory() as session:
            conn = session.connection()
            best = conn.exec_driver_sql(
                "SELECT start, high FROM bars_5m"
                " WHERE symbol = ? AND start >= ? AND start <= ?"
                " ORDER BY high DESC, start ASC LIMIT 1",
                (symbol, lo, hi),
            ).fetchone()
            worst = conn.exec_driver_sql(
                "SELECT start, low FROM bars_5m"
                " WHERE symbol = ? AND start >= ? AND start <= ?"
                " ORDER BY low ASC, start ASC LIMIT 1",
                (symbol, lo, hi),
            ).fetchone()
        if best is None or worst is None:
            return None
        return (
            (
                datetime.fromisoformat(best[0]).replace(tzinfo=UTC),
                Decimal(str(best[1])),
            ),
            (
                datetime.fromisoformat(worst[0]).replace(tzinfo=UTC),
                Decimal(str(worst[1])),
            ),
        )

    def get_touching(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        stop: Decimal | None,
        target: Decimal | None,
    ) -> list[Bar5m]:
        """Bars that could touch a stop or target: ``low <= stop`` or
        ``high >= target``, sorted by start.

        ``stop``/``target`` are the coarse bounds over every level that
        could be in play (the loosest stop, the tightest target); floats
        compare monotonically, so no Decimal touch is ever filtered out —
        the caller re-checks each candidate exactly.
        """
        if stop is None and target is None:
            return []
        lo, hi = _window(start, end)
        with self._session_factory() as session:
            rows = (
                session.connection()
                .exec_driver_sql(
                    # -inf/+inf stand in for a missing side — they never
                    # match a real bar, so the condition degrades cleanly.
                    "SELECT start, open, high, low, close, volume, feed"
                    " FROM bars_5m WHERE symbol = ? AND start >= ?"
                    " AND start <= ? AND (low <= ? OR high >= ?)"
                    " ORDER BY start",
                    (
                        symbol,
                        lo,
                        hi,
                        float(stop) if stop is not None else float("-inf"),
                        float(target) if target is not None else float("inf"),
                    ),
                )
                .fetchall()
            )
            return [
                Bar5m(
                    symbol=symbol,
                    start=datetime.fromisoformat(r[0]).replace(tzinfo=UTC),
                    open=Decimal(str(r[1])),
                    high=Decimal(str(r[2])),
                    low=Decimal(str(r[3])),
                    close=Decimal(str(r[4])),
                    volume=r[5],
                    feed=r[6],
                )
                for r in rows
            ]

    def get_rows(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[RawBar5m]:
        """Same rows as ``get``, without the per-bar Decimal work."""
        lo, hi = _window(start, end)
        with self._session_factory() as session:
            rows = (
                session.connection()
                .exec_driver_sql(
                    "SELECT start, open, high, low, close, volume, feed"
                    " FROM bars_5m WHERE symbol = ? AND start >= ? AND start <= ?"
                    " ORDER BY start",
                    (symbol, lo, hi),
                )
                .fetchall()
            )
            return [
                RawBar5m(
                    symbol,
                    datetime.fromisoformat(r[0]).replace(tzinfo=UTC),
                    r[1],
                    r[2],
                    r[3],
                    r[4],
                    r[5],
                    r[6],
                )
                for r in rows
            ]

    def get(self, symbol: str, start: datetime, end: datetime) -> list[Bar5m]:
        """Return cached bars for a symbol in [start, end], sorted by start."""
        return [row.to_bar5m() for row in self.get_rows(symbol, start, end)]
