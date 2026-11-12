"""Item 081 step 4 — the one-time stored-row scrub and its Needs you item.

Planted rows carrying key-shaped and stored-secret text are rewritten
through the same scrubber the log pipeline uses; the flag event makes
the run happen exactly once; the Needs you item appears only when rows
changed and leaves when both keys were replaced after the scrub or the
event is dismissed with a note.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from app.db.base import get_session_maker
from app.db.models import (
    AskMessage,
    AskThread,
    Cycle,
    Event,
    Notification,
)
from app.market.clock import FakeClock
from app.secrets_service import SecretsStore
from app.stored_scrub import EVENT_TYPE
from app.stored_scrub import run as run_stored_scrub
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

FAKE_KEY = "sk-proj-FAKEKEY1234567890"
STORED_SECRET = "abc123secret00"

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)

SessionFactory = sessionmaker[Session]


@pytest.fixture
def session_factory(tmp_path: Path) -> SessionFactory:
    from app.db.base import Base
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _plant(session: Session) -> None:
    session.add(
        Cycle(
            kind="cycle",
            scheduled_time=datetime(2026, 10, 15, 18, 46),
            status="done",
            data_source_health={
                "bars": f"failed: {FAKE_KEY}",
                "quotes": f"failed: token=QQ12345x near {STORED_SECRET}",
            },
        )
    )
    session.add(
        Notification(
            time=datetime(2026, 10, 15, 14, 0),
            event="source_failing",
            severity="warning",
            subject=f"[Stock Trading Agent · PAPER] source failing: {FAKE_KEY}",
            body=f"details near {STORED_SECRET}",
            channels=["app"],
            channel_status={"app": "sent"},
        )
    )
    session.add(
        Event(
            time=datetime(2026, 10, 15, 14, 0),
            level="error",
            event_type="source_failing",
            details={"error": FAKE_KEY, "nested": [STORED_SECRET]},
            affects="none",
        )
    )
    thread = AskThread(
        title="t",
        created_at=datetime(2026, 10, 15, 14, 0),
        updated_at=datetime(2026, 10, 15, 14, 0),
    )
    session.add(thread)
    session.flush()
    session.add(
        AskMessage(
            thread_id=thread.id,
            role="assistant",
            text=f"answer mentions {FAKE_KEY}",
            error=f"tool failed near {STORED_SECRET}",
            steps=[{"tool": "get_cycle", "error": f"see {FAKE_KEY}"}],
            created_at=datetime(2026, 10, 15, 14, 0),
        )
    )
    session.commit()


def _provider_values() -> list[str]:
    return [STORED_SECRET]


def test_stored_scrub_rewrites_rows_once(
    session_factory: SessionFactory,
    clock: FakeClock,
) -> None:
    import app.logging_config as lc

    lc.register_secret_provider(_provider_values)
    lc._secrets_cache.clear()
    try:
        with session_factory() as session:
            _plant(session)
        counts = run_stored_scrub(session_factory, clock)
        assert counts == {
            "cycles": 1,
            "notifications": 1,
            "events": 1,
            "ask_messages": 1,
        }
        with session_factory() as session:
            cycle = session.execute(select(Cycle)).scalar_one()
            assert FAKE_KEY not in str(cycle.data_source_health)
            assert STORED_SECRET not in str(cycle.data_source_health)
            assert "QQ12345x" not in str(cycle.data_source_health)
            note = session.execute(select(Notification)).scalar_one()
            assert FAKE_KEY not in note.subject
            assert STORED_SECRET not in (note.body or "")
            events = session.execute(
                select(Event).where(Event.event_type != EVENT_TYPE)
            ).scalars().all()
            assert len(events) == 1
            assert FAKE_KEY not in str(events[0].details)
            assert STORED_SECRET not in str(events[0].details)
            message = session.execute(select(AskMessage)).scalar_one()
            assert FAKE_KEY not in message.text
            assert STORED_SECRET not in (message.error or "")
            assert FAKE_KEY not in str(message.steps)
            flag = session.execute(
                select(Event).where(Event.event_type == EVENT_TYPE)
            ).scalar_one()
            assert flag.level == "error"
            assert flag.affects == "none"
        # Second run is a no-op: the flag row guards it.
        assert run_stored_scrub(session_factory, clock) == {}
        with session_factory() as session:
            flags = session.execute(
                select(Event).where(Event.event_type == EVENT_TYPE)
            ).scalars().all()
            assert len(flags) == 1
    finally:
        lc.unregister_secret_provider(_provider_values)
        lc._secrets_cache.clear()


def test_stored_scrub_clean_db_records_info_flag(
    session_factory: SessionFactory, clock: FakeClock
) -> None:
    counts = run_stored_scrub(session_factory, clock)
    assert counts == {
        "cycles": 0,
        "notifications": 0,
        "events": 0,
        "ask_messages": 0,
    }
    with session_factory() as session:
        flag = session.execute(
            select(Event).where(Event.event_type == EVENT_TYPE)
        ).scalar_one()
        assert flag.level == "info"


def _attention(client: TestClient) -> dict[str, Any]:
    r = client.get("/api/attention")
    assert r.status_code == 200
    return cast("dict[str, Any]", r.json())


def _kinds(payload: dict[str, Any]) -> list[str]:
    return [i["kind"] for i in payload["items"]]


def _plant_flag(client: TestClient) -> int:
    with get_session_maker()() as session:
        event = Event(
            time=datetime(2020, 1, 1),
            level="error",
            event_type=EVENT_TYPE,
            details={"tables": {"cycles": 2, "notifications": 0}},
            affects="none",
        )
        session.add(event)
        session.commit()
        return event.id


def test_needs_you_item_appears_and_dismisses(client: TestClient) -> None:
    event_id = _plant_flag(client)
    payload = _attention(client)
    assert "stored_text_scrub" in _kinds(payload)
    item = next(
        i for i in payload["items"] if i["kind"] == "stored_text_scrub"
    )
    assert item["text"] == (
        "A key appeared in stored error text. "
        "Replace the FRED and Finnhub keys"
    )
    assert item["severity"] == "warning"
    assert item["event_id"] == event_id

    r = client.post(
        f"/api/events/{event_id}/dismiss", json={"note": "keys replaced"}
    )
    assert r.status_code == 200
    assert "stored_text_scrub" not in _kinds(_attention(client))


def test_needs_you_item_leaves_when_both_keys_replaced(
    client: TestClient,
) -> None:
    _plant_flag(client)
    assert "stored_text_scrub" in _kinds(_attention(client))
    store = cast(Any, client.app).state.secrets_store
    assert isinstance(store, SecretsStore)
    store.save("fred", "FAKE-FRED-KEY-0001")
    store.save("finnhub", "FAKE-FINNHUB-KEY-001")
    assert "stored_text_scrub" not in _kinds(_attention(client))
