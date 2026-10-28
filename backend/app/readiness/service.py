"""Readiness (docs/SPEC.md, Readiness).

One function evaluates the 15 criteria for the current run from stored
data; the scheduler stores the result as a ``readiness_snapshots`` row
after each session's close. The screen reads the latest snapshot and
never recomputes per request.

Every criterion is pass / not yet / waiting for data. Below its minimum
sample a criterion is always waiting — it can never pass or fail.

Errors: an event at level critical or error that touched a decision,
order or fill (``affects != "none"``) stays open until one hour —
``readiness.error_auto_resolve_minutes`` — without a recurrence of the
same event type, or until Marc dismisses it with a note.

Nothing here enables live trading: all 15 criteria passing plus all five
manual items done is the condition for writing the separate live spec.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.db.base import get_session_maker
from app.db.models import (
    Cycle,
    Decision,
    Event,
    Position,
    ReadinessManual,
    ReadinessSnapshot,
    Run,
    SettingsVersion,
    Strategy,
)
from app.engine.performance import compute_performance
from app.log_index import index_available, index_path
from app.market.calendar import NY_TZ, CalendarProvider
from app.market.clock import Clock
from app.notifications.service import Notifier, NullNotifier
from app.shadows.compare import strategy_row

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

UTC = UTC

PASS = "pass"  # noqa: S105 - literal placeholder constant, not a secret
NOT_YET = "not_yet"
WAITING = "waiting"

ERROR_LEVELS = ("critical", "error")
# "quote_suspect" is the pre-Oct-24 name of "price_suspect"; stored
# decisions keep the old string and still count inside the window.
BLOCKED_PRICE_REASONS = ("price_stale", "price_suspect", "quote_suspect")
STOP_MISSING_TYPES = ("protective_stop", "protective_stop_failed")
# Statuses that mark a scheduled cycle/check as finished one way or the
# other; "running" and "interrupted" rows don't count either way.
CYCLE_COUNTED_STATUSES = ("done", "skipped", "error")

# Spec-fixed limits that are not editable thresholds (docs/SPEC.md,
# Readiness table and Settings → Readiness).
PROPOSAL_WINDOW = 100
PROPOSAL_MIN = 30
CALIBRATION_MIN = 50
CALIBRATION_GROUP_MIN = 10
WINDOW_CYCLES = 20
WINDOW_STOPS = 30
WINDOW_KILL = 30
WINDOW_ERRORS = 10
WINDOW_PRICE = 20

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# (key, group, label, target text, hover hint) in spec order.
_META: tuple[tuple[str, str, str, str, str], ...] = (
    ("closed_trades", "Evidence", "Closed trades", "≥ 100",
     "Trades closed in this run."),
    ("sessions", "Evidence", "Paper sessions in this run", "≥ 60",
     "Trading sessions since Start new run."),
    ("return_vs_spy", "Evidence", "Return vs SPY (adjusted)", "> 0",
     "Adjusted return of this run minus SPY buy and hold over the same sessions."),
    ("max_drawdown", "Evidence", "Max drawdown", "under 6%",
     "Largest fall from a peak in this run, adjusted."),
    ("avg_r", "Evidence", "Average R (adjusted)", "≥ +0.2R",
     "Average result per closed trade in R, after slippage."),
    ("profit_factor", "Evidence", "Profit factor", "≥ 1.3",
     "Gross wins ÷ gross losses, adjusted."),
    ("beats_rules_only", "Evidence", "Beats rules-only", "live > rules-only",
     "Average R of live above the rules-only shadow over the same sessions."),
    ("proposals", "Model", "Entry proposals that pass code checks", "≥ 75%",
     "Share of the last 100 model entries that passed stop distance and reward to risk."),
    ("calibration", "Model", "Confidence calibration", "within 10 pts",
     "Average gap between stated confidence and actual win rate per "
     "confidence group, weighted by group size."),
    ("cycles", "Operations", "Cycles completed", "≥ 98%",
     "Scheduled cycles that completed, last 20 sessions. Missed cycles show their reason."),
    ("stops_missing", "Operations", "Positions without a stop", "0",
     "Times a position had no live stop at Alpaca for longer than one "
     "position check, last 30 sessions."),
    ("kill_switch", "Operations", "Kill switch events", "0",
     "Kill switch turned on, manually or automatically, last 30 sessions."),
    ("unresolved_errors", "Operations", "Unresolved errors", "0",
     "Errors that affected a decision, order or fill and are not resolved, last 10 sessions."),
    ("price_blocked", "Operations", "Price data quality", "≤ 5%",
     "Candidate checks blocked as price_stale or price_suspect, last 20 sessions."),
    ("reconciliation", "Operations", "Reconciliation", "0",
     "Unacknowledged reconciliation mismatches right now."),
    ("log_errors", "Operations", "Errors in the log", "0",
     "Unreviewed ERROR problems in the log index, last 10 sessions."),
    ("log_critical", "Operations", "Critical lines in the log", "0",
     "CRITICAL lines in the log index, last 20 sessions."),
)
_META_BY_KEY = {m[0]: m for m in _META}

# (item key, label, detail) — fixed by the spec (docs/SPEC.md, Readiness).
MANUAL_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("sip_data", "Real-time SIP market data",
     "Paid Alpaca data, so entries use the consolidated quote instead of the IEX last trade."),
    ("tax", "Dutch tax treatment checked",
     "With an advisor, before any real money is traded."),
    ("live_spec", "Live-trading spec written and reviewed",
     "Live trading is a separate spec; passing this page is necessary, not sufficient."),
    ("hosting", "Hosting with login",
     "VPS with HTTPS and login, so the agent no longer depends on this computer."),
    ("live_size", "First live size and loss limit decided",
     "Start small; written down before the switch exists."),
)


class ReadinessError(Exception):
    """Bad request on a readiness write (missing note, unknown item)."""


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _day_start_utc(day: date) -> datetime:
    """00:00 New York of ``day`` as naive UTC — the lower bound of a
    session-count window."""
    return (
        datetime.combine(day, dtime.min, tzinfo=NY_TZ)
        .astimezone(UTC)
        .replace(tzinfo=None)
    )


def _window_start(
    calendar: CalendarProvider, session_date: date, sessions: int
) -> date:
    """First NY date of the ``sessions``-session window ending at
    ``session_date`` (fewer when the calendar doesn't reach that far)."""
    days = [session_date]
    while len(days) < sessions:
        prev = calendar.previous_trading_day(days[0])
        if prev is None:
            break
        days.insert(0, prev.date)
    return days[0]


def _next_session_date(calendar: CalendarProvider, day: date) -> date | None:
    """Next trading day, extrapolating weekdays past the loaded calendar.

    The store only loads ~30 days ahead, while estimates routinely need
    50+ sessions; beyond the horizon the estimate counts plain weekdays —
    approximate by design (the UI shows "≈").
    """
    nxt = calendar.next_trading_day(day)
    if nxt is not None:
        return nxt.date
    current = day
    for _ in range(10):
        current += timedelta(days=1)
        if current.weekday() < 5:
            return current
    return None


def _date_after_sessions(
    calendar: CalendarProvider, after: date, sessions: int
) -> date | None:
    """The NY session exactly ``sessions`` trading days after ``after``."""
    day = after
    for _ in range(max(sessions, 0)):
        nxt = _next_session_date(calendar, day)
        if nxt is None:
            return None
        day = nxt
    return day


def _fmt_estimate(day: date, today: date) -> str:
    """Design format: ``≈ Q4 2027`` when over a year out, else a date."""
    if (day - today).days > 365:
        quarter = (day.month - 1) // 3 + 1
        return f"≈ Q{quarter} {day.year}"
    if day.year != today.year:
        return f"≈ {day.day} {_MONTHS[day.month - 1]} {day.year}"
    return f"≈ {day.day} {_MONTHS[day.month - 1]}"


def _resolve_text(minutes: int) -> str:
    if minutes >= 60 and minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minutes"


def _criterion(
    key: str,
    *,
    value: str,
    status: str,
    target: str | None = None,
    sample: int | None = None,
    progress: float | None = None,
    sub: str | None = None,
    hint: str | None = None,
    estimate: str | None = None,
    estimate_date: str | None = None,
) -> dict[str, Any]:
    key_, group, label, default_target, default_hint = _META_BY_KEY[key]
    assert key_ == key  # noqa: S101 - internal invariant
    return {
        "key": key,
        "group": group,
        "label": label,
        "value": value,
        "target": target if target is not None else default_target,
        "progress": progress,
        "status": status,
        "sample": sample,
        "sub": sub,
        "hint": hint if hint is not None else default_hint,
        "estimate": estimate,
        "estimate_date": estimate_date,
    }


def _cfg(config: AppConfig, key: str, default: Any) -> Any:
    return config.get("readiness", key, default=default)


def unresolved_errors(
    session: Session,
    *,
    as_of: datetime,
    window_start: datetime | None,
    minutes: int,
) -> list[Event]:
    """Error events still open at ``as_of``.

    A row is open when it has no resolution yet at ``as_of`` and the
    latest recurrence of its event type is younger than
    ``minutes`` — the auto-resolve rule applied to the stored rows.
    ``window_start`` bounds the criterion's session window.
    """
    as_of = _naive(as_of)
    query = select(Event).where(
        Event.level.in_(ERROR_LEVELS),
        Event.affects != "none",
        Event.time <= as_of,
        (Event.resolved_at.is_(None)) | (Event.resolved_at > as_of),
    )
    if window_start is not None:
        query = query.where(Event.time >= window_start)
    rows = list(session.execute(query.order_by(Event.time)).scalars())
    latest: dict[str, datetime] = {}
    for row in rows:
        latest[row.event_type] = max(latest.get(row.event_type, row.time), row.time)
    limit = timedelta(minutes=minutes)
    return [
        row
        for row in rows
        if row.resolved_at is None or row.resolved_at > as_of
        if latest[row.event_type] + limit > as_of
    ]


def apply_auto_resolution(
    session: Session, *, now: datetime, minutes: int
) -> int:
    """Resolve error groups quiet for ``minutes``; returns rows resolved.

    Every member of a quiet group gets ``resolved_at = last recurrence +
    minutes`` and ``resolution = 'auto'`` — the timestamp preserves what
    the state was at any point in between, which backfilled snapshots
    rely on.
    """
    now = _naive(now)
    rows = list(
        session.execute(
            select(Event).where(
                Event.level.in_(ERROR_LEVELS),
                Event.affects != "none",
                Event.resolved_at.is_(None),
            )
        ).scalars()
    )
    latest: dict[str, datetime] = {}
    members: dict[str, list[Event]] = {}
    for row in rows:
        members.setdefault(row.event_type, []).append(row)
        latest[row.event_type] = max(
            latest.get(row.event_type, row.time), row.time
        )
    resolved = 0
    for event_type, group in members.items():
        last = latest[event_type]
        if last + timedelta(minutes=minutes) > now:
            continue
        for row in group:
            row.resolved_at = last + timedelta(minutes=minutes)
            row.resolution = "auto"
            resolved += 1
    return resolved


def dismiss_error(
    session: Session, *, clock: Clock, event_id: int, note: str
) -> Event:
    """Dismiss one unresolved error with a required note."""
    note = note.strip()
    if not note:
        raise ReadinessError("a note is required to dismiss an error")
    event = session.get(Event, event_id)
    if event is None:
        raise ReadinessError("event not found")
    if (
        event.resolved_at is not None
        or event.level not in ERROR_LEVELS
        or event.affects == "none"
    ):
        raise ReadinessError("not an unresolved error")
    event.resolved_at = _naive(clock.now())
    event.resolution = "dismissed"
    event.note = note
    return event


def set_manual_item(
    session: Session, *, clock: Clock, item: str, done: bool, note: str
) -> ReadinessManual:
    """Append one manual-item change; a note is required either way."""
    if item not in {key for key, _, _ in MANUAL_ITEMS}:
        raise ReadinessError(f"unknown manual item {item!r}")
    note = note.strip()
    if not note:
        raise ReadinessError("a short note is required")
    row = ReadinessManual(
        item=item, done=done, note=note, changed_at=_naive(clock.now())
    )
    session.add(row)
    session.flush()
    return row


def _latest_run(session: Session) -> Run | None:
    return session.execute(
        select(Run).order_by(Run.id.desc()).limit(1)
    ).scalar_one_or_none()


def _missing_stop_count(
    session: Session,
    *,
    run_start: datetime,
    window_start: datetime,
    as_of: datetime,
) -> int:
    """Detections of a missing stop that repeat at consecutive checks.

    A check finding a position without a live stop means the stop was
    missing for less than one check interval — it doesn't count. A check
    finding it still missing after a previous flag does. A successful
    check clears every symbol it didn't flag.
    """
    checks = list(
        session.execute(
            select(Cycle)
            .where(
                Cycle.kind.in_(("cycle", "position_check")),
                Cycle.status.in_(("done", "skipped", "error", "interrupted")),
                Cycle.started_at.is_not(None),
                Cycle.finished_at.is_not(None),
                Cycle.finished_at >= run_start,
                Cycle.started_at <= as_of,
            )
            .order_by(Cycle.started_at)
        ).scalars()
    )
    events = list(
        session.execute(
            select(Event)
            .where(
                Event.event_type.in_(STOP_MISSING_TYPES),
                Event.time >= run_start,
                Event.time <= as_of,
            )
            .order_by(Event.time)
        ).scalars()
    )
    # Attach each event to the check window that produced it; events
    # outside every window are applied loose, in time order.
    by_check: dict[int, list[Event]] = {c.id: [] for c in checks}
    loose: list[Event] = []
    ci = 0
    for event in events:
        while (
            ci < len(checks)
            and checks[ci].finished_at is not None
            and checks[ci].finished_at < event.time  # type: ignore[operator]
        ):
            ci += 1
        if ci < len(checks) and checks[ci].started_at <= event.time:  # type: ignore[operator]
            by_check[checks[ci].id].append(event)
        else:
            loose.append(event)

    missing: set[str] = set()
    count = 0

    def apply(event: Event) -> None:
        nonlocal count
        symbol = str((event.details or {}).get("symbol") or "")
        if not symbol:
            return
        if symbol in missing and event.time >= window_start:
            count += 1
        flagged[symbol] = event.event_type == "protective_stop_failed"

    flagged: dict[str, bool] = {}
    # Merge loose events and checks into one timeline.
    timeline: list[tuple[datetime, Cycle | Event]] = [
        (c.started_at, c) for c in checks if c.started_at is not None
    ] + [(e.time, e) for e in loose]
    for _at, item in sorted(timeline, key=lambda t: t[0]):
        flagged = {}
        if isinstance(item, Cycle):
            for event in sorted(by_check[item.id], key=lambda e: e.time):
                apply(event)
            if item.status == "done":
                # Every position verified: only symbols still flagged
                # with a failed placement stay missing.
                missing = {s for s, still in flagged.items() if still}
            else:
                for symbol, still in flagged.items():
                    if still:
                        missing.add(symbol)
                    else:
                        missing.discard(symbol)
        else:
            apply(item)
            for symbol, still in flagged.items():
                if still:
                    missing.add(symbol)
                else:
                    missing.discard(symbol)
    return count


def evaluate(
    session: Session,
    *,
    config: AppConfig,
    calendar: CalendarProvider,
    clock: Clock,
    session_date: date,
    as_of: datetime,
) -> list[dict[str, Any]]:
    """Compute all 15 criteria for the current run (docs/SPEC.md, Readiness).

    ``session_date`` is the New York session the snapshot belongs to;
    windows end there. ``as_of`` is the moment "now" for unresolved
    states — the close time for backfilled dates, clock time for today.
    """
    as_of_naive = _naive(as_of)
    today = _aware(clock.now()).astimezone(NY_TZ).date()

    min_trades = int(_cfg(config, "min_closed_trades", 100))
    min_sessions = int(_cfg(config, "min_sessions", 60))
    max_dd = Decimal(str(_cfg(config, "max_drawdown_pct", 6)))
    min_avg_r = Decimal(str(_cfg(config, "min_avg_r", 0.2)))
    min_pf = Decimal(str(_cfg(config, "min_profit_factor", 1.3)))
    ratio_min = int(_cfg(config, "ratio_min_trades", 30))
    min_prop_pct = Decimal(str(_cfg(config, "min_proposal_pass_pct", 75)))
    max_cal_pts = Decimal(str(_cfg(config, "max_calibration_gap_pts", 10)))
    min_cycles_pct = Decimal(str(_cfg(config, "min_cycles_completed_pct", 98)))
    max_price_pct = Decimal(str(_cfg(config, "max_price_blocked_pct", 5)))
    resolve_minutes = int(_cfg(config, "error_auto_resolve_minutes", 60))

    run = _latest_run(session)
    if run is None:
        return [
            _criterion(key, value="—", status=WAITING, sub="no run yet")
            for key, _group, _label, _t, _h in _META
        ]
    run_start = run.started_at

    perf = compute_performance(
        session,
        config=config,
        market_data=None,
        calendar=calendar,
        clock=clock,
    )
    summary = cast(
        "dict[str, Any]", perf.get("summary") if not perf.get("empty") else {}
    ) or {}
    run_info = cast("dict[str, Any]", perf.get("run") or {})
    sessions = int(run_info.get("sessions") or 0)
    closed = int(summary.get("closed") or 0)

    def _dec(key: str) -> Decimal | None:
        raw = summary.get(key)
        return Decimal(str(raw)) if raw is not None else None

    agent_trades = [
        t
        for t in cast("list[dict[str, Any]]", perf.get("closed_trades") or [])
        if t.get("origin") == "agent"
    ]
    adj_r_count = sum(1 for t in agent_trades if t.get("adjusted_r") is not None)
    adj_pnl_count = sum(1 for t in agent_trades if t.get("adjusted") is not None)
    gross_wins = sum(
        (
            Decimal(str(t["adjusted"]))
            for t in agent_trades
            if t.get("adjusted") is not None
            and Decimal(str(t["adjusted"])) > 0
        ),
        Decimal(0),
    )

    criteria: list[dict[str, Any]] = []

    # ---- Evidence -------------------------------------------------------
    pace = Decimal(closed) / sessions if sessions else None
    est_sessions: int | None = None
    est_date: date | None = None
    if closed < min_trades and pace is not None and pace > 0:
        est_sessions = math.ceil((min_trades - closed) / pace)
        est_date = _date_after_sessions(calendar, session_date, est_sessions)
    criteria.append(
        _criterion(
            "closed_trades",
            value=str(closed),
            target=f"≥ {min_trades}",
            progress=min(closed / min_trades, 1.0) if min_trades else 1.0,
            status=PASS if closed >= min_trades else NOT_YET,
            sample=closed,
            sub=(
                f"≈ {est_sessions} sessions at the current pace"
                if est_sessions is not None
                else None
            ),
            estimate=(
                _fmt_estimate(est_date, today) if est_date is not None else None
            ),
            estimate_date=est_date.isoformat() if est_date else None,
        )
    )

    sess_date = (
        _date_after_sessions(calendar, session_date, min_sessions - sessions)
        if sessions < min_sessions
        else None
    )
    criteria.append(
        _criterion(
            "sessions",
            value=str(sessions),
            target=f"≥ {min_sessions}",
            progress=min(sessions / min_sessions, 1.0) if min_sessions else 1.0,
            status=PASS if sessions >= min_sessions else NOT_YET,
            sample=sessions,
            sub=_fmt_estimate(sess_date, today) if sess_date else None,
            estimate=_fmt_estimate(sess_date, today) if sess_date else None,
            estimate_date=sess_date.isoformat() if sess_date else None,
        )
    )

    adj_pct = _dec("adjusted_pct")
    spy_pct = _dec("spy_return_pct")
    gap = adj_pct - spy_pct if adj_pct is not None and spy_pct is not None else None
    criteria.append(
        _criterion(
            "return_vs_spy",
            value=f"{gap:+.2f} pts" if gap is not None else "—",
            target="> 0",
            status=PASS if gap is not None and gap > 0 else NOT_YET,
            sample=sessions,
        )
    )

    dd = _dec("max_drawdown_pct")
    criteria.append(
        _criterion(
            "max_drawdown",
            value=f"−{dd:.2f}%" if dd is not None else "—",
            target=f"under {max_dd:g}%",
            status=PASS if dd is not None and dd < max_dd else NOT_YET,
            sample=sessions,
        )
    )

    avg_r_adj = _dec("avg_r_adjusted")
    avg_r_status = (
        WAITING
        if adj_r_count < ratio_min
        else PASS
        if avg_r_adj is not None and avg_r_adj >= min_avg_r
        else NOT_YET
    )
    criteria.append(
        _criterion(
            "avg_r",
            value=(
                f"{avg_r_adj:+.2f}R · {adj_r_count} trades"
                if avg_r_adj is not None
                else f"— · {adj_r_count} trades"
            ),
            target=f"≥ +{min_avg_r:g}R",
            status=avg_r_status,
            sample=adj_r_count,
            sub=(
                f"needs {ratio_min} closed trades"
                if adj_r_count < ratio_min
                else None
            ),
        )
    )

    pf = _dec("profit_factor")
    if adj_pnl_count < ratio_min:
        pf_status = WAITING
    elif pf is not None:
        pf_status = PASS if pf >= min_pf else NOT_YET
    else:
        # No losses yet: infinite profit factor passes; no wins fails.
        pf_status = PASS if gross_wins > 0 else NOT_YET
    criteria.append(
        _criterion(
            "profit_factor",
            value=(
                f"{pf} · {adj_pnl_count} trades"
                if pf is not None
                else f"— · {adj_pnl_count} trades"
            ),
            target=f"≥ {min_pf:g}",
            status=pf_status,
            sample=adj_pnl_count,
            sub=(
                f"needs {ratio_min} closed trades"
                if adj_pnl_count < ratio_min
                else None
            ),
        )
    )

    baseline = session.execute(
        select(Strategy)
        .where(Strategy.kind == "rules_only", Strategy.status == "active")
        .order_by(Strategy.id)
        .limit(1)
    ).scalar_one_or_none()
    # Shadow R is slippage-adjusted like live's adjusted R (ShadowPosition
    # docstring), so the comparison pairs adjusted with adjusted.
    live_avg = _dec("avg_r_adjusted")
    beats_value = f"— · {closed} vs 0 trades"
    beats_status = WAITING
    beats_sub: str | None = "the question this run must answer: does the model add value?"
    beats_sample: int | None = None
    if baseline is None:
        beats_sub = "no active rules-only shadow"
        beats_sample = 0
    else:
        row = strategy_row(session, baseline, run, config)
        shadow_closed = int(row["closed"])
        shadow_avg = Decimal(row["avg_r"]) if row["avg_r"] is not None else None
        beats_sample = min(closed, shadow_closed)
        beats_value = f"— · {closed} vs {shadow_closed} trades"
        if baseline.created_at > run_start:
            beats_sub = "the rules-only shadow started after the run began"
        elif closed < ratio_min or shadow_closed < ratio_min:
            beats_sub = f"needs {ratio_min} closed trades each"
        elif live_avg is None or shadow_avg is None:
            beats_sub = "no R values yet"
        else:
            beats_status = PASS if live_avg > shadow_avg else NOT_YET
            beats_value = (
                f"{live_avg:+.2f}R vs {shadow_avg:+.2f}R · "
                f"{closed} vs {shadow_closed} trades"
            )
            beats_sub = "the question this run must answer: does the model add value?"
    criteria.append(
        _criterion(
            "beats_rules_only",
            value=beats_value,
            status=beats_status,
            sample=beats_sample,
            sub=beats_sub,
        )
    )

    # ---- Model ------------------------------------------------------------
    # Under decision.v2 a proposal passes when the option id is valid, the
    # explanation check passed and the order-time recheck passed — or no
    # order was attempted (docs/SPEC.md, Readiness — Model).
    def _v2_passed(d: Decision) -> bool:
        checks = cast(
            "list[dict[str, Any]]", (d.validation_result or {}).get("checks") or []
        )
        option_check = next(
            (c for c in checks if str(c.get("name", "")).startswith("Option ")),
            None,
        )
        if not option_check or not option_check.get("passed"):
            return False
        explanation = d.explanation_check or {}
        if explanation and explanation.get("passed") is False:
            return False
        gates = cast(
            "list[dict[str, Any]]", (d.gates_result or {}).get("checks") or []
        )
        fresh = next(
            (c for c in gates if c.get("name") == "fresh_quote"), None
        )
        if fresh is not None and not fresh.get("passed"):
            return False
        # The other code checks (confidence, band, R/R) still count.
        return all(
            bool(c.get("passed"))
            for c in checks
            if c.get("name") != "Explanation check"
        )

    def _proposal_passed(d: Decision) -> bool:
        if d.prompt_version == "decision.v2":
            return _v2_passed(d)
        return d.final_action == "enter"

    proposals = list(
        session.execute(
            select(Decision)
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .where(
                Decision.context == "candidate",
                Cycle.scheduled_time >= run_start,
                Decision.validation_result["model_action"].as_string() == "enter",
            )
            .order_by(Decision.id.desc())
            .limit(PROPOSAL_WINDOW)
        ).scalars()
    )
    prop_total = len(proposals)
    prop_pass = sum(1 for d in proposals if _proposal_passed(d))
    prop_pct = Decimal(prop_pass) / prop_total * 100 if prop_total else None
    criteria.append(
        _criterion(
            "proposals",
            value=(
                f"{prop_pct:.0f}% · {prop_pass} of {prop_total}"
                if prop_pct is not None
                else "— · 0"
            ),
            target=f"≥ {min_prop_pct:g}%",
            progress=float(prop_pct / 100) if prop_pct is not None else 0.0,
            status=(
                WAITING
                if prop_total < PROPOSAL_MIN
                else PASS
                if prop_pct is not None and prop_pct >= min_prop_pct
                else NOT_YET
            ),
            sample=prop_total,
            sub=(
                f"needs {PROPOSAL_MIN} proposals"
                if prop_total < PROPOSAL_MIN
                else "the decision prompt (code-built stop and target options) targets this"
            ),
        )
    )

    filled = list(
        session.execute(
            select(Position).where(
                Position.origin == "agent",
                Position.entry_decision_id.is_not(None),
                Position.closed_at.is_not(None),
                Position.closed_at >= run_start,
            )
        ).scalars()
    )
    decision_ids = {p.entry_decision_id for p in filled if p.entry_decision_id}
    entry_decisions = {
        d.id: d
        for d in session.execute(
            select(Decision).where(Decision.id.in_(decision_ids or {0}))
        ).scalars()
    }
    # bucket -> [confidences], [wins]
    groups: dict[int, list[tuple[Decimal, bool]]] = {}
    entries = 0
    for pos in filled:
        decision = entry_decisions.get(pos.entry_decision_id or 0)
        output = (decision.model_output or {}) if decision else {}
        raw_conf = output.get("confidence")
        if raw_conf is None:
            continue
        conf = Decimal(str(raw_conf))
        pnl = pos.realized_pnl_adjusted or pos.realized_pnl_raw
        entries += 1
        bucket = min(int(conf * 10), 9)
        groups.setdefault(bucket, []).append((conf, bool(pnl and pnl > 0)))
    cal_gap: Decimal | None = None
    if entries >= CALIBRATION_MIN:
        weighted = Decimal(0)
        weight = 0
        for bucket_entries in groups.values():
            if len(bucket_entries) < CALIBRATION_GROUP_MIN:
                continue
            mean_conf = sum(
                (c for c, _ in bucket_entries), Decimal(0)
            ) / len(bucket_entries)
            win_rate = Decimal(
                sum(1 for _, w in bucket_entries if w)
            ) / len(bucket_entries)
            weighted += abs(mean_conf - win_rate) * len(
                bucket_entries
            )
            weight += len(bucket_entries)
        if weight:
            cal_gap = weighted / weight * 100
    cal_status = (
        WAITING
        if entries < CALIBRATION_MIN or cal_gap is None
        else PASS
        if cal_gap <= max_cal_pts
        else NOT_YET
    )
    criteria.append(
        _criterion(
            "calibration",
            value=(
                f"{cal_gap:.1f} pts · {entries} entries"
                if cal_gap is not None
                else f"— · {entries} entries"
            ),
            target=f"within {max_cal_pts:g} pts",
            status=cal_status,
            sample=entries,
            sub=(
                f"needs {CALIBRATION_MIN} filled entries"
                if entries < CALIBRATION_MIN
                else (
                    "no confidence group has 10 entries yet"
                    if cal_gap is None
                    else None
                )
            ),
        )
    )

    # ---- Operations ---------------------------------------------------------
    cycles_start = _day_start_utc(
        _window_start(calendar, session_date, WINDOW_CYCLES)
    )
    cycles = list(
        session.execute(
            select(Cycle).where(
                Cycle.kind == "cycle",
                Cycle.scheduled_time >= cycles_start,
                Cycle.scheduled_time >= run_start,
                Cycle.scheduled_time <= as_of_naive,
                Cycle.status.in_(CYCLE_COUNTED_STATUSES),
            )
        ).scalars()
    )
    done = sum(1 for c in cycles if c.status == "done")
    total = len(cycles)
    cycles_pct = Decimal(done) / total * 100 if total else None
    missed = [c for c in cycles if c.status != "done"]
    missed_sub: str | None = None
    if missed:
        reasons = Counter(
            (c.errors or ["missed"])[0].removeprefix("error: ") for c in missed
        )
        top_reason = reasons.most_common(1)[0][0]
        last_day = _aware(
            max(c.scheduled_time for c in missed)
        ).astimezone(NY_TZ).date()
        missed_sub = (
            f"{len(missed)} missed: {top_reason} "
            f"({last_day.day} {_MONTHS[last_day.month - 1]})"
        )
    criteria.append(
        _criterion(
            "cycles",
            value=(
                f"{cycles_pct:.0f}% · {done} of {total}"
                if cycles_pct is not None
                else "—"
            ),
            target=f"≥ {min_cycles_pct:g}%",
            progress=float(cycles_pct / 100) if cycles_pct is not None else None,
            status=(
                PASS if cycles_pct is None or cycles_pct >= min_cycles_pct
                else NOT_YET
            ),
            sample=total,
            sub=missed_sub,
        )
    )

    stops_start = _day_start_utc(
        _window_start(calendar, session_date, WINDOW_STOPS)
    )
    stops_count = _missing_stop_count(
        session,
        run_start=run_start,
        window_start=stops_start,
        as_of=as_of_naive,
    )
    criteria.append(
        _criterion(
            "stops_missing",
            value=str(stops_count),
            status=PASS if stops_count == 0 else NOT_YET,
            sample=stops_count,
        )
    )

    kill_start = _day_start_utc(
        _window_start(calendar, session_date, WINDOW_KILL)
    )
    kill_count = sum(
        1
        for e in session.execute(
            select(Event).where(
                Event.event_type == "kill_switch",
                Event.time >= kill_start,
                Event.time >= run_start,
                Event.time <= as_of_naive,
            )
        ).scalars()
        if (e.details or {}).get("on") is True
    )
    criteria.append(
        _criterion(
            "kill_switch",
            value=str(kill_count),
            status=PASS if kill_count == 0 else NOT_YET,
            sample=int(kill_count),
        )
    )

    errors_start = _day_start_utc(
        _window_start(calendar, session_date, WINDOW_ERRORS)
    )
    open_errors = unresolved_errors(
        session,
        as_of=as_of_naive,
        window_start=errors_start,
        minutes=resolve_minutes,
    )
    criteria.append(
        _criterion(
            "unresolved_errors",
            value=str(len(open_errors)),
            status=PASS if not open_errors else NOT_YET,
            sample=len(open_errors),
            hint=(
                "Errors that affected a decision, order or fill and are "
                f"not resolved, last {WINDOW_ERRORS} sessions. An error "
                f"resolves itself after {_resolve_text(resolve_minutes)} "
                "without recurring, or you dismiss it with a note."
            ),
        )
    )

    price_start = _day_start_utc(
        _window_start(calendar, session_date, WINDOW_PRICE)
    )
    checks_total = session.execute(
        select(func.count(Decision.id))
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .where(
            Decision.context == "candidate",
            Cycle.scheduled_time >= price_start,
            Cycle.scheduled_time >= run_start,
            Cycle.scheduled_time <= as_of_naive,
        )
    ).scalar_one()
    checks_blocked = session.execute(
        select(func.count(Decision.id))
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .where(
            Decision.context == "candidate",
            Cycle.scheduled_time >= price_start,
            Cycle.scheduled_time >= run_start,
            Cycle.scheduled_time <= as_of_naive,
            Decision.no_call_reason.in_(BLOCKED_PRICE_REASONS),
        )
    ).scalar_one()
    price_pct = (
        Decimal(checks_blocked) / checks_total * 100 if checks_total else None
    )
    criteria.append(
        _criterion(
            "price_blocked",
            value=(
                f"{price_pct:.1f}%" if price_pct is not None else "—"
            ),
            target=f"≤ {max_price_pct:g}%",
            status=(
                PASS if price_pct is None or price_pct <= max_price_pct
                else NOT_YET
            ),
            sample=int(checks_total),
        )
    )

    recon_count = session.execute(
        select(func.count(Event.id)).where(
            Event.event_type == "reconciliation",
            Event.acknowledged_at.is_(None),
            Event.time >= run_start,
            Event.time <= as_of_naive,
        )
    ).scalar_one()
    criteria.append(
        _criterion(
            "reconciliation",
            value=str(recon_count),
            status=PASS if recon_count == 0 else NOT_YET,
            sample=int(recon_count),
        )
    )

    criteria.extend(
        _log_criteria(session, calendar, session_date, config)
    )

    return criteria


def _log_criteria(
    session: Session,
    calendar: CalendarProvider,
    session_date: date,
    config: AppConfig,
) -> list[dict[str, Any]]:
    """The two log-index Operations criteria (docs/SPEC.md, Readiness).

    An unavailable or incomplete index means "not yet", never pass.
    "Errors in the log" counts unreviewed ERROR fingerprints in the last
    10 sessions: ``expected`` stays reviewed when it recurs, ``fixed``
    becomes unreviewed again once a newer line appears after the review.
    "Critical lines in the log" counts CRITICAL lines in the last 20
    sessions — never reviewable.
    """
    import sqlite3

    unavailable = [
        _criterion(key, value="—", status=NOT_YET,
                   sub="log index unavailable")
        for key in ("log_errors", "log_critical")
    ]
    db_path = index_path(config)
    if not index_available(db_path):
        return unavailable

    start10 = _window_start(calendar, session_date, 10).isoformat()
    start20 = _window_start(calendar, session_date, 20).isoformat()
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        error_fps = conn.execute(
            "SELECT fingerprint, count(*), max(ts) FROM log_lines "
            "WHERE level = 'ERROR' AND ts >= ? AND fingerprint IS NOT NULL "
            "GROUP BY fingerprint",
            (start10,),
        ).fetchall()
        critical_n = int(
            conn.execute(
                "SELECT count(*) FROM log_lines WHERE level = 'CRITICAL' "
                "AND ts >= ?",
                (start20,),
            ).fetchone()[0]
        )
    # Latest review per fingerprint (reviews are append-only).
    from app.db.models import LogReview

    reviews: dict[str, tuple[str, datetime]] = {}
    for fp, review, reviewed_at in session.execute(
        select(
            LogReview.fingerprint, LogReview.review, LogReview.reviewed_at
        ).order_by(LogReview.reviewed_at)
    ):
        reviews[fp] = (review, reviewed_at)
    unreviewed = 0
    for fp, _n, last_ts in error_fps:
        state = reviews.get(fp)
        if state is None:
            unreviewed += 1
        elif state[0] == "fixed" and last_ts > _aware(state[1]).isoformat():
            unreviewed += 1  # a Fixed problem that recurs is unreviewed again
    return [
        _criterion(
            "log_errors",
            value=str(unreviewed),
            status=PASS if unreviewed == 0 else NOT_YET,
            sample=int(unreviewed),
        ),
        _criterion(
            "log_critical",
            value=str(critical_n),
            status=PASS if critical_n == 0 else NOT_YET,
            sample=int(critical_n),
        ),
    ]


def due_session_dates(
    session: Session, *, calendar: CalendarProvider, clock: Clock
) -> list[date]:
    """Closed NY sessions since the run's start with no snapshot yet."""
    run = _latest_run(session)
    if run is None:
        return []
    now = clock.now()
    run_day = _aware(run.started_at).astimezone(NY_TZ).date()
    today = now.astimezone(NY_TZ).date()
    have = {
        row[0]
        for row in session.execute(
            select(ReadinessSnapshot.session_date).where(
                ReadinessSnapshot.run_id == run.id
            )
        ).all()
    }
    due: list[date] = []
    day = run_day
    while day <= today and len(due) < 600:
        trading = calendar.session_for(day)
        if (
            trading is not None
            and trading.close < now
            and day not in have
        ):
            due.append(day)
        day += timedelta(days=1)
    return due


def write_snapshot(
    session: Session,
    *,
    config: AppConfig,
    calendar: CalendarProvider,
    clock: Clock,
    session_date: date,
    notifier: Notifier | None = None,
) -> ReadinessSnapshot | None:
    """Evaluate and store the snapshot for ``session_date``.

    Status changes against the previous snapshot fire the
    "Readiness changed" Info notification (a criterion passing or
    stopping to pass only).
    """
    run = _latest_run(session)
    if run is None:
        return None
    now = clock.now()
    today = now.astimezone(NY_TZ).date()
    day = calendar.session_for(session_date)
    as_of = (
        day.close.astimezone(UTC)
        if day is not None and session_date < today and day.close < now
        else now
    )
    resolve_minutes = int(_cfg(config, "error_auto_resolve_minutes", 60))
    apply_auto_resolution(session, now=now, minutes=resolve_minutes)

    previous = session.execute(
        select(ReadinessSnapshot)
        .where(ReadinessSnapshot.run_id == run.id)
        .order_by(ReadinessSnapshot.session_date.desc(), ReadinessSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    criteria = evaluate(
        session,
        config=config,
        calendar=calendar,
        clock=clock,
        session_date=session_date,
        as_of=as_of,
    )
    snapshot = session.execute(
        select(ReadinessSnapshot).where(
            ReadinessSnapshot.run_id == run.id,
            ReadinessSnapshot.session_date == session_date,
        )
    ).scalar_one_or_none()
    if snapshot is None:
        snapshot = ReadinessSnapshot(
            session_date=session_date, run_id=run.id, criteria=[]
        )
        session.add(snapshot)
    snapshot.criteria = criteria
    snapshot.created_at = _naive(now)
    session.flush()

    if previous is not None and previous.id != snapshot.id:
        notifier = notifier or NullNotifier()
        prev_status = {c["key"]: c["status"] for c in previous.criteria}
        for criterion in criteria:
            was = prev_status.get(criterion["key"])
            now_status = criterion["status"]
            if was is None or was == now_status:
                continue
            if was == PASS or now_status == PASS:
                verb = "now passes" if now_status == PASS else "stopped passing"
                notifier.notify(
                    "readiness_changed",
                    f"{criterion['label']} {verb} ({criterion['value']})",
                    link="/readiness",
                )
    return snapshot


def latest_snapshot(
    session: Session,
) -> tuple[ReadinessSnapshot | None, Run | None]:
    run = _latest_run(session)
    if run is None:
        return None, None
    snapshot = session.execute(
        select(ReadinessSnapshot)
        .where(ReadinessSnapshot.run_id == run.id)
        .order_by(ReadinessSnapshot.session_date.desc(), ReadinessSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return snapshot, run


def _manual_state(session: Session) -> list[dict[str, Any]]:
    latest: dict[str, ReadinessManual] = {}
    for row in session.execute(
        select(ReadinessManual).order_by(ReadinessManual.id)
    ).scalars():
        latest[row.item] = row
    return [
        {
            "item": key,
            "label": label,
            "detail": detail,
            "done": latest[key].done if key in latest else False,
            "note": latest[key].note if key in latest else None,
            "changed_at": (
                _aware(latest[key].changed_at).isoformat()
                if key in latest
                else None
            ),
        }
        for key, label, detail in MANUAL_ITEMS
    ]


_STATUS_TEXT = {
    PASS: "passes",
    NOT_YET: "back to not yet",
    WAITING: "waiting for data again",
}


def _history(session: Session, run: Run | None) -> list[dict[str, Any]]:
    """Status changes per criterion and every manual change, dated."""
    entries: list[dict[str, Any]] = (
        [
            {
                "at": _aware(run.started_at).isoformat(),
                "kind": "run",
                "change": "Run started; all counters reset",
            }
        ]
        if run is not None
        else []
    )
    snapshots = (
        session.execute(
            select(ReadinessSnapshot)
            .where(ReadinessSnapshot.run_id == run.id)
            .order_by(ReadinessSnapshot.session_date, ReadinessSnapshot.id)
        ).scalars()
        if run is not None
        else iter(())
    )
    prev: dict[str, str] = {}
    labels = {m[0]: m[2] for m in _META}
    for snapshot in snapshots:
        at = datetime.combine(snapshot.session_date, dtime.min, tzinfo=UTC)
        for criterion in snapshot.criteria:
            key = criterion["key"]
            status = criterion["status"]
            if key in prev and prev[key] != status:
                value = criterion.get("value")
                entries.append(
                    {
                        "at": _aware(at).isoformat(),
                        "kind": "criterion",
                        "change": (
                            f"{labels.get(key, key)} {_STATUS_TEXT[status]}"
                            + (f" ({value})" if value not in (None, "—") else "")
                        ),
                    }
                )
            prev[key] = status
    from app.db.models import LogReview

    for review_row in session.execute(
        select(LogReview).order_by(LogReview.reviewed_at)
    ).scalars():
        entries.append(
            {
                "at": _aware(review_row.reviewed_at).isoformat(),
                "kind": "log_review",
                "change": (
                    f"Log problem marked {review_row.review} — {review_row.note}"
                ),
            }
        )
    for manual_row in session.execute(
        select(ReadinessManual).order_by(ReadinessManual.changed_at)
    ).scalars():
        label = next(
            (lbl for key, lbl, _d in MANUAL_ITEMS if key == manual_row.item),
            manual_row.item,
        )
        entries.append(
            {
                "at": _aware(manual_row.changed_at).isoformat(),
                "kind": "manual",
                "change": (
                    f"{label} {'ticked' if manual_row.done else 'unticked'}"
                    f" — {manual_row.note}"
                ),
            }
        )
    # Readiness threshold changes are versioned settings changes and show
    # in History too (docs/SPEC.md, Readiness — History).
    from app.settings_service import LABELS

    previous_readiness: dict[str, Any] | None = None
    for version_row in session.execute(
        select(SettingsVersion).order_by(
            SettingsVersion.changed_at, SettingsVersion.id
        )
    ).scalars():
        current = cast(
            "dict[str, Any]", (version_row.settings or {}).get("readiness") or {}
        )
        if previous_readiness is not None:
            for key in sorted(set(previous_readiness) | set(current)):
                old, new = previous_readiness.get(key), current.get(key)
                if old == new:
                    continue
                change = (
                    f"{LABELS.get(('readiness', key), key)} {old} → {new}"
                )
                if version_row.reason:
                    change += f" — {version_row.reason}"
                entries.append(
                    {
                        "at": _aware(version_row.changed_at).isoformat(),
                        "kind": "settings",
                        "change": change,
                    }
                )
        previous_readiness = current
    entries.sort(key=lambda e: str(e["at"]), reverse=True)
    return entries[:50]


def _error_message(event: Event) -> str:
    details = event.details or {}
    symbol = str(details.get("symbol") or "")
    error = str(details.get("error") or details.get("reason") or "")
    label = event.event_type.replace("_", " ")
    parts = [part for part in (label, symbol, error) if part]
    return " · ".join(parts)


def payload(
    session: Session,
    *,
    config: AppConfig,
    clock: Clock,
    run: Run | None = None,
    snapshot: ReadinessSnapshot | None = None,
) -> dict[str, Any]:
    """The Readiness screen data: latest stored snapshot plus the live
    manual/error state. Never evaluates."""
    if run is None or snapshot is None:
        snapshot, run = latest_snapshot(session)
    criteria = list(snapshot.criteria) if snapshot else []
    counts = Counter(c["status"] for c in criteria)
    manual = _manual_state(session)
    manual_done = sum(1 for m in manual if m["done"])
    earliest: dict[str, Any] | None = None
    today = clock.now().astimezone(NY_TZ).date()
    for criterion in criteria:
        raw = criterion.get("estimate_date")
        if not raw:
            continue
        day = date.fromisoformat(str(raw))
        if earliest is None or day > date.fromisoformat(str(earliest["date"])):
            earliest = {
                "label": criterion["label"],
                "date": day.isoformat(),
                "text": _fmt_estimate(day, today),
            }
    resolve_minutes = int(_cfg(config, "error_auto_resolve_minutes", 60))
    open_errors = unresolved_errors(
        session, as_of=clock.now(), window_start=None, minutes=resolve_minutes
    )
    # "Resolves at" is the quiet timer from the group's latest recurrence.
    last_seen: dict[str, datetime] = {}
    for error in open_errors:
        last_seen[error.event_type] = max(
            last_seen.get(error.event_type, error.time), error.time
        )
    return {
        "run": (
            {
                "id": run.id,
                "started_at": _aware(run.started_at).isoformat(),
            }
            if run is not None
            else None
        ),
        "snapshot": (
            {
                "session_date": snapshot.session_date.isoformat(),
                "created_at": _aware(snapshot.created_at).isoformat(),
                "criteria": criteria,
            }
            if snapshot is not None
            else None
        ),
        "summary": {
            "pass": counts.get(PASS, 0),
            "not_yet": counts.get(NOT_YET, 0),
            "waiting": counts.get(WAITING, 0),
            "total": len(criteria),
            "manual_done": manual_done,
            "manual_total": len(MANUAL_ITEMS),
            "earliest": earliest,
        },
        "manual": manual,
        "errors": [
            {
                "id": e.id,
                "time": _aware(e.time).isoformat(),
                "level": e.level,
                "event_type": e.event_type,
                "affects": e.affects,
                "message": _error_message(e),
                "resolves_at": _aware(
                    last_seen[e.event_type] + timedelta(minutes=resolve_minutes)
                ).isoformat(),
            }
            for e in open_errors
        ],
        "history": _history(session, run),
    }


def tick(
    *,
    config: AppConfig,
    calendar: CalendarProvider,
    clock: Clock,
    notifier: Notifier | None = None,
    session_factory: sessionmaker[Session] | None = None,
    publish: Any = None,
) -> int:
    """Scheduler heartbeat: write every due session-close snapshot.

    Called once a minute; backfills sessions missed while the app was
    down. One short write transaction per snapshot — no I/O is held
    inside it (everything reads stored rows).
    """
    factory = session_factory or get_session_maker()
    written = 0
    with factory() as session:
        due = due_session_dates(session, calendar=calendar, clock=clock)
        for session_date in due:
            write_snapshot(
                session,
                config=config,
                calendar=calendar,
                clock=clock,
                session_date=session_date,
                notifier=notifier,
            )
            written += 1
        if written:
            session.commit()
    if written and publish is not None:
        publish({"readiness"})
    return written
