"""Market-context analysis for work item 061.

The report is built from a consistent read-only copy of the live SQLite
database.  The script never opens the running database for writes and never
uses the network.  It is intentionally self-contained: a later refresh is
``make analysis-061`` and nothing else.
"""

from __future__ import annotations

# The report renderer intentionally keeps markdown rows and explanatory prose
# as single logical lines; wrapping them in source would make tables harder to audit.
# ruff: noqa: E501
import argparse
import json
import re
import sqlite3
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

NY_TZ = ZoneInfo("America/New_York")
TOO_FEW = 20
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 61061
DEFAULT_SINCE = date(2026, 11, 11)
ITEM071_MERGED_AT = datetime(2026, 11, 11, 13, 43, 11, tzinfo=UTC)

# Item 113 asks for these exact text families.  They are matched,
# case-insensitively, against the stored conflict text only — not against the
# thesis, data flags or validation text.
CONFLICT_PATTERNS = {
    "time left": re.compile(
        r"\b(?:time left|minutes left|time remaining|little time|limited time|short time)\b",
        re.IGNORECASE,
    ),
    "the close": re.compile(
        r"\b(?:close|closing|session end|end of (?:the )?session)\b", re.IGNORECASE
    ),
    "overnight hold": re.compile(
        r"\b(?:overnight|overnight hold|hold overnight|held overnight)\b", re.IGNORECASE
    ),
}

CYCLE_LABELS = ("10:46", "11:46", "12:46", "13:46", "14:46", "15:46")
DECIDED_TOUCHES = {"target", "stop", "neither"}


def _json(value: object) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return None


def _obj(value: object) -> dict[str, Any]:
    parsed = _json(value)
    return parsed if isinstance(parsed, dict) else {}


def _subdict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    parsed = _json(value)
    return parsed if isinstance(parsed, list) else []


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return out if out.is_finite() else None


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raw = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            raw = datetime.fromisoformat(text)
        except ValueError:
            # SQLite datetime columns in this app commonly use a space and
            # six fractional digits; fromisoformat accepts them, so this is
            # only a final defensive pass over odd legacy values.
            try:
                raw = datetime.strptime(text.split("+")[0], "%Y-%m-%d %H:%M:%S.%f").replace(
                    tzinfo=UTC
                )
            except ValueError:
                try:
                    raw = datetime.strptime(text.split("+")[0], "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=UTC
                    )
                except ValueError:
                    return None
    if raw.tzinfo is None:
        return raw.replace(tzinfo=UTC)
    return raw.astimezone(UTC)


def _date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _ny_day(at: datetime) -> date:
    return at.astimezone(NY_TZ).date()


def _cycle_label(at: datetime) -> str:
    return at.astimezone(NY_TZ).strftime("%H:%M")


def _moneyish(value: Decimal | float | None, digits: int = 2, sign: bool = False) -> str:
    if value is None:
        return "—"
    number = float(value)
    prefix = "+" if sign and number > 0 else ""
    return f"{prefix}{number:.{digits}f}"


def _pct(part: int | float, whole: int | float) -> str:
    return f"{part / whole * 100:.0f}%" if whole else "—"


def _n_text(n: int) -> str:
    return f"{n} too few" if n < TOO_FEW else str(n)


def bootstrap_mean_range(
    values: Sequence[float],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float, float] | None:
    """Average and its 90% bootstrap range, with a fixed seed.

    The seed is reset for every cell.  That makes the markdown byte-for-byte
    reproducible and keeps a cell's interval independent of the order in which
    earlier cells were rendered.
    """
    if not values:
        return None
    arr = np.asarray(values, dtype=float)
    if len(arr) == 1:
        value = float(arr[0])
        return value, value, value
    rng = np.random.default_rng(seed)
    # Chunked resampling keeps large cells (thousands of option rows) from
    # materialising a 2,000 × n matrix at once; the random stream and result
    # are identical to the unchunked draw.
    chunks: list[np.ndarray] = []
    for start in range(0, resamples, 100):
        size = min(100, resamples - start)
        picks = rng.integers(0, len(arr), size=(size, len(arr)))
        chunks.append(arr[picks].mean(axis=1))
    means = np.concatenate(chunks)
    lo, hi = np.percentile(means, [5.0, 95.0])
    return float(arr.mean()), float(lo), float(hi)


def _r_cell(values: Sequence[float]) -> str:
    result = bootstrap_mean_range(values)
    if result is None:
        return "—"
    avg, lo, hi = result
    return f"{avg:+.2f}R [{lo:+.2f}, {hi:+.2f}]"


def _conflict_texts(model_output: dict[str, Any]) -> list[str]:
    conflicts = model_output.get("conflicts")
    if not isinstance(conflicts, list):
        return []
    texts: list[str] = []
    for conflict in conflicts:
        text = conflict.get("text") if isinstance(conflict, dict) else conflict
        if text is not None:
            texts.append(str(text))
    return texts


def conflict_pattern_hits(model_output: dict[str, Any]) -> set[str]:
    """Which item-113 conflict families the stored conflict texts mention."""
    text = "\n".join(_conflict_texts(model_output))
    return {name for name, pattern in CONFLICT_PATTERNS.items() if pattern.search(text)}


@dataclass(frozen=True)
class Bar5:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class Bar1d:
    day: date
    open: Decimal
    close: Decimal


@dataclass(frozen=True)
class TouchResult:
    outcome: str  # stop | target | neither | unavailable
    price: Decimal | None
    at: datetime | None
    r: float | None


@dataclass(frozen=True)
class CounterfactualExit:
    next_open_r: float | None
    next_close_r: float | None
    five_session_r: float | None
    touch: TouchResult
    status: str  # complete | pending | unavailable


def _touch_price(bar: Bar5, stop: Decimal, target: Decimal | None) -> tuple[str, Decimal] | None:
    """First stop/target result in one bar; gaps fill at the bar open.

    A bar touching both resolves to the stop, matching the app's pessimistic
    fill rule.  The target is not checked when it is absent.
    """
    stop_hit = bar.low <= stop
    target_hit = target is not None and bar.high >= target
    if stop_hit:
        return "stop", bar.open if bar.open < stop else stop
    if target_hit and target is not None:
        return "target", bar.open if bar.open > target else target
    return None


def simulate_counterfactual_hold(
    *,
    entry: Decimal,
    initial_stop: Decimal,
    stop: Decimal,
    target: Decimal | None,
    decision_time: datetime,
    decision_day: date,
    bars5: Sequence[Bar5],
    daily: Sequence[Bar1d],
) -> CounterfactualExit:
    """What keeping an exited position would have made, in initial-risk R.

    ``daily`` is one row per trading session for the symbol.  The next session
    after ``decision_day`` is session 1; the fifth row is the 5-session close.
    Stop and target remain fixed at the levels visible in the decision's
    snapshot.  A touch before a horizon supersedes that horizon's mark.
    """
    risk = entry - initial_stop
    if risk <= 0:
        return CounterfactualExit(
            None, None, None, TouchResult("unavailable", None, None, None), "unavailable"
        )

    def rr(price: Decimal | None) -> float | None:
        return float((price - entry) / risk) if price is not None else None

    later = [bar for bar in daily if bar.day > decision_day]
    if not later:
        return CounterfactualExit(
            None, None, None, TouchResult("unavailable", None, None, None), "pending"
        )
    next_daily = later[0]
    fifth_daily = later[4] if len(later) >= 5 else None

    ordered = sorted((bar for bar in bars5 if bar.start >= decision_time), key=lambda b: b.start)
    touch = TouchResult("neither", None, None, None)
    last_seen: Bar5 | None = None
    for bar in ordered:
        if fifth_daily is not None and bar.start.astimezone(NY_TZ).date() > fifth_daily.day:
            break
        last_seen = bar
        hit = _touch_price(bar, stop, target)
        if hit is not None:
            outcome, price = hit
            touch = TouchResult(outcome, price, bar.start, rr(price))
            break
    if touch.outcome == "neither":
        if fifth_daily is None:
            # The window has not finished.  A partial answer is useful in the
            # markdown only for the first two horizons, so keep the touch row
            # pending rather than pretending five sessions exist.
            pass
        elif last_seen is not None:
            touch = TouchResult("neither", fifth_daily.close, None, rr(fifth_daily.close))
        else:
            touch = TouchResult("unavailable", None, None, None)

    def horizon(price: Decimal | None, horizon_day: date) -> float | None:
        if (
            touch.outcome in ("stop", "target")
            and touch.at is not None
            and touch.at.astimezone(NY_TZ).date() <= horizon_day
        ):
            return touch.r
        return rr(price)

    next_open_r = rr(next_daily.open)
    next_close_r = horizon(next_daily.close, next_daily.day)
    five_r = horizon(fifth_daily.close, fifth_daily.day) if fifth_daily is not None else None
    complete = fifth_daily is not None and (
        touch.outcome in ("stop", "target", "neither") or five_r is not None
    )
    return CounterfactualExit(
        next_open_r,
        next_close_r,
        five_r,
        touch,
        "complete" if complete else "pending",
    )


