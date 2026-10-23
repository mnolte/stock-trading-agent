"""The dashboard risk budget card (docs/SPEC.md, GUI — Risk budget card).

The card reads the same ``GateContext`` the entry gates see and calls the
same ``app.risk.math`` functions, so a meter can never drift from the gate
it mirrors. Nothing here trades; it is a read-out of the limits in use.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING

from app.risk.gates import resolve_sectors
from app.risk.math import (
    committed_entries,
    drawdown_pct,
    open_risk,
    session_pnl_pct,
    streak_risk_factor,
)

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.risk.gates import GateContext

# A meter turns amber above this share of its limit, red at the limit
# (docs/SPEC.md, GUI — Risk budget card).
METER_AMBER_FRACTION = Decimal("0.75")


@dataclass(frozen=True)
class Meter:
    """One limit meter: the share in use, the cap and the display level."""

    used_pct: Decimal  # percent of equity under the limit (a magnitude)
    limit_pct: Decimal
    level: str  # "normal" | "amber" | "red"


@dataclass(frozen=True)
class SectorUse:
    """Open positions + working entries in one sector vs the sector cap."""

    sector: str
    used: int
    cap: int


@dataclass(frozen=True)
class RiskBudget:
    """Every figure on the card; each one is a gate input."""

    open_risk_usd: Decimal
    open_risk_limit_pct: Decimal
    open_risk: Meter | None  # None until equity is known
    daily_loss: Meter
    session_pnl_pct: Decimal
    drawdown: Meter
    room_trades: int
    room_limited_by: str  # "positions" | "open_risk" | "cash"
    sectors: tuple[SectorUse, ...]
    risk_per_trade_pct: Decimal  # after the losing-streak factor
    losing_streak: int
    losing_streak_len: int
    losing_streak_factor: Decimal
    shock_brake_on: bool
    shock_1h_limit_pct: Decimal
    shock_session_limit_pct: Decimal
    spy_1h_pct: Decimal | None
    spy_session_pct: Decimal | None
    day_trades_5d: int
    day_trades_max: int
    open_count: int  # open positions + working entries
    max_positions: int


def meter_level(used_pct: Decimal, limit_pct: Decimal) -> str:
    """Amber above 75% of the limit, red at it (docs/SPEC.md, GUI)."""
    if limit_pct <= 0:
        return "red" if used_pct > 0 else "normal"
    fraction = used_pct / limit_pct
    if fraction >= 1:
        return "red"
    if fraction > METER_AMBER_FRACTION:
        return "amber"
    return "normal"


def _floor_div(amount: Decimal, per: Decimal) -> int:
    """How many whole units of ``per`` fit in ``amount`` (never negative)."""
    if per <= 0:
        return 0
    return max(0, int((amount / per).to_integral_value(rounding=ROUND_FLOOR)))


def risk_budget(
    ctx: GateContext,
    config: AppConfig,
    *,
    spy_1h_pct: Decimal | None = None,
    spy_session_pct: Decimal | None = None,
) -> RiskBudget:
    """The card's figures from one gate context."""
    working_risk, working_cash = committed_entries(ctx.working_entries)
    position_risk = open_risk(
        (p.qty, p.avg_entry_price, p.current_stop_price or Decimal(0)) for p in ctx.open_positions
    )
    risk_usd = position_risk + working_risk

    equity = ctx.equity
    open_risk_cap = Decimal(str(config.get("risk", "max_open_risk_pct", default=2.5)))
    open_meter = None
    if equity > 0:
        used = risk_usd / equity * 100
        open_meter = Meter(used, open_risk_cap, meter_level(used, open_risk_cap))

    daily_limit = Decimal(str(config.get("risk", "daily_loss_limit_pct", default=2)))
    pnl = session_pnl_pct(equity, ctx.last_equity)
    daily_used = max(Decimal(0), -pnl)

    kill_pct = Decimal(str(config.get("risk", "drawdown_kill_pct", default=8)))
    dd = drawdown_pct(equity, ctx.peak_equity)

    max_positions = int(config.get("risk", "max_open_positions", default=4))
    open_count = len(ctx.open_positions) + len(ctx.working_entries)

    factor = streak_risk_factor(ctx.losing_streak, config)
    per_trade_pct = Decimal(str(config.get("risk", "risk_per_trade_pct", default=0.5))) * factor
    per_trade_usd = equity * per_trade_pct / 100
    max_position_usd = (
        equity * Decimal(str(config.get("risk", "max_position_pct", default=20))) / 100
    )
    risk_room = max(Decimal(0), equity * open_risk_cap / 100 - risk_usd)
    cash = (
        max(Decimal(0), ctx.cash - working_cash)
        if config.get("risk", "cash_only", default=True)
        else ctx.equity
    )

    # Room for more trades: the smallest of positions left, open risk
    # left ÷ current risk per trade and cash ÷ max position size, each
    # rounded down (docs/SPEC.md, GUI — Risk budget card).
    candidates = {
        "positions": max(0, max_positions - open_count),
        "open_risk": _floor_div(risk_room, per_trade_usd),
        "cash": _floor_div(cash, max_position_usd),
    }
    room = min(candidates.values())
    room_limited_by = next(name for name, count in candidates.items() if count == room)

    sector_cap = int(config.get("risk", "max_per_sector", default=2))
    sector_map = ctx.sectors if ctx.sectors is not None else resolve_sectors(config)
    counts: dict[str, int] = {}
    for symbol in [p.symbol for p in ctx.open_positions] + [e.symbol for e in ctx.working_entries]:
        sector = sector_map.get(symbol)
        if sector is not None:
            counts[sector] = counts.get(sector, 0) + 1

    return RiskBudget(
        open_risk_usd=risk_usd,
        open_risk_limit_pct=open_risk_cap,
        open_risk=open_meter,
        daily_loss=Meter(daily_used, daily_limit, meter_level(daily_used, daily_limit)),
        session_pnl_pct=pnl,
        drawdown=Meter(dd, kill_pct, meter_level(dd, kill_pct)),
        room_trades=room,
        room_limited_by=room_limited_by,
        sectors=tuple(SectorUse(sector, count, sector_cap) for sector, count in counts.items()),
        risk_per_trade_pct=per_trade_pct,
        losing_streak=ctx.losing_streak,
        losing_streak_len=int(config.get("risk", "losing_streak", default=3)),
        losing_streak_factor=factor,
        shock_brake_on=ctx.shock_brake_1h or ctx.shock_brake_session,
        shock_1h_limit_pct=Decimal(str(config.get("risk", "shock_brake_1h_pct", default=1.5))),
        shock_session_limit_pct=Decimal(
            str(config.get("risk", "shock_brake_session_pct", default=2.5))
        ),
        spy_1h_pct=spy_1h_pct,
        spy_session_pct=spy_session_pct,
        day_trades_5d=ctx.day_trades_5d,
        day_trades_max=int(config.get("risk", "pdt_max_day_trades", default=3)),
        open_count=open_count,
        max_positions=max_positions,
    )
