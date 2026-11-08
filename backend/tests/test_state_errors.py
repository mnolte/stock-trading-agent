"""State errors resolve by state, never by quiet time or dismissal
(docs/SPEC.md, Readiness — Errors; item 050 step 4)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from app.broker.fake import FakeBroker
from app.config import AppConfig
from app.db.models import Event, Position
from app.engine.state import ExecutionState, record_event
from app.market.calendar import CalendarProvider
from app.market.clock import UTC, FakeClock
from app.readiness.service import (
    ReadinessError,
    apply_auto_resolution,
    dismiss_error,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import NOW, events_of, open_position
from tests.test_stop_check import _executor, _holding, _position_row

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
    kinds: list[tuple[str, dict[str, object]]] = [
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
            assert session.execute(
                select(Event).where(Event.id == event_id)
            ).scalar_one().resolved_at is None


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


def test_unclean_summary_counts_missed_work(
    session_factory: sessionmaker[Session],
) -> None:
    """Missed position checks and cycles count from the trading
    calendar between the last heartbeat and now; outside a session the
    answer is 'missed nothing' (design/screens/States.dc.html)."""
    from app.data.fake import FakeCalendarSource
    from app.restart import unclean_summary

    config = AppConfig()
    config.yaml["schedule"]["cycles"] = ["10:46", "11:46"]
    config.yaml["schedule"]["position_checks"] = [
        "10:00", "10:15", "10:30", "10:45",
    ]
    calendar = FakeCalendarSource()
    # 15 Oct 2026 is a trading day in the fake calendar; NOW is 13:00 NY.
    died = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)   # 10:00 NY
    back = datetime(2026, 10, 15, 15, 31, tzinfo=UTC)  # 11:31 NY
    summary = unclean_summary(
        died, back, config=config, calendar=cast(CalendarProvider, calendar)
    )
    assert "down 91 min" in summary["summary"]
    assert "3 position checks" in summary["summary"]
    # The missed 10:46 NY cycle renders in Amsterdam time, like the
    # design's "the 19:46 cycle".
    assert "the 16:46 cycle" in summary["summary"]
    assert "17:46" not in summary["summary"]  # not due yet

    overnight = unclean_summary(
        datetime(2026, 10, 15, 20, 0, tzinfo=UTC),   # after close
        datetime(2026, 10, 16, 12, 0, tzinfo=UTC),   # before open
        config=config,
        calendar=cast(CalendarProvider, calendar),
    )
    assert overnight["missed"] == "nothing"


def test_backup_not_set_up_sends_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Without backup credentials the offsite copy and the monthly
    restore test show 'not set up' — no failure notification
    (docs/SPEC.md, Backups; item 050 step 8)."""
    from app.ops.service import OpsService

    sent: list[tuple[str, str]] = []

    class _Notifier:
        def notify(self, event: str, line: str, **kwargs: Any) -> None:
            sent.append((event, line))

    class _Secrets:
        def get(self, name: str) -> None:
            return None

    config = AppConfig()
    config.env.sa_data_dir = str(tmp_path)
    config.env.sa_db_path = str(tmp_path / "db.sqlite")
    ops = OpsService(
        config, FakeClock(NOW), notifier=cast(Any, _Notifier()), secrets=cast(Any, _Secrets())
    )

    import asyncio
    assert asyncio.run(ops.run_restore_test()) is False
    assert ops.status["restore_test"]["not_set_up"] is True
    backups = ops.check_backups()
    assert backups["offsite"] == {"not_set_up": True}
    assert backups["restore_test"] == {"not_set_up": True}
    # Missing credentials send nothing; the unrelated stale local copy
    # may still alert.
    assert not [
        n for n in sent if "offsite" in n[1] or "restore" in n[1]
    ]
    # 'not set up' carries no timestamp — the staleness parse must not
    # log "unparseable restore_test.at" every hour (item 054).
    assert not [
        r
        for r in caplog.records
        if "unparseable restore_test.at" in r.getMessage()
    ]
