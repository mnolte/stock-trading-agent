"""What moves this forward — item 100 (docs/SPEC.md → Readiness).

A pure function of the latest stored snapshot: it never measures a
criterion again. Every act row comes from one table below, in the
order of the kickoff's sentence table; a clause whose value the
snapshot doesn't hold is dropped, never guessed.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.market.calendar import CalendarProvider

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# Statuses as stored on the snapshot rows.
_NOT_YET = "not_yet"
_WAITING = "waiting"

# Counts that grow by themselves → "building up", not actionable.
_BUILDING_UP = ("closed_trades", "sessions")

# Act order = the kickoff table's order, not the criteria table's.
_ACT_ORDER = (
    "log_errors",
    "reconciliation",
    "unresolved_errors",
    "log_critical",
    "stops_missing",
    "kill_switch",
    "proposals",
    "calibration",
    "cycles",
    "price_blocked",
    "return_vs_spy",
    "max_drawdown",
    "avg_r",
    "profit_factor",
    "beats_rules_only",
)


def _day(iso: str) -> str:
    """"2026-10-14" → "14 Oct"."""
    d = date.fromisoformat(iso)
    return f"{d.day} {_MONTHS[d.month - 1]}"


def _target_short(target: str | None) -> str:
    """"≥ 75%" → "75%", "under 6%" → "under 6%"."""
    if not target:
        return "—"
    for op in ("≥ ", "≤ ", "> ", "< "):
        if target.startswith(op):
            return target[len(op):]
    return target


def _value_text(criterion: dict[str, Any], extra: str = "") -> str:
    value = str(criterion.get("value") or "—")
    if extra:
        value = f"{value} {extra}"
    return f"{value} · target {_target_short(criterion.get('target'))}"


def _date_after_sessions(
    calendar: CalendarProvider, after: date, sessions: int
) -> date | None:
    from app.readiness.service import (  # noqa: PLC0415 - lazy, avoids a cycle
        _date_after_sessions as _impl,
    )

    return _impl(calendar, after, sessions)


def _sentence(key: str, c: dict[str, Any], calendar: CalendarProvider) -> str:
    """The one sentence per act row (kickoff table). A clause whose
    number the snapshot doesn't hold is dropped."""
    d = c.get("detail") or {}
    n = c.get("sample")
    if key == "log_errors":
        names = d.get("names") or []
        listed = f" ({', '.join(names[:2])})" if names else ""
        return (
            f"Review the {n} error problems on Logs{listed}. "
            "Marked expected or fixed, this passes at the next check."
        )
    if key == "reconciliation":
        plural = "mismatches" if n != 1 else "mismatch"
        return (
            f"{n} {plural} with Alpaca "
            f"{'are' if n != 1 else 'is'} not acknowledged. Check the "
            "position at Alpaca, then acknowledge."
        )
    if key == "unresolved_errors":
        return (
            f"{n} errors that touched a decision, order or fill are "
            "open. Each ends when its state clears, or dismiss it with "
            "a note."
        )
    if key == "log_critical":
        window = d.get("window_sessions") or 20
        last = f", the last on {_day(d['last_date'])}" if d.get("last_date") else ""
        return (
            f"{d.get('count', n)} critical lines{last}. They can't be "
            f"reviewed away: this passes once {window} sessions pass "
            "without one."
        )
    if key == "stops_missing":
        window = d.get("window_sessions") or 30
        count = d.get("count", n)
        last = f", the last on {_day(d['last_date'])}" if d.get("last_date") else ""
        after = ""
        if d.get("last_date"):
            date2 = _date_after_sessions(
                calendar, date.fromisoformat(d["last_date"]), window
            )
            if date2:
                after = f", after {_day(date2.isoformat())}"
        return (
            f"A position had no stop at Alpaca {count} "
            f"time{'s' if count != 1 else ''}{last}. This passes when "
            f"that leaves the {window}-session window{after}."
        )
    if key == "kill_switch":
        window = d.get("window_sessions") or 30
        count = d.get("count", n)
        last = f", the last on {_day(d['last_date'])}" if d.get("last_date") else ""
        after = ""
        if d.get("last_date"):
            date2 = _date_after_sessions(
                calendar, date.fromisoformat(d["last_date"]), window
            )
            if date2:
                after = f" after {_day(date2.isoformat())}"
        return (
            f"The kill switch turned on {count} "
            f"time{'s' if count != 1 else ''}{last}. This passes{after}."
        )
    if key == "proposals":
        failed = d.get("failed")
        total = d.get("total")
        if failed is None or total is None:
            return (
                "Proposals were turned down by a code check. Insights "
                "shows which check turns them down."
            )
        return (
            f"{failed} of {total} proposals were turned down by a code "
            "check. Insights shows which check turns them down."
        )
    if key == "calibration":
        gap = d.get("gap_pts")
        g = f"{gap:g}" if gap is not None else "—"
        return (
            f"Stated confidence is {g} points from the actual win "
            "rate. Insights → Calibration shows in which group."
        )
    if key == "cycles":
        window = d.get("window_sessions") or 20
        count = d.get("count", n)
        dates = d.get("dates") or []
        on = f" on {', '.join(_day(x) for x in dates)}" if dates else ""
        reason = f" ({d['reason']})" if d.get("reason") else ""
        after = ""
        if dates:
            date2 = _date_after_sessions(
                calendar, date.fromisoformat(dates[-1]), window
            )
            if date2:
                after = f" after {_day(date2.isoformat())}"
        return (
            f"{count} cycles were missed{on}{reason}. They leave the "
            f"{window}-session window{after}; until then keep the app "
            "running through every session."
        )
    if key == "price_blocked":
        pct = d.get("pct")
        p = f"{pct:g}" if pct is not None else "—"
        return (
            f"{p}% of candidate checks were blocked by a stale or "
            "suspect price. Logs shows which symbols."
        )
    if key == "return_vs_spy":
        gap = d.get("gap_pts")
        x = f"{abs(gap):g}" if gap is not None else "—"
        return (
            f"The run is {x} points behind SPY buy and hold. "
            "Performance shows where."
        )
    if key == "max_drawdown":
        dd = d.get("dd_pct")
        mx = d.get("max_pct")
        x = f"{dd:g}" if dd is not None else "—"
        y = f"{mx:g}" if mx is not None else "—"
        return (
            f"The deepest fall was {x}%, over the {y}% target. It "
            "stays in this run's record; only a new run resets it."
        )
    if key == "avg_r":
        avg = d.get("avg_r")
        mn = d.get("min_r")
        x = f"{avg:g}" if avg is not None else "—"
        y = f"{mn:g}" if mn is not None else "—"
        return (
            f"Trades earn {x} R on average, under {y} R. Insights "
            "shows which conditions lose."
        )
    if key == "profit_factor":
        pf = d.get("pf")
        mn = d.get("min_pf")
        x = f"{pf:g}" if pf is not None else "—"
        y = f"{mn:g}" if mn is not None else "—"
        return (
            f"Winners earned {x} times what losers lost, under {y}. "
            "Insights shows which conditions lose."
        )
    if key == "beats_rules_only":
        live = d.get("live_r")
        shadow = d.get("shadow_r")
        a = f"{live:g}" if live is not None else "—"
        b = f"{shadow:g}" if shadow is not None else "—"
        return (
            f"Live's average R ({a}) is not above the rules-only "
            f"shadow's ({b})."
        )
    return ""


