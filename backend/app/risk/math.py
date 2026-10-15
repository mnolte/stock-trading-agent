"""Shared risk math (docs/SPEC.md, Risk management).

``trade_risk`` is the one function that computes stop distance, per-share
risk and reward to risk; rules, gates and the GUI all call it. All money and
prices are Decimal; shares are always whole.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import AppConfig


@dataclass(frozen=True)
class TradeRisk:
    entry: Decimal
    stop: Decimal
    target: Decimal | None
    stop_distance: Decimal  # entry - stop, the per-share risk
    stop_distance_pct: Decimal | None  # stop_distance / entry * 100
    atr_multiple: Decimal | None  # stop_distance / atr
    reward_to_risk: Decimal | None  # (target - entry) / stop_distance
    valid: bool  # entry > 0 and 0 < stop < entry


def trade_risk(
    entry: Decimal,
    stop: Decimal,
    target: Decimal | None = None,
    atr: Decimal | None = None,
) -> TradeRisk:
    """Compute stop distance, per-share risk and reward to risk once."""
    stop_distance = entry - stop
    valid = entry > 0 and stop > 0 and stop < entry
    stop_distance_pct = (
        stop_distance / entry * 100 if entry > 0 else None
    )
    atr_multiple = (
        stop_distance / atr if atr is not None and atr > 0 and valid else None
    )
    reward_to_risk = (
        (target - entry) / stop_distance
        if target is not None and stop_distance > 0
        else None
    )
    return TradeRisk(
        entry=entry,
        stop=stop,
        target=target,
        stop_distance=stop_distance,
        stop_distance_pct=stop_distance_pct,
        atr_multiple=atr_multiple,
        reward_to_risk=reward_to_risk,
        valid=valid,
    )


@dataclass(frozen=True)
class Sizing:
    shares: int
    risk_budget: Decimal
    limited_by: list[str]  # caps that bound the final share count


def position_size(
    equity: Decimal,
    entry: Decimal,
    stop: Decimal,
    *,
    cash: Decimal,
    current_open_risk: Decimal,
    config: AppConfig,
) -> Sizing:
    """Whole-share size after the spec's sizing limits, each named in ``limited_by``.

    Shares = risk budget / (entry - stop), rounded down, then reduced to fit
    the max position size, the remaining open-risk budget and the cash.
    """
    risk_pct = Decimal(str(config.get("risk", "risk_per_trade_pct", default=0.5)))
    max_position_pct = Decimal(
        str(config.get("risk", "max_position_pct", default=20))
    )
    max_open_risk_pct = Decimal(
        str(config.get("risk", "max_open_risk_pct", default=2.5))
    )

    risk_budget = equity * risk_pct / 100
    open_risk_budget = equity * max_open_risk_pct / 100
    risk_room = max(Decimal(0), open_risk_budget - current_open_risk)
    max_position_value = equity * max_position_pct / 100

    risk = trade_risk(entry, stop)
    if not risk.valid or risk.stop_distance <= 0:
        return Sizing(shares=0, risk_budget=risk_budget, limited_by=["stop_distance"])

    def shares_for(budget: Decimal, per_share: Decimal) -> int:
        return int((budget / per_share).to_integral_value(rounding=ROUND_FLOOR))

    candidates = {
        "risk_per_trade": shares_for(risk_budget, risk.stop_distance),
        "max_position": shares_for(max_position_value, entry),
        "open_risk": shares_for(risk_room, risk.stop_distance),
        "cash": shares_for(cash, entry),
    }
    shares = min(candidates.values())
    limited_by = sorted(name for name, n in candidates.items() if n == shares)
    return Sizing(shares=shares, risk_budget=risk_budget, limited_by=limited_by)


def open_risk(positions: Iterable[tuple[int, Decimal, Decimal]]) -> Decimal:
    """Sum of qty x (entry - stop) over positions; a stop above entry adds 0."""
    total = Decimal(0)
    for qty, entry, stop in positions:
        total += qty * max(Decimal(0), entry - stop)
    return total
