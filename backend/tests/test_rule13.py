"""Hard rule 13 audit: no model, broker or data call runs while a SQLite
write transaction is open. Shadow and backtest work shares the event loop
and the database, so a live job that holds the write lock across an await
would block or fail other writers (found in the PR 13 shadow runner).

Each job runs against fakes wrapped in LockProbe, which tries to take the
write lock (BEGIN IMMEDIATE, no busy-wait) before every awaited call.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import cast

import pytest
from app.broker.fake import FakeBroker
from app.config import AppConfig, get_config
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
from app.db.models import Order, Position
from app.decide.client import FakeDecisionClient
from app.decide.service import DecisionService
from app.engine.cycle import CycleRunner
from app.engine.executor import Executor
from app.engine.position_check import PositionChecker
from app.engine.state import ExecutionState
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, FakeClock
from app.reviews.client import FakeReviewClient
from app.reviews.service import ReviewService
from app.signals.service import Evaluation
from app.universe.service import UniverseService
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.lock_probe import LockProbe
from tests.test_decide_cycle import (
    NOW_13H,
    _evaluation,
    _item,
)

pytestmark = pytest.mark.anyio

CYCLE_1246 = datetime(2026, 10, 15, 12, 46, tzinfo=NY_TZ)

_DB: dict[str, str] = {}


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    """The app's real engine/session maker on this test's database file —
    WAL mode and the busy timeout included."""
    from app.db.base import Base, get_engine, get_session_maker

    _DB["path"] = get_config().env.sa_db_path
    Base.metadata.create_all(get_engine())
    return get_session_maker()


def _db_path() -> str:
    return _DB["path"]


def _probed_sources(probes: list[LockProbe], clock: FakeClock | None = None) -> DataSources:
    """Every source wrapped in the lock probe."""
    db = _db_path()
    calendar = FakeCalendarSource()
    raw = DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(clock) if clock is not None else FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar,
        alpaca=None,
    )
    wrapped = DataSources(
        bars=raw.bars,
        quotes=raw.quotes,
        news=raw.news,
        earnings=raw.earnings,
        fx=raw.fx,
        corporate_actions=raw.corporate_actions,
        calendar=raw.calendar,
        alpaca=raw.alpaca,
    )
    for name in ("bars", "quotes", "news", "earnings", "fx", "corporate_actions", "alpaca"):
        src = getattr(wrapped, name)
        if src is None:
            continue
        probe = LockProbe(f"sources.{name}", src, db)
        probes.append(probe)
        setattr(wrapped, name, probe)
    return wrapped


def _probed_executor(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    sources: DataSources,
    market_data: MarketDataService,
    probes: list[LockProbe],
    config: AppConfig | None = None,
    broker: FakeBroker | None = None,
) -> Executor:
    db = _db_path()
    probe = LockProbe("broker", broker or FakeBroker(clock), db)
    probes.append(probe)
    broker = probe  # type: ignore[assignment]
    return Executor(
        broker=broker,  # type: ignore[arg-type]
        market_data=market_data,
        signals=None,  # type: ignore[arg-type]
        calendar=cast(CalendarProvider, sources.calendar),
        config=config or AppConfig(),
        clock=clock,
        state=ExecutionState(clock=clock),
        session_factory=session_factory,
    )


async def test_cycle_and_position_check_commit_before_every_io_call(
    session_factory: sessionmaker[Session],
) -> None:
    probes: list[LockProbe] = []
    clock = FakeClock(NOW_13H)
    sources = _probed_sources(probes, clock)
    db = _db_path()
    calendar = cast(CalendarProvider, sources.calendar)
    config = AppConfig()
    market_data = MarketDataService(sources, calendar, config, session_factory=session_factory)
    from app.signals.service import SignalService

    signals = SignalService(market_data, calendar, config, clock, session_factory)
    evaluation = _evaluation(
        _item("MSFT", "held"),
        _item("AAPL", "pass"),
        _item("NVDA", "fail"),
    )
    signals.refresh_eval = lambda: _eval(evaluation)  # type: ignore[method-assign]
    client = LockProbe("decide", FakeDecisionClient(enter_symbols={"AAPL"}), db)
    probes.append(client)
    decide = DecisionService(client, config, session_factory)
    fake = FakeBroker(clock)
    executor = _probed_executor(
        session_factory, clock, sources, market_data, probes, config, broker=fake
    )
    runner = CycleRunner(
        market_data=market_data,
        signals=signals,
        decide=decide,
        sources=sources,
        calendar=calendar,
        config=config,
        clock=clock,
        executor=executor,
        session_factory=session_factory,
    )

    # A held position makes the cycle take the tighten/exit review path.
    with session_factory() as session:
        session.add(
            Position(
                symbol="MSFT",
                qty=10,
                avg_entry_price=410,
                current_stop_price=398,
                target_price=434,
                opened_at=datetime(2026, 10, 14, 14, 0),
            )
        )
        session.commit()

    report = await runner.run(CYCLE_1246)
    assert report.status == "done", report.errors
    assert report.calls == 2

    # A broker-side position the database doesn't know: reconcile adopts
    # it and places a protective stop, exercising those write→call paths.
    from app.broker.fake import _PosRec

    fake.positions["IBM"] = _PosRec(5, Decimal("150"))
    checker = PositionChecker(
        executor=executor,
        state=executor.state,
        config=config,
        clock=clock,
        session_factory=session_factory,
    )
    check = await checker.run()
    assert check.ok, check.errors

    # The audited paths really ran: AAPL's bracket entry was submitted and
    # IBM's unknown broker position was adopted with a protective stop.
    assert fake.submit_calls >= 2, (
        f"entries+stops submitted: {fake.submit_calls}; "
        f"decision errors: {report.errors}; check: {check.errors}"
    )
    with session_factory() as session:
        assert (
            session.execute(select(func.count(Order.id)).where(Order.leg == "entry")).scalar_one()
            == 1
        )
        adopted = session.execute(
            select(Position).where(Position.symbol == "IBM", Position.origin == "adopted")
        ).scalar_one()
        assert adopted.pending_ack

    violations = [v for p in probes for v in p.violations]
    assert violations == []


async def _eval(evaluation: Evaluation) -> Evaluation:
    return evaluation


async def test_reviews_and_universe_screen_commit_before_every_call(
    session_factory: sessionmaker[Session],
) -> None:
    db = _db_path()
    probes: list[LockProbe] = []
    clock = FakeClock(datetime(2026, 10, 17, 14, 0, tzinfo=NY_TZ))
    config = AppConfig()
    calendar = FakeCalendarSource()

    client = LockProbe("review", FakeReviewClient(), db)
    probes.append(client)
    reviews = ReviewService(
        client=client,
        config=config,
        clock=clock,
        calendar=cast(CalendarProvider, calendar),
        session_factory=session_factory,
    )
    await reviews.write_weekly_for(datetime(2026, 10, 17, 15, 0, tzinfo=NY_TZ))
    await reviews.write_briefing_for(date(2026, 10, 16))

    sources = _probed_sources(probes, clock)
    market_data = MarketDataService(
        sources,
        cast(CalendarProvider, sources.calendar),
        config,
        session_factory=session_factory,
    )
    broker = LockProbe("broker", FakeBroker(clock), db)
    probes.append(broker)
    screen = UniverseService(
        market_data,
        sources,
        cast(CalendarProvider, sources.calendar),
        broker,
        config,
        clock,
        session_factory=session_factory,
    )
    await screen.run_screen(datetime(2026, 10, 15, 7, 30, tzinfo=NY_TZ))

    violations = [v for p in probes for v in p.violations]
    assert violations == []
