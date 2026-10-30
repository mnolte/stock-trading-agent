"""The executor against the fake broker: reconciliation, adoption, exits,
kill switch — all offline and deterministic (docs/SPEC.md, Alpaca
integration + Risk management)."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from app.broker.fake import FakeBroker, _OrderRec, _PosRec
from app.broker.models import BrokerAccount, BrokerOrder, BrokerPosition
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
from app.data.models import Bar5m, DailyBar, DataResult, Quote
from app.data.service import MarketDataService
from app.db.models import (
    Approval,
    Cycle,
    DayTrade,
    Decision,
    Event,
    Fill,
    Order,
    Position,
)
from app.engine.executor import Executor
from app.engine.state import ExecutionState
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from app.rules.prefilter import RulesResult
from app.signals.service import SignalService, SymbolEval
from app.signals.snapshot import IndicatorSnapshot
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

# Thursday 15 Oct 2026, 13:00 New York (17:00 UTC) — mid-session.
NOW = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)
YESTERDAY = datetime(2026, 10, 14, 15, 0)  # naive UTC, stored format


def _sources(**overrides: object) -> DataSources:
    calendar = FakeCalendarSource()
    sources = DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar,
        alpaca=None,
    )
    for key, value in overrides.items():
        setattr(sources, key, value)
    return sources


def _executor(
    session_factory: sessionmaker[Session],
    broker: FakeBroker,
    clock: FakeClock | None = None,
    config: AppConfig | None = None,
    sources: DataSources | None = None,
    reference_bar: bool = True,
) -> Executor:
    clock = clock or FakeClock(NOW)
    config = config or AppConfig()
    sources = sources or _sources(quotes=FakeQuoteSource(clock))
    calendar = cast(CalendarProvider, sources.calendar)
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    signals = SignalService(market_data, calendar, config, clock, session_factory)
    executor = Executor(
        broker=broker,
        market_data=market_data,
        signals=signals,
        calendar=calendar,
        config=config,
        clock=clock,
        state=ExecutionState(clock=clock),
        session_factory=session_factory,
    )
    if reference_bar:
        for symbol, price in (broker.prices or {"MSFT": Decimal("420.10")}).items():
            _store_reference(executor, symbol, price)
    return executor


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
        daily=[],
        today_5m=[],
        price=Decimal("420"),
        day_change_pct=Decimal("0.5"),
    )


def _quote(
    symbol: str,
    ask: str = "420.10",
    bid: str = "420.00",
    last: str | None = None,
) -> Quote:
    return Quote(
        symbol=symbol,
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=Decimal(last) if last is not None else Decimal(ask),
        timestamp=NOW,
        feed="iex",
        trade_timestamp=NOW,
        fetched_at=NOW,
    )


def _store_reference(executor: Executor, symbol: str, price: Decimal) -> None:
    executor.market_data.bars_cache.store([Bar5m(
        symbol=symbol, start=NOW - timedelta(minutes=30),
        open=price, high=price, low=price, close=price, volume=100, feed="sip",
    )])


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
        assert position.initial_stop == position.current_stop_price
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
    assert first._client_id("stop", 1, "TSLA") != second._client_id(
        "stop", 1, "TSLA"
    )


async def test_crash_after_broker_accepted_stop_reconciles_without_resend(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The broker accepted the protective stop but the process died before
    the order row was written. On restart reconciliation finds the open
    order at the broker, records it, covers the adopted position — and
    never sends a second order (hard rule 3)."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, cash=Decimal("10000"), prices={"XYZ": Decimal("100")})
    broker.positions["XYZ"] = _PosRec(qty=7, avg_entry_price=Decimal("100"))
    config = AppConfig()
    executor = _executor(session_factory, broker, clock, config)

    # Crash exactly between the broker's accept and the DB write.
    real_store = Executor._store_broker_order
    fired = False

    def crash_once(
        self: Executor,
        session: Session,
        order: BrokerOrder,
        decision_id: int | None,
        *,
        leg: str | None = None,
        parent: BrokerOrder | None = None,
        new_fills: list[tuple[str, datetime | None]] | None = None,
    ) -> Order:
        nonlocal fired
        if not fired:
            fired = True
            raise RuntimeError("simulated crash after broker accept")
        return real_store(
            self, session, order, decision_id,
            leg=leg, parent=parent, new_fills=new_fills,
        )

    monkeypatch.setattr(Executor, "_store_broker_order", crash_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await executor.reconcile()
    assert broker.submit_calls == 1
    # The aftermath: position committed before the broker call (hard rule
    # 13), the broker holds the stop, the order row was never written.
    with session_factory() as session:
        assert session.execute(
            select(Position).where(Position.symbol == "XYZ")
        ).scalar_one().pending_ack is True
        assert (
            session.execute(select(func.count()).select_from(Order)).scalar_one()
            == 0
        )

    # Restart: a fresh executor over the same database and broker.
    restarted = _executor(session_factory, broker, clock, config)
    report = await restarted.reconcile()

    assert broker.submit_calls == 1  # no second stop was sent
    assert any("unknown order" in m for m in report.mismatches)
    with session_factory() as session:
        orders = session.execute(
            select(Order).where(Order.symbol == "XYZ")
        ).scalars().all()
        assert len(orders) == 1
        assert orders[0].leg == "stop"
        assert orders[0].qty == 7


async def test_crash_after_broker_accepted_entry_reconciles_without_resend(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same crash on the entry path: the broker accepted the bracket and
    the app died before recording it. Reconciliation records the entry and
    its legs from the broker's open orders; no resend, ever."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("421")})
    config = AppConfig()
    executor = _executor(session_factory, broker, clock, config)

    real_store = Executor._store_broker_order
    fired = False

    def crash_once(
        self: Executor,
        session: Session,
        order: BrokerOrder,
        decision_id: int | None,
        *,
        leg: str | None = None,
        parent: BrokerOrder | None = None,
        new_fills: list[tuple[str, datetime | None]] | None = None,
    ) -> Order:
        nonlocal fired
        if not fired:
            fired = True
            raise RuntimeError("simulated crash after broker accept")
        return real_store(
            self, session, order, decision_id,
            leg=leg, parent=parent, new_fills=new_fills,
        )

    monkeypatch.setattr(Executor, "_store_broker_order", crash_once)
    with session_factory() as session:
        decision = _decision(
            session,
            "MSFT",
            "enter",
            {"stop_price": 412.0, "target_price": 435.0},
        )
        with pytest.raises(RuntimeError, match="simulated crash"):
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
    assert broker.submit_calls == 1
    with session_factory() as session:
        assert (
            session.execute(select(func.count()).select_from(Order)).scalar_one()
            == 0
        )

    restarted = _executor(session_factory, broker, clock, config)
    report = await restarted.reconcile()

    assert broker.submit_calls == 1  # the entry is never resent
    assert any("unknown order" in m for m in report.mismatches)
    with session_factory() as session:
        orders = session.execute(
            select(Order).where(Order.symbol == "MSFT")
        ).scalars().all()
        # The bracket's legs were recorded from the broker's open orders.
        assert {o.leg for o in orders} == {"entry", "stop", "target"}


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


async def test_reconcile_never_reopens_a_closed_position(
    session_factory: sessionmaker[Session],
) -> None:
    """A closed position must not be resurrected by its old entry fill.

    Regression: reconcile used to open a fresh Position for every filled
    buy that had no *open* position, so each pass re-opened and re-closed
    the same round trip, duplicating realized P&L.
    """
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker, clock)
    with session_factory() as session:
        decision = _decision(
            session,
            "MSFT",
            "enter",
            {"stop_price": 412.0, "target_price": 435.0},
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

    # The entry fills, then the stop leg fires — a complete round trip.
    await broker.tick("MSFT", Decimal("420"))
    await executor.reconcile()
    with session_factory() as session:
        positions = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalars().all()
        assert len(positions) == 1
        assert positions[0].closed_at is None

    await broker.tick("MSFT", Decimal("409"))
    await executor.reconcile()
    with session_factory() as session:
        positions = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalars().all()
        assert len(positions) == 1
        assert positions[0].closed_at is not None
        assert positions[0].exit_reason == "stop"
        first_pnl = positions[0].realized_pnl_raw
        assert first_pnl is not None

    # Every reconcile after that: no phantom position, no P&L drift.
    report = await executor.reconcile()
    assert report.fills_recorded == 0
    with session_factory() as session:
        positions = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalars().all()
        assert len(positions) == 1
        assert positions[0].closed_at is not None
        assert positions[0].realized_pnl_raw == first_pnl


async def test_partial_fills_grow_the_open_position(
    session_factory: sessionmaker[Session],
) -> None:
    """Extra fills on a working entry grow the position, not a new row."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("421")})
    broker.partial_fill["MSFT"] = 2
    executor = _executor(session_factory, broker, clock)
    with session_factory() as session:
        decision = _decision(
            session,
            "MSFT",
            "enter",
            {"stop_price": 412.0, "target_price": 435.0},
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
        entry = session.execute(
            select(Order).where(Order.leg == "entry")
        ).scalar_one()
        assert entry.qty > 2  # a partial fill is a real subset

    await broker.tick("MSFT", Decimal("420"))
    await executor.reconcile()
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert position.qty == 2
        assert position.closed_at is None

    # The remainder fills: same position row grows to the full size.
    await broker.tick("MSFT", Decimal("419"))
    await executor.reconcile()
    with session_factory() as session:
        positions = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalars().all()
        assert len(positions) == 1
        assert positions[0].qty == entry.qty
        assert positions[0].closed_at is None


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
        position = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert position.initial_stop == Decimal("410")
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


async def test_held_bracket_stop_leg_counts_as_coverage(
    session_factory: sessionmaker[Session],
) -> None:
    """Alpaca keeps a bracket's stop leg `held` under its filled parent
    and out of the open-orders list — the leg is live and still covers
    the position. Reconcile refetches it by id; no protective stop goes
    out (Alpaca would reject it: the shares are already held)."""
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
    await broker.tick("MSFT", Decimal("420"))  # entry fills; legs go live
    stop_leg = next(
        leg for leg in submitted.data.legs if leg.order_type == "stop"
    )
    broker.orders[stop_leg.id].status = "held"  # as Alpaca leaves it
    assert stop_leg.id not in {
        order.id for order in (await broker.get_open_orders()).data
    }
    executor = _executor(session_factory, broker, clock)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="420", stop="410")
        for leg_order in submitted.data.legs:
            session.add(
                Order(
                    alpaca_order_id=leg_order.id,
                    parent_alpaca_order_id=submitted.data.id,
                    symbol="MSFT",
                    side="sell",
                    order_type=leg_order.order_type,
                    leg="stop" if leg_order.order_type == "stop" else "target",
                    qty=4,
                    limit_price=leg_order.limit_price,
                    stop_price=leg_order.stop_price,
                    status=broker.orders[leg_order.id].status,
                    created_at=YESTERDAY,
                )
            )
        session.commit()

    report = await executor.reconcile()

    assert report.ok
    assert report.protective_stops == []
    assert broker.submit_calls == 1  # only the bracket; no resend
    with session_factory() as session:
        assert not _events(session, "protective_stop")
        assert not _events(session, "protective_stop_failed")



