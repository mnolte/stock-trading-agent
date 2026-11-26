"""Per-trade math shared by Performance, reviews and shadow strategies.

Kept free of data-source imports so the shadow package can use the same
functions without loading any broker or market-data client.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Bar1d, CorporateAction, Position

logger = logging.getLogger(__name__)

__all__ = ["_dividends", "_trade_r", "close_lookup", "daily_close_before"]


def _trade_r(position: Position, stop: Decimal | None) -> Decimal | None:
    """Reward-to-risk of a closed trade against its entry stop."""
    if (
        stop is None
        or position.realized_pnl_raw is None
        or position.qty == 0
        or position.avg_entry_price <= stop
    ):
        return None
    risk = (position.avg_entry_price - stop) * position.qty
    return position.realized_pnl_raw / risk


def daily_close_before(session: Session, symbol: str, day: date) -> Decimal | None:
    """Close of the last stored daily bar before ``day`` — the session
    before a stock dividend's ex-date (docs/SPEC.md, Dividends, like for
    like)."""
    return session.execute(
        select(Bar1d.close)
        .where(Bar1d.symbol == symbol, Bar1d.date < day)
        .order_by(Bar1d.date.desc())
        .limit(1)
    ).scalar_one_or_none()


def close_lookup(session: Session) -> Callable[[str, date], Decimal | None]:
    """A cached ``daily_close_before`` for one caller's dividend pass."""
    cache: dict[tuple[str, date], Decimal | None] = {}

    def lookup(symbol: str, day: date) -> Decimal | None:
        key = (symbol, day)
        if key not in cache:
            cache[key] = daily_close_before(session, symbol, day)
        return cache[key]

    return lookup


def _dividends(
    actions: Sequence[CorporateAction],
    symbol: str,
    start: date,
    end: date,
    qty: int,
    *,
    close_before: Callable[[str, date], Decimal | None] | None = None,
) -> Decimal:
    """Dividends the position was entitled to: ex-date inside the hold.

    Cash dividends add amount × qty. A stock dividend (``rate`` = new
    shares per share) adds its value: qty × rate × the close of the
    session before the ex-date ÷ (1 + rate) — the post-ex price times the
    new shares. Without a stored close it adds nothing and logs one
    warning naming the symbol and date.
    """
    total = Decimal(0)
    for action in actions:
        if action.symbol != symbol:
            continue
        # Ex-date must be after the buy (held at the prior close)
        # and on or before the sell.
        ex_date = action.date.date()
        if not (start < ex_date <= end) or action.ratio_or_amount is None:
            continue
        if action.action_type == "dividend":
            total += action.ratio_or_amount * qty
        elif action.action_type == "stock_dividend":
            close = (
                close_before(symbol, ex_date) if close_before is not None else None
            )
            if close is None or close <= 0:
                logger.warning(
                    "%s stock dividend ex-date %s: no daily close before "
                    "the ex-date; not credited",
                    symbol,
                    ex_date,
                )
                continue
            rate = action.ratio_or_amount
            total += qty * rate * close / (1 + rate)
    return total
