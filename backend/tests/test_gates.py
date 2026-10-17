"""Risk gates: every gate gets a pass and a fail case (docs/SPEC.md, Risk)."""

from datetime import datetime
from decimal import Decimal

import pytest
from app.config import AppConfig
from app.db.models import Position
from app.risk.gates import GateContext, GateResult, evaluate_entry_gates

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
        "open_entry_symbols": frozenset(),
        "day_trades_5d": 0,
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


def _position(symbol: str, qty: int = 4, entry: str = "100", stop: str = "98") -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        avg_entry_price=Decimal(entry),
        current_stop_price=Decimal(stop),
        opened_at=datetime(2026, 10, 14, 15, 0),
    )


def _run(
    ctx: GateContext, entry: str = "100", stop: str = "98", config: AppConfig = CONFIG
) -> GateResult:
    return evaluate_entry_gates(
        ctx, entry=Decimal(entry), stop=Decimal(stop), config=config
    )


def _check_names(result: GateResult) -> list[str]:
    return [c.name for c in result.checks]


def test_all_gates_pass() -> None:
    result = _run(_ctx())
    assert result.allowed
    assert result.blocked_by is None
    assert result.sizing is not None and result.sizing.shares > 0
    # 0.5% of 10000 = $50 risk / $2 stop distance = 25 shares, but the 20%
    # max position caps at 20 shares — the binding limit is named.
    assert result.sizing.shares == 20
    assert "max_position" in result.sizing.limited_by


def test_kill_switch_blocks() -> None:
    result = _run(_ctx(kill_switch_on=True))
    assert not result.allowed
    assert result.blocked_by == "kill_switch"


def test_paused_blocks() -> None:
    result = _run(_ctx(paused=True))
    assert not result.allowed
    assert result.blocked_by == "paused"


def test_paused_passes_when_resumed() -> None:
    result = _run(_ctx(paused=False))
    assert result.allowed
    check = next(c for c in result.checks if c.name == "paused")
    assert check.passed


def test_pending_reconciliation_blocks() -> None:
    result = _run(_ctx(pending_ack=True))
    assert not result.allowed
    assert result.blocked_by == "reconciliation"


def test_already_held_blocks() -> None:
    result = _run(_ctx(open_positions=[_position("MSFT")]))
    assert not result.allowed
    assert result.blocked_by == "already_held"


def test_pending_entry_blocks() -> None:
    result = _run(_ctx(open_entry_symbols=frozenset({"MSFT"})))
    assert not result.allowed
    assert result.blocked_by == "entry_pending"


def test_max_open_positions_blocks() -> None:
    positions = [_position(s) for s in ("AAPL", "JPM", "XOM", "AMZN")]
    result = _run(_ctx(open_positions=positions))
    assert not result.allowed
    assert result.blocked_by == "max_open_positions"


def test_pending_entries_count_toward_max_positions() -> None:
    positions = [_position(s) for s in ("AAPL", "JPM", "XOM")]
    result = _run(_ctx(open_positions=positions, open_entry_symbols=frozenset({"NVDA"})))
    assert not result.allowed
    assert result.blocked_by == "max_open_positions"


def test_sector_cap_blocks() -> None:
    # MSFT is Technology; two Technology positions are already open.
    positions = [_position("AAPL"), _position("NVDA")]
    result = _run(_ctx(open_positions=positions))
    assert not result.allowed
    assert result.blocked_by == "sector_cap"


def test_sector_cap_passes_below_limit() -> None:
    positions = [_position("AAPL")]
    result = _run(_ctx(open_positions=positions))
    assert result.allowed
    check = next(c for c in result.checks if c.name == "sector_cap")
    assert check.passed


def test_sector_cap_qqq_is_technology_spy_is_index() -> None:
    # Two Technology names open: QQQ (Technology) is blocked by the cap,
    # SPY (Index) is not — the ETF mappings come from config.sectors.
    positions = [_position("AAPL"), _position("NVDA")]
    assert _run(_ctx(symbol="QQQ", open_positions=positions)).blocked_by == "sector_cap"
    spy = _run(_ctx(symbol="SPY", open_positions=positions))
    check = next(c for c in spy.checks if c.name == "sector_cap")
    assert check.passed


