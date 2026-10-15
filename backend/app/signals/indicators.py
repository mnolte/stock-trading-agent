"""Indicator math on Decimal series. Pure functions, no I/O.

Conventions (the spec names the indicators, not the formula details):
- EMA is seeded with the SMA of the first ``period`` values and then smoothed
  with k = 2 / (period + 1) — the classic charting convention.
- RSI and ATR use Wilder smoothing.
- OBV trend is the sign of the OBV change across the lookback window.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol


class Ohlcv(Protocol):
    """Structural type shared by Bar5m, Bar1h and DailyBar."""

    @property
    def high(self) -> Decimal: ...

    @property
    def low(self) -> Decimal: ...

    @property
    def close(self) -> Decimal: ...

    @property
    def volume(self) -> int: ...


def sma(values: Sequence[Decimal], period: int) -> Decimal | None:
    """Simple moving average of the last ``period`` values."""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / Decimal(period)


def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """EMA series aligned to the input; None until the SMA seed at index period-1."""
    result: list[Decimal | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return result
    k = Decimal(2) / Decimal(period + 1)
    prev = sum(values[:period]) / Decimal(period)
    result[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (Decimal(1) - k)
        result[i] = prev
    return result


def ema(values: Sequence[Decimal], period: int) -> Decimal | None:
    """Latest EMA value, or None when there are fewer than ``period`` values."""
    if not values:
        return None
    return ema_series(values, period)[-1]


def rsi(closes: Sequence[Decimal], period: int) -> Decimal | None:
    """Wilder's RSI of the close series; None when len < period + 1."""
    if period <= 0 or len(closes) < period + 1:
        return None
    avg_gain = Decimal(0)
    avg_loss = Decimal(0)
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        avg_gain += max(change, Decimal(0))
        avg_loss += max(-change, Decimal(0))
    avg_gain /= Decimal(period)
    avg_loss /= Decimal(period)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, Decimal(0))) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + max(-change, Decimal(0))) / Decimal(period)
    if avg_loss == 0:
        return Decimal(100)
    rs = avg_gain / avg_loss
    return Decimal(100) - Decimal(100) / (Decimal(1) + rs)


def true_ranges(bars: Sequence[Ohlcv]) -> list[Decimal]:
    """True range per bar; the first bar has no previous close so it is skipped."""
    trs: list[Decimal] = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return trs


def atr_series(bars: Sequence[Ohlcv], period: int) -> list[Decimal]:
    """Wilder's ATR series, one value per bar from index ``period`` onward."""
    trs = true_ranges(bars)
    if period <= 0 or len(trs) < period:
        return []
    values = [sum(trs[:period]) / Decimal(period)]
    for tr in trs[period:]:
        values.append((values[-1] * (period - 1) + tr) / Decimal(period))
    return values


def atr(bars: Sequence[Ohlcv], period: int) -> Decimal | None:
    """Latest Wilder ATR, or None when there are fewer than period + 1 bars."""
    values = atr_series(bars, period)
    return values[-1] if values else None


def session_vwap(bars: Sequence[Ohlcv]) -> Decimal | None:
    """Session VWAP over the given bars (typical price x volume / volume)."""
    total_volume = sum(b.volume for b in bars)
    if not bars or total_volume == 0:
        return None
    typical_volume = sum(
        (b.high + b.low + b.close) / Decimal(3) * b.volume for b in bars
    )
    return typical_volume / Decimal(total_volume)


def obv_trend(bars: Sequence[Ohlcv], lookback: int) -> str | None:
    """OBV direction over the last ``lookback`` bar-to-bar moves: up/down/flat."""
    if lookback <= 0 or len(bars) < lookback + 1:
        return None
    obv = Decimal(0)
    first: Decimal | None = None
    for i in range(len(bars) - lookback, len(bars)):
        change = bars[i].close - bars[i - 1].close
        if change > 0:
            obv += bars[i].volume
        elif change < 0:
            obv -= bars[i].volume
        if first is None:
            first = obv
    delta = obv - (first or Decimal(0))
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


def relative_strength_pct(
    symbol_closes: Sequence[Decimal],
    benchmark_closes: Sequence[Decimal],
    lookback: int,
) -> Decimal | None:
    """``lookback``-bar return of the symbol minus the benchmark's, in percent."""
    if (
        lookback <= 0
        or len(symbol_closes) < lookback + 1
        or len(benchmark_closes) < lookback + 1
        or symbol_closes[-1 - lookback] == 0
        or benchmark_closes[-1 - lookback] == 0
    ):
        return None
    sym_ret = (
        symbol_closes[-1] - symbol_closes[-1 - lookback]
    ) / symbol_closes[-1 - lookback]
    spy_ret = (
        benchmark_closes[-1] - benchmark_closes[-1 - lookback]
    ) / benchmark_closes[-1 - lookback]
    return (sym_ret - spy_ret) * Decimal(100)


def percentile_rank(history: Sequence[Decimal], current: Decimal) -> Decimal | None:
    """Share of history values <= current, as a percentage (0-100)."""
    if not history:
        return None
    below = sum(1 for v in history if v <= current)
    return Decimal(below) / Decimal(len(history)) * Decimal(100)


def opening_gap_pct(today_open: Decimal, previous_close: Decimal) -> Decimal | None:
    """Today's opening gap vs the previous session's close, in percent."""
    if previous_close == 0:
        return None
    return (today_open - previous_close) / previous_close * Decimal(100)
