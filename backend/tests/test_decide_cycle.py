"""The decision cycle end to end on fakes: stored decisions, no network."""

import json
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import cast

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
from app.db.models import Cycle, Decision, Outcome, Position
from app.decide.client import FakeDecisionClient
from app.decide.service import DecisionService
from app.engine.cycle import CycleRunner
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
    return CycleRunner(
        market_data=market_data,
        signals=signals,
        decide=decide,
        sources=src,
        calendar=calendar,
        config=config,
        clock=clock,
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
    assert set(decisions) == {"MSFT", "JPM"}
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
