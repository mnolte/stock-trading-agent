"""The decision cycle end to end on fakes: stored decisions, no network."""

import json
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
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
from app.data.service import MarketDataService
from app.db.base import Base
from app.db.models import Cycle, Decision, Event, Order, Outcome, Position
from app.decide.client import FakeDecisionClient
from app.decide.service import DecisionService
from app.engine.cycle import CycleReport, CycleRunner
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from app.rules.prefilter import RulesResult
from app.signals.regime import MarketRegime
from app.signals.service import Evaluation, SignalService, SymbolEval
from app.signals.snapshot import IndicatorSnapshot
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

CYCLE_1246 = datetime(2026, 10, 15, 12, 46, tzinfo=NY_TZ)
NOW_13H = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)  # 13:00 New York


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _regime() -> MarketRegime:
    return MarketRegime(
        trend="up",
        volatility="normal",
        key="up_normal",
        label="Up · normal vol",
        blocks_entries=False,
        atr_percentile=Decimal("48"),
    )


def _snapshot(symbol: str) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        symbol=symbol,
        price=Decimal("420.00"),
        ema_fast=Decimal("416.90"),
        ema_slow=Decimal("414.30"),
        rsi=Decimal("57"),
        atr_1h=Decimal("3.10"),
        session_vwap=Decimal("417.40"),
        obv_trend="up",
        rs_vs_spy_pct=Decimal("0.9"),
        daily_close=Decimal("415.80"),
        daily_sma_fast=Decimal("402.10"),
        daily_sma_slow=Decimal("381.60"),
        daily_atr_percentile=Decimal("48"),
        opening_gap_pct=Decimal("0.3"),
        days_to_earnings=9,
        earnings_available=True,
    )


def _item(symbol: str, status: str) -> SymbolEval:
    return SymbolEval(
        symbol=symbol,
        snapshot=_snapshot(symbol),
        rules=RulesResult(
            symbol,
            status,
            None if status == "pass" else "failed",
            [],
        ),
        bars_1h=[],
        price=Decimal("420.00"),
        day_change_pct=Decimal("0.5"),
    )


def _evaluation(*items: SymbolEval, held: set[str] | None = None) -> Evaluation:
    return Evaluation(
        computed_at=NOW_13H,
        regime=_regime(),
        held=held or set(),
        pending=set(),
        earnings_next={},
        items=list(items),
    )


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


def _runner(
    session_factory: sessionmaker[Session],
    evaluation: Evaluation,
    *,
    sources: DataSources | None = None,
    client: FakeDecisionClient | None = None,
    clock: FakeClock | None = None,
    universe: list[str] | None = None,
    with_executor: bool = False,
) -> CycleRunner:
    config = AppConfig()
    if universe is not None:
        config.yaml["universe"]["symbols"] = universe
    src = sources or _sources()
    clock = clock or FakeClock(NOW_13H)
    calendar = cast(CalendarProvider, src.calendar)
    market_data = MarketDataService(
        src, calendar, config, session_factory=session_factory
    )
    signals = SignalService(market_data, calendar, config, clock, session_factory)
    signals.refresh_eval = lambda: _resolved(evaluation)  # type: ignore[method-assign]
    decide = DecisionService(client or FakeDecisionClient(), config, session_factory)
    executor = None
    if with_executor:
        from app.broker.fake import FakeBroker
        from app.engine.executor import Executor
        from app.engine.state import ExecutionState

        executor = Executor(
            broker=FakeBroker(clock),
            market_data=market_data,
            signals=signals,
            calendar=calendar,
            config=config,
            clock=clock,
            state=ExecutionState(clock=clock),
            session_factory=session_factory,
        )
    return CycleRunner(
        market_data=market_data,
        signals=signals,
        decide=decide,
        sources=src,
        calendar=calendar,
        config=config,
        clock=clock,
        executor=executor,
        session_factory=session_factory,
    )


async def _resolved(evaluation: Evaluation) -> Evaluation:
    return evaluation


def _decisions(
    session_factory: sessionmaker[Session],
) -> list[Decision]:
    with session_factory() as session:
        return list(session.execute(select(Decision)).scalars())


