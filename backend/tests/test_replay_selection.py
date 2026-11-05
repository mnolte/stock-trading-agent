from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from app.config import AppConfig
from app.db.models import (
    Cycle,
    Decision,
    EquitySnapshot,
    Order,
    Outcome,
    Position,
    ShadowEquity,
    ShadowPosition,
    Strategy,
)
from app.replay_selection import (
    _entry_limit,
    _live_state,
    _model_enter,
    _ny_day,
    _profit,
    _scored_entries,
    _shadow_state,
    _touch,
    _trade_prices,
    replay_selection,
)
from sqlalchemy.orm import Session, sessionmaker

D = Decimal
START = datetime(2026, 10, 15, 15)


def _decision(symbol: str = "AAA", rank: int = 1, confidence: str = "0.6") -> Decision:
    return Decision(
        symbol=symbol, context="candidate", final_action="blocked",
        prompt_version="decision.v1", prompt_hash="test", model="fake",
        validation_result={"model_action": "enter"},
        gates_result={"entry_limit": "100", "blocked_by": "max_open_positions"},
        input_snapshot={"price": "100", "rank": rank},
        model_output={"stop_price": "98", "target_price": "104", "confidence": confidence},
    )


def test_trade_prices_and_model_enter() -> None:
    assert _trade_prices(None, None, None) == (None, None)
    assert _trade_prices({"stop_price": "98", "target_price": "104"}, None, None) == (
        D("98"), D("104")
    )
    options = {"options": [{"id": "A", "stop": "97", "target": "106"}]}
    assert _trade_prices({}, options, "A") == (D("97"), D("106"))
    assert _trade_prices({}, options, "B") == (None, None)
    decision = _decision()
    assert _model_enter(decision)
    decision.validation_result = None
    assert not _model_enter(decision)
    decision.validation_result = {"model_action": "skip"}
    assert not _model_enter(decision)


def test_entry_limit_prefers_stored_then_rounds_snapshot() -> None:
    decision = _decision()
    config = AppConfig().overlay({"execution": {"entry_limit_offset_pct": 0.05}})
    assert _entry_limit(decision, config) == D("100")
    decision.gates_result = None
    decision.input_snapshot = {"price": "100.10"}
    assert _entry_limit(decision, config) == D("100.15")
    decision.input_snapshot = None
    assert _entry_limit(decision, config) is None


def test_ny_day_uses_utc_and_dst() -> None:
    assert _ny_day(datetime(2026, 10, 15, 3, 59)) == date(2026, 10, 14)
    assert _ny_day(datetime(2026, 10, 15, 4)) == date(2026, 10, 15)
    assert _ny_day(datetime(2026, 12, 15, 4, 59)) == date(2026, 12, 14)


@pytest.mark.parametrize(
    ("touch", "amount", "text"),
    [("target", "25", "+$25.00"), ("stop", "-10", "-$10.00"),
     (None, "0", "no touch"), ("not_filled", "0", "not filled"),
     ("neither", "0", "neither")],
)
def test_profit(touch: str | None, amount: str, text: str) -> None:
    assert _profit(touch, D("2.5"), D("10")) == (D(amount), text)


def test_touch_prefers_chosen_option_and_falls_back() -> None:
    outcome = Outcome(option_touches={"A": "target", "B": None})
    assert _touch(outcome, "A", "stop") == "target"
    assert _touch(outcome, "B", "stop") == "stop"
    assert _touch(outcome, None, "stop") == "stop"
    assert _touch(None, "A", None) is None


def test_live_state_reconstructs_at_start(session_factory: sessionmaker[Session]) -> None:
    before = START - timedelta(hours=2)
    with session_factory() as session:
        cycle = Cycle(scheduled_time=START, status="done")
        session.add(cycle)
        for symbol, opened, closed, pnl in [
            ("OPEN", before, None, None),
            ("FUTURE", START + timedelta(seconds=1), None, None),
            ("WIN", before, before + timedelta(minutes=1), D("1")),
            ("LOSS", before, before + timedelta(minutes=2), D("-1")),
        ]:
            session.add(Position(symbol=symbol, qty=2, avg_entry_price=D("100"),
                                 current_stop_price=D("98"), opened_at=opened,
                                 closed_at=closed, realized_pnl_raw=pnl))
        for symbol, end, filled in [
            ("WORK", START + timedelta(minutes=1), None),
            ("EXPIRED", START, None), ("FILLED", None, START),
        ]:
            session.add(Order(symbol=symbol, alpaca_order_id=symbol, side="buy",
                              order_type="limit", leg="entry", qty=3,
                              limit_price=D("100"), stop_price=D("98"), status="new",
                              created_at=before, submitted_at=before,
                              active_until=end, filled_at=filled))
        for at, equity in [(before, "11000"), (START, "10800"),
                           (START + timedelta(seconds=1), "99999")]:
            session.add(EquitySnapshot(time=at, equity=D(equity), cash=D("9000")))
        session.commit()
        state = _live_state(session, cycle, AppConfig(), {"OPEN": "tech"})
        assert [p.symbol for p in state["positions"]] == ["OPEN"]
        assert [w.symbol for w in state["working"]] == ["WORK"]
        assert state["working"][0].unfilled_qty == 3
        assert state["equity"] == D("10800")
        assert state["cash"] == D("9000")
        assert state["last_equity"] == state["peak_equity"] == D("11000")
        assert state["streak"] == 1


