"""Input snapshot builder (docs/SPEC.md, Decision layer, "Prompt").

Builds the JSON block sent as the user message of the decision call. Fields
match ``prompts/decision.v1.md``: ``symbol``, ``context``, ``time_ny``,
``price``, ``regime``, ``indicators_1h``, ``indicators_daily``, ``levels``,
``position``, ``days_to_earnings`` and ``headlines``. Anything that could not
be fetched is the string ``"unavailable"`` — the prompt tells the model to
treat it as unknown, never as "nothing happened".
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.data.models import Bar1h, NewsItem
from app.db.models import Position
from app.market.clock import NY_TZ, UTC
from app.signals.regime import MarketRegime
from app.signals.snapshot import IndicatorSnapshot


def _ny_iso(dt: datetime) -> str:
    """ISO time in New York; naive datetimes are stored UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(NY_TZ).isoformat()

UNAVAILABLE = "unavailable"

# Swing points are local extremes over this many recent 1h bars.
_SWING_LOOKBACK = 40
_SWING_KEEP = 3


def _num(value: Decimal | None, digits: int = 4) -> float | str:
    """JSON-safe number, or "unavailable" when missing."""
    if value is None:
        return UNAVAILABLE
    return float(round(value, digits))


def _pct(value: Decimal | None) -> float | str:
    """Percent value with sign, or "unavailable"."""
    if value is None:
        return UNAVAILABLE
    return float(round(value, 2))


def swing_levels(bars_1h: list[Bar1h]) -> dict[str, list[float]] | str:
    """Recent swing highs and lows: last few local extremes on 1h bars.

    A swing high is a bar whose high exceeds the two bars on each side. The
    model uses them for stop and target placement.
    """
    if not bars_1h:
        return UNAVAILABLE
    window = bars_1h[-_SWING_LOOKBACK:]
    highs: list[Decimal] = []
    lows: list[Decimal] = []
    for i in range(2, len(window) - 2):
        bar = window[i]
        neighbours = window[i - 2 : i] + window[i + 1 : i + 3]
        if bar.high > max(b.high for b in neighbours):
            highs.append(bar.high)
        if bar.low < min(b.low for b in neighbours):
            lows.append(bar.low)
    return {
        "swing_highs": [float(round(h, 4)) for h in highs[-_SWING_KEEP:]],
        "swing_lows": [float(round(low, 4)) for low in lows[-_SWING_KEEP:]],
    }


def _position_block(
    position: Position | None, price: Decimal | None
) -> dict[str, Any] | None:
    if position is None:
        return None
    r_so_far: float | str = UNAVAILABLE
    if price is not None:
        risk = position.avg_entry_price - (position.current_stop_price or Decimal(0))
        if risk > 0:
            r_so_far = float(round((price - position.avg_entry_price) / risk, 2))
    block: dict[str, Any] = {
        "entry": _num(position.avg_entry_price, 2),
        "qty": position.qty,
        "stop": _num(position.current_stop_price, 2),
        "target": _num(position.target_price, 2),
        "opened_at": _ny_iso(position.opened_at),
        "r_so_far": r_so_far,
    }
    # A same-day exit deferred by the day-trade rule is shown to the model
    # when the position is re-reviewed at the next session's first cycle.
    if position.deferred_exit:
        block["deferred_exit"] = position.deferred_exit
    return block


def _headlines_block(news: list[NewsItem] | None) -> list[dict[str, Any]] | str:
    if news is None:
        return UNAVAILABLE
    return [
        {
            "id": item.id,
            "time": _ny_iso(item.created_at),
            "source": item.source,
            "title": item.headline,
        }
        for item in news
    ]


def build_input_snapshot(
    *,
    symbol: str,
    context: str,  # candidate | held
    cycle_time_ny: datetime,
    price: Decimal | None,
    regime: MarketRegime,
    snapshot: IndicatorSnapshot,
    bars_1h: list[Bar1h],
    position: Position | None,
    news: list[NewsItem] | None,
) -> dict[str, Any]:
    """The JSON snapshot for one model call; also stored on the decision row."""
    # None = none scheduled; "unavailable" = the earnings source failed.
    days_to_earnings: int | str | None = (
        UNAVAILABLE if not snapshot.earnings_available else snapshot.days_to_earnings
    )

    return {
        "symbol": symbol,
        "context": context,
        "time_ny": cycle_time_ny.isoformat(),
        "price": _num(price, 2),
        "regime": {"trend": regime.trend, "volatility": regime.volatility},
        "indicators_1h": {
            "ema20": _num(snapshot.ema_fast, 2),
            "ema50": _num(snapshot.ema_slow, 2),
            "rsi14": _num(snapshot.rsi, 1),
            "atr14": _num(snapshot.atr_1h, 4),
            "vwap": _num(snapshot.session_vwap, 2),
            "obv_trend": snapshot.obv_trend or UNAVAILABLE,
            "rs_vs_spy_pct": _pct(snapshot.rs_vs_spy_pct),
        },
        "indicators_daily": {
            "sma50": _num(snapshot.daily_sma_fast, 2),
            "sma200": _num(snapshot.daily_sma_slow, 2),
            "atr_percentile": _pct(snapshot.daily_atr_percentile),
            "gap_pct": _pct(snapshot.opening_gap_pct),
            "close": _num(snapshot.daily_close, 2),
        },
        "levels": swing_levels(bars_1h),
        "position": _position_block(position, price),
        "days_to_earnings": days_to_earnings,
        "headlines": _headlines_block(news),
    }
