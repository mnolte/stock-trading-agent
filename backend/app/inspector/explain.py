"""Per-call explanations for the inspector (item 074).

One ``What happened`` panel per reason on the call. The fixed text comes
from ``app.llm.reasons`` (the spec table, word for word); ``detail`` is
the call's own specifics, built here from the stored rows only — the
stored snapshot/facts, the linked decision's gates and validation
result, the stored (already scrubbed) error fields. Nothing is fetched
or recomputed.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from app.llm.reasons import (
    LINK_DECISION,
    LINK_INSIGHTS,
    LINK_KEYS,
    LINK_LOGS,
    LINK_POSITIONS,
    LINK_REVIEWS,
    LINK_SETTINGS,
    LINK_STRATEGY,
    REASONS,
    api_error_class,
    change_reason_key,
    change_reason_title,
)
from app.market.calendar import WeekdayCalendarProvider

if TYPE_CHECKING:
    from app.db.models import LlmCall

_LINK_KINDS = {
    LINK_DECISION,
    LINK_STRATEGY,
    LINK_SETTINGS,
    LINK_KEYS,
    LINK_LOGS,
    LINK_INSIGHTS,
    LINK_POSITIONS,
    LINK_REVIEWS,
}


def _walk_unavailable(value: Any, path: str, out: list[str]) -> None:
    if value == "unavailable":
        out.append(path or "input")
    elif isinstance(value, dict):
        for key, child in value.items():
            _walk_unavailable(child, f"{path}.{key}" if path else str(key), out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_unavailable(child, f"{path}[{index}]", out)


def _headline_status(payload: dict[str, Any]) -> str | None:
    status = payload.get("news_status")
    if status in ("truncated", "failed"):
        return str(status)
    # Older snapshots carry the status on the news block itself.
    news = payload.get("news")
    if isinstance(news, dict):
        status = news.get("status")
        if status in ("truncated", "failed"):
            return str(status)
    return None


def _option_touches_missing(facts: Any) -> list[str]:
    """Verdict fact option ids whose touches were unavailable."""
    missing: list[str] = []
    options = facts.get("options") if isinstance(facts, dict) else None
    if isinstance(options, list):
        for opt in options:
            if isinstance(opt, dict) and opt.get("touch") == "unavailable":
                missing.append(str(opt.get("id") or opt.get("option") or "?"))
    return missing


def _give_up_date(facts: dict[str, Any], sessions: int) -> str | None:
    """The date unavailable option touches settle by: the 5-session
    price date plus ``outcomes.touches_give_up_sessions`` trading days."""
    raw = facts.get("price_5sessions_date")
    if not raw:
        return None
    try:
        day = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None
    cal = WeekdayCalendarProvider()
    for _ in range(max(0, sessions)):
        nxt = cal.next_trading_day(day)
        if nxt is None:
            return None
        day = nxt.date
    return day.isoformat()


def detail_for(
    code: str,
    *,
    call: LlmCall,
    stored: dict[str, Any] | None,
    change_reason: str | None,
    change_detail: str | None,
    touches_give_up_sessions: int,
    slow_call_seconds: float,
) -> str | None:
    """The call-specific line under one explanation — stored rows only."""
    if code == "changed_by_code":
        title = change_reason_title(change_reason)
        parts = [title]
        if change_detail:
            parts.append(str(change_detail))
        elif change_reason and change_reason_key(change_reason) is None:
            parts.append(str(change_reason))
        return " · ".join(parts) or None
    if code == "input_incomplete":
        parts: list[str] = []
        if isinstance(stored, dict):
            facts = stored.get("facts")
            payload = facts if isinstance(facts, dict) else stored
            missing: list[str] = []
            _walk_unavailable(payload, "", missing)
            # Collapse leaf noise to readable field names.
            names = sorted({m.split(".")[0].split("[")[0] for m in missing})
            if names:
                parts.append("unavailable: " + ", ".join(names[:6]))
            status = _headline_status(payload)
            if status:
                parts.append(f"headlines {status}")
            touches = _option_touches_missing(facts)
            if touches:
                parts.append("option touches unavailable (" + ", ".join(touches[:4]) + ")")
                give_up = (
                    _give_up_date(facts, touches_give_up_sessions)
                    if isinstance(facts, dict)
                    else None
                )
                if give_up:
                    parts.append(f"marked unavailable by {give_up}")
        return "; ".join(parts) if parts else None
    if code in ("timeout", "api_error"):
        parts = []
        if call.error_type:
            parts.append(str(call.error_type))
        if call.http_status is not None:
            parts.append(f"HTTP {call.http_status}")
        if call.error_message:
            parts.append(str(call.error_message))
        if call.latency_ms is not None:
            parts.append(f"{call.latency_ms / 1000:.1f} s")
        return " · ".join(parts) if parts else None
    if code == "slow":
        if call.latency_ms is None:
            return None
        return (
            f"{call.latency_ms / 1000:.1f} s, over "
            f"{slow_call_seconds:g} s"
        )
    return None


def explanation_items(
    call: LlmCall,
    *,
    stored: dict[str, Any] | None,
    change_reason: str | None,
    change_detail: str | None,
    touches_give_up_sessions: int,
    slow_call_seconds: float,
    resolve_link: Any,
) -> list[dict[str, Any]]:
    """One panel per reason, in the call's stored reason order.

    ``resolve_link(kind)`` maps a link kind to the call's href (or None
    to drop the button). ``api_error`` picks its status-class text;
    ``changed_by_code`` keeps the parent text and names the check in
    ``detail``.
    """
    items: list[dict[str, Any]] = []
    for code in call.reasons or []:
        base = REASONS.get(code)
        if base is None:
            continue
        title, cause, fix = base.title, base.cause, base.fix
        if code == "api_error":
            cls = api_error_class(call.http_status)
            title = base.title + cls.title_suffix
            cause = cls.cause
            fix = cls.fix
        detail = detail_for(
            code,
            call=call,
            stored=stored,
            change_reason=change_reason,
            change_detail=change_detail,
            touches_give_up_sessions=touches_give_up_sessions,
            slow_call_seconds=slow_call_seconds,
        )
        links = []
        for label, kind in base.links:
            href = resolve_link(kind)
            if href:
                links.append({"label": label, "href": href})
        items.append(
            {
                "code": code,
                "title": title,
                "means": base.means,
                "cause": cause,
                "fix": fix,
                "detail": detail,
                "links": links,
            }
        )
    return items


def change_reason_and_detail(
    gates: Any, validation: Any
) -> tuple[str | None, str | None]:
    """The stored change reason and its numbers, from the linked row's
    gates_result / validation_result (both plain dicts here)."""
    reason: str | None = None
    detail: str | None = None
    if isinstance(gates, dict):
        raw = gates.get("blocked_by")
        reason = str(raw) if raw else None
        for check in gates.get("checks") or []:
            if isinstance(check, dict) and check.get("passed") is False:
                detail = check.get("detail")
                if detail:
                    break
    if reason is None and isinstance(validation, dict):
        raw = validation.get("reason")
        reason = str(raw) if raw else None
        for check in validation.get("checks") or []:
            if isinstance(check, dict) and check.get("passed") is False:
                detail = check.get("detail")
                if detail:
                    break
    return reason, str(detail) if detail else None
