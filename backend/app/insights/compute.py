"""The pure insights function (docs/SPEC.md, Insights — PR 22).

``compute_insights`` takes plain values — closed trades, decisions, skips,
orders, equity days and market bars, all fetched by the caller — and
returns the snapshot JSON for one strategy and one period. It never
touches the database or the network, so every number can be checked
against a hand calculation on a fixture.

All P&L is adjusted (slippage and dividends); 1R is the trade's initial
risk ``(entry − initial stop) × qty``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.market.clock import NY_TZ

# ----------------------------------------------------------------------
# inputs


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class Bar5m:
    start: datetime  # aware UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class EquityDay:
    """The strategy's last equity snapshot of one New York session."""

    date: date
    equity: Decimal
    cash: Decimal


@dataclass(frozen=True)
class InsightsDecision:
    """One decision row for the funnel (live or shadow, candidates only
    matter for most steps)."""

    id: int
    context: str  # candidate | held
    final_action: str
    no_call_reason: str | None
    model_action: str | None  # the model's choice before code checks
    code_checks_failed: tuple[str, ...] = ()
    gate: str | None = None  # gates_result.blocked_by
    entry_filled: bool | None = None
    missed: bool = False  # shadow work skipped for time/cost


@dataclass(frozen=True)
class InsightsOrder:
    """An entry or exit order for fill-rate and slippage numbers."""

    leg: str  # entry | stop | target | exit
    cycle_time: str | None  # "HH:MM" New York of the entry cycle
    decision_price: Decimal | None  # market price at the decision
    intended: Decimal | None  # entry limit / stop / target / exit reference
    filled: bool
    fill_price: Decimal | None
    fill_qty: int | None
    # for exit legs: stop | target | exit | earnings_exit | stop_breached
    exit_reason: str | None = None


@dataclass(frozen=True)
class InsightsTrade:
    """One closed trade with everything the slices need."""

    id: int
    symbol: str
    qty: int
    entry: Decimal
    exit_price: Decimal | None
    initial_stop: Decimal | None
    adjusted_pnl: Decimal | None
    exit_reason: str | None
    opened_at: datetime  # aware UTC
    closed_at: datetime
    first_touch: str | None = None  # target | stop | neither
    # raw dimension inputs — the function buckets them
    entry_cycle: str | None = None  # "HH:MM" New York
    weekday: int | None = None  # 0 = Monday
    sector: str | None = None
    regime: str | None = None
    days_to_earnings: int | None = None
    earnings_known: bool = True
    opening_gap_pct: Decimal | None = None
    entry_vwap: Decimal | None = None
    ema20_1h: Decimal | None = None
    atr_1h: Decimal | None = None
    rs_vs_spy_pct: Decimal | None = None
    spy_day_pct: Decimal | None = None
    atr_percentile: Decimal | None = None
    confidence: Decimal | None = None
    stop_basis: str | None = None
    target_basis: str | None = None
    sessions_held: int | None = None
    prompt_version: str | None = None
    model: str | None = None
    # exit execution detail
    stop_leg_price: Decimal | None = None  # the stop that fired (gap math)
    stop_fill_price: Decimal | None = None
    entry_ny: date | None = None  # session dates for overnight math
    exit_ny: date | None = None
    after_exit_move_pct: Decimal | None = None  # 5-session move (earnings exits)


@dataclass(frozen=True)
class InsightsSkip:
    """One model skip on a candidate, scored with the rules-only geometry."""

    id: int
    symbol: str
    decided_at: datetime  # aware UTC
    session_date: date  # New York session of the decision
    cycle_time: str | None
    price: Decimal | None  # market price at the decision
    atr_1h: Decimal | None
    dims: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# small helpers


def _q2(value: Decimal | None) -> str | None:
    return str(value.quantize(Decimal("0.01"))) if value is not None else None


def _q4(value: Decimal | None) -> str | None:
    return str(value.quantize(Decimal("0.0001"))) if value is not None else None


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / len(values) if values else None


def _sample_sd(values: Sequence[Decimal]) -> Decimal:
    """Sample standard deviation (n−1); 0 under two values."""
    if len(values) < 2:
        return Decimal(0)
    mean = sum(values, Decimal(0)) / len(values)
    var = sum(((v - mean) ** 2 for v in values), Decimal(0)) / (
        len(values) - 1
    )
    return var.sqrt()


def _risk_share(trade: InsightsTrade) -> Decimal | None:
    """Risk per share in entry terms; None when the trade can't be R-scored."""
    if trade.initial_stop is None or trade.entry <= trade.initial_stop:
        return None
    return trade.entry - trade.initial_stop


def _adjusted_r(trade: InsightsTrade) -> Decimal | None:
    risk = _risk_share(trade)
    if risk is None or trade.adjusted_pnl is None or trade.qty == 0:
        return None
    return trade.adjusted_pnl / (risk * trade.qty)


def _scored_rs(trades: Sequence[InsightsTrade]) -> dict[int, Decimal]:
    out: dict[int, Decimal] = {}
    for trade in trades:
        r = _adjusted_r(trade)
        if r is not None:
            out[trade.id] = r
    return out


# ----------------------------------------------------------------------
# headline numbers


