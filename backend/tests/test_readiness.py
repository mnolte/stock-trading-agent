"""Readiness: the 15 criteria, snapshots, errors and manual items
(docs/SPEC.md, Readiness). Injected clock, isolated database, no network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from app.config import AppConfig
from app.data.cache import _naive_utc
from app.db.base import Base
from app.db.models import (
    Cycle,
    Decision,
    EquitySnapshot,
    Event,
    Position,
    ReadinessSnapshot,
    Run,
    SettingsVersion,
    ShadowPosition,
    Strategy,
)
from app.engine.state import infer_affects, record_event
from app.market.calendar import (
    CalendarProvider,
    StaticCalendarProvider,
    WeekdayCalendarProvider,
)
from app.market.clock import FakeClock
from app.readiness import service
from app.readiness.service import (
    ReadinessError,
    apply_auto_resolution,
    dismiss_error,
    due_session_dates,
    evaluate,
    payload,
    set_manual_item,
    unresolved_errors,
    write_snapshot,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

# Monday 5 October 2026 09:30 New York — the run's start, naive UTC.
RUN_START = datetime(2026, 10, 5, 13, 30)
# Friday 9 October 2026 16:30 New York — after the week's last close.
NOW = datetime(2026, 10, 9, 20, 30, tzinfo=UTC)
SESSION = date(2026, 10, 9)


@pytest.fixture
def sf(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'readiness.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def calendar() -> WeekdayCalendarProvider:
    return WeekdayCalendarProvider()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


class Notes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def notify(self, event: str, line: str, **_kw: object) -> None:
        self.sent.append((event, line))


def _override(config: AppConfig, **readiness: object) -> AppConfig:
    """Config with readiness thresholds replaced (versioned settings)."""
    config.apply_saved({"readiness": readiness}, 1)
    return config


def _run(session: Session, started: datetime = RUN_START) -> Run:
    run = Run(started_at=started, starting_equity=Decimal("10000"))
    session.add(run)
    session.flush()
    return run


def _equity(
    session: Session,
    when: datetime,
    equity: str | float,
    spy: str | float | None = None,
) -> None:
    session.add(
        EquitySnapshot(
            time=when,
            equity=Decimal(str(equity)),
            cash="9000",
            spy_benchmark_value=Decimal(str(spy)) if spy is not None else None,
        )
    )
    session.flush()


def _cycle(
    session: Session,
    scheduled: datetime,
    *,
    status: str = "done",
    kind: str = "cycle",
    errors: list[str] | None = None,
) -> Cycle:
    cycle = Cycle(
        kind=kind,
        scheduled_time=scheduled,
        started_at=scheduled,
        finished_at=scheduled + timedelta(minutes=2),
        status=status,
        errors=errors,
    )
    session.add(cycle)
    session.flush()
    return cycle


def _entry_decision(
    session: Session,
    *,
    cycle_id: int,
    symbol: str,
    final_action: str = "enter",
    model_action: str | None = "enter",
    confidence: float | None = 0.6,
    stop_price: float | None = 90.0,
    no_call_reason: str | None = None,
) -> Decision:
    output: dict[str, object] = {"action": model_action or "skip"}
    if confidence is not None:
        output["confidence"] = confidence
    if stop_price is not None:
        output["stop_price"] = stop_price
    decision = Decision(
        cycle_id=cycle_id,
        symbol=symbol,
        context="candidate",
        model_output=output,
        validation_result={
            "valid": True,
            "model_action": model_action,
            "final_action": final_action,
        },
        final_action=final_action,
        no_call_reason=no_call_reason,
        prompt_version="decision.v1",
        prompt_hash="hash",
        model="fake",
    )
    session.add(decision)
    session.flush()
    return decision


def _trade(
    session: Session,
    *,
    symbol: str,
    pnl_raw: str,
    pnl_adjusted: str | None,
    entry: str = "100",
    stop: str | None = "90",
    qty: int = 1,
    confidence: float | None = None,
    cycle: Cycle | None = None,
    origin: str = "agent",
    opened: datetime = RUN_START,
    closed: datetime = RUN_START + timedelta(days=1),
) -> Position:
    """A closed agent trade; ``stop`` becomes the entry decision's stop."""
    decision: Decision | None = None
    if origin == "agent" and (stop is not None or confidence is not None):
        if cycle is None:
            cycle = _cycle(session, opened)
        decision = _entry_decision(
            session,
            cycle_id=cycle.id,
            symbol=symbol,
            confidence=confidence,
            stop_price=float(stop) if stop is not None else None,
        )
    position = Position(
        symbol=symbol,
        origin=origin,
        entry_decision_id=decision.id if decision else None,
        qty=qty,
        avg_entry_price=Decimal(entry),
        opened_at=opened,
        closed_at=closed,
        realized_pnl_raw=Decimal(pnl_raw),
        realized_pnl_adjusted=(
            Decimal(pnl_adjusted) if pnl_adjusted is not None else None
        ),
        exit_reason="target",
    )
    session.add(position)
    session.flush()
    return position


