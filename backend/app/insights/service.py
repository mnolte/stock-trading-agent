"""Load the insights inputs and store the daily snapshots (PR 22).

The daily job runs after the readiness snapshot: one row per strategy
(live has ``strategy_id`` NULL) and period (``run``, ``last20``, ``all``)
in ``insights_snapshots``. The screen reads the latest row and never
recomputes per request (docs/SPEC.md, Insights — Storage).

Every input the pure function needs is assembled here: decisions,
positions, orders and equity for the period, plus the stored market bars
for the overnight split and the skip scoring. Nothing in this module
touches the network — bars come from the local caches — and each
snapshot write commits before the next compute starts (hard rule 13).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.db.base import get_session_maker
from app.db.models import (
    Bar1d,
    Bar5m,
    CorporateAction,
    Cycle,
    Decision,
    EquitySnapshot,
    Fill,
    InsightsSnapshot,
    Order,
    Outcome,
    Position,
    Run,
    ShadowDecision,
    ShadowEquity,
    ShadowPosition,
    Strategy,
)
from app.engine.performance import _regime_label
from app.engine.trades import _dividends
from app.insights.compute import (
    Bar5m as IBar5m,
)
from app.insights.compute import (
    DailyBar,
    EquityDay,
    InsightsDecision,
    InsightsOrder,
    InsightsSkip,
    InsightsTrade,
    compute_insights,
)
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, Clock
from app.shadows.strategy import NON_ARCHIVED, strategy_start

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

PERIODS = ("run", "last20", "all")
LAST20_SESSIONS = 20


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _dec(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _cycle_time(scheduled: datetime) -> str:
    return _aware(scheduled).astimezone(NY_TZ).strftime("%H:%M")


def _window(
    calendar: CalendarProvider,
    session_date: date,
    period: str,
    since: datetime | None,
) -> datetime | None:
    """Naive-UTC lower bound for the period's rows (None = unbounded)."""
    if period == "all":
        return None
    if period == "run":
        return _naive(since) if since is not None else None
    # last20: the 20 most recent trading days ending at session_date.
    days = [session_date]
    while len(days) < LAST20_SESSIONS:
        prev = calendar.previous_trading_day(days[0])
        if prev is None:
            break
        days.insert(0, prev.date)
    return _naive(
        datetime.combine(days[0], dtime.min, tzinfo=NY_TZ).astimezone(UTC)
    )


def _session_dates(
    session: Session, calendar: CalendarProvider
) -> list[date]:
    """Trading days the calendar knows — the skip-scoring horizon."""
    mapping = getattr(calendar, "sessions_map", None)
    days = sorted(mapping) if isinstance(mapping, dict) else []
    if days:
        return list(days)
    # Fallback: derive from stored 1d bars of the benchmark symbol.
    return [
        row[0]
        for row in session.execute(
            select(Bar1d.date).where(Bar1d.symbol == "SPY").order_by(Bar1d.date)
        ).all()
    ]


def _daily_bars(session: Session, symbol: str) -> list[DailyBar]:
    return [
        DailyBar(b.date, b.open, b.high, b.low, b.close)
        for b in session.execute(
            select(Bar1d).where(Bar1d.symbol == symbol).order_by(Bar1d.date)
        ).scalars()
    ]


def _bars_5m(
    session: Session, symbol: str, start: datetime, end: datetime
) -> list[IBar5m]:
    return [
        IBar5m(_aware(b.start), b.open, b.high, b.low, b.close)
        for b in session.execute(
            select(Bar5m)
            .where(
                Bar5m.symbol == symbol,
                Bar5m.start >= _naive(start),
                Bar5m.start <= _naive(end),
            )
            .order_by(Bar5m.start)
        ).scalars()
    ]


def _basis(
    decision: Decision | ShadowDecision,
) -> tuple[str | None, str | None]:
    """Stop and target basis of the chosen option (decision.v2)."""
    chosen = decision.chosen_option
    options = decision.options or {}
    entries = options.get("options") if isinstance(options, dict) else None
    if chosen and isinstance(entries, list):
        for option in entries:
            if isinstance(option, dict) and option.get("id") == chosen:
                return (
                    str(option.get("stop_basis") or "") or None,
                    str(option.get("target_basis") or "") or None,
                )
    if chosen:
        return None, None
    return None, None


def _dims_from_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    h = snap.get("indicators_1h") or {}
    d = snap.get("indicators_daily") or {}
    dte = snap.get("days_to_earnings")
    return {
        "vwap": _dec(h.get("vwap")),
        "ema20_1h": _dec(h.get("ema20")),
        "atr_1h": _dec(h.get("atr14")),
        "rs_vs_spy_pct": _dec(h.get("rs_vs_spy_pct")),
        "atr_percentile": _dec(d.get("atr_percentile")),
        "opening_gap_pct": _dec(d.get("gap_pct")),
        "days_to_earnings": dte if isinstance(dte, int) else None,
        "earnings_known": dte != "unavailable",
        "regime": _regime_label(snap.get("regime")),
    }


def _model_action(row: Decision | ShadowDecision) -> str | None:
    result = row.validation_result or {}
    action = result.get("model_action")
    if isinstance(action, str):
        return action
    output = row.model_output or {}
    raw = output.get("action")
    return raw if isinstance(raw, str) else None


