"""Market regime from SPY daily bars: trend x volatility.

Spec: Up = daily close above SMA 50 and SMA 50 above SMA 200; Down = both
below; anything else is Sideways. Volatility is Low / Normal / High by the
SPY daily ATR percentile over the configured session window (below 30,
30 to 70, above 70). Down + High means no new entries.

When the cached history is too short for SMA 200 or the ATR percentile, the
trend cannot be confirmed and reads Sideways; volatility reads "unknown" and
cannot trigger the Down + High block.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.data.models import DailyBar
from app.signals.indicators import atr_series, percentile_rank, sma

if TYPE_CHECKING:
    from app.config import AppConfig

TREND_UP = "up"
TREND_DOWN = "down"
TREND_SIDEWAYS = "sideways"

VOL_LOW = "low"
VOL_NORMAL = "normal"
VOL_HIGH = "high"
VOL_UNKNOWN = "unknown"


@dataclass(frozen=True)
class MarketRegime:
    trend: str
    volatility: str
    key: str  # e.g. "down_high"; matched against regime.block_entries_in
    label: str  # e.g. "Up · normal vol" for the GUI
    blocks_entries: bool
    atr_percentile: Decimal | None


def compute_regime(spy_daily: list[DailyBar], config: AppConfig) -> MarketRegime:
    sma_fast_period = int(config.get("indicators", "daily_sma_fast", default=50))
    sma_slow_period = int(config.get("indicators", "daily_sma_slow", default=200))
    atr_period = int(config.get("indicators", "atr_period", default=14))
    percentile_sessions = int(
        config.get("indicators", "atr_percentile_sessions", default=60)
    )
    vol_low_below = Decimal(
        str(config.get("regime", "vol_low_below_pct", default=30))
    )
    vol_high_above = Decimal(
        str(config.get("regime", "vol_high_above_pct", default=70))
    )
    blocked = set(config.get("regime", "block_entries_in", default=["down_high"]))

    closes = [b.close for b in spy_daily]
    sma_fast = sma(closes, sma_fast_period)
    sma_slow = sma(closes, sma_slow_period)
    last_close = closes[-1] if closes else None

    if last_close is not None and sma_fast is not None and sma_slow is not None:
        if last_close > sma_fast and sma_fast > sma_slow:
            trend = TREND_UP
        elif last_close < sma_fast and sma_fast < sma_slow:
            trend = TREND_DOWN
        else:
            trend = TREND_SIDEWAYS
    else:
        trend = TREND_SIDEWAYS

    atr_values = atr_series(spy_daily, atr_period)
    window = atr_values[-percentile_sessions:]
    percentile = percentile_rank(window, window[-1]) if window else None

    if percentile is None:
        volatility = VOL_UNKNOWN
    elif percentile < vol_low_below:
        volatility = VOL_LOW
    elif percentile > vol_high_above:
        volatility = VOL_HIGH
    else:
        volatility = VOL_NORMAL

    key = f"{trend}_{volatility}"
    label = f"{trend.capitalize()} · {volatility} vol"
    return MarketRegime(
        trend=trend,
        volatility=volatility,
        key=key,
        label=label,
        blocks_entries=key in blocked,
        atr_percentile=percentile,
    )