def test_shadow_state_isolated_book(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        strategy = Strategy(name="shadow", kind="rules_only", created_at=START)
        other = Strategy(name="other", kind="rules_only", created_at=START)
        cycle = Cycle(scheduled_time=START, status="done")
        session.add_all([strategy, other, cycle])
        session.flush()
        for owner, symbol, status, expiry in [
            (strategy, "OPEN", "open", START + timedelta(hours=1)),
            (strategy, "WORK", "pending", START + timedelta(seconds=1)),
            (strategy, "EXPIRED", "pending", START),
            (other, "OTHER", "open", START + timedelta(hours=1)),
        ]:
            session.add(ShadowPosition(
                strategy_id=owner.id, symbol=symbol, status=status, qty=2,
                entry=D("100"), stop=D("98"), initial_stop=D("98"),
                target=D("104"), limit_price=D("100"),
                opened_at=START if status == "open" else None,
                created_at=START - timedelta(minutes=1), expires_at=expiry,
            ))
        session.add(ShadowEquity(strategy_id=strategy.id, time=START,
                                 equity=D("10000"), cash=D("9000")))
        session.add(ShadowEquity(strategy_id=other.id, time=START,
                                 equity=D("99999"), cash=D("99999")))
        session.commit()
        state = _shadow_state(session, strategy, cycle, AppConfig(), {})
        assert [p.symbol for p in state["positions"]] == ["OPEN"]
        assert state["positions"][0].avg_entry_price == D("100")
        assert [w.symbol for w in state["working"]] == ["WORK"]
        assert state["equity"] == state["last_equity"] == state["peak_equity"] == D("10000")
        assert state["cash"] == D("9000")
        assert state["streak"] == 0


def test_scored_entries_and_seeded_report(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig().overlay({
        "universe": {"mode": "fixed", "sectors": {"AAA": "tech", "BBB": "finance"}},
        "risk": {"max_open_positions": 1, "risk_per_trade_pct": 0.5, "max_position_pct": 20},
        "selection": {"mode": "expected_profit", "last_slot_min_ev_r": None},
    })
    monkeypatch.setattr("app.replay_selection.AppConfig", lambda: config)
    with session_factory() as session:
        cycle = Cycle(scheduled_time=START, status="done")
        ignored = Cycle(scheduled_time=START + timedelta(hours=1), status="done")
        session.add_all([cycle, ignored])
        session.flush()
        low, high, skip = _decision(), _decision("BBB", 2, "0.9"), _decision("SKIP")
        skip.validation_result = {"model_action": "skip"}
        for decision in (low, high, skip):
            decision.cycle_id = cycle.id
            session.add(decision)
        session.add(EquitySnapshot(time=START, equity=D("10000"), cash=D("10000")))
        session.flush()
        session.add_all([Outcome(decision_id=low.id, first_touch="stop"),
                         Outcome(decision_id=high.id, first_touch="target")])
        session.commit()
        state = _live_state(session, cycle, config, {"AAA": "tech", "BBB": "finance"})
        scored, info = _scored_entries([low, high, skip], state, session, config, None)
        assert [s.rank for s in scored] == [1, 2]
        assert scored[1].expected_profit > scored[0].expected_profit
        assert info[scored[1].key].id == high.id
        # Missing trade levels, an unknown sector and an earnings block
        # must never enter the comparison even if the model said enter.
        incomplete = _decision()
        incomplete.model_output = None
        unknown = _decision("UNKNOWN")
        earnings = _decision()
        earnings.input_snapshot = {"days_to_earnings": 1}
        rejected, rejected_info = _scored_entries(
            [incomplete, unknown, earnings], state, session, config, None
        )
        assert rejected == []
        assert rejected_info == {}
        cycle_id, ignored_id = cycle.id, ignored.id
    report = replay_selection(session_factory)
    assert f"## Cycle {cycle_id} " in report
    assert f"## Cycle {ignored_id} " not in report
    assert "BBB: expected" in report
    assert "touch target → +$80.00 — expected profit pick" in report
    assert "touch stop → -$40.00 — not selected" in report
    assert "cycles where the modes differ: 1" in report
    assert "rank order outcome total: $-40.00" in report
    assert "expected-profit outcome total: $80.00" in report
