"""The executor against the fake broker: reconciliation, adoption, exits,
kill switch — all offline and deterministic (docs/SPEC.md, Alpaca
integration + Risk management)."""

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from app.broker.fake import FakeBroker, _PosRec
from app.broker.models import BrokerAccount
from app.config import AppConfig
from app.data.factory import DataSources
from app.data.fake import (
    FakeBarSource,
    FakeCalendarSource,
    FakeCorporateActionSource,
    FakeEarningsSource,
    FakeFXSource,
    FakeNewsSource,
    FakeQuoteSource,
)
from app.data.models import Quote
from app.data.service import MarketDataService
from app.db.base import Base
from app.db.models import Cycle, DayTrade, Decision, Event, Order, Position
from app.engine.executor import Executor
from app.engine.state import ExecutionState
from app.market.calendar import CalendarProvider
from app.market.clock import UTC, FakeClock
from app.rules.prefilter import RulesResult
from app.signals.service import SignalService, SymbolEval
from app.signals.snapshot import IndicatorSnapshot
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

# Thursday 15 Oct 2026, 13:00 New York (17:00 UTC) — mid-session.
NOW = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)
YESTERDAY = datetime(2026, 10, 14, 15, 0)  # naive UTC, stored format


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _sources() -> DataSources:
    calendar = FakeCalendarSource()
    return DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar,
        alpaca=None,
    )


def _executor(
    session_factory: sessionmaker[Session],
    broker: FakeBroker,
    clock: FakeClock | None = None,
    config: AppConfig | None = None,
) -> Executor:
    clock = clock or FakeClock(NOW)
    config = config or AppConfig()
    sources = _sources()
    calendar = cast(CalendarProvider, sources.calendar)
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    signals = SignalService(market_data, calendar, config, clock, session_factory)
    return Executor(
        broker=broker,
        market_data=market_data,
        signals=signals,
        calendar=calendar,
        config=config,
        clock=clock,
        state=ExecutionState(clock=clock),
        session_factory=session_factory,
    )


def _account(
    equity: str = "10000", cash: str = "10000", last_equity: str = "10000"
) -> BrokerAccount:
    return BrokerAccount(
        account_number="PA-TEST",
        equity=Decimal(equity),
        cash=Decimal(cash),
        last_equity=Decimal(last_equity),
    )


def _decision(
    session: Session,
    symbol: str,
    final_action: str,
    model_output: dict[str, object] | None = None,
) -> Decision:
    cycle = Cycle(
        kind="cycle",
        scheduled_time=NOW.replace(tzinfo=None),
        started_at=NOW.replace(tzinfo=None),
        status="running",
    )
    session.add(cycle)
    session.flush()
    decision = Decision(
        cycle_id=cycle.id,
        symbol=symbol,
        context="candidate" if final_action == "enter" else "held",
        final_action=final_action,
        model_output=model_output,
        prompt_version="decision.v1",
        prompt_hash="0" * 64,
        model="fake",
    )
    session.add(decision)
    session.flush()
    return decision


def _snapshot(symbol: str, days_to_earnings: int | None = 9) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        symbol=symbol,
        price=Decimal("420"),
        ema_fast=Decimal("416.9"),
        ema_slow=Decimal("414.3"),
        rsi=Decimal("57"),
        atr_1h=Decimal("3.10"),
        session_vwap=Decimal("417.4"),
        obv_trend="up",
        rs_vs_spy_pct=Decimal("0.9"),
        daily_close=Decimal("415.8"),
        daily_sma_fast=Decimal("402.1"),
        daily_sma_slow=Decimal("381.6"),
        daily_atr_percentile=Decimal("48"),
        opening_gap_pct=Decimal("0.3"),
        days_to_earnings=days_to_earnings,
        earnings_available=True,
    )


def _item(symbol: str) -> SymbolEval:
    return SymbolEval(
        symbol=symbol,
        snapshot=_snapshot(symbol),
        rules=RulesResult(symbol, "pass", None, []),
        bars_1h=[],
        price=Decimal("420"),
        day_change_pct=Decimal("0.5"),
    )


def _quote(symbol: str, ask: str = "420.10", bid: str = "420.00") -> Quote:
    return Quote(
        symbol=symbol,
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=Decimal(ask),
        timestamp=NOW,
        feed="iex",
    )