async def test_cycle_stores_decisions_for_candidates_and_held(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(
            Position(
                symbol="JPM",
                qty=4,
                avg_entry_price=Decimal("230.00"),
                current_stop_price=Decimal("224.00"),
                target_price=Decimal("242.00"),
                opened_at=datetime(2026, 10, 14, 15, 0),
            )
        )
        session.commit()
    runner = _runner(
        session_factory,
        _evaluation(
            _item("MSFT", "pass"),
            _item("JPM", "held"),
            _item("XOM", "fail"),
            held={"JPM"},
        ),
        universe=["MSFT", "JPM", "XOM"],
    )
    report = await runner.run(CYCLE_1246)
    assert report.status == "done"
    decisions = {d.symbol: d for d in _decisions(session_factory)}
    assert set(decisions) == {"MSFT", "JPM", "XOM"}
    msft = decisions["MSFT"]
    assert msft.context == "candidate"
    assert msft.final_action == "enter"  # fake enters MSFT
    assert msft.model_output is not None
    assert msft.input_snapshot is not None
    assert msft.prompt_version == "decision.v1"
    assert len(msft.prompt_hash) == 64
    assert msft.latency_ms is not None
    assert msft.raw_response is not None
    jpm = decisions["JPM"]
    assert jpm.context == "held"
    assert jpm.final_action == "hold"
    # A symbol the rules reject is stored as no_call, reason "rules".
    xom = decisions["XOM"]
    assert xom.final_action == "no_call"
    assert xom.no_call_reason == "rules"
    assert xom.model == "none"
    assert xom.input_snapshot is not None
    with session_factory() as session:
        outcomes = session.execute(select(Outcome)).scalars().all()
        cycles = session.execute(select(Cycle)).scalars().all()
    assert {o.decision_id for o in outcomes} == {d.id for d in decisions.values()}
    assert cycles[0].kind == "cycle"
    assert cycles[0].status == "done"
    assert cycles[0].scheduled_time == CYCLE_1246.astimezone(UTC).replace(tzinfo=None)


async def test_cycle_invalid_output_becomes_skip_and_keeps_raw(
    session_factory: sessionmaker[Session],
) -> None:
    client = FakeDecisionClient(responses={"MSFT": "not json at all"})
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass")),
        client=client,
        universe=["MSFT"],
    )
    await runner.run(CYCLE_1246)
    (decision,) = _decisions(session_factory)
    assert decision.final_action == "skip"
    assert decision.validation_result is not None
    assert decision.validation_result["valid"] is False
    assert decision.raw_response == "not json at all"
    assert decision.model_output is None


async def test_cycle_failed_call_records_no_call(
    session_factory: sessionmaker[Session],
) -> None:
    client = FakeDecisionClient(responses={"MSFT": {"error": "timeout after 60s"}})
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass")),
        client=client,
        universe=["MSFT"],
    )
    await runner.run(CYCLE_1246)
    (decision,) = _decisions(session_factory)
    assert decision.final_action == "no_call"
    assert decision.no_call_reason == "model_error"
    assert decision.validation_result is not None
    assert "timeout" in str(decision.validation_result["error"])


async def test_cycle_candidate_without_quote_gets_no_call(
    session_factory: sessionmaker[Session],
) -> None:
    class NoQuotes:
        async def fetch_quotes(
            self, symbols: list[str]
        ) -> DataResult[dict[str, Quote]]:
            return DataResult(data={}, source="fake")

    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass")),
        sources=_sources(quotes=NoQuotes()),
        universe=["MSFT"],
    )
    report = await runner.run(CYCLE_1246)
    assert "failed" not in report.data_source_health["quotes"]
    (decision,) = _decisions(session_factory)
    assert decision.final_action == "no_call"
    assert decision.no_call_reason == "quote_missing"


async def test_cycle_skips_when_market_closed(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass")),
        clock=FakeClock(datetime(2026, 10, 15, 21, 0, tzinfo=UTC)),  # 17:00 NY
        universe=["MSFT"],
    )
    report = await runner.run(CYCLE_1246)
    assert report.status == "skipped"
    assert _decisions(session_factory) == []


async def test_run_due_runs_latest_cycle_once(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass")),
        universe=["MSFT"],
    )
    first = await runner.run_due()
    assert first is not None
    assert first.status == "done"
    assert runner.due_cycle(NOW_13H) == CYCLE_1246
    second = await runner.run_due()
    assert second is None


async def test_run_due_none_before_first_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _runner(
        session_factory,
        _evaluation(),
        clock=FakeClock(datetime(2026, 10, 15, 14, 0, tzinfo=UTC)),  # 10:00 NY
    )
    assert await runner.run_due() is None


