"""Fact computation for reviews (docs/SPEC.md, Reviews).

Code computes every fact; the model only writes sentences about them.
All numbers below are the ones the number check validates model text
against — they are stored on the review row alongside the text.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.data.service import MarketDataService
from app.db.models import (
    Cycle,
    Decision,
    Earning,
    EquitySnapshot,
    Event,
    Order,
    Outcome,
    Position,
    Review,
    Run,
    Verdict,
)
from app.engine.performance import (
    _EXIT_LABELS,
    _entry_regime,
    _initial_stop,
)
from app.engine.state import ExecutionState
from app.engine.trades import _trade_r
from app.market.calendar import NY_TZ, CalendarProvider
from app.market.clock import Clock
from app.signals.service import SignalService

UTC = ZoneInfo("UTC")

# Event types that surface as action-needed banners on the dashboard.
_BANNER_EVENTS = (
    "reconciliation",
    "protective_stop",
    "position_check_failed",
    "flatten_sell_rejected",
    "universe_screen_pending",
    "broker_guard_unconfirmed",
)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _f(value: Decimal | None, places: str = "0.01") -> float | None:
    return float(value.quantize(Decimal(places))) if value is not None else None


def app_tz(config: AppConfig) -> ZoneInfo:
    return ZoneInfo(str(config.get("app", "timezone", default="Europe/Amsterdam")))


def week_window(monday: date) -> tuple[datetime, datetime]:
    """The covered Monday–Friday as an aware-UTC window [start, end)."""
    start = datetime.combine(monday, time.min, NY_TZ).astimezone(UTC)
    return start, start + timedelta(days=6)


def weekly_period(due: datetime) -> tuple[str, date, date]:
    """(period id, monday, friday) covered by a Saturday review instant."""
    saturday = _aware(due).astimezone(NY_TZ).date()
    friday = saturday - timedelta(days=1)
    monday = friday - timedelta(days=4)
    iso = monday.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}", monday, friday


def _decision_model_action(decision: Decision) -> str | None:
    """What the model answered, from its output or the validation row."""
    action = (decision.model_output or {}).get("action")
    if isinstance(action, str) and action:
        return action
    candidate = (decision.validation_result or {}).get("model_action")
    return candidate if isinstance(candidate, str) else None


def _change_reason(decision: Decision) -> str | None:
    """The gate or check that changed the model's action, if recorded."""
    for source in (decision.validation_result, decision.gates_result):
        if isinstance(source, dict):
            reason = source.get("reason") or source.get("blocked_by")
            if isinstance(reason, str) and reason:
                return reason
    return None


def _change_text(decision: Decision) -> str | None:
    """The failing check's "<name> <detail>" for the code-change line."""
    for source in (decision.validation_result, decision.gates_result):
        if not isinstance(source, dict):
            continue
        checks = source.get("checks")
        if not isinstance(checks, list):
            continue
        failed = next(
            (
                c
                for c in checks
                if isinstance(c, dict) and c.get("passed") is False
            ),
            None,
        )
        if failed is not None:
            detail = failed.get("detail")
            name = failed.get("name")
            text = " ".join(
                part
                for part in (
                    str(name) if isinstance(name, str) else "",
                    str(detail) if isinstance(detail, str) else "",
                )
                if part
            )
            return text or None
    return None


def _last_snapshot(session: Session, before: datetime) -> EquitySnapshot | None:
    return session.execute(
        select(EquitySnapshot)
        .where(EquitySnapshot.time < _naive_utc(before))
        .order_by(EquitySnapshot.time.desc())
        .limit(1)
    ).scalar_one_or_none()


def _max_drawdown(equities: list[Decimal]) -> Decimal | None:
    """Worst peak-to-trough percentage over the snapshot sequence."""
    if not equities:
        return None
    peak = equities[0]
    worst = Decimal(0)
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            worst = min(worst, (equity - peak) / peak * 100)
    return worst


