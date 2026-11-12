"""Answer sentences and sample states for the Insights page (item 075).

The page reads as five questions; each gets one or two plain sentences
written here from the snapshot — fixed templates, no model, computed
with the snapshot so they never change between page loads
(docs/SPEC.md → Insights → GUI).

The "Probably / Not shown yet / Yes" wording thresholds for question 1
are display wording, not trading rules (they can change in a later
spec): the edge counts as proven only when the 90% range's low end
reaches ``WORDING_PROVEN_R``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# Wording thresholds (docs/SPEC.md → Insights → GUI): the low end of the
# expectancy range at 0.1R or more reads "Yes"; above 0 but under it,
# "Probably, but not proven yet"; a range that includes 0, "Not shown
# yet". Display only — never a trading rule.
WORDING_PROVEN_R = Decimal("0.1")

# Beta needs a few sessions of daily equity returns before it means
# anything (compute._beta returns null under 3 pairs).
BETA_MIN_SESSIONS = 3

# Sample-state thresholds for "What each filter removed" (docs/SPEC.md,
# Decision v5): counts from 100 decided touches, early under 100, too
# few under 30.
DECIDED_COUNTS = 100

MINUS = "−"  # U+2212, the design's minus sign


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _r(value: Any) -> str:
    """"+0.21R" / "−0.31R" from a snapshot decimal string."""
    n = _num(value)
    if n is None:
        return "—"
    sign = "+" if n > 0 else MINUS if n < 0 else ""
    return f"{sign}{abs(n):.2f}R"


def _usd(value: Any) -> str:
    """"+$14.80" / "−$84" — whole dollars drop the cents."""
    n = _num(value)
    if n is None:
        return "—"
    sign = "+" if n > 0 else MINUS if n < 0 else ""
    body = f"{abs(n):.2f}".removesuffix(".00")
    return f"{sign}${body}"


def _pct(value: Any) -> str:
    n = _num(value)
    return f"{n:.0f}%" if n is not None else "—"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# ----------------------------------------------------------------------
# the five questions


def answer_edge(snapshot: dict[str, Any]) -> str:
    """1 · Is there an edge? — the verdict, the expectancy range and
    beta in words."""
    headline = snapshot.get("headline") or {}
    exp = headline.get("expectancy") or {}
    avg, lo, hi = exp.get("avg_r"), exp.get("lo"), exp.get("hi")
    if avg is None or lo is None or hi is None:
        return (
            "Not enough closed trades yet to score the edge — the "
            "tiles below say how many are needed."
        )
    lo_f, hi_f = float(lo), float(hi)
    if hi_f < 0:
        verdict = "Probably not"
        tail = "the whole range sits below zero"
    elif lo_f <= 0:
        verdict = "Not shown yet"
        tail = "the range still includes zero"
    elif lo_f < float(WORDING_PROVEN_R):
        verdict = "Probably, but not proven yet"
        tail = "the range's low end is above zero but under 0.1R"
    else:
        verdict = "Yes"
        tail = "the range's low end is at least 0.1R"
    sentence = (
        f"{verdict}. Trades earned {_r(avg)} on average, and the 90% "
        f"range runs from {_r(lo)} to {_r(hi)} — {tail}."
    )
    beta = _num((headline.get("beta") or {}).get("beta"))
    if beta is not None:
        if beta < 0:
            words = "the result moves against the market"
        elif beta < 0.5:
            words = "the result doesn't come from the market going up"
        elif beta <= 1.0:
            words = "the result only partly follows the market"
        else:
            words = "the result follows the market closely"
        sentence += f" Beta {beta:.2f}: {words}."
    return sentence


def _finding_title(finding: dict[str, Any]) -> str:
    label = str(finding.get("dimension_label") or finding.get("dimension") or "")
    slice_ = str(finding.get("slice") or "")
    return f"{label} {slice_}".strip()


def answer_stands_out(
    snapshot: dict[str, Any], *, early_trades: int = 10, min_trades: int = 30
) -> str:
    """2 · What stands out? — counts of findings and early signals and
    their titles."""
    findings = snapshot.get("findings") or []
    trades = int(snapshot.get("trades") or 0)
    if not findings:
        if trades < early_trades:
            return (
                f"Nothing to compare yet — an early signal needs "
                f"{early_trades} closed trades and a finding "
                f"{min_trades}; this run has {_plural(trades, 'closed trade')}."
            )
        return (
            "Nothing stands out: no slice differs from the rest by "
            "enough to count."
        )
    n_findings = sum(1 for f in findings if f.get("status") == "finding")
    n_early = len(findings) - n_findings
    parts = []
    if n_findings:
        parts.append(_plural(n_findings, "finding"))
    if n_early:
        parts.append(_plural(n_early, "early signal", "early signals"))
    lead = " and ".join(parts)
    titles = "; ".join(_finding_title(f) for f in findings[:3])
    if len(findings) > 3:
        titles += f"; and {len(findings) - 3} more"
    return f"{lead[0].upper()}{lead[1:]}. {titles}."


def answer_trades_lost(snapshot: dict[str, Any]) -> str:
    """3 · Where are trades lost? — the rules' share of symbol checks,
    the two biggest losses after the model, missed vs avoided skips."""
    steps = {
        s.get("key"): s
        for s in ((snapshot.get("funnel") or {}).get("steps") or [])
    }
    out: list[str] = []
    checks = _num((steps.get("symbol_checks") or {}).get("count"))
    rules_lost = sum(
        int(loss.get("count") or 0)
        for loss in (steps.get("rules") or {}).get("losses") or []
        if loss.get("label") == "rules"
    )
    if checks:
        out.append(
            f"The rules stop {_pct(rules_lost / checks * 100)} of symbol "
            "checks."
        )
    # The biggest losses after the model proposed: code checks, risk
    # gates and unfilled limits. "Still open" is not a lost entry.
    losses: list[tuple[int, str]] = []
    for key, label in (
        ("code_checks", "code checks"),
        ("risk_gates", "risk gates"),
        ("filled", "unfilled limits"),
    ):
        total = sum(
            int(loss.get("count") or 0)
            for loss in (steps.get(key) or {}).get("losses") or []
        )
        if total:
            losses.append((total, label))
    losses.sort(key=lambda item: -item[0])
    if len(losses) >= 2:
        out.append(
            f"After the model, the {losses[0][1]} ({losses[0][0]}) and "
            f"{losses[1][1]} ({losses[1][0]}) cost the most entries."
        )
    elif losses:
        out.append(
            f"After the model, {losses[0][1]} ({losses[0][0]}) costs the "
            "most."
        )
    skipped = snapshot.get("skipped") or {}
    if not skipped.get("na") and skipped.get("scored"):
        missed = _num(skipped.get("missed_pct"))
        avoided = _num(skipped.get("avoided_pct"))
        if missed is not None and avoided is not None:
            if avoided > missed:
                out.append(
                    "Skipping adds value: more skipped setups would have "
                    f"hit their stop ({_pct(avoided)}) than their target "
                    f"({_pct(missed)})."
                )
            elif missed > avoided:
                out.append(
                    "Skipping may be leaving trades on the table: more "
                    f"would have reached the target ({_pct(missed)}) than "
                    f"hit the stop ({_pct(avoided)})."
                )
            else:
                out.append(
                    f"Missed and avoided skips are even ({_pct(missed)} "
                    f"/ {_pct(avoided)})."
                )
    return " ".join(out) or "No trades in this period yet."


def answer_conditions(snapshot: dict[str, Any]) -> str:
    """4 · Which conditions work? — the best slices of the default
    dimension (entry cycle) with their sample status."""
    slices = snapshot.get("slices") or {}
    dim = slices.get("entry_cycle") or next(iter(slices.values()), None)
    if not dim:
        return "No slices in this snapshot."
    label = str(dim.get("label") or "Slices")
    rows = [
        r
        for r in dim.get("rows") or []
        if r.get("avg_r") is not None
        and r.get("status") in ("finding", "early", "counted")
    ]
    if not rows:
        return (
            f"{label}: nothing to show yet — a slice starts counting "
            "at 10 closed trades."
        )
    top = sorted(rows, key=lambda r: -float(r["avg_r"]))[:2]
    best = " and ".join(
        f"{r['slice']} ({_r(r['avg_r'])})" for r in top
    )
    n_findings = sum(1 for r in rows if r.get("status") == "finding")
    n_early = sum(1 for r in rows if r.get("status") == "early")
    status = (
        f"{_plural(n_findings, 'finding')} and "
        f"{_plural(n_early, 'early signal', 'early signals')}"
    )
    return f"{label}: the best so far is {best} — {status}."


def answer_costs(snapshot: dict[str, Any]) -> str:
    """5 · What do confidence, fills and nights cost? — the calibration
    gap, gaps through the stop and overnight vs in session."""
    out: list[str] = []
    cal = snapshot.get("calibration") or {}
    if "na" not in cal:
        gap = _num(cal.get("weighted_gap_pts"))
        entries = int(cal.get("entries") or 0)
        if gap is None:
            need = int(cal.get("min_entries") or 50)
            out.append(
                f"Calibration needs {need} entries with a confidence; "
                f"{entries} so far."
            )
        elif gap > 10:
            out.append(
                f"Stated confidence is off by about {gap:.0f} points "
                "against what trades hit (target: within 10)."
            )
        else:
            out.append(
                f"Confidence is within its target — a gap of about "
                f"{gap:.0f} points (target: within 10)."
            )
    execution = snapshot.get("execution") or {}
    gaps = int(execution.get("gaps_count") or 0)
    if gaps:
        extra = _num(execution.get("gaps_extra_r"))
        out.append(
            f"{_plural(gaps, 'stop')} filled through the stop"
            + (f" for {abs(extra):.2f}R extra" if extra is not None else "")
            + "."
        )
    else:
        out.append("No stops filled through the stop.")
    overnight = snapshot.get("overnight") or {}
    in_session = _num(overnight.get("in_session_usd"))
    overnight_usd = _num(overnight.get("overnight_usd"))
    if in_session is not None and overnight_usd is not None:
        out.append(
            f"Overnight holds earned {_usd(overnight_usd)} while the "
            f"session earned {_usd(in_session)}."
        )
    return " ".join(out) or "Not enough data yet."


def compute_answers(snapshot: dict[str, Any]) -> dict[str, str]:
    """The ``answers`` block stored with the snapshot: one entry per
    question, each one or two sentences."""
    return {
        "edge": answer_edge(snapshot),
        "stands_out": answer_stands_out(snapshot),
        "trades_lost": answer_trades_lost(snapshot),
        "conditions": answer_conditions(snapshot),
        "costs": answer_costs(snapshot),
    }


# ----------------------------------------------------------------------
# sample states — "needs n trades · m so far" instead of dashes


def with_sample_states(
    data: dict[str, Any],
    *,
    early_trades: int = 10,
    min_trades: int = 30,
    calibration_min: int = 50,
    decided_min: int = DECIDED_COUNTS,
) -> dict[str, Any]:
    """Annotate every snapshot number that needs more trades with
    ``needs`` (how many) and ``have`` (how many so far), so the GUI can
    say "needs 10 closed trades · 4 so far" instead of showing a dash
    (docs/SPEC.md → Insights → GUI — Answers, not dashes).

    ``min_trades`` is accepted for symmetry with the other thresholds;
    slice rows already carry their sample status.
    """
    del min_trades  # slice rows carry status; nothing extra to add
    trades = int(data.get("trades") or 0)
    headline = data.get("headline")
    if isinstance(headline, dict):
        for key in ("expectancy", "edge_over_spy", "model_cost"):
            node = headline.get(key)
            if isinstance(node, dict):
                node["needs"] = early_trades
                node["have"] = trades
        beta = headline.get("beta")
        if isinstance(beta, dict):
            # Beta needs sessions of daily returns, not trades.
            beta["needs"] = BETA_MIN_SESSIONS
            beta["have"] = beta.get("sessions")
    calibration = data.get("calibration")
    if isinstance(calibration, dict) and "entries" in calibration:
        # The weighted gap shows from 50 entries (CALIBRATION_MIN).
        calibration["needs"] = calibration_min
        calibration["have"] = calibration.get("entries")
    for row in data.get("filters_removed") or []:
        if isinstance(row, dict):
            row["needs"] = decided_min
            row["have"] = row.get("decided")
    return data
