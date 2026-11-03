"""The Ask-the-agent tools (docs/SPEC.md → Ask the agent → Tools).

Every tool is a pure read over the same tables and in-memory state the
GUI reads — nothing calls Alpaca, Finnhub or a model, and nothing has a
write path. Each returns a JSON dict with ``as_of``, ``ref`` on every
record and ``truncated`` + ``hint`` when its bound cut rows.

``calculate`` evaluates arithmetic with Decimal over an AST whitelist
(numbers, + - * / ( ), min, max, abs, round) — no names, no attribute
access. ``explain_rule`` searches the shipped docs/SPEC.md.
"""

from __future__ import annotations

import ast
import contextlib
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AskNote,
    Cycle,
    Decision,
    Fill,
    LlmCall,
    Order,
    Position,
    Review,
    SettingsVersion,
    ShadowDecision,
    Strategy,
    Verdict,
)
from app.llm.base import ConverseTool
from app.log_index import index_path
from app.market.clock import NY_TZ
from app.risk.math import open_risk

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.data.service import MarketDataService
    from app.engine.state import ExecutionState
    from app.market.calendar import CalendarProvider
    from app.market.clock import Clock
    from app.secrets_service import SecretsStore
    from app.signals.service import SignalService

logger = logging.getLogger(__name__)

SPEC_PATH = Path(__file__).resolve().parents[3] / "docs" / "SPEC.md"
SYMBOL_RE_LIST = ("^[A-Z]{1,6}(\\.[A-Z])?$",)


@dataclass
class AskDeps:
    """Everything a tool may read — assembled once per answer."""

    config: AppConfig
    clock: Clock
    session_factory: Any
    calendar: CalendarProvider | None = None
    exec_state: ExecutionState | None = None
    market_data: MarketDataService | None = None
    signals: SignalService | None = None
    secrets: SecretsStore | None = None
    notification_service: Any = None
    ops: Any = None

    def session(self) -> Session:
        return cast(Session, self.session_factory())


def _now(deps: AskDeps) -> datetime:
    return deps.clock.now()


def _as_of(deps: AskDeps) -> str:
    return _now(deps).astimezone(UTC).isoformat(timespec="seconds")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.isoformat(timespec="seconds")


def _dec(value: Any) -> str | None:
    return None if value is None else str(value)


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def _app_tz(config: AppConfig) -> ZoneInfo:
    return ZoneInfo(str(config.get("app", "timezone", default="Europe/Amsterdam")))


def _session_date(deps: AskDeps) -> str:
    return _now(deps).astimezone(NY_TZ).isoformat()


def _bounded(rows: list[dict[str, Any]], limit: int, hint: str, deps: AskDeps) -> dict[str, Any]:
    """Cut ``rows`` at ``limit``; flag truncation with a narrowing hint."""
    out: dict[str, Any] = {"as_of": _as_of(deps), "rows": rows[:limit], "count": len(rows[:limit])}
    if len(rows) > limit:
        out["truncated"] = True
        out["hint"] = hint
        out["total"] = len(rows)
    return out


def _period_bounds(
    period: str | None, days: int | None, deps: AskDeps | None = None
) -> tuple[datetime, datetime] | None:
    """``"2026-10-20..2026-10-25"`` or ``days`` → naive-UTC [start, end)."""
    today = (
        deps.clock.now().astimezone(UTC).date()
        if deps is not None
        else datetime.now(UTC).date()
    )
    if days is not None:
        start = today - timedelta(days=max(0, min(days, 3650)))
        return datetime.combine(start, dtime.min), datetime.combine(
            today + timedelta(days=1), dtime.min
        )
    if period:
        try:
            start_s, end_s = period.split("..", 1)
            start_d = date.fromisoformat(start_s.strip())
            end_d = date.fromisoformat(end_s.strip())
        except (ValueError, AttributeError):
            return None
        return datetime.combine(start_d, dtime.min), datetime.combine(
            end_d + timedelta(days=1), dtime.min
        )
    return None


# ---------------------------------------------------------------------
# argument models (Pydantic, bounded)


class _Base(BaseModel):
    model_config = {"extra": "forbid"}


class SymbolArg(_Base):
    symbol: str = Field(min_length=1, max_length=8)

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: str) -> str:
        import re

        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{1,6}(\.[A-Z])?", v):
            raise ValueError("not a symbol")
        return v


class DecisionArg(_Base):
    decision_id: int = Field(ge=1, le=10**12)


class PositionArg(_Base):
    position_id: int | None = Field(default=None, ge=1, le=10**12)
    symbol: str | None = Field(default=None, max_length=8)


class CycleArg(_Base):
    cycle_id: int = Field(ge=1, le=10**12)


class SearchOrdersArg(_Base):
    symbol: str | None = Field(default=None, max_length=8)
    side: str | None = Field(default=None, max_length=8)
    leg: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=32)
    period: str | None = Field(default=None, max_length=32)
    days: int | None = Field(default=None, ge=1, le=3650)
    limit: int = Field(default=50, ge=1, le=50)


class SearchDecisionsArg(_Base):
    symbol: str | None = Field(default=None, max_length=8)
    context: str | None = Field(default=None, max_length=16)
    model_action: str | None = Field(default=None, max_length=32)
    final_action: str | None = Field(default=None, max_length=32)
    change_reason: str | None = Field(default=None, max_length=64)
    strategy: str | None = Field(default=None, max_length=64)
    prompt_version: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=32)
    period: str | None = Field(default=None, max_length=32)
    days: int | None = Field(default=None, ge=1, le=3650)
    limit: int = Field(default=50, ge=1, le=50)


class PerformanceArg(_Base):
    days: int | None = Field(default=None, ge=1, le=3650)
    strategy: str | None = Field(default=None, max_length=64)


class TradesArg(_Base):
    symbol: str | None = Field(default=None, max_length=8)
    days: int | None = Field(default=None, ge=1, le=3650)
    limit: int = Field(default=100, ge=1, le=100)


class InsightsArg(_Base):
    strategy: str | None = Field(default=None, max_length=64)
    period: str = Field(default="last20", max_length=8)
    slice: str | None = Field(default=None, max_length=64)