def _evaluate(
    session: Session,
    *,
    config: AppConfig,
    calendar: CalendarProvider,
    clock: FakeClock,
    session_date: date = SESSION,
    as_of: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    criteria = evaluate(
        session,
        config=config,
        calendar=calendar,
        clock=clock,
        session_date=session_date,
        as_of=_naive_utc(as_of or clock.now()),
    )
    return {c["key"]: c for c in criteria}


def _strategy(
    session: Session,
    *,
    kind: str = "rules_only",
    status: str = "active",
    created_at: datetime = RUN_START - timedelta(days=1),
) -> Strategy:
    strategy = Strategy(
        name="Rules only",
        kind=kind,
        status=status,
        created_at=created_at,
    )
    session.add(strategy)
    session.flush()
    return strategy


def _shadow_trade(
    session: Session,
    strategy: Strategy,
    run: Run,
    symbol: str,
    r: str,
    closed: datetime,
) -> None:
    session.add(
        ShadowPosition(
            strategy_id=strategy.id,
            run_id=run.id,
            symbol=symbol,
            status="closed",
            qty=1,
            initial_stop=Decimal("90"),
            stop=Decimal("90"),
            created_at=closed - timedelta(hours=2),
            expires_at=closed,
            opened_at=closed - timedelta(hours=1),
            closed_at=closed,
            pnl=Decimal("5"),
            r=Decimal(r),
        )
    )
    session.flush()


# ---------------------------------------------------------------------------
# criteria


def test_closed_trades_boundary(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    config = _override(config, min_closed_trades=4)
    with sf() as session:
        _run(session)
        for i in range(3):
            _trade(session, symbol=f"T{i}", pnl_raw="5", pnl_adjusted="5")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["closed_trades"]["status"] == "not_yet"
        assert criteria["closed_trades"]["value"] == "3"
        _trade(session, symbol="T3", pnl_raw="5", pnl_adjusted="5")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["closed_trades"]["status"] == "pass"


def test_sessions_boundary(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    # 5 sessions this week (Mon–Fri); require exactly 5 and then 6.
    with sf() as session:
        _run(session)
        criteria = _evaluate(
            session,
            config=_override(config, min_sessions=5),
            calendar=calendar,
            clock=clock,
        )
        assert criteria["sessions"]["status"] == "pass"
        criteria = _evaluate(
            session,
            config=_override(config, min_sessions=6),
            calendar=calendar,
            clock=clock,
        )
        assert criteria["sessions"]["status"] == "not_yet"
        assert criteria["sessions"]["estimate"] is not None


def test_avg_r_waits_below_minimum_then_passes(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    """29 vs 30 closed trades: below the minimum the status is waiting."""
    with sf() as session:
        _run(session)
        # entry 100 stop 90 qty 1 → risk 10; adjusted +5 → +0.5R each.
        for i in range(29):
            _trade(session, symbol=f"R{i}", pnl_raw="5", pnl_adjusted="5")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["avg_r"]["status"] == "waiting"
        assert criteria["avg_r"]["sample"] == 29
        _trade(session, symbol="R29", pnl_raw="5", pnl_adjusted="5")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["avg_r"]["status"] == "pass"
        assert criteria["avg_r"]["sample"] == 30


def test_avg_r_not_yet_when_low(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        _run(session)
        # +1.9 adjusted on 10 risk → +0.19R, under the +0.2R target.
        for i in range(30):
            _trade(
                session, symbol=f"L{i}", pnl_raw="1.9", pnl_adjusted="1.9"
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["avg_r"]["status"] == "not_yet"


def test_profit_factor_boundary(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        _run(session)
        for i in range(29):
            _trade(session, symbol=f"P{i}", pnl_raw="5", pnl_adjusted="5")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["profit_factor"]["status"] == "waiting"
        # The 30th trade is a loss: 29 wins ×5 / 1 loss ×5 → pf 29, passes.
        _trade(session, symbol="P29", pnl_raw="-5", pnl_adjusted="-5")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["profit_factor"]["status"] == "pass"


def test_profit_factor_not_yet(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        _run(session)
        for i in range(15):
            _trade(session, symbol=f"W{i}", pnl_raw="5", pnl_adjusted="5")
        for i in range(15):
            _trade(session, symbol="X" + str(i), pnl_raw="-5", pnl_adjusted="-5")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["profit_factor"]["status"] == "not_yet"


def test_return_vs_spy(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        _run(session)
        _equity(session, RUN_START + timedelta(days=4, hours=6), "10100", "10050")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        # adjusted +1.0% vs SPY +0.5% → ahead.
        assert criteria["return_vs_spy"]["status"] == "pass"
    with sf() as session:
        _run(session)
        _equity(session, RUN_START + timedelta(days=4, hours=6), "10100", "10300")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["return_vs_spy"]["status"] == "not_yet"


def test_max_drawdown_boundary(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        _run(session)
        _equity(session, RUN_START, "10000")
        _equity(session, RUN_START + timedelta(days=2), "10600")
        # 5.9% drawdown from the 10600 peak → passes "under 6%".
        _equity(session, RUN_START + timedelta(days=3), "9974.6")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["max_drawdown"]["status"] == "pass"
    with sf() as session:
        _run(session)
        _equity(session, RUN_START, "10000")
        _equity(session, RUN_START + timedelta(days=2), "10600")
        # 6.1% drawdown → fails.
        _equity(session, RUN_START + timedelta(days=3), "9953.4")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["max_drawdown"]["status"] == "not_yet"


def test_beats_rules_only_waits_when_shadow_started_late(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        run = _run(session)
        _strategy(session, created_at=RUN_START + timedelta(days=2))
        for i in range(30):
            _trade(session, symbol=f"B{i}", pnl_raw="8", pnl_adjusted="8")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["beats_rules_only"]["status"] == "waiting"
        assert "started after" in (criteria["beats_rules_only"]["sub"] or "")
        assert run.id


def test_beats_rules_only_needs_thirty_each(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        run = _run(session)
        shadow = _strategy(session)
        for i in range(30):
            _trade(session, symbol=f"S{i}", pnl_raw="8", pnl_adjusted="8")
            _shadow_trade(
                session,
                shadow,
                run,
                symbol=f"S{i}",
                r="0.5",
                closed=RUN_START + timedelta(hours=i + 1),
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        # live +0.8R vs shadow +0.5R → pass.
        assert criteria["beats_rules_only"]["status"] == "pass"
    with sf() as session:
        run = _run(session)
        shadow = _strategy(session)
        for i in range(30):
            _trade(session, symbol=f"S{i}", pnl_raw="8", pnl_adjusted="8")
        for i in range(29):
            _shadow_trade(
                session,
                shadow,
                run,
                symbol=f"S{i}",
                r="0.5",
                closed=RUN_START + timedelta(hours=i + 1),
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["beats_rules_only"]["status"] == "waiting"


def test_beats_rules_only_not_yet(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        run = _run(session)
        shadow = _strategy(session)
        for i in range(30):
            _trade(session, symbol=f"D{i}", pnl_raw="4", pnl_adjusted="4")
            _shadow_trade(
                session,
                shadow,
                run,
                symbol=f"D{i}",
                r="0.6",
                closed=RUN_START + timedelta(hours=i + 1),
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        # live +0.4R vs shadow +0.6R → not yet.
        assert criteria["beats_rules_only"]["status"] == "not_yet"


def test_proposals_waiting_then_boundary(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        _run(session)
        cycle = _cycle(session, RUN_START + timedelta(hours=1))
        for i in range(29):
            _entry_decision(
                session,
                cycle_id=cycle.id,
                symbol=f"E{i}",
                final_action="enter",
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["proposals"]["status"] == "waiting"
        # 30 proposals, all passed → 100% ≥ 75%.
        _entry_decision(
            session, cycle_id=cycle.id, symbol="E29", final_action="enter"
        )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["proposals"]["status"] == "pass"
        # 8 more proposals rejected by code → 30/38 = 78.9% still passes;
        # 10 more → 30/48 = 62.5% fails.
        for i in range(18):
            _entry_decision(
                session,
                cycle_id=cycle.id,
                symbol=f"F{i}",
                final_action="skip",
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["proposals"]["status"] == "not_yet"
        assert "30 of 48" in criteria["proposals"]["value"]


def test_calibration_skips_small_groups(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    """Confidence groups under 10 entries are skipped, not averaged in."""
    with sf() as session:
        _run(session)
        cycle = _cycle(session, RUN_START + timedelta(hours=1))
        # 41 entries at 0.95 confidence, 39 winners → gap ≈ 0.
        for i in range(41):
            win = i < 39
            _trade(
                session,
                symbol=f"C{i}",
                pnl_raw="5" if win else "-5",
                pnl_adjusted="5" if win else "-5",
                confidence=0.95,
                cycle=cycle,
            )
        # 9 entries at 0.1, all winners — a group under 10 that would
        # wreck the gap if it counted.
        for i in range(9):
            _trade(
                session,
                symbol=f"c{i}",
                pnl_raw="5",
                pnl_adjusted="5",
                confidence=0.1,
                cycle=cycle,
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["calibration"]["status"] == "pass"
        assert criteria["calibration"]["sample"] == 50


def test_calibration_waits_below_fifty(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        _run(session)
        cycle = _cycle(session, RUN_START + timedelta(hours=1))
        for i in range(49):
            _trade(
                session,
                symbol=f"G{i}",
                pnl_raw="5",
                pnl_adjusted="5",
                confidence=0.95,
                cycle=cycle,
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["calibration"]["status"] == "waiting"
        assert "50" in (criteria["calibration"]["sub"] or "")


def test_cycles_completed_boundary(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    """98% pass, 96% not yet — the spec's ≈98% boundary."""
    with sf() as session:
        _run(session)
        for i in range(50):
            _cycle(
                session,
                RUN_START + timedelta(hours=i % 24, days=i // 24),
                status="done" if i < 49 else "error",
                errors=["model call failed"] if i == 49 else None,
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["cycles"]["status"] == "pass"  # 49/50 = 98%
        assert "1 missed" in (criteria["cycles"]["sub"] or "")
    with sf() as session:
        _run(session)
        for i in range(50):
            _cycle(
                session,
                RUN_START + timedelta(hours=i % 24, days=i // 24),
                status="done" if i < 48 else "error",
                errors=["model call failed"] if i >= 48 else None,
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["cycles"]["status"] == "not_yet"  # 48/50 = 96%


def test_zero_tolerance_criteria(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    """Kill switch events, unresolved errors and reconciliation mismatches
    fail the moment one is open; none at all passes."""
    with sf() as session:
        _run(session)
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        for key in ("kill_switch", "unresolved_errors", "reconciliation"):
            assert criteria[key]["status"] == "pass", key

        session.add(
            Event(
                time=RUN_START + timedelta(hours=2),
                level="critical",
                event_type="kill_switch",
                details={"on": True, "reason": "manual"},
            )
        )
        session.add(
            Event(
                time=clock.now() - timedelta(minutes=5),
                level="critical",
                event_type="order_submit_failed",
                details={"symbol": "AAPL"},
                affects="order",
            )
        )
        session.add(
            Event(
                time=RUN_START + timedelta(hours=3),
                level="critical",
                event_type="reconciliation",
                details={"kind": "qty_mismatch"},
            )
        )
        session.flush()
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        for key in ("kill_switch", "unresolved_errors", "reconciliation"):
            assert criteria[key]["status"] == "not_yet", key


def test_errors_without_links_do_not_count(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        _run(session)
        session.add(
            Event(
                time=clock.now() - timedelta(minutes=5),
                level="critical",
                event_type="universe_screen_failed",
                details={"error": "timeout"},
                affects="none",
            )
        )
        session.flush()
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["unresolved_errors"]["status"] == "pass"


def test_price_blocked_boundary(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        _run(session)
        cycle = _cycle(session, RUN_START + timedelta(hours=1))
        # 18 checked + 1 blocked as price_stale → 5.26% > 5%.
        for i in range(18):
            _entry_decision(
                session,
                cycle_id=cycle.id,
                symbol=f"Q{i}",
                final_action="skip",
                model_action="skip",
            )
        _entry_decision(
            session,
            cycle_id=cycle.id,
            symbol="QB",
            final_action="no_call",
            model_action=None,
            no_call_reason="price_stale",
        )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["price_blocked"]["status"] == "not_yet"
        # 20 checked + 1 blocked → 4.76% ≤ 5%.
        for i in range(2):
            _entry_decision(
                session,
                cycle_id=cycle.id,
                symbol=f"R{i}",
                final_action="skip",
                model_action="skip",
            )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["price_blocked"]["status"] == "pass"


def test_estimates_from_pace_and_calendar(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    """Estimates: closed-trades pace projects a date on the trading
    calendar; sessions project straight from the calendar."""
    config = _override(config, min_closed_trades=10, min_sessions=8)
    with sf() as session:
        _run(session)
        # 2 closed trades over 5 sessions → pace 0.4/session →
        # 8 more needed → 20 sessions ahead of Friday 9 Oct.
        _trade(session, symbol="E1", pnl_raw="5", pnl_adjusted="5")
        _trade(session, symbol="E2", pnl_raw="5", pnl_adjusted="5")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        closed = criteria["closed_trades"]
        assert closed["status"] == "not_yet"
        assert "20 sessions" in (closed["sub"] or "")
        expected = service._date_after_sessions(calendar, SESSION, 20)
        assert expected is not None
        assert closed["estimate_date"] == expected.isoformat()
        # Sessions: 3 more needed → 3rd trading day after Friday.
        sess = criteria["sessions"]
        expected_sess = service._date_after_sessions(calendar, SESSION, 3)
        assert expected_sess is not None
        assert sess["estimate_date"] == expected_sess.isoformat()
        # Earliest possible = the later of the two.
        assert expected > expected_sess


def test_estimate_extrapolates_past_the_loaded_calendar(
    sf: sessionmaker[Session],
    config: AppConfig,
    clock: FakeClock,
) -> None:
    """The store only loads ~30 days ahead; an estimate beyond that
    horizon extrapolates weekdays (the UI marks it "≈")."""
    weekdays = WeekdayCalendarProvider()
    loaded = [
        s
        for i in range(12)
        if (
            s := weekdays.session_for(date(2026, 10, 5) + timedelta(days=i))
        )
        is not None
    ]
    short_calendar = StaticCalendarProvider(loaded)
    assert short_calendar.next_trading_day(date(2026, 10, 16)) is None

    # Past the loaded Friday the estimate keeps stepping weekdays: it
    # lands on the same date the full weekday calendar computes.
    estimate = service._date_after_sessions(short_calendar, SESSION, 20)
    assert estimate == service._date_after_sessions(weekdays, SESSION, 20)

    config = _override(config, min_sessions=20)
    with sf() as session:
        _run(session)
        criteria = _evaluate(
            session, config=config, calendar=short_calendar, clock=clock
        )
        assert criteria["sessions"]["estimate_date"] is not None


def test_missing_stop_counts_only_after_a_second_check(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    """One check finding a missing stop doesn't count — the protective
    stop lands immediately. Still missing at the next check does."""
    with sf() as session:
        _run(session)
        check1_at = RUN_START + timedelta(hours=1)
        _cycle(session, check1_at, kind="position_check")
        # Missing at check1 and the protective placement failed — the
        # position stays unprotected.
        session.add(
            Event(
                time=check1_at + timedelta(minutes=1),
                level="critical",
                event_type="protective_stop_failed",
                details={"symbol": "AAPL", "error": "rejected"},
                affects="order",
            )
        )
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["stops_missing"]["status"] == "pass"
        # The next check finds the same symbol without a stop again —
        # it was exposed for longer than one check.
        check2_at = RUN_START + timedelta(hours=1, minutes=15)
        _cycle(session, check2_at, kind="position_check")
        session.add(
            Event(
                time=check2_at + timedelta(minutes=1),
                level="critical",
                event_type="protective_stop",
                details={"symbol": "AAPL", "qty": 4, "stop": "90"},
                affects="order",
            )
        )
        session.flush()
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["stops_missing"]["status"] == "not_yet"
        assert criteria["stops_missing"]["value"] == "1"


def test_new_run_resets_evidence(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    config = _override(config, min_closed_trades=4)
    with sf() as session:
        _run(session)
        for i in range(4):
            _trade(session, symbol=f"N{i}", pnl_raw="5", pnl_adjusted="5")
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["closed_trades"]["status"] == "pass"
        # A new run starts over; the old trades belong to run 1.
        _run(session, started=RUN_START + timedelta(days=3))
        criteria = _evaluate(
            session, config=config, calendar=calendar, clock=clock
        )
        assert criteria["closed_trades"]["status"] == "not_yet"
        assert criteria["closed_trades"]["value"] == "0"


# ---------------------------------------------------------------------------
# errors


def test_infer_affects() -> None:
    assert infer_affects({"fill_id": 7}) == "fill"
    assert infer_affects({"order_id": "x"}) == "order"
    assert infer_affects({"client_order_id": "sa-1"}) == "order"
    assert infer_affects({"decision_id": 3}) == "decision"
    assert infer_affects({"symbol": "AAPL"}) == "none"
    assert infer_affects(None) == "none"


def test_error_auto_resolves_after_an_hour(
    sf: sessionmaker[Session], clock: FakeClock
) -> None:
    with sf() as session:
        record_event(
            session,
            clock,
            level="critical",
            event_type="order_submit_failed",
            details={"symbol": "AAPL"},
            affects="order",
        )
        session.flush()
        assert unresolved_errors(
            session, as_of=clock.now(), window_start=None, minutes=60
        )
        # 61 minutes quiet → auto-resolved at last recurrence + 60.
        clock.advance(timedelta(minutes=61))
        resolved = apply_auto_resolution(session, now=clock.now(), minutes=60)
        assert resolved == 1
        event = session.execute(select(Event)).scalar_one()
        assert event.resolution == "auto"
        # Resolved as of the quiet deadline, not as of the tick that ran.
        assert event.resolved_at == event.time + timedelta(minutes=60)
        assert not unresolved_errors(
            session, as_of=clock.now(), window_start=None, minutes=60
        )


def test_error_recurrence_resets_the_timer(
    sf: sessionmaker[Session], clock: FakeClock
) -> None:
    with sf() as session:
        record_event(
            session,
            clock,
            level="critical",
            event_type="order_submit_failed",
            details={"symbol": "AAPL"},
            affects="order",
        )
        clock.advance(timedelta(minutes=45))
        # Same error type again — the quiet timer starts over.
        record_event(
            session,
            clock,
            level="critical",
            event_type="order_submit_failed",
            details={"symbol": "AAPL"},
            affects="order",
        )
        clock.advance(timedelta(minutes=45))
        assert apply_auto_resolution(session, now=clock.now(), minutes=60) == 0
        assert unresolved_errors(
            session, as_of=clock.now(), window_start=None, minutes=60
        )
        clock.advance(timedelta(minutes=16))
        assert apply_auto_resolution(session, now=clock.now(), minutes=60) == 2


def test_dismiss_requires_a_note(sf: sessionmaker[Session], clock: FakeClock) -> None:
    with sf() as session:
        event = record_event(
            session,
            clock,
            level="critical",
            event_type="order_submit_failed",
            details={"symbol": "AAPL"},
            affects="order",
        )
        with pytest.raises(ReadinessError):
            dismiss_error(session, clock=clock, event_id=event.id, note="  ")
        dismissed = dismiss_error(
            session, clock=clock, event_id=event.id, note="broker hiccup"
        )
        assert dismissed.resolution == "dismissed"
        assert dismissed.note == "broker hiccup"
        assert not unresolved_errors(
            session, as_of=clock.now(), window_start=None, minutes=60
        )
        # An event that touched nothing cannot be dismissed as an error.
        other = record_event(
            session,
            clock,
            level="warning",
            event_type="universe_screen_failed",
            details={"error": "x"},
        )
        with pytest.raises(ReadinessError):
            dismiss_error(session, clock=clock, event_id=other.id, note="n")


# ---------------------------------------------------------------------------
# manual items and history


def test_manual_item_requires_note_and_records_history(
    sf: sessionmaker[Session], clock: FakeClock, config: AppConfig
) -> None:
    with sf() as session:
        with pytest.raises(ReadinessError):
            set_manual_item(session, clock=clock, item="sip_data", done=True, note="")
        with pytest.raises(ReadinessError):
            set_manual_item(session, clock=clock, item="nope", done=True, note="x")
        set_manual_item(
            session, clock=clock, item="sip_data", done=True, note="ordered"
        )
        data = payload(session, config=config, clock=clock)
        item = next(m for m in data["manual"] if m["item"] == "sip_data")
        assert item["done"] is True and item["note"] == "ordered"
        assert data["summary"]["manual_done"] == 1
        # Unticking also needs a note and lands in History.
        with pytest.raises(ReadinessError):
            set_manual_item(session, clock=clock, item="sip_data", done=False, note="")
        set_manual_item(
            session,
            clock=clock,
            item="sip_data",
            done=False,
            note="cancelled, wrong plan",
        )
        data = payload(session, config=config, clock=clock)
        manual_changes = [h for h in data["history"] if h["kind"] == "manual"]
        texts = sorted(h["change"] for h in manual_changes)
        assert texts == [
            "Real-time SIP market data ticked — ordered",
            "Real-time SIP market data unticked — cancelled, wrong plan",
        ]


# ---------------------------------------------------------------------------
# snapshots


def test_snapshot_written_after_close_and_read_from_store(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    """The tick writes one snapshot per closed session; the payload reads
    the stored row and never re-evaluates."""
    config = _override(config, min_closed_trades=1)
    with sf() as session:
        _run(session)
        _trade(session, symbol="S", pnl_raw="5", pnl_adjusted="5")
        # During Friday's session the earlier closed days are due — the
        # tick backfills — but today's close hasn't happened yet.
        clock.set(datetime(2026, 10, 9, 19, 0, tzinfo=UTC))  # 15:00 NY
        assert due_session_dates(session, calendar=calendar, clock=clock) == [
            date(2026, 10, 5),
            date(2026, 10, 6),
            date(2026, 10, 7),
            date(2026, 10, 8),
        ]
        clock.set(datetime(2026, 10, 9, 20, 30, tzinfo=UTC))  # 16:30 NY
        assert due_session_dates(session, calendar=calendar, clock=clock)[
            -1
        ] == SESSION
        snapshot = write_snapshot(
            session,
            config=config,
            calendar=calendar,
            clock=clock,
            session_date=SESSION,
        )
        session.commit()
        assert snapshot is not None
        assert snapshot.session_date == SESSION
        stored = {
            c["key"]: c["status"] for c in snapshot.criteria
        }
        assert stored["closed_trades"] == "pass"

    # A fresh session — the "restart" — reads the stored row; evaluate is
    # never called on a page request.
    with sf() as session:
        data = payload(session, config=config, clock=clock)
        assert data["snapshot"]["session_date"] == "2026-10-09"
        statuses = {c["key"]: c["status"] for c in data["snapshot"]["criteria"]}
        assert statuses == stored


def test_payload_never_evaluates(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with sf() as session:
        _run(session)
        write_snapshot(
            session,
            config=config,
            calendar=calendar,
            clock=clock,
            session_date=SESSION,
        )
        session.commit()
        monkeypatch.setattr(
            service,
            "evaluate",
            lambda *_a, **_kw: pytest.fail("page request evaluated"),
        )
        data = payload(session, config=config, clock=clock)
        assert data["snapshot"]["session_date"] == "2026-10-09"


def test_snapshot_once_per_day(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    with sf() as session:
        _run(session)
        write_snapshot(
            session,
            config=config,
            calendar=calendar,
            clock=clock,
            session_date=SESSION,
        )
        write_snapshot(
            session,
            config=config,
            calendar=calendar,
            clock=clock,
            session_date=SESSION,
        )
        session.commit()
        count = session.execute(select(ReadinessSnapshot)).scalars().all()
        assert len(count) == 1


def test_status_change_fires_notification(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    config = _override(config, min_closed_trades=2)
    notes = Notes()
    with sf() as session:
        _run(session)
        write_snapshot(
            session,
            config=config,
            calendar=calendar,
            clock=clock,
            session_date=SESSION,
            notifier=notes,
        )
        assert notes.sent == []
        _trade(session, symbol="N", pnl_raw="5", pnl_adjusted="5")
        _trade(session, symbol="N2", pnl_raw="5", pnl_adjusted="5")
        next_day = SESSION + timedelta(days=4)  # weekend → Tuesday 13 Oct? use Mon 12 Oct
        next_day = date(2026, 10, 12)
        clock.set(datetime(2026, 10, 12, 20, 30, tzinfo=UTC))
        write_snapshot(
            session,
            config=config,
            calendar=calendar,
            clock=clock,
            session_date=next_day,
            notifier=notes,
        )
        assert ("readiness_changed", "Closed trades now passes (2)") in [
            (e, line) for e, line in notes.sent
        ]


def test_tick_writes_due_snapshots(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    published: list[set[str]] = []
    with sf() as session:
        _run(session)
        session.commit()
    written = service.tick(
        config=config,
        calendar=calendar,
        clock=clock,
        session_factory=sf,
        publish=published.append,
    )
    # Every closed session of the run's first week is backfilled.
    assert written == 5
    assert published == [{"readiness"}]
    with sf() as session:
        rows = session.execute(
            select(ReadinessSnapshot).order_by(ReadinessSnapshot.session_date)
        ).scalars().all()
        assert [r.session_date for r in rows] == [
            date(2026, 10, 5) + timedelta(days=i) for i in range(5)
        ]
    # A second tick the same evening writes nothing new.
    written = service.tick(
        config=config,
        calendar=calendar,
        clock=clock,
        session_factory=sf,
    )
    assert written == 0


def test_history_records_criterion_changes(
    sf: sessionmaker[Session],
    config: AppConfig,
    calendar: WeekdayCalendarProvider,
    clock: FakeClock,
) -> None:
    config = _override(config, min_closed_trades=1)
    with sf() as session:
        _run(session)
        write_snapshot(
            session,
            config=config,
            calendar=calendar,
            clock=clock,
            session_date=SESSION,
        )
        _trade(session, symbol="H", pnl_raw="5", pnl_adjusted="5")
        clock.set(datetime(2026, 10, 12, 20, 30, tzinfo=UTC))
        write_snapshot(
            session,
            config=config,
            calendar=calendar,
            clock=clock,
            session_date=date(2026, 10, 12),
        )
        session.commit()
        data = payload(session, config=config, clock=clock)
        changes = [h["change"] for h in data["history"]]
        assert any("Run started" in c for c in changes)
        assert any("Closed trades passes" in c for c in changes)


def test_settings_change_shows_in_history(
    sf: sessionmaker[Session],
    config: AppConfig,
    clock: FakeClock,
) -> None:
    """A readiness threshold change is a versioned settings change and
    shows in History with its reason (docs/SPEC.md, Readiness)."""
    with sf() as session:
        _run(session)
        session.add(
            SettingsVersion(
                version=1,
                settings={"readiness": {"min_closed_trades": 100}},
                changed_at=RUN_START,
                reason="defaults",
            )
        )
        session.add(
            SettingsVersion(
                version=2,
                settings={"readiness": {"min_closed_trades": 150}},
                changed_at=RUN_START + timedelta(days=1),
                reason="stricter evidence",
            )
        )
        session.commit()
        data = payload(session, config=config, clock=clock)
        changes = [h["change"] for h in data["history"]]
        assert (
            "Readiness closed trades 100 → 150 — stricter evidence"
            in changes
        )


def test_manual_state_all_five_items(
    sf: sessionmaker[Session], config: AppConfig, clock: FakeClock
) -> None:
    with sf() as session:
        data = payload(session, config=config, clock=clock)
        items = {m["item"] for m in data["manual"]}
        assert items == {
            "sip_data",
            "tax",
            "live_spec",
            "hosting",
            "live_size",
        }
        assert all(m["done"] is False for m in data["manual"])


# ---------------------------------------------------------------------------
# API


def test_readiness_api_shape(client: Any) -> None:
    response = client.get("/api/readiness")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "run",
        "snapshot",
        "summary",
        "manual",
        "errors",
        "history",
    }
    assert len(data["manual"]) == 5
    assert data["summary"]["manual_total"] == 5


def test_manual_item_api_requires_note(client: Any) -> None:
    response = client.post(
        "/api/readiness/manual", json={"item": "tax", "done": True, "note": ""}
    )
    assert response.status_code == 422
    response = client.post(
        "/api/readiness/manual",
        json={"item": "tax", "done": True, "note": "called the advisor"},
    )
    assert response.status_code == 200
    assert response.json()["saved"] is True
    data = client.get("/api/readiness").json()
    item = next(m for m in data["manual"] if m["item"] == "tax")
    assert item["done"] is True and item["note"] == "called the advisor"


def test_dismiss_event_api(client: Any) -> None:
    from app.db.base import get_session_maker

    with get_session_maker()() as session:
        event = Event(
            time=datetime(2026, 10, 15, 13, 30),
            level="critical",
            event_type="order_submit_failed",
            details={"symbol": "AAPL"},
            affects="order",
        )
        session.add(event)
        session.commit()
        event_id = event.id

    response = client.post(f"/api/events/{event_id}/dismiss", json={"note": ""})
    assert response.status_code == 422
    response = client.post(
        f"/api/events/{event_id}/dismiss", json={"note": "transient, checked"}
    )
    assert response.status_code == 200
    with get_session_maker()() as session:
        stored = session.get(Event, event_id)
        assert stored is not None
        assert stored.resolution == "dismissed"
        assert stored.note == "transient, checked"
    # Already resolved — a second dismiss conflicts.
    response = client.post(
        f"/api/events/{event_id}/dismiss", json={"note": "again"}
    )
    assert response.status_code == 409
