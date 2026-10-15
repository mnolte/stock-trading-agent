"""Shared risk math: stop distance, risk, reward to risk and sizing."""

from decimal import Decimal

from app.config import AppConfig
from app.data.fake import _BASE_PRICES
from app.risk.math import open_risk, position_size, trade_risk

EQUITY = Decimal("10000")
CASH = Decimal("10000")


def test_trade_risk_hand_checked() -> None:
    risk = trade_risk(Decimal("100"), Decimal("95"), Decimal("110"), atr=Decimal("2"))
    assert risk.valid
    assert risk.stop_distance == Decimal("5")
    assert risk.stop_distance_pct == Decimal("5")
    assert risk.atr_multiple == Decimal("2.5")
    assert risk.reward_to_risk == Decimal("2")


def test_trade_risk_invalid_stop() -> None:
    risk = trade_risk(Decimal("100"), Decimal("105"), Decimal("110"))
    assert not risk.valid
    assert risk.reward_to_risk is None
    assert risk.atr_multiple is None


def test_position_size_from_risk_budget() -> None:
    # 0.5% of $10,000 = $50; stop distance $5 -> 10 shares.
    sizing = position_size(
        EQUITY,
        Decimal("100"),
        Decimal("95"),
        cash=CASH,
        current_open_risk=Decimal(0),
        config=AppConfig(),
    )
    assert sizing.shares == 10
    assert sizing.limited_by == ["risk_per_trade"]


def test_position_size_max_position_cap() -> None:
    # Tight stop: risk budget would buy 100 shares, but 20% of equity caps
    # the order at $2,000 -> 20 shares.
    sizing = position_size(
        EQUITY,
        Decimal("100"),
        Decimal("99.50"),
        cash=CASH,
        current_open_risk=Decimal(0),
        config=AppConfig(),
    )
    assert sizing.shares == 20
    assert sizing.limited_by == ["max_position"]


def test_position_size_open_risk_room() -> None:
    # 2.5% open-risk cap = $250; $230 already open -> room for $20 -> 4 shares.
    sizing = position_size(
        EQUITY,
        Decimal("100"),
        Decimal("95"),
        cash=CASH,
        current_open_risk=Decimal("230"),
        config=AppConfig(),
    )
    assert sizing.shares == 4
    assert sizing.limited_by == ["open_risk"]


def test_position_size_cash_cap() -> None:
    sizing = position_size(
        EQUITY,
        Decimal("500"),
        Decimal("495"),
        cash=Decimal("1500"),
        current_open_risk=Decimal(0),
        config=AppConfig(),
    )
    assert sizing.shares == 3
    assert sizing.limited_by == ["cash"]


def test_position_size_zero_when_room_used() -> None:
    # Open-risk budget fully used -> 0 shares (the gate will skip).
    sizing = position_size(
        EQUITY,
        Decimal("100"),
        Decimal("95"),
        cash=CASH,
        current_open_risk=Decimal("250"),
        config=AppConfig(),
    )
    assert sizing.shares == 0


def test_position_size_invalid_stop_zero() -> None:
    sizing = position_size(
        EQUITY,
        Decimal("100"),
        Decimal("100"),
        cash=CASH,
        current_open_risk=Decimal(0),
        config=AppConfig(),
    )
    assert sizing.shares == 0


def test_open_risk_sums_entry_to_stop() -> None:
    # 4 x (418.20-409.60) + 8 x max(0, 231.40-233.10->0): a stop above entry
    # contributes nothing.
    total = open_risk(
        [
            (4, Decimal("418.20"), Decimal("409.60")),
            (8, Decimal("231.40"), Decimal("233.10")),
        ]
    )
    assert total == Decimal("34.4")


def test_every_default_symbol_can_trade_at_typical_atr() -> None:
    # Spec check: each default symbol can produce a valid trade at a typical
    # 1h ATR (1% of price) and $10,000 equity — stop 1.5-4x ATR, R/R >= 1.5,
    # >= 1 whole share within risk per trade, max position and cash.
    config = AppConfig()
    symbols = list(config.get("universe", "symbols"))
    assert set(symbols) == set(_BASE_PRICES)
    risk_budget = EQUITY * Decimal("0.005")  # risk_per_trade 0.5%
    position_cap = EQUITY * Decimal("0.2")  # max_position 20%
    for symbol in symbols:
        price = _BASE_PRICES[symbol]
        atr = price * Decimal("0.01")
        stop = price - 2 * atr
        risk = trade_risk(price, stop, price + 3 * atr, atr=atr)
        assert risk.valid, f"{symbol} can never trade"
        assert risk.atr_multiple is not None
        assert risk.reward_to_risk is not None
        assert Decimal("1.5") <= risk.atr_multiple <= Decimal("4.0"), symbol
        assert risk.reward_to_risk >= Decimal("1.5"), symbol
        sizing = position_size(
            EQUITY,
            price,
            stop,
            cash=CASH,
            current_open_risk=Decimal(0),
            config=config,
        )
        assert sizing.shares >= 1, f"{symbol} can never trade"
        assert sizing.shares * risk.stop_distance <= risk_budget, symbol
        assert sizing.shares * price <= position_cap, symbol
        assert sizing.shares * price <= CASH, symbol
