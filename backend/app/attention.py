"""The Needs you list (docs/SPEC.md, GUI — Needs you; PR 35).

One function that builds the list in the spec's order from what already
exists: the same unacknowledged events the banner strip serves, the
episode flags on the execution state, pending approvals, the
notification service's email counter, the Readiness unreviewed-error
count, draft verdicts and the pending settings version. Each item leaves
when its source condition clears — never when it was seen.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.config import get_config
from app.data.cache import _aware_utc
from app.db.base import get_session_maker
from app.db.models import (
    Approval,
    Cycle,
    Decision,
    Earning,
    Event,
    Notification,
    Position,
    SettingsVersion,
    ShadowDecision,
    Strategy,
    Verdict,
)
from app.log_index import index_path
from app.market.calendar import WeekdayCalendarProvider
from app.market.clock import NY_TZ, UTC, SessionClock, make_clock
from app.readiness.service import unreviewed_error_fingerprints
from app.secrets_service import GROUP_LABELS, GROUP_ORDER
from app.settings_service import version_change_summary

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def _dec(value: Any) -> Any:
    from decimal import Decimal

    return Decimal(str(value)) if value is not None else None


def _iso(dt: datetime | None) -> str | None:
    return dt.replace(tzinfo=UTC).isoformat() if dt is not None else None


def _item(
    kind: str,
    severity: str,
    text: str,
    detail: str,
    action_label: str,
    link: str,
    created_at: datetime | str | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "text": text,
        "detail": detail,
        "action_label": action_label,
        "link": link,
        "created_at": (
            created_at.replace(tzinfo=UTC).isoformat()
            if isinstance(created_at, datetime)
            else created_at
        ),
    }


def _fingerprint_message(db_path: Any, fingerprint: str) -> str | None:
    try:
        with sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True
        ) as conn:
            row = conn.execute(
                "SELECT message FROM log_lines WHERE fingerprint = ? "
                "ORDER BY ts DESC LIMIT 1",
                (fingerprint,),
            ).fetchone()
    except sqlite3.Error:
        logger.warning("log index read failed for a Needs you item")
        return None
    return str(row[0]) if row else None


def build_attention(request: Request) -> dict[str, Any]:
    """The Needs you list plus the health line (docs/SPEC.md order)."""
    config = get_config()
    state = getattr(request.app.state, "exec_state", None)
    clock = getattr(request.app.state, "clock", None) or make_clock()
    now = clock.now()
    today_ny = now.astimezone(NY_TZ).date()
    app_tz = ZoneInfo(
        str(config.get("app", "timezone", default="Europe/Amsterdam"))
    )
    store = getattr(request.app.state, "secrets_store", None)
    notifier = getattr(request.app.state, "notification_service", None)
    broker = getattr(request.app.state, "broker", None)
    ops = getattr(request.app.state, "ops", None)
    calendar_store = getattr(request.app.state, "calendar_store", None)
    calendar = (
        calendar_store
        if calendar_store is not None
        else WeekdayCalendarProvider()
    )
    session_state = SessionClock(clock, config, calendar).state()

    items: list[dict[str, Any]] = []
    email_failures = notifier.email_failures if notifier is not None else 0

    with get_session_maker()() as session:
        # 0. Open stop-missing episodes — first, critical, and they
        # leave only when the episode closes; no acknowledgement
        # (design/screens/States.dc.html).
        episodes = session.execute(
            select(Event).where(
                Event.event_type == "stop_missing",
                Event.resolved_at.is_(None),
            ).order_by(Event.time)
        ).scalars().all()
        for event in episodes:
            details = event.details or {}
            symbol = str(details.get("symbol") or "?")
            shares = str(details.get("shares") or "")
            since = _aware_utc(event.time).astimezone(app_tz).strftime("%H:%M")
            reason = str(details.get("reason") or "no live stop at Alpaca")
            items.append(
                _item(
                    "stop_missing",
                    "critical",
                    f"{symbol} has no stop at Alpaca since {since}",
                    f"{shares} shares · {reason}. "
                    "Code retries at every check.",
                    "Open position",
                    "/positions",
                    event.time,
                )
            )
        # 1. Critical banners waiting for acknowledgement.
        unacked = session.execute(
            select(Event)
            .where(
                Event.acknowledged_at.is_(None),
                Event.event_type.in_(
                    ("reconciliation", "protective_stop", "another_instance")
                ),
            )
            .order_by(Event.id.desc())
            .limit(20)
        ).scalars().all()
        for event in unacked:
            details = event.details or {}
            symbol = str(details.get("symbol") or "")
            if event.event_type == "another_instance":
                items.append(
                    _item(
                        "banner_ack",
                        "critical",
                        "Another instance is trading this Alpaca account.",
                        "New entries are blocked until you acknowledge.",
                        "Acknowledge",
                        "/",
                        event.time,
                    )
                )
            elif event.event_type == "protective_stop":
                items.append(
                    _item(
                        "banner_ack",
                        "critical",
                        f"{symbol} had no stop order at Alpaca.",
                        f"A protective stop at {details.get('stop')} was "
                        "placed; acknowledge after checking the orders.",
                        "Acknowledge",
                        "/positions",
                        event.time,
                    )
                )
            else:
                kind = str(details.get("kind", "mismatch"))
                if kind == "adopted_position":
                    text = (
                        f"Alpaca shows {details.get('qty')} {symbol} shares "
                        "the agent never opened."
                    )
                elif kind == "qty_mismatch":
                    text = (
                        f"Alpaca shows {details.get('broker_qty')} {symbol} "
                        f"shares; the agent recorded {details.get('db_qty')}."
                    )
                else:
                    text = "Reconciliation found an order it never recorded."
                items.append(
                    _item(
                        "banner_ack",
                        "critical",
                        text,
                        "Alpaca's numbers are used; new entries are blocked "
                        "until you acknowledge.",
                        "Acknowledge",
                        "/positions",
                        event.time,
                    )
                )
        # Held-back exits are their own Needs-you kind
        # (design/screens/States.dc.html): "exit held back since …",
        # Open position — and the item leaves when the exit fills. The
        # event's resolved_at clears by condition in the executor; the
        # position check covers the gap until the next reconcile.
        held_back_events = session.execute(
            select(Event)
            .where(
                Event.event_type == "pending_exit",
                Event.resolved_at.is_(None),
            )
            .order_by(Event.id.desc())
            .limit(20)
        ).scalars().all()
        seen_pending: set[str] = set()
        for event in held_back_events:
            details = event.details or {}
            symbol = str(details.get("symbol") or "")
            if not symbol or symbol in seen_pending:
                continue
            seen_pending.add(symbol)
            held_position = session.execute(
                select(Position)
                .where(Position.symbol == symbol)
                .order_by(Position.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            exit_pending = (
                dict(held_position.pending_exit)
                if held_position is not None and held_position.pending_exit
                else {}
            )
            if held_position is not None and (
                held_position.closed_at is not None or not exit_pending
            ):
                continue
            held_since = event.time
            since_raw = exit_pending.get("since")
            if since_raw:
                try:
                    held_since = datetime.fromisoformat(str(since_raw))
                except ValueError:
                    logger.debug(
                        "pending_exit since unparsable for %s: %r",
                        symbol,
                        since_raw,
                    )
            since_hm = (
                _aware_utc(held_since).astimezone(app_tz).strftime("%H:%M")
            )
            failure = str(exit_pending.get("failure") or "")
            tries = int(str(exit_pending.get("tries") or 0))
            stop = (
                held_position.current_stop_price
                if held_position is not None
                else None
            )
            if exit_pending:
                what = (
                    failure[:1].upper() + failure[1:]
                    if failure
                    else "Exit held back"
                )
                text = f"{symbol} exit held back since {since_hm}."
                detail = (
                    f"{what} at Alpaca"
                    + (
                        f" after {tries} tr{'y' if tries == 1 else 'ies'}"
                        if tries
                        else ""
                    )
                    + (
                        f"; the stop at {stop:.2f} still stands"
                        if stop is not None
                        else ""
                    )
                    + ". Retried at every check; market-on-open if still "
                    "pending."
                )
            else:
                text = str(
                    details.get("message")
                    or f"{symbol} exit held back since {since_hm}."
                )
                detail = "Check the position and orders at Alpaca."
            items.append(
                _item(
                    "pending_exit",
                    "critical",
                    text,
                    detail,
                    "Open position",
                    "/positions",
                    _aware_utc(held_since).astimezone(UTC),
                )
            )
        if (
            state is not None
            and state.kill_switch_on
            and state.kill_switch_auto
        ):
            items.append(
                _item(
                    "banner_ack",
                    "critical",
                    "The kill switch turned on automatically.",
                    (
                        f"{state.kill_switch_reason}. New entries are "
                        "blocked until you resume."
                        if state.kill_switch_reason
                        else "New entries are blocked until you resume."
                    ),
                    "Resume",
                    "/",
                    state.kill_switch_since,
                )
            )

        # 2. Live decisions failing; the trade-updates stream degraded;
        #    a key failing.
        if (
            state is not None
            and state.live_calls_failing_since is not None
        ):
            provider = state.live_calls_failing_provider or ""
            error = state.live_calls_failing_error or ""
            items.append(
                _item(
                    "episode",
                    "critical",
                    "Live decisions are failing.",
                    f"{provider} {error}".strip()
                    + " — held positions are not being reviewed; their "
                    "stops at Alpaca still stand.",
                    "Open the inspector",
                    "/inspector",
                    state.live_calls_failing_since,
                )
            )
        if state is not None and state.stream_degraded:
            items.append(
                _item(
                    "episode",
                    "critical",
                    "Order updates are delayed.",
                    "Positions are checked every 15 minutes while the "
                    "trade-updates stream is degraded.",
                    "Open positions",
                    "/positions",
                    state.stream_degraded_since,
                )
            )
        if store is not None and config.env.mode != "simulated":
            for group in GROUP_ORDER:
                if store.group_status(group)["status"] == "failing":
                    items.append(
                        _item(
                            "episode",
                            "warning",
                            f"The {GROUP_LABELS[group]} key is failing.",
                            "The agent stays paused until every required "
                            "key passes its test.",
                            "Fix in Settings",
                            "/settings",
                            None,
                        )
                    )

        # 3. Entries waiting for approval, with their expiry.
        pending = session.execute(
            select(Approval).where(Approval.status == "pending")
        ).scalars().all()
        if pending:
            expires = (
                session_state.next_cycle.astimezone(app_tz).strftime("%H:%M")
                if session_state.next_cycle is not None
                else "next"
            )
            for approval in pending:
                items.append(
                    _item(
                        "approval",
                        "warning",
                        f"{approval.symbol} waits for approval.",
                        f"{approval.qty} sh @ {approval.limit_price} · "
                        f"expires at the {expires} cycle.",
                        "Review",
                        "/",
                        approval.created_at,
                    )
                )

        # 4. A notification channel failing: the last email send failed
        #    and the counter clears only on a send that succeeds.
        if email_failures > 0:
            items.append(
                _item(
                    "channel",
                    "warning",
                    "Email alerts are failing.",
                    f"{email_failures} notification email"
                    f"{'s' if email_failures != 1 else ''} failed in a "
                    "row. Check the SMTP details under Settings → "
                    "Notifications.",
                    "Fix in Settings",
                    "/settings",
                    None,
                )
            )

        # 5. Errors not reviewed — the count Readiness uses.
        unreviewed = unreviewed_error_fingerprints(
            session, config, calendar, today_ny
        )
        if unreviewed:
            names = [
                name
                for name in (
                    _fingerprint_message(index_path(config), fp)
                    for fp in list(unreviewed)[:3]
                )
                if name
            ]
            n = len(unreviewed)
            detail = " · ".join(names[:2])
            items.append(
                _item(
                    "errors",
                    "warning",
                    f"{n} error{'s' if n != 1 else ''} not reviewed.",
                    (detail + " · " if detail else "")
                    + "Readiness waits for these.",
                    "Review in Logs",
                    "/logs",
                    max(unreviewed.values()),
                )
            )

        # 5b. A held decision in the last cycle with confidence_outlier
        #     and r_now < 0 (docs/SPEC.md, Decision v5 — Code checks):
        #     KO held overnight at −0.62R with confidence 0.65 against
        #     neutral 0.12 is the example.
        ny_session = calendar.session_for(today_ny)
        last_cycles = (
            SessionClock(clock, config, calendar).active_cycles(ny_session)
            if ny_session is not None
            else []
        )
        if last_cycles:
            last_cycle_utc = _naive(last_cycles[-1].astimezone(UTC))
            held_rows: list[tuple[Decision | ShadowDecision, str | None]] = [
                (d, None)
                for d in session.execute(
                    select(Decision)
                    .join(Cycle, Decision.cycle_id == Cycle.id)
                    .where(
                        Decision.context == "held",
                        Cycle.scheduled_time == last_cycle_utc,
                    )
                ).scalars()
            ]
            for shadow in session.execute(
                select(ShadowDecision)
                .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
                .where(
                    ShadowDecision.context == "held",
                    ShadowDecision.missed.is_(False),
                    Cycle.scheduled_time == last_cycle_utc,
                )
            ).scalars():
                strategy = session.get(Strategy, shadow.strategy_id)
                held_rows.append(
                    (shadow, strategy.name if strategy is not None else None)
                )
            for row, strategy_name in held_rows:
                validation = (
                    row.validation_result
                    if isinstance(row.validation_result, dict)
                    else {}
                )
                snapshot = (
                    row.input_snapshot
                    if isinstance(row.input_snapshot, dict)
                    else {}
                )
                warnings = (
                    cast("list[Any]", validation.get("warnings") or [])
                )
                position = cast(
                    "dict[str, Any]", snapshot.get("position") or {}
                )
                r_now = _dec(position.get("r_now"))
                neutral = _dec(position.get("neutral_probability"))
                conf = _dec(validation.get("confidence"))
                if "confidence_outlier" not in warnings or (
                    r_now is not None and r_now >= 0
                ):
                    continue
                who = f"{strategy_name} · " if strategy_name else ""
                items.append(
                    _item(
                        "decision",
                        "warning",
                        f"{who}{row.symbol} held overnight at a loss.",
                        f"r_now {float(r_now):+.2f} · confidence "
                        f"{float(conf):.2f} against neutral "
                        f"{float(neutral):.2f} — above the exit bar."
                        if r_now is not None and conf is not None
                        and neutral is not None
                        else "confidence_outlier on a losing hold.",
                        "Open the inspector",
                        "/inspector",
                        last_cycles[-1].astimezone(UTC),
                    )
                )

        # 6. Verdicts to confirm.
        verdicts = session.execute(
            select(func.count(Verdict.id), func.min(Verdict.created_at))
            .where(Verdict.source == "draft")
        ).one()
        waiting = int(verdicts[0] or 0)
        if waiting:
            items.append(
                _item(
                    "verdicts",
                    "info",
                    f"{waiting} verdict{'s' if waiting != 1 else ''} to "
                    "confirm.",
                    "Draft labels are written by code from the outcome.",
                    "Confirm",
                    "/reviews",
                    verdicts[1],
                )
            )

        # 7. Settings or prompts saved but waiting for the next cycle.
        pending_tree = config.pending()
        if pending_tree is not None:
            summary = version_change_summary(
                config.effective(), pending_tree
            )
            pending_row = session.execute(
                select(SettingsVersion)
                .where(SettingsVersion.version == config.pending_version)
            ).scalar_one_or_none()
            items.append(
                _item(
                    "settings",
                    "info",
                    f"Settings v{config.pending_version} apply at the next "
                    "cycle.",
                    summary or "Saved changes wait for the next cycle.",
                    "Open Settings",
                    "/settings",
                    pending_row.changed_at if pending_row else None,
                )
            )

        # 8. An open position whose earnings report lands tonight — its
        #    scheduled earnings exit runs at the close (docs/SPEC.md,
        #    Needs you).
        for symbol, tod in session.execute(
            select(Position.symbol, Earning.time_of_day)
            .join(Earning, Earning.symbol == Position.symbol)
            .where(
                Position.closed_at.is_(None),
                func.date(Earning.date) == today_ny.isoformat(),
            )
        ):
            if str(tod) == "amc":
                items.append(
                    _item(
                        "decision",
                        "info",
                        f"{symbol} earnings exit at the close today.",
                        "The position leaves at the close — its earnings "
                        "report lands after the close.",
                        "Open Positions",
                        "/positions",
                        None,
                    )
                )

    # Health line: Alpaca sync, order stream, clock, keys, email.
    reconcile_at = getattr(state, "last_reconcile_at", None)
    stream_connected = (
        broker.stream_connected() if broker is not None else None
    )
    if state is not None and state.stream_degraded:
        stream = "degraded"
    elif stream_connected is True:
        stream = "connected"
    elif stream_connected is False:
        stream = "disconnected"
    else:
        stream = "unknown"
    clock_info = (ops.status.get("clock") or {}) if ops is not None else {}
    keys: dict[str, Any]
    if config.env.mode == "simulated":
        keys = {"status": "passing", "detail": "simulated mode"}
    elif store is None:
        keys = {"status": "failing", "detail": "no keys saved"}
    elif store.all_ready():
        keys = {"status": "passing", "detail": "all passing"}
    else:
        from app.api.routes import _keys_issue

        keys = {
            "status": "issue",
            "detail": _keys_issue(request) or "check the keys",
        }
    smtp = notifier.smtp_status() if notifier is not None else {}
    email = (
        "failing"
        if email_failures > 0
        else "on"
        if smtp.get("configured")
        else "off"
    )
    email_since = None
    if email_failures > 0:
        # The streak's first failure — the health line's "failing since".
        failed_times = session.execute(
            select(Notification.time)
            .where(
                func.json_extract(
                    Notification.channel_status, "$.email"
                )
                == "failed"
            )
            .order_by(Notification.time.desc())
            .limit(email_failures)
        ).scalars().all()
        if failed_times:
            email_since = _iso(min(failed_times))

    return {
        "items": items,
        "count": len(items),
        "health": {
            "alpaca": {
                "ok": bool(getattr(state, "last_reconcile_ok", True)),
                "at": _iso(reconcile_at),
            },
            "stream": stream,
            "clock": {
                "offset_ms": clock_info.get("offset_ms"),
                "level": clock_info.get("level", "unknown"),
            },
            "keys": keys,
            "email": email,
            "email_since": email_since,
            "heartbeat": (
                "Heartbeat not set"
                if ops is None or not ops.heartbeat_url()
                else "set"
            ),
        },
    }