async def test_decide_validates_against_snapshot_price(
    session_factory: sessionmaker[Session],
) -> None:
    # The fake's enter for MSFT uses snapshot price - 2 x ATR: a real client
    # answer is exercised the same way here through a scripted response.
    raw = json.dumps(
        {
            "symbol": "MSFT",
            "action": "enter",
            "confidence": 0.62,
            "stop_price": 400.0,  # > 4 x ATR 3.10 below the ~420 quote
            "target_price": 460.0,
            "thesis": "scripted",
            "invalidation": None,
            "risks": [],
            "news_used": [],
        }
    )
    client = FakeDecisionClient(responses={"MSFT": raw})
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass")),
        client=client,
        universe=["MSFT"],
    )
    await runner.run(CYCLE_1246)
    (decision,) = _decisions(session_factory)
    assert decision.final_action == "skip"
    assert decision.validation_result is not None
    assert decision.validation_result["changed_by_code"] is True


async def test_held_position_uses_position_stop_for_tighten_check(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(
            Position(
                symbol="MSFT",
                qty=4,
                avg_entry_price=Decimal("415.00"),
                current_stop_price=Decimal("410.00"),
                target_price=Decimal("435.00"),
                opened_at=datetime(2026, 10, 14, 15, 0),
            )
        )
        session.commit()
    raw = json.dumps(
        {
            "symbol": "MSFT",
            "action": "tighten_stop",
            "confidence": 0.7,
            "stop_price": 412.5,
            "target_price": None,
            "thesis": "lock in progress",
            "invalidation": None,
            "risks": [],
            "news_used": [],
        }
    )
    client = FakeDecisionClient(responses={("MSFT", "held"): raw})
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "held"), held={"MSFT"}),
        client=client,
        universe=["MSFT"],
    )
    await runner.run(CYCLE_1246)
    (decision,) = _decisions(session_factory)
    assert decision.final_action == "tighten_stop"


async def test_call_cap_reviews_held_positions_before_candidates(
    session_factory: sessionmaker[Session],
) -> None:
    """Held positions take the first call slots; candidates get no_call."""
    with session_factory() as session:
        session.add(
            Position(
                symbol="JPM",
                qty=4,
                avg_entry_price=Decimal("230.00"),
                current_stop_price=Decimal("224.00"),
                target_price=Decimal("242.00"),
                opened_at=datetime(2026, 10, 14, 15, 0),
            )
        )
        session.commit()
    runner = _runner(
        session_factory,
        _evaluation(
            _item("MSFT", "pass"),
            _item("AAPL", "pass"),
            _item("JPM", "held"),
            held={"JPM"},
        ),
        universe=["MSFT", "AAPL", "JPM"],
    )
    runner.config.yaml["decision"]["max_calls_per_cycle"] = 1
    report = await runner.run(CYCLE_1246)
    assert report.calls == 1
    decisions = {d.symbol: d for d in _decisions(session_factory)}
    # JPM is listed last in the evaluation but is called first as a held
    # position; both candidates are recorded as no_call / call_cap.
    assert decisions["JPM"].context == "held"
    assert decisions["JPM"].model != "none"
    for symbol in ("MSFT", "AAPL"):
        assert decisions[symbol].final_action == "no_call"
        assert decisions[symbol].no_call_reason == "call_cap"
        assert decisions[symbol].model == "none"


async def test_quote_missing_does_not_consume_call_slot(
    session_factory: sessionmaker[Session],
) -> None:
    """A candidate with no quote makes no model call, so the cap slot stays
    free for the next candidate."""

    class MsftOnlyQuotes:
        async def fetch_quotes(
            self, symbols: list[str]
        ) -> DataResult[dict[str, Quote]]:
            return DataResult(
                data={
                    "MSFT": Quote(
                        symbol="MSFT",
                        bid=Decimal("420"),
                        ask=Decimal("420.10"),
                        last=Decimal("420.10"),
                        timestamp=NOW_13H,
                        feed="iex",
                    )
                },
                source="fake",
            )

    runner = _runner(
        session_factory,
        _evaluation(
            _item("AAPL", "pass"),  # no quote → quote_missing
            _item("MSFT", "pass"),  # gets the one call slot
            _item("XOM", "pass"),   # past the cap → call_cap
        ),
        sources=_sources(quotes=MsftOnlyQuotes()),
        universe=["AAPL", "MSFT", "XOM"],
    )
    runner.config.yaml["decision"]["max_calls_per_cycle"] = 1

    report = await runner.run(CYCLE_1246)

    assert report.calls == 1
    decisions = {d.symbol: d for d in _decisions(session_factory)}
    assert decisions["AAPL"].final_action == "no_call"
    assert decisions["AAPL"].no_call_reason == "quote_missing"
    assert decisions["MSFT"].model != "none"
    assert decisions["XOM"].final_action == "no_call"
    assert decisions["XOM"].no_call_reason == "call_cap"