async def test_no_protective_stop_when_sell_fills_cover_the_position(
    session_factory: sessionmaker[Session],
) -> None:
    """The stop leg already sold every share but the broker's position
    list hasn't caught up — a sell stop for shares we no longer hold
    would open a short. Reconcile skips it; the row closes next pass."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("409")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="415", stop="410")
        stop_row = Order(
            alpaca_order_id="filled-stop",
            symbol="MSFT",
            side="sell",
            order_type="stop",
            leg="stop",
            qty=4,
            stop_price=Decimal("410"),
            status="filled",
            created_at=YESTERDAY,
            filled_at=YESTERDAY,
        )
        session.add(stop_row)
        session.flush()
        session.add(
            Fill(
                order_id=stop_row.id,
                qty=4,
                price=Decimal("409.50"),
                filled_at=YESTERDAY + timedelta(hours=1),
            )
        )
        session.commit()

    report = await executor.reconcile()

    assert report.protective_stops == []
    assert not report.errors
    assert not any(
        o.symbol == "MSFT" and o.order_type == "stop" and o.status != "filled"
        for o in broker.orders.values()
    )
    with session_factory() as session:
        assert not _events(session, "protective_stop")
        assert not _events(session, "protective_stop_failed")


async def test_breached_stop_sells_at_market(
    session_factory: sessionmaker[Session],
) -> None:
    """A stored stop at or above the market was already breached — the
    repair is a market sale of the confirmed qty, reason stop_breached,
    and no new stop is placed."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("409")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="415", stop="410")
        session.commit()

    await executor.reconcile()

    orders = list(broker.orders.values())
    assert len(orders) == 1
    assert orders[0].order_type == "market"
    assert orders[0].side == "sell"
    assert orders[0].qty == 4
    assert not any(o.order_type == "stop" for o in orders)
    with session_factory() as session:
        row = session.execute(select(Order)).scalar_one()
        assert row.leg == "exit"  # legs stay entry/stop/target/exit
        assert _events(session, "protective_stop")

    # The position closes on the next pass: the sa-breach client id maps
    # the reason to stop_breached (docs/SPEC.md, Protective stops).
    await executor.reconcile()
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert position.exit_reason == "stop_breached"
        assert position.closed_at is not None


async def test_orphaned_stop_with_target_gets_oco_repair(
    session_factory: sessionmaker[Session],
) -> None:
    """The stop leg is gone while the target limit still holds the
    shares: the target is cancelled and an OCO exit order takes over,
    with the unprotected gap logged at CRITICAL."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    now = broker._now()
    rec = _OrderRec(
        id="tgt-1", client_order_id=None, symbol="MSFT", side="sell",
        order_type="limit", order_class="", status="new", qty=4,
        filled_qty=0, filled_avg_price=None, limit_price=Decimal("435"),
        stop_price=None, created_at=now, updated_at=now, filled_at=None,
    )
    broker.orders[rec.id] = rec
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="415", stop="410")
        session.commit()

    report = await executor.reconcile()

    assert report.protective_stops == ["MSFT"]
    oco = [o for o in broker.orders.values() if o.order_class == "oco"]
    assert len(oco) == 1
    assert rec.status == "canceled"  # the target was cancelled first
    with session_factory() as session:
        assert _events(session, "protective_stop")


async def test_orphan_repair_falls_back_to_plain_stop(
    session_factory: sessionmaker[Session],
) -> None:
    """An OCO rejection falls back to the stop alone — protection
    comes before the target."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    broker.reject_oco.add("MSFT")
    now = broker._now()
    rec = _OrderRec(
        id="tgt-1", client_order_id=None, symbol="MSFT", side="sell",
        order_type="limit", order_class="", status="new", qty=4,
        filled_qty=0, filled_avg_price=None, limit_price=Decimal("435"),
        stop_price=None, created_at=now, updated_at=now, filled_at=None,
    )
    broker.orders[rec.id] = rec
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="415", stop="410")
        session.commit()

    report = await executor.reconcile()

    assert report.protective_stops == ["MSFT"]
    assert not [o for o in broker.orders.values() if o.order_class == "oco"]
    stops = [o for o in broker.orders.values() if o.order_type == "stop"]
    assert len(stops) == 1
    assert stops[0].stop_price == Decimal("410")


async def test_protective_sell_capped_at_confirmed_qty(
    session_factory: sessionmaker[Session],
) -> None:
    """The DB position is larger than what Alpaca confirms — the stop
    goes out for the confirmed qty only; the agent never over-sells."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=2, avg_entry_price=Decimal("415"))
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="415", stop="410")
        session.commit()

    await executor.reconcile()

    stops = [o for o in broker.orders.values() if o.order_type == "stop"]
    assert len(stops) == 1
    assert stops[0].qty == 2


# ----------------------------------------------------------------------
# entries


async def test_enter_places_bracket_order(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    # The order-time quote comes from the source — pin it to the same
    # 420.10 ask so the limit below is exact.
    executor = _executor(
        session_factory,
        broker,
        sources=_sources(quotes=_StaticQuotes({"MSFT": _quote("MSFT")})),
    )
    await executor.reconcile()
    with session_factory() as session:
        decision = _decision(
            session,
            "MSFT",
            "enter",
            {"stop_price": 412.0, "target_price": 435.0},
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
            session, "MSFT", "enter", {"stop_price": 412.0, "target_price": 435.0}
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
            session, "MSFT", "enter", {"stop_price": 412.0, "target_price": 435.0}
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


async def test_entry_blocked_without_a_quote(
    session_factory: sessionmaker[Session],
) -> None:
    """No fresh quote → no spread/limit check possible → blocked, no order."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        decision = _decision(
            session, "MSFT", "enter", {"stop_price": 412.0, "target_price": 435.0}
        )
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            None,
            None,
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        assert decision.final_action == "blocked"
        assert (decision.gates_result or {}).get("blocked_by") == "missing_data"
    assert broker.submit_calls == 0


