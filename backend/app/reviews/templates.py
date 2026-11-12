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
    """One code-written status line for stored and fallback briefings."""
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
    positions = "; ".join(pos_bits) or (
        "none" if isinstance(facts.get("positions"), list) else "unavailable"
    )
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
    today = today or (
        "none" if isinstance(facts.get("earnings_today"), list) else "unavailable"
    )
    tomorrow = tomorrow or (
        "none" if isinstance(facts.get("earnings_tomorrow"), list) else "unavailable"
    )
    banners = banners or (
        "none" if isinstance(facts.get("banners"), list) else "unavailable"
    )
    return (
        f"Positions: {positions}. "
        f"Earnings today: {today}; tomorrow: {tomorrow}. "
        f"Regime: {facts.get('regime') or 'unavailable'}. "
        f"Yesterday: {y_txt}. Banners: {banners or 'none'}."
    )


def _version_number(version: str) -> int:
    """"weekly.v3" → 3; unknown shapes → 0."""
    try:
        return int(str(version).rsplit(".", 1)[-1].lstrip("v"))
    except ValueError:
        return 0


def _story_version(kind: str, version: str | None) -> bool:
    """v3+ prompts get the one-to-three-paragraph story (item 079)."""
    return version is not None and _version_number(version) >= 3


def weekly_story_plain(facts: dict[str, Any]) -> dict[str, Any]:
    """{story, suggestion} for the weekly.v3 template (item 079): one
    fixed sentence per fact group, in the prompt's paragraph order —
    the week, what drove it, then the sample so far."""
    period = facts.get("period") or {}
    week = (
        f"Week {period.get('week')} ({period.get('label', '')})"
        if period.get("week") is not None
        else "This week"
    )

    # ---- paragraph 1: the week -------------------------------------------------
    para1: list[str] = []
    ret = facts.get("return_pct")
    if ret is None:
        para1.append(
            f"{week}: no equity snapshots, so there is no return figure."
        )
    else:
        spy = facts.get("spy_return_pct")
        vs = facts.get("vs_spy_pts")
        spy_bit = (
            f", against SPY {_fmt_pct(spy)}"
            + (f" ({_fmt_signed(vs)} pts vs SPY)" if vs is not None else "")
            if spy is not None
            else ""
        )
        dd = facts.get("max_drawdown_pct")
        dd_bit = f" The deepest dip was {_fmt_pct(dd)}." if dd is not None else ""
        para1.append(
            f"{week} closed with the account {_fmt_pct(ret)}"
            f"{_fmt_usd_signed(facts.get('return_usd'))}{spy_bit}.{dd_bit}"
        )

    # ---- paragraph 2: what drove it --------------------------------------------
    para2: list[str] = []
    closed = facts.get("trades_closed") or 0
    if closed:
        para2.append(
            f"{closed} trade{'s' if closed != 1 else ''} closed: "
            f"{facts.get('won', 0)} won, {facts.get('lost', 0)} lost, "
            f"averaging {_fmt_signed(facts.get('avg_r'))}R per trade."
        )
        best = facts.get("best_trade")
        worst = facts.get("worst_trade")
        if best is not None and worst is not None:
            para2.append(
                f"The best was {best['symbol']} "
                f"{_fmt_signed(best.get('r'))}R ({best.get('exit')}); "
                f"the worst {worst['symbol']} "
                f"{_fmt_signed(worst.get('r'))}R ({worst.get('exit')})."
            )
    else:
        para2.append("No trades closed this week.")
    skips = facts.get("skips") or {}
    if skips.get("total"):
        n = (facts.get("limits") or {}).get("verdict_after_sessions", 5)
        line = (
            f"Of {skips['total']} model skips, "
            f"{skips.get('with_outcome', 0)} have a {n}-session result: "
            f"{skips.get('up', 0)} up, {skips.get('flat', 0)} flat, "
            f"{skips.get('down', 0)} down."
        )
        miss = skips.get("biggest_miss")
        if miss is not None:
            line += (
                f" The biggest miss was {miss['symbol']}, skipped "
                f"{_day_words(miss.get('date'))}, then up "
                f"{miss.get('pct')}%."
            )
        para2.append(line)

    # ---- paragraph 3: the sample so far -----------------------------------------
    sample = facts.get("sample") or {}
    closed_run = (sample.get("closed_trades") or {}).get("run")
    skips_run = (sample.get("model_skips_with_outcome") or {}).get("run")
    verdicts = facts.get("verdicts") or {}
    para3_bits: list[str] = []
    if closed_run is not None:
        para3_bits.append(
            f"{closed_run} closed trade{'s' if closed_run != 1 else ''} "
            "in the run so far"
        )
    if skips_run:
        para3_bits.append(f"{skips_run} scored model skips")
    if verdicts.get("total"):
        para3_bits.append(
            f"{verdicts['total']} confirmed verdict"
            f"{'s' if verdicts['total'] != 1 else ''} "
            f"({verdicts.get('good_call', 0)} good, "
            f"{verdicts.get('bad_call', 0)} bad, "
            f"{verdicts.get('unclear', 0)} unclear)"
        )
    para3 = (
        "So far: " + ", ".join(para3_bits) + ". That is still a small "
        "sample — one trade can swing it either way."
        if para3_bits
        else "No closed trades or confirmed verdicts yet — the sample is "
        "still empty, so nothing here is proof either way."
    )

    return {
        "story": "\n\n".join([" ".join(para1), " ".join(para2), para3]),
        "suggestion": None,
    }


