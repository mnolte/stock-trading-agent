"""Replay entry selection on stored cycles (docs/SPEC.md, Entry
selection — Comparison before switching live).

For every stored live and shadow cycle where a slot, sector or
open-risk gate blocked a model enter, this rebuilds that cycle's
scored candidates, chooses under both rank and expected_profit, and
reports what each mode would have sent and what its options touched
(``outcomes.option_touches``). Nothing is submitted and no downstream
portfolio effect is simulated: a blocked or unchosen enter is judged
by what its option would have done, exactly as ``option_touches``
already measures it.

The starting state is reconstructed from stored rows — positions and
entry orders open at the cycle's start, the last equity snapshot
before it — so an entry order whose end time was never stored counts
as working only inside its timeout window.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.db.base import get_session_maker
from app.db.models import (
    Cycle,
    Decision,
    EquitySnapshot,
    Order,
    Outcome,
    Position,
    ShadowDecision,
    ShadowEquity,
    ShadowPosition,
    Strategy,
)
from app.decide.output import decision_trade_prices
from app.engine.selection import (
    ScoredEntry,
    SelectionBudget,
    choose,
    score,
)
from app.market.clock import NY_TZ
from app.risk.gates import GateContext, evaluate_entry_gates, resolve_sectors
from app.risk.math import (
    WorkingEntry,
    committed_entries,
    open_risk,
    streak_risk_factor,
    trade_risk,
)

CENT = Decimal("0.01")
# The entry gates that count for the replay: a slot, a sector place or
# open-risk room. ``open_risk`` is the sizing cap's own blocked name.
GATE_BLOCKS = ("max_open_positions", "sector_cap", "open_risk")


def _dec(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _trade_prices(
    model_output: dict[str, Any] | None,
    options: dict[str, Any] | None,
    chosen: str | None,
) -> tuple[Decimal | None, Decimal | None]:
    out = model_output or {}
    stop, target = decision_trade_prices(model_output, options, chosen)
    return (
        stop if stop is not None else _dec(out.get("stop_price")),
        target if target is not None else _dec(out.get("target_price")),
    )


def _model_enter(d: Decision | ShadowDecision) -> bool:
    validation = d.validation_result or {}
    return validation.get("model_action") == "enter"


def _entry_limit(d: Decision | ShadowDecision, config: AppConfig) -> Decimal | None:
    gates = d.gates_result or {}
    stored = _dec(gates.get("entry_limit"))
    if stored is not None:
        return stored
    snap = d.input_snapshot or {}
    price = _dec(snap.get("price"))
    if price is None:
        return None
    offset = Decimal(
        str(config.get("execution", "entry_limit_offset_pct", default=0.05))
    )
    return (price * (1 + offset / 100)).quantize(CENT, rounding=ROUND_HALF_UP)


def _ny_day(at: datetime) -> Any:
    # Stored datetimes are naive UTC.
    return at.replace(tzinfo=UTC).astimezone(NY_TZ).date()


def _live_state(
    session: Session, cycle: Cycle, config: AppConfig, sectors: dict[str, str]
) -> dict[str, Any]:
    """Positions, working entries and budget at the cycle's start."""
    start = cycle.started_at or cycle.scheduled_time
    positions = list(
        session.execute(
            select(Position).where(
                Position.opened_at <= start,
                (Position.closed_at.is_(None)) | (Position.closed_at > start),
            )
        ).scalars()
    )
    working = tuple(
        WorkingEntry(
            symbol=o.symbol,
            unfilled_qty=o.qty,
            limit_price=o.limit_price,
            stop_price=o.stop_price,
        )
        for o in session.execute(
            select(Order).where(
                Order.leg == "entry",
                Order.submitted_at <= start,
                (Order.filled_at.is_(None)) | (Order.filled_at > start),
                (Order.active_until.is_(None)) | (Order.active_until > start),
            )
        ).scalars()
    )
    eq = session.execute(
        select(EquitySnapshot)
        .where(EquitySnapshot.time <= start)
        .order_by(EquitySnapshot.time.desc())
        .limit(1)
    ).scalar_one_or_none()
    open_ny = datetime(
        _ny_day(start).year,
        _ny_day(start).month,
        _ny_day(start).day,
        9,
        30,
        tzinfo=NY_TZ,
    ).astimezone(UTC).replace(tzinfo=None)
    before_open = session.execute(
        select(EquitySnapshot)
        .where(EquitySnapshot.time <= open_ny)
        .order_by(EquitySnapshot.time.desc())
        .limit(1)
    ).scalar_one_or_none()
    peak = session.execute(
        select(EquitySnapshot.equity)
        .where(EquitySnapshot.time <= start)
        .order_by(EquitySnapshot.equity.desc())
        .limit(1)
    ).scalar_one_or_none()
    streak = 0
    for p in sorted(
        (
            p
            for p in session.execute(
                select(Position).where(Position.closed_at.is_not(None), Position.closed_at <= start)
            ).scalars()
        ),
        key=lambda p: cast(datetime, p.closed_at),
        reverse=True,
    ):
        if (p.realized_pnl_raw or Decimal(0)) < 0:
            streak += 1
        else:
            break
    equity = eq.equity if eq is not None else Decimal(0)
    cash = eq.cash if eq is not None else equity
    return {
        "positions": positions,
        "working": working,
        "equity": equity,
        "cash": cash,
        "last_equity": before_open.equity if before_open is not None else equity,
        "peak_equity": peak if peak is not None else equity,
        "streak": streak,
        "sectors": sectors,
        "start": start,
    }


