"""Rules pre-filter tests: the pass case and every fail case."""

from decimal import Decimal

from app.config import AppConfig
from app.rules.prefilter import RulesResult, evaluate_entry_rules
from app.signals.regime import MarketRegime
from app.signals.snapshot import IndicatorSnapshot


def _regime(trend: str = "up", vol: str = "normal", blocks: bool = False) -> MarketRegime:
    return MarketRegime(
        trend=trend,
        volatility=vol,
        key=f"{trend}_{vol}",
        label=f"{trend.capitalize()} · {vol} vol",
        blocks_entries=blocks,
        atr_percentile=None,
    )


def _snapshot(**overrides: object) -> IndicatorSnapshot:
    """A snapshot that passes every check by default."""
    base: dict[str, object] = {
        "symbol": "AAPL",
        "price": Decimal("225"),
        "ema_fast": Decimal("224"),
        "ema_slow": Decimal("220"),
        "rsi": Decimal("55"),
        "atr_1h": Decimal("1.2"),
        "session_vwap": Decimal("224.5"),
        "obv_trend": "up",
        "rs_vs_spy_pct": Decimal("0.4"),
        "daily_close": Decimal("224"),
        "daily_sma_fast": Decimal("215"),
        "daily_sma_slow": Decimal("200"),
        "daily_atr_percentile": Decimal("45"),
        "opening_gap_pct": Decimal("0.3"),
        "days_to_earnings": 20,
        "earnings_available": True,
    }
    base.update(overrides)
    return IndicatorSnapshot(**base)  # type: ignore[arg-type]


_DEFAULT_SNAPSHOT = object()


def _eval(
    *,
    snapshot: object = _DEFAULT_SNAPSHOT,
    regime: MarketRegime | None = None,
    held: bool = False,
    open_entry_order: bool = False,
    is_last_cycle: bool = False,
) -> RulesResult:
    return evaluate_entry_rules(
        "AAPL",
        snapshot=_snapshot() if snapshot is _DEFAULT_SNAPSHOT else snapshot,  # type: ignore[arg-type]
        regime=regime or _regime(),
        held=held,
        open_entry_order=open_entry_order,
        is_last_cycle=is_last_cycle,
        config=AppConfig(),
    )


def test_all_rules_pass() -> None:
    result = _eval()
    assert result.status == "pass"
    assert result.reason is None
    assert all(c.passed for c in result.checks)


def test_held_symbol_is_reviewed_not_candidate() -> None:
    result = _eval(held=True)
    assert result.status == "held"
    assert result.reason == "Held · reviewed each cycle"


def test_open_entry_order_blocks() -> None:
    result = _eval(open_entry_order=True)
    assert result.status == "fail"
    assert result.reason == "entry order already open"


def test_below_sma50_fails() -> None:
    result = _eval(snapshot=_snapshot(daily_close=Decimal("200")))
    assert result.status == "fail"
    assert result.reason and "SMA" in result.reason


def test_missing_daily_data_fails() -> None:
    result = _eval(snapshot=_snapshot(daily_sma_fast=None))
    assert result.status == "fail"
    assert result.reason == "daily data unavailable"


def test_ema_crossed_down_fails() -> None:
    result = _eval(snapshot=_snapshot(ema_fast=Decimal("218"), ema_slow=Decimal("220")))
    assert result.status == "fail"
    assert result.reason and "EMA" in result.reason


def test_rsi_too_high_fails() -> None:
    result = _eval(snapshot=_snapshot(rsi=Decimal("71")))
    assert result.status == "fail"
    assert result.reason == "RSI 71 > 70"


def test_rsi_too_low_fails() -> None:
    result = _eval(snapshot=_snapshot(rsi=Decimal("39")))
    assert result.status == "fail"
    assert result.reason == "RSI 39 < 40"


def test_rsi_boundaries_pass() -> None:
    assert _eval(snapshot=_snapshot(rsi=Decimal("40"))).status == "pass"
    assert _eval(snapshot=_snapshot(rsi=Decimal("70"))).status == "pass"


def test_earnings_blackout_fails() -> None:
    for days in (0, 1, 3):
        result = _eval(snapshot=_snapshot(days_to_earnings=days))
        assert result.status == "fail"
        assert result.reason and "earnings" in result.reason
    # Four trading days out is outside the 3-day blackout.
    assert _eval(snapshot=_snapshot(days_to_earnings=4)).status == "pass"


def test_earnings_unavailable_fails() -> None:
    # Missing earnings data blocks new entries for that symbol.
    result = _eval(snapshot=_snapshot(earnings_available=False, days_to_earnings=None))
    assert result.status == "fail"
    assert result.reason == "earnings data unavailable"


def test_down_high_regime_fails() -> None:
    result = _eval(regime=_regime("down", "high", blocks=True))
    assert result.status == "fail"
    assert result.reason and "blocks" in result.reason


def test_last_cycle_fails() -> None:
    result = _eval(is_last_cycle=True)
    assert result.status == "fail"
    assert result.reason and "last cycle" in result.reason


def test_no_snapshot_fails_cleanly() -> None:
    result = _eval(snapshot=None)
    assert result.status == "fail"
    assert result.reason is not None
