"""Notification events and their spec-defined severity (docs/SPEC.md,
Notifications).

Each event maps to one row in Settings → Notifications. ``group`` is the
table group from the design (Critical / Warnings / Trading / Reviews),
``severity`` decides behaviour: critical events always include the
browser channel and are never throttled or held by quiet hours. Severity
comes from the spec — never from the ``events`` table level, whose
wording predates notifications.

``format_recorded`` translates a durable ``events`` row (written by
``record_event``) into ``(event key, one-line subject text, GUI link)``.
Unmapped event types raise no notification.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventDef:
    key: str
    label: str  # the row name in Settings → Notifications
    description: str  # the small line under the row name
    group: str  # critical | warnings | trading | reviews (design order)
    severity: str  # critical | warning | info


CRITICAL = "critical"
WARNING = "warning"
INFO = "info"

# The spec's critical list: these always include the browser channel and
# skip the repeat limit and quiet hours.
CRITICAL_EVENTS = {
    "kill_switch",
    "missing_stop",
    "reconciliation",
    "order_rejected",
    "another_instance",
}

# Table group headers, in design order (screens/Settings.dc.html).
GROUPS: tuple[tuple[str, str], ...] = (
    ("critical", "Critical · always sent, never held back"),
    ("warnings", "Warnings"),
    ("trading", "Trading"),
    ("reviews", "Reviews"),
)

# Labels and descriptions come from the design's Notifications table.
EVENT_DEFS: tuple[EventDef, ...] = (
    EventDef(
        "kill_switch",
        "Kill switch turned on",
        "Manual, drawdown kill or 3 failed cycles",
        "critical",
        CRITICAL,
    ),
    EventDef(
        "missing_stop",
        "Position without a stop at Alpaca",
        "A protective stop was placed",
        "critical",
        CRITICAL,
    ),
    EventDef(
        "reconciliation",
        "Reconciliation mismatch or adopted position",
        "New entries blocked until you acknowledge",
        "critical",
        CRITICAL,
    ),
    EventDef(
        "order_rejected",
        "Order rejected by Alpaca",
        "Including a Stop & flatten sale",
        "critical",
        CRITICAL,
    ),
    EventDef(
        "daily_loss_limit",
        "Daily loss limit reached",
        "No new entries for the rest of the session",
        "warnings",
        WARNING,
    ),
    EventDef(
        "shock_brake",
        "Shock brake on",
        "SPY dropped too fast; no new entries this session",
        "warnings",
        WARNING,
    ),
    EventDef(
        "source_failing",
        "Data source or API key failing",
        "Alpaca, OpenAI, Finnhub or ECB",
        "warnings",
        WARNING,
    ),
    EventDef(
        "position_checks_failing",
        "Position checks failing",
        "3 in a row",
        "warnings",
        WARNING,
    ),
    EventDef(
        "model_failing",
        "Model failing, or review or shadow cost cap reached",
        "",
        "warnings",
        WARNING,
    ),
    EventDef(
        "approval_waiting",
        "Entry waiting for approval",
        "Supervised mode; expires at the next cycle",
        "trading",
        INFO,
    ),
    EventDef(
        "exit_deferred_or_earnings_exit",
        "Earnings exit tomorrow",
        "Held symbol reports before the next close",
        "trading",
        INFO,
    ),
    EventDef(
        "trade",
        "Trade opened or closed",
        "Fills, stops and targets",
        "trading",
        INFO,
    ),
    EventDef(
        "daily_briefing",
        "Daily briefing",
        "Trading days 15:00 Amsterdam",
        "reviews",
        INFO,
    ),
    EventDef(
        "weekly_review",
        "Weekly review ready",
        "Saturday 10:00",
        "reviews",
        INFO,
    ),
    EventDef(
        "verdicts_waiting",
        "Verdicts waiting to confirm",
        "Once a day, with the briefing",
        "reviews",
        INFO,
    ),
    EventDef(
        "readiness_changed",
        "Readiness changed",
        "A criterion passes or stops passing",
        "reviews",
        INFO,
    ),
    EventDef(
        "another_instance",
        "Another instance is trading this Alpaca account",
        "An order or fill carries another instance id — kill switch on",
        "critical",
        CRITICAL,
    ),
    EventDef(
        "new_sign_in",
        "New sign-in",
        "A device signed in",
        "warnings",
        INFO,
    ),
    EventDef(
        "sign_in_lockout",
        "Failed sign-ins: lockout started",
        "5 failures from an address, or 20 in an hour overall",
        "warnings",
        WARNING,
    ),
    EventDef(
        "restarted_resumed",
        "Restarted and resumed",
        "Clean planned restart on the server; every check passed",
        "warnings",
        INFO,
    ),
    EventDef(
        "restarted_paused",
        "Restarted paused",
        "New entries stay paused until you press Resume",
        "warnings",
        WARNING,
    ),
    EventDef(
        "backup_failed",
        "Backup or restore test failed",
        "Local copy, offsite upload or the monthly check",
        "warnings",
        WARNING,
    ),
    EventDef(
        "disk_space",
        "Disk space",
        "Data volume 80% full (90% is critical)",
        "warnings",
        WARNING,
    ),
    EventDef(
        "cert_expiring",
        "HTTPS certificate expiring",
        "Expires within 14 days",
        "warnings",
        WARNING,
    ),
    EventDef(
        "clock_offset",
        "Clock out of sync",
        "Offset over 1 second; cycles depend on the exact time",
        "warnings",
        WARNING,
    ),
)

EVENTS: dict[str, EventDef] = {d.key: d for d in EVENT_DEFS}

# System events that are not configurable rows in Settings.
SYSTEM_SEVERITY = {"test": INFO, "quiet_summary": INFO}
SYSTEM_LABELS = {"test": "Test", "quiet_summary": "Quiet hours summary"}

# record_event event_type → notification event key. Event types not
# listed raise no notification (pause, order_recovered, run_started, …).
EVENT_TYPE_MAP: dict[str, str] = {
    "kill_switch": "kill_switch",
    "protective_stop": "missing_stop",
    "protective_stop_failed": "missing_stop",
    "reconciliation": "reconciliation",
    "order_submit_failed": "order_rejected",
    "order_sanity": "order_rejected",
    "flatten_sell_rejected": "order_rejected",
    "flatten_sell_blocked": "order_rejected",
    "leg_cancel_unconfirmed": "order_rejected",
    "position_check_failed": "position_checks_failing",
    "universe_screen_failed": "source_failing",
    "backup_failed": "source_failing",
    "api_key": "source_failing",
    "review_fallback": "model_failing",
    "another_instance": "another_instance",
    "restart_resumed": "restarted_resumed",
    "restart_paused": "restarted_paused",
}


def _detail(details: dict[str, object] | None, *keys: str) -> str:
    """First non-empty detail value among ``keys``, joined for context."""
    parts = [
        str(details[k])
        for k in keys
        if details and details.get(k) not in (None, "")
    ]
    return " — ".join(parts)


def format_recorded(
    event_type: str, details: dict[str, object] | None
) -> tuple[str, str, str | None] | None:
    """Map one recorded event to (notification key, line, link).

    Returns ``None`` for event types that should not notify. The line is
    the "one line with the key number" that follows the event label in
    the subject.
    """
    details = details or {}
    key = EVENT_TYPE_MAP.get(event_type)
    if key is None:
        return None
    symbol = str(details.get("symbol") or "")

    if event_type == "kill_switch":
        if details.get("on") is not True:
            return None  # a manual "off" write; resume has its own type
        return key, str(details.get("reason") or "activated"), "/"
    if event_type == "protective_stop":
        return (
            key,
            f"{symbol}: no stop at Alpaca — protective stop placed",
            "/positions",
        )
    if event_type == "protective_stop_failed":
        extra = _detail(details, "error")
        return (
            key,
            f"{symbol}: still no stop at Alpaca"
            + (f" ({extra})" if extra else ""),
            "/positions",
        )
    if event_type == "reconciliation":
        kind = str(details.get("kind") or "")
        if kind == "adopted_position":
            line = (
                f"{symbol}: position at Alpaca the agent never opened — "
                "adopted with a protective stop"
            )
        elif kind == "qty_mismatch":
            line = (
                f"{symbol}: Alpaca {details.get('broker_qty')} vs "
                f"recorded {details.get('qty')}"
            )
        elif kind == "unknown_order":
            line = f"{symbol or 'broker'}: unknown order at Alpaca"
        else:
            line = kind.replace("_", " ") or "mismatch at Alpaca"
        return key, line, "/positions"
    if event_type == "order_submit_failed":
        extra = _detail(details, "error")
        return (
            key,
            f"{symbol or 'order'}: {extra or 'submit failed'}",
            "/positions",
        )
    if event_type == "order_sanity":
        return key, f"{symbol}: blocked by the order sanity checks", "/positions"
    if event_type == "flatten_sell_rejected":
        extra = _detail(details, "error")
        return (
            key,
            f"{symbol}: flatten sale rejected"
            + (f" ({extra})" if extra else ""),
            "/positions",
        )
    if event_type == "flatten_sell_blocked":
        return (
            key,
            f"{symbol}: flatten sale held — leg cancel not confirmed",
            "/positions",
        )
    if event_type == "leg_cancel_unconfirmed":
        extra = _detail(details, "error")
        return (
            key,
            f"{symbol}: an order cancel was never confirmed"
            + (f" ({extra})" if extra else ""),
            "/positions",
        )
    if event_type == "position_check_failed":
        count = details.get("consecutive") or details.get("count") or "3"
        return key, f"{count} checks failed in a row", "/positions"
    if event_type == "universe_screen_failed":
        extra = _detail(details, "error")
        return key, f"universe screen failed ({extra or 'error'})", "/settings"
    if event_type == "backup_failed":
        extra = _detail(details, "error")
        return (
            key,
            "daily database copy failed"
            + (f" ({extra})" if extra else ""),
            "/settings",
        )
    if event_type == "api_key":
        if details.get("result") != "failed":
            return None  # successful key tests/imports stay quiet
        line = (
            f"{details.get('name')}: {details.get('action', 'check')} "
            f"failed"
        )
        extra = _detail(details, "detail")
        if extra:
            line += f" ({extra})"
        return key, line, "/settings"
    if event_type == "another_instance":
        line = str(
            details.get("message")
            or f"order carries instance id {details.get('foreign_instance')}"
        )
        return key, line, "/settings"
    if event_type == "restart_resumed":
        return key, "resumed after a clean restart", "/"
    if event_type == "restart_paused":
        reasons = details.get("reasons")
        line = "stayed paused"
        if isinstance(reasons, list) and reasons:
            line = "stayed paused: " + "; ".join(str(r) for r in reasons)
        return key, line, "/logs"
    if event_type == "review_fallback":
        line = (
            f"{details.get('kind', 'review')} {details.get('ref', '')}"
            f": {details.get('reason', 'fell back to the template')}"
        ).strip()
        return key, line, "/reviews"
    return key, event_type.replace("_", " "), None
