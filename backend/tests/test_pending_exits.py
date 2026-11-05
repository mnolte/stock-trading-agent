"""Live pending exits survive held attempts and are redriven without model input."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from app.broker.fake import FakeBroker, _PosRec
from app.data.models import DataResult
from app.db.models import Position
from app.engine.cycle import CycleReport, CycleRunner
from app.engine.executor import ReconcileReport
from app.market.clock import FakeClock
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import NOW, events_of, make_decision, open_position
from tests.test_executor import _executor
from tests.test_position_check import _checker

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("failure", ["quantity", "cancel", "held", "sell"])
async def test_held_paths_persist_and_protect(
    session_factory: sessionmaker[Session], failure: str,
) -> None:
    broker = cast(Any, FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")}))
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    executor = cast(Any, _executor(session_factory, broker))
    executor._place_protective_stop = AsyncMock(return_value=None)
    if failure == "quantity":
        broker.get_position = AsyncMock(return_value=DataResult(data=None, error="unavailable"))
    elif failure == "cancel":
        broker.get_open_orders = AsyncMock(return_value=DataResult(data=[], error="unavailable"))
    elif failure == "held":
        executor._await_released = AsyncMock(return_value=(
            type("Held", (), {"qty": Decimal(4), "qty_available": Decimal(0)})(), None,
        ))
        executor._free_qty = lambda _: Decimal(0)
    else:
        broker.submit_market_sell = AsyncMock(return_value=DataResult(data=None, error="timeout"))
    with session_factory() as session:
        position = open_position(session)
        decision = make_decision(session, "MSFT", "exit")
        await executor._exit(session, decision, position, reason="agent_exit")
        session.refresh(position)
        assert position.pending_exit
        assert position.pending_exit["decision_id"] == decision.id
        assert position.pending_exit["tries"] == 1
        assert position.pending_exit["reason"] == "agent_exit"
        assert not events_of(session, "pending_exit")
    executor._place_protective_stop.assert_awaited_once()


@pytest.mark.parametrize("reason", ["agent_exit", "earnings_exit"])
async def test_escalation_and_flat_clear(
    session_factory: sessionmaker[Session], reason: str,
) -> None:
    clock = FakeClock(NOW)
    broker = FakeBroker(clock)
    executor = cast(Any, _executor(session_factory, broker, clock=clock))
    executor._place_protective_stop = AsyncMock(return_value=None)
    with session_factory() as session:
        position = open_position(session)
        decision = make_decision(session, "MSFT", "exit")
        executor._hold_exit(session, position, decision, reason, "quantity not confirmed")
        assert not events_of(session, "pending_exit")
        clock.advance(timedelta(hours=4))
        executor._alert_pending_exit(session, position)
        session.commit()
        assert len(events_of(session, "pending_exit")) == 1
        position_id = position.id
    await executor.retry_pending_exits()
    with session_factory() as session:
        stored = session.get(Position, position_id)
        assert stored is not None and stored.pending_exit is None


async def test_check_retries_before_reconciliation(session_factory: sessionmaker[Session]) -> None:
    checker = cast(Any, _checker(session_factory, FakeBroker(FakeClock(NOW))))
    calls: list[str] = []

    async def retry() -> None:
        calls.append("retry")

    original = checker.executor.reconcile

    async def reconcile() -> ReconcileReport:
        calls.append("reconcile")
        return cast(ReconcileReport, await original())

    checker.executor.retry_pending_exits = retry
    checker.executor.reconcile = reconcile
    await checker.run()
    assert calls[:2] == ["retry", "reconcile"]


async def test_cycle_retries_even_before_calendar_gate(
    session_factory: sessionmaker[Session],
) -> None:
    retry = AsyncMock()
    runner = SimpleNamespace(
        executor=SimpleNamespace(retry_pending_exits=retry),
        calendar=SimpleNamespace(is_loaded=False),
    )
    report = CycleReport()
    with session_factory() as session:
        await CycleRunner._run(cast(CycleRunner, runner), session, 1, NOW, NOW, report)
    retry.assert_awaited_once()
    assert report.status == "skipped"


async def test_pending_sell_is_not_resent_after_timeout(
    session_factory: sessionmaker[Session],
) -> None:
    broker = cast(Any, FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")}))
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    executor = cast(Any, _executor(session_factory, broker))
    executor._place_protective_stop = AsyncMock(return_value=None)
    broker.submit_market_sell = AsyncMock(return_value=DataResult(data=None, error="timeout"))
    with session_factory() as session:
        position = open_position(session)
        decision = make_decision(session, "MSFT", "exit")
        await executor._exit(session, decision, position, reason="agent_exit")
    broker.get_order_by_client_id = AsyncMock(
        return_value=DataResult(data=None, error="read unavailable"),
    )
    await executor.retry_pending_exits()
    broker.submit_market_sell.assert_awaited_once()


async def test_refused_sell_retries_only_after_confirmed_absence(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    broker.reject_sells.add("MSFT")
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = open_position(session)
        decision = make_decision(session, "MSFT", "exit")
        await executor._exit(session, decision, position, reason="agent_exit")
        assert position.pending_exit
        position_id = position.id
    broker.reject_sells.clear()
    await executor.retry_pending_exits()
    with session_factory() as session:
        stored = session.get(Position, position_id)
        assert stored is not None and stored.pending_exit is None
    assert "MSFT" not in broker.positions


async def test_outside_session_check_retries_as_market_on_open(
    session_factory: sessionmaker[Session],
) -> None:
    now = NOW + timedelta(hours=4)
    broker = FakeBroker(FakeClock(now), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    checker = _checker(session_factory, broker, now=now)
    with session_factory() as session:
        position = open_position(session)
        decision = make_decision(session, "MSFT", "exit")
        checker.executor._hold_exit(session, position, decision, "earnings_exit", "shares held")
    await checker.run()
    assert any(order.time_in_force == "opg" for order in broker.orders.values())
    with session_factory() as session:
        assert len(events_of(session, "pending_exit")) == 1


async def test_real_protection_after_exit_cannot_confirm_released_quantity(
    session_factory: sessionmaker[Session],
) -> None:
    broker = cast(Any, FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")}))
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    executor = cast(Any, _executor(session_factory, broker))
    executor.state.broker_guard_confirmed = False
    executor._await_released = AsyncMock(return_value=(None, "read failed"))
    with session_factory() as session:
        position = open_position(session)
        decision = make_decision(session, "MSFT", "exit")
        await executor._exit(session, decision, position, reason="agent_exit")
        assert position.pending_exit
    assert any(order.order_type == "stop" for order in broker.orders.values())


async def test_fill_clears_pending_exit(session_factory: sessionmaker[Session]) -> None:
    broker = cast(Any, FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")}))
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    executor = cast(Any, _executor(session_factory, broker))
    with session_factory() as session:
        position = open_position(session)
        decision = make_decision(session, "MSFT", "exit")
        executor._hold_exit(session, position, decision, "agent_exit", "quantity not confirmed")
        await executor._exit(session, decision, position, reason="agent_exit")
        assert position.pending_exit is None


async def test_three_failures_escalate_once(session_factory: sessionmaker[Session]) -> None:
    executor = _executor(session_factory, FakeBroker(FakeClock(NOW)))
    with session_factory() as session:
        position = open_position(session)
        decision = make_decision(session, "MSFT", "exit")
        for attempt in range(4):
            executor._hold_exit(session, position, decision, "agent_exit", "quantity not confirmed")
            assert len(events_of(session, "pending_exit")) == (1 if attempt >= 2 else 0)