async def test_entry_blocked_when_quote_mid_is_zero(
    session_factory: sessionmaker[Session],
) -> None:
    """A zeroed quote must not crash the spread check with ZeroDivisionError."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        decision = _decision(
            session, "MSFT", "enter", {"stop_price": 412.0, "target_price": 435.0}
        )
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            None,
            _quote("MSFT", ask="0.00", bid="0.00"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        assert decision.final_action == "blocked"
    assert broker.submit_calls == 0


async def test_failed_entry_write_is_never_resent(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.place_then_fail = True  # Alpaca accepted it; the reply timed out.
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        decision = _decision(
            session, "MSFT", "enter", {"stop_price": 412.0, "target_price": 435.0}
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
# working entry orders in the gates (docs/SPEC.md, Risk management)


def _wide_config() -> AppConfig:
    """Sizing config where only the shared budgets can bind."""
    config = AppConfig()
    config.yaml["risk"]["max_open_positions"] = 10
    config.yaml["risk"]["risk_per_trade_pct"] = 0.505
    config.yaml["risk"]["max_per_sector"] = 10
    config.yaml["risk"]["max_position_pct"] = 100
    config.yaml["risk"]["max_order_pct"] = 100
    config.yaml["risk"]["max_orders_per_cycle"] = 10
    config.yaml["universe"]["sectors"].update({f"S{i}": "Technology" for i in range(10)})
    return config


async def _enter_symbol(
    executor: Executor,
    session: Session,
    symbol: str,
) -> Decision:
    session.commit()
    _store_reference(executor, symbol, Decimal("100"))
    executor.market_data.sources.quotes = _StaticQuotes({
        symbol: _quote(symbol, ask="100.00", bid="99.95"),
    })
    decision = _decision(
        session,
        symbol,
        "enter",
        {"stop_price": 90.0, "target_price": 120.0},
    )
    await executor.apply_decision(
        session,
        decision,
        _item(symbol),
        None,
        _quote(symbol, ask="100.00", bid="99.95"),
        NOW,
        account=_account(),
        earnings_known=True,
    )
    return decision


async def test_same_cycle_entries_share_the_open_risk_budget(
    session_factory: sessionmaker[Session],
) -> None:
    """Each entry in a cycle is gated against the budget left after the
    earlier ones: every working entry commits 5 × (100.05 − 90) ≈ $50 of
    open risk, so the fifth is sized down by what remains and the sixth
    gets zero shares."""
    broker = FakeBroker(FakeClock(NOW))
    executor = _executor(session_factory, broker, config=_wide_config())
    await executor.reconcile()
    with session_factory() as session:
        for i in range(4):
            decision = await _enter_symbol(executor, session, f"S{i}")
            assert decision.final_action == "enter", (
                f"entry S{i}: {decision.gates_result}"
            )
        # $201 committed; ~$49 room left → sized down to 4 by open_risk.
        fifth = await _enter_symbol(executor, session, "S4")
        assert fifth.final_action == "enter"
        assert fifth.gates_result is not None
        sizing = cast("dict[str, object]", fifth.gates_result["sizing"])
        assert sizing["shares"] == 4
        assert "open_risk" in str(sizing["limited_by"])
        # ~$241 committed; ~$9 room → a $10-distance entry gets nothing.
        sixth = await _enter_symbol(executor, session, "S5")
        session.commit()
        assert sixth.final_action == "blocked"
        assert sixth.gates_result is not None
        assert sixth.gates_result["blocked_by"] == "open_risk"


async def test_partially_filled_entry_counts_unfilled_only(
    session_factory: sessionmaker[Session],
) -> None:
    """2 of 4 filled: the filled part is a position, the working order
    contributes only its remaining 2 shares to the budgets."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("100")})
    broker.partial_fill["MSFT"] = 2
    executor = _executor(
        session_factory,
        broker,
        clock,
        _wide_config(),
        sources=_sources(
            quotes=_StaticQuotes(
                {
                    "MSFT": Quote(
                        symbol="MSFT",
                        bid=Decimal("99.95"),
                        ask=Decimal("100.00"),
                        last=Decimal("100.00"),
                        timestamp=NOW,
                        feed="iex",
                        trade_timestamp=NOW,
                        fetched_at=NOW,
                    )
                }
            )
        ),
    )
    await executor.reconcile()
    with session_factory() as session:
        decision = _decision(
            session,
            "MSFT",
            "enter",
            {"stop_price": 90.0, "target_price": 120.0},
        )
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            None,
            _quote("MSFT", ask="100.00", bid="99.95"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        entry = session.execute(
            select(Order).where(Order.leg == "entry")
        ).scalar_one()
        assert entry.qty == 5  # risk budget $50 / $10 distance

    await broker.tick("MSFT", Decimal("100"))
    await executor.reconcile()
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert position.qty == 2  # the filled part — never double counted
        ctx = executor._gate_context(
            session, _account(), "MSFT", 9, True
        )
        assert len(ctx.working_entries) == 1
        working = ctx.working_entries[0]
        assert working.symbol == "MSFT"
        assert working.unfilled_qty == 3
        assert working.limit_price == Decimal("100.05")
        assert working.stop_price == Decimal("90")


async def test_cancelling_a_working_entry_frees_the_budget(
    session_factory: sessionmaker[Session],
) -> None:
    """A cancelled entry stops counting: a candidate that was blocked by
    open risk passes once a working entry is cancelled."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock)
    executor = _executor(session_factory, broker, clock, _wide_config())
    await executor.reconcile()
    with session_factory() as session:
        for i in range(5):
            await _enter_symbol(executor, session, f"S{i}")
        blocked = await _enter_symbol(executor, session, "S5")
        session.commit()
        assert blocked.gates_result is not None
        assert blocked.gates_result["blocked_by"] == "open_risk"

        # Cancel S4's working entry: ~$40 of open risk frees up.
        entry = session.execute(
            select(Order).where(
                Order.leg == "entry", Order.symbol == "S4"
            )
        ).scalar_one()
        cancelled = await broker.cancel_order(entry.alpaca_order_id)
        assert cancelled.error is None
        entry.status = "canceled"
        session.commit()

        retry = _decision(
            session,
            "S5",
            "enter",
            {"stop_price": 90.0, "target_price": 120.0},
        )
        await executor.apply_decision(
            session,
            retry,
            _item("S5"),
            None,
            _quote("S5", ask="100.00", bid="99.95"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        assert retry.final_action == "enter"
        assert retry.gates_result is not None
        # ~$49 of room → 4 shares at $10 distance, limited by open_risk.
        sizing = cast("dict[str, object]", retry.gates_result["sizing"])
        assert sizing["shares"] == 4
        assert "open_risk" in str(sizing["limited_by"])


@pytest.mark.parametrize(
    "status",
    [
        "new",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "partially_filled",
        "held",
        "calculated",
        "pending_cancel",
        "pending_replace",
        "pending_review",
    ],
)
async def test_every_working_status_counts_in_the_gates(
    session_factory: sessionmaker[Session], status: str
) -> None:
    """All live Alpaca statuses — including the pending_* transitions —
    feed working_entries, _held_and_pending and the prefilter check."""
    broker = FakeBroker(FakeClock(NOW))
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        session.add(
            Order(
                alpaca_order_id=f"ord-{status}",
                client_order_id=f"c-{status}",
                symbol="MSFT",
                decision_id=None,
                side="buy",
                order_type="limit",
                leg="entry",
                qty=4,
                limit_price=Decimal("100"),
                status=status,
                created_at=NOW.replace(tzinfo=None),
            )
        )
        session.commit()
        ctx = executor._gate_context(session, _account(), "AAPL", 9, True)
        assert [e.symbol for e in ctx.working_entries] == ["MSFT"]
        held, pending = executor.signals._held_and_pending()
        assert "MSFT" in pending


# ----------------------------------------------------------------------
# exits and stop moves


async def test_same_day_exit_sells_at_once(
    session_factory: sessionmaker[Session],
) -> None:
    """A model exit on the entry day is carried out like any other —
    the day-trade deferral is retired (docs/SPEC.md, Day trades)."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(
            session,
            "MSFT",
            qty=4,
            entry="415",
            stop="410",
            opened_at=NOW.replace(tzinfo=None),
        )
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
                created_at=NOW.replace(tzinfo=None),
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
        assert broker.orders[stop.data.id].status == "canceled"
        exit_order = session.execute(
            select(Order).where(Order.leg == "exit")
        ).scalar_one()
        assert exit_order.side == "sell"
        assert exit_order.status == "filled"


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


