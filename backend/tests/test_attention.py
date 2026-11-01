"""PR 35: the Needs you endpoint (docs/SPEC.md, GUI — Needs you).

One test per item kind: it appears from its source and leaves on the
spec's condition — acknowledged, episode cleared, approved or expired,
send succeeded, reviewed, confirmed, applied.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import app.readiness.service as readiness
import pytest
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import Approval, Cycle, Decision, Event, Verdict
from app.log_index import LogIndexHandler
from app.notifications.service import NotificationService
from fastapi.testclient import TestClient
from sqlalchemy import select


def _attention(client: TestClient) -> dict:
    r = client.get("/api/attention")
    assert r.status_code == 200
    return r.json()


def _kinds(payload: dict) -> list[str]:
    return [i["kind"] for i in payload["items"]]


def _cycle_decision(session) -> Decision:
    cycle = Cycle(kind="cycle", scheduled_time=datetime(2026, 10, 15, 18, 46), status="done")
    session.add(cycle)
    session.flush()
    decision = Decision(
        cycle_id=cycle.id,
        symbol="MSFT",
        context="candidate",
        final_action="enter",
        prompt_version="decision.v1",
        prompt_hash="x" * 64,
        model="gpt-4o-mini",
    )
    session.add(decision)
    session.flush()
    return decision


def test_empty_state(client: TestClient) -> None:
    payload = _attention(client)
    assert payload["items"] == []
    assert payload["count"] == 0
    health = payload["health"]
    assert set(health) == {"alpaca", "stream", "clock", "keys", "email"}
    assert health["keys"]["status"] == "passing"  # simulated mode
    assert health["email"] in ("on", "off")


def test_banner_ack_leaves_when_acknowledged(client: TestClient) -> None:
    with get_session_maker()() as session:
        event = Event(
            time=datetime(2026, 10, 15, 18, 46),
            level="critical",
            event_type="reconciliation",
            details={
                "kind": "qty_mismatch",
                "symbol": "MSFT",
                "broker_qty": 4,
                "db_qty": 2,
            },
            affects="position",
        )
        session.add(event)
        session.commit()
        event_id = event.id
    payload = _attention(client)
    assert _kinds(payload)[:1] == ["banner_ack"]
    item = payload["items"][0]
    assert "MSFT" in item["text"]
    assert item["severity"] == "critical"
    r = client.post(f"/api/events/{event_id}/acknowledge")
    assert r.status_code == 200
    assert _attention(client)["items"] == []


def test_episode_leaves_when_it_clears(client: TestClient) -> None:
    app = client.app
    state = getattr(app.state, "exec_state", None)
    assert state is not None
    state.live_calls_failing_since = datetime(2026, 10, 15, 18, 46)
    state.live_calls_failing_provider = "openai"
    state.live_calls_failing_error = "timeout"
    state.stream_degraded_until = datetime(2999, 1, 1)
    state.stream_degraded_since = datetime(2026, 10, 15, 18, 46)
    payload = _attention(client)
    texts = [i["text"] for i in payload["items"] if i["kind"] == "episode"]
    assert texts == ["Live decisions are failing.", "Order updates are delayed."]
    assert payload["health"]["stream"] == "degraded"
    state.live_calls_failing_since = None
    state.stream_degraded_until = None
    payload = _attention(client)
    assert "episode" not in _kinds(payload)


def test_approval_leaves_when_resolved(client: TestClient) -> None:
    with get_session_maker()() as session:
        decision = _cycle_decision(session)
        session.add(
            Approval(
                decision_id=decision.id,
                symbol="MSFT",
                qty=4,
                limit_price=Decimal("418.40"),
                stop_price=Decimal("409.60"),
                target_price=Decimal("435.40"),
                status="pending",
                created_at=datetime(2026, 10, 15, 18, 46),
            )
        )
        session.commit()
    payload = _attention(client)
    assert "approval" in _kinds(payload)
    item = next(i for i in payload["items"] if i["kind"] == "approval")
    assert "MSFT" in item["text"] and "expires at" in item["detail"]
    with get_session_maker()() as session:
        session.execute(select(Approval)).scalar_one().status = "rejected"
        session.commit()
    assert "approval" not in _kinds(_attention(client))


def test_email_failing_leaves_when_a_send_succeeds(client: TestClient) -> None:
    notifier = getattr(client.app.state, "notification_service", None)
    if notifier is None:  # the service may not start in this mode
        notifier = SimpleNamespace(email_failures=0, smtp_status=lambda: {})
        client.app.state.notification_service = notifier

    def set_failures(n: int) -> None:
        if isinstance(notifier, NotificationService):
            notifier._email_failures = n
        else:
            notifier.email_failures = n

    try:
        set_failures(2)
        payload = _attention(client)
        assert "channel" in _kinds(payload)
        assert payload["health"]["email"] == "failing"
        set_failures(0)
        assert "channel" not in _kinds(_attention(client))
    finally:
        set_failures(0)


def test_errors_leave_when_reviewed(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = LogIndexHandler(tmp_path / "logs.db")
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "boom error", (), None)
    record.context = {}
    record.event = "test.err"
    handler.emit(record)
    handler.flush()
    conn = sqlite3.connect(handler.db_path)
    fp = conn.execute("SELECT fingerprint FROM log_lines LIMIT 1").fetchone()[0]
    conn.close()
    monkeypatch.setattr(readiness, "index_path", lambda _cfg: handler.db_path)
    # build_attention uses the calendar store; WeekdayCalendar is the
    # fallback and app.state.calendar_store exists on a real boot.
    if getattr(client.app.state, "calendar_store", None) is None:
        from app.market.calendar import WeekdayCalendarProvider

        client.app.state.calendar_store = WeekdayCalendarProvider()
    payload = _attention(client)
    assert "errors" in _kinds(payload)
    item = next(i for i in payload["items"] if i["kind"] == "errors")
    assert item["text"] == "1 error not reviewed."
    r = client.post(
        "/api/logs/review",
        json={"fingerprint": fp, "review": "expected", "note": "known"},
    )
    assert r.status_code == 200
    assert "errors" not in _kinds(_attention(client))


def test_verdicts_leave_when_confirmed(client: TestClient) -> None:
    with get_session_maker()() as session:
        decision = _cycle_decision(session)
        session.add(
            Verdict(
                decision_id=decision.id,
                verdict="bad_call",
                source="draft",
                created_at=datetime(2026, 10, 15, 18, 46),
                updated_at=datetime(2026, 10, 15, 18, 46),
            )
        )
        session.commit()
        verdict_id = session.execute(select(Verdict.id)).scalar_one()
    payload = _attention(client)
    assert "verdicts" in _kinds(payload)
    r = client.post(f"/api/verdicts/{verdict_id}", json={"verdict": "bad_call"})
    assert r.status_code == 200
    assert "verdicts" not in _kinds(_attention(client))


def test_settings_leave_when_applied(client: TestClient) -> None:
    config = get_config()
    pending = config.effective()
    pending.setdefault("risk", {})["max_open_positions"] = 3
    config.set_pending(pending, 99)
    try:
        payload = _attention(client)
        assert "settings" in _kinds(payload)
        assert "v99" in next(
            i["text"] for i in payload["items"] if i["kind"] == "settings"
        )
        config.promote_pending()
        assert "settings" not in _kinds(_attention(client))
    finally:
        config._pending = None
        config.pending_version = None


def test_order_follows_the_spec(client: TestClient) -> None:
    app = client.app
    state = app.state.exec_state
    state.stream_degraded_until = datetime(2999, 1, 1)
    state.stream_degraded_since = datetime(2026, 10, 15, 18, 46)
    notifier = getattr(app.state, "notification_service", None)
    if isinstance(notifier, NotificationService):
        notifier._email_failures = 2
    try:
        with get_session_maker()() as session:
            session.add(
                Event(
                    time=datetime(2026, 10, 15, 18, 46),
                    level="critical",
                    event_type="another_instance",
                    details={"message": "Another instance is trading"},
                    affects="none",
                )
            )
            decision = _cycle_decision(session)
            session.add(
                Approval(
                    decision_id=decision.id,
                    symbol="MSFT",
                    qty=4,
                    limit_price=Decimal("418.40"),
                    stop_price=Decimal("409.60"),
                    target_price=Decimal("435.40"),
                    status="pending",
                    created_at=datetime(2026, 10, 15, 18, 46),
                )
            )
            session.add(
                Verdict(
                    decision_id=decision.id,
                    verdict="bad_call",
                    source="draft",
                    created_at=datetime(2026, 10, 15, 18, 46),
                    updated_at=datetime(2026, 10, 15, 18, 46),
                )
            )
            session.commit()
        kinds = _kinds(_attention(client))
        expected = ["banner_ack", "episode", "approval", "channel", "verdicts"]
        # every kind appears, in the spec's relative order
        positions = [kinds.index(k) for k in expected if k in kinds]
        assert sorted(positions) == positions
        assert set(expected) <= set(kinds)
    finally:
        state.stream_degraded_until = None
        state.stream_degraded_since = None
        if isinstance(notifier, NotificationService):
            notifier._email_failures = 0


def test_websocket_sends_attention(client: TestClient) -> None:
    """Verdict confirm and mismatch acknowledge publish 'attention'."""
    with client.websocket_connect(
        "/ws/ws", headers={"Origin": "http://testserver"}
    ) as ws:
        with get_session_maker()() as session:
            session.add(
                Event(
                    time=datetime(2026, 10, 15, 18, 46),
                    level="critical",
                    event_type="reconciliation",
                    details={"kind": "mismatch"},
                    affects="position",
                )
            )
            decision = _cycle_decision(session)
            session.add(
                Verdict(
                    decision_id=decision.id,
                    verdict="bad_call",
                    source="draft",
                    created_at=datetime(2026, 10, 15, 18, 46),
                    updated_at=datetime(2026, 10, 15, 18, 46),
                )
            )
            session.commit()
            event_id = session.execute(
                select(Event.id).order_by(Event.id.desc()).limit(1)
            ).scalar_one()
            verdict_id = session.execute(
                select(Verdict.id).order_by(Verdict.id.desc()).limit(1)
            ).scalar_one()

        client.post(f"/api/events/{event_id}/acknowledge")
        seen = set()
        for _ in range(20):
            msg = ws.receive_json()
            if msg.get("type") == "update":
                seen.update(msg["topics"])
            if "attention" in seen:
                break
        assert "attention" in seen

        seen = set()
        client.post(f"/api/verdicts/{verdict_id}", json={"verdict": "good_call"})
        for _ in range(20):
            msg = ws.receive_json()
            if msg.get("type") == "update":
                seen.update(msg["topics"])
            if "attention" in seen:
                break
        assert "attention" in seen