class ReviewsArg(_Base):
    kind: str | None = Field(default=None, max_length=16)
    days: int | None = Field(default=None, ge=1, le=3650)
    limit: int = Field(default=20, ge=1, le=20)


class LogsArg(_Base):
    level: str | None = Field(default=None, max_length=16)
    component: str | None = Field(default=None, max_length=64)
    symbol: str | None = Field(default=None, max_length=8)
    text: str | None = Field(default=None, max_length=200)
    period: str | None = Field(default=None, max_length=32)
    days: int | None = Field(default=None, ge=1, le=90)
    limit: int = Field(default=50, ge=1, le=50)


class CallsArg(_Base):
    source: str | None = Field(default=None, max_length=32)
    kind: str | None = Field(default=None, max_length=16)
    symbol: str | None = Field(default=None, max_length=8)
    outcome: str | None = Field(default=None, max_length=16)
    model: str | None = Field(default=None, max_length=32)
    period: str | None = Field(default=None, max_length=32)
    days: int | None = Field(default=None, ge=1, le=3650)
    limit: int = Field(default=20, ge=1, le=20)


class SettingsArg(_Base):
    section: str | None = Field(default=None, max_length=32)
    history: int = Field(default=5, ge=0, le=20)


class MarketDataArg(_Base):
    symbol: str = Field(min_length=1, max_length=8)
    timeframe: str = Field(default="1h", max_length=4)  # "5m" | "1h" | "1d"
    around: str | None = Field(default=None, max_length=32)
    sessions: int = Field(default=3, ge=1, le=30)


class ExplainArg(_Base):
    term: str = Field(min_length=1, max_length=120)


class CalculateArg(_Base):
    expression: str = Field(min_length=1, max_length=400)


class NotesArg(_Base):
    text: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=20)


# ---------------------------------------------------------------------
# calculate — Decimal AST evaluator

_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}
def _fn_min(*a: Decimal) -> Decimal:
    return min(a)


def _fn_max(*a: Decimal) -> Decimal:
    return max(a)


def _fn_abs(a: Decimal) -> Decimal:
    return abs(a)


def _fn_round(a: Decimal, n: Decimal = Decimal(0)) -> Decimal:
    return a.quantize(Decimal(1).scaleb(-int(n)))


_FUNCS: dict[str, Callable[..., Decimal]] = {
    "min": _fn_min,
    "max": _fn_max,
    "abs": _fn_abs,
    "round": _fn_round,
}


