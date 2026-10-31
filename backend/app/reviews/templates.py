"""Plain review templates built from the computed facts (docs/SPEC.md, Reviews).

Used when the model text is unavailable or fails the number check —
labelled in the GUI as "Plain review: <reason>". Every number below comes
straight from the facts dict, so a template can never invent a figure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _day_words(iso: object) -> str:
    """"2026-10-13" → "Tue 13 Oct" (design wording); raw value otherwise."""
    try:
        day = datetime.fromisoformat(str(iso)).date()
    except ValueError:
        return str(iso)
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def _fmt_pct(value: object, digits: int = 2) -> str:
    return f"{float(value):+.{digits}f}%" if isinstance(value, int | float) else "—"


def _fmt_usd(value: object) -> str:
    return f"${float(value):,.2f}" if isinstance(value, int | float) else "—"


def weekly_plain(facts: dict[str, Any]) -> dict[str, Any]:
    """{summary, observations, suggestion} for the weekly template."""
    period = facts.get("period") or {}
    parts: list[str] = []
    ret = facts.get("return_pct")
    if ret is None:
        parts.append(
            f"Week {period.get('week')} ({period.get('label', '')}): "
            "no equity snapshots, so no return figure."
        )
    else:
        spy = facts.get("spy_return_pct")
        vs = facts.get("vs_spy_pts")
        spy_bit = (
            f", SPY {_fmt_pct(spy)} ({_fmt_signed(vs)} pts)"
            if spy is not None and vs is not None
            else ""
        )
        parts.append(
            f"Week {period.get('week')} ({period.get('label', '')}): "
            f"return {_fmt_pct(ret)}{spy_bit}."
        )
    closed = facts.get("trades_closed") or 0
    if closed:
        parts.append(
            f"{closed} trade{'s' if closed != 1 else ''} closed: "
            f"{facts.get('won', 0)} won, {facts.get('lost', 0)} lost; "
            f"average {_fmt_signed(facts.get('avg_r'))}R per trade."
        )
    else:
        parts.append("No trades closed.")
    dd = facts.get("max_drawdown_pct")
    if dd is not None:
        parts.append(f"Max drawdown {_fmt_pct(dd)}.")
    cost = facts.get("model_cost_usd")
    if cost is not None:
        parts.append(
            f"Model cost {_fmt_usd(cost)} over "
            f"{facts.get('model_calls', 0)} calls."
        )

    observations: list[str] = []
    best = facts.get("best_trade")
    worst = facts.get("worst_trade")
    if best is not None and worst is not None:
        observations.append(
            f"Best trade {best['symbol']} {_fmt_signed(best.get('r'))}R "
            f"({best.get('exit')}); worst {worst['symbol']} "
            f"{_fmt_signed(worst.get('r'))}R ({worst.get('exit')})."
        )
    skips = facts.get("skips") or {}
    if skips.get("total"):
        n = (facts.get("limits") or {}).get("verdict_after_sessions", 5)
        line = (
            f"{skips.get('with_outcome', 0)} of {skips['total']} model "
            f"skips have a {n}-session result: {skips.get('up', 0)} up, "
            f"{skips.get('flat', 0)} flat, {skips.get('down', 0)} down."
        )
        miss = skips.get("biggest_miss")
        if miss is not None:
            line += (
                f" Biggest miss: {miss['symbol']} skipped "
                f"{_day_words(miss.get('date'))}, then rose "
                f"{miss.get('pct')}%."
            )
        observations.append(line)
    verdicts = facts.get("verdicts") or {}
    if verdicts.get("total"):
        observations.append(
            f"{verdicts['total']} confirmed verdict"
            f"{'s' if verdicts['total'] != 1 else ''} this week: "
            f"{verdicts.get('good_call', 0)} good, "
            f"{verdicts.get('bad_call', 0)} bad, "
            f"{verdicts.get('unclear', 0)} unclear."
        )
    return {
        "summary": " ".join(parts),
        "observations": observations[:3],
        "suggestion": None,
    }


def _fmt_signed(value: object, digits: int = 2) -> str:
    return f"{float(value):+.{digits}f}" if isinstance(value, int | float) else "—"


def briefing_status(facts: dict[str, Any]) -> str:
    def names(key: str, field: str) -> str:
        rows = facts.get(key)
        if not isinstance(rows, list):
            return "unavailable"
        return ", ".join(str(row.get(field, "")) for row in rows) or "none"

    return (
        f"Positions: {names('positions', 'symbol')}. "
        f"Earnings today: {names('earnings_today', 'symbol')}; "
        f"tomorrow: {names('earnings_tomorrow', 'symbol')}. "
        f"Banners: {names('banners', 'kind')}."
    )


def briefing_plain(facts: dict[str, Any]) -> dict[str, Any]:
    """{text} for the daily briefing template."""
    pos = facts.get("positions") or []
    pos_bits = []
    for p in pos:
        distance = p.get("stop_distance_pct")
        pos_bits.append(
            f"{p['symbol']} {p['qty']} sh, stop "
            f"{float(distance):.1f}% below price"
            if distance is not None
            else f"{p['symbol']} {p['qty']} sh, no fresh price"
        )
    positions = "; ".join(pos_bits) or "none"
    today = ", ".join(e["symbol"] for e in facts.get("earnings_today") or [])
    tomorrow = ", ".join(
        e["symbol"] for e in facts.get("earnings_tomorrow") or []
    )
    yesterday = facts.get("yesterday") or {}
    y_pct = yesterday.get("return_pct")
    y_txt = (
        f"{_fmt_pct(y_pct)} ({_fmt_usd_signed(yesterday.get('return_usd'))})"
        if y_pct is not None
        else "unavailable"
    )
    banners = ", ".join(
        str(b.get("kind", "")).replace("_", " ")
        for b in facts.get("banners") or []
    )
    text = (
        f"Positions: {positions}. "
        f"Earnings today: {today or 'none'}; tomorrow: {tomorrow or 'none'}. "
        f"Regime: {facts.get('regime') or 'unavailable'}. "
        f"Yesterday: {y_txt}. Banners: {banners or 'none'}."
    )
    return {"text": text}


def _fmt_usd_signed(value: object) -> str:
    return (
        f"{'+' if float(value) >= 0 else '-'}${abs(float(value)):,.2f}"
        if isinstance(value, int | float)
        else "—"
    )


def briefing_display(review: Any) -> tuple[str | None, str]:
    """(status_line, text) for the GUI and email (docs/SPEC.md, Reviews —
    Briefing text). New rows store the code-written status line in
    ``content.status_line`` and only the model's sentences in ``text``;
    rows written before that had the line prefixed into the text — the
    leading copy is removed here, never rewritten in storage.
    """
    content = review.content if isinstance(review.content, dict) else {}
    facts = review.facts if isinstance(review.facts, dict) else {}
    status = content.get("status_line")
    if not isinstance(status, str) or not status.strip():
        status = briefing_status(facts) if facts else None
    text = review.text if isinstance(review.text, str) else ""
    if status and text.startswith(status):
        text = text[len(status) :].lstrip()
    return status, text


def _code_bit(facts: dict[str, Any]) -> str:
    """"; code blocked it (<reason>)" for the verdict reason line."""
    if not facts.get("changed_by_code"):
        return ""
    reason = facts.get("change_text") or facts.get("change_reason")
    verb = (
        "blocked it"
        if facts.get("final_action") in ("skip", "blocked")
        else f"changed it to {facts.get('final_action')}"
    )
    return f"; code {verb}" + (f" ({reason})" if reason else "")


def _option_touch_text(option_id: object, touch: object, n: int) -> str:
    """The "what happened" part for an enter's chosen option."""
    label = f"option {option_id}" if option_id is not None else "the entry"
    if touch == "target":
        return f"{label} reached its target first"
    if touch == "stop":
        return f"{label} reached its stop first"
    if touch == "not_filled":
        return "the order never filled"
    if touch == "neither":
        return f"neither stop nor target was reached in {n} sessions"
    return "the option outcome is unknown"


