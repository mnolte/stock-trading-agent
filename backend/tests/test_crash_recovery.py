"""Tests for crash recovery and graceful shutdown (Build plan, PR 8)."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.config import get_config
from app.db.base import Base
from app.db.models import Cycle
from app.engine.state import (
    ExecutionState,
    finalize_interrupted_cycles,
    load_execution_state,
    recover_interrupted_cycles,
)
from app.market.clock import Clock, FakeClock
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def clock() -> Clock:
    return FakeClock(datetime(2026, 10, 15, 18, 0, tzinfo=UTC))


def _insert_cycle(
    session_factory: sessionmaker[Session],
    status: str,
    kind: str = "cycle",
    errors: list[str] | None = None,
) -> int:
    with session_factory() as session:
        cycle = Cycle(
            kind=kind,
            scheduled_time=datetime.now(UTC) - timedelta(minutes=5),
            started_at=datetime.now(UTC) - timedelta(minutes=5),
            status=status,
            errors=errors,
        )
        session.add(cycle)
        session.commit()
        return cycle.id


def test_running_cycle_is_recovered_as_error(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """A cycle left 'running' by a crash is finalised as a failed cycle."""
    cycle_id = _insert_cycle(session_factory, "running")
    state = ExecutionState(clock=clock)

    with session_factory() as session:
        recovered = recover_interrupted_cycles(session, state, clock)
        session.commit()

    assert recovered == 1
    assert state.failed_cycles == 1
    assert state.failed_checks == 0
    with session_factory() as session:
        row = session.execute(select(Cycle).where(Cycle.id == cycle_id)).scalar_one()
        assert row.status == "error"
        assert row.errors == ["interrupted by app crash"]
        assert row.finished_at is not None


def test_running_position_check_counts_as_failed_check(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """A crashed position check feeds the check counter, not the cycle one."""
    _insert_cycle(session_factory, "running", kind="position_check")
    state = ExecutionState(clock=clock)

    with session_factory() as session:
        recovered = recover_interrupted_cycles(session, state, clock)
        session.commit()

    assert recovered == 1
    assert state.failed_cycles == 0
    assert state.failed_checks == 1


def test_finished_cycle_is_not_touched(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """A cycle that finished normally stays as it was."""
    cycle_id = _insert_cycle(session_factory, "done")
    state = ExecutionState(clock=clock)

    with session_factory() as session:
        recovered = recover_interrupted_cycles(session, state, clock)
        session.commit()

    assert recovered == 0
    assert state.failed_cycles == 0
    with session_factory() as session:
        row = session.execute(select(Cycle).where(Cycle.id == cycle_id)).scalar_one()
        assert row.status == "done"


def test_load_state_restores_consecutive_error_count(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """Errored cycles at the tail keep counting across a restart."""
    _insert_cycle(session_factory, "done")
    _insert_cycle(session_factory, "error")
    _insert_cycle(session_factory, "error")

    state = load_execution_state(clock, get_config(), session_factory)

    assert state.failed_cycles == 2


def test_load_state_streak_stops_at_a_success(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """A non-error row ends the consecutive failure streak."""
    _insert_cycle(session_factory, "error")
    _insert_cycle(session_factory, "done")
    _insert_cycle(session_factory, "error")

    state = load_execution_state(clock, get_config(), session_factory)

    assert state.failed_cycles == 1


def test_recovery_keeps_errors_recorded_before_the_crash(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """Recovery appends to a crashed cycle's errors instead of replacing."""
    cycle_id = _insert_cycle(
        session_factory, "running", errors=["data source down"]
    )
    state = ExecutionState(clock=clock)

    with session_factory() as session:
        recover_interrupted_cycles(session, state, clock)
        session.commit()

    with session_factory() as session:
        row = session.execute(select(Cycle).where(Cycle.id == cycle_id)).scalar_one()
        assert row.errors == ["data source down", "interrupted by app crash"]