def _eval(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int | float):
            return Decimal(str(node.value))
        raise ValueError("not a number")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Div) and right == 0:
            raise ValueError("division by zero")
        return cast(Callable[[Decimal, Decimal], Decimal], _BINOPS[type(node.op)])(left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _eval(node.operand)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _FUNCS.get(node.func.id)
        if fn is None or node.keywords:
            raise ValueError("not allowed")
        return fn(*(_eval(a) for a in node.args))
    raise ValueError("not allowed")


def calculate(expr: str) -> Decimal:
    """Evaluate ``expr`` with Decimal over the AST whitelist."""
    tree = ast.parse(expr, mode="eval")
    return _eval(tree)


# ---------------------------------------------------------------------
# explain_rule — passages from the shipped spec


def _spec_passages(term: str, limit: int = 3) -> list[str]:
    try:
        text = SPEC_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    needle = term.strip().lower()
    if not needle:
        return []
    # Paragraph-level passages: score each block by term hits (all words).
    words = [w for w in needle.split() if len(w) > 1]
    blocks: list[str] = []
    for part in text.split("\n\n"):
        chunk = part.strip()
        if not chunk or not all(w in chunk.lower() for w in words):
            continue
        blocks.append(chunk[:1200])
        if len(blocks) >= limit:
            break
    return blocks


# ---------------------------------------------------------------------
# tool implementations

ToolFn = Callable[[Session, AskDeps, Any], dict[str, Any]]


def _row_ref(kind: str, ident: Any) -> str:
    return f"{kind}:{ident}"


def get_status(session: Session, deps: AskDeps, _args: Any) -> dict[str, Any]:
    """Session, cycle, equity, today P&L, regime, paused/kill, banners,
    the Needs you list and the health line."""
    from app.attention import build_attention
    from app.market.clock import SessionClock

    state = (
        SessionClock(deps.clock, deps.config, deps.calendar).state()
        if deps.calendar is not None
        else None
    )
    exec_state = deps.exec_state
    account = getattr(exec_state, "account", None)
    regime = "unavailable"
    if deps.signals is not None:
        try:
            regime = deps.signals.regime(deps.clock.now()).label
        except Exception:  # noqa: BLE001
            regime = "unavailable"
    request = type("R", (), {"app": type("A", (), {"state": _state_ns(deps)})()})()
    attention = build_attention(request)
    open_count = session.execute(
        select(func.count(Position.id)).where(Position.closed_at.is_(None))
    ).scalar_one()
    return {
        "as_of": _as_of(deps),
        "session": _session_date(deps),
        "market_state": getattr(state, "state", None),
        "next_cycle": _iso(getattr(state, "next_cycle", None)),
        "equity_usd": _dec(account.equity if account else None),
        "cash_usd": _dec(account.cash if account else None),
        "paused": bool(getattr(exec_state, "paused", False)),
        "kill_switch": bool(getattr(exec_state, "kill_switch_on", False)),
        "kill_switch_reason": getattr(exec_state, "kill_switch_reason", None),
        "regime": regime,
        "open_positions": open_count,
        "needs_you": attention.get("items", []),
        "health": attention.get("health", {}),
        "ref": _row_ref("screen", "dashboard"),
    }


def _state_ns(deps: AskDeps) -> Any:
    """The app.state namespace the attention/health builders read."""
    return type(
        "S",
        (),
        {
            "exec_state": deps.exec_state,
            "clock": deps.clock,
            "secrets_store": deps.secrets,
            "notification_service": deps.notification_service,
            "broker": None,
            "ops": deps.ops,
            "calendar_store": deps.calendar,
        },
    )()


def list_positions(session: Session, deps: AskDeps, _args: Any) -> dict[str, Any]:
    """Open positions with the Positions-at-a-glance facts and the totals."""
    rows = (
        session.execute(
            select(Position).where(Position.closed_at.is_(None)).order_by(Position.symbol)
        )
        .scalars()
        .all()
    )
    marks: dict[str, tuple[Any, Any]] = {}
    for p in getattr(deps.exec_state, "last_broker_positions", None) or []:
        marks[p.symbol] = (p.market_value, p.current_price)
    equity = getattr(getattr(deps.exec_state, "account", None), "equity", None)
    out = []
    for p in rows:
        stop = p.current_stop_price
        mark, last = marks.get(p.symbol, (None, None))
        if_stop = (
            (p.qty * (stop - p.avg_entry_price))
            if stop is not None and p.avg_entry_price is not None
            else None
        )
        item: dict[str, Any] = {
            "ref": _row_ref("position", p.symbol),
            "id": p.id,
            "symbol": p.symbol,
            "qty": p.qty,
            "avg_entry_price": _dec(p.avg_entry_price),
            "current_stop_price": _dec(stop),
            "target_price": _dec(p.target_price),
            "last_price": _dec(last),
            "unrealized_pnl": _dec(mark - p.avg_entry_price * p.qty if mark else None),
            "if_stop_usd": _dec(if_stop),
            "locked_in": bool(stop is not None and stop > p.avg_entry_price),
            "opened_at": _iso(p.opened_at),
            "origin": p.origin,
        }
        if (
            if_stop is not None
            and stop is not None
            and p.initial_stop is not None
            and p.avg_entry_price is not None
            and p.avg_entry_price > p.initial_stop
        ):
            item["if_stop_r"] = _dec(
                (stop - p.avg_entry_price) / (p.avg_entry_price - p.initial_stop)
            )
        if last is not None and last > 0 and stop is not None:
            item["to_stop_pct"] = _dec((last - stop) / last * 100)
        if last is not None and last > 0 and p.target_price is not None:
            item["to_target_pct"] = _dec((p.target_price - last) / last * 100)
        out.append(item)
    if_stops = Decimal(0)
    for p in rows:
        if p.current_stop_price is not None:
            if_stops += p.qty * (p.current_stop_price - p.avg_entry_price)
    open_risk_usd = open_risk(
        (p.qty, p.avg_entry_price, p.current_stop_price)
        for p in rows
        if p.current_stop_price is not None
    )
    return {
        "as_of": _as_of(deps),
        "positions": out,
        "totals": {
            "if_stops_usd": _dec(if_stops),
            "if_stops_pct_equity": (_dec(if_stops / equity * 100) if equity else None),
            "open_risk_usd": _dec(open_risk_usd),
            "open_risk_pct": (_dec(open_risk_usd / equity * 100) if equity else None),
            "unprotected": sum(1 for p in rows if p.current_stop_price is None),
        },
        "prices_from": "last reconciled broker values",
    }


def get_position(session: Session, deps: AskDeps, args: PositionArg) -> dict[str, Any]:
    q = select(Position)
    if args.position_id is not None:
        q = q.where(Position.id == args.position_id)
    elif args.symbol:
        q = q.where(Position.symbol == args.symbol.upper()).order_by(Position.opened_at.desc())
    else:
        return {"error": "position_id or symbol required", "as_of": _as_of(deps)}
    p = session.execute(q.limit(1)).scalar_one_or_none()
    if p is None:
        return {"error": "position not found", "as_of": _as_of(deps)}
    legs = (
        session.execute(
            select(Order)
            .where(
                Order.symbol == p.symbol,
                Order.created_at >= p.opened_at - timedelta(days=2),
            )
            .order_by(Order.created_at)
        )
        .scalars()
        .all()
    )
    fills = (
        session.execute(
            select(Fill)
            .join(Order, Fill.order_id == Order.id)
            .where(Order.symbol == p.symbol, Order.leg.in_(["entry", "exit", "stop", "target"]))
            .order_by(Fill.filled_at)
        )
        .scalars()
        .all()
    )
    decisions = (
        session.execute(
            select(Decision)
            .where(Decision.symbol == p.symbol)
            .order_by(Decision.id.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    return {
        "as_of": _as_of(deps),
        "ref": _row_ref("position", p.symbol),
        "id": p.id,
        "symbol": p.symbol,
        "qty": p.qty,
        "avg_entry_price": _dec(p.avg_entry_price),
        "initial_stop": _dec(p.initial_stop),
        "current_stop_price": _dec(p.current_stop_price),
        "target_price": _dec(p.target_price),
        "opened_at": _iso(p.opened_at),
        "closed_at": _iso(p.closed_at),
        "realized_pnl_raw": _dec(p.realized_pnl_raw),
        "realized_pnl_adjusted": _dec(p.realized_pnl_adjusted),
        "exit_reason": p.exit_reason,
        "after_exit": {
            "best_price": _dec(p.after_exit_best_price),
            "best_at": _iso(p.after_exit_best_at),
            "close_price": _dec(p.after_exit_close_price),
            "status": p.after_exit_status,
        },
        "legs": [
            {
                "ref": _row_ref("order", o.id),
                "leg": o.leg,
                "side": o.side,
                "type": o.order_type,
                "qty": o.qty,
                "limit_price": _dec(o.limit_price),
                "stop_price": _dec(o.stop_price),
                "status": o.status,
                "filled_at": _iso(o.filled_at),
            }
            for o in legs
        ],
        "fills": [
            {
                "qty": f.qty,
                "price": _dec(f.price),
                "at": _iso(f.filled_at),
                "slippage": _dec(f.slippage),
            }
            for f in fills
        ],
        "decisions": [
            {
                "ref": _row_ref("decision", d.id),
                "id": d.id,
                "action": d.final_action,
                "context": d.context,
            }
            for d in decisions
        ],
    }


def search_orders(session: Session, deps: AskDeps, args: SearchOrdersArg) -> dict[str, Any]:
    q = select(Order)
    if args.symbol:
        q = q.where(Order.symbol == args.symbol.upper())
    if args.side:
        q = q.where(Order.side == args.side)
    if args.leg:
        q = q.where(Order.leg == args.leg)
    if args.status:
        q = q.where(Order.status == args.status)
    bounds = _period_bounds(args.period, args.days, deps)
    if bounds:
        q = q.where(Order.created_at >= bounds[0], Order.created_at < bounds[1])
    rows = (
        session.execute(q.order_by(Order.created_at.desc()).limit(args.limit + 1)).scalars().all()
    )
    items = [
        {
            "ref": _row_ref("order", o.id),
            "id": o.id,
            "symbol": o.symbol,
            "side": o.side,
            "leg": o.leg,
            "type": o.order_type,
            "qty": o.qty,
            "limit_price": _dec(o.limit_price),
            "stop_price": _dec(o.stop_price),
            "status": o.status,
            "created_at": _iso(o.created_at),
            "filled_at": _iso(o.filled_at),
        }
        for o in rows
    ]
    return _bounded(items, args.limit, "narrow with symbol, leg or a shorter period", deps)


def _decision_line(
    d: Decision | ShadowDecision, kind: str, cycle_time: datetime | None
) -> dict[str, Any]:
    validation = d.validation_result or {}
    gates = d.gates_result or {}
    return {
        "ref": _row_ref("decision", d.id) if kind == "live" else _row_ref("shadow_decision", d.id),
        "id": d.id,
        "kind": kind,
        "symbol": d.symbol,
        "context": d.context,
        "final_action": d.final_action,
        "model_action": validation.get("model_action"),
        "blocked_by": gates.get("blocked_by"),
        "change_reason": gates.get("change_reason") or gates.get("blocked_by"),
        "model": d.model,
        "prompt_version": d.prompt_version,
        "cycle_time": _iso(cycle_time),
    }


def search_decisions(session: Session, deps: AskDeps, args: SearchDecisionsArg) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    cycle_times = {c.id: c.scheduled_time for c in session.execute(select(Cycle)).scalars().all()}
    bounds = _period_bounds(args.period, args.days, deps)

    def want_strategy(strategy: str | None, is_shadow: bool, name: str | None) -> bool:
        if strategy is None:
            return True
        if strategy.lower() == "live":
            return not is_shadow
        return bool(is_shadow and name and name.lower() == strategy.lower())

    if want_strategy(args.strategy, False, None):
        q = select(Decision)
        if args.symbol:
            q = q.where(Decision.symbol == args.symbol.upper())
        if args.context:
            q = q.where(Decision.context == args.context)
        if args.final_action:
            q = q.where(Decision.final_action == args.final_action)
        if args.prompt_version:
            q = q.where(Decision.prompt_version == args.prompt_version)
        if args.model:
            q = q.where(Decision.model == args.model)
        if bounds:
            q = q.where(
                Decision.cycle_id.in_(
                    select(Cycle.id).where(
                        Cycle.scheduled_time >= bounds[0], Cycle.scheduled_time < bounds[1]
                    )
                )
            )
        for d in (
            session.execute(q.order_by(Decision.id.desc()).limit(args.limit + 1)).scalars().all()
        ):
            line = _decision_line(d, "live", cycle_times.get(d.cycle_id))
            if args.model_action and line["model_action"] != args.model_action:
                continue
            if (
                args.change_reason
                and args.change_reason.lower() not in str(line["change_reason"] or "").lower()
            ):
                continue
            items.append(line)
    # shadows
    strategies = {s.id: s.name for s in session.execute(select(Strategy)).scalars().all()}
    if args.strategy is None or args.strategy.lower() != "live":
        qs = select(ShadowDecision)
        if args.symbol:
            qs = qs.where(ShadowDecision.symbol == args.symbol.upper())
        if args.context:
            qs = qs.where(ShadowDecision.context == args.context)
        if args.final_action:
            qs = qs.where(ShadowDecision.final_action == args.final_action)
        if args.prompt_version:
            qs = qs.where(ShadowDecision.prompt_version == args.prompt_version)
        if bounds:
            qs = qs.where(
                ShadowDecision.cycle_id.in_(
                    select(Cycle.id).where(
                        Cycle.scheduled_time >= bounds[0], Cycle.scheduled_time < bounds[1]
                    )
                )
            )
        for sd in (
            session.execute(qs.order_by(ShadowDecision.id.desc()).limit(args.limit + 1))
            .scalars()
            .all()
        ):
            name = strategies.get(sd.strategy_id)
            if not want_strategy(args.strategy, True, name):
                continue
            line = _decision_line(sd, f"shadow:{name}", cycle_times.get(sd.cycle_id))
            if args.model_action and line["model_action"] != args.model_action:
                continue
            items.append(line)
    items.sort(key=lambda r: r["cycle_time"] or "", reverse=True)
    return _bounded(items, args.limit, "narrow with symbol, period or strategy", deps)


def get_decision(session: Session, deps: AskDeps, args: DecisionArg) -> dict[str, Any]:
    d = session.get(Decision, args.decision_id)
    if d is None:
        sh = session.get(ShadowDecision, args.decision_id)
        if sh is not None:
            return _decision_detail_shadow(session, deps, sh)
        return {"error": "decision not found", "as_of": _as_of(deps)}
    snapshot = d.input_snapshot or {}
    outcome = session.execute(
        select(__import__("app.db.models", fromlist=["Outcome"]).Outcome).where(
            __import__("app.db.models", fromlist=["Outcome"]).Outcome.decision_id == d.id
        )
    ).scalar_one_or_none()
    verdict = session.execute(
        select(Verdict).where(Verdict.decision_id == d.id)
    ).scalar_one_or_none()
    call = session.execute(
        select(LlmCall).where(LlmCall.decision_id == d.id).order_by(LlmCall.id.desc()).limit(1)
    ).scalar_one_or_none()
    return {
        "as_of": _as_of(deps),
        "ref": _row_ref("decision", d.id),
        "id": d.id,
        "symbol": d.symbol,
        "context": d.context,
        "final_action": d.final_action,
        "no_call_reason": d.no_call_reason,
        "model": d.model,
        "prompt_version": d.prompt_version,
        "rules_result": d.rules_result,
        "model_output": d.model_output,
        "validation_result": d.validation_result,
        "gates_result": d.gates_result,
        "levels": d.levels,
        "options": d.options,
        "chosen_option": d.chosen_option,
        "explanation_check": d.explanation_check,
        "selection": d.selection,
        "snapshot_key_numbers": {
            "price": snapshot.get("price"),
            "checks": snapshot.get("checks"),
            "position": snapshot.get("position"),
        },
        "tokens": d.tokens,
        "cost": _dec(d.cost),
        "outcome": (
            {
                "price_1h": _dec(outcome.price_1h),
                "price_1session": _dec(outcome.price_1session),
                "price_5sessions": _dec(outcome.price_5sessions),
                "first_touch": outcome.first_touch,
                "option_touches": outcome.option_touches,
            }
            if outcome
            else None
        ),
        "verdict": (
            {"verdict": verdict.verdict, "reason": verdict.reason, "source": verdict.source}
            if verdict
            else None
        ),
        "llm_call": (
            {"ref": _row_ref("call", call.id), "outcome": call.outcome, "reasons": call.reasons}
            if call
            else None
        ),
    }


def _decision_detail_shadow(session: Session, deps: AskDeps, d: ShadowDecision) -> dict[str, Any]:
    strategy = session.get(Strategy, d.strategy_id)
    return {
        "as_of": _as_of(deps),
        "ref": _row_ref("shadow_decision", d.id),
        "id": d.id,
        "symbol": d.symbol,
        "context": d.context,
        "strategy": strategy.name if strategy else d.strategy_id,
        "final_action": d.final_action,
        "model": d.model,
        "prompt_version": d.prompt_version,
        "rules_result": d.rules_result,
        "model_output": d.model_output,
        "validation_result": d.validation_result,
        "gates_result": d.gates_result,
        "missed": getattr(d, "missed", None),
    }


def get_cycle(session: Session, deps: AskDeps, args: CycleArg) -> dict[str, Any]:
    c = session.get(Cycle, args.cycle_id)
    if c is None:
        return {"error": "cycle not found", "as_of": _as_of(deps)}
    decisions = session.execute(select(Decision).where(Decision.cycle_id == c.id)).scalars().all()
    calls = session.execute(
        select(func.count(LlmCall.id)).where(LlmCall.cycle_id == c.id)
    ).scalar_one()
    by_action: dict[str, int] = {}
    for d in decisions:
        by_action[d.final_action] = by_action.get(d.final_action, 0) + 1
    return {
        "as_of": _as_of(deps),
        "ref": _row_ref("cycle", c.id),
        "id": c.id,
        "kind": c.kind,
        "scheduled_time": _iso(c.scheduled_time),
        "status": c.status,
        "duration_seconds": c.duration_seconds,
        "errors": c.errors,
        "data_source_health": c.data_source_health,
        "selection": c.selection,
        "model_calls": calls,
        "decisions_by_action": by_action,
        "decisions": [
            {
                "ref": _row_ref("decision", d.id),
                "id": d.id,
                "symbol": d.symbol,
                "action": d.final_action,
            }
            for d in decisions
        ],
    }


def performance(session: Session, deps: AskDeps, args: PerformanceArg) -> dict[str, Any]:
    """Return/drawdown/win-rate figures via the shared compute."""
    from app.engine.performance import compute_performance

    data = compute_performance(
        session,
        config=deps.config,
        market_data=deps.market_data,
        calendar=deps.calendar or _weekday_cal(),
        clock=deps.clock,
        live_equity=getattr(getattr(deps.exec_state, "account", None), "equity", None),
    )
    return {"as_of": _as_of(deps), "ref": _row_ref("screen", "performance"), "performance": data}


def _weekday_cal() -> Any:
    from app.market.calendar import WeekdayCalendarProvider

    return WeekdayCalendarProvider()


def list_trades(session: Session, deps: AskDeps, args: TradesArg) -> dict[str, Any]:
    q = select(Position).where(Position.closed_at.is_not(None))
    if args.symbol:
        q = q.where(Position.symbol == args.symbol.upper())
    if args.days:
        q = q.where(
            Position.closed_at >= datetime.now(UTC).replace(tzinfo=None) - timedelta(days=args.days)
        )
    rows = (
        session.execute(q.order_by(Position.closed_at.desc()).limit(args.limit + 1)).scalars().all()
    )
    items = []
    for p in rows:
        r_value = None
        if p.initial_stop and p.avg_entry_price > p.initial_stop and p.realized_pnl_raw is not None:
            r_value = p.realized_pnl_raw / (p.qty * (p.avg_entry_price - p.initial_stop))
        items.append(
            {
                "ref": _row_ref("trade", p.id),
                "id": p.id,
                "symbol": p.symbol,
                "qty": p.qty,
                "entry": _dec(p.avg_entry_price),
                "opened_at": _iso(p.opened_at),
                "closed_at": _iso(p.closed_at),
                "exit_reason": p.exit_reason,
                "realized_pnl": _dec(p.realized_pnl_raw),
                "realized_pnl_adjusted": _dec(p.realized_pnl_adjusted),
                "r": _dec(r_value),
                "after_exit_best": _dec(p.after_exit_best_price),
            }
        )
    return _bounded(items, args.limit, "narrow with symbol or fewer days", deps)


def insights(session: Session, deps: AskDeps, args: InsightsArg) -> dict[str, Any]:
    from app.insights.service import latest_snapshot

    strategy_id = None
    if args.strategy and args.strategy.lower() != "live":
        row = session.execute(
            select(Strategy).where(Strategy.name == args.strategy)
        ).scalar_one_or_none()
        if row is None:
            return {"error": f"unknown strategy {args.strategy!r}", "as_of": _as_of(deps)}
        strategy_id = row.id
    period = args.period if args.period in ("run", "last20", "all") else "last20"
    snap = latest_snapshot(session, strategy_id=strategy_id, period=period)
    if snap is None:
        return {"error": "no insights snapshot yet", "as_of": _as_of(deps)}
    data = dict(snap.data or {})
    if args.slice:
        slices = data.get("slices") or {}
        data = {"slice": slices.get(args.slice), "slice_name": args.slice}
        if data["slice"] is None:
            data["available_slices"] = sorted(slices.keys())[:50]
    return {
        "as_of": _as_of(deps),
        "ref": _row_ref("screen", "insights"),
        "session_date": snap.session_date.isoformat(),
        "period": period,
        "insights": data,
    }


def compare_strategies(session: Session, deps: AskDeps, _args: Any) -> dict[str, Any]:
    from app.engine.performance import compute_performance
    from app.shadows.compare import compare

    perf = compute_performance(
        session,
        config=deps.config,
        market_data=deps.market_data,
        calendar=deps.calendar or _weekday_cal(),
        clock=deps.clock,
        live_equity=getattr(getattr(deps.exec_state, "account", None), "equity", None),
    )
    data = compare(session, config=deps.config, perf=perf)
    rows = [{**r, "ref": _row_ref("screen", "strategies")} for r in (data.get("rows") or [])]
    return {"as_of": _as_of(deps), "rows": rows, "ref": _row_ref("screen", "strategies")}


def readiness(session: Session, deps: AskDeps, _args: Any) -> dict[str, Any]:
    from app.readiness.service import payload

    data = payload(session, config=deps.config, clock=deps.clock)
    return {"as_of": _as_of(deps), "ref": _row_ref("screen", "readiness"), "readiness": data}


def reviews(session: Session, deps: AskDeps, args: ReviewsArg) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    q = select(Review)
    if args.kind:
        q = q.where(Review.kind == args.kind)
    if args.days:
        q = q.where(
            Review.created_at >= datetime.now(UTC).replace(tzinfo=None) - timedelta(days=args.days)
        )
    for r in (
        session.execute(q.order_by(Review.created_at.desc()).limit(args.limit)).scalars().all()
    ):
        rows.append(
            {
                "ref": _row_ref("review", r.id),
                "kind": r.kind,
                "period": r.period,
                "source": r.source,
                "reason": r.reason,
                "text": r.text[:2000],
                "created_at": _iso(r.created_at),
            }
        )
    verdicts = (
        session.execute(select(Verdict).order_by(Verdict.updated_at.desc()).limit(args.limit))
        .scalars()
        .all()
    )
    return {
        "as_of": _as_of(deps),
        "reviews": rows,
        "verdicts": [
            {
                "ref": _row_ref("decision", v.decision_id),
                "verdict": v.verdict,
                "source": v.source,
                "updated_at": _iso(v.updated_at),
            }
            for v in verdicts
        ],
    }


def search_logs(session: Session, deps: AskDeps, args: LogsArg) -> dict[str, Any]:
    """Log lines from the index — already scrubbed at write time."""
    db = index_path(deps.config)
    if not db.exists():
        return {"as_of": _as_of(deps), "rows": [], "error": "log index not built"}
    conds, params = [], {}
    if args.level:
        conds.append("level = :level")
        params["level"] = args.level.upper()
    if args.component:
        conds.append("logger LIKE :logger")
        params["logger"] = f"%{args.component}%"
    if args.symbol:
        conds.append("symbol = :symbol")
        params["symbol"] = args.symbol.upper()
    if args.text:
        safe = args.text.replace('"', " ")[:200]
        conds.append("id IN (SELECT rowid FROM log_lines_fts WHERE log_lines_fts MATCH :match)")
        params["match"] = '"' + safe.replace(" ", '" "') + '"'
    bounds = _period_bounds(args.period, args.days, deps)
    if bounds:
        conds.append("ts >= :start AND ts < :end")
        params["start"] = bounds[0].isoformat()
        params["end"] = bounds[1].isoformat()
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            cur = conn.execute(
                f"SELECT id, ts, level, logger, event, message, symbol, fingerprint "  # noqa: S608 - only fixed fragments join `where`; every value is bound
                f"FROM log_lines {where} ORDER BY ts DESC LIMIT :lim",
                {**params, "lim": args.limit + 1},
            )
            rows = cur.fetchall()
    except sqlite3.Error:
        logger.warning("ask search_logs failed to read the index")
        return {"as_of": _as_of(deps), "rows": [], "error": "log index read failed"}
    items = [
        {
            "ref": _row_ref("log", r[0]),
            "ts": r[1],
            "level": r[2],
            "component": r[3],
            "event": r[4],
            "message": r[5][:800],
            "symbol": r[6],
            "fingerprint": r[7],
        }
        for r in rows
    ]
    return _bounded(items, args.limit, "narrow with level, component or text", deps)


def search_model_calls(session: Session, deps: AskDeps, args: CallsArg) -> dict[str, Any]:
    q = select(LlmCall)
    if args.source:
        q = q.where(LlmCall.source.in_(args.source.split(",")))
    if args.kind:
        q = q.where(LlmCall.kind == args.kind)
    if args.symbol:
        q = q.where(LlmCall.symbol == args.symbol.upper())
    if args.outcome:
        q = q.where(LlmCall.outcome == args.outcome)
    if args.model:
        q = q.where(LlmCall.model == args.model)
    bounds = _period_bounds(args.period, args.days, deps)
    if bounds:
        q = q.where(LlmCall.sent_at >= bounds[0], LlmCall.sent_at < bounds[1])
    rows = (
        session.execute(q.order_by(LlmCall.sent_at.desc(), LlmCall.id.desc()).limit(args.limit + 1))
        .scalars()
        .all()
    )
    items = [
        {
            "ref": _row_ref("call", c.id),
            "id": c.id,
            "sent_at": _iso(c.sent_at),
            "source": c.source,
            "kind": c.kind,
            "symbol": c.symbol,
            "model": c.model,
            "prompt_version": c.prompt_version,
            "prompt_hash": c.prompt_hash,
            "tokens_in": c.tokens_in,
            "tokens_out": c.tokens_out,
            "cost": _dec(c.cost),
            "latency_ms": c.latency_ms,
            "outcome": c.outcome,
            "reasons": c.reasons,
            "error_type": c.error_type,
            # the response text, never the prompt text (version+hash only)
            "raw_response": (c.raw_response or "")[:4000] or None,
        }
        for c in rows
    ]
    return _bounded(items, args.limit, "narrow with source, symbol or outcome", deps)


def settings(session: Session, deps: AskDeps, args: SettingsArg) -> dict[str, Any]:
    """Current values, defaults, settings history, key/channel status."""
    effective = deps.config.effective()
    yaml_defaults = deps.config.yaml
    sections: dict[str, Any] = {}
    wanted = {args.section} if args.section else set(effective)
    for name in sorted(wanted):
        if name not in effective:
            continue
        sections[name] = {
            "current": effective.get(name),
            "defaults": yaml_defaults.get(name),
        }
    history = []
    if args.history:
        for row in (
            session.execute(
                select(SettingsVersion).order_by(SettingsVersion.version.desc()).limit(args.history)
            )
            .scalars()
            .all()
        ):
            history.append(
                {"version": row.version, "changed_at": _iso(row.changed_at), "reason": row.reason}
            )
    keys: dict[str, Any] = {}
    if deps.secrets is not None:
        from app.secrets_service import GROUP_ORDER

        for group in GROUP_ORDER:
            status = deps.secrets.group_status(group)
            keys[group] = {
                "status": status.get("status"),
                "detail": status.get("detail"),
            }
    return {
        "as_of": _as_of(deps),
        "sections": sections,
        "history": history,
        "keys": keys,
        "channels": (
            deps.notification_service.smtp_status() if deps.notification_service is not None else {}
        ),
    }


def market_data(session: Session, deps: AskDeps, args: MarketDataArg) -> dict[str, Any]:
    """Bars and indicators from the stored bars only."""
    if deps.market_data is None:
        return {"error": "market data unavailable", "as_of": _as_of(deps)}
    symbol = args.symbol.upper()
    now = _now(deps).astimezone(NY_TZ)
    items: list[dict[str, Any]] = []
    sessions = (
        deps.market_data.recent_sessions(now.date(), max(args.sessions, 1))
        if hasattr(deps.market_data, "recent_sessions")
        else []
    )
    if args.timeframe == "1d":
        daily = deps.market_data.get_daily_bars(symbol, sessions)
        for bd in daily[-120:]:
            items.append(
                {
                    "date": bd.date.isoformat(),
                    "open": _dec(bd.open),
                    "high": _dec(bd.high),
                    "low": _dec(bd.low),
                    "close": _dec(bd.close),
                    "volume": bd.volume,
                }
            )
    elif args.timeframe == "5m":
        end = now
        if args.around:
            with contextlib.suppress(ValueError):
                end = datetime.fromisoformat(args.around).astimezone(NY_TZ)
        start = end - timedelta(hours=8)
        bars5 = deps.market_data.get_5m_bars(
            symbol, start.replace(tzinfo=None), end.replace(tzinfo=None)
        )
        for b5 in bars5[-120:]:
            items.append(
                {
                    "start": _iso(b5.start),
                    "open": _dec(b5.open),
                    "high": _dec(b5.high),
                    "low": _dec(b5.low),
                    "close": _dec(b5.close),
                    "volume": b5.volume,
                }
            )
    else:
        bars1 = deps.market_data.get_1h_bars_range(symbol, sessions)
        for b1 in bars1[-120:]:
            items.append(
                {
                    "session": _iso(b1.start),
                    "start": _iso(b1.start),
                    "open": _dec(b1.open),
                    "high": _dec(b1.high),
                    "low": _dec(b1.low),
                    "close": _dec(b1.close),
                    "volume": b1.volume,
                    "complete": b1.complete,
                }
            )
    return {
        "as_of": _as_of(deps),
        "ref": _row_ref("symbol", symbol),
        "symbol": symbol,
        "timeframe": args.timeframe,
        "bars": items,
        "count": len(items),
    }


def explain_rule(session: Session, deps: AskDeps, args: ExplainArg) -> dict[str, Any]:
    passages = _spec_passages(args.term)
    return {
        "as_of": _as_of(deps),
        "term": args.term,
        "passages": passages,
        "source": "docs/SPEC.md",
        "ref": _row_ref("screen", "readiness"),
        "count": len(passages),
    }


def calculate_tool(session: Session, deps: AskDeps, args: CalculateArg) -> dict[str, Any]:
    try:
        value = calculate(args.expression)
    except (ValueError, InvalidOperation, ZeroDivisionError, SyntaxError) as exc:
        return {"error": f"cannot evaluate: {exc}", "as_of": _as_of(deps)}
    return {
        "as_of": _as_of(deps),
        "expression": args.expression,
        "result": str(value),
    }


def notes(session: Session, deps: AskDeps, args: NotesArg) -> dict[str, Any]:
    """Marc's saved notes, newest first, with date, source and refs."""
    q = select(AskNote).order_by(AskNote.created_at.desc())
    rows = session.execute(q.limit(args.limit + 1)).scalars().all()
    if args.text:
        needle = args.text.lower()
        rows = [r for r in rows if needle in r.text.lower()]
    items = []
    for n in rows:
        source = None
        if n.source_thread_id is not None:
            thread = session.get(AskThread, n.source_thread_id)
            source = thread.title if thread is not None else None
        items.append(
            {
                "ref": _row_ref("note", n.id),
                "id": n.id,
                "text": n.text,
                "date": _iso(n.created_at),
                "author": "you",
                "source_chat": source,
                "references": n.references or [],
            }
        )
    return _bounded(items, args.limit, "filter with text", deps)


# ---------------------------------------------------------------------
# registry — name → (args model, fn, description, step label)

from app.db.models import AskThread  # noqa: E402  (late import for notes)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: ToolFn
    label: str  # step text template, {} = a short arg summary

    def schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()

    def converse_tool(self) -> ConverseTool:
        return ConverseTool(name=self.name, description=self.description, schema=self.schema())


def _noop_args() -> type[BaseModel]:
    class Empty(_Base):
        pass

    return Empty


TOOLS: dict[str, ToolSpec] = {
    "get_status": ToolSpec(
        "get_status",
        "Session, cycle, equity, today's P&L, regime, paused or kill switch, "
        "banners, the Needs you list and the health line.",
        _noop_args(),
        get_status,
        "Read the agent's status and Needs you",
    ),
    "list_positions": ToolSpec(
        "list_positions",
        "Open positions with if-the-stop-hits, distances to stop and target, "
        "planned exit and order legs, plus the totals line.",
        _noop_args(),
        list_positions,
        "Read open positions and their stops",
    ),
    "get_position": ToolSpec(
        "get_position",
        "One open or closed position: fills, legs, stop moves, exit, R and the decisions on it.",
        PositionArg,
        get_position,
        "Read the {} position",
    ),
    "search_orders": ToolSpec(
        "search_orders",
        "Orders by period, symbol, side, leg and status (up to 50).",
        SearchOrdersArg,
        search_orders,
        "Searched orders{}",
    ),
    "search_decisions": ToolSpec(
        "search_decisions",
        "Decisions by period, symbol, context, model action, final action, "
        "change reason, strategy (live or a shadow), prompt version and "
        "model; one line each (up to 50).",
        SearchDecisionsArg,
        search_decisions,
        "Searched decisions{}",
    ),
    "get_decision": ToolSpec(
        "get_decision",
        "One decision: the snapshot's checks and key numbers, options, the "
        "model's output, validation, gates, code changes, warnings, "
        "outcome, option touches, verdict and its model call.",
        DecisionArg,
        get_decision,
        "Read decision {}",
    ),
    "get_cycle": ToolSpec(
        "get_cycle",
        "One cycle: what ran, calls, decisions by action, duration and errors.",
        CycleArg,
        get_cycle,
        "Read cycle {}",
    ),
    "performance": ToolSpec(
        "performance",
        "Return, against SPY, win rate, average R, drawdown and adjusted "
        "P&L, by regime and symbol, for a period and strategy.",
        PerformanceArg,
        performance,
        "Computed performance{}",
    ),
    "list_trades": ToolSpec(
        "list_trades",
        "Closed trades with entry, exit, R, reason and after-exit moves (up to 100).",
        TradesArg,
        list_trades,
        "Read closed trades{}",
    ),
    "insights": ToolSpec(
        "insights",
        "Headline, findings, funnel, one slice and calibration for a strategy and period.",
        InsightsArg,
        insights,
        "Read Insights{}",
    ),
    "compare_strategies": ToolSpec(
        "compare_strategies",
        "The Strategies table and disagreements for a period.",
        _noop_args(),
        compare_strategies,
        "Read the strategies comparison",
    ),
    "readiness": ToolSpec(
        "readiness",
        "Every criterion with value, target and status, and the manual items.",
        _noop_args(),
        readiness,
        "Read the readiness checklist",
    ),
    "reviews": ToolSpec(
        "reviews",
        "Weekly reviews, briefings and verdicts (drafts and confirmed) by "
        "kind and period (up to 20).",
        ReviewsArg,
        reviews,
        "Read reviews and verdicts",
    ),
    "search_logs": ToolSpec(
        "search_logs",
        "Log lines by level, component, symbol, period and text, already scrubbed (up to 50).",
        LogsArg,
        search_logs,
        "Searched the logs{}",
    ),
    "search_model_calls": ToolSpec(
        "search_model_calls",
        "LLM call rows by the inspector's filters; the response text, "
        "never the prompt text (version and hash only; up to 20).",
        CallsArg,
        search_model_calls,
        "Searched model calls{}",
    ),
    "settings": ToolSpec(
        "settings",
        "Current values of a section with their defaults, the settings "
        "history and key/channel status. Never returns a key, password "
        "or token.",
        SettingsArg,
        settings,
        "Read settings{}",
    ),
    "market_data": ToolSpec(
        "market_data",
        "Bars for a symbol around a time, from the stored bars only "
        "(5m, 1h or 1d; up to 120 bars).",
        MarketDataArg,
        market_data,
        "Read stored {} bars",
    ),
    "explain_rule": ToolSpec(
        "explain_rule",
        "The spec passages for a term or rule (for example edge_too_small "
        "or the sector cap) from the shipped docs/SPEC.md (up to 3).",
        ExplainArg,
        explain_rule,
        "Read the spec about {}",
    ),
    "calculate": ToolSpec(
        "calculate",
        "An arithmetic expression on given numbers: + - * / ( ) and min, "
        "max, abs, round. No names. Use it for every number you work out.",
        CalculateArg,
        calculate_tool,
        "Calculated {}",
    ),
    "notes": ToolSpec(
        "notes",
        "Marc's saved notes with date, source chat and references, newest "
        "first; optionally filtered by text (up to 20).",
        NotesArg,
        notes,
        "Read your notes{}",
    ),
}


def run_tool(
    session: Session, deps: AskDeps, name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Validate arguments and run one tool; (result, error)."""
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"unknown tool {name!r}", "as_of": _as_of(deps)}, "unknown_tool"
    try:
        args = spec.args_model.model_validate(arguments or {})
    except ValidationError as exc:
        return (
            {
                "error": f"bad arguments: {exc.errors()[0].get('msg', 'invalid')}",
                "as_of": _as_of(deps),
            },
            "bad_arguments",
        )
    try:
        return spec.fn(session, deps, args), None
    except Exception:  # noqa: BLE001 - a failing tool answers its error
        logger.exception("ask tool %s failed", name)
        return {"error": "the lookup failed", "as_of": _as_of(deps)}, "tool_error"


def step_label(name: str, arguments: dict[str, Any]) -> str:
    """The drawer's short step text, e.g. 'Searched decisions for JPM'."""
    spec = TOOLS.get(name)
    if spec is None:
        return name
    bits: list[str] = []
    for key in (
        "symbol",
        "decision_id",
        "position_id",
        "cycle_id",
        "expression",
        "term",
        "strategy",
        "text",
        "timeframe",
    ):
        if arguments.get(key) is not None:
            bits.append(str(arguments[key]))
            break
    if arguments.get("days"):
        bits.append(f"last {arguments['days']} days")
    return spec.label.format(" " + " ".join(bits) if bits else "")