def _failed_checks(row: Decision | ShadowDecision) -> tuple[str, ...]:
    result: dict[str, Any] = row.validation_result or {}
    checks = result.get("checks") or []
    if not isinstance(checks, list):
        return ()
    return tuple(
        str(c.get("name"))
        for c in checks
        if isinstance(c, dict) and c.get("passed") is False
    )


def _gate(row: Decision | ShadowDecision) -> str | None:
    gates = row.gates_result or {}
    gate = gates.get("blocked_by")
    return str(gate) if gate else None


def _skip_dims(
    symbol: str,
    snap: dict[str, Any],
    scheduled: datetime,
    sector: str | None,
    opening_gap_pct: Decimal = Decimal("1"),
) -> dict[str, Any]:
    """Slice keys for a skip (same names as trade dims)."""
    h = snap.get("indicators_1h") or {}
    d = snap.get("indicators_daily") or {}
    dte = snap.get("days_to_earnings")
    price = _dec(snap.get("price"))
    vwap = _dec(h.get("vwap"))
    ema = _dec(h.get("ema20"))
    atr = _dec(h.get("atr14"))
    ny = _aware(scheduled).astimezone(NY_TZ)
    gap = _dec(d.get("gap_pct"))
    out: dict[str, Any] = {
        "entry_cycle": ny.strftime("%H:%M"),
        "weekday": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[ny.weekday()],
        "sector": sector or "Unknown",
        "symbol": symbol,
        "regime": _regime_label(snap.get("regime")),
        "days_to_earnings": (
            None
            if dte == "unavailable"
            else "none scheduled"
            if dte is None
            else "4–10 days"
            if dte <= 10
            else "11–20 days"
            if dte <= 20
            else "21+ days"
        )
        if isinstance(dte, int) or dte is None or dte == "unavailable"
        else None,
        "opening_gap": (
            None
            if gap is None
            else f"down >{opening_gap_pct:g}%"
            if gap < -opening_gap_pct
            else f"up >{opening_gap_pct:g}%"
            if gap > opening_gap_pct
            else f"within {opening_gap_pct:g}%"
        ),
        "entry_vs_vwap": (
            None
            if vwap is None or price is None
            else "above VWAP"
            if price >= vwap
            else "below VWAP"
        ),
        "pullback_depth": (
            None
            if ema is None or atr is None or atr <= 0 or price is None
            else "under 0.5 ATR"
            if abs(price - ema) / atr < Decimal("0.5")
            else "0.5–1.5 ATR"
            if abs(price - ema) / atr <= Decimal("1.5")
            else "over 1.5 ATR"
        ),
        "confidence": None,
        "prompt_model": None,
        "stop_target_basis": None,
        "sessions_held": None,
        "volatility": None,
        "strength_vs_spy": None,
        "spy_that_day": None,
    }
    return out


def _spy_day_pct(
    spy_daily: list[DailyBar], day: date
) -> Decimal | None:
    bar = next((b for b in spy_daily if b.date == day), None)
    if bar is None or not bar.open:
        return None
    return (bar.close - bar.open) / bar.open * 100


# ----------------------------------------------------------------------
# live inputs


