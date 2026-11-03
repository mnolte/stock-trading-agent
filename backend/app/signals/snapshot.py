"""Indicator snapshot: the per-symbol input the rules filter and the model use.

Daily indicators use completed sessions only; the still-running session never
counts as a "daily close". 1h indicators use closed session-aligned bars only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.data.models import Bar1h, Bar5m, CalendarDay, DailyBar
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
    session: CalendarDay | None = None,
) -> IndicatorSnapshot:
    """The per-symbol snapshot.

    ``session`` is today's trading session; the opening gap is only computed
    when the first cached 5m bar is the session-open bar (a later first bar
    means the open is unknown, not gapped), and the previous close is the
    last session completed *before* today — after the close, ``daily[-1]``
    is today's own bar.
    """
    ema_fast_p = config.get_int("indicators", "ema_fast", default=20)
    ema_slow_p = config.get_int("indicators", "ema_slow", default=50)
    rsi_p = config.get_int("indicators", "rsi_period", default=14)
    atr_p = config.get_int("indicators", "atr_period", default=14)
    obv_p = config.get_int("indicators", "obv_lookback", default=20)
    rs_p = config.get_int("indicators", "rs_lookback", default=20)
    sma_fast_p = config.get_int("indicators", "daily_sma_fast", default=50)
    sma_slow_p = config.get_int("indicators", "daily_sma_slow", default=200)
    percentile_sessions = config.get_int(
        "indicators", "atr_percentile_sessions", default=60
    )

    closes_1h = [b.close for b in bars_1h]
    daily_closes = [b.close for b in daily]

    daily_atr = atr_series(daily, atr_p)
    # Same rule as the regime: the percentile needs its full window.
    atr_pct = (
        percentile_rank(daily_atr[-percentile_sessions:], daily_atr[-1])
        if len(daily_atr) >= percentile_sessions
        else None
    )

    # Relative strength: pair symbol and SPY closes on the same bar times —
    # a data gap on either side must not slide one series against the other.
    spy_by_start = {b.start: b.close for b in spy_1h}
    matched = [
        (b.close, spy_by_start[b.start])
        for b in bars_1h
        if b.start in spy_by_start
    ]
    rs = relative_strength_pct(
        [pair[0] for pair in matched],
        [pair[1] for pair in matched],
        rs_p,
    )

    prev_close = (
        daily[-2].close
        if (
            session is not None
            and len(daily) >= 2
            and daily[-1].date == session.date
        )
        else (daily[-1].close if daily else None)
    )
    gap = (
        opening_gap_pct(today_5m[0].open, prev_close)
        if (
            today_5m
            and prev_close is not None
            and session is not None
            and today_5m[0].start == session.open
        )
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
        rs_vs_spy_pct=rs,
        daily_close=daily_closes[-1] if daily_closes else None,
        daily_sma_fast=sma(daily_closes, sma_fast_p),
        daily_sma_slow=sma(daily_closes, sma_slow_p),
        daily_atr_percentile=atr_pct,
        opening_gap_pct=gap,
        days_to_earnings=days_to_earnings,
        earnings_available=earnings_available,
    )