async def test_run_due_respects_early_close(
    session_factory: sessionmaker[Session],
) -> None:
    early = FakeCalendarSource(
        early_closes={date(2026, 10, 15): time(13, 0)}
    )
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass")),
        sources=_sources(calendar=early, bars=FakeBarSource(early)),
        clock=FakeClock(datetime(2026, 10, 15, 17, 30, tzinfo=UTC)),  # 13:30 NY
        universe=["MSFT"],
    )
    due = runner.due_cycle(runner.clock.now())
    # On a 13:00 close only the 10:46, 11:46 and 12:46 cycles run.
    assert due == CYCLE_1246


# ----------------------------------------------------------------------
# earnings exit timing (15:46 NY cycle the day before the report)


LAST_CYCLE = datetime(2026, 10, 15, 15, 46, tzinfo=NY_TZ)  # Thursday
MID_CYCLE = datetime(2026, 10, 15, 12, 46, tzinfo=NY_TZ)
NEXT_SESSION = date(2026, 10, 16)  # Friday, no holidays in the fake


def _held(symbol: str = "MSFT") -> Position:
    return Position(
        symbol=symbol,
        qty=4,
        avg_entry_price=Decimal("415"),
        current_stop_price=Decimal("410"),
        opened_at=datetime(2026, 10, 14, 15, 0),
    )


def test_last_cycle_detection(session_factory: sessionmaker[Session]) -> None:
    runner = _runner(session_factory, _evaluation())
    assert runner._is_last_cycle(LAST_CYCLE)
    assert not runner._is_last_cycle(MID_CYCLE)


def test_earnings_before_next_open_exits_at_last_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _runner(session_factory, _evaluation())
    earnings = {"MSFT": (NEXT_SESSION, "before_open")}
    assert runner._earnings_exit_due(
        _held(), earnings, LAST_CYCLE, last_cycle=True
    )
    assert not runner._earnings_exit_due(
        _held(), earnings, MID_CYCLE, last_cycle=False
    )