def _open_position(
    session: Session,
    symbol: str = "MSFT",
    qty: int = 4,
    entry: str = "415",
    stop: str = "410",
    opened_at: datetime = YESTERDAY,
    origin: str = "agent",
) -> Position:
    position = Position(
        symbol=symbol,
        origin=origin,
        pending_ack=False,
        qty=qty,
        avg_entry_price=Decimal(entry),
        current_stop_price=Decimal(stop),
        target_price=Decimal("435"),
        opened_at=opened_at,
    )
    session.add(position)
    session.flush()
    return position


def _events(session: Session, event_type: str) -> list[Event]:
    return list(
        session.execute(
            select(Event).where(Event.event_type == event_type)
        ).scalars()
    )


# ----------------------------------------------------------------------
# adoption


async def test_reconcile_adopts_unknown_position_with_atr(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(
        FakeClock(NOW),
        cash=Decimal("10000"),
        prices={"XYZ": Decimal("100")},
    )
    broker.positions["XYZ"] = _PosRec(qty=7, avg_entry_price=Decimal("100"))
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["XYZ"]  # bars exist for it
    executor = _executor(session_factory, broker, config=config)
    # Seed enough 5m bars so the 1h ATR is available for XYZ.
    await executor.market_data.refresh_bars(NOW - timedelta(days=8), NOW)

    report = await executor.reconcile()

    assert report.ok
    assert report.adopted == ["XYZ"]
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "XYZ")
        ).scalar_one()
        assert position.origin == "adopted"
        assert position.pending_ack is True
        assert position.qty == 7
        assert position.avg_entry_price == Decimal("100")
        # 3 × 1h ATR below the current price — tighter than the 3% fallback.
        assert position.current_stop_price is not None
        assert Decimal("97") < position.current_stop_price < Decimal("100")
        events = _events(session, "reconciliation")
        assert any(e.details and e.details.get("kind") == "adopted_position" for e in events)
    # A standalone stop sits at the broker.
    stops = [
        o for o in broker.orders.values() if o.symbol == "XYZ" and o.order_type == "stop"
    ]
    assert len(stops) == 1
    assert stops[0].qty == 7
    # Entries are blocked until acknowledged.
    with session_factory() as session:
        assert executor.state.pending_ack(session)


async def test_reconcile_adopts_unknown_position_without_atr(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(
        FakeClock(NOW),
        cash=Decimal("10000"),
        prices={"XYZ": Decimal("100")},
    )
    broker.positions["XYZ"] = _PosRec(qty=7, avg_entry_price=Decimal("100"))
    executor = _executor(session_factory, broker)  # no bars → no ATR

    report = await executor.reconcile()

    assert report.adopted == ["XYZ"]
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "XYZ")
        ).scalar_one()
        # 3% fallback below the current price.
        assert position.current_stop_price == Decimal("97.00")


async def test_reconcile_adopted_position_keeps_existing_broker_stop(
    session_factory: sessionmaker[Session],
) -> None:
    """A stop already open at the broker covers the adopted position —
    no second stop is placed (the DB-rebuild case)."""
    broker = FakeBroker(
        FakeClock(NOW), cash=Decimal("10000"), prices={"XYZ": Decimal("100")}
    )
    broker.positions["XYZ"] = _PosRec(qty=7, avg_entry_price=Decimal("100"))
    # An agent stop placed before the database was rebuilt.
    await broker.submit_stop_order(
        symbol="XYZ",
        qty=7,
        stop_price=Decimal("95"),
        client_order_id="sa-protect-1-XYZ-old",
    )
    executor = _executor(session_factory, broker)

    report = await executor.reconcile()

    assert report.adopted == ["XYZ"]
    # No new stop was submitted: the existing one still sits at the broker.
    assert broker.submit_calls == 1
    stops = [
        o for o in broker.orders.values() if o.symbol == "XYZ" and o.order_type == "stop"
    ]
    assert len(stops) == 1
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "XYZ")
        ).scalar_one()
        assert position.origin == "adopted"
        assert position.pending_ack is True
        # The effective protection is the existing stop's price.
        assert position.current_stop_price == Decimal("95")
        assert not _events(session, "protective_stop_failed")