async def test_exit_cancels_broker_leg_missing_from_db(
    session_factory: sessionmaker[Session],
) -> None:
    """A stop leg at the broker that the DB never recorded is cancelled
    and confirmed before the market sell."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(session, "MSFT", opened_at=YESTERDAY)
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
        assert decision.final_action == "exit"
        exit_order = session.execute(
            select(Order).where(Order.leg == "exit")
        ).scalar_one()
        assert exit_order.status == "filled"
    # The unrecorded stop leg was cancelled, then the market sell went in.
    assert broker.orders[stop.data.id].status == "canceled"
    sells = [o for o in broker.orders.values() if o.order_type == "market"]
    assert len(sells) == 1


async def test_exit_aborted_when_broker_leg_cancel_unconfirmed(
    session_factory: sessionmaker[Session],
) -> None:
    """An unrecorded broker leg whose cancel never lands holds the sell."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    broker.cancel_stuck = True
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(session, "MSFT", opened_at=YESTERDAY)
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
    # No market sell went past the still-live stop.
    assert all(o.order_type != "market" for o in broker.orders.values())
    assert broker.orders[stop.data.id].status == "new"


async def test_exit_cancels_working_entry_order(
    session_factory: sessionmaker[Session],
) -> None:
    """A still-working buy order for the same symbol — a partially filled
    bracket parent or a foreign order — is cancelled before the sell:
    left alone it would add shares right back after the exit."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    bracket = await broker.submit_bracket_entry(
        symbol="MSFT", qty=10, limit_price=Decimal("420"),
        stop_price=Decimal("410"), target_price=Decimal("435"),
        client_order_id="sa-entry-x",
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(session, "MSFT", opened_at=YESTERDAY)
        session.add(
            Order(
                alpaca_order_id=bracket.data.id,
                client_order_id="sa-entry-x",
                symbol="MSFT",
                decision_id=None,
                side="buy",
                order_type="limit",
                leg="entry",
                qty=10,
                limit_price=Decimal("420"),
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
        assert decision.final_action == "exit"
        entry_row = session.execute(
            select(Order).where(Order.alpaca_order_id == bracket.data.id)
        ).scalar_one()
        assert entry_row.status == "canceled"
        exit_order = session.execute(
            select(Order).where(Order.leg == "exit")
        ).scalar_one()
        assert exit_order.status == "filled"
    assert broker.orders[bracket.data.id].status == "canceled"
    sells = [o for o in broker.orders.values() if o.order_type == "market"]
    assert len(sells) == 1


async def test_exit_aborted_when_leg_state_unreadable(
    session_factory: sessionmaker[Session],
) -> None:
    """A cancel whose final state can't be verified — the read fails —
    counts as unconfirmed: no market sell goes out past it."""

    class UnreadableOrders(FakeBroker):
        async def get_order(self, order_id: str) -> DataResult[BrokerOrder | None]:
            return DataResult(data=None, error="fake read failure", source="fake")

    broker = UnreadableOrders(FakeClock(NOW), prices={"MSFT": Decimal("420")})
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
    # The leg was never cancelled and no market sell went out.
    assert all(o.order_type != "market" for o in broker.orders.values())
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


@pytest.mark.parametrize(
    ("bid", "has_bar", "atr", "reason"),
    [("100", True, Decimal("1"), None),
     ("99.6", True, Decimal("1"), "tighten_market_distance"),
     ("100", False, Decimal("1"), "reference_missing"),
     ("100", True, None, "reference_missing")],
)
async def test_tighten_rechecks_references_and_keeps_old_stop_on_failure(
    session_factory: sessionmaker[Session], bid: str, has_bar: bool,
    atr: Decimal | None, reason: str | None,
) -> None:
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("100")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("100"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("95"), client_order_id="original",
    )
    quote = Quote(
        "MSFT", Decimal(bid), Decimal("100"), Decimal("100"), NOW, "iex",
        trade_timestamp=NOW, fetched_at=NOW,
    )
    executor = _executor(
        session_factory, broker, clock,
        sources=_sources(quotes=_StaticQuotes({"MSFT": quote})), reference_bar=has_bar,
    )
    assert stop.data is not None
    with session_factory() as session:
        position = _open_position(session, entry="100", stop="95")
        executor._store_broker_order(session, stop.data, None, leg="stop")
        decision = _decision(session, "MSFT", "tighten_stop", {"stop_price": "99.5"})
        item = replace(_item("MSFT"), snapshot=replace(_snapshot("MSFT"), atr_1h=atr))
        await executor.apply_decision(
            session, decision, item, position, _quote("MSFT"), NOW,
            account=_account(), earnings_known=True,
        )
        session.commit()
        if reason is None:
            assert position.current_stop_price == Decimal("99.5")
        else:
            assert position.current_stop_price == Decimal("95")
            assert decision.gates_result is not None
            assert decision.gates_result["reason"] == reason


@pytest.mark.parametrize(
    ("quote", "reason"),
    [
        (
            Quote(
                "MSFT", Decimal("100"), Decimal("100"), Decimal("100"),
                NOW, "iex",
                trade_timestamp=NOW - timedelta(minutes=2), fetched_at=NOW,
            ),
            "price_stale",
        ),
        (
            Quote(
                "MSFT", Decimal("100"), Decimal("102"), Decimal("102"),
                NOW, "iex", trade_timestamp=NOW, fetched_at=NOW,
            ),
            "price_suspect",
        ),
    ],
)
async def test_tighten_rechecks_price_before_replacement(
    session_factory: sessionmaker[Session], quote: Quote, reason: str,
) -> None:
    """A stale or suspect market price vetoes the tighten — the position
    review itself already happened (docs/SPEC.md, tighten_stop)."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("100")})
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("95"), client_order_id="original",
    )
    executor = _executor(
        session_factory,
        broker,
        clock,
        sources=_sources(quotes=_StaticQuotes({"MSFT": quote})),
    )
    assert stop.data is not None
    with session_factory() as session:
        position = _open_position(session, entry="100", stop="95")
        executor._store_broker_order(session, stop.data, None, leg="stop")
        decision = _decision(session, "MSFT", "tighten_stop", {"stop_price": "99.5"})
        await executor.apply_decision(
            session, decision, _item("MSFT"), position, quote, NOW,
            account=_account(), earnings_known=True,
        )
        session.commit()
        assert position.current_stop_price == Decimal("95")
        assert decision.gates_result is not None
        assert decision.gates_result["reason"] == reason


async def test_tighten_ignores_implausible_low_bid(
    session_factory: sessionmaker[Session],
) -> None:
    """A resting bid 10% below the tape is not a plausible book side: the
    tighten reference is min(last trade, bar close) = 100, not 90, so the
    new stop clears the market-distance margin and the replace goes out."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("100")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("100"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("95"), client_order_id="original",
    )
    quote = Quote(
        "MSFT", Decimal("90"), Decimal("100"), Decimal("100"), NOW, "iex",
        trade_timestamp=NOW, fetched_at=NOW,
    )
    executor = _executor(
        session_factory, broker, clock,
        sources=_sources(quotes=_StaticQuotes({"MSFT": quote})),
    )
    assert stop.data is not None
    with session_factory() as session:
        position = _open_position(session, entry="100", stop="95")
        executor._store_broker_order(session, stop.data, None, leg="stop")
        decision = _decision(session, "MSFT", "tighten_stop", {"stop_price": "99.5"})
        item = replace(
            _item("MSFT"),
            snapshot=replace(_snapshot("MSFT"), atr_1h=Decimal("1")),
        )
        await executor.apply_decision(
            session, decision, item, position, quote, NOW,
            account=_account(), earnings_known=True,
        )
        session.commit()
        assert position.current_stop_price == Decimal("99.5")


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
        history = session.execute(
            select(Order).where(Order.leg == "stop").order_by(Order.id)
        ).scalars().all()
        assert [row.stop_price for row in history] == [Decimal("410"), Decimal("415")]


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
        # day_trades rows are no longer written (table kept for history)
        assert session.execute(select(DayTrade)).scalars().all() == []


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
        assert "day_trade_warnings" not in (events[0].details or {})


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


async def test_flatten_holds_sell_when_cancel_unconfirmed(
    session_factory: sessionmaker[Session],
) -> None:
    """A stop that survives cancel_all keeps the market sell held back —
    selling past a live stop could leave an unintended short."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    stop = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s"
    )
    broker.cancel_stuck = True  # cancel_all answers ok but orders stay open
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", opened_at=YESTERDAY)
        session.commit()

    result = await executor.flatten()

    assert result["sold"] == []
    assert result["blocked"] == ["MSFT"]
    errors = result["errors"]
    assert isinstance(errors, list) and any("MSFT" in e for e in errors)
    # No market sell was submitted; the protective stop is still live.
    assert all(o.order_type != "market" for o in broker.orders.values())
    assert broker.orders[stop.data.id].status == "new"
    assert [p.symbol for p in (await broker.get_positions()).data] == ["MSFT"]
    with session_factory() as session:
        events = _events(session, "flatten_sell_blocked")
        assert events and events[0].level == "critical"


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


