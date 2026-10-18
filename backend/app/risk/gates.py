"""Risk gates for entries (docs/SPEC.md, Risk management).

Gates run in code after the model answers, in the spec's order; the first
gate that fails blocks the action and is recorded by name on the
decision's ``gates_result``. Sizing limits never block — they shrink the
share count through the shared ``position_size`` function; a size of zero
blocks with the name of the limit that hit zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from app.db.models import Position
from app.risk.math import (
    Sizing,
    WorkingEntry,
    committed_entries,
    drawdown_pct,
    open_risk,
    position_size,
    session_pnl_pct,
    streak_risk_factor,
)

if TYPE_CHECKING:
    from app.config import AppConfig


@dataclass(frozen=True)
class GateCheck:
    """One gate's outcome, recorded on the decision row."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateContext:
    """Everything the gates need for one candidate decision."""

    symbol: str
    equity: Decimal
    cash: Decimal
    last_equity: Decimal  # equity at the previous session close
    peak_equity: Decimal
    open_positions: list[Position]
    working_entries: tuple[WorkingEntry, ...]
    day_trades_5d: int
    losing_streak: int
    shock_brake_1h: bool  # SPY fell more than the 1h threshold
    shock_brake_session: bool  # SPY fell more than the session threshold
    days_to_earnings: int | None  # None = no known earnings ahead
    earnings_known: bool  # False = the earnings source is down
    kill_switch_on: bool
    paused: bool  # new entries paused (startup pause or header Pause)
    pending_ack: bool  # unacknowledged reconciliation/adoption blockers
    orders_this_cycle: int  # orders already submitted in this cycle


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    blocked_by: str | None
    sizing: Sizing | None
    risk_factor: Decimal
    checks: list[GateCheck] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "blocked_by": self.blocked_by,
            "risk_factor": str(self.risk_factor),
            "sizing": (
                {
                    "shares": self.sizing.shares,
                    "risk_budget": str(self.sizing.risk_budget),
                    "limited_by": self.sizing.limited_by,
                }
                if self.sizing is not None
                else None
            ),
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
        }


