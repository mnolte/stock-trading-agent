from __future__ import annotations

import logging
import secrets
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.strategies import check_promotion, finish_promotion
from app.broker.models import OPEN_ORDER_STATUSES
from app.config import AppConfig, get_config
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import (
    Approval,
    Cycle,
    Decision,
    Earning,
    Event,
    Fill,
    LlmCall,
    Order,
    Outcome,
    Position,
    Review,
    Run,
    SettingsVersion,
    Verdict,
)
from app.decide.client import priced_models
from app.decide.prompt import PROMPTS_DIR, PromptError, load_prompt
from app.engine.executor import Executor, spy_session_moves
from app.engine.outcomes import OutcomeWorker
from app.engine.performance import compute_performance, compute_tails_payload
from app.market.calendar import CalendarProvider, WeekdayCalendarProvider
from app.market.clock import (
    NY_TZ,
    UTC,
    Clock,
    SessionClock,
    SessionState,
    make_clock,
)
from app.notifications.events import EVENT_DEFS
from app.notifications.service import (
    EMAIL_FAILURE_BANNER_AT,
    NotificationService,
)
from app.reviews.facts import verdict_eligible, verdict_facts
from app.risk.budget import Meter, risk_budget
from app.secrets_service import (
    GROUP_LABELS,
    GROUP_ORDER,
    GROUPS,
    MISSING_KEY,
    KeyTesters,
    SecretsStore,
    scrub,
)
from app.settings_service import (
    SettingsError,
    _deep_merge,
    _nested_get,
    pending_change_map,
    save_version,
    schedule_table,
    validate_settings,
    version_change_summary,
)
from app.signals.indicators import ema_series
from app.signals.service import SignalService
from app.universe.service import PoolError, UniverseService

router = APIRouter()

logger = logging.getLogger(__name__)


def _notify(request: Request, *topics: str) -> None:
    bus = getattr(request.app.state, "update_bus", None)
    if bus is not None:
        bus.publish(*topics)


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
    clock = getattr(request.app.state, "clock", None) or make_clock()
    return SessionClock(clock, calendar=_calendar(request)).state()


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


def _cycles_today(request: Request) -> int:
    """Cycles that run in today's session (fewer on early-close days)."""
    clock = getattr(request.app.state, "clock", None) or make_clock()
    session_clock = SessionClock(clock, get_config(), _calendar(request))
    today_session = _calendar(request).session_for(
        clock.now().astimezone(NY_TZ).date()
    )
    if today_session is None:
        return 0
    return len(session_clock.active_cycles(today_session))


@router.get("/session")
def session_state(request: Request) -> dict[str, object]:
    state = _session_state(request)
    return {
        "state": state.state,
        "next_cycle": state.next_cycle.isoformat() if state.next_cycle else None,
        "next_check": state.next_check.isoformat() if state.next_check else None,
        "next_open": state.next_open.isoformat() if state.next_open else None,
        "next_close": state.next_close.isoformat() if state.next_close else None,
        "display_time": state.display_time,
        "cycles_today": _cycles_today(request),
        "calendar_loaded": _calendar_loaded(request),
    }


def _iso(dt: datetime | None) -> str | None:
    """Naive stored UTC → ISO with offset."""
    return dt.replace(tzinfo=UTC).isoformat() if dt is not None else None


def _aware(dt: datetime) -> datetime:
    """An in-memory timestamp that may be naive; treated as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _fmt_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:,.2f}"


def _fmt_signed(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:+,.2f}"


def _account_snapshot(request: Request) -> dict[str, Decimal | None]:
    """Equity, cash and previous-close equity from the executor's cache."""
    state = getattr(request.app.state, "exec_state", None)
    account = getattr(state, "account", None) if state is not None else None
    if account is None:
        return {"equity": None, "cash": None, "today": None}
    return {
        "equity": account.equity,
        "cash": account.cash,
        "today": (
            account.equity - account.last_equity
            if account.last_equity
            else None
        ),
    }


def _eur(value: Decimal | None, request: Request) -> Decimal | None:
    market_data = getattr(request.app.state, "market_data", None)
    result = getattr(market_data, "latest_fx", None) if market_data else None
    rate = result.data if result is not None and result.error is None else None
    if value is None or rate is None:
        return None
    return Decimal(value / rate.usd_per_eur)


@router.get("/header")
def header_state(request: Request) -> dict[str, object]:
    """PAPER badge, session state, real equity/P&L, regime and kill switch."""
    session = _session_state(request)
    account = _account_snapshot(request)
    state = getattr(request.app.state, "exec_state", None)
    kill_on = bool(getattr(state, "kill_switch_on", False))
    return {
        "paper": True,
        "mode": get_config().env.mode,
        "session": {
            "state": session.state,
            "display_time": session.display_time,
            "now": _clock(request).now().astimezone(UTC).isoformat(),
            "next_cycle": session.next_cycle.isoformat() if session.next_cycle else None,
            "next_check": session.next_check.isoformat() if session.next_check else None,
            "next_open": session.next_open.isoformat() if session.next_open else None,
            "next_close": session.next_close.isoformat() if session.next_close else None,
            "cycles_today": _cycles_today(request),
            "calendar_loaded": _calendar_loaded(request),
        },
        "equity_usd": _fmt_money(account["equity"]),
        "equity_eur": _fmt_money(_eur(account["equity"], request)),
        "today_usd": _fmt_signed(account["today"]),
        "today_eur": _fmt_signed(_eur(account["today"], request)),
        "regime": _regime_label(request),
        "kill_switch": kill_on,
        "resume_blocked": _keys_issue(request) is not None,
        "keys_issue": _keys_issue(request),
        "paused": bool(getattr(state, "paused", False)),
        "paused_since": (
            _aware(state.paused_since).astimezone(UTC).isoformat()
            if state is not None
            and getattr(state, "paused", False)
            and state.paused_since is not None
            else None
        ),
        "eur": _eur_rate(request),
    }


