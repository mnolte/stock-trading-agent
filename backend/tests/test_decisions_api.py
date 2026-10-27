"""Decisions API: inspector list and detail endpoints."""

from datetime import datetime

from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, LlmCall, Outcome
from app.market.clock import UTC
from fastapi.testclient import TestClient
from sqlalchemy import select


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
    # The seeded decision never sent a call — the drawer hides the link.
    assert body["llm_call_id"] is None
    assert body["outcome"] == {
        "price_1h": None,
        "price_1h_at": None,
        "price_1session": None,
        "price_1session_at": None,
        "price_5sessions": None,
        "price_5sessions_at": None,
        "best_price": None,
        "best_price_at": None,
        "worst_price": None,
        "worst_price_at": None,
        "first_touch": None,
        "r_1h": None,
        "r_1session": None,
        "r_5sessions": None,
        "best_r": None,
        "worst_r": None,
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


def test_decisions_list_shows_no_call_reason(client: TestClient) -> None:
    decision_id = _seed_no_call()
    response = client.get("/api/decisions")
    assert response.status_code == 200
    data = response.json()
    # no_call rows are not model calls.
    assert data["summary"]["calls"] == 0
    assert data["summary"]["no_calls"] == 1
    (item,) = data["items"]
    assert item["final_action"] == "no_call"
    assert item["no_call_reason"] == "rules"

    detail = client.get(f"/api/decisions/{decision_id}").json()
    assert detail["no_call_reason"] == "rules"
    assert detail["model"] == "none"


def test_decision_detail_exposes_its_llm_call(client: TestClient) -> None:
    """The drawer's "Open model call" links ?call= at the real row."""
    decision_id = _seed_decision()
    with get_session_maker()() as session:
        call = LlmCall(
            sent_at=datetime(2026, 10, 15, 16, 46),
            session_date=datetime(2026, 10, 15).date(),
            source="live",
            kind="decision",
            decision_id=decision_id,
            symbol="MSFT",
            outcome="success",
            reasons=[],
            backfilled=False,
        )
        session.add(call)
        session.commit()
        call_id = call.id
    body = client.get(f"/api/decisions/{decision_id}").json()
    assert body["llm_call_id"] == call_id


def _seed_no_call() -> int:
    """Insert one no_call decision (rules-rejected); return the decision id."""
    with get_session_maker()() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=datetime(2026, 10, 15, 16, 46),
            started_at=datetime(2026, 10, 15, 16, 46),
            finished_at=datetime(2026, 10, 15, 16, 47),
            status="done",
        )
        session.add(cycle)
        session.flush()
        decision = Decision(
            cycle_id=cycle.id,
            symbol="XOM",
            context="candidate",
            input_snapshot={"symbol": "XOM"},
            rules_result={"status": "fail", "reason": "RSI below range", "checks": []},
            validation_result={
                "valid": False,
                "model_action": None,
                "final_action": "no_call",
                "changed_by_code": False,
                "error": "rules: fail",
                "note": "No call — rules: fail",
            },
            final_action="no_call",
            no_call_reason="rules",
            prompt_version="decision.v1",
            prompt_hash="abc123",
            model="none",
        )
        session.add(decision)
        session.flush()
        session.add(Outcome(decision_id=decision.id))
        session.commit()
        return decision.id


def test_decision_detail_404(client: TestClient) -> None:
    assert client.get("/api/decisions/9999").status_code == 404


def _seed_today(**over: object) -> int:
    """A decision on a cycle from today so the dashboard picks it up."""
    fields: dict[str, object] = {
        "symbol": "XOM",
        "context": "candidate",
        "input_snapshot": {"symbol": "XOM", "price": 120.5},
        "rules_result": {"status": "pass", "reason": None, "checks": []},
        "model_output": None,
        "validation_result": {},
        "final_action": "skip",
        "prompt_version": "decision.v1",
        "prompt_hash": "abc123",
        "model": "fake-decision",
    }
    fields.update(over)
    now = datetime.now(UTC).replace(tzinfo=None)
    with get_session_maker()() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=now,
            started_at=now,
            finished_at=now,
            status="done",
        )
        session.add(cycle)
        session.flush()
        decision = Decision(cycle_id=cycle.id, **fields)
        session.add(decision)
        session.flush()
        session.add(Outcome(decision_id=decision.id))
        session.commit()
        return decision.id


def test_decision_detail_trade_and_outcome_keys(client: TestClient) -> None:
    decision_id = _seed_decision()
    body = client.get(f"/api/decisions/{decision_id}").json()
    assert {
        "trade",
        "earnings",
        "next_outcome_due",
        "execution_mode",
        "eur_usd_rate",
    } <= set(body)
    # No position or orders: the snapshot price stands in as the proposed
    # entry and the model's levels are absent, so no R can be computed.
    assert body["trade"]["status"] == "none"
    assert body["trade"]["entry"] == "418.19"
    assert body["outcome"]["first_touch"] is None
    assert body["outcome"]["r_1h"] is None