async def test_daily_loss_latch_survives_recovery_restart_and_resets_next_ny_day(
    session_factory: sessionmaker[Session],
) -> None:
    from app.engine.state import load_execution_state
    from app.risk.gates import evaluate_entry_gates

    clock = FakeClock(NOW)
    broker = FakeBroker(clock, cash=Decimal("10000"))
    broker.cash = Decimal("9700")
    executor = _executor(session_factory, broker, clock)
    await executor.update_session_brakes()
    broker.cash = Decimal("9900")
    await executor.refresh_account()
    restarted = load_execution_state(clock, executor.config, session_factory)
    assert restarted.daily_loss_hit
    executor.state = restarted
    executor.state.paused = False
    with session_factory() as session:
        ctx = executor.budget_context(session)
        ctx = replace(ctx, symbol="MSFT")
        result = evaluate_entry_gates(
            ctx, entry=Decimal("100"), stop=Decimal("98"), config=executor.config,
        )
    assert result.blocked_by == "daily_loss_limit"
    clock.advance(timedelta(days=1))
    next_day = load_execution_state(clock, executor.config, session_factory)
    assert not next_day.daily_loss_hit


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


@pytest.mark.parametrize(
    "kind,order_type",
    [("stop", "stop"), ("target", "limit"), ("exit", "market"), ("breach", "market")],
)
async def test_exit_stream_closes_before_reconcile(
    session_factory: sessionmaker[Session], kind: str, order_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.broker.models import TradeUpdateInfo

    broker = FakeBroker(FakeClock(NOW), prices={"SBUX": Decimal("103.15")})
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "SBUX", entry="103.15")
        session.add(Order(
            alpaca_order_id="stream-exit", client_order_id=executor._client_id(kind, 1, "SBUX"),
            symbol="SBUX", side="sell", order_type=order_type, leg=kind,
            qty=4, status="new", created_at=YESTERDAY,
        ))
        session.commit()

    async def check_closed() -> object:
        with session_factory() as session:
            position = session.execute(select(Position)).scalar_one()
            assert position.closed_at is not None
            assert position.exit_reason == ("stop_breached" if kind == "breach" else kind)
            assert position.realized_pnl_raw == Decimal("0")
            assert session.execute(select(func.count(Fill.id))).scalar_one() == 1
            assert not _events(session, "reconciliation")
        return None

    monkeypatch.setattr(executor, "reconcile", check_closed)
    await executor.on_trade_update(TradeUpdateInfo(
        event="fill", order=BrokerOrder(
            id="stream-exit", client_order_id=executor._client_id(kind, 1, "SBUX"), symbol="SBUX",
            side="sell", order_type=order_type, order_class="simple", status="filled",
            qty=4, filled_qty=4, filled_avg_price=Decimal("103.15"), filled_at=NOW,
            limit_price=None, stop_price=None, created_at=NOW, updated_at=NOW,
        ), timestamp=NOW, position_qty=0, qty=4, price=Decimal("103.15"),
    ))