_BUTTONS: dict[str, tuple[str, str]] = {
    "log_errors": ("Review on Logs", "/logs"),
    "reconciliation": ("Open Positions", "/positions"),
    "unresolved_errors": ("Open Needs you", "/#needs"),
    "log_critical": ("Open Logs", "/logs"),
    "proposals": ("Open Insights", "/insights"),
    "calibration": ("Open Insights", "/insights"),
    "price_blocked": ("Open Logs", "/logs"),
    "return_vs_spy": ("Open Performance", "/performance"),
    "max_drawdown": ("Open Performance", "/performance"),
    "avg_r": ("Open Insights", "/insights"),
    "profit_factor": ("Open Insights", "/insights"),
    "beats_rules_only": ("Open Strategies", "/strategies"),
}


def _act_row(
    key: str, c: dict[str, Any], calendar: CalendarProvider
) -> dict[str, Any]:
    button, href = _BUTTONS.get(key, ("Open", "/"))
    if key in ("stops_missing", "kill_switch") and (c.get("detail") or {}).get(
        "last_date"
    ):
        day = c["detail"]["last_date"]
        button = f"Open Logs for {_day(day)}"
        href = f"/logs?from={day}&to={day}"
    elif key == "cycles" and (c.get("detail") or {}).get("dates"):
        day = c["detail"]["dates"][-1]
        button = f"Open Logs for {_day(day)}"
        href = f"/logs?from={day}&to={day}"
    extra = ""
    if key == "log_errors":
        extra = "unreviewed"
    elif key == "unresolved_errors":
        extra = "open"
    return {
        "key": key,
        "name": str(c.get("label") or key),
        "value_text": _value_text(c, extra),
        "sentence": _sentence(key, c, calendar),
        "button": button,
        "href": href,
    }