@dataclass
class DecisionRow:
    source_kind: str
    source_label: str
    strategy_label: str
    prompt_version: str
    id: int
    symbol: str
    context: str
    final_action: str
    no_call_reason: str | None
    scheduled: datetime
    cycle_time: str
    session_day: date
    input_snapshot: dict[str, Any]
    model_output: dict[str, Any]
    validation_result: dict[str, Any]
    rules_result: dict[str, Any]
    options_column: dict[str, Any]
    chosen_option: str | None
    outcome_touches: dict[str, Any]
    outcome_price_5sessions: Decimal | None
    position_override: dict[str, Any]
    cycle: dict[str, Any]
    market_context: dict[str, Any]
    missed: bool = False


@dataclass
class TradeRow:
    source_kind: str
    source_label: str
    prompt_version: str
    symbol: str
    r: float
    win: bool
    first_touch: str | None
    entry_day: date
    entry_cycle: str | None
    decision_id: int | None
    dims: dict[str, Any]


@dataclass
class OptionRow:
    source_kind: str
    source_label: str
    prompt_version: str
    decision_id: int
    symbol: str
    cycle_time: str
    session_day: date
    final_action: str
    option_id: str
    touch: str | None
    r_made: float | None
    dims: dict[str, Any]
    filter_name: str | None = None


@dataclass
class Dataset:
    name: str
    decisions: list[DecisionRow] = field(default_factory=list)
    trades: list[TradeRow] = field(default_factory=list)
    options: list[OptionRow] = field(default_factory=list)


def copy_database_readonly(source: Path, destination: Path) -> None:
    """Copy SQLite through the backup API while opening the source read-only."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _prompt_short(prompt_version: object) -> str:
    text = str(prompt_version or "")
    match = re.search(r"decision\.v(\d+)", text)
    return f"v{match.group(1)}" if match else "other"


def _source_label(kind: str, prompt_version: object, strategy_name: str | None = None) -> str:
    short = _prompt_short(prompt_version)
    if kind == "live":
        return f"live {short}"
    if kind == "shadow":
        if short in ("v5", "v6"):
            return f"shadow {short}"
        return f"shadow other ({strategy_name or 'unnamed'})"
    return f"backtest {strategy_name or short}"


def _decision_from_parts(
    *,
    source_kind: str,
    strategy_label: str,
    row: sqlite3.Row,
    snapshot: dict[str, Any],
    market_context: dict[str, Any],
    touches: dict[str, Any],
    price5: Decimal | None,
    position_override: dict[str, Any] | None = None,
    missed: bool = False,
) -> DecisionRow | None:
    scheduled = _dt(row["scheduled_time"])
    if scheduled is None:
        return None
    prompt_version = str(row["prompt_version"] or "")
    return DecisionRow(
        source_kind=source_kind,
        source_label=_source_label(source_kind, prompt_version, strategy_label),
        strategy_label=strategy_label,
        prompt_version=prompt_version,
        id=int(row["id"]),
        symbol=str(row["symbol"]),
        context=str(row["context"]),
        final_action=str(row["final_action"]),
        no_call_reason=row["no_call_reason"],
        scheduled=scheduled,
        cycle_time=_cycle_label(scheduled),
        session_day=_ny_day(scheduled),
        input_snapshot=snapshot,
        model_output=_obj(row["model_output"]),
        validation_result=_obj(row["validation_result"]),
        rules_result=_obj(row["rules_result"]),
        options_column=_obj(row["options_json"]),
        chosen_option=row["chosen_option"],
        outcome_touches=touches,
        outcome_price_5sessions=price5,
        position_override=position_override or {},
        cycle={
            "vix_close": _dec(row["vix_close"]),
            "vix_percentile_1y": _dec(row["vix_percentile_1y"]),
            "vix_change_5d_pct": _dec(row["vix_change_5d_pct"]),
            "breadth_above_sma50_pct": _dec(row["breadth_above_sma50_pct"]),
            "breadth_above_sma200_pct": _dec(row["breadth_above_sma200_pct"]),
            "breadth_ema_up_pct": _dec(row["breadth_ema_up_pct"]),
            "breadth_up_today_pct": _dec(row["breadth_up_today_pct"]),
            "sectors": _obj(row["sectors"]),
            "next_release": _obj(row["next_release"]),
        },
        market_context=market_context,
        missed=missed,
    )


def extract_live_decisions(conn: sqlite3.Connection, since: datetime) -> list[DecisionRow]:
    rows = conn.execute(
        """
        SELECT d.id, d.symbol, d.context, d.final_action, d.no_call_reason,
               d.prompt_version, d.input_snapshot, d.model_output,
               d.validation_result, d.rules_result, d.options AS options_json,
               d.chosen_option, d.market_context,
               c.scheduled_time, c.vix_close, c.vix_percentile_1y,
               c.vix_change_5d_pct, c.breadth_above_sma50_pct,
               c.breadth_above_sma200_pct, c.breadth_ema_up_pct,
               c.breadth_up_today_pct, c.sectors, c.next_release,
               o.option_touches, o.price_5sessions
        FROM decisions d
        JOIN cycles c ON c.id = d.cycle_id
        LEFT JOIN outcomes o ON o.decision_id = d.id
        WHERE c.kind = 'cycle' AND c.scheduled_time >= ?
        ORDER BY d.id
        """,
        (since.replace(tzinfo=None).isoformat(sep=" "),),
    ).fetchall()
    out: list[DecisionRow] = []
    for row in rows:
        decision = _decision_from_parts(
            source_kind="live",
            strategy_label="live",
            row=row,
            snapshot=_obj(row["input_snapshot"]),
            market_context=_obj(row["market_context"]),
            touches=_obj(row["option_touches"]),
            price5=_dec(row["price_5sessions"]),
        )
        if decision is not None:
            out.append(decision)
    return out


def extract_shadow_decisions(conn: sqlite3.Connection, since: datetime) -> list[DecisionRow]:
    rows = conn.execute(
        """
        SELECT sd.id, sd.symbol, sd.context, sd.final_action, sd.no_call_reason,
               sd.prompt_version, sd.input_snapshot, sd.model_output,
               sd.validation_result, sd.rules_result, sd.options AS options_json,
               sd.chosen_option, sd.option_touches, sd.missed, sd.position,
               st.name AS strategy_name,
               ld.input_snapshot AS live_snapshot,
               ld.market_context AS live_market_context,
               c.scheduled_time, c.vix_close, c.vix_percentile_1y,
               c.vix_change_5d_pct, c.breadth_above_sma50_pct,
               c.breadth_above_sma200_pct, c.breadth_ema_up_pct,
               c.breadth_up_today_pct, c.sectors, c.next_release,
               NULL AS price_5sessions
        FROM shadow_decisions sd
        JOIN cycles c ON c.id = sd.cycle_id
        JOIN strategies st ON st.id = sd.strategy_id
        LEFT JOIN decisions ld ON ld.id = sd.live_decision_id
        WHERE c.kind = 'cycle' AND c.scheduled_time >= ?
        ORDER BY sd.id
        """,
        (since.replace(tzinfo=None).isoformat(sep=" "),),
    ).fetchall()
    out: list[DecisionRow] = []
    for row in rows:
        snapshot = _obj(row["input_snapshot"]) or _obj(row["live_snapshot"])
        decision = _decision_from_parts(
            source_kind="shadow",
            strategy_label=str(row["strategy_name"] or "shadow"),
            row=row,
            snapshot=snapshot,
            market_context=_obj(row["live_market_context"]),
            touches=_obj(row["option_touches"]),
            price5=None,
            position_override=_obj(row["position"]),
            missed=bool(row["missed"]),
        )
        if decision is not None:
            out.append(decision)
    return out


def _dims(decision: DecisionRow) -> dict[str, Any]:
    snap = decision.input_snapshot
    indicators = _subdict(snap, "indicators_1h")
    daily = _subdict(snap, "indicators_daily")
    regime = _subdict(snap, "regime")
    cycle = decision.cycle
    release = _subdict(cycle, "next_release")
    release_sessions = release.get("sessions_away")
    try:
        release_sessions_int = int(str(release_sessions)) if release_sessions is not None else None
    except (TypeError, ValueError):
        release_sessions_int = None
    mc = decision.market_context
    return {
        "vix_percentile": cycle.get("vix_percentile_1y"),
        "vix_change_5d_pct": cycle.get("vix_change_5d_pct"),
        "breadth": cycle.get("breadth_above_sma50_pct"),
        "sector_rank": mc.get("sector_rank"),
        "stock_vs_sector": _dec(mc.get("rs_vs_sector_pct")),
        "release_sessions": release_sessions_int,
        "release_next_morning": (
            release_sessions_int == 1 and str(release.get("time") or "") == "08:30"
        )
        if release_sessions_int is not None
        else None,
        "atr_percentile": _dec(daily.get("atr_percentile")),
        "regime_trend": regime.get("trend"),
        "rs_vs_spy_pct": _dec(indicators.get("rs_vs_spy_pct")),
        "symbol": decision.symbol,
        "entry_cycle": decision.cycle_time,
    }


def _r_made(
    touch: str | None,
    option: dict[str, Any],
    price_5sessions: Decimal | None,
    shared_limit: Decimal | None,
) -> Decimal | None:
    """The counterfactual R of an option, matching Insights' definition."""
    rr = _dec(option.get("rr"))
    if touch == "target":
        return rr
    if touch == "stop":
        return Decimal("-1")
    if touch == "not_filled":
        return Decimal(0)
    if touch != "neither" or price_5sessions is None:
        return None
    stop = _dec(option.get("stop"))
    entry = shared_limit or _dec(option.get("entry_limit"))
    if entry is None and stop is not None and option.get("risk") is not None:
        risk = _dec(option["risk"])
        entry = stop + risk if risk is not None else None
    if entry is None or stop is None or entry <= stop:
        return None
    return (price_5sessions - entry) / (entry - stop)


