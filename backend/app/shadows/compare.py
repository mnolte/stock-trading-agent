"""Comparing live with the shadows (docs/SPEC.md, Strategies and shadows —
Comparing).

For the current run, per strategy: adjusted return, difference with SPY,
closed trades won and lost, average R, max drawdown, model cost and "same
as live". Live's numbers come from ``compute_performance`` — the function
the Performance screen uses — passed in by the caller, so live's adjusted
return here always equals Performance's.

"Same as live" is the share of counted symbol-cycles where the final action
matched live, with every ``no_call`` reason treated as one action. A
symbol-cycle counts only if at least one side did more than a rules
``no_call``; missed shadow work is left out.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Cycle,
    Decision,
    EquitySnapshot,
    Fill,
    Outcome,
    Position,
    Run,
    ShadowDecision,
    ShadowEquity,
    ShadowPosition,
    Strategy,
)
from app.market.clock import NY_TZ
from app.shadows.portfolio import aware, ny_date
from app.shadows.strategy import NON_ARCHIVED, describe, differences, strategy_start

if TYPE_CHECKING:
    from app.config import AppConfig

Q2 = Decimal("0.01")


def _s(value: Decimal | None, q: Decimal = Q2) -> str | None:
    return str(value.quantize(q)) if value is not None else None


def _norm(action: str) -> str:
    return "no_call" if action == "no_call" else action


def _rules_no_call(action: str, reason: str | None) -> bool:
    return action == "no_call" and reason == "rules"


@dataclass(frozen=True)
class SameAsLive:
    matched: int
    counted: int

    @property
    def pct(self) -> Decimal | None:
        return Decimal(self.matched) / self.counted * 100 if self.counted else None


def _paired(
    session: Session, strategy_id: int, since: datetime
) -> list[tuple[ShadowDecision, Decision, Cycle]]:
    return [
        (row, live, cycle)
        for row, live, cycle in session.execute(
            select(ShadowDecision, Decision, Cycle)
            .join(Decision, ShadowDecision.live_decision_id == Decision.id)
            .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
            .where(
                ShadowDecision.strategy_id == strategy_id,
                Cycle.scheduled_time >= since.replace(tzinfo=None),
            )
            .order_by(ShadowDecision.id)
        ).all()
    ]


def counted_pair(row: ShadowDecision, live: Decision) -> bool:
    """Missed work and symbol-cycles that were rules no_calls on both sides
    are left out of "same as live" and the disagreements."""
    if row.missed:
        return False
    return not (
        _rules_no_call(row.final_action, row.no_call_reason)
        and _rules_no_call(live.final_action, live.no_call_reason)
    )


def same_as_live(session: Session, strategy_id: int, since: datetime) -> SameAsLive:
    matched = counted = 0
    for row, live, _cycle in _paired(session, strategy_id, since):
        if not counted_pair(row, live):
            continue
        counted += 1
        if _norm(row.final_action) == _norm(live.final_action):
            matched += 1
    return SameAsLive(matched, counted)


def _max_drawdown(values: Sequence[Decimal]) -> Decimal:
    peak = values[0] if values else Decimal(0)
    worst = Decimal(0)
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100)
    return worst


def _spy_return(session: Session, since: datetime) -> Decimal | None:
    """SPY buy-and-hold return since ``since`` from the benchmark column."""
    rows = [
        v
        for (v,) in session.execute(
            select(EquitySnapshot.spy_benchmark_value)
            .where(
                EquitySnapshot.time >= since.replace(tzinfo=None),
                EquitySnapshot.spy_benchmark_value.is_not(None),
            )
            .order_by(EquitySnapshot.time)
        ).all()
        if v is not None
    ]
    if not rows or rows[0] == 0:
        return None
    return (rows[-1] - rows[0]) / rows[0] * 100


def _cost(session: Session, strategy_id: int, since: datetime) -> Decimal:
    total = session.execute(
        select(func.sum(ShadowDecision.cost))
        .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
        .where(
            ShadowDecision.strategy_id == strategy_id,
            Cycle.scheduled_time >= since.replace(tzinfo=None),
        )
    ).scalar_one()
    return Decimal(total or 0)


def _curve(points: list[tuple[datetime, Decimal]], start: Decimal) -> list[dict[str, str]]:
    """Last value per New York session as % of starting equity."""
    by_day: dict[date, Decimal] = {}
    for t, equity in points:
        by_day[ny_date(t)] = equity
    return [
        {"date": d.isoformat(), "pct": str(((v - start) / start * 100).quantize(Q2))}
        for d, v in sorted(by_day.items())
        if start
    ]


def strategy_row(
    session: Session, strategy: Strategy, run: Run, config: AppConfig
) -> dict[str, Any]:
    """One shadow's Compare row, configuration card facts and curve."""
    start = aware(strategy_start(strategy, run.started_at))
    positions = list(
        session.execute(
            select(ShadowPosition).where(
                ShadowPosition.strategy_id == strategy.id,
                ShadowPosition.run_id == run.id,
            )
        ).scalars()
    )
    closed = [p for p in positions if p.status == "closed"]
    opened = [p for p in positions if p.status == "open"]
    won = sum(1 for p in closed if (p.pnl or 0) > 0)
    rs = [p.r for p in closed if p.r is not None]
    eq = [
        (aware(t), e)
        for t, e in session.execute(
            select(ShadowEquity.time, ShadowEquity.equity)
            .where(ShadowEquity.strategy_id == strategy.id, ShadowEquity.run_id == run.id)
            .order_by(ShadowEquity.time)
        ).all()
    ]
    equity_now = eq[-1][1] if eq else run.starting_equity
    ret = (equity_now - run.starting_equity) / run.starting_equity * 100
    spy = _spy_return(session, start)
    sal = same_as_live(session, strategy.id, start)
    calls = session.execute(
        select(func.count(ShadowDecision.id))
        .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
        .where(
            ShadowDecision.strategy_id == strategy.id,
            ShadowDecision.tokens.is_not(None),
            Cycle.scheduled_time >= start.replace(tzinfo=None),
        )
    ).scalar_one()
    missed = session.execute(
        select(func.count(ShadowDecision.id)).where(
            ShadowDecision.strategy_id == strategy.id, ShadowDecision.missed.is_(True)
        )
    ).scalar_one()
    min_trades = int(config.get("shadows", "promote_min_closed_trades", default=20))
    promotable = (
        strategy.kind == "llm" and strategy.status != "archived" and len(closed) >= min_trades
    )
    return {
        "id": strategy.id,
        "name": strategy.name,
        "kind": strategy.kind,
        "role": "baseline" if strategy.kind == "rules_only" else "shadow",
        "status": strategy.status,
        "description": describe(strategy, config),
        "prompt_version": strategy.prompt_version,
        "model": strategy.model,
        "temperature": strategy.temperature,
        "overrides": strategy.overrides or {},
        "started_at": start.isoformat(),
        "starting_equity": str(run.starting_equity),
        "return_pct": _s(ret),
        "raw_return_pct": None,
        "vs_spy_pts": _s(ret - spy) if spy is not None else None,
        "closed": len(closed),
        "won": won,
        "lost": len(closed) - won,
        "avg_r": _s(sum(rs, Decimal(0)) / len(rs)) if rs else None,
        "max_drawdown_pct": _s(_max_drawdown([run.starting_equity] + [e for _, e in eq])),
        "model_cost": _s(_cost(session, strategy.id, start), Decimal("0.0001")),
        "same_as_live_pct": _s(sal.pct, Decimal("1")) if sal.pct is not None else None,
        "same_as_live_count": sal.counted,
        "calls": int(calls),
        "missed": int(missed),
        "open_positions": [{"symbol": p.symbol, "qty": p.qty} for p in opened],
        "differences": differences(config, strategy),
        "promote": {
            "allowed": promotable,
            "closed": len(closed),
            "needed": min_trades,
            "reason": (
                None
                if promotable
                else "Rules-only baselines can't become live"
                if strategy.kind == "rules_only"
                else f"Needs at least {min_trades} closed trades (has {len(closed)})"
            ),
        },
        "curve": _curve(eq, run.starting_equity),
    }