def _shadow_state(
    session: Session,
    strategy: Strategy,
    cycle: Cycle,
    config: AppConfig,
    sectors: dict[str, str],
) -> dict[str, Any]:
    """The same starting state for a shadow, from its own book rows."""
    start = cycle.started_at or cycle.scheduled_time
    positions = [
        Position(
            symbol=p.symbol,
            qty=p.qty,
            avg_entry_price=p.entry,
            current_stop_price=p.stop,
        )
        for p in session.execute(
            select(ShadowPosition).where(
                ShadowPosition.strategy_id == strategy.id,
                ShadowPosition.status == "open",
                ShadowPosition.opened_at <= start,
                (ShadowPosition.closed_at.is_(None))
                | (ShadowPosition.closed_at > start),
            )
        ).scalars()
    ]
    working = tuple(
        WorkingEntry(
            symbol=p.symbol,
            unfilled_qty=p.qty,
            limit_price=p.limit_price,
            stop_price=p.stop,
        )
        for p in session.execute(
            select(ShadowPosition).where(
                ShadowPosition.strategy_id == strategy.id,
                ShadowPosition.status == "pending",
                ShadowPosition.created_at <= start,
                ShadowPosition.expires_at > start,
            )
        ).scalars()
    )
    eq = session.execute(
        select(ShadowEquity)
        .where(ShadowEquity.strategy_id == strategy.id, ShadowEquity.time <= start)
        .order_by(ShadowEquity.time.desc())
        .limit(1)
    ).scalar_one_or_none()
    peak_row = session.execute(
        select(ShadowEquity.equity)
        .where(ShadowEquity.strategy_id == strategy.id, ShadowEquity.time <= start)
        .order_by(ShadowEquity.equity.desc())
        .limit(1)
    ).scalar_one_or_none()
    streak = 0
    for p in sorted(
        (
            p
            for p in session.execute(
                select(ShadowPosition).where(
                    ShadowPosition.strategy_id == strategy.id,
                    ShadowPosition.closed_at.is_not(None),
                    ShadowPosition.closed_at <= start,
                )
            ).scalars()
        ),
        key=lambda p: cast(datetime, p.closed_at),
        reverse=True,
    ):
        if (p.pnl or Decimal(0)) < 0:
            streak += 1
        else:
            break
    equity = eq.equity if eq is not None else Decimal(0)
    return {
        "positions": positions,
        "working": working,
        "equity": equity,
        "cash": eq.cash if eq is not None else equity,
        "last_equity": equity,
        "peak_equity": peak_row if peak_row is not None else equity,
        "streak": streak,
        "sectors": sectors,
        "start": start,
    }


