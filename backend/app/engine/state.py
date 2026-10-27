"""Runtime execution state (docs/SPEC.md, Risk management).

Holds what the gates, banner and header need between cycles: the kill
switch, the session brakes, consecutive failure counts and the last known
account snapshot. Durable facts (kill-switch changes, acknowledgments,
mismatches) are written to the ``events`` table so a restart rebuilds the
state from the log.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.broker.models import BrokerAccount
from app.db.base import get_session_maker
from app.db.models import Cycle, EquitySnapshot, Event, Position, Run
from app.market.clock import NY_TZ

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.market.clock import Clock

logger = logging.getLogger(__name__)

# Event types whose unacknowledged rows block new entries.
BLOCKING_EVENT_TYPES = ("reconciliation",)

# The notification service registers here at startup; every recorded
# event is offered to it after the row is written
# (docs/SPEC.md, Notifications).
EventSink = Callable[[str, str, "dict[str, object] | None"], None]

_event_sink: EventSink | None = None


def set_event_sink(sink: EventSink | None) -> None:
    global _event_sink
    _event_sink = sink


@dataclass
class ExecutionState:
    """Kill switch, session brakes and health counters."""

    clock: Clock
    kill_switch_on: bool = False
    kill_switch_since: datetime | None = None
    kill_switch_reason: str | None = None
    kill_switch_auto: bool = False
    # New entries paused (docs/SPEC.md, Paused at startup). Separate from
    # the kill switch: positions keep being reviewed, stops stay active.
    paused: bool = False
    paused_since: datetime | None = None
    # The startup broker-guard check confirmed no_shorting + 1x margin
    # for this run. When unconfirmed, an exit may only sell a quantity
    # Alpaca confirms fresh; the guard is re-verified each reconcile.
    broker_guard_confirmed: bool = True
    # True while this pause is the startup one — drives the blue banner.
    paused_at_startup: bool = False
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


# Detail keys that link an event to a stored object, in precedence order:
# the most downstream object the event touched wins (docs/SPEC.md, events
# affects — only errors linked to a decision, order or fill count for
# Readiness).
AFFECTS_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fill", ("fill_id",)),
    ("order", ("order", "order_id", "alpaca_order_id", "client_order_id")),
    ("decision", ("decision_id",)),
)


def infer_affects(details: dict[str, object] | None) -> str:
    """``affects`` for an event row from its stored details links."""
    if not details:
        return "none"
    for affects, keys in AFFECTS_KEYS:
        if any(details.get(key) not in (None, "") for key in keys):
            return affects
    return "none"


def record_event(
    session: Session,
    clock: Clock,
    *,
    level: str,
    event_type: str,
    details: dict[str, object] | None = None,
    affects: str | None = None,
) -> Event:
    """Append one row to the events log."""
    from app.data.cache import _naive_utc

    event = Event(
        time=_naive_utc(clock.now()),
        level=level,
        event_type=event_type,
        details=details,
        affects=affects if affects is not None else infer_affects(details),
    )
    session.add(event)
    session.flush()
    if _event_sink is not None:
        try:
            _event_sink(event_type, level, details)
        except Exception:  # noqa: BLE001 - a notification must never
            logger.exception(  # break the path that recorded the event
                "notification sink failed for %s", event_type
            )
    return event


def recover_interrupted_cycles(session: Session, state: ExecutionState, clock: Clock) -> int:
    """Startup recovery after a crash mid-cycle (docs/SPEC.md, Errors). A
    row still ``running`` at startup means the process died — a real crash,
    so it counts as a failure: decision cycles toward the failed-cycles
    kill switch, position checks toward the failed-checks alert. A normal
    shutdown marks its leftovers ``interrupted`` instead, so they never
    reach this function. Returns how many were recovered.
    """
    from app.data.cache import _naive_utc

    running = session.execute(select(Cycle).where(Cycle.status == "running")).scalars().all()
    for cycle in running:
        cycle.status = "error"
        cycle.errors = [*(cycle.errors or []), "interrupted by app crash"]
        cycle.finished_at = _naive_utc(clock.now())
        if cycle.kind == "cycle":
            state.failed_cycles += 1
        else:
            state.failed_checks += 1
    return len(running)


async def finalize_interrupted_cycles(
    session_factory: sessionmaker[Session],
    clock: Clock,
    grace_seconds: float,
    poll_seconds: float = 0.5,
) -> int:
    """Normal shutdown (docs/SPEC.md, Errors): wait up to ``grace_seconds``
    for a running cycle or position check to finish. Whatever is still
    ``running`` afterwards is marked ``interrupted`` — not a failure, so
    the failure counters stay untouched. Returns how many were marked.
    """
    import asyncio
    import time

    from app.data.cache import _naive_utc

    deadline = time.monotonic() + grace_seconds
    while True:
        with session_factory() as session:
            running = (
                session.execute(select(Cycle).where(Cycle.status == "running")).scalars().all()
            )
            if not running:
                return 0
            if time.monotonic() >= deadline:
                marked = 0
                for cycle in running:
                    # A cycle that finished while we waited keeps its own
                    # status: only rows still running are touched.
                    result = session.connection().execute(
                        update(Cycle)
                        .where(Cycle.id == cycle.id, Cycle.status == "running")
                        .values(
                            status="interrupted",
                            errors=[
                                *(cycle.errors or []),
                                "interrupted by shutdown",
                            ],
                            finished_at=_naive_utc(clock.now()),
                        )
                    )
                    marked += result.rowcount
                session.commit()
                return marked
        await asyncio.sleep(poll_seconds)


def load_execution_state(
    clock: Clock, config: AppConfig, session_factory: sessionmaker[Session] | None = None
) -> ExecutionState:
    """Rebuild the runtime state from the events log after a restart."""
    factory = session_factory or get_session_maker()
    state = ExecutionState(clock=clock)
    state.reset_session_if_new_day()
    with factory() as session:
        last_loss = session.execute(
            select(Event)
            .where(Event.event_type == "daily_loss_limit")
            .order_by(Event.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        state.daily_loss_hit = last_loss is not None and (last_loss.details or {}).get(
            "session_date"
        ) == str(state.session_date)
        # Consecutive failed decision cycles at the tail of the table keep
        # counting across restarts (three in a row kill the switch). Only a
        # completed cycle resets the count (docs/SPEC.md, Errors): "skipped"
        # (market closed/holiday), "interrupted" (clean shutdown) and
        # "running" (crash leftover — counted by recover_interrupted_cycles,
        # not again here) all leave the streak unchanged.
        tail_statuses = (
            session.execute(
                select(Cycle.status)
                .where(Cycle.kind == "cycle")
                .order_by(Cycle.id.desc())
                .limit(20)
            )
            .scalars()
            .all()
        )
        for status in tail_statuses:
            if status == "error":
                state.failed_cycles += 1
            elif status == "done":
                break
        # Same tail scan for position checks, whose consecutive failures
        # feed the failed-checks alert (schedule.position_check_failures_alert).
        tail_checks = (
            session.execute(
                select(Cycle.status)
                .where(Cycle.kind == "position_check")
                .order_by(Cycle.id.desc())
                .limit(20)
            )
            .scalars()
            .all()
        )
        for status in tail_checks:
            if status == "error":
                state.failed_checks += 1
            elif status == "done":
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
        if latest_run is not None:
            # The run's starting equity is a point on its own curve: a run
            # that dipped before its first snapshot would otherwise peak
            # below where it started and kill the drawdown brake early.
            state.peak_equity = max(
                state.peak_equity, Decimal(str(latest_run.starting_equity))
            )
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
    # Paused at startup (docs/SPEC.md, Risk management): every start comes
    # up with new entries paused, even when trading was running before.
    # Unlike the kill switch, pause is not rebuilt from the events log —
    # it is always on at startup, so "pause" events are audit trail only.
    state.paused = True
    state.paused_at_startup = True
    state.paused_since = clock.now()
    return state