def _live_inputs(
    session: Session,
    *,
    config: AppConfig,
    calendar: CalendarProvider,
    run: Run,
    window_start: datetime | None,
    sectors: dict[str, str],
) -> dict[str, Any]:
    """Everything ``compute_insights`` needs for the live strategy."""
    dec_q = (
        select(Decision, Cycle.scheduled_time)
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .where(Cycle.kind == "cycle")
        .order_by(Decision.id)
    )
    if window_start is not None:
        dec_q = dec_q.where(Cycle.scheduled_time >= window_start)
    dec_rows = session.execute(dec_q).all()

    pos_q = select(Position).where(Position.closed_at.is_not(None))
    if window_start is not None:
        pos_q = pos_q.where(Position.closed_at >= window_start)
    positions = session.execute(pos_q.order_by(Position.closed_at)).scalars().all()

    decision_ids = {
        p.entry_decision_id for p in positions if p.entry_decision_id
    }
    entry_decisions = {
        d.id: d
        for d in session.execute(
            select(Decision).where(Decision.id.in_(decision_ids or {0}))
        ).scalars()
    }
    cycle_times = {
        row[0]: row[1]
        for row in session.execute(
            select(Decision.id, Cycle.scheduled_time)
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .where(Decision.id.in_(decision_ids or {0}))
        ).all()
    }
    outcomes = {
        o.decision_id: o
        for o in session.execute(
            select(Outcome).where(Outcome.decision_id.in_(decision_ids or {0}))
        ).scalars()
    }
    dividends_rows = session.execute(
        select(CorporateAction).where(CorporateAction.action_type == "dividend")
    ).scalars().all()

    # Exit legs per position: the orders that closed it.
    orders = session.execute(
        select(Order).where(
            Order.decision_id.in_(decision_ids or {0}),
            Order.leg.in_(("stop", "target", "exit")),
        )
    ).scalars().all()
    fills_by_order: dict[int, list[Fill]] = {}
    if orders:
        for fill in session.execute(
            select(Fill).where(Fill.order_id.in_([o.id for o in orders]))
        ).scalars():
            fills_by_order.setdefault(fill.order_id, []).append(fill)

    def _fill_of(order: Order) -> tuple[Decimal | None, int | None]:
        fills = fills_by_order.get(order.id) or []
        if not fills:
            return None, None
        qty = sum(f.qty for f in fills)
        price = sum((f.price * f.qty for f in fills), Decimal(0)) / qty
        return price, qty

    {
        p.entry_decision_id: p for p in positions if p.entry_decision_id
    }
    open_positions = session.execute(
        select(func.count(Position.id)).where(Position.closed_at.is_(None))
    ).scalar_one()

    trades: list[InsightsTrade] = []
    dividends_usd = Decimal(0)
    dividend_positions = 0
    spy_daily_cache = _daily_bars(session, "SPY")
    for p in positions:
        exit_price = (
            p.avg_entry_price + p.realized_pnl_raw / p.qty
            if p.realized_pnl_raw is not None and p.qty
            else None
        )
        opened_ny = _aware(p.opened_at).astimezone(NY_TZ).date()
        closed_ny = _aware(p.closed_at).astimezone(NY_TZ).date()  # type: ignore[arg-type]
        div = _dividends(dividends_rows, p.symbol, opened_ny, closed_ny, p.qty)
        if div:
            dividend_positions += 1
            dividends_usd += div
        adjusted = (
            p.realized_pnl_adjusted + div
            if p.realized_pnl_adjusted is not None
            else None
        )
        decision = (
            entry_decisions.get(p.entry_decision_id)
            if p.entry_decision_id is not None
            else None
        )
        snap = (decision.input_snapshot or {}) if decision else {}
        dims = _dims_from_snapshot(snap) if snap else {}
        stop_basis = target_basis = None
        if decision is not None:
            stop_basis, target_basis = _basis(decision)
        outcome = (
            outcomes.get(p.entry_decision_id)
            if p.entry_decision_id is not None
            else None
        )
        # Sessions held: trading days from entry to exit (inclusive).
        held_days = 0
        cursor = opened_ny
        while cursor <= closed_ny and held_days < 100:
            if calendar.session_for(cursor) is not None:
                held_days += 1
            cursor += timedelta(days=1)
        stop_leg = stop_fill = None
        for order in orders:
            if order.decision_id != p.entry_decision_id:
                continue
            if order.leg == "stop":
                price, _qty = _fill_of(order)
                if price is not None:
                    stop_leg = order.stop_price
                    stop_fill = price
        after_move = None
        if (
            p.after_exit_status == "done"
            and p.after_exit_close_price is not None
            and exit_price
        ):
            after_move = (
                (p.after_exit_close_price - exit_price) / exit_price * 100
            )
        trades.append(
            InsightsTrade(
                id=p.id,
                symbol=p.symbol,
                qty=p.qty,
                entry=p.avg_entry_price,
                exit_price=exit_price,
                initial_stop=(
                    p.initial_stop
                    if p.initial_stop is not None
                    else (
                        _dec((decision.model_output or {}).get("stop_price"))
                        if decision
                        else None
                    )
                ),
                adjusted_pnl=adjusted,
                exit_reason=p.exit_reason,
                opened_at=_aware(p.opened_at),
                closed_at=_aware(p.closed_at),  # type: ignore[arg-type]
                first_touch=(
                    outcome.first_touch
                    if outcome is not None
                    and outcome.first_touch in ("target", "stop")
                    else (
                        "target"
                        if p.exit_reason == "target"
                        else "stop"
                        if p.exit_reason == "stop"
                        else "neither"
                    )
                ),
                entry_cycle=(
                    _cycle_time(cycle_times[p.entry_decision_id])
                    if p.entry_decision_id in cycle_times
                    else None
                ),
                weekday=opened_ny.weekday(),
                sector=sectors.get(p.symbol),
                regime=dims.get("regime"),
                days_to_earnings=dims.get("days_to_earnings"),
                earnings_known=dims.get("earnings_known", True),
                opening_gap_pct=dims.get("opening_gap_pct"),
                entry_vwap=dims.get("vwap"),
                ema20_1h=dims.get("ema20_1h"),
                atr_1h=dims.get("atr_1h"),
                rs_vs_spy_pct=dims.get("rs_vs_spy_pct"),
                spy_day_pct=_spy_day_pct(spy_daily_cache, opened_ny),
                atr_percentile=dims.get("atr_percentile"),
                confidence=_dec((decision.model_output or {}).get("confidence"))
                if decision
                else None,
                stop_basis=stop_basis,
                target_basis=target_basis,
                sessions_held=held_days or None,
                prompt_version=decision.prompt_version if decision else None,
                model=decision.model if decision else None,
                stop_leg_price=stop_leg,
                stop_fill_price=stop_fill,
                entry_ny=opened_ny,
                exit_ny=closed_ny,
                after_exit_move_pct=after_move,
            )
        )

    decisions: list[InsightsDecision] = []
    skips: list[InsightsSkip] = []
    filled_decision_ids = {
        row[0]
        for row in session.execute(
            select(Order.decision_id)
            .where(
                Order.decision_id.is_not(None),
                Order.leg == "entry",
                Order.status.in_(("filled", "partially_filled")),
            )
            .group_by(Order.decision_id)
        ).all()
    }
    entered_ids = {
        d.id
        for d, _s in dec_rows
        if d.context == "candidate" and d.final_action in ("enter", "blocked")
    }
    any_entry_orders = {
        row[0]
        for row in session.execute(
            select(Order.decision_id)
            .where(
                Order.decision_id.in_(entered_ids or {0}),
                Order.leg == "entry",
            )
            .group_by(Order.decision_id)
        ).all()
    }
    for d, scheduled in dec_rows:
        model_action = _model_action(d)
        entry_filled: bool | None = None
        if d.id in entered_ids:
            if d.id in filled_decision_ids:
                entry_filled = True
            elif d.id in any_entry_orders or d.final_action == "enter":
                entry_filled = False
        decisions.append(
            InsightsDecision(
                id=d.id,
                context=d.context,
                final_action=d.final_action,
                no_call_reason=d.no_call_reason,
                model_action=model_action,
                code_checks_failed=_failed_checks(d),
                gate=_gate(d),
                entry_filled=entry_filled,
            )
        )
        if (
            d.context == "candidate"
            and model_action == "skip"
            and d.no_call_reason is None
        ):
            snap_skip: dict[str, Any] = d.input_snapshot or {}
            h = snap_skip.get("indicators_1h") or {}
            skips.append(
                InsightsSkip(
                    id=d.id,
                    symbol=d.symbol,
                    decided_at=_aware(scheduled),
                    session_date=_aware(scheduled).astimezone(NY_TZ).date(),
                    cycle_time=_cycle_time(scheduled),
                    price=_dec(snap_skip.get("price")),
                    atr_1h=_dec(h.get("atr14")),
                    dims=_skip_dims(
                        d.symbol, snap_skip, scheduled, sectors.get(d.symbol)
                    ),
                )
            )

    # Entry orders for fill rate + slippage.
    entry_orders = session.execute(
        select(Order, Decision, Cycle.scheduled_time)
        .join(Decision, Order.decision_id == Decision.id)
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .where(Order.leg == "entry", Order.decision_id.is_not(None))
        .order_by(Order.id)
    ).all() if dec_rows else []
    order_ids = [o.id for o, _d, _s in entry_orders]
    entry_fills: dict[int, list[Fill]] = {}
    if order_ids:
        for fill in session.execute(
            select(Fill).where(Fill.order_id.in_(order_ids))
        ).scalars():
            entry_fills.setdefault(fill.order_id, []).append(fill)
    iorders: list[InsightsOrder] = []
    for order, decision, scheduled in entry_orders:
        if window_start is not None and order.created_at < window_start:
            continue
        fills = entry_fills.get(order.id) or []
        qty = sum(f.qty for f in fills)
        fill_price = (
            sum((f.price * f.qty for f in fills), Decimal(0)) / qty if qty else None
        )
        iorders.append(
            InsightsOrder(
                leg="entry",
                cycle_time=_cycle_time(scheduled),
                decision_price=_dec((decision.input_snapshot or {}).get("price")),
                intended=order.limit_price,
                filled=bool(fills),
                fill_price=fill_price,
                fill_qty=qty or None,
            )
        )
    # Exit legs for slippage, one per position's closing order.
    for p in positions:
        for order in orders:
            if order.decision_id != p.entry_decision_id:
                continue
            price, exit_qty = _fill_of(order)
            if price is None:
                continue
            iorders.append(
                InsightsOrder(
                    leg=order.leg,
                    cycle_time=None,
                    decision_price=None,
                    intended=(
                        order.stop_price
                        if order.leg == "stop"
                        else order.limit_price
                    ),
                    filled=True,
                    fill_price=price,
                    fill_qty=exit_qty,
                    exit_reason=order.leg,
                )
            )
        # stop_breached and other decision-less exits: the position's own
        # exit price against its current stop.
        if p.exit_reason == "stop_breached" and exit_price is not None:
            iorders.append(
                InsightsOrder(
                    leg="exit",
                    cycle_time=None,
                    decision_price=None,
                    intended=p.current_stop_price,
                    filled=True,
                    fill_price=exit_price,
                    fill_qty=p.qty,
                    exit_reason="stop_breached",
                )
            )

    # Equity days and model cost.
    eq_q = (
        select(EquitySnapshot)
        .where(EquitySnapshot.time >= run.started_at)
        .order_by(EquitySnapshot.time)
    )
    if window_start is not None:
        eq_q = eq_q.where(EquitySnapshot.time >= window_start)
    by_day: dict[date, EquityDay] = {}
    for esnap in session.execute(eq_q).scalars():
        day = _aware(esnap.time).astimezone(NY_TZ).date()
        by_day[day] = EquityDay(day, esnap.equity, esnap.cash)

    cost_q = (
        select(func.sum(Decision.cost))
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .where(Decision.model != "none")
    )
    if window_start is not None:
        cost_q = cost_q.where(Cycle.scheduled_time >= window_start)
    model_cost = session.execute(cost_q).scalar_one() or Decimal(0)

    # Adjusted run return from the shared performance function (this run).
    from app.engine.performance import compute_performance
    from app.market.clock import make_clock

    perf = compute_performance(
        session,
        config=config,
        market_data=None,
        calendar=calendar,
        clock=make_clock(config),
    )
    summary = cast(dict[str, Any], perf.get("summary") or {})
    return_pct = (
        _dec(summary.get("adjusted_pct")) if not perf.get("empty") else None
    )

    return {
        "trades": trades,
        "decisions": decisions,
        "skips": skips,
        "orders": iorders,
        "equity_daily": list(by_day.values()),
        "spy_daily": spy_daily_cache,
        "return_pct": return_pct,
        "model_cost": Decimal(model_cost),
        "dividends_usd": dividends_usd,
        "dividend_positions": dividend_positions,
        "open_positions": int(open_positions),
        "kind": "llm",
        "entry_cycles": None,
    }