def _rules_filter(rules: dict[str, Any]) -> str:
    for check in rules.get("checks") or []:
        if isinstance(check, dict) and not check.get("passed"):
            return str(check.get("name") or "rules")
    return "rules"


def _counterfactual_filter(decision: DecisionRow) -> str | None:
    """The code filter whose counterfactual options one row carries."""
    column = decision.options_column
    reason = str(column.get("counterfactual_reason") or decision.no_call_reason or "")
    if decision.no_call_reason == "rules" or reason.startswith("rules"):
        return _rules_filter(decision.rules_result)
    if reason.startswith("trend_gate"):
        return "trend gate"
    if reason.startswith("checks"):
        return "checks gate"
    if (decision.no_call_reason or "").startswith("price"):
        return "price checks"
    validation_reason = str(decision.validation_result.get("reason") or "")
    if validation_reason in ("edge_too_small", "sideways_rule"):
        return "confidence gate" if validation_reason == "edge_too_small" else "sideways rule"
    return reason or None


def extract_option_rows(decisions: Iterable[DecisionRow]) -> list[OptionRow]:
    out: list[OptionRow] = []
    for decision in decisions:
        if decision.context != "candidate":
            continue
        column = decision.options_column
        constraints = decision.input_snapshot.get("constraints")
        shared_limit = (
            _dec(constraints.get("entry_limit")) if isinstance(constraints, dict) else None
        )
        options: list[tuple[dict[str, Any], str | None]] = []
        for key in ("options", "removed"):
            entries = column.get(key)
            if isinstance(entries, list):
                for option in entries:
                    if isinstance(option, dict):
                        filter_name = None
                        if key == "removed":
                            filter_name = str(option.get("reason") or "removed")
                        options.append((option, filter_name))
        if not options:
            continue
        counterfactual = _counterfactual_filter(decision)
        for option, removed_filter in options:
            oid = option.get("id") or option.get("option_id")
            if oid is None:
                continue
            touch_raw = decision.outcome_touches.get(str(oid))
            touch = str(touch_raw) if touch_raw is not None else None
            made = _r_made(touch, option, decision.outcome_price_5sessions, shared_limit)
            filter_name = removed_filter or (
                counterfactual if decision.no_call_reason is not None else None
            )
            out.append(
                OptionRow(
                    source_kind=decision.source_kind,
                    source_label=decision.source_label,
                    prompt_version=decision.prompt_version,
                    decision_id=decision.id,
                    symbol=decision.symbol,
                    cycle_time=decision.cycle_time,
                    session_day=decision.session_day,
                    final_action=decision.final_action,
                    option_id=str(oid),
                    touch=touch,
                    r_made=_float(made),
                    dims=_dims(decision),
                    filter_name=filter_name,
                )
            )
    return out


def _first_touch_from_exit_reason(reason: object) -> str | None:
    text = str(reason or "")
    if text == "target":
        return "target"
    if text in ("stop", "stop_breached"):
        return "stop"
    return "neither" if text else None


def extract_live_trades(conn: sqlite3.Connection, decisions: list[DecisionRow]) -> list[TradeRow]:
    by_id = {d.id: d for d in decisions}
    rows = conn.execute(
        """
        SELECT id, symbol, qty, avg_entry_price, initial_stop,
               realized_pnl_adjusted, realized_pnl_raw, exit_reason,
               opened_at, closed_at, entry_decision_id
        FROM positions
        WHERE closed_at IS NOT NULL AND entry_decision_id IS NOT NULL
        ORDER BY id
        """
    ).fetchall()
    out: list[TradeRow] = []
    for row in rows:
        decision = by_id.get(int(row["entry_decision_id"]))
        if decision is None:
            continue
        entry = _dec(row["avg_entry_price"])
        initial = _dec(row["initial_stop"])
        pnl = _dec(row["realized_pnl_adjusted"])
        qty = int(row["qty"] or 0)
        if entry is None or initial is None or entry <= initial or pnl is None or qty == 0:
            continue
        r = float(pnl / ((entry - initial) * qty))
        opened = _dt(row["opened_at"])
        out.append(
            TradeRow(
                source_kind="live",
                source_label=decision.source_label,
                prompt_version=decision.prompt_version,
                symbol=str(row["symbol"]),
                r=r,
                win=r > 0,
                first_touch=_first_touch_from_exit_reason(row["exit_reason"]),
                entry_day=_ny_day(opened) if opened else decision.session_day,
                entry_cycle=decision.cycle_time,
                decision_id=decision.id,
                dims=_dims(decision),
            )
        )
    return out


def extract_shadow_trades(conn: sqlite3.Connection, decisions: list[DecisionRow]) -> list[TradeRow]:
    by_id = {d.id: d for d in decisions}
    rows = conn.execute(
        """
        SELECT sp.id, sp.symbol, sp.r, sp.exit_reason, sp.opened_at,
               sp.entry_decision_id, st.name AS strategy_name
        FROM shadow_positions sp
        JOIN strategies st ON st.id = sp.strategy_id
        WHERE sp.status = 'closed' AND sp.entry_decision_id IS NOT NULL
        ORDER BY sp.id
        """
    ).fetchall()
    out: list[TradeRow] = []
    for row in rows:
        decision = by_id.get(int(row["entry_decision_id"]))
        r = _dec(row["r"])
        if decision is None or r is None:
            continue
        opened = _dt(row["opened_at"])
        rv = float(r)
        out.append(
            TradeRow(
                source_kind="shadow",
                source_label=decision.source_label,
                prompt_version=decision.prompt_version,
                symbol=str(row["symbol"]),
                r=rv,
                win=rv > 0,
                first_touch=_first_touch_from_exit_reason(row["exit_reason"]),
                entry_day=_ny_day(opened) if opened else decision.session_day,
                entry_cycle=decision.cycle_time,
                decision_id=decision.id,
                dims=_dims(decision),
            )
        )
    return out


def extract_dataset(conn: sqlite3.Connection, since: datetime, *, name: str) -> Dataset:
    live = extract_live_decisions(conn, since)
    shadows = extract_shadow_decisions(conn, since)
    decisions = live + shadows
    return Dataset(
        name=name,
        decisions=decisions,
        trades=extract_live_trades(conn, live) + extract_shadow_trades(conn, shadows),
        options=extract_option_rows(decisions),
    )


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def load_daily(conn: sqlite3.Connection, symbol: str) -> list[Bar1d]:
    if not table_exists(conn, "bars_1d"):
        return []
    rows = conn.execute(
        "SELECT date, open, close FROM bars_1d WHERE symbol = ? ORDER BY date", (symbol,)
    ).fetchall()
    out: list[Bar1d] = []
    for row in rows:
        day = _date(row["date"])
        open_ = _dec(row["open"])
        close = _dec(row["close"])
        if day is not None and open_ is not None and close is not None:
            out.append(Bar1d(day, open_, close))
    return out


def load_bars5(
    conn: sqlite3.Connection, symbol: str, start: datetime, end_day: date | None
) -> list[Bar5]:
    if not table_exists(conn, "bars_5m"):
        return []
    end = datetime.combine(
        (end_day or (start.date() + timedelta(days=14))) + timedelta(days=1), time.min, UTC
    )
    rows = conn.execute(
        """
        SELECT start, open, high, low, close FROM bars_5m
        WHERE symbol = ? AND start >= ? AND start < ?
        ORDER BY start
        """,
        (
            symbol,
            start.replace(tzinfo=None).isoformat(sep=" "),
            end.replace(tzinfo=None).isoformat(sep=" "),
        ),
    ).fetchall()
    out: list[Bar5] = []
    for row in rows:
        at = _dt(row["start"])
        prices = [_dec(row[key]) for key in ("open", "high", "low", "close")]
        if at is not None and all(price is not None for price in prices):
            out.append(Bar5(at, *prices))  # type: ignore[arg-type]
    return out


def merge_completed_daily(daily: Sequence[Bar1d], bars5: Sequence[Bar5]) -> list[Bar1d]:
    """Add a daily bar synthesized from complete regular-session 5-minute data.

    ``bars_1d`` is written after the close, while a refresh can run before that
    job.  A day counts as complete only when the 5-minute rows cover nearly the
    whole regular session and reach the final 15:55 bar; otherwise the close is
    a partial price and must stay pending rather than enter an R cell.
    """
    merged = {bar.day: bar for bar in daily}
    grouped: dict[date, list[Bar5]] = defaultdict(list)
    for bar in sorted(bars5, key=lambda row: row.start):
        grouped[bar.start.astimezone(NY_TZ).date()].append(bar)
    for day, rows in grouped.items():
        if day in merged or len(rows) < 70:
            continue
        last_start = rows[-1].start.astimezone(NY_TZ).time()
        if last_start >= time(15, 55):
            merged[day] = Bar1d(day, rows[0].open, rows[-1].close)
    return [merged[day] for day in sorted(merged)]


