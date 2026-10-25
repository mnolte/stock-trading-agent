"""PR 13 "done when": in simulated mode two shadows and the rules-only
baseline run alongside live for a full session without delaying any live
cycle (docs/SPEC.md, Build plan item 13).

The live cycle runner, position checker, executor and fake broker are the
real ones; shadow work is handed off by the same hooks main uses, and only
runs after the live job has returned.
"""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from app.broker.fake import FakeBroker
from app.config import AppConfig
from app.data.cache import BarCache, _naive_utc
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
from app.db.models import Cycle, Decision, Run, ShadowDecision, ShadowEquity, Strategy
from app.decide.client import FakeDecisionClient
from app.decide.service import DecisionService
from app.engine.cycle import CycleRunner
from app.engine.executor import Executor
from app.engine.position_check import PositionChecker
from app.engine.state import ExecutionState
from app.market.calendar import CalendarStore
from app.market.clock import NY_TZ, FakeClock, SessionClock
from app.shadows.portfolio import LiveGateState
from app.shadows.runner import ShadowRunner
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

DAY = date(2026, 10, 15)


@pytest.fixture
def sf() -> sessionmaker[Session]:
    # The warm per-test database (the autouse fixture copies the seeded
    # template): refresh calls hit coverage instead of regenerating bars.
    from app.db.base import get_session_maker

    return get_session_maker()