def _needs_sentence(group: list[dict[str, Any]]) -> str:
    """"Average R, Profit factor and Beats rules-only need 30 closed
    trades (3 so far)"."""
    names = [
        str(c.get("label") or c.get("key") or "").replace(" (adjusted)", "")
        for c in group
    ]
    joined = (
        ", ".join(names[:-1]) + " and " + names[-1]
        if len(names) > 1
        else names[0]
    )
    needs = str(group[0].get("needs") or "")
    count = max(
        (c.get("sample") or 0 for c in group), default=0
    )
    verb = "needs" if len(group) == 1 else "need"
    return f"{joined} {verb} {needs} ({count} so far)"


def forward(
    criteria: list[dict[str, Any]],
    *,
    calendar: CalendarProvider,
    manual_open: int,
) -> dict[str, Any]:
    """Build the "What moves this forward" payload from stored
    criterion rows (docs/SPEC.md → Readiness, item 100)."""
    by_key = {str(c.get("key")): c for c in criteria}

    act = [
        _act_row(key, by_key[key], calendar)
        for key in _ACT_ORDER
        if key in by_key
        and by_key[key].get("status") == _NOT_YET
        and key not in _BUILDING_UP
    ]

    time_items: list[dict[str, Any]] = []
    for key in _BUILDING_UP:
        c = by_key.get(key)
        if not c or c.get("status") != _NOT_YET:
            continue
        minimum = _target_short(c.get("target"))
        estimate = str(c.get("sub") or c.get("estimate") or "")
        if estimate.startswith("≈ "):
            estimate = "about " + estimate[2:]
        text = f"{c.get('value', '—')} of {minimum}"
        if estimate:
            text += f" · {estimate}"
        time_items.append(
            {"kind": "count", "key": key, "name": c.get("label"), "text": text}
        )

    # Needs-data criteria grouped by what they need (table order).
    order = [c for c in criteria if c.get("status") == _WAITING and c.get("needs")]
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in order:
        groups.setdefault(str(c["needs"]), []).append(c)
    needs_items = [
        {"kind": "needs", "text": _needs_sentence(g)}
        for g in groups.values()
    ]
    time_items.extend(needs_items)

    time_count = len(order) + sum(
        1 for i in time_items if i["kind"] == "count"
    )

    return {
        "act": act,
        "time": time_items,
        "time_count": time_count,
        "manual_open": manual_open,
    }