def load_earnings(conn: sqlite3.Connection) -> set[tuple[str, date]]:
    if not table_exists(conn, "earnings"):
        return set()
    rows = conn.execute(
        "SELECT symbol, date, time_of_day FROM earnings WHERE time_of_day IN ('before_open', 'unknown')"
    ).fetchall()
    out: set[tuple[str, date]] = set()
    for row in rows:
        # Earning.date is stored as midnight on the report's calendar date,
        # not as a New York instant.  Taking its date directly avoids shifting
        # that midnight UTC timestamp back to the previous US evening.
        day = _date(str(row["date"])[:10])
        if day is not None:
            out.add((str(row["symbol"]), day))
    return out


def load_release_days(conn: sqlite3.Connection) -> set[date]:
    if not table_exists(conn, "economic_events"):
        return set()
    rows = conn.execute("SELECT DISTINCT date FROM economic_events WHERE time = '08:30'").fetchall()
    return {day for row in rows if (day := _date(row["date"])) is not None}


@dataclass
class OvernightDecision:
    source_label: str
    prompt_version: str
    decision_id: int
    symbol: str
    action_group: str  # held | exited
    stop_distance_group: str
    morning_event_group: str
    exit_cf: CounterfactualExit | None
    overnight_gap_r: float | None
    open_below_stop: bool | None
    status: str


def _last_cycle_held_decisions(decisions: Iterable[DecisionRow]) -> list[DecisionRow]:
    rows: list[DecisionRow] = []
    for decision in decisions:
        if decision.context != "held" or decision.missed:
            continue
        if _prompt_short(decision.prompt_version) not in ("v5", "v6"):
            continue
        session = decision.input_snapshot.get("session")
        if not isinstance(session, dict) or session.get("last_cycle") is not True:
            continue
        if decision.final_action in ("hold", "tighten_stop", "exit"):
            rows.append(decision)
    return rows


def compute_overnight_rows(
    conn: sqlite3.Connection, decisions: list[DecisionRow]
) -> list[OvernightDecision]:
    earnings = load_earnings(conn)
    release_days = load_release_days(conn)
    daily_cache: dict[str, list[Bar1d]] = {}
    out: list[OvernightDecision] = []
    for decision in _last_cycle_held_decisions(decisions):
        position = decision.position_override or decision.input_snapshot.get("position")
        if not isinstance(position, dict):
            continue
        entry = _dec(position.get("entry"))
        initial_stop = _dec(position.get("initial_stop"))
        stop = _dec(position.get("stop"))
        target = _dec(position.get("target"))
        if entry is None or initial_stop is None or stop is None or entry <= initial_stop:
            action_group = "held" if decision.final_action in ("hold", "tighten_stop") else "exited"
            out.append(
                OvernightDecision(
                    decision.source_label,
                    decision.prompt_version,
                    decision.id,
                    decision.symbol,
                    action_group,
                    "unknown",
                    "unknown",
                    None,
                    None,
                    None,
                    "unavailable",
                )
            )
            continue
        symbol = decision.symbol
        if symbol not in daily_cache:
            daily_cache[symbol] = load_daily(conn, symbol)
        stored_daily = daily_cache[symbol]
        # Always load a fourteen-calendar-day window: five trading sessions can
        # include a holiday week, and the synthesized-close fallback below needs
        # that window whether or not bars_1d has caught up yet.
        bars5 = load_bars5(conn, symbol, decision.scheduled, None)
        daily = merge_completed_daily(stored_daily, bars5)
        later = [bar for bar in daily if bar.day > decision.session_day]
        cf = simulate_counterfactual_hold(
            entry=entry,
            initial_stop=initial_stop,
            stop=stop,
            target=target,
            decision_time=decision.scheduled,
            decision_day=decision.session_day,
            bars5=bars5,
            daily=daily,
        )
        risk = entry - initial_stop
        overnight_gap_r: float | None = None
        open_below_stop: bool | None = None
        if later:
            current_close: Decimal | None = None
            for bar in daily:
                if bar.day == decision.session_day:
                    current_close = bar.close
            if current_close is None:
                same_day = [
                    bar
                    for bar in bars5
                    if bar.start.astimezone(NY_TZ).date() == decision.session_day
                ]
                if same_day:
                    current_close = same_day[-1].close
            if current_close is not None:
                overnight_gap_r = float((later[0].open - current_close) / risk)
                open_below_stop = later[0].open < stop
        stop_distance = _dec(position.get("stop_distance_atr"))
        overnight = decision.input_snapshot.get("overnight")
        gap_p90 = _dec(overnight.get("gap_p90_atr")) if isinstance(overnight, dict) else None
        if stop_distance is None or gap_p90 is None:
            stop_group = "unknown"
        elif stop_distance < gap_p90:
            stop_group = "stop inside usual gap"
        else:
            stop_group = "stop outside usual gap"
        next_day = later[0].day if later else None
        if next_day is None:
            morning = "unknown"
        else:
            morning = (
                "earnings or release next morning"
                if (symbol, next_day) in earnings or next_day in release_days
                else "no earnings or release next morning"
            )
        action_group = "held" if decision.final_action in ("hold", "tighten_stop") else "exited"
        out.append(
            OvernightDecision(
                decision.source_label,
                decision.prompt_version,
                decision.id,
                symbol,
                action_group,
                stop_group,
                morning,
                cf if action_group == "exited" else None,
                overnight_gap_r if action_group == "held" else None,
                open_below_stop if action_group == "held" else None,
                cf.status,
            )
        )
    return out


def _group_rows(rows: Iterable[Any], keys: Sequence[str]) -> dict[tuple[Any, ...], list[Any]]:
    grouped: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    for row in rows:
        grouped[tuple(getattr(row, key) if hasattr(row, key) else row[key] for key in keys)].append(
            row
        )
    return grouped


