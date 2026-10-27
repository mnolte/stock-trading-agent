"""Risk budget card: the card's figures are the gates' inputs
(docs/SPEC.md, GUI — Risk budget card)."""

from datetime import datetime
from decimal import Decimal

import pytest
from app.config import AppConfig
from app.db.models import Position
from app.risk.budget import meter_level, risk_budget
from app.risk.gates import GateContext
from app.risk.math import WorkingEntry, committed_entries, open_risk
from fastapi.testclient import TestClient

pytestmark = pytest.mark.anyio

CONFIG = AppConfig()


def _ctx(**overrides: object) -> GateContext:
    base: dict[str, object] = {
        "symbol": "MSFT",
        "equity": Decimal("10000"),
        "cash": Decimal("10000"),
        "last_equity": Decimal("10000"),
        "peak_equity": Decimal("10000"),
        "open_positions": [],
        "working_entries": (),
        "closed_today": False,
        "losing_streak": 0,
        "shock_brake_1h": False,
        "shock_brake_session": False,
        "days_to_earnings": 10,
        "earnings_known": True,
        "kill_switch_on": False,
        "paused": False,
        "pending_ack": False,
        "orders_this_cycle": 0,
    }
    base.update(overrides)
    return GateContext(**base)  # type: ignore[arg-type]


def _position(
    symbol: str, qty: int = 4, entry: str = "100", stop: str = "98"
) -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        avg_entry_price=Decimal(entry),
        current_stop_price=Decimal(stop),
        opened_at=datetime(2026, 10, 14, 15, 0),
    )


def _entry(
    symbol: str,
    unfilled_qty: int = 4,
    limit: str | None = "100",
    stop: str | None = "98",
) -> WorkingEntry:
    return WorkingEntry(
        symbol=symbol,
        unfilled_qty=unfilled_qty,
        limit_price=Decimal(limit) if limit is not None else None,
        stop_price=Decimal(stop) if stop is not None else None,
    )


def test_card_figures_equal_gate_inputs() -> None:
    """Every figure on the card is the same number a gate would see."""
    positions = [_position("AAPL"), _position("JPM")]
    entries = (_entry("NVDA", unfilled_qty=10),)
    ctx = _ctx(
        equity=Decimal("9900"),
        cash=Decimal("9000"),
        last_equity=Decimal("10000"),
        peak_equity=Decimal("10200"),
        open_positions=positions,
        working_entries=entries,
        losing_streak=1,
    )
    budget = risk_budget(ctx, CONFIG)

    # Open risk = position risk + working-entry risk, the same expression
    # evaluate_entry_gates feeds into position_size.
    expected_risk = open_risk(
        (p.qty, p.avg_entry_price, p.current_stop_price or Decimal(0))
        for p in positions
    ) + committed_entries(entries)[0]
    assert budget.open_risk_usd == expected_risk == Decimal("36")
    assert budget.open_risk is not None
    assert budget.open_risk.used_pct == expected_risk / 9900 * 100
    assert budget.open_risk.limit_pct == Decimal("2.5")

    # Today's loss and drawdown are the gates' session P&L and peak
    # drawdown, as magnitudes.
    assert budget.session_pnl_pct == (Decimal("9900") - 10000) / 10000 * 100
    assert budget.daily_loss.used_pct == Decimal("1")
    assert budget.daily_loss.limit_pct == Decimal("2")
    assert budget.drawdown.used_pct == (Decimal("10200") - 9900) / 10200 * 100
    assert budget.drawdown.limit_pct == Decimal("8")

    # Positions + working entries count together, as the gates' open
    # position count does; day trades and the losing streak pass through.
    assert budget.open_count == 3
    assert budget.max_positions == 4
    assert budget.losing_streak == 1
    assert budget.risk_per_trade_pct == Decimal("0.5")

    # Sector use matches the gates' sector check: AAPL (Technology) +
    # NVDA (Technology working entry) + JPM (Financials).
    sectors = {s.sector: (s.used, s.cap) for s in budget.sectors}
    assert sectors == {"Technology": (2, 2), "Financials": (1, 2)}


def test_losing_streak_shows_halved_risk() -> None:
    budget = risk_budget(_ctx(losing_streak=3), CONFIG)
    assert budget.losing_streak == 3
    assert budget.losing_streak_factor == Decimal("0.5")
    assert budget.risk_per_trade_pct == Decimal("0.25")


