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


def verdict_plain(facts: dict[str, Any]) -> dict[str, Any]:
    """{verdict, reason} for a draft verdict without a model.

    Enters: the trade's R decides (positive good, negative bad). Model
    skips: a move beyond the flat band decides — up is a missed winner
    (bad call), down an avoided loss (good call), inside the band unclear.
    """
    n = facts.get("sessions", 5)
    pct = facts.get("pct_5sessions")
    trade = facts.get("trade")
    if facts.get("final_action") == "skip":
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
    else:
        r = trade.get("r") if trade else None
        exit_label = trade.get("exit") if trade else None
        if exit_label is not None and r is not None:
            verdict = (
                "good_call"
                if float(r) > 0
                else "bad_call" if float(r) < 0 else "unclear"
            )
            reason = f"Closed at the {exit_label}, {_fmt_signed(r)}R."
        elif pct is not None:
            verdict = (
                "good_call"
                if float(pct) > 0
                else "bad_call" if float(pct) < 0 else "unclear"
            )
            direction = "Up" if float(pct) >= 0 else "Down"
            reason = (
                f"{direction} {abs(float(pct)):.1f}% {n} sessions after "
                "entry; position still open."
            )
        else:
            verdict, reason = "unclear", f"No {n}-session price recorded."
    return {"verdict": verdict, "reason": reason}


def plain_content(kind: str, facts: dict[str, Any]) -> dict[str, Any]:
    """The fallback content block for one review kind."""
    if kind == "briefing":
        return briefing_plain(facts)
    if kind == "verdict":
        return verdict_plain(facts)
    return weekly_plain(facts)