# ----------------------------------------------------------------------
# shadow inputs


def _shadow_snapshot(session: Session, row: ShadowDecision) -> dict[str, Any]:
    """The input snapshot a shadow decided on: the live decision's stored
    snapshot, or its own for a symbol only it holds."""
    if row.input_snapshot:
        return row.input_snapshot
    if row.live_decision_id:
        live = session.get(Decision, row.live_decision_id)
        if live is not None:
            return live.input_snapshot or {}
    return {}


def _shadow_inputs(
    session: Session,
    *,
    config: AppConfig,
    calendar: CalendarProvider,
    strategy: Strategy,
    run: Run,
    window_start: datetime | None,
    sectors: dict[str, str],
) -> dict[str, Any]:
    """Everything ``compute_insights`` needs for one shadow strategy."""
    start = _naive(strategy_start(strategy, run.started_at))
    if window_start is None or window_start < start:
        window_start = start

    dec_q = (
        select(ShadowDecision, Cycle.scheduled_time)
        .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
        .where(
            ShadowDecision.strategy_id == strategy.id,
            Cycle.scheduled_time >= window_start,
        )
        .order_by(ShadowDecision.id)
    )
    dec_rows = session.execute(dec_q).all()

    pos_q = select(ShadowPosition).where(
        ShadowPosition.strategy_id == strategy.id,
        ShadowPosition.status == "closed",
        ShadowPosition.closed_at.is_not(None),
        ShadowPosition.closed_at >= window_start,
    )
    positions = session.execute(
        pos_q.order_by(ShadowPosition.closed_at)
    ).scalars().all()

    decision_ids = {
        p.entry_decision_id for p in positions if p.entry_decision_id
    }
    entry_decisions = {
        d.id: d
        for d in session.execute(
            select(ShadowDecision).where(ShadowDecision.id.in_(decision_ids or {0}))
        ).scalars()
    }
    dec_cycle_times = {
        row[0]: row[1]
        for row in session.execute(
            select(ShadowDecision.id, Cycle.scheduled_time)
            .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
            .where(ShadowDecision.id.in_(decision_ids or {0}))
        ).all()
    }
    {
        d.id: d
        for d in session.execute(
            select(Decision).where(
                Decision.id.in_(
                    {
                        row.live_decision_id
                        for row in entry_decisions.values()
                        if row.live_decision_id
                    }
                    or {0}
                )
            )
        ).scalars()
    }

    dividends_rows = session.execute(
        select(CorporateAction).where(CorporateAction.action_type == "dividend")
    ).scalars().all()
    spy_daily_cache = _daily_bars(session, "SPY")

    trades: list[InsightsTrade] = []
    dividends_usd = Decimal(0)
    dividend_positions = 0
    for p in positions:
        opened_ny = _aware(p.opened_at).astimezone(NY_TZ).date()  # type: ignore[arg-type]
        closed_ny = _aware(p.closed_at).astimezone(NY_TZ).date()  # type: ignore[arg-type]
        div = _dividends(dividends_rows, p.symbol, opened_ny, closed_ny, p.qty)
        if div:
            dividend_positions += 1
            dividends_usd += div
        decision = (
            entry_decisions.get(p.entry_decision_id)
            if p.entry_decision_id is not None
            else None
        )
        snap = _shadow_snapshot(session, decision) if decision else {}
        dims = _dims_from_snapshot(snap) if snap else {}
        stop_basis = target_basis = None
        if decision is not None:
            stop_basis, target_basis = _basis(decision)
        held_days = 0
        cursor = opened_ny
        while cursor <= closed_ny and held_days < 100:
            if calendar.session_for(cursor) is not None:
                held_days += 1
            cursor += timedelta(days=1)
        after_move = None
        if p.exit_reason == "earnings_exit" and p.exit_price:
            later = [
                b
                for b in _daily_bars(session, p.symbol)
                if b.date > closed_ny
            ]
            session_n = 0
            for b in later:
                if calendar.session_for(b.date) is not None:
                    session_n += 1
                    if session_n == 5:
                        after_move = (
                            (b.close - p.exit_price) / p.exit_price * 100
                        )
                        break
        trades.append(
            InsightsTrade(
                id=p.id,
                symbol=p.symbol,
                qty=p.qty,
                entry=p.entry if p.entry is not None else Decimal(0),
                exit_price=p.exit_price,
                initial_stop=p.initial_stop,
                adjusted_pnl=p.pnl,
                exit_reason=p.exit_reason,
                opened_at=_aware(p.opened_at),  # type: ignore[arg-type]
                closed_at=_aware(p.closed_at),  # type: ignore[arg-type]
                first_touch=(
                    "target"
                    if p.exit_reason == "target"
                    else "stop"
                    if p.exit_reason in ("stop", "stop_breached")
                    else "neither"
                ),
                entry_cycle=(
                    _cycle_time(dec_cycle_times[p.entry_decision_id])
                    if p.entry_decision_id in dec_cycle_times
                    else None
                ),
                weekday=opened_ny.weekday(),
                sector=sectors.get(p.symbol),
                regime=dims.get("regime"),
                days_to_earnings=dims.get("days_to_earnings"),
                earnings_known=dims.get("earnings_known", True),
                opening_gap_pct=dims.get("opening_gap_pct"),
                entry_vwap=dims.get("vwap"),
                ema20_1h=dims.get("ema20_1h"),
                atr_1h=dims.get("atr_1h"),
                rs_vs_spy_pct=dims.get("rs_vs_spy_pct"),
                spy_day_pct=_spy_day_pct(spy_daily_cache, opened_ny),
                atr_percentile=dims.get("atr_percentile"),
                confidence=_dec((decision.model_output or {}).get("confidence"))
                if decision
                else None,
                stop_basis=stop_basis,
                target_basis=target_basis,
                sessions_held=held_days or None,
                prompt_version=decision.prompt_version if decision else None,
                model=decision.model if decision else None,
                stop_leg_price=p.stop if p.exit_reason == "stop" else None,
                stop_fill_price=p.exit_price if p.exit_reason == "stop" else None,
                entry_ny=opened_ny,
                exit_ny=closed_ny,
                after_exit_move_pct=after_move,
            )
        )

    decisions: list[InsightsDecision] = []
    skips: list[InsightsSkip] = []
    entered = {p.entry_decision_id: p for p in positions}
    pending_entries = {
        p.entry_decision_id
        for p in session.execute(
            select(ShadowPosition).where(
                ShadowPosition.strategy_id == strategy.id,
                ShadowPosition.status == "pending",
            )
        ).scalars()
        if p.entry_decision_id
    }
    not_filled = {
        p.entry_decision_id
        for p in session.execute(
            select(ShadowPosition).where(
                ShadowPosition.strategy_id == strategy.id,
                ShadowPosition.status == "not_filled",
            )
        ).scalars()
        if p.entry_decision_id
    }
    for d, scheduled in dec_rows:
        model_action = _model_action(d)
        entry_filled: bool | None = None
        if d.id in entered:
            entry_filled = True
        elif d.id in not_filled or d.id in pending_entries or d.final_action == "enter":
            entry_filled = False
        decisions.append(
            InsightsDecision(
                id=d.id,
                context=d.context,
                final_action=d.final_action,
                no_call_reason=d.no_call_reason,
                model_action=model_action,
                code_checks_failed=_failed_checks(d),
                gate=_gate(d),
                entry_filled=entry_filled,
                missed=d.missed,
            )
        )
        if (
            d.context == "candidate"
            and model_action == "skip"
            and not d.missed
        ):
            snap = _shadow_snapshot(session, d)
            h = snap.get("indicators_1h") or {}
            skips.append(
                InsightsSkip(
                    id=d.id,
                    symbol=d.symbol,
                    decided_at=_aware(scheduled),
                    session_date=_aware(scheduled).astimezone(NY_TZ).date(),
                    cycle_time=_cycle_time(scheduled),
                    price=_dec(snap.get("price")),
                    atr_1h=_dec(h.get("atr14")),
                    dims=_skip_dims(
                        d.symbol, snap, scheduled, sectors.get(d.symbol)
                    ),
                )
            )

    # Orders: entries from shadow positions, exits from closed trades.
    iorders: list[InsightsOrder] = []
    entry_positions = session.execute(
        select(ShadowPosition).where(
            ShadowPosition.strategy_id == strategy.id,
            ShadowPosition.created_at >= window_start,
            ShadowPosition.entry_decision_id.is_not(None),
        )
    ).scalars().all()
    for p in entry_positions:
        dec_sched = dec_cycle_times.get(p.entry_decision_id or 0)
        iorders.append(
            InsightsOrder(
                leg="entry",
                cycle_time=_cycle_time(dec_sched) if dec_sched else None,
                decision_price=p.limit_price,
                intended=p.limit_price,
                filled=p.entry is not None,
                fill_price=p.entry,
                fill_qty=p.qty if p.entry is not None else None,
            )
        )
    for p in positions:
        intended = (
            p.stop if p.exit_reason == "stop" else p.target
        )
        leg = (
            "stop"
            if p.exit_reason in ("stop", "stop_breached")
            else "target"
            if p.exit_reason == "target"
            else "exit"
        )
        iorders.append(
            InsightsOrder(
                leg=leg,
                cycle_time=None,
                decision_price=None,
                intended=intended,
                filled=True,
                fill_price=p.exit_price,
                fill_qty=p.qty,
                exit_reason=p.exit_reason,
            )
        )

    eq_q = (
        select(ShadowEquity)
        .where(
            ShadowEquity.strategy_id == strategy.id,
            ShadowEquity.time >= start,
        )
        .order_by(ShadowEquity.time)
    )
    if window_start > start:
        eq_q = eq_q.where(ShadowEquity.time >= window_start)
    by_day: dict[date, EquityDay] = {}
    for esnap in session.execute(eq_q).scalars():
        day = _aware(esnap.time).astimezone(NY_TZ).date()
        by_day[day] = EquityDay(day, esnap.equity, esnap.cash)
    equity_days = list(by_day.values())

    cost_q = (
        select(func.sum(ShadowDecision.cost))
        .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
        .where(
            ShadowDecision.strategy_id == strategy.id,
            Cycle.scheduled_time >= window_start,
        )
    )
    model_cost = session.execute(cost_q).scalar_one() or Decimal(0)

    start_equity = run.starting_equity
    return_pct = None
    if equity_days and start_equity:
        return_pct = (
            (equity_days[-1].equity - start_equity) / start_equity * 100
        )

    open_positions = session.execute(
        select(func.count(ShadowPosition.id)).where(
            ShadowPosition.strategy_id == strategy.id,
            ShadowPosition.status == "open",
        )
    ).scalar_one()

    return {
        "trades": trades,
        "decisions": decisions,
        "skips": skips,
        "orders": iorders,
        "equity_daily": equity_days,
        "spy_daily": spy_daily_cache,
        "return_pct": return_pct,
        "model_cost": Decimal(model_cost),
        "dividends_usd": dividends_usd,
        "dividend_positions": dividend_positions,
        "open_positions": int(open_positions),
        "kind": strategy.kind,
        "entry_cycles": (
            (strategy.overrides or {}).get("entry_cycles")
        ),
    }


