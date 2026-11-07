"""State errors resolve by state, never by quiet time or dismissal
(docs/SPEC.md, Readiness — Errors; item 050 step 4)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Event, Position
from app.engine.state import ExecutionState, record_event
from app.market.clock import UTC, FakeClock
from app.readiness.service import (
    ReadinessError,
    apply_auto_resolution,
    dismiss_error,
)
from tests.helpers import NOW, events_of, open_position
from tests.test_stop_check import _executor, _holding, _position_row
from app.broker.fake import FakeBroker

pytestmark = pytest.mark.anyio


def _error(
    session: Session,
    clock: FakeClock,
    event_type: str,
    details: dict[str, object],
    *,
    level: str = "critical",
) -> Event:
    event = record_event(
        session, clock, level=level, event_type=event_type,
        details=details, affects="order",
    )
    session.commit()
    return event


def test_quiet_time_never_resolves_state_errors(
    session_factory: sessionmaker[Session],
) -> None:
    """Every listed state error stays open after two quiet hours."""
    clock = FakeClock(NOW)
    kinds = [
        ("stop_missing", {"symbol": "MSFT", "reason": "rejected: x"}),
        ("protective_stop_failed", {"symbol": "MSFT", "error": "x"}),
        ("pending_exit", {"symbol": "MSFT"}),
        ("reconciliation", {"symbol": "MSFT", "kind": "qty_mismatch"}),
        ("stream_degraded", {"reason": "x"}),
        ("live_decisions_failing", {"provider": "openai"}),
        ("api_key", {"name": "alpaca"}),
    ]
    later = NOW + timedelta(hours=2)
    with session_factory() as session:
        ids = [
            _error(session, clock, kind, details).id
            for kind, details in kinds
        ]
        apply_auto_resolution(session, now=later, minutes=60)
        session.commit()
        for event_id in ids:
            assert session.get(Event, event_id).resolved_at is None  # type: ignore[union-attr]


async def test_dismiss_open_stop_missing_is_refused(
    session_factory: sessionmaker[Session],
) -> None:
    """An open episode can never be dismissed — only the stop check
    closes it."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    _holding(broker)
    executor = _executor(session_factory, broker, clock=clock)
    with session_factory() as session:
        _position_row(session)
    broker.fail_writes = True
    await executor.reconcile()

    with session_factory() as session:
        episode = events_of(session, "stop_missing")[0]
        with pytest.raises(ReadinessError, match="still true: MSFT has no stop"):
            dismiss_error(
                session, clock=clock, event_id=episode.id, note="seen it"
            )

    broker.fail_writes = False
    await executor.reconcile()
    with session_factory() as session:
        episode = events_of(session, "stop_missing")[0]
        assert episode.resolution == "condition"


async def test_dismiss_protective_stop_failed_state(
    session_factory: sessionmaker[Session],
) -> None:
    """protective_stop_failed dismiss is refused while its symbol still
    has an open episode, allowed once protected."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    _holding(broker)
    executor = _executor(session_factory, broker, clock=clock)
    with session_factory() as session:
        _position_row(session)
    broker.fail_writes = True
    await executor.reconcile()

    with session_factory() as session:
        failure = events_of(session, "protective_stop_failed")[0]
        with pytest.raises(ReadinessError, match="still true"):
            dismiss_error(
                session, clock=clock, event_id=failure.id, note="seen"
            )

    broker.fail_writes = False
    await executor.reconcile()
    # The stop check resolved it — nothing left to dismiss.
    with session_factory() as session:
        failure = events_of(session, "protective_stop_failed")[0]
        assert failure.resolution == "condition"
        with pytest.raises(ReadinessError, match="not an unresolved"):
            dismiss_error(
                session, clock=clock, event_id=failure.id, note="seen"
            )


def test_pending_exit_dismiss_and_resolution(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(NOW)
    with session_factory() as session:
        open_position(session, "MSFT", qty=4, entry="415", stop="410")
        event = _error(session, clock, "pending_exit", {"symbol": "MSFT"})
        position = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        position.pending_exit = {"reason": "x", "decision_id": 1}
        session.commit()
        with pytest.raises(
            ReadinessError, match="still true: MSFT exit still held back"
        ):
            dismiss_error(
                session, clock=clock, event_id=event.id, note="seen"
            )
        position.pending_exit = None
        session.commit()
        dismissed = dismiss_error(
            session, clock=clock, event_id=event.id, note="cleared"
        )
        assert dismissed.resolution == "dismissed"


def test_reconciliation_dismiss_until_acknowledged(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(NOW)
    with session_factory() as session:
        event = _error(
            session, clock, "reconciliation",
            {"symbol": "MSFT", "kind": "qty_mismatch"},
        )
        with pytest.raises(
            ReadinessError, match="still true: the mismatch is not acknowledged"
        ):
            dismiss_error(
                session, clock=clock, event_id=event.id, note="seen"
            )


def test_stream_degraded_dismiss_by_flag(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(NOW)
    state = ExecutionState(clock=clock)
    state.stream_degraded_until = NOW + timedelta(minutes=30)
    with session_factory() as session:
        event = _error(session, clock, "stream_degraded", {"reason": "x"})
        with pytest.raises(ReadinessError, match="still true"):
            dismiss_error(
                session, clock=clock, event_id=event.id, note="seen",
                exec_state=state,
            )
        state.stream_degraded_until = NOW - timedelta(minutes=1)
        dismissed = dismiss_error(
            session, clock=clock, event_id=event.id, note="recovered",
            exec_state=state,
        )
        assert dismissed.resolution == "dismissed"


def test_live_decisions_and_api_key_dismiss(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(NOW)
    state = ExecutionState(clock=clock)
    state.live_calls_failing_since = NOW
    with session_factory() as session:
        event = _error(session, clock, "live_decisions_failing", {"p": "x"})
        with pytest.raises(ReadinessError, match="still true"):
            dismiss_error(
                session, clock=clock, event_id=event.id, note="seen",
                exec_state=state,
            )
        state.live_calls_failing_since = None
        assert (
            dismiss_error(
                session, clock=clock, event_id=event.id, note="ok",
                exec_state=state,
            ).resolution == "dismissed"
        )

        class _Secrets:
            def group_status(self, name: str) -> dict[str, object]:
                return {"status": "failing" if name == "openai" else "ok"}

        api_event = _error(
            session, clock, "api_key", {"name": "openai"}
        )
        with pytest.raises(
            ReadinessError, match="still true: the openai key still fails"
        ):
            dismiss_error(
                session, clock=clock, event_id=api_event.id, note="seen",
                secrets=_Secrets(),
            )