def weekly_facts(
    session: Session,
    *,
    config: AppConfig,
    clock: Clock,
    calendar: CalendarProvider,
    monday: date,
) -> dict[str, Any]:
    """Every fact for the weekly review covering ``monday``–``friday``."""
    start, end = week_window(monday)
    friday = monday + timedelta(days=4)
    period_id = weekly_period(
        datetime.combine(monday + timedelta(days=5), time.min, NY_TZ)
    )[0]

    # ---- equity / return -------------------------------------------------
    base_snap = _last_snapshot(session, start)
    end_snap = _last_snapshot(session, end)
    first_in_window = session.execute(
        select(EquitySnapshot)
        .where(
            EquitySnapshot.time >= _naive_utc(start),
            EquitySnapshot.time < _naive_utc(end),
        )
        .order_by(EquitySnapshot.time)
        .limit(1)
    ).scalar_one_or_none()
    if base_snap is None:
        base_snap = first_in_window
    equity_start = base_snap.equity if base_snap is not None else None
    equity_end = end_snap.equity if end_snap is not None else None
    return_usd = (
        equity_end - equity_start
        if equity_start is not None and equity_end is not None
        else None
    )
    return_pct = (
        return_usd / equity_start * 100
        if return_usd is not None and equity_start
        else None
    )
    spy_start = base_snap.spy_benchmark_value if base_snap is not None else None
    spy_end = end_snap.spy_benchmark_value if end_snap is not None else None
    spy_return_pct = (
        (spy_end - spy_start) / spy_start * 100
        if spy_start is not None and spy_start and spy_end is not None
        else None
    )
    vs_spy_pts = (
        return_pct - spy_return_pct
        if return_pct is not None and spy_return_pct is not None
        else None
    )

    # ---- drawdown over the week's snapshots ------------------------------
    snaps_in_window = session.execute(
        select(EquitySnapshot)
        .where(
            EquitySnapshot.time >= _naive_utc(start),
            EquitySnapshot.time < _naive_utc(end),
        )
        .order_by(EquitySnapshot.time)
    ).scalars().all()
    equities = ([equity_start] if equity_start is not None else []) + [
        s.equity for s in snaps_in_window
    ]
    max_dd = _max_drawdown(equities)

    # ---- closed agent trades in the window --------------------------------
    closed = session.execute(
        select(Position)
        .where(
            Position.origin == "agent",
            Position.closed_at.is_not(None),
            Position.closed_at >= _naive_utc(start),
            Position.closed_at < _naive_utc(end),
        )
        .order_by(Position.closed_at)
    ).scalars().all()
    decisions_by_id = {
        d.id: d
        for d in session.execute(
            select(Decision).where(
                Decision.id.in_(
                    {p.entry_decision_id for p in closed if p.entry_decision_id}
                    or {0}
                )
            )
        ).scalars()
    }
    trades: list[dict[str, Any]] = []
    r_values: list[Decimal] = []
    won = lost = 0
    for p in closed:
        stop = _initial_stop(p, decisions_by_id)
        r = _trade_r(p, stop)
        if r is not None:
            r_values.append(r)
        pnl = p.realized_pnl_raw
        if pnl is not None:
            if pnl > 0:
                won += 1
            elif pnl < 0:
                lost += 1
        trades.append(
            {
                "symbol": p.symbol,
                "opened": _aware(p.opened_at).astimezone(NY_TZ).date().isoformat(),
                "closed": (
                    _aware(p.closed_at).astimezone(NY_TZ).date().isoformat()
                    if p.closed_at is not None
                    else None
                ),
                "exit": _EXIT_LABELS.get(p.exit_reason or "", p.exit_reason or ""),
                "r": _f(r),
                "pnl_usd": _f(pnl),
                "regime": _entry_regime(p, decisions_by_id),
            }
        )
    avg_r = (
        sum(r_values, Decimal(0)) / len(r_values) if r_values else None
    )
    by_r = [t for t in trades if t["r"] is not None]
    best = max(by_r, key=lambda t: t["r"], default=None)
    worst = min(by_r, key=lambda t: t["r"], default=None)

    # ---- model cost for trading decisions ---------------------------------
    cost_row = session.execute(
        select(func.sum(Decision.cost), func.count(Decision.id))
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .where(
            Cycle.scheduled_time >= _naive_utc(start),
            Cycle.scheduled_time < _naive_utc(end),
            Decision.model != "none",
        )
    ).one()
    model_cost, model_calls = cost_row
    reviews_cost = session.execute(
        select(func.sum(Review.cost)).where(
            Review.created_at >= _naive_utc(start),
            Review.created_at < _naive_utc(end),
        )
    ).scalar_one()

    # ---- skipped setups after 5 sessions -----------------------------------
    flat_pct = Decimal(str(config.get("reviews", "skip_flat_pct", default=2)))
    skip_rows = session.execute(
        select(Decision, Cycle.scheduled_time, Outcome)
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .join(Outcome, Outcome.decision_id == Decision.id)
        .where(
            Cycle.scheduled_time >= _naive_utc(start),
            Cycle.scheduled_time < _naive_utc(end),
            Decision.context == "candidate",
            Decision.final_action == "skip",
            Decision.no_call_reason.is_(None),
        )
        .order_by(Cycle.scheduled_time)
    ).all()
    # PR 32: only the model's own skips count — a model enter that code
    # turned into a skip is judged as an enter, never a model skip.
    skips = [
        (d, scheduled, o)
        for d, scheduled, o in skip_rows
        if _decision_model_action(d) == "skip"
    ]
    up = flat = down = pending = 0
    biggest_miss: dict[str, Any] | None = None
    for decision, scheduled, outcome in skips:
        price = (decision.input_snapshot or {}).get("price")
        if outcome.price_5sessions is None or not isinstance(
            price, int | float
        ):
            pending += 1
            continue
        pct = (outcome.price_5sessions - Decimal(str(price))) / Decimal(
            str(price)
        ) * 100
        if pct > flat_pct:
            up += 1
        elif pct < -flat_pct:
            down += 1
        else:
            flat += 1
        confidence = (decision.model_output or {}).get("confidence")
        if biggest_miss is None or pct > Decimal(str(biggest_miss["pct"])):
            biggest_miss = {
                "symbol": decision.symbol,
                "date": _aware(scheduled).astimezone(NY_TZ).date().isoformat(),
                "confidence": confidence if isinstance(confidence, int | float) else None,
                "pct": _f(pct, "0.1"),
            }

    # ---- confirmed verdicts in the window, per model call (PR 32) -----------
    confirmed = session.execute(
        select(Verdict, Decision)
        .join(Decision, Verdict.decision_id == Decision.id)
        .where(
            Verdict.source == "marc",
            Verdict.updated_at >= _naive_utc(start),
            Verdict.updated_at < _naive_utc(end),
        )
    ).all()
    verdict_counts = {"good_call": 0, "bad_call": 0, "unclear": 0}
    groups: dict[str, dict[str, Any]] = {
        "entered": {"good_call": 0, "bad_call": 0, "unclear": 0},
        "blocked": {"good_call": 0, "bad_call": 0, "unclear": 0, "reasons": {}},
        "skips": {"good_call": 0, "bad_call": 0, "unclear": 0},
    }
    thesis_contradictions = 0
    for v, d in confirmed:
        verdict_counts[v.verdict] = verdict_counts.get(v.verdict, 0) + 1
        if v.thesis_check == "contradicts":
            thesis_contradictions += 1
        vfacts = v.facts if isinstance(v.facts, dict) else {}
        model_action = vfacts.get("model_action")
        if not isinstance(model_action, str):
            model_action = _decision_model_action(d)
        changed = vfacts.get("changed_by_code")
        if not isinstance(changed, bool):
            changed = model_action is not None and model_action != d.final_action
        reason = vfacts.get("change_reason")
        if not isinstance(reason, str) or not reason:
            reason = _change_reason(d) if changed else None
        if model_action == "enter" and changed:
            group = "blocked"
        elif model_action == "enter":
            group = "entered"
        else:
            group = "skips"
        groups[group][v.verdict] = groups[group].get(v.verdict, 0) + 1
        if group == "blocked" and reason:
            reasons = groups["blocked"]["reasons"]
            reasons[reason] = reasons.get(reason, 0) + 1

    # ---- results by regime and by symbol ------------------------------------
    def _group(key_fn: Any) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for t, p in zip(trades, closed, strict=True):
            groups.setdefault(key_fn(t, p), []).append(t)
        rows = []
        for name, items in groups.items():
            rs = [t["r"] for t in items if t["r"] is not None]
            pnls = [t["pnl_usd"] for t in items if t["pnl_usd"] is not None]
            rows.append(
                {
                    "name": name,
                    "trades": len(items),
                    "won": sum(1 for t in items if (t["pnl_usd"] or 0) > 0),
                    "avg_r": (
                        round(sum(rs) / len(rs), 2) if rs else None
                    ),
                    "pnl_usd": round(sum(pnls), 2) if pnls else None,
                }
            )
        return sorted(rows, key=lambda r: r["name"])

    sessions = [
        calendar.session_for(monday + timedelta(days=i)) for i in range(5)
    ]
    iso = monday.isocalendar()
    return {
        "kind": "weekly",
        "period": {
            "id": period_id,
            "week": iso[1],
            "iso_year": iso[0],
            "start": monday.isoformat(),
            "end": friday.isoformat(),
            "label": (
                f"{monday.day} – {friday.day} {friday.strftime('%b')}"
                if monday.month == friday.month
                else f"{monday.day} {monday.strftime('%b')} – "
                f"{friday.day} {friday.strftime('%b')}"
            ),
            "start_day": monday.day,
            "end_day": friday.day,
            "due": (monday + timedelta(days=5)).isoformat(),
            "trading_days": sum(1 for s in sessions if s is not None),
        },
        "limits": {
            "weekly_summary_words": int(
                config.get("reviews", "max_words", "weekly_summary", default=120)
            ),
            "verdict_after_sessions": int(
                config.get("reviews", "verdict_after_sessions", default=5)
            ),
            "skip_flat_pct": float(flat_pct),
            "min_closed_trades_for_suggestion": int(
                config.get(
                    "reviews", "min_closed_trades_for_suggestion", default=10
                )
            ),
        },
        "return_pct": _f(return_pct),
        "return_usd": _f(return_usd),
        "equity_start": _f(equity_start),
        "equity_end": _f(equity_end),
        "spy_return_pct": _f(spy_return_pct),
        "vs_spy_pts": _f(vs_spy_pts),
        "max_drawdown_pct": _f(max_dd),
        "trades_closed": len(trades),
        "won": won,
        "lost": lost,
        "avg_r": _f(avg_r),
        "model_cost_usd": _f(model_cost, "0.0001"),
        "model_calls": int(model_calls or 0),
        "reviews_cost_usd": _f(reviews_cost, "0.0001"),
        "trades": trades,
        "best_trade": best,
        "worst_trade": worst,
        "skips": {
            "total": len(skips),
            "with_outcome": len(skips) - pending,
            "pending": pending,
            "up": up,
            "flat": flat,
            "down": down,
            "biggest_miss": biggest_miss,
        },
        "verdicts": {
            **verdict_counts,
            "total": len(confirmed),
            "entered": groups["entered"],
            "blocked": groups["blocked"],
            "skips": groups["skips"],
            "thesis_contradictions": thesis_contradictions,
        },
        # weekly.v2 (PR 32): the evidence behind observations and the
        # suggestion gate — this week and the current run.
        "sample": _sample_facts(session, start=start, end=end),
        "by_regime": _group(lambda t, _p: t["regime"] or "Unknown"),
        "by_symbol": _group(lambda t, _p: t["symbol"]),
        "strategies": _strategy_facts(session, config=config, clock=clock, calendar=calendar),
        "insights": _insights_facts(session, monday=monday, friday=friday),
        # weekly.v2 (PR 32): a suggestion only once the run has enough
        # closed trades; code drops a suggestion it didn't allow.
        "suggestion_allowed": (
            session.execute(
                select(func.count(Position.id)).where(
                    Position.closed_at.is_not(None), Position.origin == "agent"
                )
            ).scalar_one()
            >= int(
                config.get(
                    "reviews", "min_closed_trades_for_suggestion", default=10
                )
            )
        ),
    }


