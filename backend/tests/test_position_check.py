"""The 15-minute position check: reconcile + brakes, alert on repeats."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.broker.fake import FakeBroker, _PosRec
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
from app.data.service import MarketDataService
from app.db.base import Base
from app.db.models import Cycle, EquitySnapshot, Event
from app.engine.executor import Executor
from app.engine.position_check import PositionChecker
from app.engine.state import ExecutionState
from app.market.clock import UTC, FakeClock
from app.signals.service import SignalService
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _checker(
    session_factory: sessionmaker[Session],
    broker: FakeBroker,
) -> PositionChecker:
    clock = FakeClock(NOW)
    config = AppConfig()
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
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    signals = SignalService(market_data, calendar, config, clock, session_factory)
    state = ExecutionState(clock=clock)
    executor = Executor(
        broker=broker,
        market_data=market_data,
        signals=signals,
        calendar=calendar,
        config=config,
        clock=clock,
        state=state,
        session_factory=session_factory,
    )
    return PositionChecker(
        executor=executor,
        state=state,
        config=config,
        clock=clock,
        session_factory=session_factory,
    )


async def test_check_runs_and_snapshots_equity(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), cash=Decimal("10000"))
    checker = _checker(session_factory, broker)

    report = await checker.run()

    assert report.ok
    with session_factory() as session:
        cycle = session.execute(select(Cycle)).scalar_one()
        assert cycle.kind == "position_check"
        assert cycle.status == "done"
        snapshot = session.execute(select(EquitySnapshot)).scalar_one()
        assert snapshot.equity == Decimal("10000")
    assert checker.state.failed_checks == 0


async def test_check_places_protective_stop_for_uncovered_position(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    checker = _checker(session_factory, broker)

    report = await checker.run()

    # The unknown position was adopted with a protective stop.
    assert report.mismatches == 1
    assert broker.orders


async def test_failed_checks_alert_at_threshold(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW))
    broker.fail_reads = True
    checker = _checker(session_factory, broker)

    for _ in range(3):
        report = await checker.run()
        assert not report.ok

    assert checker.state.failed_checks == 3
    with session_factory() as session:
        events = session.execute(
            select(Event).where(Event.event_type == "position_check_failed")
        ).scalars().all()
        assert events
        assert events[0].details is not None
        assert events[0].details["consecutive"] == 3
        cycles = session.execute(
            select(Cycle).where(Cycle.kind == "position_check")
        ).scalars().all()
        assert all(c.status == "error" for c in cycles)