def _expectancy(rs: list[Decimal], z: Decimal) -> dict[str, Any]:
    """Average R with its range: ± z standard errors on the mean."""
    avg = _mean(rs)
    if avg is None:
        return {"avg_r": None, "lo": None, "hi": None, "n": 0}
    se = _sample_sd(rs) / Decimal(len(rs)).sqrt()
    return {
        "avg_r": _q2(avg),
        "lo": _q2(avg - z * se),
        "hi": _q2(avg + z * se),
        "n": len(rs),
    }


def _beta(
    equity_daily: list[EquityDay], spy_daily: list[DailyBar], sessions: int
) -> dict[str, Any]:
    """Slope and correlation of daily strategy returns against SPY's,
    over the last ``sessions`` overlapping sessions."""
    spy_close = {b.date: b.close for b in spy_daily}
    strat: list[tuple[date, Decimal]] = [
        (d.date, d.equity) for d in sorted(equity_daily, key=lambda e: e.date)
    ]
    spy: list[tuple[date, Decimal]] = sorted(
        (d, c) for d, c in spy_close.items()
    )
    strat_returns: dict[date, Decimal] = {}
    for (_d0, e0), (d1, e1) in zip(strat, strat[1:], strict=False):
        if e0:
            strat_returns[d1] = (e1 - e0) / e0
    spy_returns: dict[date, Decimal] = {}
    for (_d0, c0), (d1, c1) in zip(spy, spy[1:], strict=False):
        if c0:
            spy_returns[d1] = (c1 - c0) / c0
    days = sorted(set(strat_returns) & set(spy_returns))[-sessions:]
    pairs = [(strat_returns[d], spy_returns[d]) for d in days]
    if len(pairs) < 3:
        return {"beta": None, "correlation": None, "sessions": len(pairs)}
    xs = [float(p[1]) for p in pairs]
    ys = [float(p[0]) for p in pairs]
    n = len(pairs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
    var_x = sum((x - mx) ** 2 for x in xs)
    var_y = sum((y - my) ** 2 for y in ys)
    beta = cov / var_x if var_x else None
    corr = cov / math.sqrt(var_x * var_y) if var_x and var_y else None
    return {
        "beta": f"{beta:.2f}" if beta is not None else None,
        "correlation": f"{corr:.2f}" if corr is not None else None,
        "sessions": n,
    }


def _overnight_split(
    trades: Sequence[InsightsTrade],
    daily_bars_for: Callable[[str], list[DailyBar]],
) -> dict[str, Any]:
    """P&L from the previous close to the open versus from the open to the
    close or exit, summed over held positions (docs/SPEC.md, Overnight)."""
    overnight = Decimal(0)
    in_session = Decimal(0)
    nights = 0
    gapped = 0
    worst: tuple[Decimal, str, date] | None = None
    for trade in trades:
        if trade.entry_ny is None or trade.exit_ny is None:
            continue
        risk = _risk_share(trade)
        bars = {
            b.date: b
            for b in daily_bars_for(trade.symbol)
            if trade.entry_ny <= b.date <= trade.exit_ny
        }
        day = trade.entry_ny
        prev_close: Decimal | None = None
        prior = [
            b for b in daily_bars_for(trade.symbol) if b.date < trade.entry_ny
        ]
        if prior:
            prev_close = prior[-1].close
        while day <= trade.exit_ny:
            bar = bars.get(day)
            if bar is None:
                day += timedelta(days=1)
                continue
            if day == trade.entry_ny:
                # Entry day: the entry fill to the close (or the exit).
                end = trade.exit_price if day == trade.exit_ny else bar.close
                if end is not None:
                    in_session += (end - trade.entry) * trade.qty
            else:
                # A held night: previous close to the open.
                if prev_close is not None:
                    move = bar.open - prev_close
                    nights += 1
                    overnight += move * trade.qty
                    if move < 0:
                        gapped += 1
                    if risk is not None:
                        gap_r = move / risk
                        if worst is None or gap_r < worst[0]:
                            worst = (gap_r, trade.symbol, day)
                end = trade.exit_price if day == trade.exit_ny else bar.close
                if end is not None:
                    in_session += (end - bar.open) * trade.qty
            prev_close = bar.close
            day += timedelta(days=1)
    return {
        "in_session_usd": _q2(in_session),
        "overnight_usd": _q2(overnight),
        "nights": nights,
        "gapped_against": gapped,
        "gapped_pct": _q2(Decimal(gapped) / nights * 100) if nights else None,
        "largest_gap": (
            {
                "r": _q2(worst[0]),
                "symbol": worst[1],
                "date": worst[2].isoformat(),
            }
            if worst is not None
            else None
        ),
    }


# ----------------------------------------------------------------------
# the funnel

_PRICE_REASONS = ("price_stale", "price_suspect", "quote_suspect")


def _funnel(
    decisions: Sequence[InsightsDecision],
    trades: Sequence[InsightsTrade],
    open_positions: int,
) -> dict[str, Any]:
    """Symbol checks → closed trades, with what was lost at each step.

    Losses are counted by reason so the card can say whether the agent
    trades rarely because of the market, the rules, the model or the
    risk limits (docs/SPEC.md, Where trades come from).
    """
    candidates = [
        d for d in decisions if d.context == "candidate" and not d.missed
    ]
    checks = len(candidates)
    rules_fail = [
        d
        for d in candidates
        if d.final_action == "no_call" and d.no_call_reason == "rules"
    ]
    passed_rules = len(candidates) - len(rules_fail)

    def no_call(reason: str) -> int:
        return sum(
            1
            for d in candidates
            if d.final_action == "no_call" and d.no_call_reason == reason
        )

    # Shadows store a failed price check as "blocked" with the reason as
    # its gate; count it with the price checks, never as a model call.
    price_gated = sum(
        1
        for d in candidates
        if d.final_action == "blocked" and d.gate in _PRICE_REASONS
    )
    call_cap = no_call("call_cap")
    price_blocked = sum(no_call(r) for r in _PRICE_REASONS) + price_gated
    no_options = no_call("no_options")
    # Reached the model (call attempted): every candidate not lost to the
    # pre-call reasons above.
    called = passed_rules - call_cap - price_blocked - no_options

    called_rows = [
        d
        for d in candidates
        if not (
            d.final_action == "no_call"
            and d.no_call_reason
            in ("rules", "call_cap", "no_options", *_PRICE_REASONS)
        )
        and not (d.final_action == "blocked" and d.gate in _PRICE_REASONS)
    ]
    proposed = [d for d in called_rows if d.model_action == "enter"]
    model_skips = sum(1 for d in called_rows if d.model_action == "skip")
    model_errors = sum(
        1
        for d in called_rows
        if d.final_action == "no_call" and d.no_call_reason == "model_error"
    )
    invalid = len(called_rows) - len(proposed) - model_skips - model_errors

    passed_checks = [d for d in proposed if d.final_action != "skip"]
    check_losses: dict[str, int] = {}
    for d in proposed:
        if d.final_action == "skip":
            for name in d.code_checks_failed or ("code checks",):
                check_losses[name] = check_losses.get(name, 0) + 1

    passed_gates = [d for d in passed_checks if d.final_action == "enter"]
    gate_losses: dict[str, int] = {}
    for d in passed_checks:
        if d.final_action == "blocked":
            gate = d.gate or "risk gates"
            gate_losses[gate] = gate_losses.get(gate, 0) + 1

    filled = sum(1 for d in passed_gates if d.entry_filled)
    unfilled = len(passed_gates) - filled

    closed = len(trades)

    def step(
        key: str,
        count: int,
        losses: list[tuple[str, int]],
        detail: str | None = None,
    ) -> dict[str, Any]:
        return {
            "key": key,
            "count": count,
            "detail": detail,
            "losses": [
                {"label": label, "count": n}
                for label, n in sorted(losses, key=lambda item: -item[1])
            ],
        }

    steps = [
        step("symbol_checks", checks, []),
        step("rules", passed_rules, [("rules", len(rules_fail))]),
        step(
            "called",
            called,
            [
                ("call cap", call_cap),
                ("price checks", price_blocked),
                ("no options", no_options),
            ],
        ),
        step(
            "proposed",
            len(proposed),
            [
                ("model skipped", model_skips),
                ("invalid output", invalid),
                ("call failed", model_errors),
            ],
        ),
        step("code_checks", len(passed_checks), sorted(check_losses.items())),
        step("risk_gates", len(passed_gates), sorted(gate_losses.items())),
        step("filled", filled, [("unfilled limits", unfilled)]),
        step("closed", closed, [("still open", open_positions)]),
    ]
    return {"steps": steps}


# ----------------------------------------------------------------------
# dimensions and slices

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _tercile_edges(values: list[Decimal]) -> tuple[Decimal, Decimal] | None:
    """Bottom and top tercile cut points of the trade values present."""
    if len(values) < 3:
        return None
    ordered = sorted(values)
    n = len(ordered)

    def at(fr: float) -> Decimal:
        pos = fr * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * Decimal(
            str(pos - lo)
        )

    return at(1 / 3), at(2 / 3)


def _conf_bucket(confidence: Decimal) -> str:
    tenth = min(int(confidence * 10), 9)
    return f"{tenth / 10:.1f}–{(tenth + 1) / 10:.1f}"


def _dimension_values(
    trade: InsightsTrade,
    *,
    spy_flat_pct: Decimal,
    opening_gap_pct: Decimal,
    strength_edges: tuple[Decimal, Decimal] | None,
    vol_edges: tuple[Decimal, Decimal] | None,
) -> dict[str, str | None]:
    """Every dimension's slice key for one trade (None = not sliceable)."""
    gap = trade.opening_gap_pct
    if gap is None:
        gap_key = None
    elif gap < -opening_gap_pct:
        gap_key = f"down >{opening_gap_pct:g}%"
    elif gap > opening_gap_pct:
        gap_key = f"up >{opening_gap_pct:g}%"
    else:
        gap_key = f"within {opening_gap_pct:g}%"

    dte = trade.days_to_earnings
    if dte is None:
        dte_key = "none scheduled" if trade.earnings_known else None
    elif dte <= 10:
        dte_key = "4–10 days"
    elif dte <= 20:
        dte_key = "11–20 days"
    else:
        dte_key = "21+ days"

    vwap_key = None
    if trade.entry_vwap is not None:
        vwap_key = "above VWAP" if trade.entry >= trade.entry_vwap else "below VWAP"

    pull_key = None
    if (
        trade.ema20_1h is not None
        and trade.atr_1h is not None
        and trade.atr_1h > 0
    ):
        depth = abs(trade.entry - trade.ema20_1h) / trade.atr_1h
        pull_key = (
            "under 0.5 ATR"
            if depth < Decimal("0.5")
            else "0.5–1.5 ATR"
            if depth <= Decimal("1.5")
            else "over 1.5 ATR"
        )

    strength_key = None
    if trade.rs_vs_spy_pct is not None and strength_edges is not None:
        lo, hi = strength_edges
        strength_key = (
            "weakest third"
            if trade.rs_vs_spy_pct < lo
            else "middle third"
            if trade.rs_vs_spy_pct < hi
            else "strongest third"
        )

    spy_key = None
    if trade.spy_day_pct is not None:
        spy_key = (
            "down day"
            if trade.spy_day_pct < -spy_flat_pct
            else "up day"
            if trade.spy_day_pct > spy_flat_pct
            else "flat day"
        )

    vol_key = None
    if trade.atr_percentile is not None and vol_edges is not None:
        lo, hi = vol_edges
        vol_key = (
            "low third"
            if trade.atr_percentile < lo
            else "middle third"
            if trade.atr_percentile < hi
            else "high third"
        )

    basis_key = None
    if trade.stop_basis is not None or trade.target_basis is not None:
        basis_key = (
            f"{trade.stop_basis or '—'} → {trade.target_basis or '—'}"
        )

    held_key = None
    if trade.sessions_held is not None:
        held_key = (
            "1 session"
            if trade.sessions_held <= 1
            else "2–3 sessions"
            if trade.sessions_held <= 3
            else "4+ sessions"
        )

    pm_key = None
    if trade.prompt_version is not None or trade.model is not None:
        pm_key = " · ".join(
            part for part in (trade.prompt_version, trade.model) if part
        ) or None

    return {
        "entry_cycle": trade.entry_cycle,
        "weekday": (
            _WEEKDAYS[trade.weekday] if trade.weekday is not None else None
        ),
        "sector": trade.sector or "Unknown",
        "symbol": trade.symbol,
        "regime": trade.regime,
        "days_to_earnings": dte_key,
        "opening_gap": gap_key,
        "entry_vs_vwap": vwap_key,
        "pullback_depth": pull_key,
        "strength_vs_spy": strength_key,
        "spy_that_day": spy_key,
        "volatility": vol_key,
        "confidence": (
            _conf_bucket(trade.confidence)
            if trade.confidence is not None
            else None
        ),
        "stop_target_basis": basis_key,
        "sessions_held": held_key,
        "prompt_model": pm_key,
    }


DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("entry_cycle", "Entry cycle"),
    ("weekday", "Weekday"),
    ("sector", "Sector"),
    ("symbol", "Symbol"),
    ("regime", "Market regime"),
    ("days_to_earnings", "Days to earnings"),
    ("opening_gap", "Opening gap"),
    ("entry_vs_vwap", "Entry vs VWAP"),
    ("pullback_depth", "Pullback depth"),
    ("strength_vs_spy", "Strength vs SPY"),
    ("spy_that_day", "SPY that day"),
    ("volatility", "Volatility"),
    ("confidence", "Confidence"),
    ("stop_target_basis", "Stop and target basis"),
    ("sessions_held", "Sessions held"),
    ("prompt_model", "Prompt and model"),
)