def test_daily_loss_limit_blocks() -> None:
    # −2.5% session P&L vs the previous close's equity.
    result = _run(_ctx(equity=Decimal("9750"), last_equity=Decimal("10000")))
    assert not result.allowed
    assert result.blocked_by == "daily_loss_limit"


def test_daily_loss_limit_passes_above() -> None:
    result = _run(_ctx(equity=Decimal("9900"), last_equity=Decimal("10000")))
    assert result.allowed


def test_shock_brake_1h_blocks() -> None:
    result = _run(_ctx(shock_brake_1h=True))
    assert not result.allowed
    assert result.blocked_by == "shock_brake"


def test_shock_brake_session_blocks() -> None:
    result = _run(_ctx(shock_brake_session=True))
    assert not result.allowed
    assert result.blocked_by == "shock_brake"


def test_earnings_unknown_blocks() -> None:
    # The earnings source is down: no entry, per the spec's data-down rule.
    result = _run(_ctx(days_to_earnings=None, earnings_known=False))
    assert not result.allowed
    assert result.blocked_by == "earnings_blackout"


def test_earnings_within_blackout_blocks() -> None:
    result = _run(_ctx(days_to_earnings=3))
    assert not result.allowed
    assert result.blocked_by == "earnings_blackout"


def test_earnings_outside_blackout_passes() -> None:
    result = _run(_ctx(days_to_earnings=4))
    assert result.allowed


def test_pdt_guard_blocks_at_three() -> None:
    result = _run(_ctx(day_trades_5d=3))
    assert not result.allowed
    assert result.blocked_by == "pdt_guard"


def test_pdt_guard_passes_below_three() -> None:
    result = _run(_ctx(day_trades_5d=2))
    assert result.allowed


def test_drawdown_kill_blocks() -> None:
    # last_equity matches equity so the session loss gate stays out of it.
    result = _run(
        _ctx(
            equity=Decimal("9100"),
            last_equity=Decimal("9100"),
            peak_equity=Decimal("10000"),
        )
    )
    assert not result.allowed
    assert result.blocked_by == "drawdown_kill"


def test_drawdown_below_kill_passes() -> None:
    result = _run(
        _ctx(
            equity=Decimal("9300"),
            last_equity=Decimal("9300"),
            peak_equity=Decimal("10000"),
        )
    )
    assert result.allowed


def test_losing_streak_halves_risk_budget() -> None:
    result = _run(_ctx(losing_streak=3))
    assert result.allowed
    assert result.risk_factor == Decimal("0.5")
    assert result.sizing is not None
    # Halved risk budget: $25 / $2 stop distance = 12 shares.
    assert result.sizing.shares == 12
    assert "risk_per_trade" in result.sizing.limited_by


def test_zero_shares_blocks_with_limit_name() -> None:
    # Cash of $50 buys less than one $100 share.
    result = _run(_ctx(cash=Decimal("50")))
    assert not result.allowed
    assert result.blocked_by == "cash"
    assert result.sizing is not None and result.sizing.shares == 0


def test_invalid_stop_blocks() -> None:
    result = _run(_ctx(), entry="100", stop="100")
    assert not result.allowed
    assert result.blocked_by == "stop_distance"


def test_order_sanity_pct_blocks() -> None:
    config = AppConfig()
    config.yaml["risk"]["max_position_pct"] = 60  # let sizing exceed 25%
    result = _run(_ctx(), entry="100", stop="99.5", config=config)
    assert not result.allowed
    assert result.blocked_by == "order_sanity"


def test_order_sanity_cycle_count_blocks() -> None:
    result = _run(_ctx(orders_this_cycle=6))
    assert not result.allowed
    assert result.blocked_by == "order_sanity"


def test_first_failing_gate_wins() -> None:
    # Kill switch and a daily loss both apply; the kill switch is checked first.
    result = _run(
        _ctx(
            kill_switch_on=True,
            equity=Decimal("9000"),
            last_equity=Decimal("10000"),
        )
    )
    assert result.blocked_by == "kill_switch"
    assert _check_names(result) == ["kill_switch"]


def test_gates_result_serialises() -> None:
    result = _run(_ctx())
    payload = result.to_json()
    assert payload["allowed"] is True
    sizing = payload["sizing"]
    assert isinstance(sizing, dict) and sizing["shares"] == 20
    assert isinstance(payload["checks"], list)