async def test_full_session_two_shadows_and_rules_only_never_delay_live(
    sf: sessionmaker[Session],
) -> None:
    clock = FakeClock(datetime(2026, 10, 15, 9, 0, tzinfo=NY_TZ))
    config = AppConfig()
    cal_src = FakeCalendarSource()
    sources = DataSources(
        bars=FakeBarSource(cal_src),
        quotes=FakeQuoteSource(clock),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=cal_src,
        alpaca=None,
    )
    calendar = CalendarStore(cal_src)
    await calendar.load(DAY - timedelta(days=600), DAY + timedelta(days=30))
    market_data = MarketDataService(sources, calendar, config, session_factory=sf)
    await market_data.refresh_earnings(DAY - timedelta(days=30), DAY + timedelta(days=90))
    await market_data.refresh_daily(DAY)
    await market_data.refresh_history(DAY)
    from app.signals.service import SignalService

    signals = SignalService(market_data, calendar, config, clock, sf)
    state = ExecutionState(clock=clock)
    broker = FakeBroker(clock)
    executor = Executor(
        broker=broker,
        market_data=market_data,
        signals=signals,
        calendar=calendar,
        config=config,
        clock=clock,
        state=state,
        session_factory=sf,
    )
    await executor.refresh_account()
    live = CycleRunner(
        market_data=market_data,
        signals=signals,
        decide=DecisionService(FakeDecisionClient(enter_symbols={"MSFT"}), config, sf),
        sources=sources,
        calendar=calendar,
        config=config,
        clock=clock,
        executor=executor,
        session_factory=sf,
    )
    checker = PositionChecker(
        executor=executor, state=state, config=config, clock=clock, session_factory=sf
    )
    with sf() as session:
        session.add(
            Run(started_at=_naive_utc(clock.now()), starting_equity=state.account.equity)  # type: ignore[union-attr]
        )
        for name, kind, model, overrides in (
            ("A · Stricter trend", "llm", "gpt-4o-mini", {"rsi_min": 45, "rsi_max": 65}),
            ("B · Bigger model", "llm", "gpt-4.1-mini", None),
            ("Rules only", "rules_only", None, None),
        ):
            session.add(
                Strategy(
                    name=name,
                    kind=kind,
                    prompt_version="decision.v1" if kind == "llm" else None,
                    model=model,
                    temperature=0.0 if kind == "llm" else None,
                    overrides=overrides,
                    status="active",
                    created_at=_naive_utc(clock.now()),
                )
            )
        session.commit()

    clients = {
        "A · Stricter trend": FakeDecisionClient(enter_symbols={"AAPL", "NVDA"}),
        "B · Bigger model": FakeDecisionClient(enter_symbols={"MSFT", "META"}),
    }
    shadows = ShadowRunner(
        config=config,
        clock=clock,
        calendar=calendar,
        client_factory=lambda s: clients[s.name],
        session_factory=sf,
        live_state=lambda: LiveGateState(
            kill_switch_on=state.kill_switch_on,
            paused=state.paused,
            shock_brake_1h=state.shock_brake_1h,
            shock_brake_session=state.shock_brake_session,
        ),
        bars=BarCache(sf).get,
    )
    handed_off: list[Coroutine[Any, Any, object]] = []
    reports: list[Any] = []

    def hand_off(report: Any) -> None:
        reports.append(report)
        handed_off.append(shadows.after_cycle(report.cycle_id or 0, report.shadow_snapshots))

    live.on_cycle_done = hand_off
    # CAT stands in for a symbol only a shadow holds: live builds its
    # snapshot but makes no rules check, model call or decision for it.
    live.shadow_symbols = lambda: [*shadows.held_symbols(), "CAT"]
    checker.on_check_done = lambda: handed_off.append(shadows.after_check())

    session_day = calendar.session_for(DAY)
    assert session_day is not None
    sc = SessionClock(clock, config, calendar)
    cycles = sc.active_cycles(session_day)
    events = sorted(
        [(t, "cycle") for t in cycles] + [(t, "check") for t in sc.active_checks(session_day)]
    )
    assert len(cycles) == 6
    for at, kind in events:
        clock.set(at)
        if kind == "cycle":
            report = await live.run(at)
            assert report.status == "done", report.errors
        else:
            await checker.run()
        # The live job returned before any shadow work ran: the hook only
        # handed it off (main runs it as its own task).
        assert len(handed_off) == 1
        with sf() as session:
            done_before = session.execute(select(func.count(ShadowDecision.id))).scalar_one()
        for coro in handed_off:
            await coro
        handed_off.clear()
        if kind == "cycle":
            with sf() as session:
                done_after = session.execute(select(func.count(ShadowDecision.id))).scalar_one()
            assert done_after > done_before

    assert all("CAT" in r.shadow_snapshots for r in reports)
    cat = reports[-1].shadow_snapshots["CAT"]
    assert cat["context"] == "held" and cat["indicators_1h"]["atr14"] != "unavailable"
    with sf() as session:
        assert (
            session.execute(
                select(func.count(Decision.id)).where(Decision.symbol == "CAT")
            ).scalar_one()
            == 0
        )
        live_cycles = list(
            session.execute(select(Cycle).where(Cycle.kind == "cycle").order_by(Cycle.id)).scalars()
        )
        # Every live cycle started exactly at its scheduled time.
        assert [c.started_at for c in live_cycles] == [_naive_utc(t) for t in cycles]
        strategies = list(session.execute(select(Strategy)).scalars())
        assert len(strategies) == 3
        for strategy in strategies:
            per_cycle = dict(
                session.execute(
                    select(ShadowDecision.cycle_id, func.count(ShadowDecision.id))
                    .where(ShadowDecision.strategy_id == strategy.id)
                    .group_by(ShadowDecision.cycle_id)
                ).all()
            )
            # A decision row for every symbol of every live cycle.
            assert set(per_cycle) == {c.id for c in live_cycles}, strategy.name
            missed = session.execute(
                select(func.count(ShadowDecision.id)).where(
                    ShadowDecision.strategy_id == strategy.id,
                    ShadowDecision.missed.is_(True),
                )
            ).scalar_one()
            assert missed == 0
            equity_points = session.execute(
                select(func.count(ShadowEquity.id)).where(ShadowEquity.strategy_id == strategy.id)
            ).scalar_one()
            assert equity_points == len(events)
        calls = session.execute(
            select(func.count(ShadowDecision.id)).where(ShadowDecision.tokens.is_not(None))
        ).scalar_one()
        assert calls > 0
        # LLM shadows are priced by their own model.
        b_cost = session.execute(
            select(func.sum(ShadowDecision.cost))
            .join(Strategy, ShadowDecision.strategy_id == Strategy.id)
            .where(Strategy.name == "B · Bigger model")
        ).scalar_one()
        assert b_cost is not None and b_cost > 0


