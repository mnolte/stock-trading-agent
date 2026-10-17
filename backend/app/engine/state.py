"""Runtime execution state (docs/SPEC.md, Risk management).

Holds what the gates, banner and header need between cycles: the kill
switch, the session brakes, consecutive failure counts and the last known
account snapshot. Durable facts (kill-switch changes, acknowledgments,
mismatches) are written to the ``events`` table so a restart rebuilds the
state from the log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.models import BrokerAccount
from app.db.base import get_session_maker
from app.db.models import Cycle, EquitySnapshot, Event, Position, Run
from app.market.clock import NY_TZ

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.market.clock import Clock

# Event types whose unacknowledged rows block new entries.
BLOCKING_EVENT_TYPES = ("reconciliation",)


@dataclass
class ExecutionState:
    """Kill switch, session brakes and health counters."""

    clock: Clock
    kill_switch_on: bool = False
    kill_switch_since: datetime | None = None
    kill_switch_reason: str | None = None
    kill_switch_auto: bool = False
    # Consecutive failed decision cycles; 3 in a row turn the switch on.
    failed_cycles: int = 0
    # Consecutive failed position checks; alert at the configured threshold.
    failed_checks: int = 0
    # Session-scoped brakes; reset when the NY session date changes.
    session_date: date | None = None
    shock_brake_1h: bool = False
    shock_brake_session: bool = False
    daily_loss_hit: bool = False
    # Latest known account snapshot (updated by reconciliation/checks).
    account: BrokerAccount | None = None
    # Highest equity ever seen; drawdown kill is measured from it.
    peak_equity: Decimal = Decimal(0)
    # Start of the current run (paper resets restart peak + losing streak).
    run_started_at: datetime | None = None
    # Last broker reconciliation, for the Positions sync strip.
    last_reconcile_at: datetime | None = None
    last_reconcile_ok: bool = True

    def _today_ny(self) -> date:
        return self.clock.now().astimezone(NY_TZ).date()

    def reset_session_if_new_day(self) -> None:
        today = self._today_ny()
        if self.session_date != today:
            self.session_date = today
            self.shock_brake_1h = False
            self.shock_brake_session = False
            self.daily_loss_hit = False

    def pending_ack(self, session: Session) -> bool:
        """True while an adoption or blocking mismatch awaits acknowledgment."""
        position_pending = session.execute(
            select(func.count(Position.id)).where(
                Position.closed_at.is_(None), Position.pending_ack.is_(True)
            )
        ).scalar_one()
        if position_pending:
            return True
        blocking = session.execute(
            select(func.count(Event.id)).where(
                Event.event_type.in_(BLOCKING_EVENT_TYPES),
                Event.acknowledged_at.is_(None),
            )
        ).scalar_one()
        return bool(blocking)


def record_event(
    session: Session,
    clock: Clock,
    *,
    level: str,
    event_type: str,
    details: dict[str, object] | None = None,
) -> Event:
    """Append one row to the events log."""
    from app.data.cache import _naive_utc

    event = Event(
        time=_naive_utc(clock.now()),
        level=level,
        event_type=event_type,
        details=details,
    )
    session.add(event)
    session.flush()
    return event


def recover_interrupted_cycles(
    session: Session, state: ExecutionState, clock: Clock
) -> int:
    """Startup recovery after a crash mid-cycle (docs/SPEC.md, Build plan,
    PR 8). A cycle left ``running`` never finished, so it counts as a
    failure: decision cycles toward the failed-cycles kill switch, position
    checks toward the failed-checks alert. Returns how many were recovered.
    """
    from app.data.cache import _naive_utc

    running = session.execute(
        select(Cycle).where(Cycle.status == "running")
    ).scalars().all()
    for cycle in running:
        cycle.status = "error"
        cycle.errors = [*(cycle.errors or []), "interrupted by app shutdown"]
        cycle.finished_at = _naive_utc(clock.now())
        if cycle.kind == "cycle":
            state.failed_cycles += 1
        else:
            state.failed_checks += 1
    return len(running)


def load_execution_state(
    clock: Clock, config: AppConfig, session_factory: sessionmaker[Session] | None = None
) -> ExecutionState:
    """Rebuild the runtime state from the events log after a restart."""
    factory = session_factory or get_session_maker()
    state = ExecutionState(clock=clock)
    with factory() as session:
        # Consecutive failed decision cycles at the tail of the table keep
        # counting across restarts (three in a row kill the switch). A row
        # left "running" is a crashed cycle: it continues the streak here
        # and is counted by recover_interrupted_cycles, not again here.
        tail_statuses = session.execute(
            select(Cycle.status)
            .where(Cycle.kind == "cycle")
            .order_by(Cycle.id.desc())
            .limit(20)
        ).scalars().all()
        for status in tail_statuses:
            if status == "error":
                state.failed_cycles += 1
            elif status != "running":
                break
        # Same tail scan for position checks, whose consecutive failures
        # feed the failed-checks alert (schedule.position_check_failures_alert).
        tail_checks = session.execute(
            select(Cycle.status)
            .where(Cycle.kind == "position_check")
            .order_by(Cycle.id.desc())
            .limit(20)
        ).scalars().all()
        for status in tail_checks:
            if status == "error":
                state.failed_checks += 1
            elif status != "running":
                break
        latest_run = session.execute(
            select(Run).order_by(Run.id.desc()).limit(1)
        ).scalar_one_or_none()
        run_start = latest_run.started_at if latest_run is not None else None
        state.run_started_at = run_start
        peak_query = select(func.max(EquitySnapshot.equity))
        if run_start is not None:
            peak_query = peak_query.where(EquitySnapshot.time >= run_start)
        peak: Decimal | None = session.execute(peak_query).scalar()
        if peak is not None:
            state.peak_equity = Decimal(str(peak))
        elif latest_run is not None:
            state.peak_equity = Decimal(str(latest_run.starting_equity))
        last_kill = session.execute(
            select(Event)
            .where(Event.event_type.in_(("kill_switch", "kill_switch_resume")))
            .order_by(Event.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if (
            last_kill is not None
            and last_kill.event_type == "kill_switch"
            and (last_kill.details or {}).get("on")
        ):
            state.kill_switch_on = True
            state.kill_switch_since = last_kill.time
            details = last_kill.details or {}
            state.kill_switch_reason = str(details.get("reason", ""))
            state.kill_switch_auto = bool(details.get("automatic"))
    return state