def test_working_entry_counts_in_open_risk_and_room() -> None:
    # A working NVDA entry commits 110 × (10 − 8) = $220 of the $250
    # open-risk budget and 110 × $10 = $1100 of cash.
    ctx = _ctx(
        working_entries=(_entry("NVDA", unfilled_qty=110, limit="10", stop="8"),)
    )
    budget = risk_budget(ctx, CONFIG)
    assert budget.open_risk_usd == Decimal("220")
    assert budget.open_risk is not None
    assert budget.open_risk.used_pct == Decimal("2.2")

    # Open risk left is $30 < the $50 risk per trade → no room; the card
    # names open risk as the binding limit.
    assert budget.room_trades == 0
    assert budget.room_limited_by == "open_risk"
    assert budget.open_count == 1  # the working entry, as the gates count


def test_room_limited_by_cash_when_cash_is_smallest() -> None:
    # $2100 cash ÷ $2000 max position = 1; positions left 4 and open risk
    # left $250 ÷ $50 = 5 — cash is smallest.
    budget = risk_budget(_ctx(cash=Decimal("2100")), CONFIG)
    assert budget.room_trades == 1
    assert budget.room_limited_by == "cash"


def test_room_limited_by_positions_at_the_cap() -> None:
    positions = [_position(s) for s in ("AAPL", "JPM", "XOM", "AMZN")]
    budget = risk_budget(_ctx(open_positions=positions), CONFIG)
    assert budget.room_trades == 0
    assert budget.room_limited_by == "positions"


def test_room_floors_partial_trades() -> None:
    # $2900 cash ÷ $2000 max position = 1.45 → 1 trade, not 2.
    budget = risk_budget(_ctx(cash=Decimal("2900")), CONFIG)
    assert budget.room_trades == 1


def test_meter_level_thresholds() -> None:
    # Amber above 75% of the limit, red at it (docs/SPEC.md, GUI).
    assert meter_level(Decimal("0"), Decimal("2")) == "normal"
    assert meter_level(Decimal("1.5"), Decimal("2")) == "normal"  # exactly 75%
    assert meter_level(Decimal("1.51"), Decimal("2")) == "amber"
    assert meter_level(Decimal("1.99"), Decimal("2")) == "amber"
    assert meter_level(Decimal("2"), Decimal("2")) == "red"  # at the limit
    assert meter_level(Decimal("3"), Decimal("2")) == "red"


def test_meter_levels_in_budget() -> None:
    # A 1.5% session loss is exactly 75% of the 2% limit → still normal.
    budget = risk_budget(
        _ctx(equity=Decimal("9850"), last_equity=Decimal("10000")), CONFIG
    )
    assert budget.daily_loss.level == "normal"
    # 1.9% is 95% of the limit → amber; 2% → red.
    budget = risk_budget(
        _ctx(equity=Decimal("9810"), last_equity=Decimal("10000")), CONFIG
    )
    assert budget.daily_loss.level == "amber"
    budget = risk_budget(
        _ctx(equity=Decimal("9800"), last_equity=Decimal("10000")), CONFIG
    )
    assert budget.daily_loss.level == "red"


def test_shock_brake_state_and_spy_moves() -> None:
    ctx = _ctx(shock_brake_1h=True)
    budget = risk_budget(
        ctx, CONFIG, spy_1h_pct=Decimal("-1.7"), spy_session_pct=Decimal("-2.1")
    )
    assert budget.shock_brake_on
    assert budget.spy_1h_pct == Decimal("-1.7")
    assert budget.spy_session_pct == Decimal("-2.1")
    assert budget.shock_1h_limit_pct == Decimal("1.5")
    assert budget.shock_session_limit_pct == Decimal("2.5")


def test_risk_budget_endpoint_shape(client: TestClient) -> None:
    body = client.get("/api/risk-budget").json()
    assert {
        "settings_version",
        "open_risk",
        "daily_loss",
        "drawdown",
        "room",
        "sectors",
        "risk_per_trade_pct",
        "losing_streak",
        "shock_brake",
        "re_entry",
        "positions",
    } <= set(body)
    assert {
        "used_pct",
        "used_usd",
        "limit_pct",
        "level",
        "fill_pct",
    } <= set(body["open_risk"])
    assert Decimal(body["open_risk"]["limit_pct"]) == Decimal("2.5")
    assert Decimal(body["daily_loss"]["limit_pct"]) == Decimal("2")
    assert Decimal(body["drawdown"]["limit_pct"]) == Decimal("8")
    assert {"trades", "limited_by"} <= set(body["room"])
    assert body["re_entry"]["allowed"] is False
    assert body["positions"]["cap"] == 4
    assert {
        "on",
        "limit_1h_pct",
        "limit_session_pct",
        "spy_1h_pct",
        "spy_session_pct",
    } <= set(body["shock_brake"])