def live_adjusted_curve(
    session: Session, run: Run, perf: dict[str, Any], config: AppConfig
) -> list[dict[str, str]]:
    """Live's curve as adjusted % of starting equity: each session's raw
    equity minus the slippage on fills up to that day; the last point is
    Performance's adjusted total exactly (it also includes dividends)."""
    curve = perf.get("curve") or {}
    points = curve.get("points") or []
    slip = Decimal(str(config.get("performance", "slippage_pct_per_side", default=0.05)))
    fills = session.execute(
        select(Fill.filled_at, Fill.qty, Fill.price).where(Fill.filled_at >= run.started_at)
    ).all()
    start = run.starting_equity
    out: list[dict[str, str]] = []
    for point in points:
        day = date.fromisoformat(point["date"])
        cum = sum((q * p * slip / 100 for t, q, p in fills if ny_date(t) <= day), Decimal(0))
        value = Decimal(point["equity"]) - cum
        out.append(
            {"date": point["date"], "pct": str(((value - start) / start * 100).quantize(Q2))}
        )
    summary = perf.get("summary") or {}
    if out and summary.get("adjusted_usd") is not None:
        out[-1]["pct"] = str((Decimal(summary["adjusted_usd"]) / start * 100).quantize(Q2))
    return out


def live_row(session: Session, run: Run, perf: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    summary = perf.get("summary") or {}
    start = run.starting_equity
    adjusted_pct = (
        Decimal(summary["adjusted_usd"]) / start * 100
        if summary.get("adjusted_usd") is not None and start
        else None
    )
    spy = Decimal(summary["spy_return_pct"]) if summary.get("spy_return_pct") else None
    cost = session.execute(
        select(func.sum(Decision.cost))
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .where(Cycle.scheduled_time >= run.started_at, Decision.model != "none")
    ).scalar_one()
    closed = int(summary.get("closed") or 0)
    wins = int(summary.get("wins") or 0)
    return {
        "id": None,
        "name": "Live",
        "kind": "llm",
        "role": "live",
        "status": "active",
        "description": (
            f"{config.get('decision', 'prompt')} · {config.get('decision', 'model')}"
            + (f" · settings v{config.active_version}" if config.active_version else "")
        ),
        "started_at": aware(run.started_at).isoformat(),
        "starting_equity": str(start),
        "return_pct": _s(adjusted_pct),
        "raw_return_pct": summary.get("return_pct"),
        "vs_spy_pts": _s(adjusted_pct - spy)
        if adjusted_pct is not None and spy is not None
        else None,
        "closed": closed,
        "won": wins,
        "lost": closed - wins,
        "avg_r": summary.get("avg_r"),
        "max_drawdown_pct": summary.get("max_drawdown_pct"),
        "model_cost": _s(Decimal(cost or 0), Decimal("0.0001")),
        "same_as_live_pct": None,
        "same_as_live_count": None,
        "curve": live_adjusted_curve(session, run, perf, config),
    }


def _confidence(output: dict[str, Any] | None) -> str:
    conf = (output or {}).get("confidence")
    return f" {float(conf):.2f}" if isinstance(conf, int | float) else ""


def _action_label(action: str, output: dict[str, Any] | None) -> str:
    return f"{action.upper()}{_confidence(output)}"


def _shadow_trade(session: Session, row: ShadowDecision) -> ShadowPosition | None:
    return session.execute(
        select(ShadowPosition).where(ShadowPosition.entry_decision_id == row.id)
    ).scalar_one_or_none()


def _live_trade_r(session: Session, live: Decision) -> tuple[Position | None, Decimal | None]:
    from app.engine.trades import _trade_r

    position = session.execute(
        select(Position).where(Position.entry_decision_id == live.id)
    ).scalar_one_or_none()
    if position is None:
        return None, None
    stop = (live.model_output or {}).get("stop_price")
    r = _trade_r(position, Decimal(str(stop))) if isinstance(stop, int | float) else None
    return position, r


def disagreements(
    session: Session, strategy: Strategy, run: Run, *, short: str, limit: int = 50
) -> dict[str, Any]:
    """Symbol-cycles where the shadow and live chose differently, newest
    first, with the trade result or the 5-session outcome."""
    start = aware(strategy_start(strategy, run.started_at))
    items: list[dict[str, Any]] = []
    sessions: set[date] = set()
    for row, live, cycle in _paired(session, strategy.id, start):
        if not counted_pair(row, live) or _norm(row.final_action) == _norm(live.final_action):
            continue
        when = aware(cycle.scheduled_time)
        sessions.add(ny_date(when))
        result, tone = None, "neutral"
        trade = _shadow_trade(session, row)
        if trade is not None:
            if trade.status == "closed" and trade.r is not None:
                result = f"{short} {trade.r:+.1f}R {trade.exit_reason}"
                tone = "positive" if trade.r > 0 else "negative"
            elif trade.status in ("open", "pending"):
                result = f"{short} open"
            else:
                result = f"{short} not filled"
        if result is None:
            position, r = _live_trade_r(session, live)
            if position is not None:
                if position.closed_at is None:
                    result = "live open"
                elif r is not None:
                    result = f"live {r:+.1f}R {position.exit_reason or ''}".rstrip()
                    tone = "positive" if r > 0 else "negative"
        if result is None:
            outcome = session.execute(
                select(Outcome).where(Outcome.decision_id == live.id)
            ).scalar_one_or_none()
            price = (live.input_snapshot or {}).get("price")
            if (
                outcome is not None
                and outcome.price_5sessions is not None
                and isinstance(price, int | float)
                and price
            ):
                pct = (outcome.price_5sessions - Decimal(str(price))) / Decimal(str(price)) * 100
                result = f"{pct:+.1f}% after 5 sessions"
                tone = "positive" if pct > 0 else "negative" if pct < 0 else "neutral"
            else:
                result = "waiting for 5 sessions"
        items.append(
            {
                "time": when.isoformat(),
                "date": when.astimezone(NY_TZ).date().isoformat(),
                "symbol": row.symbol,
                "live_action": live.final_action,
                "live_label": _action_label(live.final_action, live.model_output),
                "shadow_action": row.final_action,
                "shadow_label": _action_label(row.final_action, row.model_output),
                "live_decision_id": live.id,
                "result": result,
                "tone": tone,
            }
        )
    items.reverse()
    return {"total": len(items), "sessions": len(sessions), "items": items[:limit]}


def compare(
    session: Session,
    *,
    config: AppConfig,
    perf: dict[str, Any],
    include_archived: bool = False,
) -> dict[str, Any]:
    """The Strategies screen's comparison: live first, then every shadow."""
    run = session.execute(select(Run).order_by(Run.id.desc()).limit(1)).scalar_one_or_none()
    if run is None or perf.get("empty"):
        return {"run": None, "rows": [], "spy_curve": []}
    statuses = (*NON_ARCHIVED, "archived") if include_archived else NON_ARCHIVED
    strategies = list(
        session.execute(
            select(Strategy).where(Strategy.status.in_(statuses)).order_by(Strategy.id)
        ).scalars()
    )
    rows = [live_row(session, run, perf, config)] + [
        strategy_row(session, s, run, config) for s in strategies
    ]
    run_info = perf.get("run") or {}
    start = run.starting_equity
    spy_curve = [
        {
            "date": point["date"],
            "pct": str(((Decimal(point["benchmark"]) - start) / start * 100).quantize(Q2)),
        }
        for point in (perf.get("curve") or {}).get("points") or []
        if point.get("benchmark") is not None and start
    ]
    return {
        "run": {
            "id": run.id,
            "started_at": aware(run.started_at).isoformat(),
            "sessions": run_info.get("sessions"),
        },
        "rows": rows,
        "spy_curve": spy_curve,
    }


def weekly_comparison(
    session: Session, *, config: AppConfig, perf: dict[str, Any]
) -> list[dict[str, Any]]:
    """The comparison as weekly-review facts (numbers only, no curves)."""
    keep = (
        "name",
        "role",
        "return_pct",
        "vs_spy_pts",
        "closed",
        "won",
        "lost",
        "avg_r",
        "max_drawdown_pct",
        "model_cost",
        "same_as_live_pct",
        "same_as_live_count",
        "missed",
    )
    out = []
    for row in compare(session, config=config, perf=perf)["rows"]:
        fact: dict[str, Any] = {}
        for key in keep:
            value = row.get(key)
            if isinstance(value, str) and key not in ("name", "role"):
                try:
                    fact[key] = float(value)
                    continue
                except ValueError:
                    pass
            fact[key] = value
        out.append(fact)
    return out
