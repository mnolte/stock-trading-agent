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
from app.data.models import DataResult, Quote
from app.data.protocols import QuoteSource
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
    quotes: QuoteSource | None = None,
    now: datetime = NOW,
) -> PositionChecker:
    clock = FakeClock(now)
    config = AppConfig()
    calendar = FakeCalendarSource()
    sources = DataSources(
        bars=FakeBarSource(calendar),
        quotes=quotes or FakeQuoteSource(clock),
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


class _CrashQuotes:
    """SPY trading ~5% below the session open — a mid-session crash."""

    async def fetch_quotes(
        self, symbols: list[str]
    ) -> DataResult[dict[str, Quote]]:
        return DataResult(
            data={
                s: Quote(
                    symbol=s,
                    bid=Decimal("549"),
                    ask=Decimal("550"),
                    last=Decimal("550"),
                    timestamp=NOW,
                    feed="iex",
                )
                for s in symbols
            },
            source="fake",
        )


async def test_position_check_trips_shock_brake_on_spy_crash(
    session_factory: sessionmaker[Session],
) -> None:
    """B1: SPY crossing the since-open threshold mid-session must trip the
    brake inside this same position check — the quote is real-time."""
    broker = FakeBroker(FakeClock(NOW), cash=Decimal("10000"))
    checker = _checker(session_factory, broker, quotes=_CrashQuotes())

    report = await checker.run()

    assert report.ok
    assert checker.state.shock_brake_session is True
    # The 1h leg is decision-cycle only; position checks leave it alone.
    assert checker.state.shock_brake_1h is False


async def test_position_check_quiet_spy_no_brake(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), cash=Decimal("10000"))
    checker = _checker(session_factory, broker)  # default calm quotes

    report = await checker.run()

    assert report.ok
    assert checker.state.shock_brake_session is False
    assert checker.state.shock_brake_1h is False


async def test_check_skipped_on_non_trading_day(
    session_factory: sessionmaker[Session],
) -> None:
    """A weekend check does nothing: no cycle row, no broker call, no
    snapshot. The broker-side stops keep working without the polling."""
    saturday = datetime(2026, 10, 17, 17, 0, tzinfo=UTC)
    broker = FakeBroker(FakeClock(saturday), cash=Decimal("10000"))
    # Any broker read would fail — a clean report proves none happened.
    broker.fail_reads = True
    checker = _checker(session_factory, broker, now=saturday)

    report = await checker.run()

    assert report.ok
    assert report.errors == []
    with session_factory() as session:
        assert session.execute(select(Cycle)).first() is None
        assert session.execute(select(EquitySnapshot)).first() is None
    assert checker.state.failed_checks == 0


async def test_check_skipped_after_session_close(
    session_factory: sessionmaker[Session],
) -> None:
    """Same guard after the close on a trading day — a 17:00 ET check
    makes no broker calls and writes nothing."""
    after_close = datetime(2026, 10, 15, 21, 0, tzinfo=UTC)  # 17:00 ET
    broker = FakeBroker(FakeClock(after_close), cash=Decimal("10000"))
    broker.fail_reads = True
    checker = _checker(session_factory, broker, now=after_close)

    report = await checker.run()

    assert report.ok
    with session_factory() as session:
        assert session.execute(select(Cycle)).first() is None
        assert session.execute(select(EquitySnapshot)).first() is None


async def test_check_skipped_before_session_open(
    session_factory: sessionmaker[Session],
) -> None:
    """Same guard before the open on a trading day — an 08:00 ET check
    makes no broker calls and writes nothing."""
    pre_open = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)  # 08:00 ET
    broker = FakeBroker(FakeClock(pre_open), cash=Decimal("10000"))
    broker.fail_reads = True
    checker = _checker(session_factory, broker, now=pre_open)

    report = await checker.run()

    assert report.ok
    with session_factory() as session:
        assert session.execute(select(Cycle)).first() is None
        assert session.execute(select(EquitySnapshot)).first() is None


async def test_reconcile_errors_reported_once(
    session_factory: sessionmaker[Session],
) -> None:
    """Each reconcile error lands in the cycle row exactly once."""
    broker = FakeBroker(FakeClock(NOW))
    broker.fail_reads = True
    checker = _checker(session_factory, broker)

    report = await checker.run()

    assert not report.ok
    assert len(report.errors) == len(set(report.errors))
    with session_factory() as session:
        cycle = session.execute(select(Cycle)).scalar_one()
        assert cycle.errors is not None
        assert len(cycle.errors) == len(set(cycle.errors))