@router.get("/watchlist")
def watchlist(request: Request) -> dict[str, object]:
    """Per-symbol rules status; serves the stored signal snapshot.

    ``universe_mode`` is the list cycles actually run — ``screened`` only
    once a screened-mode screen has produced an eligible list (a mode
    switch applies at the next daily screen). In screened mode the rows
    arrive in screen-rank order and the dashboard shows the configured
    ``watchlist_rows`` first.
    """
    signals = _signals(request)
    result = signals.latest if signals is not None else None
    market_data = getattr(request.app.state, "market_data", None)
    universe_mode = (
        market_data.universe_mode() if market_data is not None else "fixed"
    )
    config = get_config()
    meta: dict[str, object] = {
        "universe_mode": universe_mode,
        "watchlist_rows": int(
            config.get("universe", "watchlist_rows", default=15)
        ),
        "earnings_blackout_days": int(
            config.get("risk", "earnings_blackout_days", default=3)
        ),
    }
    if result is None:
        return {"computed_at": None, "regime": None, "rows": [], **meta}
    return {
        "computed_at": result.computed_at.isoformat(),
        "regime": {
            "trend": result.regime.trend,
            "volatility": result.regime.volatility,
            "label": result.regime.label,
            "blocks_entries": result.regime.blocks_entries,
        },
        **meta,
        "rows": [
            {
                "symbol": row.symbol,
                "price": str(row.price) if row.price is not None else None,
                "day_change_pct": (
                    float(row.day_change_pct)
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
                "warming_up": row.warming_up,
                "last_action": row.last_action,
                "last_action_time": (
                    row.last_action_time.isoformat()
                    if row.last_action_time
                    else None
                ),
                "rank": row.rank,
                "days_to_earnings": row.days_to_earnings,
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
    calls = 0
    for decision, _ in rows:
        if decision.model != "none":
            calls += 1
            cost += decision.cost or Decimal(0)
        validation = decision.validation_result or {}
        if validation.get("changed_by_code"):
            changed += 1
    return {
        "calls": calls,
        "no_calls": len(rows) - calls,
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
        "no_call_reason": decision.no_call_reason,
        "changed_by_code": bool(validation.get("changed_by_code")),
        "valid": validation.get("valid"),
    }


@router.get("/decisions")
def list_decisions(
    request: Request, cycle: str | None = None
) -> dict[str, object]:
    """Today's decisions (Amsterdam day) for the LLM inspector list.

    ``?cycle=<ISO>`` narrows the list to one cycle — the dashboard feed's
    grouped rows link here filtered to their cycle and action.
    """
    app_tz = ZoneInfo(str(get_config().get("app", "timezone", default="Europe/Amsterdam")))
    clock = getattr(request.app.state, "clock", None) or make_clock()
    today = clock.now().astimezone(app_tz).date()
    day_start = datetime.combine(today, dtime.min, tzinfo=app_tz)
    day_end = day_start + timedelta(days=1)
    start_utc = day_start.astimezone(UTC).replace(tzinfo=None)
    end_utc = day_end.astimezone(UTC).replace(tzinfo=None)

    filters = [Cycle.scheduled_time >= start_utc, Cycle.scheduled_time < end_utc]
    if cycle is not None:
        try:
            cycle_at = datetime.fromisoformat(cycle).astimezone(UTC).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(status_code=422, detail="bad cycle timestamp") from None
        filters = [Cycle.scheduled_time == cycle_at]

    with get_session_maker()() as session:
        rows: list[tuple[Decision, datetime]] = [
            (decision, scheduled)
            for decision, scheduled in session.execute(
                select(Decision, Cycle.scheduled_time)
                .join(Cycle, Decision.cycle_id == Cycle.id)
                .where(*filters)
                .order_by(Cycle.scheduled_time.desc(), Decision.id.desc())
            ).all()
        ]
    return {
        "summary": _decision_summary(request, rows),
        "items": [_decision_item(d, t) for d, t in rows],
    }


def _decision_earnings(
    session: Session, decision: Decision, scheduled: datetime
) -> dict[str, object]:
    """Earnings chip data: the next report at decision time + sessions seen."""
    snapshot = decision.input_snapshot or {}
    days = snapshot.get("days_to_earnings")
    scheduled_day = scheduled.replace(tzinfo=UTC).astimezone(NY_TZ).date()
    next_date = session.execute(
        select(func.min(Earning.date)).where(
            Earning.symbol == decision.symbol,
            Earning.date >= datetime.combine(scheduled_day, dtime.min),
        )
    ).scalar_one_or_none()
    return {
        "date": next_date.date().isoformat() if next_date is not None else None,
        "sessions": days if isinstance(days, int) else None,
        "unavailable": days == "unavailable",
    }


def _r_multiple(
    price: Decimal | None, entry: Decimal | None, stop: Decimal | None
) -> str | None:
    """Return on risk, e.g. '+0.22'; None when the inputs can't make one."""
    if price is None or entry is None or stop is None:
        return None
    risk = entry - stop
    if risk <= 0:
        return None
    return f"{(price - entry) / risk:+.2f}"


def _trade_block(
    position: Position | None,
    orders: Sequence[Order],
    fills: dict[int, Sequence[Fill]],
    approval: Approval | None,
    output: dict[str, object],
    snapshot: dict[str, object],
) -> dict[str, object]:
    """The levels the decision traded at, for the tiles and R multiples.

    Filled position first, then the order legs, then the approval or the
    model's proposed levels.
    """
    entry: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    qty: int | None = None
    status = "none"
    if position is not None:
        qty = position.qty
        entry = position.avg_entry_price
        stop = position.current_stop_price
        target = position.target_price
        status = "closed" if position.closed_at else "open"
    entry_order = next((o for o in orders if o.leg == "entry"), None)
    stop_order = next((o for o in orders if o.leg == "stop"), None)
    target_order = next((o for o in orders if o.leg == "target"), None)
    if entry is None and entry_order is not None:
        qty = qty or entry_order.qty
        order_fills = fills.get(entry_order.id, [])
        filled_qty = sum(f.qty for f in order_fills)
        if filled_qty:
            entry = (
                sum((f.price * f.qty for f in order_fills), Decimal(0))
                / filled_qty
            )
        else:
            entry = entry_order.limit_price
    if stop is None and stop_order is not None:
        stop = stop_order.stop_price or stop_order.limit_price
    if target is None and target_order is not None:
        target = target_order.limit_price or target_order.stop_price
    if qty is None and approval is not None:
        qty = approval.qty
    if entry is None and approval is not None:
        entry = approval.limit_price
    if stop is None and isinstance(output.get("stop_price"), int | float):
        stop = Decimal(str(output["stop_price"]))
    if target is None and isinstance(output.get("target_price"), int | float):
        target = Decimal(str(output["target_price"]))
    if entry is None and isinstance(snapshot.get("price"), int | float):
        entry = Decimal(str(snapshot["price"]))
    if position is None:
        status = (
            "filled"
            if entry_order is not None and entry_order.status == "filled"
            else "open"
            if entry_order is not None
            else "approval"
            if approval is not None and approval.status == "pending"
            else "none"
        )
    return {
        "qty": qty,
        "entry": str(entry) if entry is not None else None,
        "stop": str(stop) if stop is not None else None,
        "target": str(target) if target is not None else None,
        "status": status,
    }


@router.get("/decisions/{decision_id}")
def decision_detail(request: Request, decision_id: int) -> dict[str, object]:
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
        order_rows = session.execute(
            select(Order)
            .where(Order.decision_id == decision_id)
            .order_by(Order.id)
        ).scalars().all()
        fills = {
            row.id: session.execute(
                select(Fill).where(Fill.order_id == row.id)
            ).scalars().all()
            for row in order_rows
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
        verdict_row = session.execute(
            select(Verdict).where(Verdict.decision_id == decision_id)
        ).scalar_one_or_none()
        llm_call_id = session.execute(
            select(LlmCall.id).where(LlmCall.decision_id == decision_id)
        ).scalar_one_or_none()
        earnings = _decision_earnings(session, decision, scheduled)
    output = decision.model_output or {}
    snapshot = decision.input_snapshot or {}
    trade = _trade_block(
        position_row, order_rows, fills, approval_row, output, snapshot
    )
    entry = Decimal(trade["entry"]) if isinstance(trade["entry"], str) else None
    stop = Decimal(trade["stop"]) if isinstance(trade["stop"], str) else None
    try:
        prompt_text = load_prompt(decision.prompt_version).text
    except (OSError, PromptError):
        prompt_text = None
    next_due = None
    verdict_due = None
    if outcome is not None:
        worker = OutcomeWorker(
            market_data=None,
            calendar=_calendar(request),
            config=get_config(),
            clock=make_clock(),
        )
        next_due = worker.next_due(scheduled.replace(tzinfo=UTC), outcome)
        # The verdict waits for the 5-session outcome specifically; the
        # drawer chip shows the date it lands.
        if outcome.price_5sessions is None:
            for field, _at, target in worker._targets(
                scheduled.replace(tzinfo=UTC)
            ):
                if field == "price_5sessions":
                    verdict_due = target
    return {
        "id": decision.id,
        "cycle_time": scheduled.replace(tzinfo=UTC).isoformat(),
        "symbol": decision.symbol,
        "context": decision.context,
        "final_action": decision.final_action,
        "no_call_reason": decision.no_call_reason,
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
                "price_1session": (
                    str(outcome.price_1session) if outcome.price_1session else None
                ),
                "price_1session_at": _iso(outcome.price_1session_at),
                "price_5sessions": (
                    str(outcome.price_5sessions)
                    if outcome.price_5sessions
                    else None
                ),
                "price_5sessions_at": _iso(outcome.price_5sessions_at),
                "best_price": (
                    str(outcome.best_price) if outcome.best_price else None
                ),
                "best_price_at": _iso(outcome.best_price_at),
                "worst_price": (
                    str(outcome.worst_price) if outcome.worst_price else None
                ),
                "worst_price_at": _iso(outcome.worst_price_at),
                "first_touch": outcome.first_touch,
                "r_1h": _r_multiple(outcome.price_1h, entry, stop),
                "r_1session": _r_multiple(outcome.price_1session, entry, stop),
                "r_5sessions": _r_multiple(outcome.price_5sessions, entry, stop),
                "best_r": _r_multiple(outcome.best_price, entry, stop),
                "worst_r": _r_multiple(outcome.worst_price, entry, stop),
            }
            if outcome is not None
            else None
        ),
        "next_outcome_due": _iso(next_due),
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
        "verdict_eligible": verdict_eligible(decision),
        "verdict_due": _iso(verdict_due),
        "llm_call_id": llm_call_id,
        "earnings": earnings,
        "execution_mode": get_config().get(
            "execution", "mode", default="automatic"
        ),
        "eur_usd_rate": _eur_rate(request)["rate"],
        "prompt_version": decision.prompt_version,
        "prompt_hash": decision.prompt_hash,
        "prompt_text": prompt_text,
        # decision.v2: the offered levels/options and the stored checks
        "levels": decision.levels,
        "options": decision.options,
        "chosen_option": decision.chosen_option,
        "explanation_check": decision.explanation_check,
        "model": decision.model,
        "tokens_in": decision.tokens_in,
        "tokens_out": decision.tokens_out,
        "tokens": decision.tokens,
        "cost": str(decision.cost) if decision.cost is not None else None,
        "latency_ms": decision.latency_ms,
        "raw_response": decision.raw_response,
        "orders": [
            _order_json(row, fills[row.id]) for row in order_rows
        ],
        "approval": (
            {
                "id": approval_row.id,
                "status": approval_row.status,
                "reason": approval_row.reason,
                "qty": approval_row.qty,
                "limit_price": str(approval_row.limit_price),
                "created_at": approval_row.created_at.replace(
                    tzinfo=UTC
                ).isoformat(),
                "resolved_at": (
                    approval_row.resolved_at.replace(tzinfo=UTC).isoformat()
                    if approval_row.resolved_at
                    else None
                ),
            }
            if approval_row is not None
            else None
        ),
    }


# ----------------------------------------------------------------------
# Execution: kill switch, positions, orders, banners, acknowledgments


def _executor(request: Request) -> Executor:
    executor = getattr(request.app.state, "executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor unavailable")
    return executor  # type: ignore[no-any-return]


def _secrets(request: Request) -> SecretsStore | None:
    return getattr(request.app.state, "secrets_store", None)


def _notifications(request: Request) -> NotificationService | None:
    return getattr(request.app.state, "notification_service", None)


def _key_testers(request: Request) -> KeyTesters:
    testers = getattr(request.app.state, "key_testers", None)
    if testers is None:
        raise HTTPException(status_code=503, detail="key testers unavailable")
    return testers  # type: ignore[no-any-return]


def _keys_issue(request: Request) -> str | None:
    """Short text for the missing/failing-key state; None when all pass or
    in simulated mode (which needs no keys)."""
    if get_config().env.mode == "simulated":
        return None
    store = _secrets(request)
    missing = store.missing_groups() if store is not None else list(GROUP_ORDER)
    if not missing:
        return None
    parts: list[str] = []
    for group in missing:
        status = (
            store.group_status(group)["status"] if store is not None else "missing"
        )
        label = GROUP_LABELS[group]
        if status == "missing":
            parts.append(f"no {label} saved")
        elif status == "failing":
            parts.append(f"{label} failing the test")
        else:
            parts.append(f"{label} saved but untested")
    return " · ".join(parts)


@router.post("/kill-switch")
async def kill_switch(request: Request, body: dict[str, object]) -> dict[str, object]:
    """Turn the kill switch on, or stop & flatten everything."""
    from app.auth.api import require_confirmed

    executor = _executor(request)
    action = str(body.get("action", "entries"))
    if action == "flatten":
        require_confirmed(request)
        result = await executor.flatten()
        return {"kill_switch": True, **result}
    reason = str(body.get("reason") or "manual")
    await executor.activate_kill_switch(reason=reason, automatic=False)
    return {"kill_switch": True, "sold": [], "cancelled_orders": 0}


@router.post("/pause")
async def pause(request: Request) -> dict[str, object]:
    """Pause new entries; positions keep being reviewed and protected."""
    executor = _executor(request)
    await executor.pause()
    return {"paused": True}


@router.post("/pause/resume")
async def pause_resume(request: Request) -> dict[str, object]:
    """Resume entries after a pause. One click, no reason needed — but only
    when every required key is set and passing (docs/SPEC.md, API keys)."""
    executor = _executor(request)
    issue = _keys_issue(request)
    if issue is not None:
        raise HTTPException(
            status_code=422,
            detail=f"Resume needs all three keys set and passing ({issue}).",
        )
    await executor.resume_from_pause()
    return {"paused": False}


@router.post("/kill-switch/resume")
async def kill_switch_resume(
    request: Request, body: dict[str, object]
) -> dict[str, object]:
    """Resume after a kill switch; the typed reason is stored in the log.
    Same key gate as a normal resume: nothing works without them."""
    from app.auth.api import require_confirmed

    require_confirmed(request)
    executor = _executor(request)
    issue = _keys_issue(request)
    if issue is not None:
        raise HTTPException(
            status_code=422,
            detail=f"Resume needs all three keys set and passing ({issue}).",
        )
    reason = str(body.get("reason") or "")
    try:
        await executor.resume(reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"kill_switch": False}


@router.post("/positions/{position_id}/acknowledge")
def acknowledge_position(request: Request, position_id: int) -> dict[str, object]:
    executor = _executor(request)
    if not executor.acknowledge_position(position_id):
        raise HTTPException(status_code=404, detail="position not found")
    return {"acknowledged": True}


@router.post("/events/{event_id}/acknowledge")
def acknowledge_event(request: Request, event_id: int) -> dict[str, object]:
    executor = _executor(request)
    if not executor.acknowledge_event(event_id):
        raise HTTPException(status_code=404, detail="event not found")
    return {"acknowledged": True}


@router.post("/events/{event_id}/dismiss")
def dismiss_event(
    request: Request, event_id: int, body: dict[str, object]
) -> dict[str, object]:
    """Dismiss one unresolved readiness error; a note is required."""
    from app.readiness.service import ReadinessError, dismiss_error

    note = str(body.get("note") or "")
    with get_session_maker()() as session:
        try:
            event = dismiss_error(
                session, clock=_clock(request), event_id=event_id, note=note
            )
            session.commit()
            dismissed_id = event.id
        except ReadinessError as exc:
            detail = str(exc)
            if detail == "a note is required to dismiss an error":
                raise HTTPException(status_code=422, detail=detail) from exc
            raise HTTPException(status_code=409, detail=detail) from exc
    _notify(request, "banners", "readiness")
    return {"dismissed": True, "id": dismissed_id}


@router.get("/readiness")
def readiness(request: Request) -> dict[str, object]:
    """The latest stored readiness snapshot — never evaluated here."""
    from app.readiness.service import payload

    with get_session_maker()() as session:
        return payload(
            session, config=get_config(), clock=_clock(request)
        )


@router.post("/readiness/manual")
def readiness_manual(
    request: Request, body: dict[str, object]
) -> dict[str, object]:
    """Tick or untick one fixed manual item; a note is required."""
    from app.readiness.service import ReadinessError, set_manual_item

    item = str(body.get("item") or "")
    done = body.get("done")
    note = str(body.get("note") or "")
    with get_session_maker()() as session:
        try:
            row = set_manual_item(
                session,
                clock=_clock(request),
                item=item,
                done=bool(done),
                note=note,
            )
            session.commit()
            row_id = row.id
        except ReadinessError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    _notify(request, "readiness")
    return {"saved": True, "id": row_id}


def _position_json(
    position: Position, market_value: Decimal | None = None
) -> dict[str, object]:
    return {
        "id": position.id,
        "symbol": position.symbol,
        "qty": position.qty,
        "avg_entry_price": str(position.avg_entry_price),
        "market_value": str(market_value) if market_value is not None else None,
        "current_stop_price": (
            str(position.current_stop_price)
            if position.current_stop_price is not None
            else None
        ),
        "target_price": (
            str(position.target_price) if position.target_price is not None else None
        ),
        "origin": position.origin,
        "pending_ack": position.pending_ack,
        "opened_at": position.opened_at.replace(tzinfo=UTC).isoformat(),
        "closed_at": (
            position.closed_at.replace(tzinfo=UTC).isoformat()
            if position.closed_at
            else None
        ),
        "realized_pnl_raw": (
            float(position.realized_pnl_raw)
            if position.realized_pnl_raw is not None
            else None
        ),
        "exit_reason": position.exit_reason,
    }


def _order_json(row: Order, fills: Sequence[Fill]) -> dict[str, object]:
    filled_qty = sum(f.qty for f in fills)
    filled_avg = (
        sum(f.price * f.qty for f in fills) / filled_qty if filled_qty else None
    )
    return {
        "id": row.id,
        "alpaca_order_id": row.alpaca_order_id,
        "client_order_id": row.client_order_id,
        "parent_alpaca_order_id": row.parent_alpaca_order_id,
        "symbol": row.symbol,
        "leg": row.leg,
        "side": row.side,
        "type": row.order_type,
        "qty": row.qty,
        "limit_price": (
            str(row.limit_price) if row.limit_price is not None else None
        ),
        "stop_price": (
            str(row.stop_price) if row.stop_price is not None else None
        ),
        "status": row.status,
        "created_at": row.created_at.replace(tzinfo=UTC).isoformat(),
        "filled_at": (
            row.filled_at.replace(tzinfo=UTC).isoformat() if row.filled_at else None
        ),
        "filled_avg_price": str(filled_avg) if filled_avg is not None else None,
        "fills": [
            {
                "qty": f.qty,
                "price": str(f.price),
                "slippage": str(f.slippage) if f.slippage is not None else None,
                "filled_at": f.filled_at.replace(tzinfo=UTC).isoformat(),
            }
            for f in fills
        ],
    }


def _position_legs(session: Session, position: Position) -> list[dict[str, object]]:
    """Entry + open stop/target legs for one position (design: Positions)."""
    rows = session.execute(
        select(Order)
        .where(Order.symbol == position.symbol)
        .order_by(Order.id.desc())
        .limit(30)
    ).scalars().all()
    entry = next(
        (
            row
            for row in rows
            if row.leg == "entry"
            and row.created_at <= position.opened_at + timedelta(minutes=5)
            and row.status == "filled"
        ),
        None,
    )
    legs = [
        row
        for row in rows
        if row.leg in ("stop", "target") and row.status in OPEN_ORDER_STATUSES
    ]
    out: list[dict[str, object]] = []
    if entry is not None:
        fills = session.execute(
            select(Fill).where(Fill.order_id == entry.id)
        ).scalars().all()
        out.append(_order_json(entry, fills) | {"label": "Entry"})
    for leg in legs:
        fills = session.execute(
            select(Fill).where(Fill.order_id == leg.id)
        ).scalars().all()
        item = _order_json(leg, fills) | {
            "label": "Stop leg" if leg.leg == "stop" else "Target leg"
        }
        if leg.leg == "stop" and leg.parent_alpaca_order_id:
            # A replaced stop shows what it replaced (design chip text).
            replaced = session.execute(
                select(Order)
                .where(
                    Order.symbol == position.symbol,
                    Order.leg == "stop",
                    Order.status == "replaced",
                )
                .order_by(Order.id.desc())
            ).scalars().all()
            prev = next(
                (
                    r
                    for r in replaced
                    if r.id < leg.id
                    and (r.stop_price or Decimal(0)) < (leg.stop_price or Decimal(0))
                ),
                None,
            )
            if prev is not None:
                item["replaced_from"] = str(prev.stop_price)
        out.append(item)
    return out


@router.get("/positions")
async def positions(request: Request) -> dict[str, object]:
    """Open positions with legs, marks, account and day-trade boxes."""
    clock = getattr(request.app.state, "clock", None) or make_clock()
    today_ny = clock.now().astimezone(NY_TZ).date()
    market_data = getattr(request.app.state, "market_data", None)
    marks: dict[str, Decimal | None] = {}
    last_prices: dict[str, Decimal | None] = {}
    broker = getattr(request.app.state, "broker", None)
    if broker is not None:
        broker_positions = await broker.get_positions()
        if broker_positions.error is None:
            marks = {p.symbol: p.market_value for p in broker_positions.data}
            last_prices = {
                p.symbol: p.current_price for p in broker_positions.data
            }
    state = getattr(request.app.state, "exec_state", None)
    account = getattr(state, "account", None) if state is not None else None

    fx = getattr(market_data, "latest_fx", None) if market_data else None
    fx_rate = fx.data if fx is not None and fx.error is None else None
    with get_session_maker()() as session:
        open_rows = session.execute(
            select(Position).where(Position.closed_at.is_(None))
        ).scalars().all()
        legs = {p.id: _position_legs(session, p) for p in open_rows}
        earnings_next = session.execute(
            select(Earning.symbol, func.min(Earning.date))
            .where(Earning.symbol.in_([p.symbol for p in open_rows] or [""]))
            .group_by(Earning.symbol)
        ).all()
        earnings_map = {symbol: d.date() for symbol, d in earnings_next}
        closed_today = session.execute(
            select(Position)
            .where(Position.closed_at.is_not(None))
            .order_by(Position.closed_at.desc())
        ).scalars().all()
        closed_today = [
            p
            for p in closed_today
            if p.closed_at and _ny_date(p.closed_at) == today_ny
        ]
        opened_today_rows = [
            p for p in open_rows if _ny_date(p.opened_at) == today_ny
        ]
        round_trips = [
            p
            for p in closed_today
            if p.opened_at and _ny_date(p.opened_at) == today_ny
        ]
        reentry_blocked = sorted({p.symbol for p in closed_today})

    max_positions = int(
        get_config().get("risk", "max_open_positions", default=4)
    )
    positions_value = sum(
        (m for m in marks.values() if m is not None), Decimal(0)
    )

    out_open = []
    for p in open_rows:
        item = _position_json(p, marks.get(p.symbol))
        item["legs"] = legs[p.id]
        item["last_price"] = (
            str(last_prices.get(p.symbol))
            if last_prices.get(p.symbol) is not None
            else None
        )
        market_value = marks.get(p.symbol)
        if market_value is not None:
            item["unrealized_pnl"] = float(
                market_value - p.avg_entry_price * p.qty
            )
            item["unrealized_pnl_eur"] = (
                float(
                    (market_value - p.avg_entry_price * p.qty)
                    / fx_rate.usd_per_eur
                )
                if fx_rate is not None
                else None
            )
            if p.current_stop_price is not None and p.avg_entry_price > 0:
                r_dist = p.avg_entry_price - p.current_stop_price
                if r_dist <= 0:
                    item["stop_distance_label"] = "stop above entry"
                else:
                    risk_per_share = p.avg_entry_price - (
                        p.current_stop_price or Decimal(0)
                    )
                    unrealized = market_value - p.avg_entry_price * p.qty
                    item["r_multiple"] = (
                        float(
                            (unrealized / (risk_per_share * p.qty)).quantize(
                                Decimal("0.01")
                            )
                        )
                        if risk_per_share > 0
                        else None
                    )
        earnings_date = earnings_map.get(p.symbol)
        item["earnings_date"] = (
            earnings_date.isoformat() if earnings_date is not None else None
        )
        opened_today = _ny_date(p.opened_at) == today_ny
        item["opened_today"] = opened_today
        out_open.append(item)

    return {
        "open": out_open,
        "closed_today": [_position_json(p) for p in closed_today],
        "today": {
            "entries": len(opened_today_rows) + len(round_trips),
            "exits": len(closed_today),
            "round_trips": len(round_trips),
            "reentry_blocked": reentry_blocked,
        },
        "account": {
            "equity": _fmt_money(account.equity if account else None),
            "cash": _fmt_money(account.cash if account else None),
            "positions_value": _fmt_money(positions_value),
            "equity_eur": _fmt_money(
                account.equity / fx_rate.usd_per_eur
                if account and fx_rate
                else None
            ),
            "eur_usd_rate": (
                str(fx_rate.usd_per_eur) if fx_rate is not None else None
            ),
            "eur_usd_date": (
                fx_rate.for_date.isoformat() if fx_rate is not None else None
            ),
            "margin": "none · cash only",
            "account_number": account.account_number if account else None,
        },
        "reconciliation": {
            "ok": getattr(state, "last_reconcile_ok", True),
            "checked_at": (
                state.last_reconcile_at.astimezone(UTC).isoformat()
                if state is not None and state.last_reconcile_at is not None
                else None
            ),
            "stream_connected": (
                broker.stream_connected() if broker is not None else None
            ),
            "account_number": account.account_number if account else None,
        },
        "max_open_positions": max_positions,
    }


def _ny_date(dt: datetime) -> object:
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(NY_TZ).date()


def _meter_fill(meter: Meter | None) -> str:
    """Bar width in percent of the limit, capped at 100 (display only)."""
    if meter is None:
        return "0"
    if meter.limit_pct <= 0:
        return "100" if meter.used_pct > 0 else "0"
    return str(min(Decimal(100), meter.used_pct / meter.limit_pct * 100))


@router.get("/risk-budget")
async def risk_budget_state(request: Request) -> dict[str, object]:
    """The dashboard risk budget card: every limit as it stands now.

    Figures come from the same gate context and helpers the entry gates
    use — the GUI never recomputes risk (docs/SPEC.md, GUI — Risk budget
    card). The account refresh is the same call the engine makes before
    it gates, so the meters read what the gates would see right now.
    """
    executor = _executor(request)
    config = get_config()
    clock = getattr(request.app.state, "clock", None) or make_clock()
    now = clock.now()
    today_ny = now.astimezone(NY_TZ).date()
    await executor.refresh_account()
    spy_1h: Decimal | None = None
    spy_session: Decimal | None = None
    market_data = getattr(request.app.state, "market_data", None)
    if market_data is not None:
        sessions = market_data.recent_sessions(today_ny, 3)
        spy_1h, spy_session = spy_session_moves(
            market_data, sessions, now, today_ny
        )
    with get_session_maker()() as session:
        ctx = executor.budget_context(session)
    budget = risk_budget(
        ctx, config, spy_1h_pct=spy_1h, spy_session_pct=spy_session
    )
    return {
        "settings_version": config.active_version,
        "open_risk": {
            "used_pct": (
                str(budget.open_risk.used_pct)
                if budget.open_risk is not None
                else None
            ),
            "used_usd": str(budget.open_risk_usd),
            "limit_pct": str(budget.open_risk_limit_pct),
            "level": (
                budget.open_risk.level
                if budget.open_risk is not None
                else "normal"
            ),
            "fill_pct": _meter_fill(budget.open_risk),
        },
        "daily_loss": {
            "used_pct": str(budget.daily_loss.used_pct),
            "session_pnl_pct": str(budget.session_pnl_pct),
            "limit_pct": str(budget.daily_loss.limit_pct),
            "level": budget.daily_loss.level,
            "fill_pct": _meter_fill(budget.daily_loss),
        },
        "drawdown": {
            "used_pct": str(budget.drawdown.used_pct),
            "limit_pct": str(budget.drawdown.limit_pct),
            "level": budget.drawdown.level,
            "fill_pct": _meter_fill(budget.drawdown),
        },
        "room": {
            "trades": budget.room_trades,
            "limited_by": budget.room_limited_by,
        },
        "sectors": [
            {"sector": s.sector, "used": s.used, "cap": s.cap}
            for s in budget.sectors
        ],
        "risk_per_trade_pct": str(budget.risk_per_trade_pct),
        "losing_streak": {
            "count": budget.losing_streak,
            "max": budget.losing_streak_len,
            "factor": str(budget.losing_streak_factor),
        },
        "shock_brake": {
            "on": budget.shock_brake_on,
            "limit_1h_pct": str(budget.shock_1h_limit_pct),
            "limit_session_pct": str(budget.shock_session_limit_pct),
            "spy_1h_pct": (
                str(budget.spy_1h_pct) if budget.spy_1h_pct is not None else None
            ),
            "spy_session_pct": (
                str(budget.spy_session_pct)
                if budget.spy_session_pct is not None
                else None
            ),
        },
        "re_entry": {
            "allowed": bool(
                get_config().get("risk", "reentry_same_session", default=False)
            ),
        },
        "positions": {
            "open": budget.open_count,
            "cap": budget.max_positions,
        },
    }


@router.get("/orders")
def orders(request: Request) -> dict[str, object]:
    """Latest order rows with leg, fills and slippage."""
    with get_session_maker()() as session:
        rows = session.execute(
            select(Order).order_by(Order.id.desc()).limit(100)
        ).scalars().all()
        fills = {
            row.id: session.execute(
                select(Fill).where(Fill.order_id == row.id)
            ).scalars().all()
            for row in rows
        }
    return {"orders": [_order_json(row, fills[row.id]) for row in rows]}


@router.get("/banners")
def banners(request: Request) -> dict[str, object]:
    """The banner strip under the header: kill switch, mismatches, brakes."""
    state = getattr(request.app.state, "exec_state", None)
    items: list[dict[str, object]] = []
    manager = getattr(request.app.state, "shutdown", None)
    if manager is not None and manager.shutting_down.is_set():
        items.append(
            {
                "id": "shutting-down",
                "kind": "shutting_down",
                "level": "warning",
                "message": (
                    "The agent is shutting down — writes are refused; "
                    "the last steps may still be finishing."
                ),
                "details": {},
                "time": "",
                "actions": [],
            }
        )
    if getattr(request.app.state, "unclean_previous_run", False):
        integrity = getattr(request.app.state, "unclean_quick_check", "?")
        report = getattr(request.app.state, "unclean_reconcile", None)
        if report is None:
            reconcile_text = "not run"
        elif not report.ok:
            reconcile_text = "failed"
        elif report.mismatches:
            count = len(report.mismatches)
            reconcile_text = f"{count} difference{'s' if count != 1 else ''}"
        else:
            reconcile_text = "no differences"
        interrupted = int(
            getattr(request.app.state, "unclean_interrupted", 0) or 0
        )
        stopped = getattr(request.app.state, "unclean_stopped_at", None)
        when = ""
        if isinstance(stopped, datetime):
            app_tz = ZoneInfo(
                str(
                    get_config().get(
                        "app", "timezone", default="Europe/Amsterdam"
                    )
                )
            )
            local = stopped.astimezone(app_tz)
            when = f" ({local.day} {local.strftime('%b %H:%M')})"
        items.append(
            {
                "id": None,
                "kind": "unclean_run",
                "level": "warning",
                "message": (
                    f"The previous run did not shut down cleanly{when}. "
                    f"Database integrity check {integrity} · "
                    f"reconciliation with Alpaca: {reconcile_text} · "
                    f"{interrupted} "
                    f"cycle{'s' if interrupted != 1 else ''} marked "
                    "interrupted."
                ),
                "details": {
                    "quick_check": integrity,
                    "reconciliation": reconcile_text,
                    "interrupted": interrupted,
                },
                "time": "",
                "actions": ["open_logs", "dismiss"],
            }
        )
    with get_session_maker()() as session:
        unacked = session.execute(
            select(Event)
            .where(
                Event.acknowledged_at.is_(None),
                Event.event_type.in_(
                    [
                        "reconciliation",
                        "protective_stop",
                        "position_check_failed",
                        "flatten_sell_rejected",
                        "flatten_sell_blocked",
                        "universe_screen_failed",
                        "universe_screen_pending",
                        "broker_guard_unconfirmed",
                        "cycle_slow",
                        "another_instance",
                        "restart_resumed",
                        "restart_paused",
                    ]
                ),
            )
            .order_by(Event.id.desc())
            .limit(20)
        ).scalars().all()
        for event in unacked:
            details = event.details or {}
            if event.event_type == "another_instance":
                items.append(
                    {
                        "id": event.id,
                        "kind": "another_instance",
                        "level": "critical",
                        "message": str(
                            details.get("message")
                            or "Another instance is trading this Alpaca account"
                        ),
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": ["acknowledge"],
                    }
                )
            elif event.event_type == "reconciliation":
                kind = str(details.get("kind", "mismatch"))
                if kind == "adopted_position":
                    message = (
                        f"Alpaca shows {details.get('qty')} "
                        f"{event_symbol(details)} shares the agent never "
                        "opened. A protective stop was placed; new entries "
                        "are blocked until you acknowledge."
                    )
                elif kind == "qty_mismatch":
                    message = (
                        f"Alpaca shows {details.get('broker_qty')} "
                        f"{event_symbol(details)} shares; the agent recorded "
                        f"{details.get('db_qty')}. Alpaca's numbers are now "
                        "used. New entries are blocked until you acknowledge."
                    )
                else:
                    message = (
                        f"Alpaca shows an order the agent never recorded "
                        f"({event_symbol(details)}). New entries are blocked "
                        "until you acknowledge."
                    )
                items.append(
                    {
                        "id": event.id,
                        "kind": "reconciliation",
                        "level": event.level,
                        "message": message,
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "position_id": _pending_position_id(
                            session, event_symbol(details)
                        ),
                        "actions": ["review", "acknowledge"],
                    }
                )
            elif event.event_type == "protective_stop":
                items.append(
                    {
                        "id": event.id,
                        "kind": "protective_stop",
                        "level": "critical",
                        "message": (
                            f"{event_symbol(details)} had no stop order at "
                            f"Alpaca. A protective stop at "
                            f"{details.get('stop')} was placed. Check the "
                            "order history to see why the stop was missing."
                        ),
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": ["open_orders", "acknowledge"],
                    }
                )
            elif event.event_type == "flatten_sell_rejected":
                items.append(
                    {
                        "id": event.id,
                        "kind": "flatten_sell_rejected",
                        "level": "critical",
                        "message": (
                            f"Alpaca rejected the sale of "
                            f"{event_symbol(details)} during stop & flatten "
                            f"({details.get('error')}). The position keeps "
                            "its stop."
                        ),
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": ["acknowledge"],
                    }
                )
            elif event.event_type == "flatten_sell_blocked":
                items.append(
                    {
                        "id": event.id,
                        "kind": "flatten_sell_blocked",
                        "level": "critical",
                        "message": (
                            f"Stop & flatten held back the sale of "
                            f"{event_symbol(details)}: an order cancel was "
                            "never confirmed at Alpaca. The position keeps "
                            "its stop; reconcile or cancel the order before "
                            "selling."
                        ),
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": ["acknowledge"],
                    }
                )
            elif event.event_type == "restart_resumed":
                items.append(
                    {
                        "id": event.id,
                        "kind": "restart_resumed",
                        "level": "info",
                        "message": (
                            "Restarted after a clean shutdown; "
                            "reconciliation found no differences, so "
                            "trading resumed."
                        ),
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": ["acknowledge"],
                    }
                )
            elif event.event_type == "restart_paused":
                reasons = details.get("reasons")
                reason_text = (
                    "; ".join(str(r) for r in reasons)
                    if isinstance(reasons, list) and reasons
                    else "a check did not pass"
                )
                kill_switch_on = bool(
                    getattr(state, "kill_switch_on", False)
                )
                items.append(
                    {
                        "id": event.id,
                        "kind": "restart_paused",
                        "level": "warning",
                        "message": (
                            "Restarted and stayed paused: "
                            f"{reason_text}. New entries wait until you "
                            "press Resume."
                        ),
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": [
                            "open_logs",
                            "resume" if kill_switch_on else "resume_pause",
                            "acknowledge",
                        ],
                    }
                )
            elif event.event_type == "cycle_slow":
                seconds = details.get("duration_seconds")
                took = (
                    f"{seconds / 60:.0f} minutes"
                    if isinstance(seconds, int | float)
                    else "too long"
                )
                warn = details.get("warn_minutes")
                warn_text = (
                    f"{warn:g}" if isinstance(warn, int | float) else "10"
                )
                items.append(
                    {
                        "id": event.id,
                        "kind": "cycle_slow",
                        "level": "warning",
                        "message": (
                            f"A decision cycle took {took} — over the "
                            f"{warn_text}-minute warning limit. Check "
                            "its cycle row for a slow data source."
                        ),
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": ["acknowledge"],
                    }
                )
            elif event.event_type == "universe_screen_pending":
                list_date = details.get("list_date") or "none"
                items.append(
                    {
                        "id": event.id,
                        "kind": "universe_screen_pending",
                        "level": "warning",
                        "message": (
                            "Today's universe screen was missed while the "
                            "app was down and is running now. Until it "
                            f"finishes, cycles trade the {list_date} list."
                        ),
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": ["acknowledge"],
                    }
                )
            elif event.event_type == "broker_guard_unconfirmed":
                items.append(
                    {
                        "id": event.id,
                        "kind": "broker_guard_unconfirmed",
                        "level": "warning",
                        "message": (
                            "The broker-side safety guard could not be "
                            "confirmed (no_shorting, 1× margin) "
                            f"({details.get('error', 'see details')}). "
                            "Cash-only sizing and the sell cap still apply."
                        ),
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": ["acknowledge"],
                    }
                )
            elif event.event_type == "universe_screen_failed":
                items.append(
                    {
                        "id": event.id,
                        "kind": "universe_screen_failed",
                        "level": "warning",
                        "message": (
                            "Today's universe screen failed "
                            f"({details.get('error', 'unknown error')}). The "
                            "previous eligible list stays in effect."
                        ),
                        "details": details,
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": ["acknowledge"],
                    }
                )
            else:
                items.append(
                    {
                        "id": event.id,
                        "kind": event.event_type,
                        "level": "critical",
                        "message": "Position checks are failing. "
                        "Stops at Alpaca stay in place.",
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": ["acknowledge"],
                    }
                )

        # Readiness errors that touched a decision, order or fill can be
        # dismissed with a note; they also auto-resolve after the
        # configured quiet period (docs/SPEC.md, Readiness, Errors).
        from app.notifications.events import format_recorded
        from app.readiness.service import unresolved_errors

        minutes = int(
            get_config().get(
                "readiness", "error_auto_resolve_minutes", default=60
            )
        )
        open_errors = unresolved_errors(
            session,
            as_of=_clock(request).now(),
            window_start=None,
            minutes=minutes,
        )
        open_ids = {e.id for e in open_errors}
        shown_ids = {item["id"] for item in items if "id" in item}
        for item in items:
            actions = cast("list[str]", item.get("actions") or [])
            if item.get("id") in open_ids and "dismiss" not in actions:
                item["actions"] = [*actions, "dismiss"]
        for event in open_errors:
            if event.id in shown_ids:
                continue
            mapped = format_recorded(event.event_type, event.details)
            line = (
                mapped[1]
                if mapped is not None
                else event.event_type.replace("_", " ")
            )
            items.append(
                {
                    "id": event.id,
                    "kind": "unresolved_error",
                    "level": event.level,
                    "message": (
                        f"{line}. Dismiss with a note or it resolves "
                        f"itself after {minutes} minutes without recurring."
                    ),
                    "details": event.details or {},
                    "time": event.time.replace(tzinfo=UTC).isoformat(),
                    "actions": ["dismiss"],
                }
            )

    if (
        state is not None
        and getattr(state, "paused", False)
        and getattr(state, "paused_at_startup", False)
    ):
        app_tz = ZoneInfo(
            str(get_config().get("app", "timezone", default="Europe/Amsterdam"))
        )
        at = (
            _aware(state.paused_since).astimezone(app_tz).strftime("%H:%M")
            if state.paused_since is not None
            else "—"
        )
        with get_session_maker()() as session:
            open_positions = session.execute(
                select(Position).where(Position.closed_at.is_(None))
            ).scalars().all()
        # Only claim broker state after a successful reconcile: a failed
        # pass leaves the stored stops unverified (last_reconcile_ok).
        if getattr(state, "last_reconcile_at", None) is None or not getattr(
            state, "last_reconcile_ok", False
        ):
            reconciled = ""
        elif not open_positions:
            reconciled = "Reconciled with Alpaca: no open positions. "
        else:
            covered = sum(
                1 for p in open_positions if p.current_stop_price is not None
            )
            count = len(open_positions)
            detail = (
                "all with a stop"
                if covered == count
                else f"{covered} of {count} with a stop"
            )
            reconciled = (
                f"Reconciled with Alpaca: {count} "
                f"position{'s' if count != 1 else ''}, {detail}. "
            )
        items.insert(
            0,
            {
                "id": None,
                "kind": "paused",
                "level": "info",
                "since": (
                    _aware(state.paused_since).astimezone(UTC).isoformat()
                    if state.paused_since
                    else None
                ),
                "message": (
                    f"Started paused at {at}. {reconciled}Held positions "
                    "are still reviewed each cycle; no new entries until "
                    "you resume."
                ),
                "resume_blocked": _keys_issue(request) is not None,
                "actions": ["resume_pause"],
            },
        )
    keys_issue = _keys_issue(request)
    if keys_issue is not None:
        items.insert(
            0,
            {
                "id": None,
                "kind": "missing_keys",
                "level": "warning",
                "message": (
                    f"{keys_issue} — the agent stays paused until all "
                    "three keys are set and passing."
                ),
                "actions": ["open_settings"],
            },
        )
    store = _secrets(request)
    if store is not None and store.env_present:
        message = (
            "API keys were imported from .env and now live encrypted in "
            "Settings — remove them from .env."
            if store.imported
            else "API keys found in .env are ignored — keys live in "
            "Settings. Remove them from .env."
        )
        items.insert(
            0,
            {
                "id": None,
                "kind": "env_keys",
                "level": "info",
                "message": message,
                "actions": [],
            },
        )
    if state is not None and state.kill_switch_on:
        since = (
            _aware(state.kill_switch_since).astimezone(UTC).isoformat()
            if state.kill_switch_since
            else None
        )
        items.insert(
            0,
            {
                "id": None,
                "kind": "kill_switch",
                "level": "critical",
                "automatic": state.kill_switch_auto,
                "reason": state.kill_switch_reason,
                "since": since,
                "message": (
                    "No new entries. Stops and targets at Alpaca stay in place."
                ),
                "actions": ["resume", "flatten"],
            },
        )
    if state is not None and state.shock_brake_1h:
        items.append(
            {
                "id": None,
                "kind": "shock_brake",
                "level": "warning",
                "message": "Shock brake on: SPY fell hard in the last hour. "
                "No new entries for the rest of this session.",
                "actions": [],
            }
        )
    if state is not None and state.daily_loss_hit:
        items.append(
            {
                "id": None,
                "kind": "daily_loss",
                "level": "warning",
                "message": "Daily loss limit reached. "
                "No new entries until tomorrow's first cycle.",
                "actions": [],
            }
        )
    notification_service = _notifications(request)
    if (
        notification_service is not None
        and notification_service.email_failures >= EMAIL_FAILURE_BANNER_AT
    ):
        items.append(
            {
                "id": None,
                "kind": "email_failing",
                "level": "warning",
                "message": (
                    f"{notification_service.email_failures} notification "
                    "emails failed in a row. Check the SMTP details under "
                    "Settings → Notifications."
                ),
                "actions": ["open_settings"],
            }
        )
    market_data = getattr(request.app.state, "market_data", None)
    earnings_status = (
        getattr(market_data, "latest_earnings", None) if market_data else None
    )
    if earnings_status is not None and earnings_status.error is not None:
        items.append(
            {
                "id": None,
                "kind": "earnings_down",
                "level": "warning",
                "message": (
                    "Earnings calendar unavailable. New entries are blocked "
                    "on every symbol until it is back. Open positions are "
                    "unaffected."
                ),
                "actions": ["connections"],
            }
        )
    # Bars missing in the last run cycle → market data down banner.
    with get_session_maker()() as session:
        last_cycle = session.execute(
            select(Cycle)
            .where(Cycle.kind == "cycle", Cycle.status == "done")
            .order_by(Cycle.id.desc())
            .limit(1)
        ).scalar_one_or_none()
    health = (last_cycle.data_source_health or {}) if last_cycle else {}
    bars_health = str(health.get("bars", ""))
    if bars_health.startswith("failed"):
        items.append(
            {
                "id": None,
                "kind": "market_data_down",
                "level": "warning",
                "message": (
                    f"No new 5-minute bars for {bars_health[8:]}. Entries "
                    "stop until market data is back; stops at Alpaca still "
                    "protect open positions."
                ),
                "actions": [],
            }
        )
    # Day-trade guard and losing-streak info from the gate inputs.
    if state is not None and state.account is not None:
        config = get_config()
        with get_session_maker()() as session:
            closed_query = (
                select(Position)
                .where(
                    Position.closed_at.is_not(None),
                    Position.realized_pnl_raw.is_not(None),
                )
                .order_by(Position.closed_at.desc())
            )
            run_started = getattr(state, "run_started_at", None)
            if run_started is not None:
                closed_query = closed_query.where(
                    Position.closed_at >= run_started
                )
            closed = session.execute(closed_query).scalars().all()
            streak = 0
            for p in closed:
                if (p.realized_pnl_raw or Decimal(0)) < 0:
                    streak += 1
                else:
                    break
            threshold = int(config.get("risk", "losing_streak", default=3))
            if threshold > 0 and streak >= threshold:
                factor = Decimal(
                    str(
                        config.get(
                            "risk", "losing_streak_risk_factor", default=0.5
                        )
                    )
                )
                halved = Decimal(
                    str(config.get("risk", "risk_per_trade_pct", default=0.5))
                ) * factor
                items.append(
                    {
                        "id": None,
                        "kind": "losing_streak",
                        "level": "info",
                        "message": (
                            f"Risk per trade cut to {halved}% after "
                            f"{streak} losing trades in a row. Back to "
                            f"{config.get('risk', 'risk_per_trade_pct', default=0.5)}% "
                            "after the next winning trade."
                        ),
                        "actions": [],
                    }
                )
    return {"items": items}


@router.post("/banners/unclean-run/dismiss")
def dismiss_unclean_run(request: Request) -> dict[str, object]:
    """One-click Dismiss on the unclean-run banner (design: States): it
    hides for the rest of this run and returns on the next unclean stop.
    """
    request.app.state.unclean_previous_run = False
    logger.debug("unclean-run banner dismissed")
    _notify(request, "banners")
    return {"dismissed": True}


def event_symbol(details: dict[str, object]) -> str:
    return str(details.get("symbol", "?"))


def _pending_position_id(session: Session, symbol: str) -> int | None:
    row = session.execute(
        select(Position.id).where(
            Position.symbol == symbol,
            Position.closed_at.is_(None),
            Position.pending_ack.is_(True),
        )
    ).scalar_one_or_none()
    return row


# ----------------------------------------------------------------------
# Supervised-mode approvals


def _approval_json(approval: Approval, decision: Decision | None) -> dict[str, object]:
    output = (decision.model_output or {}) if decision else {}
    return {
        "id": approval.id,
        "decision_id": approval.decision_id,
        "symbol": approval.symbol,
        "qty": approval.qty,
        "limit_price": str(approval.limit_price),
        "stop_price": str(approval.stop_price),
        "target_price": str(approval.target_price),
        "status": approval.status,
        "reason": approval.reason,
        "created_at": approval.created_at.replace(tzinfo=UTC).isoformat(),
        "resolved_at": (
            approval.resolved_at.replace(tzinfo=UTC).isoformat()
            if approval.resolved_at
            else None
        ),
        "confidence": output.get("confidence"),
        "action": output.get("action"),
        "thesis": output.get("thesis"),
    }


@router.get("/approvals")
def approvals(request: Request) -> dict[str, object]:
    """Pending supervised-mode approvals plus the last resolved ones."""
    state = getattr(request.app.state, "exec_state", None)
    with get_session_maker()() as session:
        pending = session.execute(
            select(Approval).where(Approval.status == "pending")
        ).scalars().all()
        decisions = {
            row.id: row
            for row in session.execute(
                select(Decision).where(
                    Decision.id.in_([a.decision_id for a in pending] or [0])
                )
            ).scalars()
        }
        recent = session.execute(
            select(Approval)
            .where(Approval.status != "pending")
            .order_by(Approval.id.desc())
            .limit(10)
        ).scalars().all()
    # Pending approvals expire at the start of the next full cycle — the
    # card shows that time (design/screens/States.dc.html).
    next_cycle = _session_state(request).next_cycle
    return {
        "mode": get_config().get("execution", "mode", default="automatic"),
        "kill_switch_on": bool(getattr(state, "kill_switch_on", False)),
        "paused": bool(getattr(state, "paused", False)),
        "next_cycle": next_cycle.isoformat() if next_cycle else None,
        "items": [_approval_json(a, decisions.get(a.decision_id)) for a in pending],
        "recent": [_approval_json(a, None) for a in recent],
    }


@router.post("/approvals/{approval_id}/approve")
async def approve_approval(request: Request, approval_id: int) -> dict[str, object]:
    """Approve an entry: re-checks gates/code checks on a fresh quote."""
    executor = _executor(request)
    result = await executor.approve(approval_id)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("error"))
    return result


@router.post("/approvals/{approval_id}/reject")
def reject_approval(
    request: Request,
    approval_id: int,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    """Reject a pending approval; the reason is stored and shown."""
    executor = _executor(request)
    reason = str((body or {}).get("reason") or "") or None
    if not executor.reject_approval(approval_id, reason):
        raise HTTPException(status_code=404, detail="approval is not pending")
    return {"rejected": True}


# ----------------------------------------------------------------------
# Settings (versioned, effective next cycle)


def _held_symbols() -> set[str]:
    with get_session_maker()() as session:
        held = {
            row[0]
            for row in session.execute(
                select(Position.symbol).where(Position.closed_at.is_(None))
            ).all()
        }
        pending = {
            row[0]
            for row in session.execute(
                select(Order.symbol).where(
                    Order.leg == "entry",
                    Order.status.in_(OPEN_ORDER_STATUSES),
                )
            ).all()
        }
    return held | pending


def _settings_form(config: object) -> dict[str, object]:
    """Current effective values in the shape the settings form expects."""
    get = config.get  # type: ignore[attr-defined]
    return {
        "execution": {
            "mode": get("execution", "mode", default="automatic"),
            "entry_limit_offset_pct": get(
                "execution", "entry_limit_offset_pct", default=0.05
            ),
            "entry_timeout_minutes": get(
                "execution", "entry_timeout_minutes", default=10
            ),
            "max_spread_pct": get("execution", "max_spread_pct", default=0.15),
        },
        "rules": {
            "rsi_min": get("rules", "rsi_min", default=40),
            "rsi_max": get("rules", "rsi_max", default=70),
        },
        "decision": {
            "model": get("decision", "model", default="gpt-4o-mini"),
            "temperature": get("decision", "temperature", default=0),
            "prompt": get("decision", "prompt", default="decision.v1"),
            "min_confidence_enter": get(
                "decision", "min_confidence_enter", default=0.55
            ),
            "stop_atr_min": get("decision", "stop_atr_min", default=1.5),
            "stop_atr_max": get("decision", "stop_atr_max", default=4.0),
            "min_reward_risk": get("decision", "min_reward_risk", default=1.5),
            "max_calls_per_cycle": get(
                "decision", "max_calls_per_cycle", default=10
            ),
        },
        "risk": {
            "risk_per_trade_pct": get("risk", "risk_per_trade_pct", default=0.5),
            "max_position_pct": get("risk", "max_position_pct", default=20),
            "max_open_risk_pct": get("risk", "max_open_risk_pct", default=2.5),
            "max_open_positions": get("risk", "max_open_positions", default=4),
            "max_per_sector": get("risk", "max_per_sector", default=2),
            "daily_loss_limit_pct": get(
                "risk", "daily_loss_limit_pct", default=2.0
            ),
            "losing_streak": get("risk", "losing_streak", default=3),
            "shock_brake_1h_pct": get("risk", "shock_brake_1h_pct", default=1.5),
            "shock_brake_session_pct": get(
                "risk", "shock_brake_session_pct", default=2.5
            ),
            "earnings_blackout_days": get(
                "risk", "earnings_blackout_days", default=3
            ),
            "hold_through_earnings": get(
                "risk", "hold_through_earnings", default=False
            ),
            "drawdown_kill_pct": get("risk", "drawdown_kill_pct", default=8),
            "max_order_pct": get("risk", "max_order_pct", default=25),
            "max_orders_per_cycle": get(
                "risk", "max_orders_per_cycle", default=6
            ),
        },
        "performance": {
            "slippage_pct_per_side": get(
                "performance", "slippage_pct_per_side", default=0.05
            ),
            "starting_equity": get(
                "performance", "starting_equity", default=10000
            ),
        },
        "schedule": {
            "cycles": get("schedule", "cycles", default=[]),
        },
        "auth": {
            "idle_hours": get("auth", "idle_hours", default=12),
        },
        "notifications": {
            "repeat_limit_minutes": get(
                "notifications", "repeat_limit_minutes", default=30
            ),
            "quiet_hours": get(
                "notifications",
                "quiet_hours",
                default={"start": "23:00", "end": "08:00"},
            ),
            "events": get("notifications", "events", default={}),
        },
        "readiness": {
            "min_closed_trades": get(
                "readiness", "min_closed_trades", default=100
            ),
            "min_sessions": get("readiness", "min_sessions", default=60),
            "max_drawdown_pct": get(
                "readiness", "max_drawdown_pct", default=6
            ),
            "min_avg_r": get("readiness", "min_avg_r", default=0.2),
            "min_profit_factor": get(
                "readiness", "min_profit_factor", default=1.3
            ),
            "ratio_min_trades": get(
                "readiness", "ratio_min_trades", default=30
            ),
            "min_proposal_pass_pct": get(
                "readiness", "min_proposal_pass_pct", default=75
            ),
            "max_calibration_gap_pts": get(
                "readiness", "max_calibration_gap_pts", default=10
            ),
            "min_cycles_completed_pct": get(
                "readiness", "min_cycles_completed_pct", default=98
            ),
            "max_price_blocked_pct": get(
                "readiness", "max_price_blocked_pct", default=5
            ),
            "error_auto_resolve_minutes": get(
                "readiness", "error_auto_resolve_minutes", default=60
            ),
        },
        "reviews": {
            "briefing_enabled": get(
                "reviews", "briefing_enabled", default=True
            ),
            "monthly_cost_cap_usd": get(
                "reviews", "monthly_cost_cap_usd", default=2
            ),
        },
        "universe": {
            "mode": get("universe", "mode", default="fixed"),
            "symbols": get("universe", "symbols", default=[]),
            "sectors": get("universe", "sectors", default={}),
            "allowed_sectors": get("universe", "allowed_sectors", default=[]),
            "screen": {
                "min_price": get("universe", "screen", "min_price", default=10),
                "min_avg_dollar_volume": get(
                    "universe",
                    "screen",
                    "min_avg_dollar_volume",
                    default=100000000,
                ),
                "lookback_sessions": get(
                    "universe", "screen", "lookback_sessions", default=20
                ),
                "drop_after_failed_screens": get(
                    "universe",
                    "screen",
                    "drop_after_failed_screens",
                    default=3,
                ),
            },
        },
    }


def _prompt_options() -> list[dict[str, str]]:
    out = []
    for path in sorted(PROMPTS_DIR.glob("decision.*.md")):
        version = path.stem
        try:
            prompt = load_prompt(version)
        except (OSError, PromptError):
            continue
        out.append({"version": version, "hash": prompt.short_hash})
    return out


def _connections(request: Request) -> list[dict[str, object]]:
    config = get_config()
    mode = config.env.mode
    simulated = mode == "simulated"
    state = getattr(request.app.state, "exec_state", None)
    account = getattr(state, "account", None) if state is not None else None
    broker = getattr(request.app.state, "broker", None)
    market_data = getattr(request.app.state, "market_data", None)
    rows: list[dict[str, object]] = []
    account_num = account.account_number if account else None
    masked = (
        f"{account_num[:4]}…{account_num[-4:]}" if account_num else None
    )
    rows.append(
        {
            "source": "Alpaca trading",
            "status": (
                "simulated"
                if simulated
                else ("connected · paper" if account is not None else "error")
            ),
            "detail": (
                f"account {masked} · keys stored encrypted"
                if masked
                else ("fake broker" if simulated else "account unread")
            ),
        }
    )
    rows.append(
        {
            "source": "Alpaca market data",
            "status": "simulated" if simulated else "ok",
            "detail": (
                "Basic plan: IEX real-time quotes, SIP bars 15 min delayed"
                if not simulated
                else "fake bars and quotes"
            ),
        }
    )
    rows.append(
        {
            "source": "Alpaca news",
            "status": "simulated" if simulated else "ok",
            "detail": "fake news" if simulated else "latest 24 h",
        }
    )
    earnings = (
        getattr(market_data, "latest_earnings", None) if market_data else None
    )
    rows.append(
        {
            "source": "Finnhub earnings",
            "status": (
                "simulated"
                if simulated
                else ("ok" if earnings and earnings.error is None else "error")
            ),
            "detail": (
                str(earnings.error)
                if earnings is not None and earnings.error is not None
                else ("fake calendar" if simulated else "next 90 days")
            ),
        }
    )
    fx = getattr(market_data, "latest_fx", None) if market_data else None
    rows.append(
        {
            "source": "ECB EUR/USD",
            "status": (
                "simulated"
                if simulated
                else ("ok" if fx and fx.error is None else "error")
            ),
            "detail": (
                f"{fx.data.usd_per_eur} · {fx.data.for_date.isoformat()}"
                if fx is not None and fx.error is None and fx.data is not None
                else ("fake rate" if simulated else "unavailable")
            ),
        }
    )
    stream = broker.stream_connected() if broker is not None else None
    rows.append(
        {
            "source": "Trade updates stream",
            "status": (
                "connected" if stream else ("simulated" if simulated else "down")
            ),
            "detail": "fill, cancel and reject events",
        }
    )
    return rows


def _reviews_spend(request: Request) -> str | None:
    """This month's review-call spend for the cost-cap row (None without
    a review service — e.g. a bare test app)."""
    service = getattr(request.app.state, "review_service", None)
    if service is None:
        return None
    with get_session_maker()() as session:
        return str(service.monthly_spend(session))


@router.get("/settings")
def settings(request: Request) -> dict[str, object]:
    """The Settings form, schedule table, connections and version history."""
    config = get_config()
    clock = getattr(request.app.state, "clock", None) or make_clock()
    with get_session_maker()() as session:
        rows = session.execute(
            select(SettingsVersion).order_by(SettingsVersion.version.desc()).limit(20)
        ).scalars().all()
    history = []
    for index, row in enumerate(rows):
        prev = rows[index + 1].settings if index + 1 < len(rows) else None
        history.append(
            {
                "version": row.version,
                "changed_at": row.changed_at.replace(tzinfo=UTC).isoformat(),
                "change": version_change_summary(prev, row.settings),
                "reason": row.reason,
            }
        )
    pending_tree = config.pending()
    active_tree = config.effective()
    return {
        "settings": _settings_form(config),
        "active_version": config.active_version,
        "pending_version": config.pending_version,
        "pending": pending_change_map(active_tree, pending_tree)
        if pending_tree is not None
        else {},
        "pending_change": version_change_summary(active_tree, pending_tree)
        if pending_tree is not None
        else None,
        "next_version": (rows[0].version + 1) if rows else 1,
        "held_symbols": sorted(_held_symbols()),
        "prompt_options": _prompt_options(),
        # Only models with a price may be selected (docs/SPEC.md, Cost).
        "priced_models": priced_models(config),
        "schedule": schedule_table(config, clock, _calendar(request)),
        "connections": _connections(request),
        "history": history,
        "reviews_spend_usd": _reviews_spend(request),
        "notification_events": [
            {
                "key": d.key,
                "label": d.label,
                "description": d.description,
                "group": d.group,
                "severity": d.severity,
            }
            for d in EVENT_DEFS
        ],
        "smtp": (
            _notifications(request).smtp_status()  # type: ignore[union-attr]
            if _notifications(request) is not None
            else None
        ),
        "mode": config.env.mode,
    }


@router.post("/settings")
async def update_settings(request: Request, body: dict[str, object]) -> dict[str, object]:
    """Validate and save a new settings version; applies at the next cycle."""
    config = get_config()
    submitted = body.get("settings")
    if not isinstance(submitted, dict):
        raise HTTPException(status_code=422, detail="settings object required")
    reason = str(body.get("reason") or "")
    broker = getattr(request.app.state, "broker", None)
    if broker is None:
        raise HTTPException(status_code=503, detail="broker unavailable")
    # A still-pending save is the base for the next one — merging onto
    # ``active`` would drop its changes from the new version's tree.
    active = config.effective()
    base = config.pending() or active
    try:
        cleaned, changes = await validate_settings(
            submitted,
            base,
            broker=broker,
            held_symbols=_held_symbols(),
        )
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    if not reason.strip():
        # Required and shown in History (docs/SPEC.md, Settings screen).
        raise HTTPException(
            status_code=422, detail=["a reason is required"]
        )
    # "Promote to live" saves like any other change, then archives the
    # shadow (docs/SPEC.md, Strategies and shadows — Promote to live).
    # Promoting is a risky action: it needs a fresh "Confirm it is you".
    promote_raw = body.get("promote_strategy_id")
    promote_id = int(promote_raw) if isinstance(promote_raw, int | str) and str(
        promote_raw
    ).isdigit() else None
    # Promote, and loosening any risk limit, need "Confirm it is you";
    # tightening a limit never asks (design, Settings → Access).
    loosens = _settings_loosen_risk(cleaned, base)
    # Changing the heartbeat URL is a confirm action too (design, Access).
    new_hb = (cleaned.get("ops") or {}).get("heartbeat_url")
    hb_changed = (
        new_hb is not None
        and new_hb != _nested_get(base, "ops", "heartbeat_url")
    )
    if promote_id is not None or loosens or hb_changed:
        from app.auth.api import require_confirmed

        require_confirmed(request)
    new_tree = _deep_merge(base, cleaned)
    with get_session_maker()() as session:
        if promote_id is not None:
            check_promotion(session, config, promote_id)
        row = save_version(
            session, _clock(request), settings=new_tree, reason=reason
        )
        version = row.version
        if promote_id is not None:
            finish_promotion(session, promote_id, version, _clock(request).now())
        session.commit()
    # The pending version must be the full merged tree, same as the stored
    # row — passing only ``cleaned`` would silently revert every earlier
    # saved change at the next cycle's promotion.
    config.set_pending(new_tree, version)
    schedule = cleaned.get("schedule")
    if isinstance(schedule, dict) and "cycles" in schedule and (
        schedule["cycles"] != _nested_get(base, "schedule", "cycles")
    ):
        config.reschedule_cycles = True
    _notify(request, "header", "settings", "strategies")
    return {
        "version": version,
        "changes": changes,
        "effective_from": "next cycle",
    }


def _clock(request: Request) -> Clock:
    clock = getattr(request.app.state, "clock", None)
    return clock if clock is not None else make_clock()


def _settings_loosen_risk(
    submitted: dict[str, object], base: dict[str, object]
) -> bool:
    """True when a numeric risk limit goes up — loosening asks for a
    fresh confirmation; tightening never does (design, Access)."""
    new_risk = submitted.get("risk")
    if not isinstance(new_risk, dict):
        return False
    old_risk = _nested_get(base, "risk") or {}
    for key, value in new_risk.items():
        old = old_risk.get(key) if isinstance(old_risk, dict) else None
        try:
            if value is not None and old is not None and float(value) > float(old):
                return True
        except (TypeError, ValueError):
            continue
    return False


# ----------------------------------------------------------------------
# Notifications (docs/SPEC.md, Notifications)


@router.get("/notifications")
def notifications(
    request: Request, after_id: int = 0, limit: int = 20
) -> dict[str, object]:
    """Recent notifications, newest first, for the toasts and the
    'Recently sent' list on the Settings page."""
    service = _notifications(request)
    if service is None:
        return {"items": [], "latest_id": after_id}
    return service.recent(after_id=after_id, limit=limit)


@router.post("/notifications/test")
async def notification_test(request: Request) -> dict[str, object]:
    """'Send test': one notification on every enabled channel."""
    service = _notifications(request)
    if service is None:
        raise HTTPException(status_code=503, detail="notifications unavailable")
    return await service.send_test()


@router.post("/notifications/smtp")
async def save_smtp(
    request: Request, body: dict[str, object]
) -> dict[str, object]:
    """Save the SMTP details — only after a real test send succeeded.
    The password (if any) goes to the encrypted secrets table."""
    service = _notifications(request)
    if service is None:
        raise HTTPException(status_code=503, detail="notifications unavailable")
    host = str(body.get("host") or "").strip()
    username = str(body.get("username") or "").strip()
    from_addr = str(body.get("from_addr") or "").strip()
    to_addr = str(body.get("to_addr") or "").strip()
    raw_password = body.get("password")
    password = str(raw_password) if raw_password else None
    errors: list[str] = []
    if not host:
        errors.append("SMTP host is required")
    if not to_addr:
        errors.append("Send to is required")
    try:
        port = int(str(body.get("port") or 587))
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        errors.append("SMTP port must be 1–65535")
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    # Changing where the stored password is sent (host, port, username)
    # or where alerts go (To) needs "Confirm it is you" (design, Access).
    from app.auth.api import require_confirmed

    current = service.smtp_status()
    if (
        current.get("host") != host
        or current.get("port") != port
        or current.get("username") != username
        or current.get("to_addr") != to_addr
    ) and current.get("configured"):
        require_confirmed(request)
    result = await service.save_smtp(
        host=host,
        port=port,
        username=username,
        from_addr=from_addr,
        to_addr=to_addr,
        password=password,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=422,
            detail=[f"test send failed: {result.get('error')}"],
        )
    return {"ok": True, "smtp": service.smtp_status()}


def _open_items() -> int:
    """Open positions plus open orders — the Alpaca-replace guard count."""
    with get_session_maker()() as session:
        positions = session.execute(
            select(func.count(Position.id)).where(Position.closed_at.is_(None))
        ).scalar_one()
        open_orders = session.execute(
            select(func.count(Order.id)).where(
                Order.status.in_(OPEN_ORDER_STATUSES)
            )
        ).scalar_one()
    return positions + open_orders


def _data_source_tiles(request: Request) -> list[dict[str, object]]:
    """The design's four data-source tiles for the keys section."""
    wanted = {
        "Alpaca market data",
        "Alpaca news",
        "Finnhub earnings",
        "ECB EUR/USD",
    }
    return [row for row in _connections(request) if row["source"] in wanted]


_KEY_ROW_INFO: dict[str, dict[str, str]] = {
    "alpaca": {
        "label": "Alpaca paper keys",
        "hint": "Orders, positions, market data and news",
    },
    "openai": {"label": "OpenAI", "hint": "Trading decisions and reviews"},
    "finnhub": {"label": "Finnhub", "hint": "Earnings calendar (free plan)"},
}


@router.get("/keys")
def api_keys(request: Request) -> dict[str, object]:
    """Key metadata for Settings: status, mask and timestamps — never a key."""
    store = _secrets(request)
    open_items = _open_items()
    state = getattr(request.app.state, "exec_state", None)
    paused_or_kill = bool(
        state is not None
        and (getattr(state, "paused", False) or getattr(state, "kill_switch_on", False))
    )
    replace_allowed = paused_or_kill and open_items == 0
    rows = []
    for group in GROUP_ORDER:
        status = store.group_status(group) if store is not None else {
            "name": group,
            "set": False,
            "status": "missing",
            "masked": None,
            "account": None,
            "saved_at": None,
            "tested_at": None,
            "error": None,
        }
        rows.append({**_KEY_ROW_INFO[group], **status})
    return {
        "keys": rows,
        "set_count": sum(1 for row in rows if row["set"]),
        "all_ready": store.all_ready() if store is not None else False,
        "open_items": open_items,
        "paused_or_kill": paused_or_kill,
        "replace_allowed": replace_allowed,
        "replace_hint": (
            "Replacing needs the agent paused with no open positions or "
            f"orders ({open_items} open). Only paper keys are accepted."
        ),
        "env_keys": bool(store is not None and store.env_present),
        "mode": get_config().env.mode,
        "data_sources": _data_source_tiles(request),
    }


async def _apply_keys(request: Request, changed: set[str]) -> None:
    """Point the live clients at newly saved keys — in place, so every
    service holding the client object keeps working."""
    store = _secrets(request)
    if store is None:
        return
    config = get_config()
    sources = getattr(request.app.state, "data_sources", None)
    if "alpaca" in changed:
        key_id, secret = store.alpaca_pair()
        if sources is not None and sources.alpaca is not None:
            sources.alpaca.update_credentials(
                key_id or MISSING_KEY, secret or MISSING_KEY
            )
        from app.broker.alpaca import AlpacaPaperBroker

        broker = getattr(request.app.state, "broker", None)
        if isinstance(broker, AlpacaPaperBroker):
            await broker.update_credentials(
                key_id or MISSING_KEY,
                secret or MISSING_KEY,
                keyed=bool(key_id and secret),
            )
        executor = getattr(request.app.state, "executor", None)
        if executor is not None:
            await executor.refresh_account()
    if "finnhub" in changed and sources is not None:
        update_key = getattr(sources.earnings, "update_key", None)
        if callable(update_key):
            update_key(store.get("finnhub") or MISSING_KEY)
    if "openai" in changed and config.env.mode != "simulated":
        key = store.get("openai")
        if key:
            from app.reviews.client import OpenAIReviewClient

            runner = getattr(request.app.state, "cycle_runner", None)
            decide = getattr(runner, "decide", None)
            if decide is not None:
                decide.api_key = key
                update_key = getattr(decide.client, "update_key", None)
                if callable(update_key):
                    update_key(key)
                else:
                    decide.reload()
            service = getattr(request.app.state, "review_service", None)
            review_client = getattr(service, "client", None)
            if service is not None:
                update_key = getattr(review_client, "update_key", None)
                if callable(update_key):
                    update_key(key)
                else:
                    service.client = OpenAIReviewClient(config, api_key=key)
            # Shadow clients are rebuilt lazily with the new stored key.
            shadows = getattr(request.app.state, "shadow_runner", None)
            if shadows is not None:
                shadows.reset_clients()


@router.post("/keys/{group}")
async def save_api_key(
    request: Request, group: str, body: dict[str, object]
) -> dict[str, object]:
    """Test a candidate key, then save it only when the test passes —
    the old key keeps working until then. Alpaca has extra guards."""
    from app.auth.api import require_confirmed

    require_confirmed(request)  # replacing a key is a risky action
    store = _secrets(request)
    if store is None:
        raise HTTPException(status_code=503, detail="key store unavailable")
    if group not in GROUPS:
        raise HTTPException(status_code=404, detail="unknown key")
    testers = _key_testers(request)

    if group == "alpaca":
        key_id = str(body.get("key_id") or "").strip()
        secret = str(body.get("secret") or "").strip()
        if not key_id or not secret:
            raise HTTPException(
                status_code=422,
                detail="the Alpaca key id and secret are both required",
            )
        state = getattr(request.app.state, "exec_state", None)
        paused_or_kill = bool(
            state is not None
            and (
                getattr(state, "paused", False)
                or getattr(state, "kill_switch_on", False)
            )
        )
        if not paused_or_kill:
            raise HTTPException(
                status_code=422,
                detail="pause the agent or turn the kill switch on before "
                "replacing the Alpaca keys",
            )
        open_items = _open_items()
        if open_items:
            raise HTTPException(
                status_code=422,
                detail=f"close all positions and orders first ({open_items} open)",
            )
        result = await testers.alpaca(key_id, secret)
        if not result.ok:
            # The candidate was never stored, so the store's scrub list
            # doesn't cover it — strip it here before it can reach the
            # event log or the response.
            clean = scrub(result.error or "", [key_id, secret])
            store.record_event(
                "alpaca_key_id", "tested", False, clean, last4=key_id[-4:]
            )
            raise HTTPException(status_code=422, detail=clean or "test failed")
        old_account = getattr(
            getattr(state, "account", None) if state is not None else None,
            "account_number",
            None,
        )
        account_changed = bool(
            result.account and old_account and result.account != old_account
        )
        for name, value in (
            ("alpaca_key_id", key_id),
            ("alpaca_secret", secret),
        ):
            store.save(name, value, "ok", result.detail)
            store.record_event(name, "saved", True, result.detail)
        await _apply_keys(request, {"alpaca"})
        if account_changed:
            executor = _executor(request)
            await executor.activate_kill_switch(
                reason=(
                    "Alpaca keys point at a different paper account — "
                    "start a new run"
                ),
                automatic=True,
            )
        _notify(request, "banners", "header", "settings")
        return {
            "ok": True,
            "account_changed": account_changed,
            "key": store.group_status("alpaca"),
        }

    value = str(body.get("value") or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="a key is required")
    result = await (
        testers.openai(value) if group == "openai" else testers.finnhub(value)
    )
    if not result.ok:
        clean = scrub(result.error or "", [value])
        store.record_event(group, "tested", False, clean, last4=value[-4:])
        raise HTTPException(status_code=422, detail=clean or "test failed")
    store.save(group, value, "ok", result.detail)
    store.record_event(group, "saved", True, result.detail)
    await _apply_keys(request, {group})
    _notify(request, "banners", "header", "settings")
    return {"ok": True, "key": store.group_status(group)}


@router.post("/keys/{group}/test")
async def test_api_key(request: Request, group: str) -> dict[str, object]:
    """The separate Test action: re-checks the stored key and updates its
    result — the value is never returned."""
    store = _secrets(request)
    if store is None:
        raise HTTPException(status_code=503, detail="key store unavailable")
    if group not in GROUPS:
        raise HTTPException(status_code=404, detail="unknown key")
    result = await store.retest_stored(group, _key_testers(request))
    if result is None:
        raise HTTPException(
            status_code=422, detail=f"no {GROUP_LABELS[group]} saved yet"
        )
    _notify(request, "banners", "header", "settings")
    return {
        "ok": result.ok,
        "error": scrub(result.error or "", store.scrub_values()) or None,
        "key": store.group_status(group),
    }





# ----------------------------------------------------------------------
# Paper account reset + runs


@router.post("/paper-reset")
async def paper_reset(request: Request) -> dict[str, object]:
    """Step 1: kill switch on, close all positions, cancel all orders."""
    from app.auth.api import require_confirmed

    require_confirmed(request)
    executor = _executor(request)
    return await executor.paper_reset()


@router.post("/runs")
async def start_new_run(request: Request) -> dict[str, object]:
    """Step 2: verify the reset balance, then start a new performance run."""
    from app.auth.api import require_confirmed

    require_confirmed(request)
    executor = _executor(request)
    result = await executor.start_new_run()
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("failed"))
    return result


@router.get("/runs")
def list_runs(request: Request) -> dict[str, object]:
    """All performance runs, newest first (earlier runs stay viewable)."""
    with get_session_maker()() as session:
        rows = session.execute(
            select(Run).order_by(Run.id.desc())
        ).scalars().all()
    return {
        "runs": [
            {
                "id": row.id,
                "started_at": row.started_at.replace(tzinfo=UTC).isoformat(),
                "starting_equity": str(row.starting_equity),
                "spy_close": (
                    str(row.spy_close) if row.spy_close is not None else None
                ),
            }
            for row in rows
        ]
    }


@router.get("/performance")
def performance(request: Request, run: int | None = None) -> dict[str, object]:
    """Performance screen: equity vs SPY, outcomes, closed trades.

    ``run`` selects an earlier run; the default is the current one.
    """
    config = get_config()
    clock = _clock(request)
    market_data = getattr(request.app.state, "market_data", None)
    state = getattr(request.app.state, "exec_state", None)
    live_equity = (
        state.account.equity
        if state is not None and state.account is not None
        else None
    )
    with get_session_maker()() as session:
        payload = compute_performance(
            session,
            config=config,
            market_data=market_data,
            calendar=_calendar(request),
            clock=clock,
            live_equity=live_equity if run is None else None,
            run_id=run,
        )
    if payload.get("empty") and run is not None:
        raise HTTPException(status_code=404, detail="run not found")
    return payload


@router.get("/performance/tails")
def performance_tails(request: Request, run: int | None = None) -> dict[str, object]:
    """Winners and tails numbers for one run (docs/SPEC.md, Tail metrics).

    ``run`` selects an earlier run; the default is the current one. All
    values are numbers (decimal strings), never formatted text.
    """
    config = get_config()
    with get_session_maker()() as session:
        payload = compute_tails_payload(session, config=config, run_id=run)
    if payload.get("empty") and run is not None:
        raise HTTPException(status_code=404, detail="run not found")
    return payload


# ----------------------------------------------------------------------
# Dashboard + chart


def _f(value: object) -> float | None:
    """JSON number or None; 'unavailable' strings read as None."""
    return value if isinstance(value, int | float) else None


def _fmt_price2(value: object) -> str:
    f = _f(value)
    return f"{f:.2f}" if f is not None else "—"


def _sig_chips(
    snapshot: dict[str, object], config: AppConfig
) -> list[dict[str, object]]:
    """Signal chips under the card's reasoning line (design: Main)."""
    chips: list[dict[str, object]] = []
    ind1h = cast("dict[str, object]", snapshot.get("indicators_1h") or {})
    daily = cast("dict[str, object]", snapshot.get("indicators_daily") or {})

    rsi = _f(ind1h.get("rsi14"))
    if rsi is not None:
        rsi_min = float(config.get("rules", "rsi_min", default=40))
        rsi_max = float(config.get("rules", "rsi_max", default=70))
        chips.append(
            {
                "text": f"RSI {rsi:.0f}",
                "tone": -1 if rsi < rsi_min or rsi > rsi_max else 0,
            }
        )

    price, vwap = _f(snapshot.get("price")), _f(ind1h.get("vwap"))
    if price is not None and vwap:
        rel = (price - vwap) / vwap * 100
        chips.append(
            {
                "text": (
                    "> VWAP" if rel > 0.05 else "< VWAP" if rel < -0.05 else "≈ VWAP"
                ),
                "tone": 1 if rel > 0.05 else -1 if rel < -0.05 else 0,
            }
        )

    ema20, ema50 = _f(ind1h.get("ema20")), _f(ind1h.get("ema50"))
    if ema20 is not None and ema50 is not None:
        chips.append(
            {
                "text": "EMA 20 > 50" if ema20 > ema50 else "EMA 20 < 50",
                "tone": 1 if ema20 > ema50 else -1,
            }
        )

    close, sma50 = _f(daily.get("close")), _f(daily.get("sma50"))
    if close is not None and sma50 is not None:
        chips.append(
            {
                "text": "Daily > SMA 50" if close > sma50 else "Daily < SMA 50",
                "tone": 1 if close > sma50 else -1,
            }
        )

    obv = ind1h.get("obv_trend")
    if obv in ("up", "down", "flat"):
        chips.append(
            {
                "text": {"up": "OBV ↑", "down": "OBV ↓", "flat": "OBV flat"}[obv],
                "tone": {"up": 1, "down": -1, "flat": 0}[obv],
            }
        )

    rs = _f(ind1h.get("rs_vs_spy_pct"))
    if rs is not None:
        chips.append(
            {
                "text": f"RS vs SPY {rs:+.1f}%",
                "tone": 1 if rs > 0 else -1 if rs < 0 else 0,
            }
        )

    gap = _f(daily.get("gap_pct"))
    if gap is not None:
        chips.append({"text": f"Gap {gap:+.1f}%", "tone": 0})
    return chips


def _box_row(label: str, value: str, tone: int = 0) -> dict[str, object]:
    return {"label": label, "value": value, "tone": tone}


def _check_row(check: dict[str, object]) -> dict[str, object]:
    """One check → a summary-box line (name / value with a pass mark)."""
    passed = bool(check.get("passed"))
    value = check.get("value") or check.get("detail") or "—"
    return _box_row(
        str(check.get("name")),
        f"{value} {'✓' if passed else '✗'}",
        1 if passed else -1,
    )


def _latest_box(
    decision: Decision,
    snapshot: dict[str, object],
    session_state: SessionState,
    app_tz: ZoneInfo,
) -> list[dict[str, object]]:
    """The card's three-line summary box (design: skip / held / no-call)."""
    output = decision.model_output or {}
    validation = decision.validation_result or {}
    rules = decision.rules_result or {}

    if decision.final_action == "no_call":
        reason = decision.no_call_reason or "rules"
        rule_checks = cast(
            "list[dict[str, object]]", rules.get("checks") or []
        )
        failed = next((c for c in rule_checks if not c.get("passed")), None)
        if reason == "rules":
            label, tone = "Pre-filter", -1
            detail = str(
                (failed or {}).get("detail") or rules.get("reason") or "failed"
            )
        elif reason == "call_cap":
            label, tone, detail = "Call cap", 0, "reached"
        elif reason == "price_stale":
            label, tone, detail = "Price", -1, "stale"
        elif reason == "price_suspect":
            label, tone, detail = "Price", -1, "suspect"
        else:
            label, tone = "Model call", -1
            detail = str(validation.get("error") or "failed")
        nxt = session_state.next_cycle
        look = (
            f"{nxt.astimezone(app_tz).strftime('%H:%M')} cycle"
            if nxt is not None
            else "next session"
        )
        return [
            _box_row(label, detail, tone),
            _box_row("Model call", "none this cycle"),
            _box_row("Next look", look),
        ]

    if decision.context == "held":
        pos = cast("dict[str, object]", snapshot.get("position") or {})
        rows: list[dict[str, object]] = []
        if pos:
            r = _f(pos.get("r_so_far"))
            value = f"{pos.get('qty')} sh @ {_fmt_price2(pos.get('entry'))}"
            if r is not None:
                value += f" · {r:+.2f}R"
            rows.append(_box_row("Position", value, 1 if (r or 0) >= 0 else -1))
            rows.append(
                _box_row(
                    "Stop / target",
                    f"{_fmt_price2(pos.get('stop'))} / "
                    f"{_fmt_price2(pos.get('target'))}",
                )
            )
        else:
            rows += [
                _box_row("Position", "—"),
                _box_row("Stop / target", "—"),
            ]
        rows.append(_box_row("Agent exit", "now"))
        return rows[:3]

    # Candidate decisions: failed check first, then a pass, then the
    # proposed stop and target.
    checks = cast("list[dict[str, object]]", validation.get("checks") or [])
    gates = decision.gates_result or {}
    gate_checks = cast(
        "list[dict[str, object]]", gates.get("checks") or []
    )
    ordered = [c for c in checks + gate_checks if not c.get("passed")] + [
        c for c in checks + gate_checks if c.get("passed")
    ]
    rows = [_check_row(c) for c in ordered[:2]]
    while len(rows) < 2:
        rows.append(
            _box_row(
                "Model action",
                str(validation.get("model_action") or decision.final_action),
            )
        )
    stop = _fmt_price2(output.get("stop_price"))
    target = _fmt_price2(output.get("target_price"))
    label = (
        "Stop / target"
        if decision.final_action == "enter"
        else "Proposed stop / target"
    )
    rows.append(_box_row(label, f"{stop} / {target}"))
    return rows[:3]


def _briefing_chips(facts: dict[str, Any]) -> list[dict[str, str]]:
    """The fact chips on the dashboard briefing card (design Main:
    position count, yesterday's result, market regime, earnings inside a
    day, banner count). The text is built here so the GUI only renders."""
    chips: list[dict[str, str]] = []
    positions = facts.get("positions") or []
    n = len(positions)
    chips.append(
        {
            "text": (
                f"{n} position{'s' if n != 1 else ''}" if n else "no positions"
            ),
            "tone": "neutral",
        }
    )
    yesterday = facts.get("yesterday") or {}
    pct = yesterday.get("return_pct")
    usd = yesterday.get("return_usd")
    if pct is None:
        chips.append({"text": "yesterday —", "tone": "neutral"})
    else:
        text = f"yesterday {pct:+.2f}%"
        if usd is not None:
            text += f" · {'+' if usd >= 0 else '−'}${abs(usd):,.2f}"
        tone = "positive" if pct > 0 else "negative" if pct < 0 else "neutral"
        chips.append({"text": text, "tone": tone})
    regime = str(facts.get("regime") or "unavailable").lower()
    chips.append(
        {
            "text": f"market {regime}",
            "tone": (
                "positive"
                if regime.startswith("up")
                else "negative"
                if regime.startswith("down")
                else "neutral"
            ),
        }
    )
    earnings = [
        *(f"{e['symbol']} today" for e in facts.get("earnings_today") or []),
        *(
            f"{e['symbol']} tomorrow"
            for e in facts.get("earnings_tomorrow") or []
        ),
    ]
    chips.append(
        {
            "text": (
                "earnings " + " · ".join(earnings)
                if earnings
                else "no earnings ≤ 1 day"
            ),
            "tone": "neutral",
        }
    )
    banners = facts.get("banners") or []
    chips.append(
        {
            "text": (
                f"{len(banners)} banner{'s' if len(banners) != 1 else ''}"
                if banners
                else "no banners"
            ),
            "tone": "warning" if banners else "neutral",
        }
    )
    return chips


def _briefing_card(
    request: Request, clock: Clock, app_tz: ZoneInfo, today_ny: date
) -> dict[str, Any]:
    """The daily briefing card on the dashboard (docs/SPEC.md, GUI —
    Daily briefing card). Shows the latest written briefing — today's
    once its slot has passed, the previous one before that — plus when
    the next one is written, or "off" with a Settings link."""
    config = get_config()
    service = getattr(request.app.state, "review_service", None)
    enabled = bool(config.get("reviews", "briefing_enabled", default=True))
    next_at = (
        service.next_briefing_at() if service is not None and enabled else None
    )
    card: dict[str, Any] | None = None
    with get_session_maker()() as session:
        review = session.execute(
            select(Review)
            .where(Review.kind == "briefing")
            .order_by(Review.due_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    if review is not None:
        due = review.due_at.replace(tzinfo=UTC)
        card = {
            "id": review.id,
            "label": _review_label(review),
            "today": review.period == today_ny.isoformat(),
            "time": due.astimezone(app_tz).strftime("%H:%M"),
            "text": (review.content or {}).get("text") or review.text,
            "chips": _briefing_chips(review.facts or {}),
            "source": review.source,
            "reason": review.reason,
            "model": review.model,
            "prompt_version": review.prompt_version,
            "cost": str(review.cost) if review.cost is not None else None,
        }
    return {
        "enabled": enabled,
        "next_at": next_at.isoformat() if next_at is not None else None,
        "card": card,
    }


@router.get("/dashboard")
def dashboard(request: Request, symbol: str | None = None) -> dict[str, object]:
    """The Main screen: cycle line, stat cards, latest decision, feed."""
    config = get_config()
    clock = getattr(request.app.state, "clock", None) or make_clock()
    state = getattr(request.app.state, "exec_state", None)
    account = getattr(state, "account", None) if state is not None else None
    session_state = _session_state(request)
    app_tz = ZoneInfo(
        str(config.get("app", "timezone", default="Europe/Amsterdam"))
    )
    calendar = _calendar(request)
    today_ny = clock.now().astimezone(NY_TZ).date()
    today = clock.now().astimezone(app_tz).date()
    # "Today" on the dashboard is the current New York session or, outside
    # a session day, the last completed one — never the Amsterdam calendar
    # day (docs/SPEC.md, GUI — Dashboard at scale and off-hours).
    shown_day = calendar.session_for(today_ny)
    if shown_day is None:
        shown_day = calendar.previous_trading_day(today_ny)
    shown_date = shown_day.date if shown_day is not None else today_ny
    shown_is_today = shown_date == today_ny
    shown_start_utc = (
        datetime.combine(shown_date, dtime.min, tzinfo=NY_TZ)
        .astimezone(UTC)
        .replace(tzinfo=None)
    )
    shown_end_utc = (
        datetime.combine(shown_date, dtime.max, tzinfo=NY_TZ)
        .astimezone(UTC)
        .replace(tzinfo=None)
    )

    with get_session_maker()() as session:
        cycles = session.execute(
            select(Cycle)
            .where(Cycle.kind == "cycle")
            .order_by(Cycle.scheduled_time.desc())
            .limit(10)
        ).scalars().all()
        last_cycle = cycles[0] if cycles else None
        shown_decisions = (
            session.execute(
                select(Decision, Cycle.scheduled_time)
                .join(Cycle, Decision.cycle_id == Cycle.id)
                .where(Cycle.scheduled_time >= shown_start_utc)
                .where(Cycle.scheduled_time <= shown_end_utc)
                .order_by(Decision.id.desc())
            ).all()
            if last_cycle is not None
            else []
        )
        cycle_decisions = (
            session.execute(
                select(Decision).where(Decision.cycle_id == last_cycle.id)
            ).scalars().all()
            if last_cycle is not None
            else []
        )
        open_rows = session.execute(
            select(Position).where(Position.closed_at.is_(None))
        ).scalars().all()
        pending = session.execute(
            select(Approval).where(Approval.status == "pending")
        ).scalars().all()
        run = session.execute(
            select(Run).order_by(Run.id.desc()).limit(1)
        ).scalar_one_or_none()
        # The counter's "in this run" number: cycles since the run started
        # (docs/SPEC.md, GUI — Cycle counter).
        run_cycle_number = (
            session.execute(
                select(func.count(Cycle.id)).where(
                    Cycle.kind == "cycle",
                    Cycle.scheduled_time >= run.started_at,
                )
            ).scalar_one()
            if run is not None
            else None
        )
        # Latest decision for the card: the shown session's newest for the
        # selected symbol (or the newest overall when no symbol is given).
        # With no decision in the session it falls back to the most recent
        # decision at all, marked so the GUI shows its date
        # (docs/SPEC.md, GUI — Dashboard at scale and off-hours).
        latest = next(
            (
                row
                for row in shown_decisions
                if symbol is None or row[0].symbol == symbol.upper()
            ),
            None,
        )
        latest_in_session = latest is not None
        if latest is None:
            q = (
                select(Decision, Cycle.scheduled_time)
                .join(Cycle, Decision.cycle_id == Cycle.id)
                .order_by(Decision.id.desc())
                .limit(1)
            )
            if symbol:
                q = q.where(Decision.symbol == symbol.upper())
            rows = session.execute(q).all()
            latest = rows[0] if rows else None
        latest_earnings = (
            _decision_earnings(session, latest[0], latest[1])
            if latest is not None
            else None
        )

    # Stat cards. Risk figures live on /api/risk-budget, which reads the
    # same gate context the entry gates see.
    equity = account.equity if account else None
    since_start_pct = None
    spy_pct = None
    since_label = "since start"
    if run is not None and equity is not None:
        since_start_pct = (
            (equity - run.starting_equity) / run.starting_equity * 100
        )
        if run.spy_close:
            signals = _signals(request)
            if signals is not None and signals.latest is not None:
                spy_row = next(
                    (r for r in signals.latest.rows if r.symbol == "SPY"), None
                )
                if spy_row is not None and spy_row.price is not None:
                    spy_pct = (
                        (spy_row.price - run.spy_close)
                        / run.spy_close
                        * 100
                    )
        since_label = f"since {run.started_at.astimezone(app_tz).strftime('%d %b')}"

    # Latest decision card.
    latest_card = None
    if latest is not None:
        d, scheduled = latest
        validation = d.validation_result or {}
        checks = cast("list[dict[str, object]]", validation.get("checks") or [])
        output = d.model_output or {}
        rules = d.rules_result or {}
        snapshot = d.input_snapshot or {}
        if d.final_action == "no_call":
            if d.no_call_reason in (None, "rules"):
                why = rules.get("reason") or "failed the pre-filter"
            else:
                why = validation.get("error") or _NO_CALL_WHY.get(
                    d.no_call_reason,
                    str(d.no_call_reason).replace("_", " "),
                )
        else:
            why = (
                output.get("thesis")
                or validation.get("note")
                or validation.get("error")
            )
        latest_card = {
            "id": d.id,
            "symbol": d.symbol,
            "context": d.context,
            "model_action": validation.get("model_action"),
            "final_action": d.final_action,
            "no_call_reason": d.no_call_reason,
            "confidence": output.get("confidence"),
            "chosen_option": (
                d.chosen_option if d.prompt_version == "decision.v2" else None
            ),
            "why": why,
            "model": d.model,
            "regime": snapshot.get("regime"),
            "earnings": latest_earnings,
            "sig": _sig_chips(snapshot, config),
            "box": _latest_box(d, snapshot, session_state, app_tz),
            "checks": [
                {
                    "name": c.get("name"),
                    "passed": c.get("passed"),
                    "detail": c.get("detail"),
                }
                for c in checks[:4]
            ],
            "price": snapshot.get("price"),
            "time": scheduled.replace(tzinfo=UTC).isoformat(),
            # False when the decision is not from today's session — either
            # an off-hours look at the last session or an older fallback —
            # and the card shows the compact dated layout.
            "in_session": latest_in_session and shown_is_today,
        }

    # Cycle counter and cycle line share the session math. "Cycle N of M"
    # counts the shown session's cycles so far; M is the session's active
    # count (fewer on early-close days), not the configured list length.
    session_clock = SessionClock(clock, config, calendar)
    active_shown = (
        session_clock.active_cycles(shown_day)
        if shown_day is not None
        else []
    )
    shown_cycle_rows = [
        c for c in cycles if _ny_date(c.scheduled_time) == shown_date
    ]
    cycle_index = len(shown_cycle_rows)

    # Cycle line. In session (design: "5 of 8 symbols not held failed the
    # pre-filter. 3 reached the model: all skipped. Both open positions
    # held. 5 model calls · $0.002."), market closed: the shown session's
    # summary ("Last session: 6 cycles, 60 decisions, 1 entry, 1 stop
    # raised.").
    cycle_line = None
    if session_state.state == "closed":
        if shown_cycle_rows:
            entered_n = sum(
                1 for d, _ in shown_decisions if d.final_action == "enter"
            )
            tightened_n = sum(
                1
                for d, _ in shown_decisions
                if d.final_action == "tighten_stop"
            )
            parts = [
                f"{len(shown_cycle_rows)} cycles",
                f"{len(shown_decisions)} decisions",
            ]
            if entered_n:
                parts.append(
                    f"{entered_n} entr{'y' if entered_n == 1 else 'ies'}"
                )
            if tightened_n:
                parts.append(
                    f"{tightened_n} stop{'s' if tightened_n != 1 else ''} raised"
                )
            text = "Last session: " + ", ".join(parts) + "."
            if open_rows:
                n = len(open_rows)
                protected = (
                    "The position is"
                    if n == 1
                    else "Both positions are"
                    if n == 2
                    else f"All {n} positions are"
                )
                text += (
                    f" {protected} protected by stops at Alpaca until"
                    " trading opens; a stop only triggers once trading opens."
                )
            cycle_line = {
                "label": _session_date_label(shown_date),
                "status": "closed",
                "text": text,
                "cycle_index": cycle_index,
                "cycles_today": len(active_shown),
            }
    elif (
        last_cycle is not None
        and _ny_date(last_cycle.scheduled_time) == shown_date
    ):
        scheduled_ny = last_cycle.scheduled_time.replace(tzinfo=UTC)
        held_ctx = sum(1 for d in cycle_decisions if d.context == "held")
        not_held = len(cycle_decisions) - held_ctx
        prefilter_failed = sum(
            1 for d in cycle_decisions if d.no_call_reason == "rules"
        )
        calls = sum(1 for d in cycle_decisions if d.model != "none")
        cost = sum(
            (d.cost or Decimal(0)) for d in cycle_decisions
        )
        entered = sum(1 for d in cycle_decisions if d.final_action == "enter")
        blocked = sum(1 for d in cycle_decisions if d.final_action == "blocked")
        skipped = sum(1 for d in cycle_decisions if d.final_action == "skip")
        approved_count = sum(
            1
            for d in cycle_decisions
            if (d.gates_result or {}).get("pending_approval")
        )
        sentences: list[str] = []
        if not_held:
            if prefilter_failed:
                sentences.append(
                    f"{prefilter_failed} of {not_held} symbols not held "
                    "failed the pre-filter."
                )
            outcomes = []
            if skipped:
                outcomes.append(
                    "all skipped"
                    if skipped == calls
                    else f"{skipped} skipped"
                )
            if entered:
                outcomes.append(
                    f"{entered} ent{'ry' if entered == 1 else 'ries'}"
                )
            if approved_count:
                outcomes.append(
                    f"{approved_count} awaiting approval"
                )
            if blocked:
                outcomes.append(f"{blocked} blocked")
            if calls:
                sentences.append(
                    f"{calls} reached the model: "
                    + (", ".join(outcomes) if outcomes else "no change")
                    + "."
                )
        if held_ctx:
            if held_ctx == 2:
                sentences.append("Both open positions held.")
            else:
                sentences.append(
                    f"{held_ctx} open position"
                    f"{'s' if held_ctx != 1 else ''} held."
                )
        if calls:
            sentences.append(
                f"{calls} model call{'s' if calls != 1 else ''} · "
                f"${cost:.3f}."
            )
        ams_time = scheduled_ny.astimezone(app_tz).strftime("%H:%M")
        cycle_line = {
            "label": f"{ams_time} cycle",
            "status": last_cycle.status,
            "text": " ".join(sentences),
            "cycle_index": cycle_index,
            "cycles_today": len(active_shown),
        }

    # Cycle counter at the strip's right end (docs/SPEC.md, GUI). The whole
    # schedule — today's active cycles as done/next/later segments, the
    # next cycle and the next position check — is computed here from the
    # trading calendar and config; the GUI only formats and ticks.
    ny_now = clock.now().astimezone(NY_TZ)
    next_cycle_utc = session_state.next_cycle
    if next_cycle_utc is None:
        # Nothing left today (last cycle done): point at the next
        # session's first cycle, like the closed state does.
        next_session = _calendar(request).next_trading_day(today_ny)
        upcoming = (
            session_clock.active_cycles(next_session)
            if next_session is not None
            else []
        )
        next_cycle_utc = upcoming[0].astimezone(UTC) if upcoming else None
    next_check_utc = session_state.next_check
    cycle_counter = {
        "kill_switch": bool(getattr(state, "kill_switch_on", False)),
        "running": any(c.status == "running" for c in shown_cycle_rows),
        "session_is_today": shown_is_today,
        "now": clock.now().astimezone(UTC).isoformat(),
        "next_cycle": (
            next_cycle_utc.isoformat() if next_cycle_utc is not None else None
        ),
        "next_cycle_today": (
            next_cycle_utc is not None
            and next_cycle_utc.astimezone(NY_TZ).date() == today_ny
        ),
        "next_check": (
            next_check_utc.isoformat() if next_check_utc is not None else None
        ),
        "next_check_today": (
            next_check_utc is not None
            and next_check_utc.astimezone(NY_TZ).date() == today_ny
        ),
        "segments": [
            {
                "time": t.astimezone(UTC).isoformat(),
                "status": (
                    "done"
                    if t <= ny_now
                    else "next"
                    if next_cycle_utc is not None
                    and t.astimezone(UTC) == next_cycle_utc
                    else "later"
                ),
            }
            for t in active_shown
        ],
        "cycle_index": cycle_index,
        "cycles_today": len(active_shown),
        "run_cycle_number": run_cycle_number,
    }

    # Decisions feed, grouped client-side by cycle. Each item carries its
    # cycle id, the cycle's number within the session, whether the decision
    # changed something (entries, exits, stop moves, approvals — always
    # shown, even in collapsed cycles) and whether a real model call
    # happened.
    cycle_order = {
        c.id: i
        for i, c in enumerate(
            sorted(shown_cycle_rows, key=lambda c: c.scheduled_time), start=1
        )
    }
    feed = [
        {
            "id": d.id,
            "time": t.replace(tzinfo=UTC).isoformat(),
            "cycle_id": d.cycle_id,
            "cycle_index": cycle_order.get(d.cycle_id),
            "symbol": d.symbol,
            "context": d.context,
            "action": d.final_action,
            "model_action": (d.validation_result or {}).get("model_action"),
            "no_call_reason": d.no_call_reason,
            "reason": _feed_reason(d),
            "change": _feed_change(d),
            "called": d.model != "none",
        }
        for d, t in shown_decisions
    ]

    # Market-closed card data.
    market_closed = None
    if session_state.state == "closed":
        stops = [
            {"symbol": p.symbol, "stop": str(p.current_stop_price)}
            for p in open_rows
            if p.current_stop_price is not None
        ]
        entered_today = sum(1 for d, _ in shown_decisions if d.final_action == "enter")
        tightened = sum(
            1 for d, _ in shown_decisions if d.final_action == "tighten_stop"
        )
        summary_parts = []
        if entered_today:
            summary_parts.append(
                f"{entered_today} entr{'y' if entered_today == 1 else 'ies'}"
            )
        if tightened:
            summary_parts.append(
                f"{tightened} stop{'s' if tightened != 1 else ''} raised"
            )
        market_closed = {
            "next_open": (
                session_state.next_open.isoformat()
                if session_state.next_open
                else None
            ),
            "first_cycle": (
                session_state.next_cycle.isoformat()
                if session_state.next_cycle
                else None
            ),
            "stops": stops,
            "open_count": len(open_rows),
            "today_summary": ", ".join(summary_parts) or "quiet day",
        }

    # Model call counter tile (docs/SPEC.md, GUI — Model call counter):
    # today's real decision calls against the day's maximum — the
    # per-cycle cap times today's active cycles — with today's and this
    # month's decision spend. Reviews and shadows have their own caps; their
    # spend feeds the tile's hover text. Backtests (PR 14) join later.
    calls_today = sum(1 for d, _ in shown_decisions if d.model != "none")
    calls_cost_today = sum(
        (d.cost or Decimal(0))
        for d, _ in shown_decisions
        if d.model != "none"
    )
    month_start = (
        datetime.combine(today.replace(day=1), dtime.min, tzinfo=app_tz)
        .astimezone(UTC)
        .replace(tzinfo=None)
    )
    with get_session_maker()() as session:
        calls_cost_month = session.execute(
            select(func.sum(Decision.cost))
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .where(
                Cycle.scheduled_time >= month_start,
                Decision.model != "none",
            )
        ).scalar_one() or Decimal(0)
        review_service = getattr(request.app.state, "review_service", None)
        reviews_spend = (
            review_service.monthly_spend(session)
            if review_service is not None
            else None
        )
        shadow_runner = getattr(request.app.state, "shadow_runner", None)
        shadows_spend = (
            shadow_runner.monthly_spend(session) if shadow_runner is not None else None
        )
    calls_per_cycle = int(
        config.get("decision", "max_calls_per_cycle", default=10)
    )

    return {
        "session": {
            "state": session_state.state,
            # The session the feed, tiles and cards describe: today's when a
            # session runs today, else the last completed one.
            "shown_date": shown_date.isoformat(),
            "shown_is_today": shown_is_today,
            "next_cycle": (
                session_state.next_cycle.isoformat()
                if session_state.next_cycle
                else None
            ),
            "next_open": (
                session_state.next_open.isoformat()
                if session_state.next_open
                else None
            ),
            "next_close": (
                session_state.next_close.isoformat()
                if session_state.next_close
                else None
            ),
        },
        "cycle_line": cycle_line,
        "cycle_counter": cycle_counter,
        "stats": {
            "positions": len(open_rows),
            "positions_cap": config.get("risk", "max_open_positions", default=4),
            "cash": _fmt_money(account.cash if account else None),
            # Numbers travel as numbers; the GUI formats them (minus sign
            # U+2212, docs/SPEC.md — Interaction and states).
            "since_start_pct": (
                float(since_start_pct) if since_start_pct is not None else None
            ),
            "since_label": since_label,
            "spy_pct": float(spy_pct) if spy_pct is not None else None,
            "execution_mode": config.get(
                "execution", "mode", default="automatic"
            ),
            "calls_today": calls_today,
            "calls_max": calls_per_cycle * len(active_shown),
            "calls_per_cycle": calls_per_cycle,
            "cycles_today": len(active_shown),
            "calls_cost_today": str(calls_cost_today),
            "calls_cost_month": str(calls_cost_month),
            "reviews_cost_month": (
                str(reviews_spend) if reviews_spend is not None else None
            ),
            "reviews_cost_cap": str(
                config.get("reviews", "monthly_cost_cap_usd", default=2)
            ),
            # Shadows have their own cap too (docs/SPEC.md, Model call counter).
            "shadows_cost_month": (
                str(shadows_spend) if shadows_spend is not None else None
            ),
            "shadows_cost_cap": str(
                config.get("shadows", "monthly_cost_cap_usd", default=5)
            ),
        },
        "briefing": _briefing_card(request, clock, app_tz, today_ny),
        "latest_decision": latest_card,
        "approvals_pending": len(pending),
        "market_closed": market_closed,
        "feed": feed,
    }


def _session_date_label(day: date) -> str:
    """"Friday 23 Oct" — a session's date for the closed-state strip."""
    return f"{day.strftime('%A')} {day.day} {day.strftime('%b')}"


# Why a symbol reached no model call, for the latest-decision card
# (docs/SPEC.md: "why from the stored snapshot — pre-filter reason, call
# cap, price check").
_NO_CALL_WHY = {
    "call_cap": "call cap reached — the model was not asked",
    "price_stale": "no fresh price — the model was not asked",
    "price_suspect": "price check failed — the model was not asked",
    # Stored decisions from before the Oct-24 rename keep the old name.
    "quote_suspect": "price check failed — the model was not asked",
    "model_error": "the model call failed",
    "no_options": "no option met the rules — the model was not asked",
    "reference_missing": "no usable reference price — the model was not asked",
}


def _feed_change(decision: Decision) -> bool:
    """Changes always show as their own feed rows, even in collapsed cycles
    (design/screens/Main.dc.html: entries, exits, stop moves, pending
    approvals)."""
    if decision.final_action in {"enter", "exit", "tighten_stop"}:
        return True
    return bool((decision.gates_result or {}).get("pending_approval"))


def _feed_reason(decision: Decision) -> str | None:
    """One-line reason under the action in a feed row.

    Rows with the same (action, reason) collapse into one grouped row, so
    the text is the shared reason — not the symbol. ``pre-filter`` for
    rules-held rows; the failing code check when code changed the model's
    action; the model's thesis otherwise.
    """
    validation = decision.validation_result or {}
    output = decision.model_output or {}
    if decision.final_action == "no_call":
        if decision.no_call_reason in (None, "rules"):
            return "pre-filter"
        return str(
            validation.get("error") or decision.no_call_reason
        ).replace("_", " ")
    if decision.final_action == "blocked":
        gates = decision.gates_result or {}
        return f"blocked: {gates.get('blocked_by', 'gate')}"
    if validation.get("changed_by_code"):
        failed = next(
            (
                c
                for c in cast("list[dict[str, object]]", validation.get("checks") or [])
                if c.get("passed") is False
            ),
            None,
        )
        if failed is not None and failed.get("name"):
            return str(failed["name"])
        note = str(validation.get("note") or "")
        return note or None
    thesis = output.get("thesis")
    if isinstance(thesis, str) and thesis:
        return thesis
    conf = output.get("confidence")
    return f"confidence {conf}" if conf is not None else None


@router.get("/chart/{symbol}")
def chart(request: Request, symbol: str) -> dict[str, object]:
    """Session-aligned 1h candles + VWAP + EMAs + level lines for the chart."""
    clock = getattr(request.app.state, "clock", None) or make_clock()
    now = clock.now()
    market_data = getattr(request.app.state, "market_data", None)
    if market_data is None:
        raise HTTPException(status_code=503, detail="market data unavailable")
    today_ny = now.astimezone(NY_TZ).date()
    # Extra sessions warm up the EMAs; only the last 5 are returned.
    display_sessions = market_data.recent_sessions(today_ny, 5)
    warm_sessions = market_data.recent_sessions(today_ny, 20)
    if not display_sessions:
        raise HTTPException(status_code=404, detail="no sessions loaded")
    bars_1h_all = market_data.get_1h_bars_range(symbol, warm_sessions)
    bars_1h_all = [b for b in bars_1h_all if b.end <= now]
    closes = [b.close for b in bars_1h_all]
    ema20 = ema_series(closes, 20)
    ema50 = ema_series(closes, 50)

    # Per-session VWAP from 5m bars, sampled at each 1h bar end.
    bars_5m = market_data.get_5m_bars_sessions(symbol, display_sessions)
    vwap_by_session: dict[object, list[tuple[datetime, Decimal]]] = {}
    cum_pv: dict[object, Decimal] = {}
    cum_v: dict[object, Decimal] = {}
    for bar in bars_5m:
        day = bar.start.astimezone(NY_TZ).date()
        typical = (bar.high + bar.low + bar.close) / 3
        cum_pv[day] = cum_pv.get(day, Decimal(0)) + typical * bar.volume
        cum_v[day] = cum_v.get(day, Decimal(0)) + bar.volume
        if cum_v[day] > 0:
            vwap_by_session.setdefault(day, []).append(
                (bar.start, cum_pv[day] / cum_v[day])
            )

    display_dates = {s.date for s in display_sessions}
    out_bars = []
    for index, bar in enumerate(bars_1h_all):
        day = bar.start.astimezone(NY_TZ).date()
        if day not in display_dates:
            continue
        vwap_series = vwap_by_session.get(day, [])
        vwap = next(
            (
                v
                for t, v in reversed(vwap_series)
                if t <= bar.end - timedelta(minutes=5)
            ),
            vwap_series[-1][1] if vwap_series else None,
        )
        out_bars.append(
            {
                "time": bar.start.replace(tzinfo=UTC).isoformat()
                if bar.start.tzinfo is None
                else bar.start.astimezone(UTC).isoformat(),
                "session": day.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": bar.volume,
                "ema20": str(ema20[index]) if ema20[index] is not None else None,
                "ema50": str(ema50[index]) if ema50[index] is not None else None,
                "vwap": str(vwap) if vwap is not None else None,
            }
        )

    # Level lines from the open position or open entry order.
    levels: dict[str, object] = {"kind": None}
    with get_session_maker()() as session:
        position = session.execute(
            select(Position).where(
                Position.symbol == symbol, Position.closed_at.is_(None)
            )
        ).scalar_one_or_none()
        if position is not None:
            levels = {
                "kind": "position",
                "entry": str(position.avg_entry_price),
                "stop": (
                    str(position.current_stop_price)
                    if position.current_stop_price is not None
                    else None
                ),
                "target": (
                    str(position.target_price)
                    if position.target_price is not None
                    else None
                ),
                "opened_at": position.opened_at.replace(tzinfo=UTC).isoformat(),
            }
        else:
            order = session.execute(
                select(Order).where(
                    Order.symbol == symbol,
                    Order.leg == "entry",
                    Order.status.in_(OPEN_ORDER_STATUSES),
                )
            ).scalar_one_or_none()
            if order is not None:
                legs = session.execute(
                    select(Order).where(
                        Order.parent_alpaca_order_id == order.alpaca_order_id
                    )
                ).scalars().all()
                stop_leg = next(
                    (leg for leg in legs if leg.leg == "stop"), None
                )
                target_leg = next(
                    (leg for leg in legs if leg.leg == "target"), None
                )
                levels = {
                    "kind": "order",
                    "entry": (
                        str(order.limit_price)
                        if order.limit_price is not None
                        else None
                    ),
                    "stop": (
                        str(stop_leg.stop_price) if stop_leg is not None else None
                    ),
                    "target": (
                        str(target_leg.limit_price)
                        if target_leg is not None
                        else None
                    ),
                }
            else:
                approval = session.execute(
                    select(Approval).where(
                        Approval.symbol == symbol, Approval.status == "pending"
                    )
                ).scalar_one_or_none()
                if approval is not None:
                    levels = {
                        "kind": "approval",
                        "entry": str(approval.limit_price),
                        "stop": str(approval.stop_price),
                        "target": str(approval.target_price),
                    }

    signals = _signals(request)
    warming = False
    price = None
    change = None
    if signals is not None and signals.latest is not None:
        row = next(
            (r for r in signals.latest.rows if r.symbol == symbol), None
        )
        if row is not None:
            warming = row.warming_up
            price = str(row.price) if row.price is not None else None
            change = (
                float(row.day_change_pct)
                if row.day_change_pct is not None
                else None
            )
    sessions = sorted(
        {day.isoformat() for day in display_dates},
    )
    return {
        "symbol": symbol,
        "sessions": sessions,
        "bars": out_bars,
        "levels": levels,
        "warming_up": warming,
        "price": price,
        "day_change_pct": change,
    }


# ----------------------------------------------------------------------
# Reviews: weekly review, daily briefing, verdicts (docs/SPEC.md, Reviews)

_VERDICT_VALUES = ("good_call", "bad_call", "unclear")
_WEEKDAY_NUMBERS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _week_sub(review: Review) -> str:
    facts = review.facts or {}
    period = facts.get("period") or {}
    start, end = period.get("start"), period.get("end")
    if isinstance(start, str) and isinstance(end, str):
        s = datetime.fromisoformat(start).date()
        e = datetime.fromisoformat(end).date()
        if s.month == e.month:
            return f"{s.day} – {e.day} {e.strftime('%b')}"
        return f"{s.day} {s.strftime('%b')} – {e.day} {e.strftime('%b')}"
    return review.period


def _review_label(review: Review) -> str:
    facts = review.facts or {}
    period = facts.get("period") or {}
    if review.kind == "weekly":
        return f"Week {period.get('week', review.period)}"
    try:
        day = datetime.fromisoformat(review.period).date()
    except ValueError:
        return review.period
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def _review_item(review: Review) -> dict[str, object]:
    facts = review.facts or {}
    item: dict[str, object] = {
        "id": review.id,
        "kind": review.kind,
        "period": review.period,
        "label": _review_label(review),
        "sub": _week_sub(review),
        "source": review.source,
        "reason": review.reason,
        "due_at": _iso(review.due_at),
        "created_at": _iso(review.created_at),
    }
    if review.kind == "weekly":
        item["return_pct"] = facts.get("return_pct")
    else:
        item["preview"] = review.text[:80]
    return item


def _next_weekly_due(request: Request) -> str | None:
    """The next weekly-review instant in the future (aware UTC ISO)."""
    config = get_config()
    tz = ZoneInfo(str(config.get("app", "timezone", default="Europe/Amsterdam")))
    weekday = _WEEKDAY_NUMBERS.get(
        str(config.get("reviews", "weekly_day", default="saturday"))
        .strip()
        .lower(),
        5,
    )
    raw = str(config.get("reviews", "weekly_time", default="10:00"))
    hour, minute = (int(p) for p in raw.split(":")[:2])
    now = _clock(request).now()
    local_today = now.astimezone(tz).date()
    days_ahead = (weekday - local_today.weekday()) % 7
    candidate = datetime.combine(
        local_today + timedelta(days=days_ahead), dtime(hour, minute), tzinfo=tz
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate.astimezone(UTC).isoformat()


def _review_schedule(request: Request) -> str:
    """The subtitle under the page title: the configured schedule, in the
    app timezone ("Weekly review Saturday 10:00 · briefing trading days
    15:00 Amsterdam"; "briefing off" when briefings are disabled)."""
    config = get_config()
    tz = ZoneInfo(str(config.get("app", "timezone", default="Europe/Amsterdam")))
    city = str(tz).rsplit("/", 1)[-1]
    day = str(config.get("reviews", "weekly_day", default="saturday")).capitalize()
    at = str(config.get("reviews", "weekly_time", default="10:00"))
    parts = [f"Weekly review {day} {at}"]
    if not bool(config.get("reviews", "briefing_enabled", default=True)):
        parts.append("briefing off")
        return " · ".join(parts)
    raw = str(config.get("reviews", "briefing_time_ny", default="09:00"))
    hour, minute = (int(p) for p in raw.split(":")[:2])
    now_ny = _clock(request).now().astimezone(NY_TZ).date()
    local = datetime.combine(now_ny, dtime(hour, minute), tzinfo=NY_TZ).astimezone(tz)
    parts.append(f"briefing trading days {local.strftime('%H:%M')} {city}")
    return " · ".join(parts)


@router.get("/reviews")
def reviews_list(request: Request, kind: str = "weekly") -> dict[str, object]:
    """Written weekly reviews or daily briefings, newest first."""
    if kind not in ("weekly", "briefing"):
        raise HTTPException(status_code=400, detail="kind must be weekly or briefing")
    with get_session_maker()() as session:
        rows = session.execute(
            select(Review)
            .where(Review.kind == kind)
            .order_by(Review.due_at.desc())
            .limit(52)
        ).scalars().all()
    return {
        "items": [_review_item(r) for r in rows],
        "next_weekly_due": _next_weekly_due(request) if kind == "weekly" else None,
        "schedule": _review_schedule(request),
    }


@router.get("/reviews/{review_id}")
def review_detail(review_id: int) -> dict[str, object]:
    """One review: the facts, the text and where the text came from."""
    with get_session_maker()() as session:
        review = session.execute(
            select(Review).where(Review.id == review_id)
        ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    return {
        **_review_item(review),
        "facts": review.facts,
        "text": review.text,
        "content": review.content,
        "model": review.model,
        "prompt_version": review.prompt_version,
        "tokens": review.tokens,
        "cost": str(review.cost) if review.cost is not None else None,
    }


def _verdict_outcome(
    request: Request,
    session: Session,
    decision: Decision,
    scheduled: datetime,
    outcome: Outcome | None,
) -> str | None:
    """The one-line outcome next to a draft verdict ("target +1.6R")."""
    if outcome is None or outcome.price_5sessions is None:
        return None
    facts = verdict_facts(
        session,
        config=get_config(),
        clock=_clock(request),
        decision=decision,
        scheduled=scheduled,
        outcome=outcome,
    )
    n = facts.get("sessions", 5)
    trade = facts.get("trade")
    if trade is not None and trade.get("exit") is not None and trade.get("r") is not None:
        r = float(trade["r"])
        return f"{trade['exit']} {r:+.1f}R"
    pct = facts.get("pct_5sessions")
    if pct is None:
        return None
    suffix = " · open" if trade is not None and trade.get("open") else ""
    return f"{float(pct):+.1f}% after {n} sessions{suffix}"


@router.get("/verdicts")
def verdicts_list(request: Request, status: str = "draft") -> dict[str, object]:
    """Draft verdicts to confirm (default) or confirmed ones (?status=confirmed|all)."""
    if status not in ("draft", "confirmed", "all"):
        raise HTTPException(status_code=400, detail="unknown status")
    with get_session_maker()() as session:
        query = (
            select(Verdict, Decision, Cycle.scheduled_time, Outcome)
            .join(Decision, Verdict.decision_id == Decision.id)
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .outerjoin(Outcome, Outcome.decision_id == Decision.id)
            .order_by(Verdict.id)
        )
        if status == "draft":
            query = query.where(Verdict.source == "draft")
        elif status == "confirmed":
            query = query.where(Verdict.source == "marc").order_by(
                Verdict.updated_at.desc()
            )
        rows = session.execute(query.limit(100)).all()
        items = [
            {
                "id": verdict.id,
                "decision_id": decision.id,
                "symbol": decision.symbol,
                "final_action": decision.final_action,
                "decided_at": _aware(scheduled).isoformat(),
                "outcome": _verdict_outcome(
                    request, session, decision, scheduled, outcome
                ),
                "verdict": verdict.verdict,
                "reason": verdict.reason,
                "source": verdict.source,
                "draft_source": verdict.draft_source,
                "created_at": _iso(verdict.created_at),
                "updated_at": _iso(verdict.updated_at),
            }
            for verdict, decision, scheduled, outcome in rows
        ]
    return {"items": items, "status": status}


@router.post("/verdicts/{verdict_id}")
def verdict_resolve(
    request: Request, verdict_id: int, body: dict[str, object]
) -> dict[str, object]:
    """Confirm a draft verdict (same value) or change it — either way the
    verdict becomes Marc's and counts in reviews."""
    value = str(body.get("verdict", ""))
    if value not in _VERDICT_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"verdict must be one of {', '.join(_VERDICT_VALUES)}",
        )
    with get_session_maker()() as session:
        verdict = session.execute(
            select(Verdict).where(Verdict.id == verdict_id)
        ).scalar_one_or_none()
        if verdict is None:
            raise HTTPException(status_code=404, detail="verdict not found")
        verdict.verdict = value
        verdict.source = "marc"
        verdict.updated_at = _naive_utc(_clock(request).now())
        session.commit()
        resolved = {"id": verdict.id, "verdict": verdict.verdict, "source": verdict.source}
    _notify(request, "reviews", "decisions")
    return resolved


# ----------------------------------------------------------------------
# Universe (docs/SPEC.md, Larger universe). All of this is inert in fixed
# mode: describe() returns the fixed list and the pool stays untouched.


def _universe(request: Request) -> UniverseService:
    service = getattr(request.app.state, "universe", None)
    if service is None:
        raise HTTPException(status_code=503, detail="universe service unavailable")
    return cast(UniverseService, service)


@router.get("/universe")
def universe(request: Request) -> dict[str, object]:
    """The Universe screen: mode, pool rows, latest screen and funnel."""
    return _universe(request).describe()


async def _warm_up_in_background(service: UniverseService, symbols: list[str]) -> None:
    try:
        await service.warm_up(symbols)
    except Exception:  # noqa: BLE001
        logger.exception("universe warm-up failed for %s", symbols)


@router.post("/universe/pool")
async def universe_pool_add(
    request: Request, body: dict[str, object]
) -> dict[str, object]:
    """Add one symbol (with a required sector) to the screened pool.

    The same checks apply as a fixed-universe symbol add: valid ticker,
    active and tradable US stock or ETF at Alpaca, allowed sector — plus
    the pool cap. The symbol warms up and joins the list at the next
    daily screen.
    """
    service = _universe(request)
    symbol = str(body.get("symbol") or "").strip().upper()
    sector = str(body.get("sector") or "").strip()
    try:
        row = await service.add_symbol(symbol, sector)
    except PoolError as exc:
        raise HTTPException(status_code=422, detail=exc.messages) from exc
    from app.spawn import spawn

    spawn(_warm_up_in_background(service, [row.symbol]), "universe-warmup")
    _notify(request, "universe")
    return {"symbol": row.symbol, "sector": row.sector, "added": True}


@router.post("/universe/import")
async def universe_pool_import(
    request: Request, body: dict[str, object]
) -> dict[str, object]:
    """Import ``symbol,sector`` CSV rows into the pool — all or nothing."""
    service = _universe(request)
    text = body.get("csv")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail=["csv text required"])
    try:
        added, skipped = await service.import_csv(text)
    except PoolError as exc:
        raise HTTPException(status_code=422, detail=exc.messages) from exc
    if added:
        from app.spawn import spawn

        spawn(_warm_up_in_background(service, added), "universe-warmup")
    _notify(request, "universe")
    return {"added": len(added), "skipped": skipped}


@router.post("/universe/pool/{symbol}/remove")
def universe_pool_remove(request: Request, symbol: str) -> dict[str, object]:
    """Remove a symbol from the pool. A held symbol stays listed until its
    position is closed — held symbols are always handled."""
    service = _universe(request)
    try:
        service.remove_symbol(symbol)
    except PoolError as exc:
        raise HTTPException(status_code=422, detail=exc.messages) from exc
    _notify(request, "universe")
    return {"symbol": symbol.upper(), "removed": True}


@router.post("/universe/screen")
async def universe_screen_now(request: Request) -> dict[str, object]:
    """Run the daily screen on demand — simulated mode only.

    In paper mode the screen runs from the 08:10 New York scheduler job;
    this hook exists for tests and the simulated demo.
    """
    if get_config().env.mode != "simulated":
        raise HTTPException(status_code=403, detail="simulated_only")
    result = await _universe(request).run_screen()
    _notify(request, "universe", "banners", "header")
    return result


# ---------------------------------------------------------------------------
# Frontend error reporting (docs/SPEC.md, Error handling)

_client_error_times: dict[str, list[float]] = {}


@router.post("/logs/client")
def log_client_error(request: Request, body: dict[str, object]) -> dict[str, object]:
    """Frontend errors: window.onerror, unhandledrejection and the React
    error boundary post here, at most ``logging.client_errors_per_minute``
    per tab. Logged at ERROR with logger ``frontend``."""
    import time

    limit = int(get_config().get("logging", "client_errors_per_minute", default=10))
    tab = str(body.get("tab_id") or request.client.host if request.client else "?")
    now = time.monotonic()
    bucket = [t for t in _client_error_times.get(tab, []) if now - t < 60]
    _client_error_times[tab] = bucket
    if len(bucket) >= limit:
        return {"logged": False, "rate_limited": True}
    bucket.append(now)
    # The id the boundary shows — the client's own when it sent one, else
    # minted here; it is on the log line and echoed back (spec →
    # Something went wrong).
    error_id = str(body.get("error_id") or "") or f"e-{secrets.token_hex(3)}"
    logging.getLogger("frontend").error(
        "%s (%s:%s:%s)\n%s",
        body.get("message", "frontend error"),
        body.get("source", ""),
        body.get("lineno", ""),
        body.get("colno", ""),
        body.get("stack", ""),
        extra={
            "event": "frontend.error",
            "context": {"url": str(body.get("url", "")), "tab_id": tab},
            "error_id": error_id,
        },
    )
    return {"logged": True, "error_id": error_id}


# ---------------------------------------------------------------------------
# Log index (docs/SPEC.md, Logging — API)

def _log_db(request: Request) -> sqlite3.Connection | None:
    """Open the log index read-only, or None when unavailable (never pass
    readiness, but the API still answers)."""
    from app.log_index import index_path

    path = index_path(get_config())
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _log_row(row: tuple[object, ...]) -> dict[str, object]:
    import json

    return {
        "id": row[0], "ts": row[1], "level": row[2], "logger": row[3],
        "event": row[4], "message": row[5],
        "context": json.loads(str(row[6] or "{}")),
        "cycle": row[7], "symbol": row[8], "process": row[9], "pid": row[10],
        "error_id": row[11], "exception_type": row[12],
        "exception_message": row[13], "traceback": row[14],
        "fingerprint": row[15],
    }


_COLS = ("id,ts,level,logger,event,message,context,cycle,symbol,process,pid,"
         "error_id,exception_type,exception_message,traceback,fingerprint")


def _log_where(
    params: Mapping[str, str],
) -> tuple[str, list[object]]:
    where: list[str] = []
    args: list[object] = []
    levels = params.get("levels")
    if levels:
        lv = [x.strip().upper() for x in str(levels).split(",") if x.strip()]
        where.append("level IN (" + ",".join("?" * len(lv)) + ")")
        args += lv
    for key, col in (("logger", "logger"), ("symbol", "symbol"), ("cycle", "cycle")):
        if params.get(key):
            where.append(f"{col} = ?")
            args.append(params[key])
    if params.get("from"):
        where.append("ts >= ?")
        args.append(params["from"])
    if params.get("to"):
        where.append("ts <= ?")
        args.append(params["to"])
    if params.get("search"):
        where.append(
            "id IN (SELECT rowid FROM log_lines_fts WHERE message MATCH ?)"
        )
        args.append(str(params["search"]).replace('"', '""'))
    if params.get("cursor"):
        where.append("id < ?")
        args.append(int(str(params["cursor"])))
    return ("WHERE " + " AND ".join(where)) if where else "", args


@router.get("/logs")
def api_logs(request: Request) -> dict[str, object]:
    """Newest-first keyset paging (cursor = last id), limit ≤ 200."""
    conn = _log_db(request)
    if conn is None:
        return {"lines": [], "next_cursor": None, "index_available": False}
    with conn:
        where, args = _log_where(request.query_params)
        limit = min(int(str(request.query_params.get("limit") or 100)), 200)
        rows = conn.execute(
            f"SELECT {_COLS} FROM log_lines {where} "
            f"ORDER BY id DESC LIMIT {limit + 1}",
            args,
        ).fetchall()
    lines = [_log_row(r) for r in rows[:limit]]
    return {
        "lines": lines,
        "next_cursor": rows[limit][0] if len(rows) > limit else None,
        "index_available": True,
    }


@router.get("/logs/summary")
def api_logs_summary(request: Request) -> dict[str, object]:
    """Warnings/errors per day + recurring problems by fingerprint."""
    conn = _log_db(request)
    if conn is None:
        return {"days": [], "problems": [], "index_available": False}
    with conn:
        days = conn.execute(
            "SELECT substr(ts,1,10) day, level, count(*) FROM log_lines "
            "WHERE level IN ('WARNING','ERROR','CRITICAL') "
            "GROUP BY day, level ORDER BY day"
        ).fetchall()
        level_counts = conn.execute(
            "SELECT level, count(*) FROM log_lines GROUP BY level"
        ).fetchall()
        problems = conn.execute(
            "SELECT fingerprint, level, min(ts) first_ts, max(ts) last_ts, "
            "count(*) n, substr(message,1,160) sample FROM log_lines "
            "WHERE level IN ('ERROR','CRITICAL') "
            "GROUP BY fingerprint ORDER BY last_ts DESC LIMIT 50"
        ).fetchall()
        # Review state for each fingerprint (step 8: log_reviews).
        reviews: dict[str, str] = {}
        try:
            from sqlalchemy import text

            with get_session_maker()() as session:
                for fp, review in session.execute(
                    text("SELECT fingerprint, review FROM log_reviews")
                ):
                    reviews[fp] = review
        except Exception:  # noqa: BLE001 - table may predate the migration
            logging.getLogger("api").debug("log_reviews not available yet")
    return {
        "days": [{"day": d, "level": lv, "count": c} for d, lv, c in days],
        "level_counts": dict(level_counts),
        "problems": [
            {
                "fingerprint": fp, "level": lv, "first_ts": fts,
                "last_ts": ts, "count": n,
                "sample": smp, "review": reviews.get(fp),
            }
            for fp, lv, fts, ts, n, smp in problems
        ],
        "index_available": True,
    }


@router.get("/logs/{line_id}/around")
def api_logs_around(line_id: int, request: Request) -> dict[str, object]:
    """The line plus `window` lines before/after by index order."""
    conn = _log_db(request)
    if conn is None:
        raise HTTPException(status_code=503, detail="log index unavailable")
    window = min(int(str(request.query_params.get("window") or 20)), 100)
    with conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM log_lines WHERE id BETWEEN ? AND ? "
            "ORDER BY id",
            (line_id - window, line_id + window),
        ).fetchall()
    return {"lines": [_log_row(r) for r in rows], "center": line_id}


@router.get("/logs/export.jsonl")
def api_logs_export(request: Request) -> Response:
    """Download the filtered lines as JSONL (same filters as /api/logs)."""
    import json

    conn = _log_db(request)
    if conn is None:
        raise HTTPException(status_code=503, detail="log index unavailable")
    with conn:
        where, args = _log_where(request.query_params)
        rows = conn.execute(
            f"SELECT {_COLS} FROM log_lines {where} ORDER BY id", args
        ).fetchall()
    body = "\n".join(json.dumps(_log_row(r), default=str) for r in rows)
    return Response(
        content=body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="logs.jsonl"'},
    )


@router.post("/logs/review")
def api_logs_review(request: Request, body: dict[str, object]) -> dict[str, object]:
    """Review a recurring problem: expected or fixed, with a required
    note (docs/SPEC.md, Readiness — Errors in the log)."""
    from app.data.cache import _naive_utc
    from app.db.models import LogReview

    review = str(body.get("review") or "")
    note = str(body.get("note") or "").strip()
    fingerprint = str(body.get("fingerprint") or "")
    if review not in ("expected", "fixed") or not note or not fingerprint:
        raise HTTPException(
            status_code=422, detail="review (expected|fixed), note and fingerprint required"
        )
    clock = getattr(request.app.state, "clock", None) or make_clock()
    with get_session_maker()() as session:
        session.add(
            LogReview(
                fingerprint=fingerprint,
                level=str(body.get("level") or "ERROR"),
                sample=str(body.get("sample") or "")[:400],
                review=review,
                note=note,
                reviewed_at=_naive_utc(clock.now()),
                app_version="0.2.0",
            )
        )
        session.commit()
    return {"ok": True, "review": review}


# ----------------------------------------------------------------------
# Settings → Server (docs/SPEC.md → VPS deployment → Monitoring). The
# section exists only when sign-in is required (the VPS); every row is
# read-only except the two backup actions.


def _ops(request: Request) -> Any:
    return getattr(request.app.state, "ops", None)


@router.get("/server")
def server_status(request: Request) -> dict[str, object]:
    """Everything the Server section shows, from live state + the
    persisted check results."""
    config = get_config()
    if config.env.sa_auth != "required":
        raise HTTPException(status_code=404, detail="not on a VPS")
    ops = _ops(request)
    status = ops.status if ops is not None else {}
    state = getattr(request.app.state, "exec_state", None)
    started = getattr(request.app.state, "run_lock", None)
    started_at = None
    if started is not None and started.path.exists():
        try:
            import json as _json

            started_at = _json.loads(started.path.read_text()).get("started_at")
        except (OSError, ValueError):
            started_at = None
    import os

    db = Path(config.env.sa_db_path)
    backups = sorted((db.parent / "backups").glob(f"{db.name}.*"))
    return {
        "version": request.app.version,
        "image_tag": os.environ.get("SA_VERSION", ""),
        "deployed_at": os.environ.get("SA_DEPLOYED_AT", ""),
        "public_url": config.env.sa_public_url,
        "instance_id": config.env.sa_instance_id,
        "started_at": started_at,
        "restart_resumed": getattr(request.app.state, "restart_resumed", None),
        "paused": bool(getattr(state, "paused", False)),
        "disk": status.get("disk"),
        "clock": status.get("clock"),
        "cert": status.get("cert"),
        "backups": status.get("backups"),
        "host": ops.host_status() if ops is not None else {},
        "heartbeat": {
            "url": (ops.heartbeat_url() if ops is not None else ""),
            "last_ping": getattr(ops, "last_heartbeat_at", None),
            "configured": bool(ops and ops.heartbeat_url()),
        },
        "latest_backup": backups[-1].name if backups else None,
    }


@router.post("/server/backup-now")
async def server_backup_now(request: Request) -> dict[str, object]:
    """Run the daily database copy now (same path as the 03:00 job)."""
    from app.db.backup import create_backup

    if get_config().env.sa_auth != "required":
        raise HTTPException(status_code=404, detail="not on a VPS")
    try:
        await run_in_threadpool(create_backup, _clock(request))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=[f"backup failed: {exc}"]) from exc
    return {"ok": True}


@router.get("/server/backup-download")
def server_backup_download(request: Request) -> Response:
    """The newest local backup as a file download — behind 'Confirm it
    is you' like the other server actions."""
    from fastapi.responses import FileResponse

    from app.auth.api import require_confirmed

    if get_config().env.sa_auth != "required":
        raise HTTPException(status_code=404, detail="not on a VPS")
    require_confirmed(request)
    db = Path(get_config().env.sa_db_path)
    backups = sorted((db.parent / "backups").glob(f"{db.name}.*"))
    if not backups:
        raise HTTPException(status_code=404, detail="no backup yet")
    latest = backups[-1]
    # The database only — secret.key is never part of a download.
    # Every download is an event and an info-level email (spec).
    from app.engine.state import record_event

    with get_session_maker()() as session:
        record_event(
            session,
            _clock(request),
            level="info",
            event_type="backup_downloaded",
            details={"file": latest.name},
        )
        session.commit()
    return FileResponse(
        latest, filename=latest.name, media_type="application/octet-stream"
    )


@router.get("/server/backup-credentials")
def server_backup_credentials_status(request: Request) -> dict[str, object]:
    """Write-only like the API keys: reads return only the endpoint,
    the bucket and the last 4 of the key id — never the secrets."""
    if get_config().env.sa_auth != "required":
        raise HTTPException(status_code=404, detail="not on a VPS")
    secrets = _secrets(request)
    if secrets is None:
        raise HTTPException(status_code=503, detail="secrets unavailable")
    import json

    raw = secrets.get("backup_credentials")
    if not raw or not secrets.get("backup_password"):
        return {"configured": False}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"configured": False}
    key_id = str(data.get("access_key_id") or "")
    return {
        "configured": True,
        "endpoint": data.get("endpoint"),
        "bucket": data.get("bucket"),
        "key_id_last4": key_id[-4:] if key_id else None,
    }


