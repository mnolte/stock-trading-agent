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
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import (
    Approval,
    Cycle,
    Decision,
    Event,
    Position,
    SettingsVersion,
    Verdict,
)
from app.log_index import index_available, index_path
from app.market.clock import NY_TZ, UTC, SessionClock, make_clock
from app.readiness.service import unreviewed_error_fingerprints
from app.secrets_service import GROUP_LABELS, GROUP_ORDER
from app.settings_service import version_change_summary

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)


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
    calendar = getattr(request.app.state, "calendar_store", None)
    session_state = SessionClock(clock, config, calendar).state()

    items: list[dict[str, Any]] = []

    with get_session_maker()() as session:
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
        if getattr(state, "live_calls_failing_since", None) is not None:
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

    # 4. A notification channel failing: the last email send failed and
    #    the counter clears only on a send that succeeds.
    email_failures = (
        notifier.email_failures if notifier is not None else 0
    )
    if email_failures > 0:
        items.append(
            _item(
                "channel",
                "warning",
                "Email alerts are failing.",
                f"{email_failures} notification email"
                f"{'s' if email_failures != 1 else ''} failed in a row. "
                "Check the SMTP details under Settings → Notifications.",
                "Fix in Settings",
                "/settings",
                None,
            )
        )

    with get_session_maker()() as session:
        # 5. Errors not reviewed — the count Readiness uses.
        unreviewed = (
            unreviewed_error_fingerprints(
                session, config, calendar, today_ny
            )
            if calendar is not None
            else None
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
        },
    }