# ----------------------------------------------------------------------
# snapshot rows


def _sectors(config: AppConfig, session: Session) -> dict[str, str]:
    """Symbol → sector, from the pool rows first, then the config map."""
    from app.db.models import UniversePoolRow

    sectors: dict[str, str] = {}
    for row in session.execute(select(UniversePoolRow)).scalars():
        sectors[row.symbol] = row.sector
    for symbol, sector in (config.get("universe", "sectors", default={}) or {}).items():
        sectors.setdefault(str(symbol), str(sector))
    return sectors


def compute_snapshot(
    session: Session,
    *,
    config: AppConfig,
    calendar: CalendarProvider,
    session_date: date,
    strategy: Strategy | None,
    period: str,
    run: Run,
) -> dict[str, Any]:
    """One strategy, one period: build inputs and run the pure function."""
    sectors = _sectors(config, session)
    if strategy is None:
        window = _window(calendar, session_date, period, run.started_at)
        inputs = _live_inputs(
            session,
            config=config,
            calendar=calendar,
            run=run,
            window_start=window,
            sectors=sectors,
        )
    else:
        window = _window(calendar, session_date, period, None)
        inputs = _shadow_inputs(
            session,
            config=config,
            calendar=calendar,
            strategy=strategy,
            run=run,
            window_start=window,
            sectors=sectors,
        )
    cfg = config.get("insights", default={}) or {}
    skip_cfg = cfg.get("skip_score") or {}
    result = compute_insights(
        inputs["trades"],
        inputs["decisions"],
        inputs["skips"],
        inputs["orders"],
        equity_daily=inputs["equity_daily"],
        spy_daily=inputs["spy_daily"],
        daily_bars_for=lambda s: _daily_bars(session, s),
        bars_5m_for=lambda s, a, b: _bars_5m(session, s, a, b),
        session_dates=_session_dates(session, calendar),
        return_pct=inputs["return_pct"],
        model_cost=inputs["model_cost"],
        dividends_usd=inputs["dividends_usd"],
        dividend_positions=inputs["dividend_positions"],
        open_positions=inputs["open_positions"],
        strategy_kind=inputs["kind"],
        session_date=session_date,
        display_tz=str(config.get("app", "timezone", default="Europe/Amsterdam")),
        cycles=list(
            config.get("schedule", "cycles", default=[])
        ),
        last_cycle_no_entries=bool(
            config.get("schedule", "last_cycle_no_entries", default=True)
        ),
        entry_cycles=inputs["entry_cycles"],
        min_trades=int(cfg.get("min_trades", 30)),
        early_trades=int(cfg.get("early_trades", 10)),
        finding_sigma=Decimal(str(cfg.get("finding_sigma", 2.0))),
        range_z=Decimal(str(cfg.get("range_z", 1.64))),
        skip_stop_atr=Decimal(str(skip_cfg.get("stop_atr", 2.0))),
        skip_target_r=Decimal(str(skip_cfg.get("target_r", 2.0))),
        skip_sessions=int(skip_cfg.get("sessions", 5)),
        gap_through_stop_r=Decimal(str(cfg.get("gap_through_stop_r", 0.1))),
        spy_flat_pct=Decimal(str(cfg.get("spy_flat_pct", 0.3))),
        opening_gap_pct=Decimal(str(cfg.get("opening_gap_pct", 1.0))),
        entry_limit_offset_pct=Decimal(
            str(config.get("execution", "entry_limit_offset_pct", default=0.05))
        ),
        entry_timeout_minutes=int(
            config.get("execution", "entry_timeout_minutes", default=10)
        ),
    )
    return result