def test_client_order_ids_unique_across_processes(
    session_factory: sessionmaker[Session],
) -> None:
    """Alpaca rejects reused client_order_ids forever; the per-process
    nonce makes the same logical order unique after a restart or a
    database rebuild."""
    broker = FakeBroker(FakeClock(NOW))
    first = _executor(session_factory, broker)
    second = _executor(session_factory, broker)
    assert first._client_id("protect", 1, "TSLA") != second._client_id(
        "protect", 1, "TSLA"
    )


async def test_acknowledge_adopted_position_unblocks_entries(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"XYZ": Decimal("100")})
    broker.positions["XYZ"] = _PosRec(qty=7, avg_entry_price=Decimal("100"))
    executor = _executor(session_factory, broker)
    await executor.reconcile()
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "XYZ")
        ).scalar_one()
        position_id = position.id
        assert executor.state.pending_ack(session)

    assert executor.acknowledge_position(position_id)

    with session_factory() as session:
        refreshed = session.get(Position, position_id)
        assert refreshed is not None and refreshed.pending_ack is False
        # Blocking events are acknowledged too.
        assert not _events(session, "reconciliation") or all(
            e.acknowledged_at is not None
            for e in _events(session, "reconciliation")
        )
        assert not executor.state.pending_ack(session)


# ----------------------------------------------------------------------
# reconciliation mismatches


async def test_reconcile_qty_mismatch_corrects_from_broker(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=6, avg_entry_price=Decimal("415"))
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="415", stop="410")
        session.commit()

    report = await executor.reconcile()

    assert any("MSFT" in m for m in report.mismatches)
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert position.qty == 6  # Alpaca wins
        assert position.pending_ack is True
        assert executor.state.pending_ack(session)
        assert any(
            e.details and e.details.get("kind") == "qty_mismatch"
            for e in _events(session, "reconciliation")
        )


async def test_reconcile_closes_position_broker_no_longer_reports(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW))
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="415", stop="410")
        session.commit()

    report = await executor.reconcile()

    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert position.closed_at is not None
        assert position.exit_reason == "broker_close"
    assert any("closed at broker" in m for m in report.mismatches)


async def test_position_without_stop_gets_protective_stop(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="415", stop="410")
        session.commit()

    report = await executor.reconcile()

    assert report.protective_stops == ["MSFT"]
    stops = [
        o
        for o in broker.orders.values()
        if o.symbol == "MSFT" and o.order_type == "stop"
    ]
    assert len(stops) == 1
    assert stops[0].stop_price == Decimal("410")  # the stored stop price
    with session_factory() as session:
        assert _events(session, "protective_stop")


async def test_position_with_live_stop_needs_no_new_order(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="x"
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="415", stop="410")
        session.commit()

    report = await executor.reconcile()

    assert report.protective_stops == []
    assert len(broker.orders) == 1


# ----------------------------------------------------------------------
# entries


