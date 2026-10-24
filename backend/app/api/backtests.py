"""Backtest screen API (docs/SPEC.md, Backtesting, GUI)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.backtests.bundle import slugify
from app.backtests.estimate import (
    Estimate,
    StrategySpec,
    estimate,
)
from app.backtests.manager import BacktestManager, store_usage
from app.backtests.store import run_dir_for, session_maker_for
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import Backtest, Cycle, Decision, Outcome, Position, Strategy
from app.decide.client import priced_models
from app.decide.prompt import load_prompt
from app.market.calendar import CalendarStore
from app.market.clock import Clock, make_clock

router = APIRouter()


class RunRequest(BaseModel):
    name: str = ""
    start: date
    end: date
    universe_mode: str = "fixed"
    strategies: list[str]
    starting_equity: Decimal = Decimal("10000")
    slippage_pct: Decimal = Decimal("0.05")


def _clock(request: Request) -> Clock:
    clock = getattr(request.app.state, "clock", None)
    return clock if clock is not None else make_clock()


def _manager(request: Request) -> BacktestManager:
    manager = getattr(request.app.state, "backtests", None)
    if manager is None:
        manager = BacktestManager(get_config(), _clock(request))
        request.app.state.backtests = manager
    return manager


def _store(request: Request) -> Any:
    from app.backtests.store import bar_store_path, make_bar_store

    return make_bar_store(bar_store_path(get_config()), get_config())


def _resolve_strategies(
    request: Request, session: Session, refs: list[str]
) -> list[StrategySpec]:
    """'live' | 'rules_only' | 'strategy:<id>' → specs, or 404."""
    specs: list[StrategySpec] = []
    seen_slugs: set[str] = set()
    for ref in refs:
        if ref == "live":
            cfg = get_config()
            slug = _unique("live", seen_slugs)
            specs.append(
                StrategySpec(
                    ref="live",
                    name="Live settings",
                    kind="llm",
                    slug=slug,
                    model=str(cfg.get("decision", "model", default="")),
                    prompt_version=str(
                        cfg.get("decision", "prompt", default="decision.v1")
                    ),
                    temperature=float(
                        cfg.get("decision", "temperature", default=0.2)
                    ),
                )
            )
        elif ref == "rules_only":
            specs.append(
                StrategySpec(
                    ref="rules_only",
                    name="Rules only",
                    kind="rules_only",
                    slug=_unique("rules-only", seen_slugs),
                )
            )
        elif ref.startswith("strategy:"):
            strategy = session.get(Strategy, int(ref.split(":", 1)[1]))
            if strategy is None or strategy.status == "archived":
                raise HTTPException(404, f"strategy {ref} not found")
            specs.append(
                StrategySpec(
                    ref=ref,
                    name=strategy.name,
                    kind=strategy.kind,
                    slug=_unique(slugify(strategy.name), seen_slugs),
                    model=strategy.model,
                    prompt_version=strategy.prompt_version,
                    temperature=strategy.temperature,
                    strategy=strategy,
                )
            )
        else:
            raise HTTPException(400, f"unknown strategy ref {ref!r}")
    return specs


def _unique(slug: str, seen: set[str]) -> str:
    base, i = slug, 2
    while slug in seen:
        slug = f"{base}-{i}"
        i += 1
    seen.add(slug)
    return slug


def _estimate_payload(est: Estimate) -> dict[str, Any]:
    return {
        "sessions": est.sessions,
        "bars_to_download": est.bars_to_download,
        "download_calls": est.download_calls,
        "download_minutes": est.download_minutes,
        "calls": est.calls,
        "cache_hits": est.cache_hits,
        "cost_usd": float(est.cost_usd),
        "cap_cost_usd": float(est.cap_cost_usd),
        "cap_remaining_usd": float(est.cap_remaining_usd),
        "may_stop_early": est.may_stop_early,
        "disabled_reason": est.disabled_reason,
        "cutoff": est.cutoff.isoformat() if est.cutoff else None,
        "cutoff_label": est.cutoff_label,
        "strategies": [
            {
                "ref": s.ref,
                "name": s.name,
                "kind": s.kind,
                "model": s.model,
                "calls": s.calls,
                "cache_hits": s.cache_hits,
                "cost_usd": float(s.cost_usd) if s.cost_usd is not None else None,
                "cutoff": s.cutoff.isoformat() if s.cutoff else None,
            }
            for s in est.strategies
        ],
    }


def _specs_json(specs: list[StrategySpec]) -> list[dict[str, Any]]:
    return [
        {
            "ref": s.ref,
            "name": s.name,
            "kind": s.kind,
            "slug": s.slug,
            "model": s.model,
            "prompt_version": s.prompt_version,
            "temperature": s.temperature,
        }
        for s in specs
    ]


def _calendar(request: Request, start: date, end: date) -> CalendarStore:
    """A throwaway store loaded for the estimate's range — the app's live
    calendar keeps its own loaded range."""
    from app.data.fake import FakeCalendarSource

    sources = getattr(request.app.state, "data_sources", None)
    store = CalendarStore(
        sources.calendar if sources is not None else FakeCalendarSource()
    )
    return store


def _validate_range(body: RunRequest) -> None:
    if body.end <= body.start:
        raise HTTPException(400, "end must be after start")
    max_years = float(
        get_config().get("backtest", "max_range_years", default=3)
    )
    if (body.end - body.start).days > max_years * 366:
        raise HTTPException(
            400, f"range is longer than {max_years:g} years"
        )


async def _estimate(request: Request, body: RunRequest) -> tuple[Estimate, list[StrategySpec]]:
    _validate_range(body)
    sessions = get_session_maker()
    with sessions() as session:
        specs = _resolve_strategies(request, session, body.strategies)
    store = _store(request)
    calendar = _calendar(request, body.start, body.end)
    if hasattr(calendar, "load"):
        await calendar.load(body.start, body.end)
    symbols = _universe_symbols(request, body.universe_mode)
    return (
        estimate(
            config=get_config(),
            session_factory=sessions,
            calendar=calendar,
            start=body.start,
            end=body.end,
            strategies=specs,
            symbols=symbols,
            store=store,
            today=_clock(request).now().date(),
        ),
        specs,
    )


def _universe_symbols(request: Request, mode: str) -> list[str]:
    cfg = get_config()
    if mode == "screened":
        with get_session_maker()() as session:
            from app.db.models import UniversePoolRow

            pool = session.execute(select(UniversePoolRow.symbol)).scalars().all()
        if pool:
            return sorted(set(pool) | {"SPY"})
    return sorted(set(cfg.get("universe", "symbols", default=[]) or []) | {"SPY"})


@router.post("/backtests/estimate")
async def estimate_run(request: Request, body: RunRequest) -> dict[str, Any]:
    est, _specs = await _estimate(request, body)
    return _estimate_payload(est)


@router.post("/backtests")
async def create_run(request: Request, body: RunRequest) -> dict[str, Any]:
    if not body.strategies:
        raise HTTPException(400, "pick at least one strategy")
    est, specs = await _estimate(request, body)
    manager = _manager(request)
    name = body.name or f"{body.start.isoformat()} – {body.end.isoformat()}"
    if est.disabled_reason is not None:
        row = manager.refuse(
            name=name,
            start=body.start,
            end=body.end,
            universe_mode=body.universe_mode,
            strategies=_specs_json(specs),
            starting_equity=body.starting_equity,
            slippage_pct=body.slippage_pct,
            reason=est.disabled_reason,
        )
        return {"id": row.id, "status": "refused", "reason": est.disabled_reason}
    row = manager.create(
        name=name,
        start=body.start,
        end=body.end,
        universe_mode=body.universe_mode,
        strategies=_specs_json(specs),
        starting_equity=body.starting_equity,
        slippage_pct=body.slippage_pct,
    )
    manager.tick()
    _notify(request, "backtests")
    return {"id": row.id, "status": row.status}


@router.get("/backtests")
def list_runs(request: Request) -> dict[str, Any]:
    sessions = get_session_maker()
    with sessions() as session:
        rows = session.execute(
            select(Backtest).order_by(Backtest.id.desc())
        ).scalars().all()
        items = [_row_json(r) for r in rows]
        strategies = session.execute(
            select(Strategy).where(Strategy.status != "archived")
        ).scalars().all()
        shadows = [
            {
                "ref": f"strategy:{s.id}",
                "name": s.name,
                "kind": s.kind,
                "model": s.model,
                "prompt_version": s.prompt_version,
            }
            for s in strategies
        ]
    manager = _manager(request)
    manager.tick()
    store = store_usage(get_config(), sessions)
    return {
        "runs": items,
        "store": store,
        "monthly_cap": float(
            get_config().get("backtest", "monthly_cost_cap_usd", default=20)
        ),
        "available": {
            "live": {
                "ref": "live",
                "name": "Live settings",
                "model": get_config().get("decision", "model"),
                "prompt_version": get_config().get("decision", "prompt"),
            },
            "rules_only": {"ref": "rules_only", "name": "Rules only"},
            "shadows": shadows,
            "priced_models": priced_models(get_config()),
            "model_cutoffs": get_config().get(
                "backtest", "model_cutoffs", default={}
            ),
            "universe": {
                "mode": get_config().get("universe", "mode", default="fixed"),
                "fixed_count": len(
                    get_config().get("universe", "symbols", default=[]) or []
                ),
            },
        },
    }


def _row_json(row: Backtest) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "start": row.start_date.isoformat(),
        "end": row.end_date.isoformat(),
        "universe_mode": row.universe_mode,
        "strategies": row.strategies,
        "starting_equity": str(row.starting_equity),
        "slippage_pct": str(row.slippage_pct),
        "status": row.status,
        "progress": row.progress,
        "cost_usd": float(row.cost_usd),
        "process_id": row.process_id,
        "size_bytes": row.size_bytes,
        "warnings": row.warnings,
        "summary": row.summary,
        "refuse_reason": row.refuse_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": (
            row.finished_at.isoformat() if row.finished_at else None
        ),
    }


@router.get("/backtests/{run_id}")
def run_detail(request: Request, run_id: int) -> dict[str, Any]:
    with get_session_maker()() as session:
        row = session.get(Backtest, run_id)
        if row is None:
            raise HTTPException(404, "run not found")
        payload = _row_json(row)
    _manager(request).tick()
    return payload


@router.post("/backtests/{run_id}/cancel")
def cancel_run(request: Request, run_id: int) -> dict[str, Any]:
    if not _manager(request).cancel(run_id):
        raise HTTPException(400, "run is not active")
    return {"ok": True}


@router.delete("/backtests/{run_id}")
def delete_run(request: Request, run_id: int) -> dict[str, Any]:
    if not _manager(request).delete(run_id):
        raise HTTPException(400, "run is active or unknown")
    _notify(request, "backtests")
    return {"ok": True}


@router.post("/backtests/bar-store/clear")
def clear_store(request: Request) -> dict[str, Any]:
    if not _manager(request).clear_bar_store():
        raise HTTPException(400, "a run is active")
    return {"ok": True}


# ---------------------------------------------------------------------------
# per-strategy detail (the run's own databases)


def _strategy_sf(request: Request, run_id: int, slug: str) -> sessionmaker[Session]:
    with get_session_maker()() as session:
        row = session.get(Backtest, run_id)
        if row is None:
            raise HTTPException(404, "run not found")
        if not any(s["slug"] == slug for s in row.strategies):
            raise HTTPException(404, "strategy not in run")
    path = run_dir_for(get_config(), run_id) / f"{slug}.db"
    if not path.exists():
        raise HTTPException(404, "strategy database not found")
    return session_maker_for(path, get_config())


@router.get("/backtests/{run_id}/strategies/{slug}/trades")
def run_trades(request: Request, run_id: int, slug: str) -> dict[str, Any]:
    sf = _strategy_sf(request, run_id, slug)
    with sf() as session:
        rows = session.execute(
            select(Position).order_by(Position.opened_at)
        ).scalars().all()
    return {
        "trades": [
            {
                "id": p.id,
                "symbol": p.symbol,
                "qty": p.qty,
                "entry": str(p.avg_entry_price),
                "opened_at": _iso(p.opened_at),
                "closed_at": _iso(p.closed_at),
                "exit_reason": p.exit_reason,
                "pnl_raw": str(p.realized_pnl_raw) if p.realized_pnl_raw else None,
                "pnl_adjusted": (
                    str(p.realized_pnl_adjusted) if p.realized_pnl_adjusted else None
                ),
            }
            for p in rows
        ]
    }


@router.get("/backtests/{run_id}/strategies/{slug}/decisions")
def run_decisions(request: Request, run_id: int, slug: str) -> dict[str, Any]:
    sf = _strategy_sf(request, run_id, slug)
    with sf() as session:
        rows = session.execute(
            select(Decision, Cycle.scheduled_time)
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .order_by(Cycle.scheduled_time, Decision.id)
        ).all()
    return {
        "items": [
            {
                "id": d.id,
                "cycle_time": _iso(scheduled),
                "symbol": d.symbol,
                "context": d.context,
                "final_action": d.final_action,
                "no_call_reason": d.no_call_reason,
                "cached": d.cached,
            }
            for d, scheduled in rows
        ]
    }


@router.get("/backtests/{run_id}/strategies/{slug}/decisions/{decision_id}")
def run_decision_detail(
    request: Request, run_id: int, slug: str, decision_id: int
) -> dict[str, Any]:
    """The same payload the live decision drawer renders, read from the
    run's own database."""
    from app.api.routes import (
        _decision_earnings,
        _iso,
        _order_json,
        _r_multiple,
        _trade_block,
    )
    from app.db.models import Approval, Fill, Order, Position, Verdict

    sf = _strategy_sf(request, run_id, slug)
    with sf() as session:
        row = session.execute(
            select(Decision, Cycle.scheduled_time)
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .where(Decision.id == decision_id)
        ).first()
        if row is None:
            raise HTTPException(404, "decision not found")
        decision, scheduled = row
        outcome = session.execute(
            select(Outcome).where(Outcome.decision_id == decision_id)
        ).scalar_one_or_none()
        order_rows = session.execute(
            select(Order)
            .where(Order.decision_id == decision_id)
            .order_by(Order.id)
        ).scalars().all()
        fills = {
            o.id: session.execute(
                select(Fill).where(Fill.order_id == o.id)
            ).scalars().all()
            for o in order_rows
        }
        approval_row = session.execute(
            select(Approval)
            .where(Approval.decision_id == decision_id)
            .order_by(Approval.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        position_row = session.execute(
            select(Position).where(Position.entry_decision_id == decision_id)
        ).scalar_one_or_none()
        # Column select, not the entity: run databases keep the schema they
        # were created with, so newer Verdict columns (draft_reason, facts)
        # are not guaranteed to exist.
        verdict_row = session.execute(
            select(
                Verdict.id,
                Verdict.verdict,
                Verdict.reason,
                Verdict.source,
                Verdict.draft_source,
                Verdict.created_at,
                Verdict.updated_at,
            ).where(Verdict.decision_id == decision_id)
        ).first()
        earnings = _decision_earnings(session, decision, scheduled)
    output = decision.model_output or {}
    snapshot = decision.input_snapshot or {}
    trade = _trade_block(position_row, order_rows, fills, approval_row, output, snapshot)
    entry = Decimal(trade["entry"]) if isinstance(trade["entry"], str) else None
    stop = Decimal(trade["stop"]) if isinstance(trade["stop"], str) else None
    try:
        prompt_text = load_prompt(decision.prompt_version).text
    except OSError:
        prompt_text = None
    return {
        "id": decision.id,
        "cycle_time": _iso(scheduled),
        "symbol": decision.symbol,
        "context": decision.context,
        "final_action": decision.final_action,
        "no_call_reason": decision.no_call_reason,
        "cached": decision.cached,
        "input_snapshot": decision.input_snapshot,
        "rules_result": decision.rules_result,
        "model_output": decision.model_output,
        "validation_result": decision.validation_result,
        "gates_result": decision.gates_result,
        "trade": trade,
        "outcome": (
            {
                "price_1h": str(outcome.price_1h) if outcome.price_1h else None,
                "price_1h_at": _iso(outcome.price_1h_at),
                "price_1session": str(outcome.price_1session) if outcome.price_1session else None,
                "price_1session_at": _iso(outcome.price_1session_at),
                "price_5sessions": str(outcome.price_5sessions)
                if outcome.price_5sessions
                else None,
                "price_5sessions_at": _iso(outcome.price_5sessions_at),
                "best_price": str(outcome.best_price) if outcome.best_price else None,
                "best_price_at": _iso(outcome.best_price_at),
                "worst_price": str(outcome.worst_price) if outcome.worst_price else None,
                "worst_price_at": _iso(outcome.worst_price_at),
                "first_touch": outcome.first_touch,
                "r_1h": _r_multiple(outcome.price_1h, entry, stop),
                "r_1session": _r_multiple(outcome.price_1session, entry, stop),
                "r_5sessions": _r_multiple(
                    outcome.price_5sessions, entry, stop
                ),
                "best_r": _r_multiple(outcome.best_price, entry, stop),
                "worst_r": _r_multiple(outcome.worst_price, entry, stop),
            }
            if outcome is not None
            else None
        ),
        "next_outcome_due": None,
        "verdict": (
            {
                "id": verdict_row.id,
                "verdict": verdict_row.verdict,
                "reason": verdict_row.reason,
                "source": verdict_row.source,
                "draft_source": verdict_row.draft_source,
                "created_at": _iso(verdict_row.created_at),
                "updated_at": _iso(verdict_row.updated_at),
            }
            if verdict_row is not None
            else None
        ),
        "verdict_eligible": False,
        "verdict_due": None,
        # The inspector reads the live database only; run databases have no
        # llm_calls history to link to.
        "llm_call_id": None,
        "earnings": earnings,
        "execution_mode": get_config().get(
            "execution", "mode", default="automatic"
        ),
        "eur_usd_rate": None,
        "prompt_version": decision.prompt_version,
        "prompt_hash": decision.prompt_hash,
        "prompt_text": prompt_text,
        "model": decision.model,
        "tokens_in": decision.tokens_in,
        "tokens_out": decision.tokens_out,
        "tokens": decision.tokens,
        "cost": str(decision.cost) if decision.cost is not None else None,
        "latency_ms": decision.latency_ms,
        "raw_response": decision.raw_response,
        "orders": [_order_json(o, fills[o.id]) for o in order_rows],
        "approval": (
            {
                "id": approval_row.id,
                "status": approval_row.status,
                "reason": approval_row.reason,
                "qty": approval_row.qty,
                "limit_price": str(approval_row.limit_price),
                "created_at": _iso(approval_row.created_at),
                "resolved_at": _iso(approval_row.resolved_at),
            }
            if approval_row is not None
            else None
        ),
    }


@router.get("/backtests/{run_id}/strategies/{slug}/export.csv")
def export_csv(
    request: Request, run_id: int, slug: str, kind: str = "trades"
) -> StreamingResponse:
    sf = _strategy_sf(request, run_id, slug)
    buf = io.StringIO()
    writer = csv.writer(buf)
    with sf() as session:
        if kind == "decisions":
            rows = session.execute(
                select(Decision, Cycle.scheduled_time)
                .join(Cycle, Decision.cycle_id == Cycle.id)
                .order_by(Cycle.scheduled_time, Decision.id)
            ).all()
            writer.writerow(
                ["cycle_time", "symbol", "context", "final_action", "no_call_reason",
                 "model", "tokens", "cost", "cached"]
            )
            for d, scheduled in rows:
                writer.writerow(
                    [_iso(scheduled), d.symbol, d.context, d.final_action,
                     d.no_call_reason, d.model, d.tokens,
                     d.cost if d.cost is not None else "", d.cached]
                )
        else:
            writer.writerow(
                ["symbol", "qty", "entry", "opened_at", "closed_at",
                 "exit_reason", "pnl_raw", "pnl_adjusted"]
            )
            for p in session.execute(
                select(Position).order_by(Position.opened_at)
            ).scalars():
                writer.writerow(
                    [p.symbol, p.qty, p.avg_entry_price, _iso(p.opened_at),
                     _iso(p.closed_at), p.exit_reason, p.realized_pnl_raw,
                     p.realized_pnl_adjusted]
                )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="run{run_id}-{slug}-{kind}.csv"'
        },
    )


def _iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=UTC).isoformat()
    return str(dt)


def _notify(request: Request, *topics: str) -> None:
    bus = getattr(request.app.state, "update_bus", None)
    if bus is not None:
        bus.publish(*topics)
