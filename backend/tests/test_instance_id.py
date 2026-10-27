"""PR 23 step 5: a foreign instance id in a client order id is critical."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.broker.client_id import client_order_id, order_instance
from app.broker.fake import FakeBroker, _OrderRec
from app.config import AppConfig
from app.db.models import Event
from app.market.clock import FakeClock
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.test_executor import NOW, _executor

pytestmark = pytest.mark.anyio


def test_order_instance_parses() -> None:
    assert order_instance(client_order_id("sa-vps-1", "entry", 3, "MSFT", "n", 1)) == "savps1"
    assert order_instance("sa-entry-3-MSFT-n1") is None  # pre-instance shape
    assert order_instance("manual-order-1") is None
    assert order_instance(None) is None
    assert order_instance("sa-foo-bar") is None  # no kind segment


def _foreign_order(broker: FakeBroker, client_id: str) -> _OrderRec:
    rec = _OrderRec(
        id=broker._next_id(),
        client_order_id=client_id,
        symbol="XYZ",
        side="buy",
        order_type="limit",
        order_class="simple",
        status="new",
        qty=1,
        filled_qty=0,
        filled_avg_price=None,
        limit_price=Decimal("100"),
        stop_price=None,
        created_at=NOW,
        updated_at=NOW,
        filled_at=None,
    )
    broker.orders[rec.id] = rec
    return rec


async def test_foreign_instance_turns_kill_switch_on(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SA_INSTANCE_ID", "sa-vps-1")
    config = AppConfig()
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"XYZ": Decimal("100")})
    _foreign_order(broker, client_order_id("sa-local", "entry", 5, "XYZ", "n", 1))
    executor = _executor(session_factory, broker, clock=clock, config=config)
    await executor.reconcile()
    assert executor.state.kill_switch_on
    with session_factory() as session:
        events = session.execute(
            select(Event).where(Event.event_type == "another_instance")
        ).scalars().all()
        assert len(events) == 1
        assert events[0].level == "critical"
        assert (events[0].details or {})["foreign_instance"] == "salocal"
    # A second reconcile does not record the same order twice.
    await executor.reconcile()
    with session_factory() as session:
        assert (
            session.execute(select(Event).where(Event.event_type == "another_instance"))
            .scalars()
            .all()
        ).__len__() == 1


async def test_own_and_plain_ids_do_not_trip_it(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SA_INSTANCE_ID", "sa-vps-1")
    config = AppConfig()
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"XYZ": Decimal("100")})
    # This instance's own breach and exit orders…
    _foreign_order(broker, client_order_id("sa-vps-1", "breach", 5, "XYZ", "n", 1))
    _foreign_order(broker, client_order_id("sa-vps-1", "exit", 6, "XYZ", "n", 2))
    # …an order without an sa- id (manual / pre-format)…
    _foreign_order(broker, "alpaca-web-order-9")
    # …and a pre-instance agent id.
    _foreign_order(broker, "sa-stop-7-XYZ-n3")
    executor = _executor(session_factory, broker, clock=clock, config=config)
    await executor.reconcile()
    assert not executor.state.kill_switch_on
    with session_factory() as session:
        assert (
            session.execute(select(Event).where(Event.event_type == "another_instance"))
            .scalars()
            .all()
            == []
        )
