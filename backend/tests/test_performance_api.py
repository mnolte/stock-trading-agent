"""Performance endpoint: run window, aggregates, outcomes, benchmark."""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from app.db.base import get_session_maker
from app.db.models import (
    CorporateAction,
    Cycle,
    Decision,
    EquitySnapshot,
    Fill,
    Order,
    Outcome,
    Position,
    Run,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

_PERF_TABLES = (
    CorporateAction,
    EquitySnapshot,
    Position,
    Fill,
    Order,
    Outcome,
    Decision,
    Cycle,
    Run,
)


@pytest.fixture
def clean_performance(client: TestClient) -> Generator[None, None, None]:
    """Seed a controlled two-run history on this test's own database.

    Depends on ``client`` so the app has started (and possibly created its
    own run/cycle) before the tables are reset. No teardown: each test gets
    a fresh database file, so nothing can leak into another test or the
    next app boot.
    """
    with get_session_maker()() as session:
        for table in _PERF_TABLES:
            session.execute(delete(table))
        session.commit()
    yield


def _add_decision(
    session: Session,
    *,
    cycle_id: int,
    symbol: str,
    final_action: str,
    model: str = "fake-decision",
    valid: bool = True,
    no_call_reason: str | None = None,
    price: float = 100.0,
    stop: float | None = None,
    price_1session: float | None = None,
) -> Decision:
    model_output = {"action": final_action, "confidence": 0.6}
    if stop is not None:
        model_output["stop_price"] = stop
    decision = Decision(
        cycle_id=cycle_id,
        symbol=symbol,
        context="candidate",
        input_snapshot={
            "symbol": symbol,
            "price": price,
            "regime": {"trend": "up", "volatility": "normal"},
        },
        rules_result={"status": "pass", "reason": None, "checks": []},
        model_output=model_output if model != "none" else None,
        validation_result={
            "valid": valid,
            "model_action": final_action if valid else None,
            "final_action": final_action,
            "changed_by_code": False,
        },
        final_action=final_action,
        no_call_reason=no_call_reason,
        prompt_version="decision.v1",
        prompt_hash="abc",
        model=model,
    )
    session.add(decision)
    session.flush()
    outcome = Outcome(decision_id=decision.id)
    if price_1session is not None:
        outcome.price_1session = Decimal(str(price_1session))
        outcome.price_1session_at = datetime(2026, 10, 16, 14, 40)
    session.add(outcome)
    return decision


def _cycle(session: Session, scheduled: datetime) -> Cycle:
    cycle = Cycle(
        kind="cycle",
        scheduled_time=scheduled,
        started_at=scheduled,
        finished_at=scheduled,
        status="done",
    )
    session.add(cycle)
    session.flush()
    return cycle


def _seed() -> tuple[int, int]:
    """Two runs; run B carries positions, decisions, snapshots."""
    with get_session_maker()() as session:
        run_a = Run(
            started_at=datetime(2026, 10, 5, 13, 30),
            starting_equity=10000,
            spy_close=560,
        )
        run_b = Run(
            started_at=datetime(2026, 10, 12, 13, 30),
            starting_equity=10100,
            spy_close=562,
        )
        session.add_all([run_a, run_b])
        session.flush()

        for day, equity, bench in (
            (13, "10120", "10112"),
            (14, "10100", "10105"),
            (15, "10150", "10130"),
        ):
            session.add(
                EquitySnapshot(
                    time=datetime(2026, 10, day, 20, 0),
                    equity=equity,
                    cash="9000",
                    spy_benchmark_value=bench,
                    eur_usd_rate=Decimal("1.12"),
                )
            )

        cycle = _cycle(session, datetime(2026, 10, 13, 14, 46))

        # MSFT: a winner closed by target; entry decision stop 90 → R = 1.
        entry_msft = _add_decision(
            session,
            cycle_id=cycle.id,
            symbol="MSFT",
            final_action="enter",
            price=100.0,
            stop=90.0,
            price_1session=105.0,
        )
        position_msft = Position(
            symbol="MSFT",
            origin="agent",
            entry_decision_id=entry_msft.id,
            qty=4,
            avg_entry_price=100,
            current_stop_price=95,
            opened_at=datetime(2026, 10, 13, 14, 30),
            closed_at=datetime(2026, 10, 14, 18, 0),
            realized_pnl_raw=Decimal("40"),
            realized_pnl_adjusted=Decimal("39.58"),
            exit_reason="target",
        )
        session.add(position_msft)
        session.flush()

        # XOM: a loser stopped out, regime at entry down/normal.
        entry_xom = _add_decision(
            session,
            cycle_id=cycle.id,
            symbol="XOM",
            final_action="enter",
            price=200.0,
            stop=195.0,
            price_1session=198.0,
        )
        xom_dec = session.get(Decision, entry_xom.id)
        assert xom_dec is not None
        # JSON columns aren't dirty-tracked; replace the whole snapshot.
        xom_dec.input_snapshot = {
            **(xom_dec.input_snapshot or {}),
            "regime": {"trend": "down", "volatility": "normal"},
        }
        session.add(
            Position(
                symbol="XOM",
                origin="agent",
                entry_decision_id=entry_xom.id,
                qty=2,
                avg_entry_price=200,
                current_stop_price=195,
                opened_at=datetime(2026, 10, 13, 15, 30),
                closed_at=datetime(2026, 10, 14, 19, 0),
                realized_pnl_raw=Decimal("-30"),
                realized_pnl_adjusted=Decimal("-30.40"),
                exit_reason="stop",
            )
        )

        # A third enter that never filled (no filled entry order).
        _add_decision(
            session,
            cycle_id=cycle.id,
            symbol="GOOGL",
            final_action="enter",
            price=170.0,
            price_1session=170.4,
        )
        # A model skip with a 1-session outcome.
        _add_decision(
            session,
            cycle_id=cycle.id,
            symbol="QQQ",
            final_action="skip",
            price=490.0,
            price_1session=491.0,
        )
        # Rules-rejected: no call at all.
        _add_decision(
            session,
            cycle_id=cycle.id,
            symbol="NVDA",
            final_action="no_call",
            model="none",
            no_call_reason="rules",
            price=135.0,
            price_1session=136.0,
        )
        # A call with invalid output → final action is the fallback.
        _add_decision(
            session,
            cycle_id=cycle.id,
            symbol="AAPL",
            final_action="skip",
            valid=False,
            price=225.0,
        )

        # Entry fills for MSFT (buy) and its exit (sell) plus XOM exit.
        def _order(
            symbol: str, side: str, leg: str, qty: int, decision_id: int | None
        ) -> Order:
            order = Order(
                alpaca_order_id=f"ord-{symbol}-{leg}",
                client_order_id=f"sa-{symbol}-{leg}",
                symbol=symbol,
                side=side,
                order_type="limit" if leg == "entry" else "stop",
                leg=leg,
                qty=qty,
                status="filled",
                created_at=datetime(2026, 10, 13, 14, 30),
                filled_at=datetime(2026, 10, 13, 14, 30),
            )
            order.decision_id = decision_id
            session.add(order)
            session.flush()
            return order

        buy = _order("MSFT", "buy", "entry", 4, entry_msft.id)
        session.add(
            Fill(
                order_id=buy.id,
                qty=4,
                price=Decimal("100"),
                filled_at=datetime(2026, 10, 13, 14, 30),
            )
        )
        sell = _order("MSFT", "sell", "target", 4, None)
        session.add(
            Fill(
                order_id=sell.id,
                qty=4,
                price=Decimal("110"),
                filled_at=datetime(2026, 10, 14, 18, 0),
            )
        )
        buy_xom = _order("XOM", "buy", "entry", 2, entry_xom.id)
        session.add(
            Fill(
                order_id=buy_xom.id,
                qty=2,
                price=Decimal("200"),
                filled_at=datetime(2026, 10, 13, 15, 30),
            )
        )
        sell_xom = _order("XOM", "sell", "stop", 2, None)
        session.add(
            Fill(
                order_id=sell_xom.id,
                qty=2,
                price=Decimal("185"),
                filled_at=datetime(2026, 10, 14, 19, 0),
            )
        )

        # A dividend MSFT was entitled to (ex-date inside the hold).
        session.add(
            CorporateAction(
                symbol="MSFT",
                action_type="dividend",
                date=datetime(2026, 10, 14),
                ratio_or_amount=Decimal("0.50"),
            )
        )

        # Still open. Opened before "today" so an unexpected reconcile close
        # cannot register a day trade in the shared DB.
        session.add(
            Position(
                symbol="JPM",
                origin="agent",
                qty=3,
                avg_entry_price=100,
                current_stop_price=95,
                opened_at=datetime(2026, 10, 13, 14, 30),
            )
        )
        session.commit()
        return run_a.id, run_b.id


def test_performance_endpoint(client: TestClient, clean_performance: None) -> None:
    run_a, run_b = _seed()
    body = client.get("/api/performance").json()

    assert body["empty"] is False
    assert body["run"]["id"] == run_b
    assert body["run"]["sessions"] == 4  # Oct 12–15, all weekdays
    assert Decimal(body["run"]["starting_equity"]) == Decimal(10100)
    assert {r["id"] for r in body["runs"]} == {run_a, run_b}

    curve = body["curve"]
    assert curve["benchmark"] == "SPY"
    assert [p["date"] for p in curve["points"]] == [
        "2026-10-13",
        "2026-10-14",
        "2026-10-15",
    ]
    assert Decimal(curve["points"][0]["benchmark"]) == Decimal(10112)
    assert Decimal(curve["points"][0]["eur_usd"]) == Decimal("1.12")

    summary = body["summary"]
    assert summary["wins"] == 1
    assert summary["closed"] == 2
    assert summary["win_rate_pct"] == "50.00"
    # R: +40 on $40 risk = +1.00; −30 on $10 risk = −3.00 → avg −1.00.
    assert summary["avg_r"] == "-1.00"
    assert summary["kill_pct"] == "8"

    by_symbol = body["by_symbol"]
    assert [row["label"] for row in by_symbol] == ["MSFT", "XOM"]
    assert by_symbol[0]["pnl"] == "40.00"
    assert by_symbol[1]["won"] == 0

    regime = {row["label"]: row for row in body["by_regime"]}
    assert regime["Up · normal vol"]["trades"] == 1
    assert regime["Up · normal vol"]["pnl"] == "40.00"
    assert regime["Down · normal or low"]["trades"] == 1
    # Item 076: regimes without trades fold into one line, not rows.
    assert "Sideways · any" not in regime
    assert body["empty_regimes"] == [
        "Up · high vol",
        "Up · low vol",
        "Sideways · any",
        "Down · high vol",
    ]

    rows = {row["key"]: row for row in body["decision_outcomes"]}
    assert rows["enter"]["count"] == 3
    assert "1 not filled" in rows["enter"]["read"]
    # MSFT +5.0%, XOM −1.0%, GOOGL +0.2353% → avg ≈ 1.41
    assert rows["enter"]["avg_1session_pct"] is not None
    assert rows["enter"]["first_price_date"] is None
    assert rows["skip"]["count"] == 1
    assert rows["skip"]["read"].startswith("skipped setups")
    assert rows["no_call"]["count"] == 1
    assert rows["no_call"]["read"] == "no model call"
    assert rows["invalid"]["count"] == 1
    assert rows["invalid"]["read"] == "treated as skip"
    # AAPL's outcome has no 1-session price: the first lands on the next
    # trading session after its Oct 13 decision (same NY time).
    assert rows["invalid"]["avg_1session_pct"] is None
    assert rows["invalid"]["first_price_date"] == "2026-10-14"

    trades = body["closed_trades"]
    assert len(trades) == 2
    msft = next(t for t in trades if t["symbol"] == "MSFT")
    assert Decimal(msft["exit"]) == Decimal(110)  # entry + raw/qty
    assert msft["exit_label"] == "target"
    assert msft["qty"] == 4  # kept at close for the closed-trades table
    assert Decimal(msft["r"]) == Decimal("1.00")
    # adjusted = stored adjusted + dividend (0.50 × 4)
    assert Decimal(msft["adjusted"]) == Decimal("41.58")
    xom = next(t for t in trades if t["symbol"] == "XOM")
    assert xom["regime"] == "Down · normal or low"

    assert body["open"]["count"] == 1
    assert body["open"]["symbols"] == ["JPM"]

    # Item 076: the summary sentence, exits on the curve and the
    # deepest drawdown span.
    assert summary["sentence"].startswith(
        "In 4 sessions the agent closed 2 trades and won 1."
    )
    assert "against SPY's" in summary["sentence"]
    assert summary["sentence"].endswith("1 position is still open (JPM).")
    assert summary["tail_min_trades"] == 20

    exits = {(e["symbol"], e["date"]): e for e in body["exits"]}
    assert exits[("MSFT", "2026-10-14")]["r"] == "1.00"
    assert exits[("MSFT", "2026-10-14")]["won"] is True
    assert exits[("XOM", "2026-10-14")]["r"] == "-3.00"
    assert exits[("XOM", "2026-10-14")]["won"] is False

    dd = body["drawdown"]
    # 10120 on the 13th to 10100 on the 14th is the deepest fall.
    assert dd["peak_date"] == "2026-10-13"
    assert dd["low_date"] == "2026-10-14"
    assert Decimal(dd["pct"]) == Decimal(summary["max_drawdown_pct"])

    # Adjusted total: return − fill slippage + dividends.
    # Fills: 4×100 + 4×110 + 2×200 + 2×185 → 1610 × 0.05% = 0.805.
    assert summary["dividends_usd"] == "2.00"


def test_performance_earlier_run(
    client: TestClient, clean_performance: None
) -> None:
    run_a, run_b = _seed()
    body = client.get(f"/api/performance?run={run_a}").json()
    assert body["run"]["id"] == run_a
    assert body["run"]["current"] is False
    assert body["closed_trades"] == []
    assert body["decision_outcomes"] == []


def test_performance_unknown_run_404s(
    client: TestClient, clean_performance: None
) -> None:
    _seed()
    assert client.get("/api/performance?run=99999").status_code == 404


def test_summary_sentence_branches() -> None:
    """One branch per wording case (item 076): no trades, losing,
    winning, ahead of or behind SPY."""
    from app.engine.performance import summary_sentence

    base: dict[str, Any] = {
        "sessions": 9,
        "closed": 6,
        "wins": 4,
        "adjusted_usd": Decimal("171.86"),
        "adjusted_pct": Decimal("1.70"),
        "return_pct": Decimal("1.84"),
        "spy_return_pct": Decimal("1.21"),
        "open_count": 2,
        "open_symbols": ["MSFT", "JPM"],
    }
    s = summary_sentence(**base)
    assert s == (
        "In 9 sessions the agent closed 6 trades and won 4. "
        "After costs it is up $171.86 (+1.70%), and the account is "
        "+1.84% against SPY's +1.21% over the same days. "
        "2 positions are still open (MSFT, JPM)."
    )

    # Losing, behind SPY, nothing open.
    s = summary_sentence(
        **{
            **base,
            "adjusted_usd": Decimal("-22.38"),
            "adjusted_pct": Decimal("-0.22"),
            "return_pct": Decimal("-0.20"),
            "spy_return_pct": Decimal("0.50"),
            "open_count": 0,
            "open_symbols": [],
        }
    )
    assert "it is down $22.38 (−0.22%)" in s
    assert "−0.20% against SPY's +0.50%" in s
    assert "still open" not in s

    # Winning and ahead of SPY.
    s = summary_sentence(
        **{
            **base,
            "return_pct": Decimal("2.50"),
            "spy_return_pct": Decimal("1.00"),
        }
    )
    assert "+2.50% against SPY's +1.00%" in s

    # No trades yet.
    s = summary_sentence(
        **{
            **base,
            "closed": 0,
            "wins": 0,
            "adjusted_usd": Decimal("0"),
            "adjusted_pct": Decimal("0"),
        }
    )
    assert s.startswith("In 9 sessions the agent has not closed a trade yet.")
    assert "it is flat at $0.00 (0.00%)" in s

    # No SPY baseline.
    s = summary_sentence(**{**base, "spy_return_pct": None})
    assert "since the start." in s
    assert "SPY" not in s.split("After costs")[1]


def test_decision_category_model_error_beats_invalid() -> None:
    """A failed model call stores a real model name and valid=False —
    it must count as a model error in the decisions table, not as
    invalid output."""
    from app.engine.performance import _decision_category

    decision = Decision(
        cycle_id=1,
        symbol="MSFT",
        context="candidate",
        model_output=None,
        validation_result={"valid": False, "error": "timeout after 60s"},
        final_action="no_call",
        no_call_reason="model_error",
        prompt_version="decision.v1",
        prompt_hash="abc",
        model="fake-decision",
    )
    assert _decision_category(decision) == "model_error"


def test_decision_category_invalid_output_still_counts() -> None:
    """A call that returned output failing validation still counts as
    invalid — the reorder only protects the no_call bucket."""
    from app.engine.performance import _decision_category

    decision = Decision(
        cycle_id=1,
        symbol="MSFT",
        context="candidate",
        model_output=None,
        validation_result={"valid": False, "error": "bad schema"},
        final_action="skip",
        no_call_reason=None,
        prompt_version="decision.v1",
        prompt_hash="abc",
        model="fake-decision",
    )
    assert _decision_category(decision) == "invalid"