def _ctx(
    state: dict[str, Any],
    symbol: str,
    days_to_earnings: int | None,
    earnings_known: bool,
    closed_today: bool,
) -> GateContext:
    return GateContext(
        symbol=symbol,
        equity=state["equity"],
        cash=state["cash"],
        last_equity=state["last_equity"],
        peak_equity=state["peak_equity"],
        open_positions=state["positions"],
        working_entries=state["working"],
        closed_today=closed_today,
        losing_streak=state["streak"],
        shock_brake_1h=False,
        shock_brake_session=False,
        days_to_earnings=days_to_earnings,
        earnings_known=earnings_known,
        kill_switch_on=False,
        paused=False,
        pending_ack=False,
        orders_this_cycle=0,
        daily_loss_hit=False,
        sectors=state["sectors"],
    )


def _closed_today(
    state: dict[str, Any], session: Session, symbol: str, shadow_id: int | None
) -> bool:
    day = _ny_day(state["start"])
    if shadow_id is None:
        closed = [
            p.closed_at
            for p in session.execute(
                select(Position).where(
                    Position.symbol == symbol, Position.closed_at.is_not(None)
                )
            ).scalars()
        ]
    else:
        closed = [
            p.closed_at
            for p in session.execute(
                select(ShadowPosition).where(
                    ShadowPosition.strategy_id == shadow_id,
                    ShadowPosition.symbol == symbol,
                    ShadowPosition.closed_at.is_not(None),
                )
            ).scalars()
        ]
    return any(
        t is not None and t <= state["start"] and _ny_day(t) == day for t in closed
    )


def _scored_entries(
    decisions: list[Decision | ShadowDecision],
    state: dict[str, Any],
    session: Session,
    config: AppConfig,
    shadow_id: int | None,
) -> tuple[list[ScoredEntry], dict[str, Any]]:
    """Score every model enter through the starting-state dry run."""
    weight = Decimal(
        str(config.get("selection", "confidence_weight", default=0.5))
    )
    scored: list[ScoredEntry] = []
    info: dict[str, Any] = {}
    for d in decisions:
        if not _model_enter(d):
            continue
        stop, target = _trade_prices(
            d.model_output, d.options, d.chosen_option
        )
        limit = _entry_limit(d, config)
        if stop is None or target is None or limit is None:
            continue
        ind = d.input_snapshot or {}
        days = ind.get("days_to_earnings")
        known = ind.get("earnings_available", True) is not False
        ctx = _ctx(
            state,
            d.symbol,
            days if isinstance(days, int) else None,
            bool(known),
            _closed_today(state, session, d.symbol, shadow_id),
        )
        gate = evaluate_entry_gates(ctx, entry=limit, stop=stop, config=config)
        if not gate.allowed or gate.sizing is None:
            continue
        risk = trade_risk(limit, stop, target)
        if risk.reward_to_risk is None or risk.reward_to_risk <= 0:
            continue
        confidence = _dec((d.model_output or {}).get("confidence")) or Decimal(0)
        dollars = gate.sizing.shares * (limit - stop)
        be, p_used, expected_r, expected_profit = score(
            reward_to_risk=risk.reward_to_risk,
            confidence=confidence,
            dollars_at_risk=dollars,
            confidence_weight=weight,
        )
        rank_raw = (d.input_snapshot or {}).get("rank")
        se = ScoredEntry(
            symbol=d.symbol,
            rank=rank_raw if isinstance(rank_raw, int) else None,
            sector=state["sectors"].get(d.symbol),
            entry=limit,
            stop=stop,
            reward_to_risk=risk.reward_to_risk,
            breakeven=be,
            p_used=p_used,
            expected_r=expected_r,
            dollars_at_risk=dollars,
            expected_profit=expected_profit,
            decision_id=d.id,
        )
        scored.append(se)
        info[se.key] = d
    return scored, info


def _touch(
    outcome: Outcome | None, chosen: str | None, first: str | None
) -> str | None:
    if outcome is not None and chosen is not None:
        touches = outcome.option_touches or {}
        found = touches.get(chosen)
        if isinstance(found, str):
            return found
    return first


