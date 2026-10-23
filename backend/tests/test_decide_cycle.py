"""The decision cycle end to end on fakes: stored decisions, no network."""

import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta
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
from app.data.models import Bar1h, Bar5m, DataResult, NewsItem, Quote
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
    clock = clock or FakeClock(NOW_13H)
    src = sources or _sources(quotes=FakeQuoteSource(clock))
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


async def test_held_snapshot_uses_initial_risk_and_full_provenance(
    session_factory: sessionmaker[Session],
) -> None:
    signal_bar = Bar1h(
        symbol="MSFT",
        start=CYCLE_1246 - timedelta(hours=1),
        end=CYCLE_1246,
        open=Decimal("415"), high=Decimal("421"), low=Decimal("414"),
        close=Decimal("420"), volume=1000, feed="fake", complete=True,
    )
    with session_factory() as session:
        entry_cycle = Cycle(
            kind="cycle",
            scheduled_time=CYCLE_1246 - timedelta(days=1),
            status="done",
        )
        session.add(entry_cycle)
        session.flush()
        entry = Decision(
            cycle_id=entry_cycle.id,
            symbol="MSFT",
            context="candidate",
            model_output={
                "thesis": "Breakout above consolidation",
                "invalidation": "Close below 410",
            },
            final_action="enter",
            prompt_version="decision.v1",
            prompt_hash="a" * 64,
            model="fake",
        )
        session.add(entry)
        session.flush()
        session.add(
            Position(
                symbol="MSFT",
                qty=4,
                avg_entry_price=Decimal("415"),
                initial_stop=Decimal("410"),
                current_stop_price=Decimal("412"),
                target_price=Decimal("430"),
                opened_at=CYCLE_1246 - timedelta(days=1),
                entry_decision_id=entry.id,
            )
        )
        session.commit()

    item = replace(_item("MSFT", "held"), bars_1h=[signal_bar])
    runner = _runner(
        session_factory,
        _evaluation(item, held={"MSFT"}),
        universe=["MSFT"],
    )
    await runner.run(CYCLE_1246)

    decision = max(_decisions(session_factory), key=lambda row: row.id)
    assert decision.input_snapshot is not None
    snapshot = decision.input_snapshot
    position = cast(dict[str, object], snapshot["position"])
    assert position["initial_stop"] == 410.0
    assert position["stop"] == 412.0
    assert position["remaining_downside_usd"] == 32.03
    assert position["r_so_far"] == 1.0
    assert position["original_thesis"] == "Breakout above consolidation"
    assert position["original_invalidation"] == "Close below 410"
    quote = cast(dict[str, object], snapshot["quote"])
    assert quote["feed"] == "fake"
    assert quote["quote_timestamp"] == NOW_13H.isoformat()
    assert quote["trade_timestamp"] is None
    assert quote["signal_bar_time"] == signal_bar.end.isoformat()
    assert snapshot["signal_bar_time"] == signal_bar.end.isoformat()
    assert snapshot["last_bar_time"] is not None


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


async def test_scheduled_cycle_outcome_failure_is_stored_with_reason(
    session_factory: sessionmaker[Session],
) -> None:
    class BrokenOutcomes:
        def fill(self, session: Session, now: datetime) -> None:
            raise RuntimeError("outcome failure")

    runner = _runner(session_factory, _evaluation(), universe=["MSFT"])
    runner.outcomes = cast(Any, BrokenOutcomes())
    report = await runner.run(CYCLE_1246)
    assert report.status == "error"
    with session_factory() as session:
        row = session.get(Cycle, report.cycle_id)
        assert row is not None
        assert row.status == "error"
        assert row.errors is not None and "outcome failure" in row.errors[0]


