"""Shared risk math (docs/SPEC.md, Risk management).

``trade_risk`` is the one function that computes stop distance, per-share
risk and reward to risk; rules, gates and the GUI all call it. All money and
prices are Decimal; shares are always whole.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
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
class WorkingEntry:
    """An entry order still working at the broker: submitted, accepted or
    partially filled and not yet cancelled, expired or rejected
    (docs/SPEC.md, Risk management — Working entry orders). Only the
    unfilled quantity counts; filled shares are a position and are never
    counted twice."""

    symbol: str
    unfilled_qty: int
    limit_price: Decimal | None
    stop_price: Decimal | None  # bracket stop leg; None → worst case


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
    risk_factor: Decimal = Decimal(1),
) -> Sizing:
    """Whole-share size after the spec's sizing limits, each named in ``limited_by``.

    Shares = risk budget / (entry - stop), rounded down, then reduced to fit
    the max position size, the remaining open-risk budget and the cash.
    ``risk_factor`` shrinks the risk budget after a losing streak.
    """
    risk_pct = Decimal(str(config.get("risk", "risk_per_trade_pct", default=0.5)))
    risk_pct = risk_pct * risk_factor
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

    # The spec's gate order — blocked_by names the constraint that
    # actually bound the size; ties report in this order, not sorted.
    candidates = [
        ("risk_per_trade", shares_for(risk_budget, risk.stop_distance)),
        ("max_position", shares_for(max_position_value, entry)),
        ("open_risk", shares_for(risk_room, risk.stop_distance)),
        ("cash", shares_for(cash, entry)),
    ]
    shares = min(n for _name, n in candidates)
    limited_by = [name for name, n in candidates if n == shares]
    return Sizing(shares=shares, risk_budget=risk_budget, limited_by=limited_by)


def open_risk(positions: Iterable[tuple[int, Decimal, Decimal]]) -> Decimal:
    """Sum of qty x (entry - stop) over positions; a stop above entry adds 0."""
    total = Decimal(0)
    for qty, entry, stop in positions:
        total += qty * max(Decimal(0), entry - stop)
    return total


@dataclass(frozen=True)
class CommittedEntries:
    """(risk, cash) already spent by working entry orders, plus the
    symbols that had no usable price at all."""

    risk: Decimal
    cash: Decimal
    unpriced: tuple[str, ...] = ()


def committed_entries(
    entries: Iterable[WorkingEntry],
    market_price: Callable[[str], Decimal | None] | None = None,
) -> CommittedEntries:
    """(risk, cash) already spent by working entry orders.

    docs/SPEC.md, Working entry orders: unfilled qty x (limit - stop)
    counts toward open risk, unfilled qty x limit toward cash; an entry
    whose stop leg is missing counts its full committed value as risk —
    the conservative worst case. An entry without a limit price counts
    at the current market price when the caller passes one, never zero;
    symbols with no usable price at all come back in ``unpriced`` so
    the entry gates can fail closed.
    """
    risk = Decimal(0)
    cash = Decimal(0)
    unpriced: list[str] = []
    for e in entries:
        price = e.limit_price
        if price is None and market_price is not None:
            price = market_price(e.symbol)
        if price is None:
            unpriced.append(e.symbol)
            continue
        committed = e.unfilled_qty * price
        cash += committed
        if e.stop_price is not None:
            risk += e.unfilled_qty * max(Decimal(0), price - e.stop_price)
        else:
            risk += committed
    return CommittedEntries(risk=risk, cash=cash, unpriced=tuple(unpriced))


def session_pnl_pct(equity: Decimal, last_equity: Decimal) -> Decimal:
    """Session P&L vs the previous close's equity; the daily-loss input."""
    if last_equity <= 0:
        return Decimal(0)
    return (equity - last_equity) / last_equity * 100


def drawdown_pct(equity: Decimal, peak_equity: Decimal) -> Decimal:
    """How far equity is under the run's peak; the drawdown-kill input."""
    if peak_equity <= 0:
        return Decimal(0)
    return (peak_equity - equity) / peak_equity * 100


def streak_risk_factor(losing_streak: int, config: AppConfig) -> Decimal:
    """The losing-streak multiplier on the risk budget (1 under the limit)."""
    streak_len = int(config.get("risk", "losing_streak", default=3))
    if streak_len > 0 and losing_streak >= streak_len:
        return Decimal(
            str(config.get("risk", "losing_streak_risk_factor", default=0.5))
        )
    return Decimal(1)
