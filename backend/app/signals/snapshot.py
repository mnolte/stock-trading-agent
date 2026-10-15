"""Indicator snapshot: the per-symbol input the rules filter and the model use.

Daily indicators use completed sessions only; the still-running session never
counts as a "daily close". 1h indicators use closed session-aligned bars only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.data.models import Bar1h, Bar5m, DailyBar
from app.signals.indicators import (
    atr,
    atr_series,
    ema,
    obv_trend,
    opening_gap_pct,
    percentile_rank,
    relative_strength_pct,
    rsi,
    session_vwap,
    sma,
)

if TYPE_CHECKING:
    from app.config import AppConfig


@dataclass(frozen=True)
class IndicatorSnapshot:
    symbol: str
    # 1h values on session-aligned bars
    price: Decimal | None  # close of the last closed 1h bar
    ema_fast: Decimal | None
    ema_slow: Decimal | None
    rsi: Decimal | None
    atr_1h: Decimal | None
    session_vwap: Decimal | None  # today's session, from 5m bars
    obv_trend: str | None  # up | down | flat
    rs_vs_spy_pct: Decimal | None
    # daily values on completed sessions
    daily_close: Decimal | None
    daily_sma_fast: Decimal | None
    daily_sma_slow: Decimal | None
    daily_atr_percentile: Decimal | None
    opening_gap_pct: Decimal | None
    # earnings
    days_to_earnings: int | None  # trading days until the next event
    earnings_available: bool  # False when the earnings source failed


def compute_snapshot(
    symbol: str,
    *,
    bars_1h: list[Bar1h],
    daily: list[DailyBar],
    today_5m: list[Bar5m],
    spy_1h: list[Bar1h],
    days_to_earnings: int | None,
    earnings_available: bool,
    config: AppConfig,
) -> IndicatorSnapshot:
    ema_fast_p = int(config.get("indicators", "ema_fast", default=20))
    ema_slow_p = int(config.get("indicators", "ema_slow", default=50))
    rsi_p = int(config.get("indicators", "rsi_period", default=14))
    atr_p = int(config.get("indicators", "atr_period", default=14))
    obv_p = int(config.get("indicators", "obv_lookback", default=20))
    rs_p = int(config.get("indicators", "rs_lookback", default=20))
    sma_fast_p = int(config.get("indicators", "daily_sma_fast", default=50))
    sma_slow_p = int(config.get("indicators", "daily_sma_slow", default=200))
    percentile_sessions = int(
        config.get("indicators", "atr_percentile_sessions", default=60)
    )

    closes_1h = [b.close for b in bars_1h]
    spy_closes_1h = [b.close for b in spy_1h]
    daily_closes = [b.close for b in daily]

    daily_atr = atr_series(daily, atr_p)
    atr_window = daily_atr[-percentile_sessions:]
    atr_pct = percentile_rank(atr_window, atr_window[-1]) if atr_window else None

    prev_close = daily[-1].close if daily else None
    gap = (
        opening_gap_pct(today_5m[0].open, prev_close)
        if today_5m and prev_close is not None
        else None
    )

    return IndicatorSnapshot(
        symbol=symbol,
        price=closes_1h[-1] if closes_1h else None,
        ema_fast=ema(closes_1h, ema_fast_p),
        ema_slow=ema(closes_1h, ema_slow_p),
        rsi=rsi(closes_1h, rsi_p),
        atr_1h=atr(bars_1h, atr_p),
        session_vwap=session_vwap(today_5m),
        obv_trend=obv_trend(bars_1h, obv_p),
        rs_vs_spy_pct=relative_strength_pct(closes_1h, spy_closes_1h, rs_p),
        daily_close=daily_closes[-1] if daily_closes else None,
        daily_sma_fast=sma(daily_closes, sma_fast_p),
        daily_sma_slow=sma(daily_closes, sma_slow_p),
        daily_atr_percentile=atr_pct,
        opening_gap_pct=gap,
        days_to_earnings=days_to_earnings,
        earnings_available=earnings_available,
    )
