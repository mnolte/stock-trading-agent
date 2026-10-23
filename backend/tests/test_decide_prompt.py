"""Prompt loading: version, hash and the input snapshot shape."""

from datetime import datetime
from decimal import Decimal

from app.data.models import Bar1h, NewsItem
from app.db.models import Position
from app.decide.prompt import cached_prompt, load_prompt
from app.decide.snapshot import UNAVAILABLE, build_input_snapshot, swing_levels
from app.market.clock import NY_TZ
from app.signals.regime import MarketRegime
from app.signals.snapshot import IndicatorSnapshot


def test_prompt_v1_loads_with_hash() -> None:
    prompt = load_prompt("decision.v1")
    assert prompt.version == "decision.v1"
    assert len(prompt.sha256) == 64
    assert prompt.short_hash == prompt.sha256[:8]
    assert "decision layer of Stock Agent" in prompt.text


def test_cached_prompt_is_stable() -> None:
    assert cached_prompt("decision.v1") is cached_prompt("decision.v1")


def _snapshot(**overrides: object) -> IndicatorSnapshot:
    base: dict[str, object] = {
        "symbol": "MSFT",
        "price": Decimal("418.19"),
        "ema_fast": Decimal("416.90"),
        "ema_slow": Decimal("414.30"),
        "rsi": Decimal("57"),
        "atr_1h": Decimal("3.10"),
        "session_vwap": Decimal("417.40"),
        "obv_trend": "up",
        "rs_vs_spy_pct": Decimal("0.9"),
        "daily_close": Decimal("415.80"),
        "daily_sma_fast": Decimal("402.10"),
        "daily_sma_slow": Decimal("381.60"),
        "daily_atr_percentile": Decimal("48"),
        "opening_gap_pct": Decimal("0.3"),
        "days_to_earnings": 9,
        "earnings_available": True,
    }
    base.update(overrides)
    return IndicatorSnapshot(**base)  # type: ignore[arg-type]


def _regime() -> MarketRegime:
    return MarketRegime(
        trend="up",
        volatility="normal",
        key="up_normal",
        label="Up · normal vol",
        blocks_entries=False,
        atr_percentile=Decimal("48"),
    )


def _bar(i: int, high: str, low: str) -> Bar1h:
    start = datetime(2026, 10, 15, 9, 30, tzinfo=NY_TZ)
    return Bar1h(
        symbol="MSFT",
        start=start,
        end=start,
        open=Decimal("415"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("416"),
        volume=1000 + i,
        feed="fake",
        complete=True,
    )


def test_swing_levels_finds_extremes() -> None:
    bars = [
        _bar(0, "410", "408"),
        _bar(1, "411", "409"),
        _bar(2, "415", "410"),  # swing high
        _bar(3, "412", "410"),
        _bar(4, "411", "407"),  # swing low
        _bar(5, "412", "408"),
        _bar(6, "413", "410"),
    ]
    levels = swing_levels(bars)
    assert isinstance(levels, dict)
    assert levels["swing_highs"] == [415.0]
    assert levels["swing_lows"] == [407.0]


def test_swing_levels_unavailable_without_bars() -> None:
    assert swing_levels([]) == UNAVAILABLE


def test_input_snapshot_shape_for_candidate() -> None:
    news = [
        NewsItem(
            id="n_1",
            symbol="MSFT",
            headline="Microsoft expands Azure capacity in Europe",
            source="Benzinga",
            created_at=datetime(2026, 10, 15, 7, 12, tzinfo=NY_TZ),
        )
    ]
    snap = build_input_snapshot(
        symbol="MSFT",
        context="candidate",
        cycle_time_ny=datetime(2026, 10, 15, 10, 46, tzinfo=NY_TZ),
        price=Decimal("418.19"),
        regime=_regime(),
        snapshot=_snapshot(),
        bars_1h=[_bar(0, "416", "412")],
        position=None,
        news=news,
    )
    assert snap["symbol"] == "MSFT"
    assert snap["context"] == "candidate"
    assert snap["price"] == 418.19
    assert snap["regime"] == {"trend": "up", "volatility": "normal"}
    assert snap["indicators_1h"]["rsi14"] == 57.0
    assert snap["indicators_daily"]["sma200"] == 381.6
    assert snap["position"] is None
    assert snap["days_to_earnings"] == 9
    assert snap["headlines"][0]["id"] == "n_1"
    assert "swing_highs" in snap["levels"]


def test_input_snapshot_marks_unavailable() -> None:
    snap = build_input_snapshot(
        symbol="MSFT",
        context="held",
        cycle_time_ny=datetime(2026, 10, 15, 10, 46, tzinfo=NY_TZ),
        price=None,
        regime=_regime(),
        snapshot=_snapshot(
            rsi=None, atr_1h=None, earnings_available=False, days_to_earnings=None
        ),
        bars_1h=[],
        position=None,
        news=None,
    )
    assert snap["price"] == UNAVAILABLE
    assert snap["indicators_1h"]["rsi14"] == UNAVAILABLE
    assert snap["indicators_1h"]["atr14"] == UNAVAILABLE
    assert snap["days_to_earnings"] == UNAVAILABLE
    assert snap["headlines"] == UNAVAILABLE
    assert snap["levels"] == UNAVAILABLE


def test_report_r_uses_initial_risk_after_stop_moves() -> None:
    from app.decide.snapshot import _position_block

    position = Position(
        symbol="WMT", qty=16, avg_entry_price=Decimal("100"),
        current_stop_price=Decimal("99"), opened_at=datetime(2026, 10, 22, 15),
    )
    position.initial_stop = Decimal("95")
    for stop in ("95", "99", "100", "101"):
        position.current_stop_price = Decimal(stop)
        block = _position_block(position, Decimal("105"))
        assert block is not None
        assert block["r_so_far"] == 1
        assert block["remaining_downside_usd"] == float((Decimal("105") - Decimal(stop)) * 16)


def test_input_snapshot_position_block() -> None:
    position = Position(
        symbol="MSFT",
        entry_decision_id=None,
        qty=4,
        avg_entry_price=Decimal("418.20"),
        initial_stop=Decimal("409.60"),
        current_stop_price=Decimal("409.60"),
        target_price=Decimal("435.40"),
        opened_at=datetime(2026, 10, 15, 14, 48),  # naive UTC
    )
    snap = build_input_snapshot(
        symbol="MSFT",
        context="held",
        cycle_time_ny=datetime(2026, 10, 15, 11, 46, tzinfo=NY_TZ),
        price=Decimal("421.30"),
        regime=_regime(),
        snapshot=_snapshot(),
        bars_1h=[_bar(0, "416", "412")],
        position=position,
        news=[],
    )
    pos = snap["position"]
    assert isinstance(pos, dict)
    assert pos["entry"] == 418.2
    assert pos["qty"] == 4
    assert pos["stop"] == 409.6
    # (421.30 - 418.20) / (418.20 - 409.60) = 0.36R
    assert pos["r_so_far"] == 0.36
    assert "-04:00" in pos["opened_at"]  # stored UTC shown in New York time
