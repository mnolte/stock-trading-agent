"""Regime tests: trend and volatility classification plus the entry block."""

from datetime import date, timedelta
from decimal import Decimal

from app.config import AppConfig
from app.data.models import DailyBar
from app.signals.regime import compute_regime


def _daily(day: date, close: Decimal, tr: Decimal) -> DailyBar:
    """A daily bar whose true range vs the previous close is ``tr``."""
    return DailyBar(
        symbol="SPY",
        date=day,
        open=close,
        high=close + tr / 2,
        low=close - tr / 2,
        close=close,
        volume=1000,
    )


def _days(count: int, close_start: int, step: int, tr: str) -> list[DailyBar]:
    start = date(2026, 1, 5)
    return [
        _daily(
            start + timedelta(days=i),
            Decimal(close_start + i * step),
            Decimal(tr),
        )
        for i in range(count)
    ]


def test_regime_up() -> None:
    # Rising closes: last close > SMA 50 and SMA 50 > SMA 200.
    bars = _days(220, 100, 1, "1")
    regime = compute_regime(bars, AppConfig())
    assert regime.trend == "up"


def test_regime_down() -> None:
    bars = _days(220, 500, -1, "1")
    regime = compute_regime(bars, AppConfig())
    assert regime.trend == "down"


def test_regime_sideways_when_flat() -> None:
    bars = _days(220, 100, 0, "1")
    regime = compute_regime(bars, AppConfig())
    assert regime.trend == "sideways"


def test_regime_sideways_when_history_too_short_for_sma200() -> None:
    # The spec's 60-session cache cannot confirm a 200-session trend.
    bars = _days(60, 100, 1, "1")
    regime = compute_regime(bars, AppConfig())
    assert regime.trend == "sideways"


def test_regime_volatility_high() -> None:
    # Constant large ranges: the latest ATR tops its own history -> high.
    bars = _days(60, 100, 0, "10")
    regime = compute_regime(bars, AppConfig())
    assert regime.volatility == "high"


def test_regime_volatility_low() -> None:
    # Recent quiet days after a volatile stretch -> current ATR ranks low.
    bars = _days(40, 100, 0, "10") + [
        _daily(
            date(2026, 1, 5) + timedelta(days=40 + i),
            Decimal(140),
            Decimal(1),
        )
        for i in range(20)
    ]
    regime = compute_regime(bars, AppConfig())
    assert regime.volatility == "low"


def test_regime_volatility_normal() -> None:
    # Volatile stretch, quiet stretch, then a moderate tail: the current ATR
    # lands in the middle of its own history (percentile ~63 -> normal).
    bars = (
        _days(20, 100, 0, "4")
        + [
            _daily(
                date(2026, 1, 5) + timedelta(days=20 + i),
                Decimal(100),
                Decimal("0.5"),
            )
            for i in range(15)
        ]
        + [
            _daily(
                date(2026, 1, 5) + timedelta(days=35 + i),
                Decimal(100),
                Decimal(2),
            )
            for i in range(25)
        ]
    )
    regime = compute_regime(bars, AppConfig())
    assert regime.volatility == "normal"


def test_down_high_blocks_entries() -> None:
    bars = _days(210, 500, -1, "10")
    regime = compute_regime(bars, AppConfig())
    assert regime.trend == "down"
    assert regime.volatility == "high"
    assert regime.blocks_entries is True


def test_sideways_never_blocks() -> None:
    bars = _days(60, 100, 0, "10")
    regime = compute_regime(bars, AppConfig())
    assert regime.trend == "sideways"
    assert regime.blocks_entries is False