def _sample_facts(
    session: Session, *, start: datetime, end: datetime
) -> dict[str, Any]:
    """The evidence sample for weekly.v2 (docs/SPEC.md, Weekly
    suggestion): closed trades, model skips with a 5-session outcome and
    confirmed verdicts — this week and in the current run."""
    run_start = session.execute(select(func.max(Run.started_at))).scalar()

    closed_week = session.execute(
        select(func.count(Position.id)).where(
            Position.origin == "agent",
            Position.closed_at.is_not(None),
            Position.closed_at >= _naive_utc(start),
            Position.closed_at < _naive_utc(end),
        )
    ).scalar_one()
    closed_run = (
        session.execute(
            select(func.count(Position.id)).where(
                Position.origin == "agent",
                Position.closed_at.is_not(None),
                Position.closed_at >= run_start,
            )
        ).scalar_one()
        if run_start is not None
        else 0
    )
    skip_q = (
        select(Decision, Outcome)
        .join(Outcome, Outcome.decision_id == Decision.id)
        .where(
            Decision.context == "candidate",
            Decision.final_action == "skip",
            Decision.no_call_reason.is_(None),
            Outcome.price_5sessions.is_not(None),
        )
    )
    skips_rows = session.execute(
        skip_q.join(Cycle, Decision.cycle_id == Cycle.id).where(
            Cycle.scheduled_time >= _naive_utc(start),
            Cycle.scheduled_time < _naive_utc(end),
        )
    ).all()
    skips_week = sum(1 for d, _ in skips_rows if _decision_model_action(d) == "skip")
    skips_run = 0
    if run_start is not None:
        run_rows = session.execute(
            skip_q.join(Cycle, Decision.cycle_id == Cycle.id).where(
                Cycle.scheduled_time >= run_start,
            )
        ).all()
        skips_run = sum(
            1 for d, _ in run_rows if _decision_model_action(d) == "skip"
        )
    verdicts_week = session.execute(
        select(func.count(Verdict.id)).where(
            Verdict.source == "marc",
            Verdict.updated_at >= _naive_utc(start),
            Verdict.updated_at < _naive_utc(end),
        )
    ).scalar_one()
    verdicts_run = (
        session.execute(
            select(func.count(Verdict.id)).where(
                Verdict.source == "marc", Verdict.updated_at >= run_start
            )
        ).scalar_one()
        if run_start is not None
        else 0
    )
    return {
        "closed_trades": {"week": closed_week, "run": closed_run},
        "model_skips_with_outcome": {"week": skips_week, "run": skips_run},
        "confirmed_verdicts": {"week": verdicts_week, "run": verdicts_run},
    }