async def test_v2_shadow_runs_a_full_session(sf: sessionmaker[Session]) -> None:
    """A decision.v2 shadow completes a simulated session: code-built
    options, stored levels/options columns and chosen option ids
    (docs/SPEC.md, Decision v2)."""
    clock = FakeClock(datetime(2026, 10, 15, 9, 0, tzinfo=NY_TZ))
    config = AppConfig()
    cal_src = FakeCalendarSource()
    sources = DataSources(
        bars=FakeBarSource(cal_src),
        quotes=FakeQuoteSource(clock),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=cal_src,
        alpaca=None,
    )
    calendar = CalendarStore(cal_src)
    await calendar.load(DAY - timedelta(days=600), DAY + timedelta(days=30))
    market_data = MarketDataService(sources, calendar, config, session_factory=sf)
    await market_data.refresh_earnings(DAY - timedelta(days=30), DAY + timedelta(days=90))
    await market_data.refresh_daily(DAY)
    await market_data.refresh_history(DAY)
    from app.signals.service import SignalService

    signals = SignalService(market_data, calendar, config, clock, sf)
    state = ExecutionState(clock=clock)
    executor = Executor(
        broker=FakeBroker(clock), market_data=market_data, signals=signals,
        calendar=calendar, config=config, clock=clock, state=state,
        session_factory=sf,
    )
    await executor.refresh_account()
    live = CycleRunner(
        market_data=market_data, signals=signals,
        decide=DecisionService(FakeDecisionClient(), config, sf),
        sources=sources, calendar=calendar, config=config, clock=clock,
        executor=executor, session_factory=sf,
    )
    with sf() as session:
        session.add(
            Run(started_at=_naive_utc(clock.now()), starting_equity=state.account.equity)  # type: ignore[union-attr]
        )
        session.add(
            Strategy(
                name="C · Options (decision.v2)", kind="llm",
                prompt_version="decision.v2", model="gpt-4o-mini",
                temperature=0.0, overrides=None, status="active",
                created_at=_naive_utc(clock.now()),
            )
        )
        session.commit()

    shadows = ShadowRunner(
        config=config, clock=clock, calendar=calendar,
        client_factory=lambda s: FakeDecisionClient(enter_symbols=set()),
        session_factory=sf,
        live_state=lambda: LiveGateState(
            kill_switch_on=state.kill_switch_on, paused=state.paused,
            shock_brake_1h=state.shock_brake_1h,
            shock_brake_session=state.shock_brake_session,
        ),
        bars=BarCache(sf).get,
    )
    handed_off: list[Any] = []
    live.on_cycle_done = lambda r: handed_off.append(
        shadows.after_cycle(r.cycle_id or 0, r.shadow_snapshots)
    )
    checker = PositionChecker(
        executor=executor, state=state, config=config, clock=clock, session_factory=sf
    )
    checker.on_check_done = lambda: handed_off.append(shadows.after_check())

    session_day = calendar.session_for(DAY)
    assert session_day is not None
    sc = SessionClock(clock, config, calendar)
    cycles = sc.active_cycles(session_day)
    for at in cycles:
        clock.set(at)
        report = await live.run(at)
        assert report.status == "done", report.errors
        for coro in handed_off:
            await coro
        handed_off.clear()

    with sf() as session:
        rows = list(
            session.execute(
                select(ShadowDecision).order_by(ShadowDecision.id)
            ).scalars()
        )
        assert rows, "no shadow decisions"
        assert all(r.prompt_version == "decision.v2" for r in rows)
        # Every row either offered v2 options or explained the miss.
        for r in rows:
            assert r.options_version in ("v2", None)
            if r.model_output:
                # A call happened: the builder's levels/options stored.
                assert r.levels is not None or r.options is not None
            if r.final_action == "no_call" and r.no_call_reason:
                assert r.no_call_reason in (
                    "rules", "call_cap", "no_options", "model_error"
                )
        # The fake answered skip everywhere it was asked.
        assert any(r.model_output for r in rows)