def test_earnings_unknown_time_counts_as_before_open(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _runner(session_factory, _evaluation())
    earnings = {"MSFT": (NEXT_SESSION, "unknown")}
    assert runner._earnings_exit_due(
        _held(), earnings, LAST_CYCLE, last_cycle=True
    )


def test_earnings_during_market_next_day_exits_day_before(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _runner(session_factory, _evaluation())
    earnings = {"MSFT": (NEXT_SESSION, "during_market")}
    assert runner._earnings_exit_due(
        _held(), earnings, LAST_CYCLE, last_cycle=True
    )


def test_earnings_after_next_close_stays_held_today(
    session_factory: sessionmaker[Session],
) -> None:
    """A report after Friday's close is exited at Friday's last cycle."""
    runner = _runner(session_factory, _evaluation())
    earnings = {"MSFT": (NEXT_SESSION, "after_close")}
    assert not runner._earnings_exit_due(
        _held(), earnings, LAST_CYCLE, last_cycle=True
    )


def test_earnings_after_todays_close_exits_at_last_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    """Reporting after today's close still means held through the report if
    we wait — sell at today's last cycle."""
    runner = _runner(session_factory, _evaluation())
    earnings = {"MSFT": (date(2026, 10, 15), "after_close")}
    assert runner._earnings_exit_due(
        _held(), earnings, LAST_CYCLE, last_cycle=True
    )


def test_earnings_today_before_open_no_exit(
    session_factory: sessionmaker[Session],
) -> None:
    """A report this morning can no longer be front-run — not our rule."""
    runner = _runner(session_factory, _evaluation())
    earnings = {"MSFT": (date(2026, 10, 15), "before_open")}
    assert not runner._earnings_exit_due(
        _held(), earnings, LAST_CYCLE, last_cycle=True
    )


def test_earnings_weeks_away_no_exit(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _runner(session_factory, _evaluation())
    earnings = {"MSFT": (date(2026, 10, 22), "before_open")}
    assert not runner._earnings_exit_due(
        _held(), earnings, LAST_CYCLE, last_cycle=True
    )


def test_hold_through_earnings_disables_exit(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _runner(session_factory, _evaluation())
    runner.config.yaml["risk"]["hold_through_earnings"] = True
    earnings = {"MSFT": (NEXT_SESSION, "before_open")}
    assert not runner._earnings_exit_due(
        _held(), earnings, LAST_CYCLE, last_cycle=True
    )


# ----------------------------------------------------------------------
# error kill switch (docs/SPEC.md, Risk management, Errors)


async def _explode(
    session: Session,
    cycle_id: int,
    scheduled_ny: datetime,
    now: datetime,
    report: CycleReport,
) -> None:
    raise RuntimeError("data exploded")


async def test_three_failed_cycles_turn_the_kill_switch_on(
    session_factory: sessionmaker[Session],
) -> None:
    """Three cycles erroring in a row activate the automatic kill switch."""
    runner = _runner(session_factory, _evaluation(), with_executor=True)
    assert runner.executor is not None

    runner._run = _explode  # type: ignore[method-assign]
    for _ in range(3):
        report = await runner.run(CYCLE_1246)

    assert report.status == "error"
    state = runner.executor.state
    assert state.failed_cycles == 3
    assert state.kill_switch_on
    assert state.kill_switch_auto
    assert "3 cycles failed in a row" in (state.kill_switch_reason or "")
    with session_factory() as session:
        event = session.execute(
            select(Event).where(Event.event_type == "kill_switch")
        ).scalar_one()
    assert (event.details or {})["automatic"] is True
    assert (event.details or {})["on"] is True


async def test_a_successful_cycle_resets_the_failure_streak(
    session_factory: sessionmaker[Session],
) -> None:
    """A done cycle between errors restarts the count; no kill at two."""
    runner = _runner(session_factory, _evaluation(), with_executor=True)
    assert runner.executor is not None

    real_run = runner._run
    runner._run = _explode  # type: ignore[method-assign]
    await runner.run(CYCLE_1246)
    await runner.run(CYCLE_1246)
    assert runner.executor.state.failed_cycles == 2

    runner._run = real_run  # type: ignore[method-assign]
    report = await runner.run(CYCLE_1246)
    assert report.status == "done"
    assert runner.executor.state.failed_cycles == 0

    runner._run = _explode  # type: ignore[method-assign]
    await runner.run(CYCLE_1246)
    await runner.run(CYCLE_1246)
    assert runner.executor.state.failed_cycles == 2
    assert not runner.executor.state.kill_switch_on


async def test_paused_cycle_reviews_held_and_blocks_entries(
    session_factory: sessionmaker[Session],
) -> None:
    """Paused at startup (docs/SPEC.md, Risk management): the cycle still
    reviews held positions — a model exit sells — but the same cycle's
    candidate entry is blocked by the paused gate."""
    from app.broker.fake import _PosRec

    exit_raw = json.dumps(
        {
            "symbol": "MSFT",
            "action": "exit",
            "confidence": 0.8,
            "stop_price": None,
            "target_price": None,
            "thesis": "thesis broken",
            "invalidation": None,
            "risks": [],
            "news_used": [],
        }
    )
    client = FakeDecisionClient(
        responses={("MSFT", "held"): exit_raw},
        enter_symbols={"AAPL"},
    )
    runner = _runner(
        session_factory,
        _evaluation(
            _item("MSFT", "held"),
            _item("AAPL", "pass"),
            held={"MSFT"},
        ),
        client=client,
        universe=["MSFT", "AAPL"],
        with_executor=True,
    )
    assert runner.executor is not None
    runner.executor.state.paused = True
    executor = runner.executor
    broker = cast(Any, executor.broker)
    broker.positions["MSFT"] = _PosRec(qty=4, avg_entry_price=Decimal("415"))
    with session_factory() as session:
        session.add(
            Position(
                symbol="MSFT",
                qty=4,
                avg_entry_price=Decimal("415.00"),
                current_stop_price=Decimal("410.00"),
                target_price=Decimal("435.00"),
                opened_at=datetime(2026, 10, 14, 15, 0),
            )
        )
        session.commit()

    report = await runner.run(CYCLE_1246)

    assert report.status == "done"
    decisions = {d.symbol: d for d in _decisions(session_factory)}
    # The held position was still reviewed and its exit went out.
    assert decisions["MSFT"].context == "held"
    assert decisions["MSFT"].final_action == "exit"
    # The candidate's enter is blocked by the paused gate — no order sent.
    assert decisions["AAPL"].final_action == "blocked"
    assert decisions["AAPL"].gates_result is not None
    assert decisions["AAPL"].gates_result["blocked_by"] == "paused"
    with session_factory() as session:
        orders = session.execute(
            select(Order).where(Order.symbol == "AAPL")
        ).scalars().all()
        sells = session.execute(
            select(Order).where(Order.symbol == "MSFT", Order.side == "sell")
        ).scalars().all()
    assert orders == []
    assert any(o.leg == "exit" for o in sells)
