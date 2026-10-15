"""Indicator tests against hand-checked values (spec: Testing rules)."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.data.aggregate import aggregate_daily
from app.data.models import Bar5m, CalendarDay, DailyBar
from app.market.clock import NY_TZ, UTC
from app.signals.indicators import (
    atr,
    ema,
    ema_series,
    obv_trend,
    opening_gap_pct,
    percentile_rank,
    relative_strength_pct,
    rsi,
    session_vwap,
    sma,
)


def _bar(
    high: str, low: str, close: str, volume: int = 100, day: date | None = None
) -> DailyBar:
    return DailyBar(
        symbol="T",
        date=day or date(2026, 10, 15),
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


def test_sma_hand_checked() -> None:
    values = [Decimal(str(v)) for v in [10, 11, 12, 13, 14]]
    assert sma(values, 3) == Decimal(13)  # (12+13+14)/3
    assert sma(values, 5) == Decimal(12)
    assert sma(values, 6) is None  # not enough values


def test_ema_sma_seed_hand_checked() -> None:
    # Seeded with SMA(1,2,3)=2 at index 2, then k=0.5: ema=4*0.5+2*0.5=3, then 5*0.5+3*0.5=4.
    series = ema_series([Decimal(str(v)) for v in [1, 2, 3, 4, 5]], 3)
    assert series == [None, None, Decimal(2), Decimal("3.0"), Decimal("4.00")]
    assert ema([Decimal(str(v)) for v in [1, 2, 3, 4, 5]], 3) == Decimal("4.00")
    assert ema([Decimal(1), Decimal(2)], 3) is None


def test_rsi_all_gains_and_losses() -> None:
    up = [Decimal(100 + i) for i in range(20)]
    down = [Decimal(200 - i) for i in range(20)]
    assert rsi(up, 14) == Decimal(100)
    assert rsi(down, 14) == Decimal(0)


def test_rsi_balanced_seed() -> None:
    # First 14 changes: 7 gains of 1, 7 losses of 1 -> avg gain = avg loss -> RSI 50.
    closes = [Decimal(100)]
    for i in range(14):
        closes.append(closes[-1] + (Decimal(1) if i % 2 == 0 else Decimal(-1)))
    assert rsi(closes, 14) == Decimal(50)


def test_rsi_smoothing_step_hand_checked() -> None:
    # Seed: 7 gains and 7 losses of 1 -> avg gain = avg loss = 0.5.
    # Then one +2 move: avg_gain=(0.5*13+2)/14=8.5/14, avg_loss=6.5/14
    # -> RS=17/13 -> RSI = 100 - 100*13/30 = 56.67.
    closes = [Decimal(100)]
    for i in range(14):
        closes.append(closes[-1] + (Decimal(1) if i % 2 == 0 else Decimal(-1)))
    closes.append(closes[-1] + Decimal(2))
    expected = Decimal(100) - Decimal(100) / (1 + Decimal(17) / Decimal(13))
    assert rsi(closes, 14) == expected


def test_rsi_needs_period_plus_one() -> None:
    closes = [Decimal(100 + i) for i in range(14)]
    assert rsi(closes, 14) is None


def test_atr_constant_range() -> None:
    # Every bar 100..110 with prev close 105 -> TR = 10 -> ATR = 10.
    bars = [_bar("110", "100", "105") for _ in range(20)]
    assert atr(bars, 14) == Decimal(10)


def test_atr_smoothing_step_hand_checked() -> None:
    # True ranges 1,2,...,15: seed = mean(1..14) = 7.5,
    # next = (7.5*13 + 15)/14 = 8.0357142...
    bars = [_bar("0", "0", "0")]  # prev close anchor
    for tr in range(1, 16):
        # high-low = tr, prev close centred so gaps add nothing
        bars.append(_bar(str(tr), "0", "0"))
    expected = (Decimal("7.5") * 13 + Decimal(15)) / 14
    assert atr(bars, 14) == expected


def test_atr_insufficient_bars() -> None:
    bars = [_bar("110", "100", "105") for _ in range(10)]
    assert atr(bars, 14) is None


def test_session_vwap_hand_checked() -> None:
    # Bar 1: typical 100 x 1000; bar 2: typical 102 x 500 -> VWAP 151000/1500.
    bars = [
        Bar5m(
            symbol="T",
            start=datetime(2026, 10, 15, 13, 30, tzinfo=UTC),
            open=Decimal("99"),
            high=Decimal("102"),
            low=Decimal("98"),
            close=Decimal("100"),
            volume=1000,
            feed="test",
        ),
        Bar5m(
            symbol="T",
            start=datetime(2026, 10, 15, 13, 35, tzinfo=UTC),
            open=Decimal("101"),
            high=Decimal("103"),
            low=Decimal("101"),
            close=Decimal("102"),
            volume=500,
            feed="test",
        ),
    ]
    assert session_vwap(bars) == Decimal("151000") / Decimal(1500)


def test_obv_trend() -> None:
    up_bars = [_bar("11", "9", str(10 + i), 100) for i in range(25)]
    assert obv_trend(up_bars, 20) == "up"
    down_bars = [_bar("11", "9", str(40 - i), 100) for i in range(25)]
    assert obv_trend(down_bars, 20) == "down"
    flat_bars = [_bar("11", "9", "10", 100) for _ in range(25)]
    assert obv_trend(flat_bars, 20) == "flat"
    assert obv_trend(flat_bars[:10], 20) is None


def test_relative_strength_hand_checked() -> None:
    # Symbol up 10% over 20 bars, SPY up 5% -> +5 pct points.
    sym = [Decimal(100)] + [Decimal(110)] + [Decimal(110)] * 19
    spy = [Decimal(400)] + [Decimal(420)] + [Decimal(420)] * 19
    assert relative_strength_pct(sym, spy, 20) == Decimal(5)


def test_percentile_rank() -> None:
    history = [Decimal(str(v)) for v in range(1, 11)]  # 1..10
    assert percentile_rank(history, Decimal(5)) == Decimal(50)
    assert percentile_rank(history, Decimal(0)) == Decimal(0)
    assert percentile_rank([], Decimal(5)) is None


def test_opening_gap() -> None:
    assert opening_gap_pct(Decimal("103"), Decimal("100")) == Decimal(3)
    assert opening_gap_pct(Decimal("97"), Decimal("100")) == Decimal(-3)


def test_aggregate_daily() -> None:
    session = CalendarDay(
        date=date(2026, 10, 15),
        open=datetime(2026, 10, 15, 9, 30, tzinfo=NY_TZ),
        close=datetime(2026, 10, 15, 16, 0, tzinfo=NY_TZ),
    )
    bars = [
        Bar5m(
            symbol="T",
            start=(session.open + timedelta(minutes=5 * i)).astimezone(UTC),
            open=Decimal(str(100 + i)),
            high=Decimal(str(100 + i)) + Decimal("0.5"),
            low=Decimal(str(100 + i)) - Decimal("0.5"),
            close=Decimal(str(100 + i)),
            volume=100,
            feed="test",
        )
        for i in range(78)
    ]
    daily = aggregate_daily("T", bars, session)
    assert daily is not None
    assert daily.open == Decimal("100")
    assert daily.close == Decimal("177")
    assert daily.high == Decimal("177.5")
    assert daily.low == Decimal("99.5")
    assert daily.volume == 7800
    assert aggregate_daily("T", [], session) is None
