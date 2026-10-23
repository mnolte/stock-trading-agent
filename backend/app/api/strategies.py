"""Strategies screen API (docs/SPEC.md, Strategies and shadows, PR 13)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import AppConfig, get_config
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import Run, ShadowDecision, ShadowPosition, Strategy
from app.decide.client import priced_models
from app.decide.prompt import PROMPTS_DIR, load_prompt
from app.engine.performance import compute_performance
from app.market.calendar import CalendarProvider, WeekdayCalendarProvider
from app.market.clock import Clock, make_clock
from app.settings_service import EDITABLE, LABELS
from app.shadows.compare import compare, disagreements
from app.shadows.strategy import (
    OVERRIDE_KEYS,
    StrategyError,
    promotion_changes,
    slots_used,
    validate_new_strategy,
)

router = APIRouter()


def _clock(request: Request) -> Clock:
    clock = getattr(request.app.state, "clock", None)
    return clock if clock is not None else make_clock()


def _calendar(request: Request) -> CalendarProvider:
    store = getattr(request.app.state, "calendar_store", None)
    return store if store is not None else WeekdayCalendarProvider()


def _notify(request: Request, *topics: str) -> None:
    bus = getattr(request.app.state, "update_bus", None)
    if bus is not None:
        bus.publish(*topics)


def live_performance(request: Request, session: Session, config: AppConfig) -> dict[str, Any]:
    """The Performance screen's own numbers for the current run."""
    state = getattr(request.app.state, "exec_state", None)
    account = getattr(state, "account", None) if state is not None else None
    return compute_performance(
        session,
        config=config,
        market_data=getattr(request.app.state, "market_data", None),
        calendar=_calendar(request),
        clock=_clock(request),
        live_equity=account.equity if account is not None else None,
    )


def short_name(name: str) -> str:
    """"B · Bigger model" → "B" for the disagreements list."""
    return name.split(" · ", 1)[0].strip() or name


def promotion_payload(config: AppConfig, strategy: Strategy, closed: int) -> dict[str, Any]:
    needed = int(config.get("shadows", "promote_min_closed_trades", default=20))
    allowed = strategy.kind == "llm" and strategy.status != "archived" and closed >= needed
    changes = promotion_changes(config, strategy)
    lines = [
        f"{LABELS.get((section, key), f'{section}.{key}')}: "
        f"{config.get(section, key)} → {value}"
        for section, body in changes.items()
        for key, value in body.items()
    ]
    return {
        "strategy_id": strategy.id,
        "name": strategy.name,
        "allowed": allowed,
        "closed": closed,
        "needed": needed,
        "changes": changes,
        "lines": lines,
        "reason": f"Promote shadow {strategy.name!r} to live",
    }


def _prompt_versions() -> list[str]:
    out = []
    for path in sorted(PROMPTS_DIR.glob("decision.*.md")):
        try:
            load_prompt(path.stem)
        except OSError:
            continue
        out.append(path.stem)
    return out


@router.get("/strategies")
def strategies(request: Request) -> dict[str, Any]:
    config = get_config()
    runner = getattr(request.app.state, "shadow_runner", None)
    with get_session_maker()() as session:
        perf = live_performance(request, session, config)
        data = compare(session, config=config, perf=perf)
        run = session.execute(select(Run).order_by(Run.id.desc()).limit(1)).scalar_one_or_none()
        for row in data["rows"]:
            if row["id"] is None or run is None:
                continue
            strategy = session.get(Strategy, row["id"])
            assert strategy is not None
            row["short"] = short_name(strategy.name)
            row["disagreements"] = disagreements(session, strategy, run, short=row["short"])
        spend = runner.monthly_spend(session) if runner is not None else Decimal(0)
        cap = Decimal(str(config.get("shadows", "monthly_cost_cap_usd", default=5)))
        used = slots_used(session)
        synthetic_entries = session.execute(
            select(func.count(ShadowPosition.id)).join(
                ShadowDecision, ShadowPosition.entry_decision_id == ShadowDecision.id,
            ).where(
                ShadowPosition.run_id == run.id if run else ShadowPosition.id < 0,
                ShadowPosition.entry.is_not(None),
                func.json_extract(ShadowDecision.gates_result, "$.quote.synthetic") == 1,
            )
        ).scalar_one()
    return {
        **data,
        "synthetic_quote_entries": synthetic_entries,
        "cost": {
            "month_usd": str(spend),
            "cap_usd": str(cap),
            "cap_reached": spend >= cap,
        },
        "slots": {
            "used": used,
            "max": int(config.get("shadows", "max_active", default=3)),
        },
        "promote_min_closed_trades": int(
            config.get("shadows", "promote_min_closed_trades", default=20)
        ),
        "rules_only": {
            "stop_atr": config.get("shadows", "rules_only", "stop_atr", default=2.0),
            "target_r": config.get("shadows", "rules_only", "target_r", default=2.0),
        },
        "options": {
            "models": priced_models(config),
            "prompts": _prompt_versions(),
            "live": {
                "prompt_version": config.get("decision", "prompt"),
                "model": config.get("decision", "model"),
                "temperature": config.get("decision", "temperature", default=0),
            },
            "overrides": [
                {
                    "key": name,
                    "label": LABELS.get((section, key), name),
                    "live": config.get(section, key),
                    "min": EDITABLE[section][key][1],
                    "max": EDITABLE[section][key][2],
                    "check": section == "decision",
                }
                for name, (section, key) in OVERRIDE_KEYS.items()
            ],
        },
    }