@pytest.mark.anyio
async def test_shutdown_marks_running_cycle_interrupted(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """Grace left at zero: the running row becomes 'interrupted', not a
    failure — the counters stay untouched."""
    cycle_id = _insert_cycle(
        session_factory, "running", errors=["data source down"]
    )

    interrupted = await finalize_interrupted_cycles(
        session_factory, clock, grace_seconds=0
    )

    assert interrupted == 1
    with session_factory() as session:
        row = session.execute(select(Cycle).where(Cycle.id == cycle_id)).scalar_one()
        assert row.status == "interrupted"
        assert row.errors == ["data source down", "interrupted by shutdown"]
        assert row.finished_at is not None
    state = load_execution_state(clock, get_config(), session_factory)
    assert state.failed_cycles == 0
    assert state.failed_checks == 0


@pytest.mark.anyio
async def test_shutdown_waits_for_a_running_cycle_to_finish(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """A cycle that finishes inside the grace window keeps its own status."""
    cycle_id = _insert_cycle(session_factory, "running")

    async def finish_cycle() -> None:
        await asyncio.sleep(0.1)
        with session_factory() as session:
            row = session.execute(
                select(Cycle).where(Cycle.id == cycle_id)
            ).scalar_one()
            row.status = "done"
            row.finished_at = datetime.now(UTC)
            session.commit()

    task = asyncio.create_task(finish_cycle())
    interrupted = await finalize_interrupted_cycles(
        session_factory, clock, grace_seconds=10, poll_seconds=0.05
    )
    await task

    assert interrupted == 0
    with session_factory() as session:
        row = session.execute(select(Cycle).where(Cycle.id == cycle_id)).scalar_one()
        assert row.status == "done"


def test_interrupted_rows_do_not_count_and_do_not_break_the_streak(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """An interrupted cycle is like one that never ran: the failure streak
    behind it keeps counting, but the row itself adds nothing."""
    _insert_cycle(session_factory, "error")
    _insert_cycle(session_factory, "interrupted")
    _insert_cycle(session_factory, "error")

    state = load_execution_state(clock, get_config(), session_factory)

    assert state.failed_cycles == 2


def test_interrupted_rows_are_not_recovered_as_errors(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """Startup recovery only touches rows still 'running' (a real crash)."""
    cycle_id = _insert_cycle(session_factory, "interrupted")
    state = ExecutionState(clock=clock)

    with session_factory() as session:
        recovered = recover_interrupted_cycles(session, state, clock)
        session.commit()

    assert recovered == 0
    assert state.failed_cycles == 0
    with session_factory() as session:
        row = session.execute(select(Cycle).where(Cycle.id == cycle_id)).scalar_one()
        assert row.status == "interrupted"


def test_load_state_restores_consecutive_check_errors(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """Errored position checks keep their own streak across a restart."""
    _insert_cycle(session_factory, "done", kind="position_check")
    _insert_cycle(session_factory, "error", kind="position_check")
    _insert_cycle(session_factory, "error", kind="position_check")
    _insert_cycle(session_factory, "error")

    state = load_execution_state(clock, get_config(), session_factory)

    assert state.failed_checks == 2
    assert state.failed_cycles == 1


def test_restart_always_pauses_and_keeps_kill_switch(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """Paused at startup: a kill switch that was on stays on after a
    restart, and startup always comes up paused — even when trading was
    running (never paused) before the restart."""
    from app.engine.state import record_event

    with session_factory() as session:
        record_event(
            session,
            clock,
            level="warning",
            event_type="kill_switch",
            details={"on": True, "reason": "testing", "automatic": False},
        )
        session.commit()

    state = load_execution_state(clock, get_config(), session_factory)

    assert state.kill_switch_on
    assert state.paused
    assert state.paused_at_startup
    assert state.paused_since is not None


def test_restart_after_resume_is_paused_again(
    session_factory: sessionmaker[Session], clock: Clock
) -> None:
    """A pause resumed before shutdown still comes back paused."""
    from app.engine.state import record_event

    with session_factory() as session:
        record_event(
            session,
            clock,
            level="info",
            event_type="pause",
            details={"on": True, "reason": "startup"},
        )
        record_event(
            session,
            clock,
            level="info",
            event_type="pause",
            details={"on": False, "reason": "manual"},
        )
        session.commit()

    state = load_execution_state(clock, get_config(), session_factory)

    assert not state.kill_switch_on
    assert state.paused
    assert state.paused_at_startup
