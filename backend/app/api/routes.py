from __future__ import annotations

from datetime import datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Outcome
from app.decide.prompt import load_prompt
from app.market.calendar import CalendarProvider, WeekdayCalendarProvider
from app.market.clock import UTC, SessionClock, SessionState, make_clock
from app.signals.service import SignalService

router = APIRouter()


def _calendar(request: Request) -> CalendarProvider:
    store = getattr(request.app.state, "calendar_store", None)
    return store if store is not None else WeekdayCalendarProvider()


def _calendar_loaded(request: Request) -> bool:
    store = getattr(request.app.state, "calendar_store", None)
    return getattr(store, "is_loaded", True)


def _eur_rate(request: Request) -> dict[str, object]:
    """Latest ECB rate for EUR display; never stale or zero."""
    market_data = getattr(request.app.state, "market_data", None)
    result = getattr(market_data, "latest_fx", None) if market_data else None
    rate = result.data if result is not None and result.error is None else None
    return {
        "available": rate is not None,
        "rate": str(rate.usd_per_eur) if rate is not None else None,
    }


def _session_state(request: Request) -> SessionState:
    clock = SessionClock(make_clock(), calendar=_calendar(request))
    return clock.state()


def _signals(request: Request) -> SignalService | None:
    return getattr(request.app.state, "signal_service", None)


def _regime_label(request: Request) -> str:
    """Live regime from SPY daily bars; 'unavailable' until data is in."""
    signals = _signals(request)
    if signals is None:
        return "unavailable"
    try:
        return signals.regime(signals.clock.now()).label
    except Exception:  # noqa: BLE001
        return "unavailable"


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": get_config().env.mode}


@router.get("/session")
def session_state(request: Request) -> dict[str, object]:
    state = _session_state(request)
    return {
        "state": state.state,
        "next_cycle": state.next_cycle.isoformat() if state.next_cycle else None,
        "next_open": state.next_open.isoformat() if state.next_open else None,
        "next_close": state.next_close.isoformat() if state.next_close else None,
        "display_time": state.display_time,
        "calendar_loaded": _calendar_loaded(request),
    }


@router.get("/header")
def header_state(request: Request) -> dict[str, object]:
    """Skeleton header payload: PAPER badge plus placeholders for equity/P&L/regime."""
    session = _session_state(request)
    return {
        "paper": True,
        "mode": get_config().env.mode,
        "session": {
            "state": session.state,
            "display_time": session.display_time,
            "next_cycle": session.next_cycle.isoformat() if session.next_cycle else None,
            "calendar_loaded": _calendar_loaded(request),
        },
        "equity_usd": "10,184.30",
        "equity_eur": "8,749.40",
        "today_usd": "+42.10",
        "today_eur": "+36.17",
        "regime": _regime_label(request),
        "kill_switch": False,
        "eur": _eur_rate(request),
    }


@router.get("/watchlist")
def watchlist(request: Request) -> dict[str, object]:
    """Per-symbol rules status; serves the stored signal snapshot."""
    signals = _signals(request)
    result = signals.latest if signals is not None else None
    if result is None:
        return {"computed_at": None, "regime": None, "rows": []}
    return {
        "computed_at": result.computed_at.isoformat(),
        "regime": {
            "trend": result.regime.trend,
            "volatility": result.regime.volatility,
            "label": result.regime.label,
            "blocks_entries": result.regime.blocks_entries,
        },
        "rows": [
            {
                "symbol": row.symbol,
                "price": str(row.price) if row.price is not None else None,
                "day_change_pct": (
                    str(row.day_change_pct)
                    if row.day_change_pct is not None
                    else None
                ),
                "rules": {
                    "status": row.rules.status,
                    "reason": row.rules.reason,
                    "checks": [
                        {
                            "name": check.name,
                            "passed": check.passed,
                            "detail": check.detail,
                        }
                        for check in row.rules.checks
                    ],
                },
                "earnings_date": (
                    row.earnings_date.isoformat() if row.earnings_date else None
                ),
                "last_action": row.last_action,
                "last_action_time": (
                    row.last_action_time.isoformat()
                    if row.last_action_time
                    else None
                ),
            }
            for row in result.rows
        ],
    }


# ----------------------------------------------------------------------
# Decisions / LLM inspector


