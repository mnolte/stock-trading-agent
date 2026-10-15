from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.data.models import Bar5m
from app.db.base import get_session_maker
from app.db.models import Bar5m as Bar5mRow

UTC = ZoneInfo("UTC")


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