def verdict_plain(facts: dict[str, Any]) -> dict[str, Any]:
    """{verdict, reason} for a draft verdict without a model.

    The draft-label table (docs/SPEC.md, Reviews — Verdicts): the verdict
    judges the model's call. Enters are labelled by the chosen option's
    (or the real trade's) first touch; model skips with offered options
    by what those options did afterwards, and v1 skips by the 5-session
    price move against the flat band.
    """
    n = facts.get("sessions", 5)
    pct = facts.get("pct_5sessions")
    trade = facts.get("trade")
    action = facts.get("model_action") or facts.get("final_action")
    code_bit = _code_bit(facts)
    option_id = facts.get("option_id")
    options = [o for o in facts.get("options") or [] if isinstance(o, dict)]

    if action == "enter":
        call = (
            f"Model entered on {option_id}"
            if option_id is not None
            else "Model entered"
        )
        chosen = next(
            (
                o
                for o in options
                if option_id is not None
                and str(o.get("option_id")) == str(option_id)
            ),
            None,
        )
        if chosen is not None:
            touch = chosen.get("touch")
            # The traded option's null touch falls back to the real
            # trade's stored first touch (SPEC: "or the real trade").
            if touch is None and trade is not None:
                touch = trade.get("first_touch")
            happened = _option_touch_text(option_id, touch, n)
        elif trade is not None:
            touch = trade.get("first_touch")
            if touch in ("target", "stop"):
                happened = f"the trade reached its {touch} first"
            else:
                happened = f"neither stop nor target was reached in {n} sessions"
        else:
            touch = None
            happened = f"no {n}-session outcome recorded"
        verdict = (
            "good_call"
            if touch == "target"
            else "bad_call"
            if touch == "stop"
            else "unclear"
        )
        return {
            "verdict": verdict,
            "reason": f"{call}{code_bit}; {happened}.",
        }

    if action == "skip" and options:
        touches = [o.get("touch") for o in options]
        total = len(touches)
        targets = sum(1 for t in touches if t == "target")
        if targets:
            verdict = "bad_call"
            happened = (
                f"{targets} of {total} option{'s' if total != 1 else ''} "
                "reached its target first"
            )
        elif all(t in ("stop", "not_filled") for t in touches):
            verdict = "good_call"
            happened = "every option hit its stop first or never filled"
        elif all(t == "neither" for t in touches):
            verdict = "unclear"
            ids = ", ".join(str(o.get("option_id")) for o in options)
            happened = f"{ids}: none reached stop or target in {n} sessions"
        else:
            verdict = "unclear"
            happened = "the option outcomes were mixed or unknown"
        return {
            "verdict": verdict,
            "reason": f"Model skipped{code_bit}; {happened}.",
        }

    if action == "skip":
        flat = facts.get("skip_flat_pct", 2)
        if pct is None:
            verdict, reason = "unclear", f"No {n}-session price recorded."
        elif float(pct) > flat:
            verdict = "bad_call"
            reason = f"Rose {float(pct):.1f}% in the {n} sessions after the skip."
        elif float(pct) < -flat:
            verdict = "good_call"
            reason = (
                f"Fell {abs(float(pct)):.1f}% in the {n} sessions after "
                "the skip."
            )
        else:
            verdict = "unclear"
            reason = (
                f"Stayed within ±{flat:g}% in the {n} sessions after "
                "the skip."
            )
        return {"verdict": verdict, "reason": f"Model skipped{code_bit}; {reason}"}

    # Anything else (hold, tighten, exit or no model action): no label
    # table row — the decision's outcome decides nothing by itself.
    if trade is not None:
        r = trade.get("r")
        exit_label = trade.get("exit")
        if exit_label is not None and r is not None:
            verdict = (
                "good_call"
                if float(r) > 0
                else "bad_call"
                if float(r) < 0
                else "unclear"
            )
            return {
                "verdict": verdict,
                "reason": f"Closed at the {exit_label}, {_fmt_signed(r)}R.",
            }
    return {"verdict": "unclear", "reason": "No model call to judge."}


def plain_content(kind: str, facts: dict[str, Any]) -> dict[str, Any]:
    """The fallback content block for one review kind."""
    if kind == "briefing":
        return briefing_plain(facts)
    if kind == "verdict":
        return verdict_plain(facts)
    return weekly_plain(facts)
