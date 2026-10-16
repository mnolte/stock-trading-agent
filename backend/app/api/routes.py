from __future__ import annotations

from datetime import datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_config
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import Cycle, DayTrade, Decision, Event, Order, Outcome, Position
from app.decide.prompt import load_prompt
from app.engine.executor import Executor
from app.market.calendar import CalendarProvider, WeekdayCalendarProvider
from app.market.clock import NY_TZ, UTC, SessionClock, SessionState, make_clock
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
            "next_cycle": session.next_cycle.isoformat() if session.next_cycle else None,
            "calendar_loaded": _calendar_loaded(request),
        },
        "equity_usd": _fmt_money(account["equity"]),
        "equity_eur": _fmt_money(_eur(account["equity"], request)),
        "today_usd": _fmt_signed(account["today"]),
        "today_eur": _fmt_signed(_eur(account["today"], request)),
        "regime": _regime_label(request),
        "kill_switch": kill_on,
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
        "no_call_reason": decision.no_call_reason,
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


# ----------------------------------------------------------------------
# Execution: kill switch, positions, orders, banners, acknowledgments


def _executor(request: Request) -> Executor:
    executor = getattr(request.app.state, "executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor unavailable")
    return executor  # type: ignore[no-any-return]


@router.post("/kill-switch")
async def kill_switch(request: Request, body: dict[str, object]) -> dict[str, object]:
    """Turn the kill switch on, or stop & flatten everything."""
    executor = _executor(request)
    action = str(body.get("action", "entries"))
    if action == "flatten":
        result = await executor.flatten()
        return {"kill_switch": True, **result}
    reason = str(body.get("reason") or "manual")
    await executor.activate_kill_switch(reason=reason, automatic=False)
    return {"kill_switch": True, "sold": [], "cancelled_orders": 0}


@router.post("/kill-switch/resume")
async def kill_switch_resume(
    request: Request, body: dict[str, object]
) -> dict[str, object]:
    """Resume after a kill switch; the typed reason is stored in the log."""
    executor = _executor(request)
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
        "deferred_exit": position.deferred_exit,
        "opened_at": position.opened_at.replace(tzinfo=UTC).isoformat(),
        "closed_at": (
            position.closed_at.replace(tzinfo=UTC).isoformat()
            if position.closed_at
            else None
        ),
        "realized_pnl_raw": (
            str(position.realized_pnl_raw)
            if position.realized_pnl_raw is not None
            else None
        ),
        "exit_reason": position.exit_reason,
    }


@router.get("/positions")
async def positions(request: Request) -> dict[str, object]:
    """Open positions plus today's closed ones; adopted P&L stays apart."""
    clock = getattr(request.app.state, "clock", None) or make_clock()
    today_ny = clock.now().astimezone(NY_TZ).date()
    market_data = getattr(request.app.state, "market_data", None)
    sessions = (
        market_data.recent_sessions(today_ny, 5) if market_data is not None else []
    )
    marks: dict[str, Decimal | None] = {}
    broker = getattr(request.app.state, "broker", None)
    if broker is not None:
        broker_positions = await broker.get_positions()
        if broker_positions.error is None:
            marks = {p.symbol: p.market_value for p in broker_positions.data}
    with get_session_maker()() as session:
        open_rows = session.execute(
            select(Position).where(Position.closed_at.is_(None))
        ).scalars().all()
        today = clock.now().astimezone(UTC).date()
        closed_rows = session.execute(
            select(Position).where(Position.closed_at.is_not(None))
        ).scalars().all()
        closed_today = [
            p
            for p in closed_rows
            if p.closed_at and p.closed_at.date() == today
        ]
        day_trades = 0
        if sessions:
            day_trades = session.execute(
                select(func.count(DayTrade.id)).where(
                    DayTrade.date >= _naive_utc(sessions[0].open)
                )
            ).scalar_one()
    return {
        "open": [_position_json(p, marks.get(p.symbol)) for p in open_rows],
        "closed_today": [_position_json(p) for p in closed_today],
        "day_trades_5d": day_trades,
    }


@router.get("/orders")
def orders(request: Request) -> dict[str, object]:
    """Latest order rows with leg and status."""
    with get_session_maker()() as session:
        rows = session.execute(
            select(Order).order_by(Order.id.desc()).limit(100)
        ).scalars().all()
    return {
        "orders": [
            {
                "id": row.id,
                "alpaca_order_id": row.alpaca_order_id,
                "client_order_id": row.client_order_id,
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
                    row.filled_at.replace(tzinfo=UTC).isoformat()
                    if row.filled_at
                    else None
                ),
            }
            for row in rows
        ]
    }


@router.get("/banners")
def banners(request: Request) -> dict[str, object]:
    """The banner strip under the header: kill switch, mismatches, brakes."""
    state = getattr(request.app.state, "exec_state", None)
    items: list[dict[str, object]] = []
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
                    ]
                ),
            )
            .order_by(Event.id.desc())
            .limit(20)
        ).scalars().all()
        for event in unacked:
            details = event.details or {}
            if event.event_type == "reconciliation":
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
            else:
                items.append(
                    {
                        "id": event.id,
                        "kind": event.event_type,
                        "level": "critical",
                        "message": "Position checks are failing. "
                        "Stops at Alpaca stay in place.",
                        "time": event.time.replace(tzinfo=UTC).isoformat(),
                        "actions": [],
                    }
                )

    if state is not None and state.kill_switch_on:
        since = (
            state.kill_switch_since.astimezone(UTC).isoformat()
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
    return {"items": items}


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