@router.post("/server/backup-credentials")
async def server_backup_credentials(
    request: Request, body: dict[str, object]
) -> dict[str, object]:
    """Store the offsite backup password + storage credentials in the
    encrypted secrets table — behind "Confirm it is you". The endpoint
    is validated https and public; when the destination (endpoint or
    bucket) changes, every credential must be sent again in the same
    request (docs/SPEC.md → Stored credentials)."""
    if get_config().env.sa_auth != "required":
        raise HTTPException(status_code=404, detail="not on a VPS")
    from app.auth.api import require_confirmed

    require_confirmed(request)
    secrets = _secrets(request)
    if secrets is None:
        raise HTTPException(status_code=503, detail="secrets unavailable")
    import json

    from app.ops.urls import UrlRejected, validate_public_https_url

    endpoint = str(body.get("endpoint") or "").strip()
    bucket = str(body.get("bucket") or "").strip()
    access_key_id = str(body.get("access_key_id") or "").strip()
    secret_access_key = str(body.get("secret_access_key") or "").strip()
    password = str(body.get("password") or "")

    # Destination change? Then all three credentials must come along in
    # the same request — stored secrets never travel to a new endpoint.
    stored_raw = secrets.get("backup_credentials")
    if stored_raw:
        try:
            stored = json.loads(stored_raw)
        except ValueError:
            stored = {}
        moved = (
            str(stored.get("endpoint") or "") != endpoint
            or str(stored.get("bucket") or "") != bucket
        )
        if moved and not (password and access_key_id and secret_access_key):
            from app.errors import ApiError, ErrorCode

            raise ApiError(
                422,
                ErrorCode.PASSWORD_REQUIRED,
                fields=[
                    {
                        "name": "backup_credentials",
                        "message": (
                            "the storage endpoint or bucket changed — "
                            "enter the backup password and both storage "
                            "keys again"
                        ),
                    }
                ],
            )

    errors: list[str] = []
    try:
        endpoint = validate_public_https_url(endpoint)
    except UrlRejected as exc:
        errors.append(f"endpoint: {exc}")
    for name, value in (
        ("bucket", bucket),
        ("access_key_id", access_key_id),
        ("secret_access_key", secret_access_key),
    ):
        if not value:
            errors.append(f"{name} is required")
    if not password:
        errors.append("backup password is required")
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    secrets.save(
        "backup_credentials",
        json.dumps(
            {
                "endpoint": endpoint,
                "bucket": bucket,
                "access_key_id": access_key_id,
                "secret_access_key": secret_access_key,
            }
        ),
    )
    secrets.save("backup_password", password)
    _notify(request, "settings")
    return {"ok": True}