async def test_missing_scheduled_cycles_are_stored_once_with_reason(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _runner(session_factory, _evaluation(), universe=["MSFT"])
    count = runner.record_missing_cycles("app down")
    assert count == 3
    assert runner.record_missing_cycles("app down") == 0
    with session_factory() as session:
        rows = session.execute(select(Cycle).order_by(Cycle.scheduled_time)).scalars().all()
        assert all(row.status == "skipped" and row.errors == ["app down"] for row in rows)
        assert [row.scheduled_time.hour for row in rows] == [14, 15, 16]


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


@pytest.mark.parametrize(
    ("bid", "ask", "timestamp", "reason"),
    [
        ("0", "420", NOW_13H, "quote_missing"),
        ("419", "0", NOW_13H, "quote_missing"),
        ("419", "420", None, "quote_missing"),
        ("419", "420", NOW_13H - timedelta(hours=2), "quote_stale"),
        ("439", "440", NOW_13H, "quote_suspect"),
    ],
)
@pytest.mark.parametrize("status", ["pass", "held"])
async def test_cycle_rejects_unusable_quotes_before_model(
    session_factory: sessionmaker[Session],
    status: str,
    bid: str,
    ask: str,
    timestamp: datetime | None,
    reason: str,
) -> None:
    class Quotes:
        async def fetch_quotes(self, symbols: list[str]) -> DataResult[dict[str, Quote]]:
            return DataResult(data={"MSFT": Quote(
                symbol="MSFT", bid=Decimal(bid), ask=Decimal(ask),
                last=Decimal("420"), timestamp=timestamp, feed="iex",
                trade_timestamp=NOW_13H, fetched_at=NOW_13H,
            )})

    runner = _runner(
        session_factory, _evaluation(_item("MSFT", status)),
        sources=_sources(quotes=Quotes()), universe=["MSFT"],
    )
    report = await runner.run(CYCLE_1246)
    assert report.status == "done"
    assert report.calls == 0
    (decision,) = _decisions(session_factory)
    assert decision.no_call_reason == reason
    assert decision.input_snapshot is not None
    provenance = cast("dict[str, str]", decision.input_snapshot["quote"])
    assert provenance["trade_timestamp"] == NOW_13H.isoformat()


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


# ----------------------------------------------------------------------
# cycles at scale (docs/SPEC.md, Cycles at scale)


class _CountingBars(FakeBarSource):
    """Records the symbol list of every 5m bar request."""

    def __init__(self, calendar: FakeCalendarSource) -> None:
        super().__init__(calendar)
        self.calls: list[list[str]] = []

    async def fetch_5m_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> DataResult[list[Bar5m]]:
        self.calls.append(list(symbols))
        return await super().fetch_5m_bars(symbols, start, end)


class _RecordingQuotes:
    """FakeQuoteSource that records the requested symbols."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def fetch_quotes(
        self, symbols: list[str]
    ) -> DataResult[dict[str, Quote]]:
        self.calls.append(list(symbols))
        return await FakeQuoteSource().fetch_quotes(symbols)


class _RecordingNews:
    """FakeNewsSource that records the requested symbols."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def fetch_news(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> DataResult[list[NewsItem]]:
        self.calls.append(list(symbols))
        return await FakeNewsSource().fetch_news(symbols, start, end)


async def test_cycle_fetches_bars_in_one_multi_symbol_request(
    session_factory: sessionmaker[Session],
) -> None:
    """The whole cycle universe is topped up with one multi-symbol bars
    request — not one call per symbol."""
    bars = _CountingBars(FakeCalendarSource())
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass"), _item("AAPL", "fail")),
        sources=_sources(bars=bars),
        universe=["MSFT", "AAPL", "XOM"],
    )
    report = await runner.run(CYCLE_1246)
    assert report.status == "done"
    # SPY joins the universe for the regime/shock-brake baseline.
    assert bars.calls == [["MSFT", "AAPL", "XOM", "SPY"]]


async def test_cycle_fetches_quotes_for_review_symbols_only(
    session_factory: sessionmaker[Session],
) -> None:
    """Quotes are fetched for the review set — held first, then
    rules-pass candidates — never for symbols the rules rejected."""
    quotes = _RecordingQuotes()
    runner = _runner(
        session_factory,
        _evaluation(
            _item("MSFT", "pass"),
            _item("AAPL", "fail"),
            _item("JPM", "held"),
            held={"JPM"},
        ),
        sources=_sources(quotes=quotes),
        universe=["MSFT", "AAPL", "JPM"],
    )
    await runner.run(CYCLE_1246)
    assert quotes.calls == [["JPM", "MSFT"]]


