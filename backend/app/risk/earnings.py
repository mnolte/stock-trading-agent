"""Earnings handling and the day-trade exit rule, shared by live and shadows.

docs/SPEC.md, Risk management: a held symbol that reports before the next
session's close is exited in the last cycle the day before (an unknown time
of day counts as before the open), unless "Hold through earnings" is on.
Alpaca integration, PDT: the agent never closes a position on the day it
opened it. Live cycles and shadow strategies both call these functions —
the rules are never copied.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Earning
from app.market.clock import NY_TZ, UTC

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.market.calendar import CalendarProvider


def _ny_date(dt: datetime) -> date:
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(NY_TZ).date()


def exit_blocked_same_day(opened_at: datetime, now: datetime) -> bool:
    """The day-trade rule: no agent exit on the New York day of the entry."""
    return _ny_date(opened_at) == _ny_date(now)


def next_earnings(
    session: Session, symbols: list[str], now: datetime
) -> dict[str, tuple[date, str]]:
    """Next earnings (date, time_of_day) per symbol, today or later."""
    today = now.astimezone(NY_TZ).date()
    rows = session.execute(
        select(Earning.symbol, Earning.date, Earning.time_of_day)
        .where(
            Earning.symbol.in_(symbols),
            Earning.date >= datetime.combine(today, datetime.min.time()),
        )
        .order_by(Earning.date)
    ).all()
    result: dict[str, tuple[date, str]] = {}
    for symbol, day, tod in rows:
        result.setdefault(symbol, (day.date(), tod))
    return result


def earnings_exit_due(
    symbol: str,
    earnings: dict[str, tuple[date, str]],
    now: datetime,
    *,
    last_cycle: bool,
    calendar: CalendarProvider,
    config: AppConfig,
) -> bool:
    """Held symbol reports before the next session's close → exit in the
    last cycle the day before. Unknown time of day counts as before open."""
    if not last_cycle:
        return False
    if config.get("risk", "hold_through_earnings", default=False):
        return False
    info = earnings.get(symbol)
    if info is None:
        return False
    event_date, time_of_day = info
    today = now.astimezone(NY_TZ).date()
    # A report after today's close still counts as "before the next
    # session's close": sell in this last cycle so the position is never
    # held through the release.
    if event_date == today:
        return time_of_day == "after_close"
    next_day = calendar.next_trading_day(today)
    if next_day is None or event_date != next_day.date:
        return False
    # Reports at next close (after_close) are handled at that session's
    # last cycle, not today's; an unknown time counts as before open.
    return time_of_day != "after_close"
