"""Per-trade math shared by Performance, reviews and shadow strategies.

Kept free of data-source imports so the shadow package can use the same
functions without loading any broker or market-data client.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from app.db.models import CorporateAction, Position

__all__ = ["_dividends", "_trade_r"]


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


def _dividends(
    actions: Sequence[CorporateAction],
    symbol: str,
    start: date,
    end: date,
    qty: int,
) -> Decimal:
    """Dividends the position was entitled to: ex-date inside the hold."""
    total = Decimal(0)
    for action in actions:
        if (
            action.symbol == symbol
            and action.action_type == "dividend"
            and start <= action.date.date() <= end
            and action.ratio_or_amount is not None
        ):
            total += action.ratio_or_amount * qty
    return total