def _sample_status(
    n: int,
    sigma: Decimal | None,
    *,
    min_trades: int,
    early_trades: int,
    finding_sigma: Decimal,
) -> str:
    """finding | early | hidden (docs/SPEC.md, Slices — Sample status)."""
    if n < early_trades:
        return "hidden"
    big = sigma is not None and sigma >= finding_sigma
    if n >= min_trades:
        return "finding" if big else "counted"
    return "early" if big else "counted"


def _slices(
    trades: Sequence[InsightsTrade],
    skip_dims: Sequence[dict[str, Any]],
    *,
    min_trades: int,
    early_trades: int,
    finding_sigma: Decimal,
    range_z: Decimal,
    spy_flat_pct: Decimal,
    opening_gap_pct: Decimal,
    cycles: list[str],
    last_cycle_no_entries: bool,
    entry_cycles: list[str] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Per dimension: one row per slice with its sample status, plus the
    findings list across all dimensions."""
    rs = _scored_rs(trades)
    list(rs.values())

    strength_edges = _tercile_edges(
        [t.rs_vs_spy_pct for t in trades if t.rs_vs_spy_pct is not None]
    )
    vol_edges = _tercile_edges(
        [t.atr_percentile for t in trades if t.atr_percentile is not None]
    )

    dims_of: dict[int, dict[str, str | None]] = {
        t.id: _dimension_values(
            t,
            spy_flat_pct=spy_flat_pct,
            opening_gap_pct=opening_gap_pct,
            strength_edges=strength_edges,
            vol_edges=vol_edges,
        )
        for t in trades
    }
    # Skips carry their own dimension values (same keys).
    skips_by_dim: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for skip in skip_dims:
        for key, _label in DIMENSIONS:
            value = skip.get(key)
            if value is not None:
                skips_by_dim.setdefault(key, {}).setdefault(
                    value, []
                ).append(skip)

    findings: list[dict[str, Any]] = []
    out_dims: dict[str, Any] = {}
    for key, label in DIMENSIONS:
        groups: dict[str, list[InsightsTrade]] = {}
        for trade in trades:
            value = dims_of[trade.id][key]
            if value is not None:
                groups.setdefault(value, []).append(trade)

        # Entry cycle always lists the configured cycles, in order; a
        # cycle the rules never trade shows its rule instead of numbers.
        order: list[str]
        rules_rows: dict[str, str] = {}
        if key == "entry_cycle":
            order = list(cycles)
            for c in cycles:
                if c not in groups:
                    if last_cycle_no_entries and c == cycles[-1]:
                        rules_rows[c] = (
                            "No entries in the last cycle of a session "
                            "(rule). Held positions are still reviewed."
                        )
                    elif entry_cycles is not None and c not in entry_cycles:
                        rules_rows[c] = (
                            "The strategy's entry_cycles override does "
                            "not allow entries in this cycle."
                        )
            order += sorted(k for k in groups if k not in order)
        else:
            order = sorted(groups)

        rows: list[dict[str, Any]] = []
        hidden = 0
        for value in order:
            members = groups.get(value, [])
            if value in rules_rows and not members:
                rows.append(
                    {
                        "slice": value,
                        "trades": 0,
                        "status": "rule",
                        "rule": rules_rows[value],
                    }
                )
                continue
            member_rs = [rs[t.id] for t in members if t.id in rs]
            member_ids = {t.id for t in members}
            rest = [r for tid, r in rs.items() if tid not in member_ids]

            sigma: Decimal | None = None
            diff: Decimal | None = None
            if len(member_rs) >= 2 and len(rest) >= 1:
                avg_in = _mean(member_rs)
                avg_out = _mean(rest)
                diff = (avg_in - avg_out) if avg_in is not None and avg_out is not None else None
                var = _sample_sd(member_rs) ** 2 / len(member_rs) + (
                    _sample_sd(rest) ** 2 / len(rest)
                )
                se = var.sqrt()
                if se == 0:
                    sigma = (
                        Decimal(999)
                        if diff is not None and diff != 0
                        else Decimal(0)
                    )
                else:
                    sigma = abs(diff) / se if diff is not None else None
            status = _sample_status(
                len(members),
                sigma,
                min_trades=min_trades,
                early_trades=early_trades,
                finding_sigma=finding_sigma,
            )
            if status == "hidden":
                hidden += len(members)
                continue
            won = sum(
                1
                for t in members
                if t.adjusted_pnl is not None and t.adjusted_pnl > 0
            )
            avg_r = _mean(member_rs)
            se_r = (
                _sample_sd(member_rs) / Decimal(len(member_rs)).sqrt()
                if len(member_rs) >= 2
                else None
            )
            skip_rows = skips_by_dim.get(key, {}).get(value, [])
            skip_missed = sum(
                1 for s in skip_rows if s.get("outcome") == "missed"
            )
            skip_avoided = sum(
                1 for s in skip_rows if s.get("outcome") == "avoided"
            )
            row: dict[str, Any] = {
                "slice": value,
                "trades": len(members),
                "won": won,
                "win_rate_pct": (
                    _q2(Decimal(won) / len(members) * 100) if members else None
                ),
                "avg_r": _q2(avg_r),
                "lo": _q2(avg_r - range_z * se_r)
                if avg_r is not None and se_r is not None
                else None,
                "hi": _q2(avg_r + range_z * se_r)
                if avg_r is not None and se_r is not None
                else None,
                "total_r": _q2(sum(member_rs, Decimal(0)))
                if member_rs
                else None,
                "skips": len(skip_rows),
                "skips_missed_pct": _q2(
                    Decimal(skip_missed) / len(skip_rows) * 100
                )
                if skip_rows
                else None,
                "skips_avoided_pct": _q2(
                    Decimal(skip_avoided) / len(skip_rows) * 100
                )
                if skip_rows
                else None,
                "status": status,
                "sigma": _q2(sigma),
                "diff_r": _q2(diff),
                "rest_trades": len(rest),
                "rest_avg_r": _q2(_mean(rest)),
                "trade_ids": [t.id for t in members],
            }
            rows.append(row)
            if status in ("finding", "early"):
                findings.append(
                    {
                        "dimension": key,
                        "dimension_label": label,
                        "slice": value,
                        "status": status,
                        "trades": len(members),
                        "avg_r": _q2(avg_r),
                        "rest_trades": len(rest),
                        "rest_avg_r": _q2(_mean(rest)),
                        "sigma": _q2(sigma),
                        "total_r": _q2(sum(member_rs, Decimal(0)))
                        if member_rs
                        else None,
                        "trade_ids": [t.id for t in members],
                    }
                )
        out_dims[key] = {"label": label, "rows": rows, "hidden": hidden}
    findings.sort(
        key=lambda f: (
            0 if f["status"] == "finding" else 1,
            -(abs(Decimal(f["sigma"])) if f["sigma"] else Decimal(0)),
        )
    )
    return out_dims, findings


# ----------------------------------------------------------------------
# skipped setups


def _score_skip(
    skip: InsightsSkip,
    bars: list[Bar5m],
    *,
    stop_atr: Decimal,
    target_r: Decimal,
    entry_offset_pct: Decimal,
    fill_minutes: int,
) -> tuple[str, Decimal | None]:
    """Rules-only geometry on one skip (docs/SPEC.md, Skipped setups).

    Entry at the decision's limit (price + offset), stop ``stop_atr`` ×
    ATR below, target ``target_r`` R. The stop is already checked on the
    fill bar; the target counts from the next bar, and a bar touching
    both counts as a stop — the shadow fill rules applied to stored bars.
    Returns (outcome, r) with outcome missed | avoided | neither.
    """
    if skip.price is None or skip.atr_1h is None or skip.atr_1h <= 0:
        return "neither", None
    limit = skip.price * (1 + entry_offset_pct / 100)
    stop = limit - stop_atr * skip.atr_1h
    risk = limit - stop
    target = limit + target_r * risk
    deadline = skip.decided_at + timedelta(minutes=fill_minutes)
    filled_at: int | None = None
    for i, bar in enumerate(bars):
        if bar.start >= deadline:
            break
        if bar.low <= limit:
            filled_at = i
            break
    if filled_at is None:
        return "neither", None
    bar = bars[filled_at]
    if bar.low <= stop:  # the stop is already checked on the fill bar
        return "avoided", Decimal(-1)
    for bar in bars[filled_at + 1 :]:
        if bar.low <= stop:
            return "avoided", Decimal(-1)
        if bar.high >= target:
            return "missed", target_r
    if bars:
        return "neither", (bars[-1].close - limit) / risk
    return "neither", Decimal(0)


def _skipped_setups(
    skips: Sequence[InsightsSkip],
    bars_5m_for: Callable[[str, datetime, datetime], list[Bar5m]],
    *,
    sessions: int,
    session_dates: list[date],
    stop_atr: Decimal,
    target_r: Decimal,
    entry_offset_pct: Decimal,
    fill_minutes: int,
) -> dict[str, Any]:
    """Score every model skip; also returns each skip's outcome for the
    per-slice skip columns."""
    scored = {"missed": 0, "avoided": 0, "neither": 0}
    rs: list[Decimal] = []
    outcomes: list[str | None] = []
    dates_sorted = sorted(session_dates)
    for skip in skips:
        if skip.price is None or skip.atr_1h is None:
            outcomes.append(None)
            continue
        start_idx = next(
            (i for i, d in enumerate(dates_sorted) if d >= skip.session_date),
            None,
        )
        if start_idx is None:
            outcomes.append(None)
            continue
        window = set(
            dates_sorted[start_idx : start_idx + sessions]
        )
        end = (
            datetime.combine(max(window), datetime.max.time()).replace(
                tzinfo=NY_TZ
            )
            if window
            else skip.decided_at
        )
        bars = [
            b
            for b in bars_5m_for(skip.symbol, skip.decided_at, end)
            if b.start >= skip.decided_at
            and b.start.astimezone(NY_TZ).date() in window
        ]
        outcome, r = _score_skip(
            skip,
            bars,
            stop_atr=stop_atr,
            target_r=target_r,
            entry_offset_pct=entry_offset_pct,
            fill_minutes=fill_minutes,
        )
        scored[outcome] += 1
        if r is not None:
            rs.append(r)
        outcomes.append(outcome)
    total = sum(scored.values())
    return {
        "total": len(skips),
        "scored": total,
        "missed": scored["missed"],
        "avoided": scored["avoided"],
        "neither": scored["neither"],
        "missed_pct": _q2(Decimal(scored["missed"]) / total * 100)
        if total
        else None,
        "avoided_pct": _q2(Decimal(scored["avoided"]) / total * 100)
        if total
        else None,
        "neither_pct": _q2(Decimal(scored["neither"]) / total * 100)
        if total
        else None,
        "baseline_r": _q2(_mean(rs)),
        "outcomes": outcomes,
    }


# ----------------------------------------------------------------------
# calibration

CALIBRATION_MIN = 50
CALIBRATION_GROUP_MIN = 10


def _calibration(
    trades: Sequence[InsightsTrade],
    *,
    min_trades: int,
    early_trades: int,
    range_z: Decimal,
) -> dict[str, Any]:
    """Per 0.1 confidence bucket: stated confidence against the share of
    entries that reached the target before the stop, with the readiness
    weighted gap (docs/SPEC.md, Calibration)."""
    rows = [
        t
        for t in trades
        if t.confidence is not None and t.first_touch is not None
    ]
    buckets: dict[int, list[InsightsTrade]] = {}
    for trade in rows:
        tenth = min(int(trade.confidence * 10), 9)  # type: ignore[operator]
        buckets.setdefault(tenth, []).append(trade)

    out: list[dict[str, Any]] = []
    hidden = 0
    weighted = Decimal(0)
    weight = 0
    for tenth in sorted(buckets):
        members = buckets[tenth]
        stated = _mean([t.confidence for t in members if t.confidence is not None])
        reached = sum(1 for t in members if t.first_touch == "target")
        actual = Decimal(reached) / len(members) if members else None
        n = len(members)
        status = (
            "finding"
            if n >= min_trades
            else "early"
            if n >= early_trades
            else "hidden"
        )
        if status == "hidden":
            hidden += n
        if n >= CALIBRATION_GROUP_MIN and stated is not None and actual is not None:
            weighted += abs(stated - actual) * n
            weight += n
        out.append(
            {
                "label": f"{tenth / 10:.1f}–{(tenth + 1) / 10:.1f}",
                "stated": _q2(stated),
                "actual": _q2(actual),
                "n": n,
                "status": status,
            }
        )
    return {
        "buckets": out,
        "hidden": hidden,
        "entries": len(rows),
        "weighted_gap_pts": (
            _q2(weighted / weight * 100) if weight and len(rows) >= CALIBRATION_MIN else None
        ),
        "min_entries": CALIBRATION_MIN,
        "group_min": CALIBRATION_GROUP_MIN,
    }


# ----------------------------------------------------------------------
# execution


def _execution(
    orders: Sequence[InsightsOrder],
    trades: Sequence[InsightsTrade],
    *,
    gap_through_stop_r: Decimal,
) -> dict[str, Any]:
    """Fills, slippage in basis points, gaps through the stop, and the
    rules-and-calendar rows (docs/SPEC.md, Execution)."""
    entries = [o for o in orders if o.leg == "entry"]
    placed = len(entries)
    filled_orders = [o for o in entries if o.filled]
    fill_rate = Decimal(len(filled_orders)) / placed if placed else None

    # The worst entry cycle: lowest fill rate across cycle times.
    by_cycle: dict[str, list[InsightsOrder]] = {}
    for order in entries:
        by_cycle.setdefault(order.cycle_time or "?", []).append(order)
    worst_cycle: dict[str, Any] | None = None
    for cycle, group in sorted(by_cycle.items()):
        rate = Decimal(sum(1 for o in group if o.filled)) / len(group)
        if worst_cycle is None or rate < Decimal(str(worst_cycle["rate"])):
            worst_cycle = {
                "cycle": cycle,
                "rate": float(rate),
                "filled": sum(1 for o in group if o.filled),
                "placed": len(group),
            }

    def bps(ref: Decimal | None, fill: Decimal | None, *, sell: bool) -> Decimal | None:
        if ref is None or fill is None or not ref:
            return None
        diff = ref - fill if sell else fill - ref
        return diff / ref * 10000

    entry_slips = [
        s
        for o in filled_orders
        if (s := bps(o.decision_price, o.fill_price, sell=False)) is not None
    ]
    exits = [o for o in orders if o.leg in ("stop", "target", "exit")]
    splits: dict[str, list[Decimal]] = {}
    for order in exits:
        reason = order.exit_reason or order.leg
        slip = bps(order.intended, order.fill_price, sell=True)
        if slip is not None:
            splits.setdefault(reason, []).append(slip)
    exit_all = [s for group in splits.values() for s in group]

    # Gaps through the stop: stop exits that filled more than
    # gap_through_stop_r below the stop, and the extra R beyond −1R.
    gaps = 0
    extra_r = Decimal(0)
    for trade in trades:
        if trade.exit_reason not in ("stop", "stop_breached"):
            continue
        risk = _risk_share(trade)
        ref = trade.stop_leg_price or trade.initial_stop
        fill = trade.stop_fill_price or trade.exit_price
        if risk is None or ref is None or fill is None or fill >= ref:
            continue
        extra = (ref - fill) / risk
        if extra > gap_through_stop_r:
            gaps += 1
            extra_r += extra

    # Agent exits on the entry day (the model sold the same session).
    same_day = [
        t
        for t in trades
        if t.exit_reason == "exit"
        and t.entry_ny is not None
        and t.entry_ny == t.exit_ny
    ]
    rs = _scored_rs(trades)
    same_day_rs = [rs[t.id] for t in same_day if t.id in rs]

    earnings = [t for t in trades if t.exit_reason == "earnings_exit"]
    earnings_rs = [rs[t.id] for t in earnings if t.id in rs]
    moves = sorted(
        t.after_exit_move_pct for t in earnings if t.after_exit_move_pct is not None
    )
    median_move: Decimal | None = None
    if moves:
        mid = len(moves) // 2
        median_move = (
            moves[mid]
            if len(moves) % 2
            else (moves[mid - 1] + moves[mid]) / 2
        )

    return {
        "fill_rate": _q2(fill_rate * 100) if fill_rate is not None else None,
        "filled": len(filled_orders),
        "placed": placed,
        "worst_cycle": worst_cycle,
        "entry_slippage_bps": _q2(_mean(entry_slips)),
        "exit_slippage_bps": _q2(_mean(exit_all)),
        "exit_slippage_splits": [
            {"reason": reason, "bps": _q2(_mean(values)), "n": len(values)}
            for reason, values in sorted(splits.items())
        ],
        "gaps_count": gaps,
        "gaps_extra_r": _q2(extra_r),
        "same_day_exits": len(same_day),
        "same_day_avg_r": _q2(_mean(same_day_rs)),
        "earnings_exits": len(earnings),
        "earnings_avg_r": _q2(_mean(earnings_rs)),
        "earnings_median_move_pct": _q2(median_move),
        "earnings_moves_scored": len(moves),
    }


# ----------------------------------------------------------------------
# the one function


def compute_insights(
    trades: Sequence[InsightsTrade],
    decisions: Sequence[InsightsDecision],
    skips: Sequence[InsightsSkip],
    orders: Sequence[InsightsOrder],
    *,
    equity_daily: Sequence[EquityDay],
    spy_daily: Sequence[DailyBar],
    daily_bars_for: Callable[[str], list[DailyBar]],
    bars_5m_for: Callable[[str, datetime, datetime], list[Bar5m]],
    session_dates: Sequence[date],
    return_pct: Decimal | None,
    model_cost: Decimal,
    dividends_usd: Decimal,
    dividend_positions: int,
    open_positions: int,
    strategy_kind: str = "llm",
    cycles: Sequence[str] = ("10:46", "11:46", "12:46", "13:46", "14:46", "15:46"),
    last_cycle_no_entries: bool = True,
    entry_cycles: Sequence[str] | None = None,
    min_trades: int = 30,
    early_trades: int = 10,
    finding_sigma: Decimal = Decimal("2"),
    range_z: Decimal = Decimal("1.64"),
    skip_stop_atr: Decimal = Decimal("2"),
    skip_target_r: Decimal = Decimal("2"),
    skip_sessions: int = 5,
    gap_through_stop_r: Decimal = Decimal("0.1"),
    spy_flat_pct: Decimal = Decimal("0.3"),
    opening_gap_pct: Decimal = Decimal("1"),
    entry_limit_offset_pct: Decimal = Decimal("0.05"),
    entry_timeout_minutes: int = 10,
    beta_sessions: int = 20,
) -> dict[str, Any]:
    """The whole insights snapshot for one strategy and period.

    Every input is a plain value the caller already fetched: the function
    reads no tables and makes no calls, so a fixture run reproduces every
    number by hand.
    """
    rs_map = _scored_rs(trades)
    all_rs = list(rs_map.values())
    z = Decimal(str(range_z))

    # ---- headline -------------------------------------------------------
    expectancy = _expectancy(all_rs, z)

    # Edge over SPY at the same exposure.
    spy_close = {b.date: b.close for b in spy_daily}
    spy_return_pct: Decimal | None = None
    if equity_daily:
        days = [d.date for d in equity_daily if d.date in spy_close]
        if days:
            first, last = min(days), max(days)
            if spy_close[first]:
                spy_return_pct = (
                    (spy_close[last] - spy_close[first])
                    / spy_close[first]
                    * 100
                )
    exposure: Decimal | None = None
    shares = [
        (d.equity - d.cash) / d.equity
        for d in equity_daily
        if d.equity and d.cash is not None
    ]
    if shares:
        exposure = _mean(shares)
    edge = (
        return_pct - spy_return_pct * exposure
        if return_pct is not None
        and spy_return_pct is not None
        and exposure is not None
        else None
    )

    beta = _beta(list(equity_daily), list(spy_daily), beta_sessions)

    overnight = _overnight_split(trades, daily_bars_for)

    gross_profit = sum(
        (t.adjusted_pnl for t in trades if t.adjusted_pnl and t.adjusted_pnl > 0),
        Decimal(0),
    )
    closed = len(trades)
    headline = {
        "expectancy": expectancy,
        "edge_over_spy": {
            "pts": _q2(edge),
            "return_pct": _q2(return_pct),
            "spy_return_pct": _q2(spy_return_pct),
            "exposure": _q2(exposure),
        },
        "beta": beta,
        "overnight": {
            "in_session_usd": overnight["in_session_usd"],
            "overnight_usd": overnight["overnight_usd"],
        },
        "model_cost": {
            "total_usd": _q2(model_cost),
            "per_trade_usd": (
                _q2(model_cost / closed) if closed else None
            ),
            "pct_gross_profit": (
                _q2(model_cost / gross_profit * 100)
                if gross_profit > 0
                else None
            ),
        },
    }

    # ---- funnel -----------------------------------------------------------
    funnel = _funnel(decisions, trades, open_positions)

    # ---- skipped setups ----------------------------------------------------
    llm = strategy_kind == "llm"
    skipped: dict[str, Any]
    skip_dims: list[dict[str, Any]] = []
    if llm:
        scored_skips = _skipped_setups(
            skips,
            bars_5m_for,
            sessions=skip_sessions,
            session_dates=list(session_dates),
            stop_atr=Decimal(str(skip_stop_atr)),
            target_r=Decimal(str(skip_target_r)),
            entry_offset_pct=Decimal(str(entry_limit_offset_pct)),
            fill_minutes=entry_timeout_minutes,
        )
        for skip, outcome in zip(skips, scored_skips["outcomes"], strict=False):
            dims = dict(skip.dims)
            dims["outcome"] = outcome
            skip_dims.append(dims)
        skipped = {k: v for k, v in scored_skips.items() if k != "outcomes"}
    else:
        skipped = {"total": 0, "scored": 0, "na": "rules only makes no model skips"}

    # ---- slices -------------------------------------------------------------
    slices, findings = _slices(
        list(trades),
        skip_dims,
        min_trades=min_trades,
        early_trades=early_trades,
        finding_sigma=Decimal(str(finding_sigma)),
        range_z=z,
        spy_flat_pct=Decimal(str(spy_flat_pct)),
        opening_gap_pct=Decimal(str(opening_gap_pct)),
        cycles=list(cycles),
        last_cycle_no_entries=last_cycle_no_entries,
        entry_cycles=list(entry_cycles) if entry_cycles is not None else None,
    )

    # ---- execution ------------------------------------------------------------
    execution = _execution(
        orders,
        trades,
        gap_through_stop_r=Decimal(str(gap_through_stop_r)),
    )
    headline["gaps"] = {
        "count": execution["gaps_count"],
        "extra_r": execution["gaps_extra_r"],
    }
    execution["dividends_usd"] = _q2(dividends_usd)
    execution["dividend_positions"] = dividend_positions

    calibration = (
        _calibration(
            trades,
            min_trades=min_trades,
            early_trades=early_trades,
            range_z=z,
        )
        if llm
        else {"na": "rules only has no model confidence"}
    )

    return {
        "trades": closed,
        "headline": headline,
        "funnel": funnel,
        "findings": findings,
        "skipped": skipped,
        "slices": slices,
        "calibration": calibration,
        "execution": execution,
        "overnight": overnight,
    }