@router.post("/strategies")
def create_strategy(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """New shadow: validated, then active from the next live cycle."""
    config = get_config()
    with get_session_maker()() as session:
        try:
            cleaned = validate_new_strategy(body, config, session)
        except StrategyError as exc:
            raise HTTPException(status_code=422, detail=exc.errors) from exc
        strategy = Strategy(
            **cleaned,
            status="active",
            created_at=_naive_utc(_clock(request).now()),
        )
        session.add(strategy)
        session.commit()
        strategy_id = strategy.id
    _notify(request, "strategies")
    return {"id": strategy_id}


def _set_status(request: Request, strategy_id: int, status: str) -> dict[str, Any]:
    config = get_config()
    with get_session_maker()() as session:
        strategy = session.get(Strategy, strategy_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail="strategy not found")
        if strategy.status == "archived":
            raise HTTPException(status_code=409, detail=["an archived shadow can't change"])
        if status == "active" and strategy.status == "paused" and strategy.kind == "llm":
            # A resumed LLM shadow must still have a priced model.
            from app.decide.client import model_price

            if model_price(config.get("model_prices", default={}), str(strategy.model)) is None:
                raise HTTPException(
                    status_code=422, detail=[f"Model {strategy.model!r} has no price"]
                )
        strategy.status = status
        if status == "archived":
            strategy.archived_at = _naive_utc(_clock(request).now())
        session.commit()
    _notify(request, "strategies")
    return {"id": strategy_id, "status": status}


@router.post("/strategies/{strategy_id}/pause")
def pause_strategy(request: Request, strategy_id: int) -> dict[str, Any]:
    return _set_status(request, strategy_id, "paused")


@router.post("/strategies/{strategy_id}/resume")
def resume_strategy(request: Request, strategy_id: int) -> dict[str, Any]:
    return _set_status(request, strategy_id, "active")


@router.post("/strategies/{strategy_id}/archive")
def archive_strategy(request: Request, strategy_id: int) -> dict[str, Any]:
    return _set_status(request, strategy_id, "archived")


def closed_trades(session: Session, strategy: Strategy) -> int:
    from sqlalchemy import func

    from app.db.models import ShadowPosition

    run = session.execute(select(Run).order_by(Run.id.desc()).limit(1)).scalar_one_or_none()
    if run is None:
        return 0
    return int(
        session.execute(
            select(func.count(ShadowPosition.id)).where(
                ShadowPosition.strategy_id == strategy.id,
                ShadowPosition.run_id == run.id,
                ShadowPosition.status == "closed",
            )
        ).scalar_one()
    )


@router.get("/strategies/{strategy_id}/promotion")
def promotion(strategy_id: int) -> dict[str, Any]:
    """The shadow's differences, ready to pre-fill Settings as a new version."""
    config = get_config()
    with get_session_maker()() as session:
        strategy = session.get(Strategy, strategy_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail="strategy not found")
        return promotion_payload(config, strategy, closed_trades(session, strategy))


def finish_promotion(
    session: Session, strategy_id: int, version: int, now: datetime
) -> None:
    """After the promoted settings version is saved: archive the shadow."""
    strategy = session.get(Strategy, strategy_id)
    if strategy is not None:
        strategy.status = "archived"
        strategy.archived_at = _naive_utc(now)
        strategy.promoted_version = version


def check_promotion(session: Session, config: AppConfig, strategy_id: int) -> None:
    """Refuse a promote save that the 20-trade rule doesn't allow."""
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    payload = promotion_payload(config, strategy, closed_trades(session, strategy))
    if not payload["allowed"]:
        raise HTTPException(
            status_code=409,
            detail=[
                f"Promote needs at least {payload['needed']} closed trades "
                f"(has {payload['closed']})"
                if strategy.kind == "llm"
                else "A rules-only baseline can't be promoted"
            ],
        )