def _finding_keys(data: dict[str, Any]) -> set[str]:
    return {
        f"{f['dimension']}|{f['slice']}"
        for f in data.get("findings") or []
        if f.get("status") == "finding"
    }


def _with_finding_history(
    session: Session,
    *,
    strategy_id: int | None,
    period: str,
    session_date: date,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Annotate each finding with when it first appeared (for "new since"
    and "held for"), from earlier snapshots of the same strategy+period."""
    first_seen: dict[str, date] = {}
    rows = session.execute(
        select(InsightsSnapshot)
        .where(
            InsightsSnapshot.session_date < session_date,
            InsightsSnapshot.period == period,
            (
                InsightsSnapshot.strategy_id.is_(None)
                if strategy_id is None
                else InsightsSnapshot.strategy_id == strategy_id
            ),
        )
        .order_by(InsightsSnapshot.session_date)
    ).scalars().all()
    for row in rows:
        for key in _finding_keys(row.data or {}):
            first_seen.setdefault(key, row.session_date)
    for finding in data.get("findings") or []:
        key = f"{finding['dimension']}|{finding['slice']}"
        seen = first_seen.get(key, session_date)
        finding["first_seen"] = seen.isoformat()
        finding["held_days"] = (session_date - seen).days
        finding["new"] = seen == session_date
    return data


def write_snapshots(
    session: Session,
    *,
    config: AppConfig,
    calendar: CalendarProvider,
    clock: Clock,
    session_date: date,
) -> int:
    """Compute and store every strategy's three period snapshots for one
    session date. Returns rows written."""
    run = session.execute(
        select(Run).order_by(Run.id.desc()).limit(1)
    ).scalar_one_or_none()
    if run is None:
        return 0
    strategies: list[Strategy | None] = [None] + list(
        session.execute(
            select(Strategy)
            .where(Strategy.status.in_(NON_ARCHIVED))
            .order_by(Strategy.id)
        ).scalars()
    )
    now = _naive(clock.now())
    written = 0
    for strategy in strategies:
        strategy_id = strategy.id if strategy is not None else None
        for period in PERIODS:
            exists = session.execute(
                select(InsightsSnapshot.id).where(
                    InsightsSnapshot.session_date == session_date,
                    InsightsSnapshot.period == period,
                    (
                        InsightsSnapshot.strategy_id.is_(None)
                        if strategy_id is None
                        else InsightsSnapshot.strategy_id == strategy_id
                    ),
                )
            ).first()
            if exists:
                continue
            data = compute_snapshot(
                session,
                config=config,
                calendar=calendar,
                session_date=session_date,
                strategy=strategy,
                period=period,
                run=run,
            )
            data = _with_finding_history(
                session,
                strategy_id=strategy_id,
                period=period,
                session_date=session_date,
                data=data,
            )
            session.add(
                InsightsSnapshot(
                    session_date=session_date,
                    strategy_id=strategy_id,
                    period=period,
                    computed_at=now,
                    data=data,
                )
            )
            session.flush()
            written += 1
        # Commit each strategy before the next compute: no open write
        # transaction spans another strategy's bar reads (hard rule 13).
        session.commit()
    return written


def due_session_dates(
    session: Session, *, calendar: CalendarProvider, clock: Clock
) -> list[date]:
    """Closed trading sessions with no insights snapshot yet — today
    included once its close has passed; backfills days the app was down."""
    now = clock.now()
    run = session.execute(
        select(Run).order_by(Run.id.desc()).limit(1)
    ).scalar_one_or_none()
    if run is None:
        return []
    run_day = _aware(run.started_at).astimezone(NY_TZ).date()
    today = now.astimezone(NY_TZ).date()
    have = {
        row[0]
        for row in session.execute(
            select(InsightsSnapshot.session_date).distinct()
        ).all()
    }
    due: list[date] = []
    day = run_day
    while day <= today and len(due) < 600:
        trading = calendar.session_for(day)
        if trading is not None and trading.close < now and day not in have:
            due.append(day)
        day += timedelta(days=1)
    return due


def tick(
    *,
    config: AppConfig,
    calendar: CalendarProvider,
    clock: Clock,
    session_factory: sessionmaker[Session] | None = None,
    publish: Any = None,
) -> int:
    """Scheduler heartbeat: write every due session-close snapshot."""
    factory = session_factory or get_session_maker()
    written = 0
    with factory() as session:
        for session_date in due_session_dates(
            session, calendar=calendar, clock=clock
        ):
            written += write_snapshots(
                session,
                config=config,
                calendar=calendar,
                clock=clock,
                session_date=session_date,
            )
    if written and publish is not None:
        publish({"insights"})
    return written


def latest_snapshot(
    session: Session, *, strategy_id: int | None, period: str
) -> InsightsSnapshot | None:
    return session.execute(
        select(InsightsSnapshot)
        .where(
            InsightsSnapshot.period == period,
            (
                InsightsSnapshot.strategy_id.is_(None)
                if strategy_id is None
                else InsightsSnapshot.strategy_id == strategy_id
            ),
        )
        .order_by(
            InsightsSnapshot.session_date.desc(), InsightsSnapshot.id.desc()
        )
        .limit(1)
    ).scalar_one_or_none()


def findings_changes(
    session: Session, *, since: date, until: date
) -> dict[str, Any]:
    """New findings and findings that stopped holding for the weekly
    review's facts: live's run-period snapshots inside the covered week."""
    def keys_at(day: date | None) -> set[str]:
        if day is None:
            return set()
        row = session.execute(
            select(InsightsSnapshot)
            .where(
                InsightsSnapshot.strategy_id.is_(None),
                InsightsSnapshot.period == "run",
                InsightsSnapshot.session_date <= day,
            )
            .order_by(
                InsightsSnapshot.session_date.desc(),
                InsightsSnapshot.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if row is None or row.session_date is None:
            return set()
        if row.session_date < since and day != since - timedelta(days=1):
            return set()
        return _finding_keys(row.data or {})

    before = keys_at(since - timedelta(days=1))
    latest_row = session.execute(
        select(InsightsSnapshot)
        .where(
            InsightsSnapshot.strategy_id.is_(None),
            InsightsSnapshot.period == "run",
            InsightsSnapshot.session_date <= until,
        )
        .order_by(
            InsightsSnapshot.session_date.desc(), InsightsSnapshot.id.desc()
        )
        .limit(1)
    ).scalar_one_or_none()
    now_keys = _finding_keys(latest_row.data or {}) if latest_row else set()
    return {
        "new": sorted(now_keys - before),
        "stopped": sorted(before - now_keys),
    }