async def test_reconcile_closes_position_from_offline_fill(
    session_factory: sessionmaker[Session],
) -> None:
    """The app was down while the stop filled: reconcile explains it from
    Alpaca's filled orders — closed_while_offline, no banner, no block."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _open_position(session, "MSFT", entry="415")
        session.commit()
        pos_id = position.id
    rec = _OrderRec(
        id="offline-stop", client_order_id=executor._client_id("stop", pos_id, "MSFT"),
        symbol="MSFT", side="sell", order_type="stop", order_class="simple",
        status="filled", qty=4, filled_qty=4, filled_avg_price=Decimal("410"),
        limit_price=None, stop_price=Decimal("410"), created_at=YESTERDAY,
        updated_at=NOW, filled_at=NOW,
    )
    broker.orders[rec.id] = rec

    report = await executor.reconcile()

    assert not report.mismatches
    with session_factory() as session:
        closed = session.get(Position, pos_id)
        assert closed is not None
        assert closed.closed_at is not None
        assert closed.closed_while_offline is True
        assert closed.exit_reason == "stop"
        assert closed.realized_pnl_raw == Decimal("-20")
        assert not _events(session, "reconciliation")
        assert not executor.state.pending_ack(session)


async def test_reconcile_flat_without_fill_is_mismatch(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT")
        session.commit()

    report = await executor.reconcile()

    assert any("closed at broker" in m for m in report.mismatches)
    with session_factory() as session:
        events = _events(session, "reconciliation")
        assert len(events) == 1
        assert events[0].details is not None
        assert events[0].details["kind"] == "flat_without_fill"
        assert executor.state.pending_ack(session)


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
        # A terminal update ends the order's live window — without it the
        # leg would count as live for every later bar in outcome metrics.
        assert row.active_until is not None


# ----------------------------------------------------------------------
# paused (docs/SPEC.md, Risk management, Paused at startup)


async def test_paused_blocks_new_entry(
    session_factory: sessionmaker[Session],
) -> None:
    """While paused the model's enter becomes blocked — nothing is sent."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker)
    executor.state.paused = True
    with session_factory() as session:
        decision = _decision(
            session, "MSFT", "enter", {"stop_price": 412.0, "target_price": 435.0}
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
        assert decision.gates_result["blocked_by"] == "paused"
    assert broker.orders == {}
    assert broker.submit_calls == 0


async def test_paused_position_can_still_exit(
    session_factory: sessionmaker[Session],
) -> None:
    """Held positions keep working while paused: a model exit sells."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    executor = _executor(session_factory, broker)
    executor.state.paused = True
    with session_factory() as session:
        position = _open_position(session, "MSFT", opened_at=YESTERDAY)
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
        exit_order = session.execute(
            select(Order).where(Order.leg == "exit")
        ).scalar_one()
        assert exit_order.side == "sell"


async def test_pause_and_resume_record_events(
    session_factory: sessionmaker[Session],
) -> None:
    """Pause and resume are stored; resume needs no reason."""
    broker = FakeBroker(FakeClock(NOW))
    executor = _executor(session_factory, broker)
    state = executor.state

    paused = state.paused
    assert not paused

    await executor.pause()
    paused = state.paused
    paused_since = state.paused_since
    at_startup = state.paused_at_startup
    assert paused
    assert paused_since is not None
    assert not at_startup  # a manual pause

    await executor.resume_from_pause()  # one click, no reason
    paused = state.paused
    paused_since = state.paused_since
    assert not paused
    assert paused_since is None

    with session_factory() as session:
        events = _events(session, "pause")
    assert [e.details["on"] for e in events if e.details] == [True, False]


# ----------------------------------------------------------------------
# session safety brakes (B1)


class _StaticQuotes:
    """Fixed quotes keyed by symbol — deterministic brake inputs."""

    def __init__(self, quotes: dict[str, Quote]) -> None:
        self._quotes = quotes

    async def fetch_quotes(
        self, symbols: list[str]
    ) -> DataResult[dict[str, Quote]]:
        return DataResult(
            data={s: self._quotes[s] for s in symbols if s in self._quotes},
            source="fake",
        )


class _NoBars:
    """A bar source that never returns data — only the cache matters."""

    async def fetch_5m_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> DataResult[list[Bar5m]]:
        return DataResult(data=[], source="fake")

    async def fetch_daily_bars(
        self, symbol: str, start: datetime, end: datetime
    ) -> DataResult[list[DailyBar]]:
        return DataResult(data=[], source="fake")


def _spy_5m_bars(
    start: datetime, count: int, open_: Decimal, close: Decimal
) -> list[Bar5m]:
    """``count`` consecutive 5m bars drifting evenly from ``open_`` to ``close``."""
    step = (close - open_) / count
    bars = []
    price = open_
    for i in range(count):
        nxt = price + step
        bars.append(
            Bar5m(
                symbol="SPY",
                start=start + timedelta(minutes=5 * i),
                open=price,
                high=max(price, nxt),
                low=min(price, nxt),
                close=nxt,
                volume=1000,
                feed="fake",
            )
        )
        price = nxt
    return bars


async def test_shock_brake_session_uses_fresh_spy_quote(
    session_factory: sessionmaker[Session],
) -> None:
    """A mid-session SPY crash trips the brake via the real-time quote,
    even though every cached bar is flat."""
    crash_quote = Quote(
        symbol="SPY",
        bid=Decimal("549"),
        ask=Decimal("550"),
        last=Decimal("550"),  # ~-5% below the session open (~580)
        timestamp=NOW,
        feed="iex",
    )
    sources = _sources(quotes=_StaticQuotes({"SPY": crash_quote}))
    executor = _executor(session_factory, FakeBroker(FakeClock(NOW)), sources=sources)

    await executor.update_session_brakes()  # position-check mode

    assert executor.state.shock_brake_session is True
    assert executor.state.shock_brake_1h is False  # 1h leg is cycle-only


async def test_shock_brake_session_quiet_quote_no_trip(
    session_factory: sessionmaker[Session],
) -> None:
    calm_quote = Quote(
        symbol="SPY",
        bid=Decimal("579"),
        ask=Decimal("580"),
        last=Decimal("580"),
        timestamp=NOW,
        feed="iex",
    )
    sources = _sources(quotes=_StaticQuotes({"SPY": calm_quote}))
    executor = _executor(session_factory, FakeBroker(FakeClock(NOW)), sources=sources)

    await executor.update_session_brakes()

    assert executor.state.shock_brake_session is False
    assert executor.state.shock_brake_1h is False


async def test_shock_brake_session_ignores_missing_last_trade(
    session_factory: sessionmaker[Session],
) -> None:
    """A quote without a last trade (last = 0) is not a −100 % crash —
    the session leg of the brake must not trip on it."""
    no_last_quote = Quote(
        symbol="SPY",
        bid=Decimal("579"),
        ask=Decimal("580"),
        last=Decimal("0"),
        timestamp=NOW,
        feed="iex",
    )
    sources = _sources(quotes=_StaticQuotes({"SPY": no_last_quote}))
    executor = _executor(session_factory, FakeBroker(FakeClock(NOW)), sources=sources)

    await executor.update_session_brakes()

    assert executor.state.shock_brake_session is False
    assert executor.state.shock_brake_1h is False


async def test_shock_brake_1h_runs_only_with_flag(
    session_factory: sessionmaker[Session],
) -> None:
    """The last complete 1h bar is down > 1.5% — the 1h brake trips only
    when ``include_1h`` is set (decision cycles, not position checks)."""
    session = FakeCalendarSource().session_for(NOW.astimezone(NY_TZ).date())
    assert session is not None
    bars = []
    # 09:30-10:30 and 10:30-11:30 windows: complete, flat.
    for i in range(2):
        bars += _spy_5m_bars(
            session.open + timedelta(hours=i), 12, Decimal("580"), Decimal("580")
        )
    # 11:30-12:30 window: complete bar, -1.72% (past the -1.5% threshold).
    bars += _spy_5m_bars(
        session.open + timedelta(hours=2), 12, Decimal("580"), Decimal("570")
    )
    sources = _sources(bars=_NoBars())
    executor = _executor(session_factory, FakeBroker(FakeClock(NOW)), sources=sources)
    executor.market_data.bars_cache.store(bars)

    await executor.update_session_brakes()  # position-check mode
    assert executor.state.shock_brake_1h is False

    await executor.update_session_brakes(include_1h=True)  # cycle mode
    assert executor.state.shock_brake_1h is True


async def test_shock_brake_1h_ignores_partial_bar(
    session_factory: sessionmaker[Session],
) -> None:
    """A still-forming 1h window (fewer than 12 cached 5m bars) must not be
    read as 'the last 1h bar' — even after its window end has passed."""
    session = FakeCalendarSource().session_for(NOW.astimezone(NY_TZ).date())
    assert session is not None
    bars = []
    # 09:30-10:30 and 10:30-11:30 windows: complete, flat.
    for i in range(2):
        bars += _spy_5m_bars(
            session.open + timedelta(hours=i), 12, Decimal("580"), Decimal("580")
        )
    # 11:30-12:30 window: only 8 of 12 bars cached (SIP gap), but a crash.
    bars += _spy_5m_bars(
        session.open + timedelta(hours=2), 8, Decimal("580"), Decimal("540")
    )
    sources = _sources(bars=_NoBars())
    executor = _executor(session_factory, FakeBroker(FakeClock(NOW)), sources=sources)
    executor.market_data.bars_cache.store(bars)

    await executor.update_session_brakes(include_1h=True)

    assert executor.state.shock_brake_1h is False


# ----------------------------------------------------------------------
# fresh-quote gate before order submission (S3)


class _AgedQuotes:
    """Quotes whose last trade is stamped ``age`` seconds behind the
    executor clock."""

    def __init__(
        self,
        clock: FakeClock,
        age: int,
        ask: str = "420.10",
        bid: str = "420.00",
        last: str | None = None,
    ) -> None:
        self._clock = clock
        self._age = age
        self._ask = Decimal(ask)
        self._bid = Decimal(bid)
        self._last = Decimal(last) if last is not None else self._ask

    async def fetch_quotes(
        self, symbols: list[str]
    ) -> DataResult[dict[str, Quote]]:
        ts = self._clock.now() - timedelta(seconds=self._age)
        return DataResult(
            data={
                s: Quote(
                    symbol=s,
                    bid=self._bid,
                    ask=self._ask,
                    last=self._last,
                    timestamp=ts,
                    feed="iex",
                    trade_timestamp=ts,
                    fetched_at=self._clock.now(),
                )
                for s in symbols
            },
            source="fake",
        )


class _DownQuotes:
    """The quote feed is unavailable."""

    async def fetch_quotes(
        self, symbols: list[str]
    ) -> DataResult[dict[str, Quote] | None]:
        return DataResult(data=None, error="quote feed down", source="fake")


async def _apply_enter(executor: Executor, session: Session) -> Decision:
    session.commit()
    _store_reference(executor, "MSFT", Decimal("420.10"))
    decision = _decision(
        session,
        "MSFT",
        "enter",
        {"stop_price": 412.0, "target_price": 435.0},
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
    return decision


@pytest.mark.parametrize(("ask", "expected_qty"), [("100", 19), ("101", 16)])
@pytest.mark.parametrize("mode", ["automatic", "supervised"])
async def test_entry_sizes_against_final_limit(
    session_factory: sessionmaker[Session], ask: str, expected_qty: int,
    mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("100")})
    executor = _executor(
        session_factory, broker, clock,
        sources=_sources(quotes=_AgedQuotes(clock, 0, ask=ask, bid=ask)),
    )
    executor.config.yaml["execution"]["mode"] = mode
    monkeypatch.setattr(executor, "_atr_1h", lambda _symbol: Decimal("1"))
    _store_reference(executor, "MSFT", Decimal(ask))
    with session_factory() as session:
        decision = _decision(session, "MSFT", "enter", {
            "stop_price": "98", "target_price": "108",
        })
        item = replace(_item("MSFT"), snapshot=replace(_snapshot("MSFT"), atr_1h=Decimal("1")))
        await executor.apply_decision(
            session, decision, item, None,
            Quote("MSFT", Decimal("100"), Decimal("100"), Decimal("100"), NOW, "iex"),
            NOW, account=_account(), earnings_known=True,
        )
        session.commit()
        if mode == "supervised":
            approval = session.execute(select(Approval)).scalar_one()
            result = await executor.approve(approval.id)
            assert result["ok"] is True
            session.expire_all()
        order = session.execute(select(Order).where(Order.side == "buy")).scalar_one()
        assert order.qty == expected_qty
        assert order.limit_price is not None
        assert order.qty * order.limit_price <= Decimal("2000")
        assert order.qty * (order.limit_price - Decimal("98")) <= Decimal("50")
        assert decision.gates_result is not None
        sizing = cast("dict[str, object]", decision.gates_result["sizing"])
        assert sizing["shares"] == order.qty


async def test_fresh_entry_stop_distance_uses_lowest_reference(
    session_factory: sessionmaker[Session],
) -> None:
    """The stop-distance check runs against the lowest of market price,
    plausible ask and last closed bar — here the bar close (99) drags the
    reference below the 1.5 × ATR minimum even though the limit (99.55)
    itself is far enough from the stop."""
    clock = FakeClock(NOW)
    executor = _executor(
        session_factory, FakeBroker(clock), clock,
        sources=_sources(
            quotes=_AgedQuotes(clock, 0, ask="99.5", bid="99.5", last="99.5")
        ),
        reference_bar=False,
    )
    _store_reference(executor, "MSFT", Decimal("99"))
    _, limit, reason = await executor._fresh_entry_check(
        symbol="MSFT", stop=Decimal("98"), target=Decimal("108"), atr_1h=Decimal("1"),
    )
    assert limit is None
    assert reason == "stop_market_distance"


@pytest.mark.parametrize(
    ("missing_last", "has_bar", "atr", "expected"),
    [(True, True, Decimal("1"), "price_stale"),
     (False, False, Decimal("1"), "reference_missing"),
     (False, True, None, "reference_missing"),
     (False, True, Decimal("1"), None)],
)
async def test_fresh_entry_market_price_and_required_references(
    session_factory: sessionmaker[Session], missing_last: bool, has_bar: bool,
    atr: Decimal | None, expected: str | None,
) -> None:
    """The last trade IS the market price — it is required, like the bar
    and ATR for the stop-distance reference (docs/SPEC.md, Entries)."""
    clock = FakeClock(NOW)

    class Quotes(_AgedQuotes):
        async def fetch_quotes(self, symbols: list[str]) -> DataResult[dict[str, Quote]]:
            result = await super().fetch_quotes(symbols)
            if not missing_last:
                return result
            return DataResult(
                data={s: replace(q, last=Decimal("0")) for s, q in result.data.items()}
            )

    executor = _executor(
        session_factory, FakeBroker(clock), clock,
        sources=_sources(quotes=Quotes(clock, 0, ask="100", bid="100")),
        reference_bar=False,
    )
    if has_bar:
        _store_reference(executor, "MSFT", Decimal("100"))
    quote, limit, reason = await executor._fresh_entry_check(
        symbol="MSFT", stop=Decimal("98"), target=Decimal("108"), atr_1h=atr,
    )
    assert reason == expected
    assert (limit is None) == (expected is not None)
    if expected is None:
        assert quote is not None
        assert quote.last == Decimal("100")
        assert limit == Decimal("100.05")


async def test_fresh_entry_thin_book_keeps_entry_and_limit_from_last_trade(
    session_factory: sessionmaker[Session],
) -> None:
    """An IEX ask 3% above the tape makes the book implausible: the entry
    is NOT blocked (spread_unknown), the spread gate is skipped, and the
    limit is the last trade plus the configured offset — not the ask."""
    clock = FakeClock(NOW)
    executor = _executor(
        session_factory, FakeBroker(clock), clock,
        sources=_sources(
            quotes=_AgedQuotes(clock, 0, ask="103", bid="99.5", last="100")
        ),
        reference_bar=False,
    )
    _store_reference(executor, "MSFT", Decimal("100"))
    quote, limit, reason = await executor._fresh_entry_check(
        symbol="MSFT", stop=Decimal("98"), target=Decimal("108"), atr_1h=Decimal("1"),
    )
    assert reason is None
    assert quote is not None
    assert quote.quote_plausible(Decimal("1")) is False
    assert limit == Decimal("100.05")


async def test_entry_does_not_use_old_budgets_when_account_refresh_fails(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    broker.fail_reads = True
    executor = _executor(
        session_factory, broker, clock, sources=_sources(quotes=_AgedQuotes(clock, 0)),
    )
    executor.state.account = _account()
    with session_factory() as session:
        decision = await _apply_enter(executor, session)
        assert decision.final_action == "blocked"
        assert decision.gates_result is not None
        assert decision.gates_result["blocked_by"] == "account_unavailable"
    assert broker.submit_calls == 0


async def test_fresh_quote_gate_rejects_stale_quote(
    session_factory: sessionmaker[Session],
) -> None:
    """A quote 61s old at order time fails the Fresh quote gate — no order."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    executor = _executor(
        session_factory,
        broker,
        clock,
        sources=_sources(quotes=_AgedQuotes(clock, 61)),
    )
    with session_factory() as session:
        decision = await _apply_enter(executor, session)
        session.commit()
        assert decision.final_action == "blocked"
        assert decision.gates_result is not None
        assert decision.gates_result["blocked_by"] == "fresh_quote"
        checks = cast("list[dict[str, object]]", decision.gates_result["checks"])
        assert checks[-1]["name"] == "fresh_quote"
        assert checks[-1]["detail"] == "price_stale"
        assert decision.gates_result["quote"] is not None
    assert broker.submit_calls == 0