def _pct(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def evaluate_entry_gates(
    ctx: GateContext,
    *,
    entry: Decimal,
    stop: Decimal,
    config: AppConfig,
) -> GateResult:
    """Run every gate in spec order; return the first blocker by name."""
    checks: list[GateCheck] = []

    def block(name: str, detail: str) -> GateResult:
        checks.append(GateCheck(name, False, detail))
        return GateResult(
            allowed=False,
            blocked_by=name,
            sizing=None,
            risk_factor=Decimal(1),
            checks=checks,
        )

    def ok(name: str, detail: str) -> None:
        checks.append(GateCheck(name, True, detail))

    # Pre-checks, before the spec's gate table.
    if ctx.kill_switch_on:
        return block("kill_switch", "kill switch is on; no new entries")
    ok("kill_switch", "off")
    if ctx.paused:
        return block(
            "paused", "new entries are paused; press Resume in the header"
        )
    ok("paused", "entries active")
    if ctx.pending_ack:
        return block(
            "reconciliation",
            "unacknowledged reconciliation mismatch blocks new entries",
        )
    ok("reconciliation", "clean")
    if any(p.symbol == ctx.symbol for p in ctx.open_positions):
        return block("already_held", "a position in this symbol is open")
    if any(e.symbol == ctx.symbol for e in ctx.working_entries):
        return block("entry_pending", "an entry order for this symbol is open")
    ok("already_held", "no open position or pending entry")

    max_positions = int(config.get("risk", "max_open_positions", default=4))
    open_count = len(ctx.open_positions) + len(ctx.working_entries)
    if open_count >= max_positions:
        return block(
            "max_open_positions",
            f"{open_count} open or pending ≥ {max_positions}",
        )
    ok("max_open_positions", f"{open_count} of {max_positions}")

    max_per_sector = int(config.get("risk", "max_per_sector", default=2))
    sectors = config.get("universe", "sectors", default={}) or {}
    sector = sectors.get(ctx.symbol)
    if sector is not None:
        in_sector = sum(
            1 for p in ctx.open_positions if sectors.get(p.symbol) == sector
        )
        in_sector += sum(
            1 for e in ctx.working_entries if sectors.get(e.symbol) == sector
        )
        if in_sector >= max_per_sector:
            return block(
                "sector_cap",
                f"{in_sector} {sector} positions or pending entries "
                f"≥ {max_per_sector}",
            )
        ok("sector_cap", f"{in_sector} of {max_per_sector} in {sector}")
    else:
        ok("sector_cap", "no sector configured")

    # Daily loss limit: session P&L vs the previous close's equity.
    daily_limit_pct = Decimal(
        str(config.get("risk", "daily_loss_limit_pct", default=2))
    )
    pnl_pct = session_pnl_pct(ctx.equity, ctx.last_equity)
    if pnl_pct <= -daily_limit_pct:
        return block(
            "daily_loss_limit",
            f"session P&L {_pct(pnl_pct)}% ≤ −{_pct(daily_limit_pct)}%",
        )
    ok("daily_loss_limit", f"session P&L {_pct(pnl_pct)}%")

    # Shock brake: SPY down hard in the last hour or since the open.
    shock_1h = Decimal(str(config.get("risk", "shock_brake_1h_pct", default=1.5)))
    shock_session = Decimal(
        str(config.get("risk", "shock_brake_session_pct", default=2.5))
    )
    if ctx.shock_brake_1h or ctx.shock_brake_session:
        which = "last hour" if ctx.shock_brake_1h else "since the open"
        limit = shock_1h if ctx.shock_brake_1h else shock_session
        return block("shock_brake", f"SPY fell past {_pct(limit)}% {which}")
    ok("shock_brake", "SPY within limits")

    # Earnings blackout: earnings within the next N trading days.
    blackout = int(config.get("risk", "earnings_blackout_days", default=3))
    if not ctx.earnings_known:
        return block(
            "earnings_blackout", "earnings calendar unavailable; entry blocked"
        )
    if ctx.days_to_earnings is not None and ctx.days_to_earnings <= blackout:
        return block(
            "earnings_blackout",
            f"earnings in {ctx.days_to_earnings} trading days ≤ {blackout}",
        )
    ok(
        "earnings_blackout",
        (
            f"{ctx.days_to_earnings} trading days out"
            if ctx.days_to_earnings is not None
            else "no earnings ahead"
        ),
    )

    # PDT guard: N day trades in the last 5 trading days block entries.
    pdt_max = int(config.get("risk", "pdt_max_day_trades", default=3))
    if ctx.day_trades_5d >= pdt_max:
        return block(
            "pdt_guard", f"{ctx.day_trades_5d} day trades in 5 days ≥ {pdt_max}"
        )
    ok("pdt_guard", f"{ctx.day_trades_5d} day trades in 5 days")

    # Drawdown kill: equity below peak by the configured pct turns the
    # kill switch on — the executor handles activation on this gate name.
    kill_pct = Decimal(str(config.get("risk", "drawdown_kill_pct", default=8)))
    dd_pct = drawdown_pct(ctx.equity, ctx.peak_equity)
    if dd_pct >= kill_pct:
        return block(
            "drawdown_kill",
            f"drawdown {_pct(dd_pct)}% ≥ {_pct(kill_pct)}% of peak",
        )
    ok("drawdown_kill", f"drawdown {_pct(dd_pct)}%")

    # Losing streak halves the risk budget; it never blocks by itself.
    streak_len = int(config.get("risk", "losing_streak", default=3))
    factor = streak_risk_factor(ctx.losing_streak, config)
    checks.append(
        GateCheck(
            "losing_streak",
            True,
            (
                f"{ctx.losing_streak} losses in a row; risk ×{factor}"
                if ctx.losing_streak >= streak_len
                else f"{ctx.losing_streak} losses in a row"
            ),
        )
    )

    # Sizing gates: risk per trade, max position, open risk, cash. The shared
    # sizing function applies them; a size of zero blocks with the name of
    # the limit that hit zero. Working entry orders spend part of the open
    # risk and cash budgets already (docs/SPEC.md, Working entry orders):
    # unfilled qty × (limit − stop) counts toward open risk, unfilled qty ×
    # limit toward cash; an entry whose stop leg is missing counts its full
    # committed value as risk — the conservative worst case.
    working_risk, working_cash = committed_entries(ctx.working_entries)
    current_open_risk = open_risk(
        (p.qty, p.avg_entry_price, p.current_stop_price or Decimal(0))
        for p in ctx.open_positions
    ) + working_risk
    sizing = position_size(
        ctx.equity,
        entry,
        stop,
        cash=(
            max(Decimal(0), ctx.cash - working_cash)
            if config.get("risk", "cash_only", default=True)
            else ctx.equity
        ),
        current_open_risk=current_open_risk,
        config=config,
        risk_factor=factor,
    )
    checks.append(
        GateCheck(
            "position_size",
            sizing.shares >= 1,
            (
                f"{sizing.shares} shares, limited by "
                f"{', '.join(sizing.limited_by)}"
            ),
        )
    )
    if sizing.shares < 1:
        limit_name = sizing.limited_by[0] if sizing.limited_by else "position_size"
        return GateResult(
            allowed=False,
            blocked_by=limit_name,
            sizing=sizing,
            risk_factor=factor,
            checks=checks,
        )

    # Order sanity: single order vs equity, orders this cycle.
    max_order_pct = Decimal(str(config.get("risk", "max_order_pct", default=25)))
    order_value = entry * sizing.shares
    order_pct = order_value / ctx.equity * 100 if ctx.equity > 0 else Decimal(0)
    if order_pct > max_order_pct:
        checks.append(
            GateCheck(
                "order_sanity",
                False,
                f"order {_pct(order_pct)}% of equity > {_pct(max_order_pct)}%",
            )
        )
        return GateResult(
            allowed=False,
            blocked_by="order_sanity",
            sizing=sizing,
            risk_factor=factor,
            checks=checks,
        )
    max_orders = int(config.get("risk", "max_orders_per_cycle", default=6))
    if ctx.orders_this_cycle >= max_orders:
        checks.append(
            GateCheck(
                "order_sanity",
                False,
                f"{ctx.orders_this_cycle} orders this cycle ≥ {max_orders}",
            )
        )
        return GateResult(
            allowed=False,
            blocked_by="order_sanity",
            sizing=sizing,
            risk_factor=factor,
            checks=checks,
        )
    ok("order_sanity", f"order {_pct(order_pct)}% of equity")

    return GateResult(
        allowed=True,
        blocked_by=None,
        sizing=sizing,
        risk_factor=factor,
        checks=checks,
    )