def _insights_facts(session: Session, *, monday: date, friday: date) -> dict[str, Any]:
    """New findings and findings that stopped holding (PR 22)."""
    try:
        from app.insights.service import findings_changes

        return findings_changes(session, since=monday, until=friday)
    except Exception:  # noqa: BLE001
        return {"new": [], "stopped": []}


def _strategy_facts(
    session: Session, *, config: AppConfig, clock: Clock, calendar: CalendarProvider
) -> list[dict[str, Any]]:
    """Live vs shadows for the current run (docs/SPEC.md, Strategies and
    shadows — Comparing); empty while no shadow exists."""
    from app.db.models import Strategy
    from app.engine.performance import compute_performance
    from app.shadows.compare import weekly_comparison

    if session.execute(select(Strategy.id).limit(1)).first() is None:
        return []
    perf = compute_performance(
        session, config=config, market_data=None, calendar=calendar, clock=clock
    )
    return weekly_comparison(session, config=config, perf=perf)


def banner_facts(
    session: Session,
    *,
    config: AppConfig,
    clock: Clock,
    market_data: MarketDataService | None,
    state: ExecutionState | None,
) -> list[dict[str, Any]]:
    """Compact active-banner list for the daily briefing."""
    items: list[dict[str, Any]] = []
    unacked = session.execute(
        select(Event)
        .where(
            Event.acknowledged_at.is_(None),
            Event.event_type.in_(_BANNER_EVENTS),
        )
        .order_by(Event.id.desc())
        .limit(20)
    ).scalars().all()
    for event in unacked:
        level = "critical" if event.event_type != "reconciliation" else event.level
        items.append({"kind": event.event_type, "level": level})
    if state is not None:
        if state.kill_switch_on:
            items.append({"kind": "kill_switch", "level": "critical"})
        if state.paused:
            items.append(
                {
                    "kind": "paused",
                    "level": "info" if state.paused_at_startup else "warning",
                }
            )
        if state.shock_brake_1h or state.shock_brake_session:
            items.append({"kind": "shock_brake", "level": "warning"})
        if state.daily_loss_hit:
            items.append({"kind": "daily_loss", "level": "warning"})
        if state.account is not None:
            today_ny = clock.now().astimezone(NY_TZ).date()
            sessions = (
                market_data.recent_sessions(today_ny, 5)
                if market_data is not None
                else []
            )
            if sessions:
                closed_q = (
                    select(Position)
                    .where(
                        Position.closed_at.is_not(None),
                        Position.realized_pnl_raw.is_not(None),
                    )
                    .order_by(Position.closed_at.desc())
                )
                if state.run_started_at is not None:
                    closed_q = closed_q.where(
                        Position.closed_at >= state.run_started_at
                    )
                streak = 0
                for p in session.execute(closed_q).scalars():
                    if (p.realized_pnl_raw or Decimal(0)) < 0:
                        streak += 1
                    else:
                        break
                threshold = int(config.get("risk", "losing_streak", default=3))
                if threshold > 0 and streak >= threshold:
                    items.append(
                        {
                            "kind": "losing_streak",
                            "level": "info",
                            "count": streak,
                        }
                    )
    earnings = (
        getattr(market_data, "latest_earnings", None)
        if market_data is not None
        else None
    )
    if earnings is not None and earnings.error is not None:
        items.append({"kind": "earnings_down", "level": "warning"})
    last_cycle = session.execute(
        select(Cycle)
        .where(Cycle.kind == "cycle", Cycle.status == "done")
        .order_by(Cycle.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    bars_health = (
        str((last_cycle.data_source_health or {}).get("bars", ""))
        if last_cycle is not None
        else ""
    )
    if bars_health.startswith("failed"):
        items.append({"kind": "market_data_down", "level": "warning"})
    return items


def briefing_facts(
    session: Session,
    *,
    config: AppConfig,
    clock: Clock,
    calendar: CalendarProvider,
    market_data: MarketDataService | None,
    signals: SignalService | None,
    state: ExecutionState | None,
    day: date,
) -> dict[str, Any]:
    """Every fact for the daily briefing of trading day ``day`` (New York)."""
    now = clock.now()

    # ---- open positions with stop distance --------------------------------
    positions: list[dict[str, Any]] = []
    recent = (
        market_data.recent_sessions(day, 3) if market_data is not None else []
    )
    for p in session.execute(
        select(Position)
        .where(Position.closed_at.is_(None))
        .order_by(Position.symbol)
    ).scalars():
        price: Decimal | None = None
        if market_data is not None and recent:
            bars = market_data.get_5m_bars_sessions(p.symbol, recent)
            closed_bars = [b for b in bars if b.start + timedelta(minutes=5) <= now]
            if closed_bars:
                price = closed_bars[-1].close
        distance = (
            (price - p.current_stop_price) / price * 100
            if price is not None and price and p.current_stop_price is not None
            else None
        )
        positions.append(
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "price": _f(price),
                "stop": _f(p.current_stop_price),
                "stop_distance_pct": _f(distance, "0.1"),
            }
        )

    # ---- earnings today / tomorrow ------------------------------------------
    watched = {p["symbol"] for p in positions}
    watched.update(
        market_data.universe() if market_data is not None else []
    )
    next_day = calendar.next_trading_day(day)

    def _earnings_on(d: date) -> list[dict[str, Any]]:
        rows = session.execute(
            select(Earning)
            .where(
                Earning.date == datetime.combine(d, time.min),
                Earning.symbol.in_(watched or {"\0"}),
            )
            .order_by(Earning.symbol)
        ).scalars().all()
        return [
            {"symbol": e.symbol, "time_of_day": e.time_of_day} for e in rows
        ]

    earnings_today = _earnings_on(day)
    earnings_tomorrow = _earnings_on(next_day.date) if next_day else []

    # ---- regime --------------------------------------------------------------
    regime: str | None = None
    if signals is not None:
        try:
            regime = signals.regime(now).label
        except Exception:  # noqa: BLE001
            regime = None

    # ---- yesterday's result ---------------------------------------------------
    yesterday: dict[str, Any] = {"return_pct": None, "return_usd": None}
    prev = calendar.previous_trading_day(day)
    before_prev = (
        calendar.previous_trading_day(prev.date) if prev is not None else None
    )
    if prev is not None and before_prev is not None:
        end_snap = _last_snapshot(session, prev.close)
        start_snap = _last_snapshot(session, before_prev.close)
        if (
            end_snap is not None
            and start_snap is not None
            and start_snap.equity
        ):
            delta = end_snap.equity - start_snap.equity
            yesterday = {
                "return_pct": _f(delta / start_snap.equity * 100),
                "return_usd": _f(delta),
            }

    return {
        "kind": "briefing",
        "date": day.isoformat(),
        "positions": positions,
        "earnings_today": earnings_today,
        "earnings_tomorrow": earnings_tomorrow,
        "regime": regime or "unavailable",
        "yesterday": yesterday,
        "banners": banner_facts(
            session,
            config=config,
            clock=clock,
            market_data=market_data,
            state=state,
        ),
        "limits": {
            "briefing_words": int(
                config.get("reviews", "max_words", "briefing", default=80)
            )
        },
    }


def verdict_eligible(decision: Decision) -> bool:
    """Enters and model skips get a draft verdict; rules no_calls never do."""
    return (
        decision.final_action in ("enter", "skip")
        and decision.no_call_reason is None
    )


def _stored_levels(snapshot: dict[str, Any]) -> list[Any]:
    """Level objects from a stored input snapshot.

    v2/v3 store the level list code built; v1 stored only the swing
    highs/lows block (``{"swing_highs": [...], "swing_lows": [...]}``),
    which maps onto the same room kinds. Returns [] for neither shape.
    """
    from app.decide.levels import Level
    from app.decide.v3 import _levels_from_stored

    raw = snapshot.get("levels")
    if isinstance(raw, list):
        return list(_levels_from_stored(raw))
    if isinstance(raw, dict):
        out: list[Level] = []
        for kind, key in (("swing_high", "swing_highs"), ("swing_low", "swing_lows")):
            for i, price in enumerate(raw.get(key) or []):
                try:
                    level_price = Decimal(str(price))
                except ArithmeticError:
                    continue
                out.append(
                    Level(
                        id=f"S{i + 1}",
                        price=level_price,
                        kind=kind,
                        timeframe="1h",
                        time=None,
                    )
                )
        return out
    return []


def checks_at_decision(
    decision: Decision, *, config: AppConfig
) -> dict[str, Any] | None:
    """The v3 check facts for a stored decision (docs/SPEC.md, Reviews —
    Facts). v3 rows stored them under ``input_snapshot.checks``; for v1/v2
    rows they are recomputed with the decision.v3 rules on the stored
    input — never refetched.
    """
    from app.decide.snapshot import UNAVAILABLE
    from app.decide.v3 import _trend, room_check, v3_config

    snapshot = decision.input_snapshot
    if not isinstance(snapshot, dict):
        return None
    stored = snapshot.get("checks")
    if isinstance(stored, dict):
        return dict(stored)

    def _num(value: object) -> Decimal | None:
        try:
            return Decimal(str(value))
        except ArithmeticError:
            return None

    ind_1h = snapshot.get("indicators_1h")
    ind_1h = ind_1h if isinstance(ind_1h, dict) else {}
    ind_d = snapshot.get("indicators_daily")
    ind_d = ind_d if isinstance(ind_d, dict) else {}
    regime = snapshot.get("regime")
    regime = regime if isinstance(regime, dict) else {}
    price = _num(snapshot.get("price"))
    atr_1h = _num(ind_1h.get("atr14"))
    offset = Decimal(
        str(config.get("execution", "entry_limit_offset_pct", default=0.05))
    )
    limit = (
        price * (1 + offset / 100)
        if decision.context == "candidate" and price is not None
        else None
    )
    v3 = v3_config(config)
    room_level, room_atr = (
        room_check(_stored_levels(snapshot), limit, atr_1h, v3.room_level_kinds)
        if limit is not None and atr_1h is not None and atr_1h > 0
        else (None, None)
    )
    return {
        "trend_1h": _trend(
            price, _num(ind_1h.get("ema20")), _num(ind_1h.get("ema50"))
        ),
        "trend_daily": _trend(
            _num(ind_d.get("close")),
            _num(ind_d.get("sma50")),
            _num(ind_d.get("sma200")),
        ),
        "regime_trend": str(regime.get("trend") or UNAVAILABLE),
        "regime_volatility": str(regime.get("volatility") or UNAVAILABLE),
        "room_atr": float(room_atr.quantize(Decimal("0.01"))) if room_atr is not None else None,
        "room_level": room_level.id if room_level is not None else None,
    }


def _execution(session: Session, decision: Decision, *, has_trade: bool) -> str | None:
    """filled | not_filled | not_sent for a model enter; null otherwise."""
    output = decision.model_output or {}
    model_action = output.get("action")
    if model_action is None:
        model_action = (decision.validation_result or {}).get("model_action")
    if model_action != "enter":
        return None
    if has_trade:
        return "filled"
    sent = session.execute(
        select(Order.id)
        .where(Order.decision_id == decision.id, Order.leg == "entry")
        .limit(1)
    ).first()
    return "not_filled" if sent is not None else "not_sent"


def verdict_facts(
    session: Session,
    *,
    config: AppConfig,
    clock: Clock,
    decision: Decision,
    scheduled: datetime,
    outcome: Outcome,
) -> dict[str, Any]:
    """Every fact for one decision's draft verdict."""
    n_sessions = int(config.get("reviews", "verdict_after_sessions", default=5))
    flat_pct = float(config.get("reviews", "skip_flat_pct", default=2))
    price = (decision.input_snapshot or {}).get("price")
    pct: Decimal | None = None
    if isinstance(price, int | float) and outcome.price_5sessions is not None:
        pct = (
            (outcome.price_5sessions - Decimal(str(price)))
            / Decimal(str(price))
            * 100
        )
    output = decision.model_output or {}
    model_action = _decision_model_action(decision)
    changed_by_code = (
        model_action is not None and model_action != decision.final_action
    )
    change_reason = _change_reason(decision) if changed_by_code else None
    change_text = _change_text(decision) if changed_by_code else None
    option_id = decision.chosen_option or (
        output.get("option_id")
        if isinstance(output.get("option_id"), str | int)
        else None
    )
    options_facts: list[dict[str, Any]] = []
    raw_options = (decision.options or {}).get("options", [])
    touches = (outcome.option_touches or {}) if outcome else {}
    if isinstance(raw_options, list):
        for option in raw_options:
            if not isinstance(option, dict):
                continue
            oid = option.get("id") or option.get("option_id")
            entry = option.get("entry_limit", option.get("entry"))
            stop = option.get("stop", option.get("stop_price"))
            target = option.get("target", option.get("target_price"))
            rr: float | None = None
            if all(
                isinstance(v, int | float | Decimal) for v in (entry, stop, target)
            ):
                risk = Decimal(str(entry)) - Decimal(str(stop))
                if risk > 0:
                    rr = float((Decimal(str(target)) - Decimal(str(entry))) / risk)
            touch = touches.get(str(oid)) if isinstance(touches, dict) else None
            options_facts.append({"option_id": oid, "rr": rr, "touch": touch})
    trade: dict[str, Any] | None = None
    position = session.execute(
        select(Position).where(Position.entry_decision_id == decision.id)
    ).scalar_one_or_none()
    if position is not None:
        stop = _initial_stop(position, {decision.id: decision})
        r = _trade_r(position, stop)
        r_so_far = (
            (outcome.price_5sessions - position.avg_entry_price)
            / (position.avg_entry_price - stop)
            if stop is not None
            and outcome.price_5sessions is not None
            and position.avg_entry_price > stop
            else None
        )
        trade = {
            "qty": position.qty,
            "entry": _f(position.avg_entry_price),
            "stop": _f(stop),
            "open": position.closed_at is None,
            "exit": (
                _EXIT_LABELS.get(
                    position.exit_reason or "", position.exit_reason or "—"
                )
                if position.closed_at is not None
                else None
            ),
            "r": _f(r),
            "r_so_far": _f(r_so_far),
            "pnl_usd": _f(position.realized_pnl_raw),
            "first_touch": outcome.first_touch,
        }
    base_facts = {
        "kind": "verdict",
        "symbol": decision.symbol,
        "model_action": model_action,
        "option_id": option_id,
        "final_action": decision.final_action,
        "changed_by_code": changed_by_code,
        "change_reason": change_reason,
        "change_text": change_text,
        "execution": _execution(session, decision, has_trade=trade is not None),
        "checks_at_decision": checks_at_decision(decision, config=config),
        "options": options_facts,
        "prompt_version": decision.prompt_version,
        "context": decision.context,
        "decided_at": _aware(scheduled).isoformat(),
        "date": _aware(scheduled).astimezone(NY_TZ).date().isoformat(),
        "confidence": (
            output.get("confidence")
            if isinstance(output.get("confidence"), int | float)
            else None
        ),
        "thesis": output.get("thesis"),
        "price_at_decision": float(price) if isinstance(price, int | float) else None,
        "price_5sessions": _f(outcome.price_5sessions),
        "price_5sessions_date": (
            _aware(outcome.price_5sessions_at).astimezone(NY_TZ).date().isoformat()
            if outcome.price_5sessions_at is not None
            else None
        ),
        "pct_5sessions": _f(pct, "0.1"),
        "sessions": n_sessions,
        "skip_flat_pct": flat_pct,
        "trade": trade,
    }
    from app.reviews.templates import verdict_plain

    base_facts["draft_verdict"] = verdict_plain(base_facts)["verdict"]
    return base_facts
