"""Performance aggregation for the Performance screen (docs/SPEC.md).

Everything is measured against the current paper run: a "Start new run"
after a paper reset restarts the numbers. Raw P&L comes straight from the
recorded fills; the adjusted figure subtracts the configured per-side
slippage on every fill and adds dividends recorded from corporate actions.
The benchmark is a same-day SPY buy-and-hold anchored at the run's stored
SPY close, one point per session's last equity snapshot.

Aggregates (win rate, average R, by regime, by symbol) count only trades
the agent opened and closed itself — adopted positions are listed in the
closed trades but kept out of the stats.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.data.service import MarketDataService
from app.db.models import (
    CorporateAction,
    Cycle,
    Decision,
    EquitySnapshot,
    Fill,
    Order,
    Outcome,
    Position,
    Run,
)
from app.engine.trades import _dividends, _trade_r
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, Clock

_REGIME_ORDER = [
    "Up · normal vol",
    "Up · high vol",
    "Up · low vol",
    "Sideways · any",
    "Down · normal or low",
    "Down · high vol",
    "Unknown",
]

_DECISION_ORDER = [
    "enter",
    "skip",
    "hold",
    "tighten_stop",
    "exit",
    "deferred",
    "blocked",
    "no_call",
    "model_error",
    "invalid",
]

_DECISION_LABELS = {
    "enter": "Enter",
    "skip": "Skip (model)",
    "hold": "Hold",
    "tighten_stop": "Tighten stop",
    "exit": "Exit",
    "deferred": "Exit deferred",
    "blocked": "Enter blocked",
    "no_call": "Held by rules",
    "model_error": "Model error",
    "invalid": "Invalid output",
}

_EXIT_LABELS = {
    "stop": "stop",
    "target": "target",
    "exit": "agent exit",
}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    return _aware(dt).isoformat() if dt is not None else None


def _decision_category(decision: Decision) -> str:
    """Group key for the "Decisions and outcomes" table."""
    if decision.final_action == "no_call":
        return "model_error" if decision.no_call_reason == "model_error" else "no_call"
    valid = (decision.validation_result or {}).get("valid")
    if decision.model != "none" and valid is False:
        return "invalid"
    return decision.final_action


def _regime_label(regime: object) -> str:
    """Entry-regime bucket label matching the design's rows."""
    if not isinstance(regime, dict):
        return "Unknown"
    trend = str(regime.get("trend") or "")
    vol = str(regime.get("volatility") or "")
    if trend == "up":
        return f"Up · {vol or 'normal'} vol"
    if trend == "sideways":
        return "Sideways · any"
    if trend == "down":
        return "Down · high vol" if vol == "high" else "Down · normal or low"
    return "Unknown"


def _pct_change(new: Decimal, base: Decimal) -> Decimal | None:
    if base == 0:
        return None
    return (new - base) / base * 100


def _decision_price(decision: Decision) -> Decimal | None:
    """Price at decision time from the stored input snapshot."""
    snapshot = decision.input_snapshot or {}
    price = snapshot.get("price")
    if isinstance(price, int | float):
        return Decimal(str(price))
    return None


def _decision_outcome_row(
    key: str, decisions: list[tuple[Decision, Outcome | None, bool]]
) -> dict[str, object]:
    """One row of the "Decisions and outcomes" table."""
    pcts: list[Decimal] = []
    for decision, outcome, _filled in decisions:
        base = _decision_price(decision)
        if (
            outcome is not None
            and outcome.price_1session is not None
            and base is not None
            and base != 0
        ):
            pct = _pct_change(outcome.price_1session, base)
            if pct is not None:
                pcts.append(pct)
    avg = sum(pcts, Decimal(0)) / len(pcts) if pcts else None

    changed = sum(
        1
        for d, _, _ in decisions
        if (d.validation_result or {}).get("changed_by_code")
    )
    not_filled = sum(1 for _, _, filled in decisions if not filled)

    read: str
    if key == "enter":
        if avg is None:
            read = "waiting for 1-session prices"
        else:
            direction = (
                "the right way"
                if avg > 0
                else "the wrong way"
                if avg < 0
                else "flat"
            )
            read = f"entries moved {direction} on average"
    elif key == "skip":
        if avg is None:
            read = "waiting for 1-session prices"
        elif abs(avg) < Decimal("0.05"):
            read = "skipped setups went nowhere"
        elif avg > 0:
            read = "skipped setups rose on average"
        else:
            read = "skipped setups fell on average"
    elif key == "hold":
        read = "positions kept"
    elif key == "tighten_stop":
        read = "stop moved up"
    elif key == "exit":
        read = "agent closed the position"
    elif key == "deferred":
        read = "day-trade rule — re-asked next session"
    elif key == "blocked":
        gates = [
            str((d.gates_result or {}).get("blocked_by"))
            for d, _, _ in decisions
            if (d.gates_result or {}).get("blocked_by")
        ]
        top = max(set(gates), key=gates.count) if gates else None
        read = f"blocked by {top}" if top else "blocked by risk gates"
    elif key == "model_error":
        read = "call failed — treated as skip or hold"
    elif key == "invalid":
        actions = {d.final_action for d, _, _ in decisions}
        read = (
            "treated as skip or hold"
            if len(actions) > 1
            else f"treated as {actions.pop()}"
        )
    else:
        read = "no model call"

    extras: list[str] = []
    if key == "enter" and not_filled:
        extras.append(f"{not_filled} not filled")
    if changed:
        extras.append(f"{changed} changed by code")
    if extras:
        read += " · " + " · ".join(extras)

    return {
        "key": key,
        "label": _DECISION_LABELS[key],
        "count": len(decisions),
        "avg_1session_pct": str(avg.quantize(Decimal("0.01"))) if avg is not None else None,
        "outcomes": len(pcts),
        "read": read,
    }