async def test_fresh_quote_gate_accepts_quote_within_max_age(
    session_factory: sessionmaker[Session],
) -> None:
    """A quote 59s old is still fresh — the order goes out."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    executor = _executor(
        session_factory,
        broker,
        clock,
        sources=_sources(quotes=_AgedQuotes(clock, 59)),
    )
    with session_factory() as session:
        decision = await _apply_enter(executor, session)
        session.commit()
        assert decision.final_action == "enter"
    assert broker.submit_calls == 1


async def test_fresh_quote_gate_rejects_fetch_failure(
    session_factory: sessionmaker[Session],
) -> None:
    """No quote at order time → no market price → price_stale, no order sent."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(
        session_factory,
        broker,
        sources=_sources(quotes=_DownQuotes()),
    )
    with session_factory() as session:
        decision = await _apply_enter(executor, session)
        session.commit()
        assert decision.final_action == "blocked"
        assert decision.gates_result is not None
        assert decision.gates_result["blocked_by"] == "fresh_quote"
        checks = cast("list[dict[str, object]]", decision.gates_result["checks"])
        assert checks[-1]["detail"] == "price_stale"
    assert broker.submit_calls == 0


async def test_fresh_quote_gate_rejects_when_fresh_price_breaks_rr(
    session_factory: sessionmaker[Session],
) -> None:
    """The fresh ask jumped: reward to risk falls below 1.5 — no order."""
    # limit = 421.30 * 1.0005 ≈ 421.51 → distance 9.51 (3.1 × ATR ok),
    # reward 13.49 → rr 1.42 < 1.5.
    clock = FakeClock(NOW)
    executor = _executor(
        session_factory,
        FakeBroker(clock, prices={"MSFT": Decimal("420")}),
        clock,
        sources=_sources(quotes=_AgedQuotes(clock, 0, ask="421.30", bid="421.20")),
    )
    with session_factory() as session:
        decision = await _apply_enter(executor, session)
        session.commit()
        assert decision.final_action == "blocked"
        assert decision.gates_result is not None
        assert decision.gates_result["blocked_by"] == "fresh_quote"
        checks = cast("list[dict[str, object]]", decision.gates_result["checks"])
        assert "reward to risk" in str(checks[-1]["detail"])