def render_entries_skips(decisions: list[DecisionRow]) -> list[str]:
    candidates = [
        d
        for d in decisions
        if d.context == "candidate"
        and not d.missed
        and _prompt_short(d.prompt_version) in ("v5", "v6")
    ]
    sources = sorted({d.source_label for d in candidates})
    lines = [
        "### 1. Entries and skips per cycle",
        "",
        "Entries are final-action `enter` rows; skips are final-action `skip` rows. `no_call` and `blocked` rows are shown for context but are not model skips. The time/close/overnight share is over model skips and uses the stored conflict text only.",
        "",
        "| Source | Cycle | Entries | Model skips | no_call/blocked | Skips mentioning time, close or overnight |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for source in sources:
        for label in CYCLE_LABELS:
            rows = [d for d in candidates if d.source_label == source and d.cycle_time == label]
            entries = sum(1 for d in rows if d.final_action == "enter")
            skips = [d for d in rows if d.final_action == "skip"]
            other = len(rows) - entries - len(skips)
            timed = sum(1 for d in skips if conflict_pattern_hits(d.model_output))
            lines.append(
                f"| {source} | {label} | {_n_text(entries)} | {_n_text(len(skips))} | {other} | "
                f"{timed}/{len(skips)} ({_pct(timed, len(skips))}) |"
            )
    lines += [
        "",
        "Conflict-match patterns used, case-insensitively:",
    ]
    for name, pattern in CONFLICT_PATTERNS.items():
        lines.append(f"- **{name}:** `{pattern.pattern}`")
    lines.append("")
    return lines


def render_skip_touches(options: list[OptionRow]) -> list[str]:
    rows = [
        o
        for o in options
        if o.final_action == "skip" and _prompt_short(o.prompt_version) in ("v5", "v6")
    ]
    sources = sorted({o.source_label for o in rows})
    lines = [
        "### 2. What the skipped late candidates would have done",
        "",
        "Every built option on a model skip counts once, including counterfactual options. `not filled` means the shared entry limit was not reached inside its ten-minute life; `pending/unavailable` is outside the three decided touch columns. Average R uses target = the option's R/R, stop = −1R and neither = the 5-session price against the same entry geometry.",
        "",
        "| Source | Cycles | Options n | Scored n | Target first | Stop first | Neither after 5 sessions | Not filled | Pending/unavailable | Average R (90% range) |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for source in sources:
        predicates: list[tuple[str, Callable[[OptionRow], bool]]] = [
            ("14:46 and 15:46", lambda o: o.cycle_time in ("14:46", "15:46")),
            ("10:46–13:46", lambda o: o.cycle_time in ("10:46", "11:46", "12:46", "13:46")),
        ]
        for label, predicate in predicates:
            group = [o for o in rows if o.source_label == source and predicate(o)]
            target = sum(1 for o in group if o.touch == "target")
            stop = sum(1 for o in group if o.touch == "stop")
            neither = sum(1 for o in group if o.touch == "neither")
            not_filled = sum(1 for o in group if o.touch == "not_filled")
            pending = len(group) - target - stop - neither - not_filled
            rs = [o.r_made for o in group if o.r_made is not None and o.touch in DECIDED_TOUCHES]
            lines.append(
                f"| {source} | {label} | {_n_text(len(group))} | {_n_text(len(rs))} | {target} ({_pct(target, len(group))}) | "
                f"{stop} ({_pct(stop, len(group))}) | {neither} ({_pct(neither, len(group))}) | "
                f"{not_filled} | {pending} | {_r_cell(rs)} |"
            )
    lines.append("")
    return lines


def _exit_summary(rows: list[OvernightDecision]) -> tuple[list[float], list[float], list[float]]:
    open_rs = [
        r.exit_cf.next_open_r for r in rows if r.exit_cf and r.exit_cf.next_open_r is not None
    ]
    close_rs = [
        r.exit_cf.next_close_r for r in rows if r.exit_cf and r.exit_cf.next_close_r is not None
    ]
    five_rs = [
        r.exit_cf.five_session_r for r in rows if r.exit_cf and r.exit_cf.five_session_r is not None
    ]
    return open_rs, close_rs, five_rs


def render_held_exits(rows: list[OvernightDecision]) -> list[str]:
    sources = sorted({r.source_label for r in rows})
    lines = [
        "### 3. Held against exited in the last cycle",
        "",
        "The counterfactual for an exit keeps the position at the stop and target visible in that decision's snapshot. A gap through either fills at the bar open; a bar touching both counts as the stop. Horizon marks are the next session's open and close and the fifth later session's close, unless a stop or target touch came first. R uses the original entry risk (`entry − initial_stop`), not the tightened stop distance.",
        "",
        "| Source | Held | Exited | Exit→hold next open | Exit→hold next close | Exit→hold after 5 sessions | Held overnight gap | Open below stop |",
        "| --- | ---: | ---: | --- | --- | --- | --- | ---: |",
    ]
    if not sources:
        lines.append("| — | 0 too few | 0 too few | — | — | — | — | 0/0 (—) |")
    for source in sources:
        group = [r for r in rows if r.source_label == source]
        held = [r for r in group if r.action_group == "held"]
        exited = [r for r in group if r.action_group == "exited"]
        open_rs, close_rs, five_rs = _exit_summary(exited)
        gaps = [r.overnight_gap_r for r in held if r.overnight_gap_r is not None]
        below_flags = [r.open_below_stop for r in held if r.open_below_stop is not None]
        below = sum(1 for flag in below_flags if flag)
        lines.append(
            f"| {source} | {_n_text(len(held))} | {_n_text(len(exited))} | {_r_cell(open_rs)} | "
            f"{_r_cell(close_rs)} | {_r_cell(five_rs)} | {_r_cell(gaps)} | "
            f"{below}/{len(below_flags)} ({_pct(below, len(below_flags))}) |"
        )
    lines.append("")
    return lines


def render_overnight_splits(rows: list[OvernightDecision], key: str, title: str) -> list[str]:
    lines = [
        f"### 4. Split by {title}",
        "",
        "| Split | Source | Held | Exited | Exit→hold next open | Exit→hold next close | Exit→hold after 5 sessions | Held overnight gap | Open below stop |",
        "| --- | --- | ---: | ---: | --- | --- | --- | --- | ---: |",
    ]
    values = sorted({str(getattr(r, key)) for r in rows})
    sources = sorted({r.source_label for r in rows})
    if not rows:
        lines.append("| — | — | 0 too few | 0 too few | — | — | — | — | 0/0 (—) |")
    for value in values:
        for source in sources:
            group = [r for r in rows if r.source_label == source and str(getattr(r, key)) == value]
            if not group:
                continue
            held = [r for r in group if r.action_group == "held"]
            exited = [r for r in group if r.action_group == "exited"]
            open_rs, close_rs, five_rs = _exit_summary(exited)
            gaps = [r.overnight_gap_r for r in held if r.overnight_gap_r is not None]
            flags = [r.open_below_stop for r in held if r.open_below_stop is not None]
            below = sum(1 for flag in flags if flag)
            lines.append(
                f"| {value} | {source} | {_n_text(len(held))} | {_n_text(len(exited))} | "
                f"{_r_cell(open_rs)} | {_r_cell(close_rs)} | {_r_cell(five_rs)} | {_r_cell(gaps)} | "
                f"{below}/{len(flags)} ({_pct(below, len(flags))}) |"
            )
    lines.append("")
    return lines


def _percentile_tercile(value: Decimal | None) -> str | None:
    if value is None:
        return None
    v = float(value)
    if v < 100 / 3:
        return "lowest third"
    if v < 200 / 3:
        return "middle third"
    return "highest third"


def _vix_change_bucket(value: Decimal | None) -> str | None:
    if value is None:
        return None
    v = float(value)
    if v < -10:
        return "< −10%"
    if v > 10:
        return "> +10%"
    return "flat (−10% to +10%)"


def _sector_rank_bucket(value: object) -> str | None:
    try:
        rank = int(str(value))
    except (TypeError, ValueError):
        return None
    if rank <= 3:
        return "1–3"
    if rank <= 8:
        return "4–8"
    return "9–11"


def _sign_bucket(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return "> 0" if value > 0 else "< 0" if value < 0 else "0"


def _release_sessions_bucket(value: object) -> str | None:
    if value is None:
        return None
    try:
        sessions = int(str(value))
    except (TypeError, ValueError):
        return None
    if sessions <= 0:
        return "0"
    if sessions == 1:
        return "1"
    return "2+"


def _yes_no(value: object, yes: str = "yes", no: str = "no") -> str | None:
    if value is None:
        return None
    return yes if bool(value) else no


DIMENSIONS: list[tuple[str, str, Any]] = [
    (
        "vix_percentile",
        "VIX percentile",
        lambda dims: _percentile_tercile(dims.get("vix_percentile")),
    ),
    (
        "vix_change",
        "VIX 5-session change",
        lambda dims: _vix_change_bucket(dims.get("vix_change_5d_pct")),
    ),
    ("breadth", "Breadth above SMA50", lambda dims: _percentile_tercile(dims.get("breadth"))),
    ("sector_rank", "Sector rank", lambda dims: _sector_rank_bucket(dims.get("sector_rank"))),
    ("stock_vs_sector", "Stock vs sector", lambda dims: _sign_bucket(dims.get("stock_vs_sector"))),
    (
        "release_sessions",
        "Sessions to next release",
        lambda dims: _release_sessions_bucket(dims.get("release_sessions")),
    ),
    (
        "release_next_morning",
        "Release next morning",
        lambda dims: _yes_no(dims.get("release_next_morning")),
    ),
]


def _slice_rows(
    rows: Iterable[Any],
    *,
    r_getter: Any,
    win_getter: Any | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    material = list(rows)
    for key, label, bucket_fn in DIMENSIONS:
        grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for row in material:
            bucket = bucket_fn(row.dims)
            if bucket is None:
                continue
            grouped[(row.source_label, str(bucket))].append(row)
        for (source, bucket), group in sorted(grouped.items()):
            rs = [r_getter(row) for row in group]
            rs = [r for r in rs if r is not None]
            wins = [win_getter(row) for row in group] if win_getter else []
            out.append(
                {
                    "dimension": label,
                    "dimension_key": key,
                    "bucket": bucket,
                    "source": source,
                    "n": len(group),
                    "scored_n": len(rs),
                    "win_rate": _pct(sum(1 for win in wins if win), len(wins))
                    if win_getter
                    else "—",
                    "r_values": rs,
                }
            )
    return out


def render_trade_slices(trades: list[TradeRow]) -> list[str]:
    rows = _slice_rows(trades, r_getter=lambda row: row.r, win_getter=lambda row: row.win)
    lines = [
        "## Market-context slices",
        "",
        "### Closed trades",
        "",
        "A cell's n is the number of closed trades. Cells under 20 are shown but marked `too few`; no candidate rule below rests on one. The range is the bootstrap 90% range of average R (2,000 resamples, fixed seed 61061).",
        "",
        "| Source | Dimension | Bucket | n | Win rate | Average R (90% range) |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['source']} | {row['dimension']} | {row['bucket']} | {_n_text(row['n'])} | "
            f"{row['win_rate']} | {_r_cell(row['r_values'])} |"
        )
    if not rows:
        lines.append("| — | — | — | 0 too few | — | — |")
    lines.append("")
    return lines


def render_option_slices(options: list[OptionRow]) -> list[str]:
    decided = [row for row in options if row.touch in DECIDED_TOUCHES]
    rows = _slice_rows(decided, r_getter=lambda row: row.r_made)
    counts: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"target": 0, "stop": 0, "neither": 0}
    )
    for row in decided:
        for key, _label, bucket_fn in DIMENSIONS:
            bucket = bucket_fn(row.dims)
            if bucket is not None and row.touch in DECIDED_TOUCHES:
                counts[(row.source_label, key, str(bucket))][row.touch] += 1
    by_key = {(r["source"], r["dimension_key"], r["bucket"]): r for r in rows}
    lines = [
        "### Built-option touches",
        "",
        "Every built option counts once, whether it was offered, removed or built counterfactually. Only decided touches (`target`, `stop`, `neither`) enter this table; `not_filled`, pending and unavailable rows stay out.",
        "",
        "| Source | Dimension | Bucket | n | Target first | Stop first | Neither | Average R (90% range) |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    labels = {key: label for key, label, _fn in DIMENSIONS}
    for dimension_key in sorted(by_key):
        slice_row = by_key[dimension_key]
        c = counts[
            (str(slice_row["source"]), str(slice_row["dimension_key"]), str(slice_row["bucket"]))
        ]
        n = slice_row["n"]
        lines.append(
            f"| {slice_row['source']} | {labels[slice_row['dimension_key']]} | {slice_row['bucket']} | {_n_text(n)} | "
            f"{c['target']} ({_pct(c['target'], n)}) | {c['stop']} ({_pct(c['stop'], n)}) | "
            f"{c['neither']} ({_pct(c['neither'], n)}) | {_r_cell(slice_row['r_values'])} |"
        )
    if not rows:
        lines.append("| — | — | — | 0 too few | — | — | — | — |")
    lines.append("")
    return lines


def tercile_edges(values: Sequence[float]) -> tuple[float, float] | None:
    """1/3 and 2/3 cut points of a numeric sample, with linear interpolation."""
    ordered = sorted(values)
    if len(ordered) < 3:
        return None

    def quantile(q: float) -> float:
        pos = (len(ordered) - 1) * q
        lower = int(pos)
        upper = min(lower + 1, len(ordered) - 1)
        share = pos - lower
        return ordered[lower] * (1 - share) + ordered[upper] * share

    return quantile(1 / 3), quantile(2 / 3)


def sample_tercile(value: float, edges: tuple[float, float]) -> str:
    low, high = edges
    if value <= low:
        return "lowest third"
    if value <= high:
        return "middle third"
    return "highest third"


def _controlled_rows(options: list[OptionRow], outer: str, inner: str) -> list[dict[str, Any]]:
    decided = [row for row in options if row.touch in DECIDED_TOUCHES]
    rs_edges: dict[str, tuple[float, float]] = {}
    if outer == "rs_vs_spy_pct":
        by_source: dict[str, list[float]] = defaultdict(list)
        for row in decided:
            value = _dec(row.dims.get("rs_vs_spy_pct"))
            if value is not None:
                by_source[row.source_label].append(float(value))
        for source, values in by_source.items():
            edges = tercile_edges(values)
            if edges is not None:
                rs_edges[source] = edges

    grouped: dict[tuple[str, str, str], list[OptionRow]] = defaultdict(list)
    for row in decided:
        if outer == "rs_vs_spy_pct":
            value = _dec(row.dims.get(outer))
            edges = rs_edges.get(row.source_label)
            outer_bucket = (
                sample_tercile(float(value), edges)
                if value is not None and edges is not None
                else None
            )
        elif outer == "regime_trend":
            raw_outer = row.dims.get(outer)
            outer_bucket = str(raw_outer) if raw_outer is not None else None
        else:
            outer_bucket = _percentile_tercile(row.dims.get(outer))

        inner_value = row.dims.get(inner)
        if inner == "vix_percentile" or inner == "breadth":
            inner_bucket = _percentile_tercile(inner_value)
        elif inner == "sector_rank":
            inner_bucket = _sector_rank_bucket(inner_value)
        else:
            inner_bucket = str(inner_value) if inner_value is not None else None
        if outer_bucket is None or inner_bucket is None:
            continue
        grouped[(row.source_label, str(outer_bucket), str(inner_bucket))].append(row)
    out: list[dict[str, Any]] = []
    for (source, outer_bucket, inner_bucket), group in sorted(grouped.items()):
        rs = [row.r_made for row in group if row.r_made is not None]
        out.append(
            {
                "source": source,
                "outer": outer_bucket,
                "inner": inner_bucket,
                "n": len(group),
                "rs": rs,
                "target": sum(1 for row in group if row.touch == "target"),
                "stop": sum(1 for row in group if row.touch == "stop"),
                "neither": sum(1 for row in group if row.touch == "neither"),
            }
        )
    return out


def render_controlled(options: list[OptionRow]) -> list[str]:
    specs = [
        ("atr_percentile", "vix_percentile", "VIX within ATR-percentile thirds"),
        ("rs_vs_spy_pct", "sector_rank", "Sector rank within stock-vs-SPY strength"),
        ("regime_trend", "breadth", "Breadth within regime trend"),
    ]
    lines = [
        "### What each field adds",
        "",
        "These controlled slices test whether a new field still separates outcomes after a field the model already sees. Stock-vs-SPY thirds are computed within each source, so a strong/weak split is not hidden by one source's distribution.",
        "",
    ]
    for outer, inner, title in specs:
        lines += [
            f"#### {title}",
            "",
            "| Source | Outer | Inner | n | Target first | Stop first | Neither | Average R (90% range) |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for row in _controlled_rows(options, outer, inner):
            n = row["n"]
            lines.append(
                f"| {row['source']} | {row['outer']} | {row['inner']} | {_n_text(n)} | "
                f"{row['target']} ({_pct(row['target'], n)}) | {row['stop']} ({_pct(row['stop'], n)}) | "
                f"{row['neither']} ({_pct(row['neither'], n)}) | {_r_cell(row['rs'])} |"
            )
        lines.append("")
    return lines


def render_fomc_and_late_entries(
    decisions: list[DecisionRow], options: list[OptionRow], trades: list[TradeRow]
) -> list[str]:
    fomc_options = [o for o in options if (o.filter_name or "").lower() == "fomc_window"]
    lines = [
        "### FOMC window and 15:46 entries",
        "",
        "| Measurement | n | Target first | Stop first | Neither | Average R (90% range) |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    rs = [o.r_made for o in fomc_options if o.r_made is not None and o.touch in DECIDED_TOUCHES]
    n = len(fomc_options)
    lines.append(
        f"| Candidates removed by `fomc_window` | {_n_text(n)} | "
        f"{sum(1 for o in fomc_options if o.touch == 'target')} | "
        f"{sum(1 for o in fomc_options if o.touch == 'stop')} | "
        f"{sum(1 for o in fomc_options if o.touch == 'neither')} | {_r_cell(rs)} |"
    )
    lines += [
        "",
        "15:46 entries since item 071 are compared with earlier-cycle entries. The entry count is decision rows; the result rows are the closed trades those entries produced.",
        "",
        "| Source | Cycle group | Entries | Closed trades | Win rate | Average R (90% range) |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    entry_rows = [
        d
        for d in decisions
        if d.context == "candidate"
        and d.final_action == "enter"
        and d.scheduled >= ITEM071_MERGED_AT
    ]
    for source in sorted({d.source_label for d in entry_rows}):
        for label, late in (("15:46", True), ("10:46–14:46", False)):
            entries = [
                d
                for d in entry_rows
                if d.source_label == source and (d.cycle_time == "15:46") == late
            ]
            ids = {d.id for d in entries}
            closed = [t for t in trades if t.decision_id in ids and t.source_label == source]
            wins = sum(1 for t in closed if t.win)
            lines.append(
                f"| {source} | {label} | {_n_text(len(entries))} | {_n_text(len(closed))} | "
                f"{_pct(wins, len(closed))} | {_r_cell([t.r for t in closed])} |"
            )
    lines.append("")
    return lines


def _avg(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt_avg(values: Sequence[float]) -> str:
    cell = bootstrap_mean_range(values)
    if cell is None:
        return "no scored rows"
    avg, lo, hi = cell
    return f"{avg:+.2f}R [{lo:+.2f}, {hi:+.2f}], n {len(values)}"


def render_holding_rules(rows: list[OvernightDecision], options: list[OptionRow]) -> list[str]:
    """Item-113 candidate rules, written in the prompt's own terms."""
    late = [
        o.r_made
        for o in options
        if o.final_action == "skip"
        and o.cycle_time in ("14:46", "15:46")
        and o.touch in DECIDED_TOUCHES
        and o.r_made is not None
        and _prompt_short(o.prompt_version) in ("v5", "v6")
    ]
    early = [
        o.r_made
        for o in options
        if o.final_action == "skip"
        and o.cycle_time in ("10:46", "11:46", "12:46", "13:46")
        and o.touch in DECIDED_TOUCHES
        and o.r_made is not None
        and _prompt_short(o.prompt_version) in ("v5", "v6")
    ]
    exits = [r for r in rows if r.action_group == "exited" and r.exit_cf is not None]
    five_all = [
        r.exit_cf.five_session_r
        for r in exits
        if r.exit_cf and r.exit_cf.five_session_r is not None
    ]
    inside = [r for r in exits if r.stop_distance_group == "stop inside usual gap"]
    five_inside = [
        r.exit_cf.five_session_r
        for r in inside
        if r.exit_cf and r.exit_cf.five_session_r is not None
    ]
    held = [r for r in rows if r.action_group == "held"]
    gaps_inside = [
        r.overnight_gap_r
        for r in held
        if r.stop_distance_group == "stop inside usual gap" and r.overnight_gap_r is not None
    ]
    flags_inside = [
        r.open_below_stop
        for r in held
        if r.stop_distance_group == "stop inside usual gap" and r.open_below_stop is not None
    ]
    below = sum(1 for flag in flags_inside if flag)

    lines = [
        "### 5. Candidate rules for decision v7's holding period",
        "",
        "These are evidence statements for Marc's choice, not rules already chosen. A statement is marked provisional when its cells are under 20.",
        "",
    ]

    enough_late = len(late) >= TOO_FEW and len(early) >= TOO_FEW
    late_avg = _avg(late) or 0
    early_avg = _avg(early) or 0
    late_minus_early = late_avg - early_avg
    if enough_late and late_minus_early <= -0.15:
        time_rule = (
            "With less than 120 minutes left, a target more than 2 ATR away is an overnight hold; "
            "weigh that as a conflict."
        )
        time_verdict = "supported for keeping the conflict"
    else:
        time_rule = (
            "The time left in today's session is no reason to skip an entry or to exit a position. "
            "A target today's session can't reach is normal inside `constraints.hold_sessions`."
        )
        if not enough_late:
            time_verdict = "provisional — too few scored options"
        elif late_minus_early >= 0.15:
            time_verdict = "supported for removal"
        else:
            time_verdict = "inconclusive — late/early gap under 0.15R"
    lines += [
        f"1. **Time-left conflict — {time_verdict}.** Prompt wording: “{time_rule}” "
        f"Numbers: skipped 14:46/15:46 options {_fmt_avg(late)}; earlier cycles {_fmt_avg(early)}. Negative skipped-option R means the skips avoided losses; the late/early comparison tests whether lateness itself separated worse candidates.",
    ]

    enough_exits = len(five_all) >= TOO_FEW
    five_avg = _avg(five_all) or 0
    if enough_exits and five_avg < -0.10:
        overnight_rule = (
            "In the last cycle, when `position.stop_distance_atr` is below `overnight.gap_p90_atr`, "
            "hold only with confidence at least `neutral_probability` + 0.10."
        )
        overnight_verdict = "supported"
    else:
        overnight_rule = (
            "A stop inside the usual overnight gap is the known cost of holding for days, not a reason "
            "to exit by itself; list it as a conflict only with a concrete reason the next gap could be larger."
        )
        if not enough_exits:
            overnight_verdict = "provisional — too few exits"
        elif five_avg > 0.10:
            overnight_verdict = "supported"
        else:
            overnight_verdict = "inconclusive — exit counterfactual near zero"
    lines.append(
        f"2. **Overnight line — {overnight_verdict}.** Prompt wording: “{overnight_rule}” "
        f"Numbers: what exits would have made after 5 sessions {_fmt_avg(five_all)}; "
        f"stop-inside-gap exits {_fmt_avg(five_inside)}; held positions with the stop inside the usual gap "
        f"had overnight gaps {_fmt_avg(gaps_inside)} and opened below the stop in "
        f"{below}/{len(flags_inside)} ({_pct(below, len(flags_inside))})."
    )

    five_note = (
        "The 5-session horizon is measurable in the counterfactual exits above. "
        "The data does not test 4 against 5 or 5 against 7 directly, so it cannot choose the number by itself; "
        "it can only test whether trades still have room after several sessions."
    )
    if len(five_all) < TOO_FEW:
        five_support = "provisional — too few exits"
    elif (_avg(five_all) or 0) > 0:
        five_support = "consistent with a multi-day hold"
    else:
        five_support = "not supported by the exits measured"
    lines.append(
        f"3. **`hold_sessions` 5 — {five_support}.** Prompt wording: “A trade is meant to reach its target "
        f"within `constraints.hold_sessions` sessions. It is held overnight as often as needed in that time; "
        f"nothing sells only because the time is up.” {five_note} Numbers: exit counterfactual after 5 sessions "
        f"{_fmt_avg(five_all)}."
    )
    lines.append("")
    return lines


def render_market_candidate_rules(trades: list[TradeRow], options: list[OptionRow]) -> list[str]:
    """At most two evidence-backed market-context rules per field.

    The evidence bar is deliberately strict: at least 20 decided option rows in
    the adverse bucket and in the comparison set, and an average at least
    0.15R worse.  This is a screen for candidates, not statistical proof.
    """
    decided = [row for row in options if row.touch in DECIDED_TOUCHES and row.r_made is not None]
    lines = [
        "## Candidate rules and fields without support",
        "",
        "The screen uses decided built-option touches because they have far more rows than closed trades. A bucket becomes a candidate only with at least 20 rows in both sides and a gap of at least 0.15R; bootstrap intervals are shown in the tables above. These are candidates for Marc, not rules chosen by the script.",
        "",
    ]
    wording = {
        "vix_percentile": (
            "highest third",
            "No new entry when the VIX percentile is in its highest third",
        ),
        "vix_change": ("> +10%", "No new entry when the VIX rose more than 10% in 5 sessions"),
        "breadth": ("lowest third", "No new entry when breadth above SMA50 is in its lowest third"),
        "sector_rank": (
            "9–11",
            "Prefer candidates in sectors ranked 1–8; treat rank 9–11 as a conflict",
        ),
        "stock_vs_sector": ("< 0", "Treat a stock weaker than its sector as a conflict"),
        "release_sessions": ("0", "No new entry on a release day"),
        "release_next_morning": ("yes", "Treat an 08:30 release the next morning as a conflict"),
    }
    unsupported: list[str] = []
    candidate_count = 0
    for key, label, bucket_fn in DIMENSIONS:
        adverse, sentence = wording[key]
        bad: list[float] = []
        good: list[float] = []
        for row in decided:
            bucket = bucket_fn(row.dims)
            if bucket is None or row.r_made is None:
                continue
            if str(bucket) == adverse:
                bad.append(float(row.r_made))
            else:
                good.append(float(row.r_made))
        if (
            len(bad) >= TOO_FEW
            and len(good) >= TOO_FEW
            and (_avg(bad) or 0) < (_avg(good) or 0) - 0.15
            and candidate_count < 2
        ):
            candidate_count += 1
            lines.append(
                f"- **{label}:** “{sentence}.” Numbers: adverse bucket {_fmt_avg(bad)}; "
                f"the other buckets together {_fmt_avg(good)}."
            )
        else:
            unsupported.append(label)
    if candidate_count == 0:
        lines.append("- No market-context field met the evidence screen for a candidate rule.")
    lines += [
        "",
        "Fields without enough separating evidence at this refresh: "
        + (", ".join(unsupported) if unsupported else "none")
        + ".",
        "",
    ]
    return lines


def render_holding_overnight(
    decisions: list[DecisionRow], options: list[OptionRow], overnight_rows: list[OvernightDecision]
) -> list[str]:
    lines = [
        "## Holding overnight",
        "",
        "This section is the item-113 measurement set added to step A. It covers decision v5 and v6 rows only, because those are the prompt versions whose time-left and overnight rules are being measured. Live and shadow rows stay apart. This database window has no v5/v6 shadow rows: the active shadows are decision.v2 and rules-only, so they remain outside this section rather than being treated as v5/v6 evidence.",
        "",
    ]
    lines += render_entries_skips(decisions)
    lines += render_skip_touches(options)
    lines += render_held_exits(overnight_rows)
    lines += render_overnight_splits(
        overnight_rows, "stop_distance_group", "the stop against the usual overnight gap"
    )
    lines += render_overnight_splits(
        overnight_rows, "morning_event_group", "earnings or an 08:30 release the next morning"
    )
    lines += render_holding_rules(overnight_rows, options)
    return lines


def _relabel_backtest(dataset: Dataset, strategy_name: str) -> Dataset:
    label = f"backtest {strategy_name}"
    for row in dataset.decisions:
        row.source_kind = "backtest"
        row.source_label = label
    for trade in dataset.trades:
        trade.source_kind = "backtest"
        trade.source_label = label
    for option_row in dataset.options:
        option_row.source_kind = "backtest"
        option_row.source_label = label
    dataset.name = label
    return dataset


def _resolve_run_dir(raw: str, live_db: Path) -> Path | None:
    path = Path(raw)
    candidates = [path]
    if not path.is_absolute():
        candidates.extend(
            [
                live_db.parent.parent / path,  # repository-root-relative data/backtests/run-n
                live_db.parent / path,  # data-relative backtests/run-n
            ]
        )
    return next((candidate for candidate in candidates if candidate.exists()), None)


def extract_backtests(live_conn: sqlite3.Connection, live_db: Path) -> tuple[list[Dataset], str]:
    """Extract the newest completed backtest of at least 12 months, if present.

    The summary row lives in the copied live database; the strategy databases
    stay on disk under the run directory and are opened read-only.  The live
    copy is never written back to.
    """
    if not table_exists(live_conn, "backtests"):
        return [], "the live database has no `backtests` table"
    rows = live_conn.execute(
        """
        SELECT id, name, start_date, end_date, strategies, run_dir, status
        FROM backtests
        WHERE status = 'done'
        ORDER BY id DESC
        """
    ).fetchall()
    chosen: sqlite3.Row | None = None
    for row in rows:
        start = _date(row["start_date"])
        end = _date(row["end_date"])
        if start is not None and end is not None and (end - start).days >= 365:
            chosen = row
            break
    if chosen is None:
        return [], "no completed backtest of at least 12 months is recorded"
    run_dir = _resolve_run_dir(str(chosen["run_dir"]), live_db)
    if run_dir is None:
        return [], f"run directory for backtest {chosen['id']} is missing: {chosen['run_dir']}"
    strategies = _list(chosen["strategies"])
    datasets: list[Dataset] = []
    missing: list[str] = []
    start = _date(chosen["start_date"]) or DEFAULT_SINCE
    since = datetime.combine(start, time.min, UTC)
    for spec in strategies:
        if not isinstance(spec, dict):
            continue
        slug = str(spec.get("slug") or spec.get("ref") or spec.get("name") or "strategy")
        name = str(spec.get("name") or slug)
        db_path = run_dir / f"{slug}.db"
        if not db_path.exists():
            missing.append(slug)
            continue
        conn = connect_readonly(db_path)
        try:
            decisions = extract_live_decisions(conn, since)
            dataset = Dataset(
                name=name,
                decisions=decisions,
                trades=extract_live_trades(conn, decisions),
                options=extract_option_rows(decisions),
            )
            datasets.append(_relabel_backtest(dataset, name))
        finally:
            conn.close()
    if not datasets:
        detail = f"backtest {chosen['id']} has no readable strategy databases"
        if missing:
            detail += " (missing: " + ", ".join(missing) + ")"
        return [], detail
    note = f"backtest {chosen['id']} “{chosen['name']}” ({chosen['start_date']} to {chosen['end_date']})"
    if missing:
        note += "; missing strategy databases: " + ", ".join(missing)
    return datasets, note


def _session_count(decisions: Iterable[DecisionRow]) -> int:
    return len({row.session_day for row in decisions})


def _source_counts(dataset: Dataset) -> str:
    labels = sorted({row.source_label for row in dataset.decisions})
    return ", ".join(labels) if labels else "none"


def render_report(
    *,
    live_dataset: Dataset,
    backtest_datasets: list[Dataset],
    backtest_note: str,
    since: datetime,
    generated_at: datetime,
    database_note: str,
    overnight_rows: list[OvernightDecision],
) -> str:
    all_decisions = list(live_dataset.decisions)
    all_trades = list(live_dataset.trades)
    all_options = list(live_dataset.options)
    for dataset in backtest_datasets:
        all_decisions += dataset.decisions
        all_trades += dataset.trades
        all_options += dataset.options

    sessions = _session_count(live_dataset.decisions)
    lines: list[str] = [
        "# 061 · Market context for decision v8",
        "",
        f"Generated by `make analysis-061` on {generated_at.date().isoformat()} from a read-only SQLite copy. {database_note}",
        "",
        "## Data and start condition",
        "",
        f"- Window start: {since.date().isoformat()} (the first data session after items 060, 067 and 068 were live).",
        f"- Live/shadow sessions in this copy: **{sessions}**. The kickoff asks for at least 20; until then every conclusion is preliminary.",
        f"- Sources present: {_source_counts(live_dataset)}.",
        f"- Backtest: {backtest_note}.",
        "- Method: cells show n, average R and the bootstrap 90% range of average R (2,000 resamples, fixed seed 61061). Cells under 20 are marked `too few`.",
        "- Option-touch R: target = the option's reward/risk, stop = −1R, not filled = excluded from decided-touch tables, neither = the 5-session price against the same entry geometry.",
        "",
    ]
    if sessions < 20:
        lines += [
            "**Start condition not yet met.** This refresh is useful for checking the measurements and their wording, but no field or rule should be chosen from it.",
            "",
        ]
    lines += render_holding_overnight(
        [d for d in all_decisions if d.source_kind in ("live", "shadow")],
        [o for o in all_options if o.source_kind in ("live", "shadow")],
        overnight_rows,
    )
    lines += render_trade_slices(all_trades)
    lines += render_option_slices(all_options)
    lines += render_controlled(all_options)
    lines += render_fomc_and_late_entries(all_decisions, all_options, all_trades)
    lines += render_market_candidate_rules(all_trades, all_options)
    lines += [
        "## Method notes and limits",
        "",
        "- Live and shadow rows are never combined. Backtest strategies, when a qualifying run exists, are separate rows in the same tables.",
        "- The item-113 overnight section uses live and shadow rows only: backtest bars live in the separate historical bar store, and the kickoff asks this measurement specifically for the v5/v6 behaviour seen in operation.",
        "- Counterfactual exits freeze the stop and target visible at the last-cycle decision. Later tightening is not replayed, so the measurement answers the prompt decision itself rather than simulating every later model call.",
        "- `neither` in option touches means neither stop nor target was touched inside the five-session measurement window.",
        "- Null or unavailable market-context fields are excluded from that slice rather than treated as calm or neutral.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def price_sessions_after(
    conn: sqlite3.Connection, symbol: str, decision_time: datetime, sessions: int = 5
) -> Decimal | None:
    """The last closed 5m bar price the same NY time ``sessions`` later.

    This mirrors outcome tracking closely enough for counterfactual shadow
    rows.  Session dates come from the symbol's stored daily bars, so no
    network or market calendar service is needed.
    """
    daily = load_daily(conn, symbol)
    decision_day = _ny_day(decision_time)
    later = [bar.day for bar in daily if bar.day > decision_day]
    if len(later) < sessions:
        return None
    target_day = later[sessions - 1]
    decision_ny = decision_time.astimezone(NY_TZ)
    target_ny = datetime.combine(target_day, decision_ny.timetz().replace(tzinfo=None), NY_TZ)
    target_utc = target_ny.astimezone(UTC)
    rows = conn.execute(
        """
        SELECT start, close FROM bars_5m
        WHERE symbol = ? AND start < ? AND start >= ?
        ORDER BY start DESC
        LIMIT 30
        """,
        (
            symbol,
            target_utc.replace(tzinfo=None).isoformat(sep=" "),
            (target_utc - timedelta(hours=2)).replace(tzinfo=None).isoformat(sep=" "),
        ),
    ).fetchall()
    for row in rows:
        start = _dt(row["start"])
        close = _dec(row["close"])
        if start is not None and close is not None and start + timedelta(minutes=5) <= target_utc:
            return close
    return None


def enrich_shadow_neither_r(
    conn: sqlite3.Connection, decisions: list[DecisionRow], options: list[OptionRow]
) -> None:
    """Fill shadow `neither` R values missing from ``shadow_decisions``.

    The shadow table stores the touch but not the 5-session price outcome.
    The live outcome table has that price; shadows therefore need this small
    local-bar lookup for the same counterfactual R definition.
    """
    by_id = {d.id: d for d in decisions}
    price_cache: dict[tuple[str, datetime], Decimal | None] = {}
    for row in options:
        if row.source_kind != "shadow" or row.touch != "neither" or row.r_made is not None:
            continue
        decision = by_id.get(row.decision_id)
        if decision is None:
            continue
        column = decision.options_column
        raw_entries = column.get("options")
        raw_removed = column.get("removed")
        entries: list[Any] = raw_entries if isinstance(raw_entries, list) else []
        removed: list[Any] = raw_removed if isinstance(raw_removed, list) else []
        option = next(
            (
                item
                for item in entries + removed
                if isinstance(item, dict)
                and str(item.get("id") or item.get("option_id")) == row.option_id
            ),
            None,
        )
        if option is None:
            continue
        key = (row.symbol, decision.scheduled)
        if key not in price_cache:
            price_cache[key] = price_sessions_after(conn, row.symbol, decision.scheduled)
        price5 = price_cache[key]
        constraints = decision.input_snapshot.get("constraints")
        shared_limit = (
            _dec(constraints.get("entry_limit")) if isinstance(constraints, dict) else None
        )
        made = _r_made("neither", option, price5, shared_limit)
        row.r_made = _float(made)


def build_report(
    *,
    live_db: Path,
    out_path: Path,
    since_day: date,
    generated_at: datetime | None = None,
) -> str:
    if not live_db.exists():
        raise FileNotFoundError(f"live database not found: {live_db}")
    since = datetime.combine(since_day, time.min, UTC)
    generated = generated_at or datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="analysis-061-") as tmp:
        copy_path = Path(tmp) / "live-copy.db"
        copy_database_readonly(live_db, copy_path)
        conn = connect_readonly(copy_path)
        try:
            live_dataset = extract_dataset(conn, since, name="live/shadows")
            enrich_shadow_neither_r(conn, live_dataset.decisions, live_dataset.options)
            overnight_rows = compute_overnight_rows(conn, live_dataset.decisions)
            backtests, backtest_note = extract_backtests(conn, live_db)
            report = render_report(
                live_dataset=live_dataset,
                backtest_datasets=backtests,
                backtest_note=backtest_note,
                since=since,
                generated_at=generated,
                database_note=(
                    f"Source `{live_db}` was opened read-only only long enough to make "
                    "a SQLite backup copy; every live/shadow query ran against that copy."
                ),
                overnight_rows=overnight_rows,
            )
        finally:
            conn.close()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    return report


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def main(argv: Sequence[str] | None = None) -> int:
    root = repo_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=root / "data" / "stock-agent.db")
    parser.add_argument(
        "--out", type=Path, default=root / "docs" / "analysis" / "061-market-context.md"
    )
    parser.add_argument("--since", type=date.fromisoformat, default=DEFAULT_SINCE)
    args = parser.parse_args(argv)
    build_report(live_db=args.database, out_path=args.out, since_day=args.since)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
