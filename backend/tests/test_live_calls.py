"""Live decisions failing (docs/SPEC.md, Decision layer): every live
call failed, or any call got a 401/403, opens an episode — critical
event, banner state, notification once — cleared by the first good call."""

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from app.config import AppConfig
from app.db.models import Event
from app.engine.live_calls import LiveModelHealth
from app.engine.state import ExecutionState, set_event_sink
from app.llm.base import ModelResponse
from app.market.clock import FakeClock
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)


def _resp(
    error: str | None = None,
    http_status: int | None = None,
    provider: str = "openai",
    refused: bool = False,
) -> ModelResponse:
    return ModelResponse(
        raw="{}" if error is None else None,
        model="gpt-4.1-mini",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
        error=error,
        error_type="api_error" if error else None,
        http_status=http_status,
        provider=provider,
        refused=refused,
    )


class _FakeSecrets:
    def __init__(self) -> None:
        self.tests: list[tuple[str, bool, str | None]] = []
        self.status: dict[str, str] = {}

    def record_test(
        self, name: str, ok: bool, detail: str | None = None
    ) -> None:
        self.tests.append((name, ok, detail))
        self.status[name] = "ok" if ok else "failing"

    def group_status(self, group: str) -> dict[str, Any]:
        return {"status": self.status.get(group, "ok")}


def _health(
    session_factory: sessionmaker[Session],
) -> tuple[LiveModelHealth, ExecutionState, _FakeSecrets, list[dict[str, Any]]]:
    clock = FakeClock(NOW)
    state = ExecutionState(clock=clock)
    secrets = _FakeSecrets()
    health = LiveModelHealth(
        state=state,
        clock=clock,
        config=AppConfig(),
        secrets=secrets,
    )
    events: list[dict[str, Any]] = []

    def sink(et: str, level: str, details: dict[str, Any] | None) -> None:
        events.append({"type": et, "level": level, "details": details})

    set_event_sink(sink)
    return health, state, secrets, events


@pytest.fixture(autouse=True)
def _sink_reset() -> Generator[None, None, None]:
    yield
    set_event_sink(None)


def test_every_call_401_episode_then_clear(
    session_factory: sessionmaker[Session],
) -> None:
    health, state, secrets, events = _health(session_factory)
    with session_factory() as session:
        health.settle_cycle(
            session,
            [_resp(error="HTTP 401", http_status=401)],
        )
        session.commit()
        assert state.live_calls_failing_since == NOW
        assert state.live_calls_failing_provider == "openai"
        assert "401" in (state.live_calls_failing_error or "")
        assert secrets.status["openai"] == "failing"
        row = session.execute(
            select(Event).where(
                Event.event_type == "live_decisions_failing"
            )
        ).scalar_one()
        assert row.level == "critical"
        assert len(events) == 1  # one notification per episode

        # A second failing cycle keeps the episode — no second event.
        health.settle_cycle(session, [_resp(error="HTTP 500", http_status=500)])
        session.commit()
        assert len(events) == 1

        # First successful live call clears the episode and un-marks
        # the key this check had marked.
        health.settle_cycle(session, [_resp()])
        session.commit()
        since = cast("datetime | None", state.live_calls_failing_since)
        assert since is None
        assert secrets.status["openai"] == "ok"


def test_one_401_among_good_calls_triggers(
    session_factory: sessionmaker[Session],
) -> None:
    health, state, secrets, events = _health(session_factory)
    with session_factory() as session:
        health.settle_cycle(
            session,
            [_resp(), _resp(error="HTTP 403", http_status=403), _resp()],
        )
        session.commit()
        assert state.live_calls_failing_since is not None
        assert secrets.status["openai"] == "failing"


def test_one_timeout_among_good_calls_does_not(
    session_factory: sessionmaker[Session],
) -> None:
    health, state, secrets, events = _health(session_factory)
    with session_factory() as session:
        health.settle_cycle(
            session,
            [_resp(), _resp(error="timed out", http_status=None), _resp()],
        )
        session.commit()
        assert state.live_calls_failing_since is None
        assert secrets.tests == []


def test_all_failed_without_auth_opens_episode(
    session_factory: sessionmaker[Session],
) -> None:
    health, state, secrets, events = _health(session_factory)
    with session_factory() as session:
        health.settle_cycle(
            session,
            [
                _resp(error="timeout"),
                _resp(error="HTTP 500", http_status=500),
            ],
        )
        session.commit()
        assert state.live_calls_failing_since is not None
        # No auth error → the key stays un-marked.
        assert secrets.tests == []


def test_no_calls_keeps_episode_state(
    session_factory: sessionmaker[Session],
) -> None:
    health, state, secrets, events = _health(session_factory)
    with session_factory() as session:
        health.settle_cycle(session, [_resp(error="x")])
        session.commit()
        assert state.live_calls_failing_since is not None
        # A cycle that made no call at all neither fires nor clears.
        health.settle_cycle(session, [])
        assert state.live_calls_failing_since is not None