def _profit(touch: str | None, rr: Decimal, risk: Decimal) -> tuple[Decimal, str]:
    if touch == "target":
        return rr * risk, f"+{_money(rr * risk)}"
    if touch == "stop":
        return -risk, f"-{_money(risk)}"
    if touch in (None, "not_filled"):
        return Decimal(0), "not filled" if touch == "not_filled" else "no touch"
    return Decimal(0), touch


def _money(v: Decimal) -> str:
    return f"${v.quantize(CENT, rounding=ROUND_HALF_UP):,}"


def replay_selection(
    session_factory: Any | None = None,
) -> str:
    """The comparison report over stored live and shadow cycles."""
    config = AppConfig()
    sf = session_factory or get_session_maker()
    lines: list[str] = [
        "# Entry selection replay — rank vs expected profit",
        "",
    ]
    totals: dict[str, Any] = {"rank": Decimal(0), "ep": Decimal(0), "diff_cycles": 0}
    with sf() as session:
        sectors = resolve_sectors(config, session)
        # Live cycles with a slot/sector/open-risk gate block.
        blocked = session.execute(
            select(Decision).where(
                Decision.context == "candidate",
                Decision.no_call_reason.is_(None),
                Decision.final_action == "blocked",
            )
        ).scalars()
        flagged = {
            d.cycle_id
            for d in blocked
            if _model_enter(d)
            and (d.gates_result or {}).get("blocked_by") in GATE_BLOCKS
        }
        for cycle_id in sorted(flagged):
            cycle = session.get(Cycle, cycle_id)
            if cycle is None:
                continue
            decisions = list(
                session.execute(
                    select(Decision).where(
                        Decision.cycle_id == cycle_id,
                        Decision.context == "candidate",
                        Decision.no_call_reason.is_(None),
                    )
                ).scalars()
            )
            lines += _cycle_report(
                session, cycle, decisions, config, sectors, None, totals
            )
        # Shadow cycles: a model enter blocked by the same gates.
        sblocked = session.execute(
            select(ShadowDecision).where(
                ShadowDecision.context == "candidate",
                ShadowDecision.no_call_reason.is_(None),
                ShadowDecision.final_action == "blocked",
                ShadowDecision.missed.is_(False),
            )
        ).scalars()
        for d in sblocked:
            if not _model_enter(d) or (
                (d.gates_result or {}).get("blocked_by") not in GATE_BLOCKS
            ):
                continue
            strategy = session.get(Strategy, d.strategy_id)
            cycle = session.get(Cycle, d.cycle_id)
            if strategy is None or cycle is None:
                continue
            decisions = list(
                session.execute(
                    select(ShadowDecision).where(
                        ShadowDecision.cycle_id == d.cycle_id,
                        ShadowDecision.strategy_id == d.strategy_id,
                        ShadowDecision.context == "candidate",
                        ShadowDecision.no_call_reason.is_(None),
                    )
                ).scalars()
            )
            lines += _cycle_report(
                session,
                cycle,
                decisions,
                config,
                sectors,
                strategy,
                totals,
            )
    lines += [
        "",
        "## Totals",
        "",
        f"- cycles where the modes differ: {totals['diff_cycles']}",
        f"- rank order outcome total: {_money(totals['rank'])}",
        f"- expected-profit outcome total: {_money(totals['ep'])}",
    ]
    return "\n".join(lines)


