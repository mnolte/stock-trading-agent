"""Decisions API: inspector list and detail endpoints."""

from collections.abc import Generator
from datetime import datetime

import pytest
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Outcome
from app.market.clock import UTC
from fastapi.testclient import TestClient
from sqlalchemy import delete, select


@pytest.fixture(autouse=True)
def clean_decisions() -> Generator[None, None, None]:
    """The shared test DB keeps rows between runs; start each test empty."""
    with get_session_maker()() as session:
        session.execute(delete(Outcome))
        session.execute(delete(Decision))
        session.execute(delete(Cycle))
        session.commit()
    yield


def _seed_decision() -> int:
    """Insert one cycle, decision and outcome; return the decision id."""
    with get_session_maker()() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=datetime(2026, 10, 15, 16, 46),  # naive UTC
            started_at=datetime(2026, 10, 15, 16, 46),
            finished_at=datetime(2026, 10, 15, 16, 47),
            status="done",
        )
        session.add(cycle)
        session.flush()
        decision = Decision(
            cycle_id=cycle.id,
            symbol="MSFT",
            context="candidate",
            input_snapshot={"symbol": "MSFT", "price": 418.19},
            rules_result={"status": "pass", "reason": None, "checks": []},
            model_output={"action": "enter", "confidence": 0.62},
            validation_result={
                "valid": True,
                "model_action": "enter",
                "final_action": "skip",
                "changed_by_code": True,
                "note": "code changed enter → skip",
            },
            final_action="skip",
            prompt_version="decision.v1",
            prompt_hash="abc123",
            model="fake-decision",
            tokens=100,
            tokens_in=80,
            tokens_out=20,
            cost=None,
            latency_ms=900,
            raw_response='{"action": "enter"}',
        )
        session.add(decision)
        session.flush()
        session.add(Outcome(decision_id=decision.id))
        session.commit()
        return decision.id


def test_decisions_list_empty(client: TestClient) -> None:
    response = client.get("/api/decisions")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["summary"]["calls"] == 0
    assert data["summary"]["prompt_version"] == "decision.v1"


def test_decisions_list_and_detail(client: TestClient) -> None:
    decision_id = _seed_decision()
    response = client.get("/api/decisions")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["calls"] == 1
    assert data["summary"]["changed_by_code"] == 1
    (item,) = data["items"]
    assert item["id"] == decision_id
    assert item["symbol"] == "MSFT"
    assert item["context"] == "candidate"
    assert item["model_action"] == "enter"
    assert item["final_action"] == "skip"
    assert item["changed_by_code"] is True

    detail = client.get(f"/api/decisions/{decision_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["symbol"] == "MSFT"
    assert body["input_snapshot"]["price"] == 418.19
    assert body["model_output"]["action"] == "enter"
    assert body["validation_result"]["changed_by_code"] is True
    assert body["raw_response"] == '{"action": "enter"}'
    assert body["tokens_in"] == 80
    assert body["tokens_out"] == 20
    assert body["latency_ms"] == 900
    assert body["outcome"] == {
        "price_1h": None,
        "price_1session": None,
        "price_5sessions": None,
        "best_price": None,
        "worst_price": None,
    }
    # The versioned prompt file ships with the repo, so text resolves.
    assert body["prompt_version"] == "decision.v1"
    assert "decision layer" in (body["prompt_text"] or "")

    # The outcome row exists for the outcome worker to fill in later.
    with get_session_maker()() as session:
        outcome = session.execute(
            select(Outcome).where(Outcome.decision_id == decision_id)
        ).scalar_one()
    assert outcome.price_1h is None


def test_decision_detail_404(client: TestClient) -> None:
    assert client.get("/api/decisions/9999").status_code == 404


def test_decisions_cycle_time_is_utc(client: TestClient) -> None:
    _seed_decision()
    item = client.get("/api/decisions").json()["items"][0]
    stamp = datetime.fromisoformat(item["cycle_time"])
    assert stamp.tzinfo is not None
    assert stamp.astimezone(UTC).hour == 16