async def test_enter_places_bracket_order(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker)
    await executor.reconcile()
    with session_factory() as session:
        decision = _decision(
            session,
            "MSFT",
            "enter",
            {"stop_price": 410.0, "target_price": 435.0},
        )
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            None,
            _quote("MSFT"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        assert decision.final_action == "enter"
        assert decision.gates_result is not None
        assert decision.gates_result["allowed"] is True
        order = session.execute(
            select(Order).where(Order.leg == "entry")
        ).scalar_one()
        assert order.side == "buy"
        # Entry limit = IEX ask + 0.05%.
        assert order.limit_price == Decimal("420.31")
        assert order.client_order_id is not None
    # The bracket sits at the broker with both legs.
    parents = await broker.get_open_orders()
    assert len(parents.data) == 1
    assert len(parents.data[0].legs) == 2


async def test_enter_blocked_by_gate_places_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker)
    executor.state.kill_switch_on = True
    with session_factory() as session:
        decision = _decision(
            session, "MSFT", "enter", {"stop_price": 410.0, "target_price": 435.0}
        )
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            None,
            _quote("MSFT"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        assert decision.final_action == "blocked"
        assert decision.gates_result is not None
        assert decision.gates_result["blocked_by"] == "kill_switch"
    assert broker.orders == {}
    assert broker.submit_calls == 0


async def test_enter_skipped_on_wide_spread(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        decision = _decision(
            session, "MSFT", "enter", {"stop_price": 410.0, "target_price": 435.0}
        )
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            None,
            _quote("MSFT", ask="421.00", bid="419.00"),  # ~0.48% spread
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        assert decision.final_action == "blocked"
        assert decision.gates_result is not None
        assert decision.gates_result["blocked_by"] == "spread"
    assert broker.submit_calls == 0


async def test_failed_entry_write_is_never_resent(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.place_then_fail = True  # Alpaca accepted it; the reply timed out.
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        decision = _decision(
            session, "MSFT", "enter", {"stop_price": 410.0, "target_price": 435.0}
        )
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            None,
            _quote("MSFT"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        # One attempt only; the accepted order is recovered by client id.
        assert broker.submit_calls == 1
        assert _events(session, "order_recovered")
        order = session.execute(
            select(Order).where(Order.leg == "entry")
        ).scalar_one()
        assert order.alpaca_order_id is not None


async def test_entry_timeout_cancels_and_records(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    submitted = await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="sa-entry-1-MSFT-1",
    )
    executor = _executor(session_factory, broker, clock)
    with session_factory() as session:
        session.add(
            Order(
                alpaca_order_id=submitted.data.id,
                client_order_id=submitted.data.client_order_id,
                symbol="MSFT",
                decision_id=None,
                side="buy",
                order_type="limit",
                leg="entry",
                qty=4,
                limit_price=Decimal("421"),
                status="new",
                created_at=(NOW - timedelta(minutes=11)).replace(tzinfo=None),
            )
        )
        session.commit()

    report = await executor.reconcile()

    assert "MSFT" in report.entries_cancelled
    assert broker.orders[submitted.data.id].status == "canceled"
    with session_factory() as session:
        row = session.execute(
            select(Order).where(Order.leg == "entry")
        ).scalar_one()
        assert row.status == "canceled"
        events = _events(session, "entry_timeout")
        assert events and "entry not filled" in str(events[0].details)


async def test_partial_entry_fill_then_timeout_uses_filled_qty(
    session_factory: sessionmaker[Session],
) -> None:
    """A partially filled entry cancelled on timeout keeps the filled qty.

    Position qty, the stop/target legs and the stored risk all track the
    2 filled shares, not the 4 that were ordered.
    """
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    broker.partial_fill["MSFT"] = 2
    submitted = await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="sa-entry-1-MSFT-1",
    )
    executor = _executor(session_factory, broker, clock)
    with session_factory() as session:
        session.add(
            Order(
                alpaca_order_id=submitted.data.id,
                client_order_id=submitted.data.client_order_id,
                symbol="MSFT",
                decision_id=None,
                side="buy",
                order_type="limit",
                leg="entry",
                qty=4,
                limit_price=Decimal("421"),
                status="new",
                created_at=NOW.replace(tzinfo=None),
            )
        )
        session.commit()

    # The limit crosses: 2 of 4 shares fill.
    await broker.tick("MSFT", Decimal("420"))
    await executor.reconcile()

    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert position.qty == 2
        assert position.avg_entry_price == Decimal("420")
        assert position.current_stop_price == Decimal("410")
        assert position.target_price == Decimal("435")
        # Stored risk is based on the 2 filled shares.
        assert position.qty * (
            position.avg_entry_price - position.current_stop_price
        ) == Decimal("20")

    # The broker legs cover 2 shares, not 4.
    legs = [
        o for o in broker.orders.values()
        if o.symbol == "MSFT" and o.side == "sell" and o.status == "new"
    ]
    assert {leg.qty for leg in legs} == {2}
    assert len(legs) == 2

    # The entry times out: the parent is cancelled, the position stays.
    clock.advance(timedelta(minutes=11))
    report = await executor.reconcile()

    assert "MSFT" in report.entries_cancelled
    parent = broker.orders[submitted.data.id]
    assert parent.status == "canceled" and parent.filled_qty == 2
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert position.qty == 2 and position.closed_at is None
        for leg in session.execute(
            select(Order).where(
                Order.symbol == "MSFT", Order.side == "sell"
            )
        ).scalars():
            assert leg.qty == 2 and leg.status == "new"


async def test_recent_entry_is_not_timed_out(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    submitted = await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="sa-entry-1-MSFT-1",
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        session.add(
            Order(
                alpaca_order_id=submitted.data.id,
                client_order_id=submitted.data.client_order_id,
                symbol="MSFT",
                decision_id=None,
                side="buy",
                order_type="limit",
                leg="entry",
                qty=4,
                limit_price=Decimal("421"),
                status="new",
                created_at=(NOW - timedelta(minutes=5)).replace(tzinfo=None),
            )
        )
        session.commit()

    report = await executor.reconcile()
    assert report.entries_cancelled == []
    assert broker.orders[submitted.data.id].status == "new"


# ----------------------------------------------------------------------
# exits and stop moves


async def test_same_day_exit_is_deferred(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(
            session,
            "MSFT",
            opened_at=datetime(2026, 10, 15, 15, 0),  # today, 11:00 NY
        )
        decision = _decision(session, "MSFT", "exit")
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            position,
            _quote("MSFT"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        assert decision.final_action == "deferred"
        assert position.deferred_exit is not None
        assert position.deferred_exit["reason"] == "day_trade_rule"
        assert "at" in position.deferred_exit
        assert _events(session, "exit_deferred")
    assert broker.submit_calls == 0  # nothing was sold


async def test_next_day_exit_cancels_legs_then_sells(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(session, "MSFT", opened_at=YESTERDAY)
        session.add(
            Order(
                alpaca_order_id=stop.data.id,
                client_order_id="s",
                symbol="MSFT",
                decision_id=None,
                side="sell",
                order_type="stop",
                leg="stop",
                qty=4,
                stop_price=Decimal("410"),
                status="new",
                created_at=YESTERDAY,
            )
        )
        decision = _decision(session, "MSFT", "exit")
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            position,
            _quote("MSFT"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        assert decision.final_action == "exit"
        assert position.deferred_exit is None
        # The stop leg was cancelled and confirmed before the sell went out.
        assert broker.orders[stop.data.id].status == "canceled"
        exit_order = session.execute(
            select(Order).where(Order.leg == "exit")
        ).scalar_one()
        assert exit_order.side == "sell"
        assert exit_order.status == "filled"


async def test_exit_aborted_when_leg_cancel_fails(
    session_factory: sessionmaker[Session],
) -> None:
    """A failed leg cancel means no market sell and the stop stays live."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(session, "MSFT", opened_at=YESTERDAY)
        session.add(
            Order(
                alpaca_order_id=stop.data.id,
                client_order_id="s",
                symbol="MSFT",
                decision_id=None,
                side="sell",
                order_type="stop",
                leg="stop",
                qty=4,
                stop_price=Decimal("410"),
                status="new",
                created_at=YESTERDAY,
            )
        )
        session.commit()
        broker.fail_writes = True
        decision = _decision(session, "MSFT", "exit")
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            position,
            _quote("MSFT"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        events = _events(session, "order_submit_failed")
        assert events and events[0].level == "critical"
        assert position.closed_at is None
    # No market sell was ever attempted; the stop leg stayed live.
    assert list(broker.orders) == [stop.data.id]
    assert broker.orders[stop.data.id].status == "new"


async def test_exit_aborted_when_cancel_unconfirmed(
    session_factory: sessionmaker[Session],
) -> None:
    """A cancel that leaves the leg open means no market sell."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    broker.cancel_stuck = True  # cancel answers 200 but the leg stays open
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(session, "MSFT", opened_at=YESTERDAY)
        session.add(
            Order(
                alpaca_order_id=stop.data.id,
                client_order_id="s",
                symbol="MSFT",
                decision_id=None,
                side="sell",
                order_type="stop",
                leg="stop",
                qty=4,
                stop_price=Decimal("410"),
                status="new",
                created_at=YESTERDAY,
            )
        )
        session.commit()
        decision = _decision(session, "MSFT", "exit")
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            position,
            _quote("MSFT"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        events = _events(session, "leg_cancel_unconfirmed")
        assert events and events[0].level == "critical"
        assert position.closed_at is None
    assert list(broker.orders) == [stop.data.id]
    assert broker.orders[stop.data.id].status == "new"


async def test_earnings_exit_sells_held_position(
    session_factory: sessionmaker[Session],
) -> None:
    """The earnings exit is a forced exit: legs cancelled, then market sell."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(session, "MSFT", opened_at=YESTERDAY)
        session.add(
            Order(
                alpaca_order_id=stop.data.id,
                client_order_id="s",
                symbol="MSFT",
                decision_id=None,
                side="sell",
                order_type="stop",
                leg="stop",
                qty=4,
                stop_price=Decimal("410"),
                status="new",
                created_at=YESTERDAY,
            )
        )
        decision = _decision(session, "MSFT", "hold")
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            position,
            _quote("MSFT"),
            NOW,
            account=_account(),
            earnings_known=True,
            force_exit_reason="earnings_exit",
        )
        session.commit()
        assert broker.orders[stop.data.id].status == "canceled"
        assert session.execute(
            select(Order).where(Order.leg == "exit")
        ).scalar_one().status == "filled"


async def test_earnings_exit_runs_for_acknowledged_adopted_position(
    session_factory: sessionmaker[Session],
) -> None:
    """After acknowledgment an adopted position exits like an agent one."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"XYZ": Decimal("50")})
    broker.positions["XYZ"] = _PosRec(qty=12, avg_entry_price=Decimal("48"))
    executor = _executor(session_factory, broker, clock)

    await executor.reconcile()
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "XYZ")
        ).scalar_one()
        assert position.origin == "adopted" and position.pending_ack
        position_id = position.id
    assert executor.acknowledge_position(position_id)

    # Next session: an acknowledged position is a normal one — forced exits
    # (earnings exit) cancel its protective stop and sell at market.
    clock.advance(timedelta(days=1))
    with session_factory() as session:
        adopted = session.get(Position, position_id)
        assert adopted is not None
        decision = _decision(session, "XYZ", "hold")
        await executor.apply_decision(
            session,
            decision,
            _item("XYZ"),
            adopted,
            _quote("XYZ", ask="50.10", bid="50.00"),
            clock.now(),
            account=_account(),
            earnings_known=True,
            force_exit_reason="earnings_exit",
        )
        session.commit()
        assert session.execute(
            select(Order).where(Order.leg == "exit", Order.symbol == "XYZ")
        ).scalar_one().status == "filled"
    assert "XYZ" not in broker.positions


async def test_tighten_stop_moves_only_upward(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(session, "MSFT", opened_at=YESTERDAY)
        session.add(
            Order(
                alpaca_order_id=stop.data.id,
                client_order_id="s",
                symbol="MSFT",
                decision_id=None,
                side="sell",
                order_type="stop",
                leg="stop",
                qty=4,
                stop_price=Decimal("410"),
                status="new",
                created_at=YESTERDAY,
            )
        )
        # A lower stop is ignored: stops only move in the position's favour.
        lower = _decision(session, "MSFT", "tighten_stop", {"stop_price": 405.0})
        await executor.apply_decision(
            session, lower, _item("MSFT"), position, _quote("MSFT"), NOW,
            account=_account(), earnings_known=True,
        )
        assert broker.orders[stop.data.id].stop_price == Decimal("410")
        calls_before = broker.submit_calls
        higher = _decision(session, "MSFT", "tighten_stop", {"stop_price": 415.0})
        await executor.apply_decision(
            session, higher, _item("MSFT"), position, _quote("MSFT"), NOW,
            account=_account(), earnings_known=True,
        )
        session.commit()
        assert position.current_stop_price == Decimal("415")
        assert broker.submit_calls == calls_before + 1  # one replace call


# ----------------------------------------------------------------------
# PDT


async def test_same_day_round_trip_records_day_trade(
    session_factory: sessionmaker[Session],
) -> None:
    """A position closed by the broker on its open day counts as a day trade."""
    broker = FakeBroker(FakeClock(NOW))  # broker no longer reports it
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(
            session, "MSFT", opened_at=datetime(2026, 10, 15, 15, 0)
        )
        session.commit()

    await executor.reconcile()

    with session_factory() as session:
        trades = session.execute(select(DayTrade)).scalars().all()
        assert len(trades) == 1
        assert trades[0].symbol == "MSFT"


async def test_overnight_close_is_not_a_day_trade(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW))
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", opened_at=YESTERDAY)
        session.commit()

    await executor.reconcile()

    with session_factory() as session:
        assert session.execute(select(DayTrade)).scalars().all() == []


# ----------------------------------------------------------------------
# kill switch and brakes


async def test_flatten_cancels_orders_and_sells_positions(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    executor = _executor(session_factory, broker)

    result = await executor.flatten()

    assert executor.state.kill_switch_on
    assert result["sold"] == ["MSFT"]
    assert result["cancelled_orders"] == 1
    assert (await broker.get_positions()).data == []
    with session_factory() as session:
        assert _events(session, "flatten")
        assert _events(session, "kill_switch")


async def test_flatten_never_refuses_same_day_positions(
    session_factory: sessionmaker[Session],
) -> None:
    """Flatten sells positions opened today; the day-trade rule only warns."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", opened_at=NOW.replace(tzinfo=None))
        session.commit()

    result = await executor.flatten()

    assert result["sold"] == ["MSFT"]
    assert (await broker.get_positions()).data == []
    # The sale counted as a day trade — a warning goes into the event.
    with session_factory() as session:
        events = _events(session, "flatten")
        assert events[0].details is not None
        assert events[0].details["day_trade_warnings"] == ["MSFT"]


async def test_flatten_outside_hours_queues_market_on_open(
    session_factory: sessionmaker[Session],
) -> None:
    """After the close the flatten sell is queued for the open."""
    # 18:00 New York — regular hours are over.
    clock = FakeClock(datetime(2026, 10, 15, 22, 0, tzinfo=UTC))
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    executor = _executor(session_factory, broker, clock)

    result = await executor.flatten()

    assert result["market_on_open"] is True
    sell = next(
        o for o in broker.orders.values()
        if o.symbol == "MSFT" and o.order_type == "market"
    )
    assert sell.time_in_force == "opg" and sell.status == "accepted"
    # The position is still at the broker until the next open...
    positions = (await broker.get_positions()).data
    assert [p.symbol for p in positions] == ["MSFT"]
    # ...and the queued order fills when the market opens.
    await broker.tick("MSFT", Decimal("420"))
    assert (await broker.get_positions()).data == []


async def test_flatten_rejected_sell_keeps_stop_and_alerts(
    session_factory: sessionmaker[Session],
) -> None:
    """A rejected flatten sale leaves the position protected and alerts."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    broker.reject_sells.add("MSFT")
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", opened_at=YESTERDAY)
        session.commit()

    result = await executor.flatten()

    assert result["sold"] == []
    errors = result["errors"]
    assert isinstance(errors, list) and "MSFT" in errors[0]
    # The position keeps its stop: reconciliation re-armed a protective one.
    stops = [
        o for o in broker.orders.values()
        if o.symbol == "MSFT" and o.order_type == "stop" and o.status == "new"
    ]
    assert len(stops) == 1 and stops[0].qty == 4
    with session_factory() as session:
        events = _events(session, "flatten_sell_rejected")
        assert events and events[0].level == "critical"
        assert _events(session, "protective_stop")


async def test_resume_requires_a_reason(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW))
    executor = _executor(session_factory, broker)
    await executor.activate_kill_switch(reason="test", automatic=False)
    with pytest.raises(ValueError):
        await executor.resume("  ")
    assert executor.state.kill_switch_on
    await executor.resume("checked, all good")
    state = executor.state
    assert not state.kill_switch_on
    with session_factory() as session:
        events = _events(session, "kill_switch_resume")
        assert events[0].details is not None
        assert events[0].details["reason"] == "checked, all good"


async def test_daily_loss_limit_trips_the_brake(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), cash=Decimal("10000"))
    broker.cash = Decimal("9800")  # −2% vs the previous close's equity
    executor = _executor(session_factory, broker)

    await executor.update_session_brakes()

    assert executor.state.daily_loss_hit


async def test_drawdown_kill_turns_the_switch_on(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), cash=Decimal("10000"))
    broker.cash = Decimal("9800")
    executor = _executor(session_factory, broker)
    executor.state.peak_equity = Decimal("12000")  # 18.3% drawdown

    await executor.update_session_brakes()

    assert executor.state.kill_switch_on
    assert executor.state.kill_switch_auto
    with session_factory() as session:
        events = _events(session, "kill_switch")
        assert events[0].details is not None
        assert events[0].details["automatic"] is True


async def test_trade_update_updates_order_and_reconciles(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    submitted = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    executor = _executor(session_factory, broker)
    broker.subscribe_trade_updates(executor.on_trade_update)
    with session_factory() as session:
        session.add(
            Order(
                alpaca_order_id=submitted.data.id,
                client_order_id="s",
                symbol="MSFT",
                decision_id=None,
                side="sell",
                order_type="stop",
                leg="stop",
                qty=4,
                stop_price=Decimal("410"),
                status="new",
                created_at=YESTERDAY,
            )
        )
        session.commit()

    await broker.cancel_order(submitted.data.id)

    with session_factory() as session:
        row = session.execute(select(Order)).scalar_one()
        assert row.status == "canceled"