def _decision_summary(
    request: Request, rows: list[tuple[Decision, datetime]]
) -> dict[str, object]:
    """The inspector's status strip: calls, cost, changed count, prompt, model."""
    runner = getattr(request.app.state, "cycle_runner", None)
    prompt = runner.decide.prompt if runner is not None else None
    config = get_config()
    changed = 0
    cost = Decimal(0)
    for decision, _ in rows:
        cost += decision.cost or Decimal(0)
        validation = decision.validation_result or {}
        if validation.get("changed_by_code"):
            changed += 1
    return {
        "calls": len(rows),
        "cost": str(cost.quantize(Decimal("0.0000001"))),
        "changed_by_code": changed,
        "prompt_version": prompt.version if prompt else None,
        "prompt_hash": (prompt.short_hash if prompt else None),
        "model": config.get("decision", "model", default=None),
        "temperature": config.get("decision", "temperature", default=None),
    }


def _decision_item(decision: Decision, scheduled: datetime) -> dict[str, object]:
    validation = decision.validation_result or {}
    return {
        "id": decision.id,
        "cycle_time": scheduled.replace(tzinfo=UTC).isoformat(),
        "symbol": decision.symbol,
        "context": decision.context,
        "model_action": validation.get("model_action"),
        "final_action": decision.final_action,
        "changed_by_code": bool(validation.get("changed_by_code")),
        "valid": validation.get("valid"),
    }


@router.get("/decisions")
def list_decisions(request: Request) -> dict[str, object]:
    """Today's decisions (Amsterdam day) for the LLM inspector list."""
    app_tz = ZoneInfo(str(get_config().get("app", "timezone", default="Europe/Amsterdam")))
    clock = getattr(request.app.state, "clock", None) or make_clock()
    today = clock.now().astimezone(app_tz).date()
    day_start = datetime.combine(today, dtime.min, tzinfo=app_tz)
    day_end = day_start + timedelta(days=1)
    start_utc = day_start.astimezone(UTC).replace(tzinfo=None)
    end_utc = day_end.astimezone(UTC).replace(tzinfo=None)

    with get_session_maker()() as session:
        rows: list[tuple[Decision, datetime]] = [
            (decision, scheduled)
            for decision, scheduled in session.execute(
                select(Decision, Cycle.scheduled_time)
                .join(Cycle, Decision.cycle_id == Cycle.id)
                .where(
                    Cycle.scheduled_time >= start_utc,
                    Cycle.scheduled_time < end_utc,
                )
                .order_by(Cycle.scheduled_time.desc(), Decision.id.desc())
            ).all()
        ]
    return {
        "summary": _decision_summary(request, rows),
        "items": [_decision_item(d, t) for d, t in rows],
    }


@router.get("/decisions/{decision_id}")
def decision_detail(decision_id: int) -> dict[str, object]:
    """One stored decision with its prompt, output, checks and raw response."""
    with get_session_maker()() as session:
        row = session.execute(
            select(Decision, Cycle.scheduled_time)
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .where(Decision.id == decision_id)
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="decision not found")
    decision, scheduled = row
    with get_session_maker()() as session:
        outcome = session.execute(
            select(Outcome).where(Outcome.decision_id == decision_id)
        ).scalar_one_or_none()
    try:
        prompt_text = load_prompt(decision.prompt_version).text
    except OSError:
        prompt_text = None
    return {
        "id": decision.id,
        "cycle_time": scheduled.replace(tzinfo=UTC).isoformat(),
        "symbol": decision.symbol,
        "context": decision.context,
        "final_action": decision.final_action,
        "input_snapshot": decision.input_snapshot,
        "rules_result": decision.rules_result,
        "model_output": decision.model_output,
        "validation_result": decision.validation_result,
        "gates_result": decision.gates_result,
        "outcome": (
            {
                "price_1h": str(outcome.price_1h) if outcome.price_1h else None,
                "price_1session": (
                    str(outcome.price_1session) if outcome.price_1session else None
                ),
                "price_5sessions": (
                    str(outcome.price_5sessions)
                    if outcome.price_5sessions
                    else None
                ),
                "best_price": (
                    str(outcome.best_price) if outcome.best_price else None
                ),
                "worst_price": (
                    str(outcome.worst_price) if outcome.worst_price else None
                ),
            }
            if outcome is not None
            else None
        ),
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
    }