async def test_enter_and_approve_share_the_fresh_quote_step(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both paths call the same ``_fresh_entry_check``."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker, clock)
    calls: list[str] = []
    real = executor._fresh_entry_check

    async def spy(
        *, symbol: str, stop: Decimal, target: Decimal, atr_1h: Decimal | None
    ) -> object:
        calls.append(symbol)
        return await real(symbol=symbol, stop=stop, target=target, atr_1h=atr_1h)

    monkeypatch.setattr(executor, "_fresh_entry_check", spy)

    with session_factory() as session:
        decision = await _apply_enter(executor, session)
        session.commit()
        assert decision.final_action == "enter"
    assert calls == ["MSFT"]
    assert broker.submit_calls == 1


async def test_v2_order_time_recheck_fails_fresh_quote(
    session_factory: sessionmaker[Session],
) -> None:
    """decision.v2: the fresh-price recheck fails the Fresh quote gate with
    the reason, and code never picks another option (docs/SPEC.md)."""
    clock = FakeClock(NOW)
    executor = _executor(
        session_factory,
        FakeBroker(clock, prices={"MSFT": Decimal("420")}),
        clock,
        sources=_sources(quotes=_AgedQuotes(clock, 0, ask="421.30", bid="421.20")),
    )
    with session_factory() as session:
        session.commit()
        _store_reference(executor, "MSFT", Decimal("420.10"))
        decision = _decision(
            session,
            "MSFT",
            "enter",
            # v2 output carries no prices — only the chosen option id.
            {"action": "enter", "option_id": "A", "confidence": 0.62},
        )
        decision.prompt_version = "decision.v2"
        decision.chosen_option = "A"
        decision.options = {
            "options_version": "v2",
            "options": [
                {"id": "A", "stop": 412.0, "target": 435.0, "rr": 1.42},
                {"id": "B", "stop": 410.0, "target": 450.0, "rr": 2.5},
            ],
        }
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
        assert decision.gates_result["blocked_by"] == "fresh_quote"
        checks = cast("list[dict[str, object]]", decision.gates_result["checks"])
        assert "reward to risk" in str(checks[-1]["detail"])
        # The chosen option stands; nothing swapped in its place.
        assert decision.chosen_option == "A"
        assert "option_id" in (decision.model_output or {})


# ----------------------------------------------------------------------
# trade-updates stream health (docs/SPEC.md, Alpaca integration)


async def test_unparseable_update_marks_stream_degraded(
    session_factory: sessionmaker[Session],
) -> None:
    """One failure: counted, the critical event recorded once, the
    stream marked degraded (banner + notification ride on that)."""
    executor = _executor(session_factory, FakeBroker(FakeClock(NOW)))
    await executor.on_stream_failure("KeyError('id')")
    assert executor.state.stream_degraded
    assert executor.state.stream_failures == 1
    with session_factory() as session:
        events = _events(session, "stream_degraded")
        assert len(events) == 1
        assert events[0].level == "critical"


async def test_repeat_failures_notify_once_per_episode(
    session_factory: sessionmaker[Session],
) -> None:
    """Every failure counts and extends the window; the alert fires
    once per degradation episode, not per bad message."""
    executor = _executor(session_factory, FakeBroker(FakeClock(NOW)))
    await executor.on_stream_failure("bad 1")
    await executor.on_stream_failure("bad 2")
    assert executor.state.stream_failures == 2
    with session_factory() as session:
        assert len(_events(session, "stream_degraded")) == 1


async def test_degraded_clears_after_quiet_window(
    session_factory: sessionmaker[Session],
) -> None:
    """Degraded clears 30 minutes after the last failure."""
    clock = FakeClock(NOW)
    executor = _executor(session_factory, FakeBroker(clock), clock=clock)
    await executor.on_stream_failure("bad message")
    assert executor.state.stream_degraded
    clock.advance(timedelta(minutes=29))
    assert executor.state.stream_degraded
    clock.advance(timedelta(minutes=2))
    assert not executor.state.stream_degraded


async def test_reconcile_missed_fill_marks_degraded(
    session_factory: sessionmaker[Session],
) -> None:
    """A fill reconciliation records for the first time — the stream
    update never arrived within the grace window — marks degraded."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("410"))
    now = broker._now()
    rec = _OrderRec(
        id="fill-1", client_order_id=None, symbol="MSFT", side="buy",
        order_type="limit", order_class="", status="filled", qty=4,
        filled_qty=4, filled_avg_price=Decimal("410"),
        limit_price=Decimal("410"), stop_price=None,
        created_at=now, updated_at=now - timedelta(minutes=5),
        filled_at=now - timedelta(minutes=5),
    )
    broker.orders[rec.id] = rec
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        session.add(
            Order(
                alpaca_order_id="fill-1",
                symbol="MSFT",
                side="buy",
                order_type="limit",
                leg="entry",
                qty=4,
                limit_price=Decimal("410"),
                status="new",
                created_at=YESTERDAY,
            )
        )
        session.commit()

    await executor.reconcile()

    assert executor.state.stream_degraded
    assert executor.state.stream_failures == 1
    with session_factory() as session:
        assert _events(session, "stream_degraded")


async def test_reconcile_fresh_fill_waits_out_grace(
    session_factory: sessionmaker[Session],
) -> None:
    """A fill recorded seconds ago may still have its update in flight:
    no degraded mark inside the grace window, but the pending fill is
    flagged at the next pass once the window has passed."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("410"))
    rec = _OrderRec(
        id="fill-1", client_order_id=None, symbol="MSFT", side="buy",
        order_type="limit", order_class="", status="filled", qty=4,
        filled_qty=4, filled_avg_price=Decimal("410"),
        limit_price=Decimal("410"), stop_price=None,
        created_at=NOW, updated_at=NOW,
        filled_at=NOW - timedelta(seconds=30),
    )
    broker.orders[rec.id] = rec
    executor = _executor(session_factory, broker, clock=clock)
    with session_factory() as session:
        session.add(
            Order(
                alpaca_order_id="fill-1",
                symbol="MSFT",
                side="buy",
                order_type="limit",
                leg="entry",
                qty=4,
                limit_price=Decimal("410"),
                status="new",
                created_at=YESTERDAY,
            )
        )
        session.commit()

    await executor.reconcile()
    assert not executor.state.stream_degraded
    assert "fill-1" in executor._fills_awaiting_update

    clock.advance(timedelta(minutes=3))
    await executor.reconcile()
    assert executor.state.stream_degraded


def test_stream_degraded_event_maps_to_notification() -> None:
    """The critical event becomes the "Trade-updates stream degraded"
    notification through the event sink."""
    from app.notifications.events import EVENTS, format_recorded

    assert EVENTS["stream_degraded"].severity == "critical"
    mapped = format_recorded(
        "stream_degraded", {"reason": "unparseable update"}
    )
    assert mapped == ("stream_degraded", "unparseable update", "/positions")


async def test_flat_symbol_is_not_refetched_within_a_pass(
    session_factory: sessionmaker[Session],
) -> None:
    """A position found flat at the broker isn't asked for again in the
    same pass (the 29 Oct RTX 404 burst); the next pass asks fresh."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    calls: list[str] = []
    real_get_position = broker.get_position

    async def spy(symbol: str) -> DataResult[BrokerPosition | None]:
        calls.append(symbol)
        return await real_get_position(symbol)

    broker.get_position = spy  # type: ignore[method-assign]
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _open_position(session, "MSFT", qty=4, entry="415", stop="410")
        session.commit()

    report = await executor.reconcile()
    calls.clear()

    assert "MSFT" in executor._flat_symbols
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        placed = await executor._place_protective_stop(
            session, position, None, report
        )
    # The flat marker answered it — no second broker ask this pass.
    assert placed == Decimal(0)
    assert calls == []


class _Notices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify(self, event: str, line: str, **kwargs: object) -> None:
        self.calls.append((event, line))


async def _seed_entry_row(session, submitted, symbol: str, qty: int, limit: str) -> None:
    session.add(
        Order(
            alpaca_order_id=submitted.data.id,
            client_order_id=submitted.data.client_order_id,
            symbol=symbol,
            decision_id=None,
            side="buy",
            order_type="limit",
            leg="entry",
            qty=qty,
            limit_price=Decimal(limit),
            status="new",
            created_at=NOW.replace(tzinfo=None),
        )
    )
    session.commit()


async def test_opened_notification_once_on_final_fill(
    session_factory: sessionmaker[Session],
) -> None:
    """PG of 30 Oct: prints arriving over seconds give one "opened"
    notification with the filled quantity, not one per partial fill."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"PG": Decimal("162.90")})
    broker.partial_fill["PG"] = 4
    submitted = await broker.submit_bracket_entry(
        symbol="PG",
        qty=12,
        limit_price=Decimal("162.90"),
        stop_price=Decimal("158.00"),
        target_price=Decimal("170.00"),
        client_order_id="sa-entry-1-PG-1",
    )
    executor = _executor(session_factory, broker, clock)
    notices = _Notices()
    executor._notifier = notices
    with session_factory() as session:
        await _seed_entry_row(session, submitted, "PG", 12, "162.90")

    # Four prints: 4, 8, 12 — the first two are still working.
    for _ in range(2):
        await broker.tick("PG", Decimal("162.85"))
        await executor.reconcile()
        assert [c for c in notices.calls if c[0] == "trade"] == []

    await broker.tick("PG", Decimal("162.85"))
    await executor.reconcile()
    trades = [c for c in notices.calls if c[0] == "trade"]
    assert trades == [("trade", "opened PG: 12 shares @ 162.85")]

    # Later reconciles never repeat it.
    await executor.reconcile()
    assert [c for c in notices.calls if c[0] == "trade"] == trades


async def test_opened_notification_partial_fill_then_cancel(
    session_factory: sessionmaker[Session],
) -> None:
    """An entry cancelled after partial fills reports "8 of 12"."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"PG": Decimal("162.90")})
    broker.partial_fill["PG"] = 8
    submitted = await broker.submit_bracket_entry(
        symbol="PG",
        qty=12,
        limit_price=Decimal("162.90"),
        stop_price=Decimal("158.00"),
        target_price=Decimal("170.00"),
        client_order_id="sa-entry-1-PG-2",
    )
    executor = _executor(session_factory, broker, clock)
    notices = _Notices()
    executor._notifier = notices
    with session_factory() as session:
        await _seed_entry_row(session, submitted, "PG", 12, "162.90")

    await broker.tick("PG", Decimal("162.85"))
    await executor.reconcile()
    assert [c for c in notices.calls if c[0] == "trade"] == []

    await broker.cancel_order(submitted.data.id)
    await executor.reconcile()
    trades = [c for c in notices.calls if c[0] == "trade"]
    assert trades == [("trade", "opened PG: 8 of 12 shares @ 162.85")]
    with session_factory() as session:
        position = session.execute(
            select(Position).where(Position.symbol == "PG")
        ).scalar_one()
        assert position.qty == 8
