"""Smoke tests for the decision-eval dev harness (app/eval_decision.py).

Covers the argument parsing in ``main`` and one full ``run_eval`` pass
against the scratch database and a fake model client — no network, no
real prompt calls.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Any

import app.eval_decision as eval_decision
import pytest
from app.config import AppConfig
from app.db.models import Cycle, Decision, LlmCall
from app.llm.base import ModelResponse
from sqlalchemy.orm import Session, sessionmaker

WHEN = datetime(2026, 10, 22, 15, 46)  # Thursday 11:46 New York


def _seed_call(
    session_factory: sessionmaker[Session], snapshot: dict[str, Any]
) -> int:
    with session_factory() as session:
        cycle = Cycle(kind="cycle", scheduled_time=WHEN, status="done")
        session.add(cycle)
        session.flush()
        decision = Decision(
            cycle_id=cycle.id,
            symbol="AAPL",
            context="candidate",
            input_snapshot=snapshot,
            final_action="skip",
            prompt_version="decision.v2",
            prompt_hash="ab" * 32,
            model="fake-decision",
        )
        session.add(decision)
        session.flush()
        call = LlmCall(
            sent_at=WHEN,
            session_date=date(2026, 10, 22),
            source="live",
            kind="decision",
            decision_id=decision.id,
            cycle_id=cycle.id,
            symbol="AAPL",
            context="candidate",
            model="fake-decision",
            prompt_version="decision.v2",
            prompt_hash="ab" * 32,
            outcome="success",
            reasons=[],
        )
        session.add(call)
        session.commit()
        return call.id


def _eval_file(tmp_path: Any, call_id: int) -> Any:
    path = tmp_path / "eval.json"
    path.write_text(
        json.dumps(
            {
                "predicates": {"valid_output": "output validates"},
                "calls": [
                    {
                        "llm_call_id": call_id,
                        "category": "baseline",
                        "predicates": ["valid_output"],
                    }
                ],
            }
        )
    )
    return path


def _run(**kwargs: Any) -> int:
    return asyncio.run(eval_decision.run_eval(**kwargs))


class FakeClient:
    """A model client returning one canned decision.v2 answer."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.calls = 0

    async def complete(self, **kwargs: Any) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            raw=self.raw, model="fake-decision",
            tokens_in=10, tokens_out=5, latency_ms=1,
        )


def test_main_requires_prompts() -> None:
    with pytest.raises(SystemExit):
        eval_decision.main([])


def test_main_parses_args(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    seen: dict[str, Any] = {}

    async def fake_run_eval(**kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(eval_decision, "run_eval", fake_run_eval)
    rc = eval_decision.main(
        ["--prompts", "decision.v3, decision.v4", "--model", "m", "--yes"]
    )
    assert rc == 0
    assert seen["prompts"] == ["decision.v3", "decision.v4"]
    assert seen["model"] == "m"
    assert seen["assume_yes"] is True
    assert seen["eval_path"] == eval_decision.EVAL_FILE


def test_run_eval_happy_path(
    session_factory: sessionmaker[Session], tmp_path: Any, capsys: Any
) -> None:
    """One stored call rebuilt from the (empty) bar cache, answered by a
    fake client with a valid skip, passes the valid_output predicate."""
    call_id = _seed_call(
        session_factory,
        {"symbol": "AAPL", "price": "150.00",
         "indicators_1h": {"atr14": "1.20"}},
    )
    client = FakeClient(
        json.dumps(
            {"symbol": "AAPL", "action": "skip", "confidence": 0.4,
             "thesis": "no edge"}
        )
    )
    rc = _run(
        prompts=["decision.v2"],
        model="fake-decision",
        assume_yes=True,
        eval_path=_eval_file(tmp_path, call_id),
        session_factory=session_factory,
        client=client,
        config=AppConfig(),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert client.calls == 1
    assert "1/1" in out


def test_run_eval_skips_unreplayable_call(
    session_factory: sessionmaker[Session], tmp_path: Any, capsys: Any
) -> None:
    """A snapshot without price/ATR can't be rebuilt: the call is
    skipped, the model is never asked, the run still reports."""
    call_id = _seed_call(session_factory, {"symbol": "AAPL"})
    client = FakeClient("{}")
    rc = _run(
        prompts=["decision.v2"],
        model="fake-decision",
        assume_yes=True,
        eval_path=_eval_file(tmp_path, call_id),
        session_factory=session_factory,
        client=client,
        config=AppConfig(),
    )
    assert rc == 0
    assert client.calls == 0
    assert "0/0" in capsys.readouterr().out


def test_run_eval_missing_call_ids(
    session_factory: sessionmaker[Session], tmp_path: Any, capsys: Any
) -> None:
    path = _eval_file(tmp_path, 999999)
    rc = _run(
        prompts=["decision.v2"],
        model="fake-decision",
        assume_yes=True,
        eval_path=path,
        session_factory=session_factory,
        client=FakeClient("{}"),
        config=AppConfig(),
    )
    assert rc == 1
    assert "nothing to run" in capsys.readouterr().out
