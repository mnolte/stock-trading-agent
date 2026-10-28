"""Deterministic rules pre-filter (docs/SPEC.md, Decision layer).

A symbol is an entry candidate only if every check passes. Held positions are
not candidates: they are reviewed as positions every cycle. Every threshold
comes from config; nothing is hard-coded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.signals.regime import MarketRegime
from app.signals.snapshot import IndicatorSnapshot

if TYPE_CHECKING:
    from app.config import AppConfig

STATUS_PASS = "pass"  # noqa: S105 - a status literal, not a password
STATUS_FAIL = "fail"
STATUS_HELD = "held"


@dataclass(frozen=True)
class RuleCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class RulesResult:
    symbol: str
    status: str  # pass | fail | held
    reason: str | None  # detail of the first failing check
    checks: list[RuleCheck]


def _fmt(value: Decimal | None, digits: int = 0) -> str:
    """Compact number for a check detail, e.g. RSI 71."""
    if value is None:
        return "—"
    if digits == 0:
        return f"{value:.0f}"
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def evaluate_entry_rules(
    symbol: str,
    *,
    snapshot: IndicatorSnapshot | None,
    regime: MarketRegime,
    held: bool,
    open_entry_order: bool,
    is_last_cycle: bool,
    config: AppConfig,
    cycle_time: str | None = None,
) -> RulesResult:
    rsi_min = config.get("rules", "rsi_min", default=40)
    rsi_max = config.get("rules", "rsi_max", default=70)
    require_above_sma = bool(
        config.get("rules", "require_daily_above_sma50", default=True)
    )
    require_ema = bool(config.get("rules", "require_ema20_above_ema50", default=True))
    blackout_days = int(config.get("risk", "earnings_blackout_days", default=3))
    block_last_cycle = bool(
        config.get("schedule", "last_cycle_no_entries", default=True)
    )

    checks: list[RuleCheck] = []

    if held:
        checks.append(
            RuleCheck("not held", passed=False, detail="Held · reviewed each cycle")
        )
        return RulesResult(symbol, STATUS_HELD, "Held · reviewed each cycle", checks)
    checks.append(
        RuleCheck(
            "no open order",
            passed=not open_entry_order,
            detail="entry order already open" if open_entry_order else "no open order",
        )
    )

    if require_above_sma:
        ok = (
            snapshot is not None
            and snapshot.daily_close is not None
            and snapshot.daily_sma_fast is not None
            and snapshot.daily_close > snapshot.daily_sma_fast
        )
        detail = (
            f"close {_fmt(snapshot.daily_close)} > SMA {_fmt(snapshot.daily_sma_fast)}"
            if ok and snapshot is not None
            else "close not above SMA 50"
        )
        if snapshot is not None and (
            snapshot.daily_close is None or snapshot.daily_sma_fast is None
        ):
            detail = "daily data unavailable"
        checks.append(RuleCheck("above SMA 50", passed=ok, detail=detail))

    if require_ema:
        ok = (
            snapshot is not None
            and snapshot.ema_fast is not None
            and snapshot.ema_slow is not None
            and snapshot.ema_fast > snapshot.ema_slow
        )
        detail = (
            f"EMA {_fmt(snapshot.ema_fast, 2)} > {_fmt(snapshot.ema_slow, 2)}"
            if ok and snapshot is not None
            else "EMA 20 not above EMA 50"
        )
        if snapshot is not None and (
            snapshot.ema_fast is None or snapshot.ema_slow is None
        ):
            detail = "1h data unavailable"
        checks.append(RuleCheck("EMA 20 > EMA 50", passed=ok, detail=detail))

    rsi_value = snapshot.rsi if snapshot else None
    rsi_ok = rsi_value is not None and rsi_min <= rsi_value <= rsi_max
    if rsi_value is None:
        rsi_detail = "RSI unavailable"
    elif rsi_ok:
        rsi_detail = f"RSI {_fmt(rsi_value)} in {rsi_min}\u2013{rsi_max}"
    elif rsi_value < rsi_min:
        rsi_detail = f"RSI {_fmt(rsi_value)} < {rsi_min}"
    else:
        rsi_detail = f"RSI {_fmt(rsi_value)} > {rsi_max}"
    checks.append(RuleCheck("RSI range", passed=rsi_ok, detail=rsi_detail))

    earnings_ok = True
    if snapshot is None or not snapshot.earnings_available:
        earnings_ok = False
        earnings_detail = "earnings data unavailable"
    elif snapshot.days_to_earnings is None:
        earnings_detail = "no earnings scheduled"
    elif snapshot.days_to_earnings <= blackout_days:
        earnings_ok = False
        n = snapshot.days_to_earnings
        earnings_detail = "earnings today" if n == 0 else f"earnings in {n}d"
    else:
        n = snapshot.days_to_earnings
        earnings_detail = f"earnings in {n}d"
    checks.append(
        RuleCheck("earnings blackout", passed=earnings_ok, detail=earnings_detail)
    )

    checks.append(
        RuleCheck(
            "regime allows entries",
            passed=not regime.blocks_entries,
            detail=(
                f"{regime.label} blocks entries"
                if regime.blocks_entries
                else regime.label
            ),
        )
    )

    if block_last_cycle:
        checks.append(
            RuleCheck(
                "not last cycle",
                passed=not is_last_cycle,
                detail="last cycle: no new entries" if is_last_cycle else "entries allowed",
            )
        )

    # Strategy override (PR 22): entry_cycles limits the cycles a
    # strategy may enter in; absent means every cycle is allowed.
    allowed_cycles = config.get("rules", "entry_cycles")
    if isinstance(allowed_cycles, list) and allowed_cycles and cycle_time is not None:
        checks.append(
            RuleCheck(
                "entry cycle",
                passed=cycle_time in allowed_cycles,
                detail=(
                    "entries allowed"
                    if cycle_time in allowed_cycles
                    else "no entries in this cycle (strategy)"
                ),
            )
        )

    first_fail = next((c for c in checks if not c.passed), None)
    if first_fail is None:
        return RulesResult(symbol, STATUS_PASS, None, checks)
    return RulesResult(symbol, STATUS_FAIL, first_fail.detail, checks)