def briefing_story_plain(facts: dict[str, Any]) -> dict[str, Any]:
    """{text} for the briefing.v3 template (item 079): account, market,
    what to watch — the prompt's paragraph order, one sentence per fact
    group, code-written like the v1/v2 status line but as prose."""
    # ---- paragraph 1: the account ----------------------------------------------
    pos = facts.get("positions")
    para1: list[str] = []
    if isinstance(pos, list):
        if not pos:
            para1.append("The account is flat today — no positions are open.")
        else:
            for p in pos:
                dist = p.get("stop_distance_pct")
                price = p.get("price")
                if price is None:
                    para1.append(
                        f"{p.get('symbol')} has {p.get('qty')} shares open; "
                        "no fresh price for the stop distance."
                    )
                    continue
                stop_bit = (
                    f"its stop sits {float(dist):.1f}% below the last price"
                    if dist is not None
                    else "the stop distance is unavailable"
                )
                para1.append(
                    f"{p.get('symbol')} has {p.get('qty')} shares open at "
                    f"${float(price):,.2f}, and {stop_bit}."
                )
    yesterday = facts.get("yesterday") or {}
    y_pct = yesterday.get("return_pct")
    if y_pct is not None:
        spy = yesterday.get("spy_return_pct")
        spy_bit = (
            f" SPY moved {_fmt_pct(spy)} over the same day."
            if spy is not None
            else ""
        )
        para1.append(
            f"Yesterday the account {_fmt_pct(y_pct)} "
            f"({_fmt_usd_signed(yesterday.get('return_usd'))}).{spy_bit}"
        )

    # ---- paragraph 2: the market ------------------------------------------------
    para2: list[str] = []
    regime = facts.get("regime")
    if regime and regime != "unavailable":
        para2.append(f"The market regime reads {regime}.")
    vix = facts.get("vix")
    if isinstance(vix, dict) and vix.get("close") is not None:
        pct = vix.get("percentile_1y")
        stale = " and the reading is stale" if vix.get("stale") else ""
        pct_bit = (
            f", its {_ordinal(float(pct))} percentile of the past year,"
            if pct is not None
            else ","
        )
        para2.append(
            f"VIX closed at {float(vix['close']):g}{pct_bit}{stale}."
        )
    release = facts.get("next_release")
    if isinstance(release, dict) and release.get("label"):
        when = _day_words(release.get("date"))
        para2.append(
            f"The next scheduled release is {release['label']} on "
            f"{when} at {release.get('time', '')} New York time."
        )

    # ---- paragraph 3: what to watch ------------------------------------------------
    para3: list[str] = []
    today = [e["symbol"] for e in facts.get("earnings_today") or [] if e.get("symbol")]
    tomorrow = [
        e["symbol"] for e in facts.get("earnings_tomorrow") or [] if e.get("symbol")
    ]
    if today or tomorrow:
        bits = []
        if today:
            bits.append(f"{', '.join(today)} reports today")
        if tomorrow:
            bits.append(f"{', '.join(tomorrow)} reports tomorrow")
        para3.append("Earnings to watch: " + "; ".join(bits) + ".")
    banners = facts.get("banners") or []
    if banners:
        para3.append(
            "Banners up: "
            + ", ".join(str(b.get("kind", "")).replace("_", " ") for b in banners)
            + "."
        )
    if not para3:
        para3.append("Nothing on the earnings calendar and no banners today.")

    return {"text": "\n\n".join([" ".join(p) for p in (para1, para2, para3) if p])}


def briefing_plain(facts: dict[str, Any]) -> dict[str, Any]:
    """{text} for the daily briefing template."""
    return {"text": briefing_status(facts)}


def _ordinal(value: float) -> str:
    """42.0 → "42nd" for the VIX percentile sentence."""
    n = int(value)
    suffix = (
        "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    )
    return f"{n}{suffix}"


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
    if touch == "unavailable":
        return "the option touches were unavailable"
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
        elif all(t == "unavailable" for t in touches):
            verdict = "unclear"
            happened = "the option touches were unavailable"
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


def plain_content(
    kind: str, facts: dict[str, Any], version: str | None = None
) -> dict[str, Any]:
    """The fallback content block for one review kind. ``version`` is the
    prompt that was about to run — v3+ gets the story shape (item 079);
    older versions keep theirs."""
    if kind == "briefing":
        if _story_version(kind, version):
            return briefing_story_plain(facts)
        return briefing_plain(facts)
    if kind == "verdict":
        return verdict_plain(facts)
    if _story_version(kind, version):
        return weekly_story_plain(facts)
    return weekly_plain(facts)