def _cycle_report(
    session: Session,
    cycle: Cycle,
    decisions: list[Any],
    config: AppConfig,
    sectors: dict[str, str],
    strategy: Strategy | None,
    totals: dict[str, Any],
) -> list[str]:
    state = (
        _live_state(session, cycle, config, sectors)
        if strategy is None
        else _shadow_state(session, strategy, cycle, config, sectors)
    )
    shadow_id = strategy.id if strategy is not None else None
    scored, info = _scored_entries(
        decisions, state, session, config, shadow_id
    )
    if not scored:
        return []
    spent = committed_entries(state["working"], None)
    used_risk = (
        open_risk(
            (p.qty, p.avg_entry_price, p.current_stop_price or Decimal(0))
            for p in state["positions"]
        )
        + spent.risk
    )
    cash = (
        max(Decimal(0), state["cash"] - spent.cash)
        if config.get("risk", "cash_only", default=True)
        else state["equity"]
    )
    counts: dict[str, int] = {}
    for symbol in [p.symbol for p in state["positions"]] + [
        e.symbol for e in state["working"]
    ]:
        sector = sectors.get(symbol)
        if sector is not None:
            counts[sector] = counts.get(sector, 0) + 1
    factor = streak_risk_factor(state["streak"], config)
    budget = SelectionBudget(
        free_slots=max(
            0,
            int(config.get("risk", "max_open_positions", default=4))
            - len(state["positions"])
            - len(state["working"]),
        ),
        sector_cap=int(config.get("risk", "max_per_sector", default=2)),
        sector_counts=counts,
        open_risk_used=used_risk,
        open_risk_cap=state["equity"]
        * Decimal(str(config.get("risk", "max_open_risk_pct", default=2.5)))
        / 100,
        cash=cash,
        orders_left=int(config.get("risk", "max_orders_per_cycle", default=6)),
        equity=state["equity"],
        last_slot_min_ev_r=(
            Decimal(str(v))
            if (v := config.get("selection", "last_slot_min_ev_r"))
            is not None
            else None
        ),
    )
    sel = choose(scored, budget, config, risk_factor=factor)
    # Shadow decisions have no Outcome rows of their own; the market
    # result of an option is the live decision's stored touch for the
    # same symbol in the same cycle.
    outcome_by_id = {
        o.decision_id: o
        for o in session.execute(
            select(Outcome).where(
                Outcome.decision_id.in_([d.id for d in decisions])
            )
        ).scalars()
    }
    if strategy is not None:
        live_decisions = session.execute(
            select(Decision).where(Decision.cycle_id == cycle.id)
        ).scalars()
        live_outcomes = {
            o.decision_id: o
            for o in session.execute(
                select(Outcome).where(
                    Outcome.decision_id.in_([d.id for d in live_decisions])
                )
            ).scalars()
        }
        outcome_by_symbol = {
            d.symbol: live_outcomes.get(d.id) for d in live_decisions
        }
    else:
        outcome_by_symbol = {}

    def _line(se: ScoredEntry) -> tuple[str, Decimal]:
        d = info[se.key]
        outcome = outcome_by_id.get(d.id) or outcome_by_symbol.get(d.symbol)
        touch = _touch(
            outcome, d.chosen_option,
            outcome.first_touch if outcome is not None else None,
        )
        profit, text = _profit(touch, se.reward_to_risk, se.dollars_at_risk)
        return (
            f"  - {se.symbol}: expected {_money(se.expected_profit)}, "
            f"touch {touch or '—'} → {text}",
            profit,
        )

    ep_keys = {e.key for e in sel.chosen}
    rank_keys = set(sel.rank_keys)
    when = (cycle.started_at or cycle.scheduled_time).strftime("%Y-%m-%d %H:%M")
    name = "live" if strategy is None else f"shadow {strategy.name}"
    ep_total = Decimal(0)
    rank_total = Decimal(0)
    body: list[str] = [f"## Cycle {cycle.id} · {when} ({name})", ""]
    for se in sel.chosen:
        text, profit = _line(se)
        ep_total += profit
        body.append(f"{text} — expected profit pick")
    for se in scored:
        if se.key in ep_keys:
            continue
        text, profit = _line(se)
        body.append(f"{text} — not selected")
    for se in scored:
        if se.key in rank_keys:
            text, profit = _line(se)
            rank_total += profit
    if ep_keys != rank_keys:
        totals["diff_cycles"] += 1
    totals["rank"] += rank_total
    totals["ep"] += ep_total
    body += [
        "",
        f"Rank order would take {len(rank_keys)} entries, "
        f"outcome {_money(rank_total)}; expected profit takes "
        f"{len(sel.chosen)}, outcome {_money(ep_total)}.",
        "",
    ]
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.replay_selection")
    parser.add_argument("--out", default=None, help="write the report to a file")
    args = parser.parse_args(argv)
    report = replay_selection()
    if args.out:
        Path(args.out).write_text(report)
        print(f"report written to {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