def test_dashboard_latest_decision_for_symbol(client: TestClient) -> None:
    _seed_today(
        symbol="XOM",
        final_action="no_call",
        no_call_reason="rules",
        model="none",
        model_output=None,
        rules_result={
            "status": "fail",
            "reason": "RSI 71 is above the 70 limit",
            "checks": [
                {"name": "RSI 40–70", "passed": False, "detail": "RSI 71 > 70"}
            ],
        },
        input_snapshot={
            "symbol": "XOM",
            "price": 120.5,
            "regime": {"trend": "up", "volatility": "normal"},
            "indicators_1h": {"rsi14": 71, "vwap": 119.8, "obv_trend": "up"},
            "indicators_daily": {"close": 121, "sma50": 118},
            "days_to_earnings": 12,
        },
    )
    card = client.get("/api/dashboard?symbol=XOM").json()["latest_decision"]
    assert card["symbol"] == "XOM"
    assert card["final_action"] == "no_call"
    assert card["regime"] == {"trend": "up", "volatility": "normal"}
    assert card["earnings"]["sessions"] == 12
    assert card["earnings"]["date"] is None
    # No model call: pre-filter reason, then model-call row, then next look.
    assert len(card["box"]) == 3
    assert card["box"][0]["label"] == "Pre-filter"
    assert card["box"][0]["value"] == "RSI 71 > 70"
    assert card["box"][0]["tone"] == -1
    assert card["box"][1] == {
        "label": "Model call",
        "value": "none this cycle",
        "tone": 0,
    }
    assert card["box"][2]["label"] == "Next look"
    # Signal chips carry text and a tone for the colour.
    assert {"text", "tone"} <= set(card["sig"][0])


def test_dashboard_latest_decision_held_box(client: TestClient) -> None:
    _seed_today(
        symbol="MSFT",
        context="held",
        final_action="hold",
        model_output={"action": "hold", "confidence": 0.66},
        input_snapshot={
            "symbol": "MSFT",
            "context": "held",
            "price": 421.3,
            "time_ny": "2026-10-15T10:46:00-04:00",
            "position": {
                "qty": 4,
                "entry": 418.2,
                "stop": 409.6,
                "target": 435.4,
                "opened_at": "2026-10-15T09:46:00-04:00",
                "r_so_far": 0.36,
            },
        },
    )
    card = client.get("/api/dashboard?symbol=MSFT").json()["latest_decision"]
    assert card["symbol"] == "MSFT"
    labels = [row["label"] for row in card["box"]]
    assert labels == ["Position", "Stop / target", "Agent exit"]
    assert card["box"][0]["value"] == "4 sh @ 418.20 · +0.36R"
    assert card["box"][1]["value"] == "409.60 / 435.40"
    # The day-trade rule is retired: an agent exit can happen this session.
    assert card["box"][2]["value"] == "now"


def test_dashboard_latest_decision_skip_box(client: TestClient) -> None:
    _seed_today(
        symbol="AAPL",
        final_action="skip",
        model_output={
            "action": "enter",
            "confidence": 0.58,
            "stop_price": 249.25,
            "target_price": 255.8,
        },
        validation_result={
            "valid": False,
            "model_action": "enter",
            "final_action": "skip",
            "changed_by_code": True,
            "checks": [
                {
                    "name": "Reward to risk ≥ 1.5",
                    "passed": False,
                    "detail": "",
                    "value": "1.3",
                },
                {
                    "name": "Stop 1.5–4 × ATR",
                    "passed": True,
                    "detail": "",
                    "value": "2.1 × ATR",
                },
            ],
        },
        input_snapshot={"symbol": "AAPL", "price": 252.1},
    )
    card = client.get("/api/dashboard?symbol=AAPL").json()["latest_decision"]
    labels = [row["label"] for row in card["box"]]
    assert labels[0] == "Reward to risk ≥ 1.5"
    assert card["box"][0]["value"] == "1.3 ✗"
    assert card["box"][0]["tone"] == -1
    assert card["box"][1]["value"] == "2.1 × ATR ✓"
    assert card["box"][2] == {
        "label": "Proposed stop / target",
        "value": "249.25 / 255.80",
        "tone": 0,
    }


def test_decisions_cycle_time_is_utc(client: TestClient) -> None:
    _seed_decision()
    item = client.get("/api/decisions").json()["items"][0]
    stamp = datetime.fromisoformat(item["cycle_time"])
    assert stamp.tzinfo is not None
    assert stamp.astimezone(UTC).hour == 16
