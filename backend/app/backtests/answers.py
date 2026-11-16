"""The answer sentence of a finished backtest run (docs/SPEC.md,
Backtesting, Results header — item 100).

Written by code from the results table and stored with the run's results
(``Backtest.summary["answer"]``) when the run finishes; runs that
finished before this module existed get it computed once on first read.
An interrupted run keeps "results so far" and gets no sentence.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

MINUS = "−"  # U+2212, the design's minus sign


def _signed_pct(value: Decimal, digits: int = 1) -> str:
    """"+18.6%" / "−4.1%" / "0.0%"."""
    q = Decimal(1).scaleb(-digits)
    v = value.quantize(q)
    if v > 0:
        return f"+{v}%"
    if v < 0:
        return f"{MINUS}{abs(v)}%"
    return f"{v}%"


def _points(diff: Decimal) -> str:
    """"21.3 points less" / "2.0 points more" / "the same as"."""
    q = abs(diff).quantize(Decimal("0.1"))
    if q == 0:
        return "the same as"
    return f"{q} points {'less' if diff < 0 else 'more'}"


def _metric(summary: dict[str, Any], key: str) -> Decimal | None:
    value = summary.get(key)
    return Decimal(str(value)) if value is not None else None


def _spy_drawdown(
    spy_curve: list[dict[str, Any]] | None, equity: Decimal
) -> Decimal | None:
    """Largest peak-to-trough drop of the SPY buy-and-hold curve, in % —
    the same computation the results table runs in the browser."""
    if not spy_curve:
        return None
    peak = equity
    worst = Decimal(0)
    for point in spy_curve:
        eq = Decimal(str(point["equity"]))
        peak = max(peak, eq)
        if peak:
            worst = min(worst, (eq - peak) / peak * 100)
    return abs(worst)


def answer_sentence(
    strategies: list[dict[str, Any]],
    summary: dict[str, Any] | None,
    starting_equity: Decimal,
) -> str | None:
    """One or more sentences from the run's stored results, or None when
    there is nothing to answer (no results, or the first strategy lacks
    the numbers the sentence needs).

    ``strategies`` is the run's ordered strategy list (``Backtest.
    strategies``); ``summary`` maps each slug to its results.
    """
    if not summary:
        return None
    rows = [
        (spec["name"], summary[spec["slug"]])
        for spec in strategies
        if spec.get("slug") in summary
    ]
    if not rows:
        return None
    first_name, first = rows[0]
    ret = _metric(first, "return_pct")
    spy = _metric(first, "spy_return_pct")
    if ret is None or spy is None:
        return None
    sentence = (
        f"{first_name} made {_signed_pct(ret)} where buying and holding "
        f"SPY made {_signed_pct(spy)}: {_points(ret - spy)}"
    )
    dd = _metric(first, "max_drawdown_pct")
    spy_dd = _spy_drawdown(first.get("spy_curve"), starting_equity)
    if dd is not None and spy_dd is not None:
        sentence += (
            f", with a deepest fall of {abs(dd).quantize(Decimal('0.1'))}% "
            f"against {spy_dd.quantize(Decimal('0.1'))}%"
        )
    tim = _metric(first, "time_in_market")
    if tim is not None:
        pct = (tim * 100).quantize(Decimal("1"))
        sentence += f" and money in the market {pct}% of the time"
    sentence += "."
    for name, s in rows[1:]:
        other = _metric(s, "return_pct")
        if other is None:
            continue
        if abs(other - ret).quantize(Decimal("0.1")) == 0:
            sentence += f" {name} made {_signed_pct(other)}, the same as {first_name}."
        else:
            sentence += (
                f" {name} made {_signed_pct(other)}, "
                f"{_points(other - ret)} than {first_name}."
            )
    return sentence