async def test_cycle_fetches_news_for_called_symbols_only(
    session_factory: sessionmaker[Session],
) -> None:
    """News is fetched only for symbols that get a model call — the
    review order walked until the call cap fills."""
    news = _RecordingNews()
    runner = _runner(
        session_factory,
        _evaluation(
            _item("AAPL", "pass"),
            _item("MSFT", "pass"),
            _item("XOM", "pass"),
        ),
        sources=_sources(news=news, quotes=FakeQuoteSource(FakeClock(NOW_13H))),
        universe=["AAPL", "MSFT", "XOM"],
    )
    runner.config.yaml["decision"]["max_calls_per_cycle"] = 2
    report = await runner.run(CYCLE_1246)
    assert report.calls == 2
    assert news.calls == [["AAPL", "MSFT"]]


class _TickingClock(FakeClock):
    """now() advances by a fixed step on every call — models a slow cycle
    without sleeping."""

    def __init__(self, start: datetime, step: timedelta) -> None:
        super().__init__(start)
        self._step = step

    def now(self) -> datetime:
        current = self._when
        self._when = current + self._step
        return current


async def test_cycle_duration_is_stored(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass")),
        universe=["MSFT"],
    )
    report = await runner.run(CYCLE_1246)
    with session_factory() as session:
        cycle = session.execute(select(Cycle)).scalar_one()
    assert cycle.duration_seconds == 0  # frozen clock → no elapsed time
    assert report.duration_seconds == 0


async def test_slow_cycle_raises_warning_event(
    session_factory: sessionmaker[Session],
) -> None:
    """A cycle over the configured warning limit writes a cycle_slow
    event — the banner the dashboard shows."""
    clock = _TickingClock(NOW_13H, timedelta(minutes=6))
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass")),
        clock=clock,
        universe=["MSFT"],
    )
    report = await runner.run(CYCLE_1246)
    assert report.duration_seconds is not None
    assert report.duration_seconds > 600  # over the 10-minute limit
    with session_factory() as session:
        cycle = session.execute(select(Cycle)).scalar_one()
        event = session.execute(
            select(Event).where(Event.event_type == "cycle_slow")
        ).scalar_one()
    assert cycle.duration_seconds == report.duration_seconds
    assert event.level == "warning"
    assert (event.details or {})["cycle_id"] == cycle.id


@pytest.mark.parametrize(
    ("day", "last", "early_close"),
    [
        (date(2026, 10, 15), time(15, 46), None),  # normal day: 15:46
        (date(2026, 11, 27), time(12, 46), time(13, 0)),  # early close: 12:46
    ],
)
async def test_simulated_last_cycle_of_the_session_runs(
    session_factory: sessionmaker[Session],
    day: date,
    last: time,
    early_close: time | None,
) -> None:
    """At the last cycle's own minute the session state already reads
    "Last cycle done"; the market is still open and the cycle must run
    in simulated mode (PR 13 fix), on early-close days too."""
    from app.engine.cycle import CycleReport
    from app.market.clock import SessionClock

    calendar = FakeCalendarSource(early_closes={day: early_close} if early_close else None)
    scheduled = datetime.combine(day, last, tzinfo=NY_TZ)
    clock = FakeClock(scheduled)
    sources = _sources(
        calendar=calendar, bars=FakeBarSource(calendar), quotes=FakeQuoteSource(clock)
    )
    runner = _runner(session_factory, _evaluation(), sources=sources, clock=clock)
    assert runner._is_last_cycle(scheduled)
    state = SessionClock(clock, runner.config, cast(CalendarProvider, calendar)).state()
    assert state.state == "last_cycle_done"
    assert await runner._market_open(CycleReport())
    report = await runner.run(scheduled)
    assert report.status == "done", report.errors
