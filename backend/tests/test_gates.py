"""Risk gates: every gate gets a pass and a fail case (docs/SPEC.md, Risk)."""

from datetime import datetime
from decimal import Decimal

import pytest
from app.config import AppConfig
from app.db.models import Position
from app.risk.gates import GateContext, GateResult, evaluate_entry_gates
from app.risk.math import WorkingEntry

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


def _entry(
    symbol: str,
    unfilled_qty: int = 4,
    limit: str | None = "100",
    stop: str | None = "98",
) -> WorkingEntry:
    """A working entry order; only the unfilled remainder counts."""
    return WorkingEntry(
        symbol=symbol,
        unfilled_qty=unfilled_qty,
        limit_price=Decimal(limit) if limit is not None else None,
        stop_price=Decimal(stop) if stop is not None else None,
    )


def _run(
    ctx: GateContext, entry: str = "100", stop: str = "98", config: AppConfig = CONFIG
) -> GateResult:
    return evaluate_entry_gates(ctx, entry=Decimal(entry), stop=Decimal(stop), config=config)


def _check_names(result: GateResult) -> list[str]:
    return [c.name for c in result.checks]


def test_unknown_sector_blocks_instead_of_bypassing_cap() -> None:
    result = _run(_ctx(symbol="UNKNOWN"))
    assert not result.allowed
    assert result.blocked_by == "sector_unknown"


def test_screened_ibm_cannot_bypass_two_technology_positions() -> None:
    config = AppConfig()
    config.yaml["universe"]["mode"] = "screened"
    result = _run(
        _ctx(
            symbol="IBM",
            open_positions=[_position("AAPL"), _position("MSFT")],
            sectors={"IBM": "Technology", "AAPL": "Technology", "MSFT": "Technology"},
        ),
        config=config,
    )
    assert not result.allowed
    assert result.blocked_by == "sector_cap"


def test_daily_loss_latch_blocks_after_equity_recovers() -> None:
    result = _run(_ctx(equity=Decimal("9900"), daily_loss_hit=True))
    assert not result.allowed
    assert result.blocked_by == "daily_loss_limit"


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
    result = _run(_ctx(working_entries=(_entry("MSFT"),)))
    assert not result.allowed
    assert result.blocked_by == "entry_pending"


def test_max_open_positions_blocks() -> None:
    positions = [_position(s) for s in ("AAPL", "JPM", "XOM", "AMZN")]
    result = _run(_ctx(open_positions=positions))
    assert not result.allowed
    assert result.blocked_by == "max_open_positions"


def test_pending_entries_count_toward_max_positions() -> None:
    positions = [_position(s) for s in ("AAPL", "JPM", "XOM")]
    result = _run(_ctx(open_positions=positions, working_entries=(_entry("NVDA"),)))
    assert not result.allowed
    assert result.blocked_by == "max_open_positions"


# Working entry orders (docs/SPEC.md, Risk management): a working entry
# counts as if it were already a position — unfilled qty × (limit − stop)
# toward open risk, unfilled qty × limit toward cash.


def test_working_entry_counts_toward_open_risk() -> None:
    # 2.5% of 10000 = $250 open-risk budget. The working NVDA entry already
    # commits 110 × (10 − 8) = $220, leaving $30 → 30 shares at $1 distance
    # on a cheap candidate so order_sanity stays out of the way.
    config = AppConfig()
    config.yaml["risk"]["max_position_pct"] = 100  # isolate open_risk
    result = _run(
        _ctx(working_entries=(_entry("NVDA", unfilled_qty=110, limit="10", stop="8"),)),
        entry="10",
        stop="9",
        config=config,
    )
    assert result.allowed
    assert result.sizing is not None
    assert result.sizing.shares == 30
    assert "open_risk" in result.sizing.limited_by


def test_working_entry_can_exhaust_open_risk() -> None:
    # 500 × (1 − 0.5) = $250 committed — the whole budget: the next entry
    # gets zero shares and the limit is named.
    config = AppConfig()
    config.yaml["risk"]["max_position_pct"] = 100  # isolate open_risk
    result = _run(
        _ctx(working_entries=(_entry("NVDA", unfilled_qty=500, limit="1", stop="0.5"),)),
        entry="100",
        stop="99",
        config=config,
    )
    assert not result.allowed
    assert result.blocked_by == "open_risk"
    assert result.sizing is not None and result.sizing.shares == 0


def test_working_entry_counts_toward_cash() -> None:
    # A working NVDA entry commits 80 × $100 = $8000 of the $10000 cash,
    # leaving $2000 → 20 shares instead of the 25 risk alone would allow.
    config = AppConfig()
    config.yaml["risk"]["max_position_pct"] = 100  # isolate the cash cap
    result = _run(
        _ctx(working_entries=(_entry("NVDA", unfilled_qty=80),)),
        entry="100",
        stop="98",
        config=config,
    )
    assert result.allowed
    assert result.sizing is not None
    assert result.sizing.shares == 20
    assert "cash" in result.sizing.limited_by


def test_working_entry_counts_toward_sector_cap() -> None:
    # AAPL position + a working NVDA entry are both Technology.
    result = _run(
        _ctx(
            open_positions=[_position("AAPL")],
            working_entries=(_entry("NVDA"),),
        )
    )
    assert not result.allowed
    assert result.blocked_by == "sector_cap"


def test_working_entry_without_stop_counts_full_value_as_risk() -> None:
    # No stop leg visible: the whole committed value is the worst case.
    # 3 × $100 = $300 > the $250 budget → zero room.
    config = AppConfig()
    config.yaml["risk"]["max_position_pct"] = 100  # isolate open_risk
    result = _run(
        _ctx(working_entries=(_entry("NVDA", unfilled_qty=3, stop=None),)),
        entry="100",
        stop="98",
        config=config,
    )
    assert not result.allowed
    assert result.blocked_by == "open_risk"


def test_filled_part_is_never_counted_twice() -> None:
    # A partially filled entry: 4 filled (a position already) + 2 working.
    # Risk counts the 2 working only; the filled 4 count once, via the
    # position. JPM is Financials, so the Technology sector cap stays out.
    result = _run(
        _ctx(
            open_positions=[_position("JPM", qty=4, entry="100", stop="98")],
            working_entries=(_entry("JPM", unfilled_qty=2),),
        ),
        entry="100",
        stop="98",
    )
    assert result.allowed
    assert result.sizing is not None and result.sizing.shares > 0


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