def _entry_regime(
    position: Position, decisions: dict[int, Decision]
) -> str | None:
    decision = (
        decisions.get(position.entry_decision_id)
        if position.entry_decision_id is not None
        else None
    )
    if decision is None:
        return None
    regime = (decision.input_snapshot or {}).get("regime")
    return _regime_label(regime)


def _initial_stop(
    position: Position, decisions: dict[int, Decision]
) -> Decimal | None:
    """The stop the entry decision proposed, so R uses the risk at entry."""
    decision = (
        decisions.get(position.entry_decision_id)
        if position.entry_decision_id is not None
        else None
    )
    if decision is not None:
        stop = (decision.model_output or {}).get("stop_price")
        if isinstance(stop, int | float):
            return Decimal(str(stop))
    return None


def compute_performance(
    session: Session,
    *,
    config: AppConfig,
    market_data: MarketDataService | None,
    calendar: CalendarProvider,
    clock: Clock,
    live_equity: Decimal | None = None,
    run_id: int | None = None,
) -> dict[str, object]:
    """Assemble the Performance payload for one run (default: latest)."""
    now = clock.now()
    runs = session.execute(select(Run).order_by(Run.id)).scalars().all()
    if not runs:
        return {"empty": True, "runs": []}
    run = (
        next((r for r in runs if r.id == run_id), None)
        if run_id is not None
        else runs[-1]
    )
    if run is None:
        return {
            "empty": True,
            "runs": [
                {
                    "id": r.id,
                    "started_at": _iso(r.started_at),
                    "starting_equity": str(r.starting_equity),
                }
                for r in reversed(runs)
            ],
        }
    start = _aware(run.started_at)
    later = next((r for r in runs if r.id > run.id), None)
    end = _aware(later.started_at) if later is not None else None

    slip_pct = Decimal(
        str(config.get("performance", "slippage_pct_per_side", default=0.05))
    )
    kill_pct = Decimal(str(config.get("risk", "drawdown_kill_pct", default=8)))

    # ---- equity curve: last snapshot per New York session ----------------
    snap_query = (
        select(EquitySnapshot)
        .where(EquitySnapshot.time >= run.started_at)
        .order_by(EquitySnapshot.time)
    )
    if end is not None:
        snap_query = snap_query.where(EquitySnapshot.time < later.started_at)  # type: ignore[union-attr]
    points: list[dict[str, Any]] = []
    seen: set[date] = set()
    for snap in session.execute(snap_query).scalars():
        day = _aware(snap.time).astimezone(NY_TZ).date()
        if day in seen:
            points[-1] = {
                "date": day.isoformat(),
                "equity": str(snap.equity),
                "benchmark": (
                    str(snap.spy_benchmark_value)
                    if snap.spy_benchmark_value is not None
                    else None
                ),
                "eur_usd": (
                    str(snap.eur_usd_rate) if snap.eur_usd_rate is not None else None
                ),
            }
        else:
            seen.add(day)
            points.append(
                {
                    "date": day.isoformat(),
                    "equity": str(snap.equity),
                    "benchmark": (
                        str(snap.spy_benchmark_value)
                        if snap.spy_benchmark_value is not None
                        else None
                    ),
                    "eur_usd": (
                        str(snap.eur_usd_rate)
                        if snap.eur_usd_rate is not None
                        else None
                    ),
                }
            )

    # Sessions elapsed: trading days from the run's start to the window end.
    sessions = 0
    cursor = start.astimezone(NY_TZ).date()
    window_end_day = (end or now).astimezone(NY_TZ).date()
    while cursor <= window_end_day and sessions < 500:
        if calendar.session_for(cursor) is not None:
            sessions += 1
        cursor += timedelta(days=1)

    # ---- trades ---------------------------------------------------------
    closed_q = select(Position).where(
        Position.closed_at.is_not(None),
        Position.closed_at >= run.started_at,
    )
    if end is not None:
        closed_q = closed_q.where(Position.closed_at < later.started_at)  # type: ignore[union-attr]
    closed_positions = session.execute(
        closed_q.order_by(Position.closed_at.desc())
    ).scalars().all()

    decision_ids = {
        p.entry_decision_id for p in closed_positions if p.entry_decision_id
    }
    decisions_by_id = {
        d.id: d
        for d in session.execute(
            select(Decision).where(Decision.id.in_(decision_ids or {0}))
        ).scalars()
    }

    actions = session.execute(
        select(CorporateAction).where(CorporateAction.action_type == "dividend")
    ).scalars().all()

    trades: list[dict[str, Any]] = []
    agent_trades: list[tuple[Position, Decimal | None, str | None]] = []
    adj_pnls: list[Decimal] = []
    adj_rs: list[Decimal] = []
    for p in closed_positions:
        qty = p.qty
        exit_price = (
            p.avg_entry_price + p.realized_pnl_raw / qty
            if p.realized_pnl_raw is not None and qty
            else None
        )
        stop = _initial_stop(p, decisions_by_id)
        r_value = _trade_r(p, stop)
        div = _dividends(
            actions,
            p.symbol,
            _aware(p.opened_at).astimezone(NY_TZ).date(),
            _aware(p.closed_at).astimezone(NY_TZ).date(),  # type: ignore[arg-type]
            qty,
        )
        adjusted = (
            p.realized_pnl_adjusted + div
            if p.realized_pnl_adjusted is not None
            else None
        )
        r_adjusted = (
            adjusted / ((p.avg_entry_price - stop) * qty)
            if adjusted is not None
            and stop is not None
            and qty
            and p.avg_entry_price > stop
            else None
        )
        regime = (
            _entry_regime(p, decisions_by_id)
            if p.origin == "agent"
            else "adopted"
        )
        trades.append(
            {
                "id": p.id,
                "symbol": p.symbol,
                "qty": qty,
                "entry": str(p.avg_entry_price),
                "exit": str(exit_price) if exit_price is not None else None,
                "opened_at": _iso(p.opened_at),
                "closed_at": _iso(p.closed_at),
                "pnl": (
                    str(p.realized_pnl_raw)
                    if p.realized_pnl_raw is not None
                    else None
                ),
                "adjusted": str(adjusted) if adjusted is not None else None,
                "r": (
                    str(r_value.quantize(Decimal("0.01")))
                    if r_value is not None
                    else None
                ),
                "adjusted_r": (
                    str(r_adjusted.quantize(Decimal("0.01")))
                    if r_adjusted is not None
                    else None
                ),
                "exit_reason": p.exit_reason,
                "exit_label": _EXIT_LABELS.get(
                    p.exit_reason or "", p.exit_reason or "—"
                ),
                "regime": regime,
                "origin": p.origin,
            }
        )
        if p.origin == "agent":
            agent_trades.append((p, r_value, regime))
            if adjusted is not None:
                adj_pnls.append(adjusted)
            if r_adjusted is not None:
                adj_rs.append(r_adjusted)

    # ---- by symbol / by regime -------------------------------------------
    def _aggregate(key_fn: Any) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for p, r_value, regime in agent_trades:
            key = key_fn(p, regime)
            group = groups.setdefault(
                key, {"trades": 0, "won": 0, "pnl": Decimal(0), "rs": []}
            )
            group["trades"] += 1
            if p.realized_pnl_raw is not None:
                group["pnl"] += p.realized_pnl_raw
                if p.realized_pnl_raw > 0:
                    group["won"] += 1
            if r_value is not None:
                group["rs"].append(r_value)
        return groups

    def _agg_row(label: str, group: dict[str, Any]) -> dict[str, Any]:
        avg_r = (
            sum(group["rs"], Decimal(0)) / len(group["rs"]) if group["rs"] else None
        )
        return {
            "label": label,
            "trades": group["trades"],
            "won": group["won"],
            "pnl": str(group["pnl"].quantize(Decimal("0.01"))),
            "avg_r": (
                str(avg_r.quantize(Decimal("0.01"))) if avg_r is not None else None
            ),
        }

    by_symbol_groups = _aggregate(lambda p, _r: p.symbol)
    by_symbol = sorted(
        (
            {
                "label": label,
                "trades": g["trades"],
                "won": g["won"],
                "pnl_num": g["pnl"],
                "pnl": str(g["pnl"].quantize(Decimal("0.01"))),
                "avg_r": (
                    str(
                        (sum(g["rs"], Decimal(0)) / len(g["rs"])).quantize(
                            Decimal("0.01")
                        )
                    )
                    if g["rs"]
                    else None
                ),
            }
            for label, g in by_symbol_groups.items()
        ),
        key=lambda row: row["pnl_num"],
        reverse=True,
    )
    for row in by_symbol:
        del row["pnl_num"]

    by_regime_groups = _aggregate(lambda _p, regime: regime or "Unknown")
    # The design's four fixed rows stay even when they have no trades.
    fixed = ["Up · normal vol", "Up · high vol", "Sideways · any", "Down · normal or low"]
    by_regime: list[dict[str, Any]] = []
    for label in _REGIME_ORDER:
        group = by_regime_groups.pop(label, None)
        if group is None:
            if label in fixed:
                by_regime.append(
                    {"label": label, "trades": 0, "won": None, "pnl": None, "avg_r": None}
                )
            continue
        by_regime.append(_agg_row(label, group))
    for label in sorted(by_regime_groups):
        by_regime.append(_agg_row(label, by_regime_groups[label]))

    # ---- decisions and outcomes ------------------------------------------
    decision_rows = session.execute(
        select(Decision, Outcome)
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .outerjoin(Outcome, Outcome.decision_id == Decision.id)
        .where(
            Cycle.kind == "cycle",
            Cycle.scheduled_time >= run.started_at,
            *(
                [Cycle.scheduled_time < later.started_at]  # type: ignore[union-attr]
                if end is not None
                else []
            ),
        )
        .order_by(Decision.id)
    ).all()

    filled_entry_decisions = {
        row[0]
        for row in session.execute(
            select(Order.decision_id).where(
                Order.decision_id.is_not(None),
                Order.leg == "entry",
                Order.status.in_(("filled", "partially_filled")),
            )
        ).all()
    }
    grouped: dict[str, list[tuple[Decision, Outcome | None, bool]]] = {}
    for decision, outcome in decision_rows:
        key = _decision_category(decision)
        filled = decision.id in filled_entry_decisions
        grouped.setdefault(key, []).append((decision, outcome, filled))
    decision_outcomes = [
        _decision_outcome_row(key, grouped[key])
        for key in _DECISION_ORDER
        if key in grouped
    ]

    # ---- totals -----------------------------------------------------------
    equity_now = live_equity
    if equity_now is None and points:
        equity_now = Decimal(points[-1]["equity"])
    if equity_now is None:
        equity_now = run.starting_equity
    return_usd = equity_now - run.starting_equity
    return_pct = _pct_change(equity_now, run.starting_equity)

    benchmark_last = (
        Decimal(points[-1]["benchmark"])
        if points and points[-1]["benchmark"] is not None
        else None
    )
    spy_return = (
        _pct_change(benchmark_last, run.starting_equity)
        if benchmark_last is not None
        else None
    )
    vs_spy = (
        return_pct - spy_return
        if return_pct is not None and spy_return is not None
        else None
    )

    fill_rows = session.execute(
        select(Fill.qty, Fill.price)
        .join(Order, Fill.order_id == Order.id)
        .where(
            Fill.filled_at >= run.started_at,
            *(
                [Fill.filled_at < later.started_at]  # type: ignore[union-attr]
                if end is not None
                else []
            ),
        )
    ).all()
    slippage_total = sum(
        (qty * price * slip_pct / 100 for qty, price in fill_rows), Decimal(0)
    )

    dividends_total = Decimal(0)
    for p in closed_positions:
        dividends_total += _dividends(
            actions,
            p.symbol,
            _aware(p.opened_at).astimezone(NY_TZ).date(),
            _aware(p.closed_at).astimezone(NY_TZ).date(),  # type: ignore[arg-type]
            p.qty,
        )
    open_positions = session.execute(
        select(Position).where(
            Position.closed_at.is_(None),
            Position.opened_at >= run.started_at,
        )
    ).scalars().all()
    today_ny = now.astimezone(NY_TZ).date()
    for p in open_positions:
        dividends_total += _dividends(
            actions,
            p.symbol,
            _aware(p.opened_at).astimezone(NY_TZ).date(),
            today_ny,
            p.qty,
        )
    adjusted_total = return_usd - slippage_total + dividends_total

    wins = sum(
        1
        for p, _, _ in agent_trades
        if p.realized_pnl_raw is not None and p.realized_pnl_raw > 0
    )
    scored = [p for p, _, _ in agent_trades if p.realized_pnl_raw is not None]
    win_rate = Decimal(wins) / len(scored) * 100 if scored else None
    rs = [r for _, r, _ in agent_trades if r is not None]
    avg_r = sum(rs, Decimal(0)) / len(rs) if rs else None
    # Adjusted metrics (slippage + dividends) for the Readiness criteria.
    avg_r_adjusted = sum(adj_rs, Decimal(0)) / len(adj_rs) if adj_rs else None
    gross_wins = sum((p for p in adj_pnls if p > 0), Decimal(0))
    gross_losses = sum((-p for p in adj_pnls if p < 0), Decimal(0))
    # No losses yet → None (the Readiness screen shows "—", like the design).
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else None

    curve_values = [run.starting_equity] + [
        Decimal(point["equity"]) for point in points
    ]
    peak = curve_values[0]
    max_dd = Decimal(0)
    for value in curve_values:
        peak = max(peak, value)
        if peak:
            max_dd = max(max_dd, (peak - value) / peak * 100)

    unrealized = Decimal(0)
    for p in open_positions:
        price = _latest_price(market_data, p.symbol, now)
        if price is not None:
            unrealized += (price - p.avg_entry_price) * p.qty

    fx = getattr(market_data, "latest_fx", None) if market_data else None
    eur_rate = fx.data.usd_per_eur if fx and fx.data else None
    return_eur = (
        return_usd / eur_rate if eur_rate is not None else None
    )

    return {
        "empty": False,
        "run": {
            "id": run.id,
            "started_at": _iso(run.started_at),
            "starting_equity": str(run.starting_equity),
            "sessions": sessions,
            "current": later is None,
        },
        "runs": [
            {
                "id": r.id,
                "started_at": _iso(r.started_at),
                "starting_equity": str(r.starting_equity),
            }
            for r in reversed(runs)
        ],
        "summary": {
            "return_pct": (
                str(return_pct.quantize(Decimal("0.01")))
                if return_pct is not None
                else None
            ),
            "return_usd": str(return_usd.quantize(Decimal("0.01"))),
            "return_eur": (
                str(return_eur.quantize(Decimal("0.01")))
                if return_eur is not None
                else None
            ),
            "spy_return_pct": (
                str(spy_return.quantize(Decimal("0.01")))
                if spy_return is not None
                else None
            ),
            "vs_spy_pct": (
                str(vs_spy.quantize(Decimal("0.01"))) if vs_spy is not None else None
            ),
            "adjusted_usd": str(adjusted_total.quantize(Decimal("0.01"))),
            "adjusted_pct": (
                str(
                    (adjusted_total / run.starting_equity * 100).quantize(
                        Decimal("0.01")
                    )
                )
                if run.starting_equity
                else None
            ),
            "slippage_pct": str(slip_pct),
            "dividends_usd": str(dividends_total.quantize(Decimal("0.01"))),
            "win_rate_pct": (
                str(win_rate.quantize(Decimal("0.01")))
                if win_rate is not None
                else None
            ),
            "wins": wins,
            "closed": len(scored),
            "avg_r": (
                str(avg_r.quantize(Decimal("0.01"))) if avg_r is not None else None
            ),
            "avg_r_adjusted": (
                str(avg_r_adjusted.quantize(Decimal("0.01")))
                if avg_r_adjusted is not None
                else None
            ),
            "profit_factor": (
                str(profit_factor.quantize(Decimal("0.01")))
                if profit_factor is not None
                else None
            ),
            "max_drawdown_pct": str(max_dd.quantize(Decimal("0.01"))),
            "kill_pct": str(kill_pct),
        },
        "curve": {
            "benchmark": str(
                config.get("performance", "benchmark", default="SPY")
            ),
            "start_label": start.astimezone(NY_TZ).date().isoformat(),
            "points": points,
        },
        "by_regime": by_regime,
        "by_symbol": by_symbol,
        "decision_outcomes": decision_outcomes,
        "closed_trades": trades,
        "open": {
            "count": len(open_positions),
            "symbols": [p.symbol for p in open_positions],
            "unrealized": str(unrealized.quantize(Decimal("0.01"))),
        },
    }


def _latest_price(
    market_data: MarketDataService | None, symbol: str, now: datetime
) -> Decimal | None:
    """Last closed 5-minute bar at or before now."""
    if market_data is None:
        return None
    bars = market_data.get_5m_bars(symbol, now - timedelta(days=7), now)
    return bars[-1].close if bars else None
