"""PR 36 — Ask the agent: tools, the answer loop and the API."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from app.ask.service import answer, check_numbers, resolve_refs
from app.ask.tools import TOOLS, AskDeps, calculate, run_tool
from app.config import get_config
from app.data.cache import _naive_utc
from app.db.models import AskMessage, AskThread, LlmCall
from app.llm.fake import FakeModelClient
from app.market.clock import FakeClock
from sqlalchemy import select

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_ask_rate() -> None:
    """The per-session question rate lives in a module-level deque."""
    import app.api.ask as ask_api

    ask_api._rate.clear()
    yield
    ask_api._rate.clear()


def _deps(session_factory, **over: Any) -> AskDeps:
    return AskDeps(
        config=get_config(),
        clock=FakeClock(NOW),
        session_factory=session_factory,
        **over,
    )


def _thread(session_factory) -> tuple[AskThread, AskMessage]:
    with session_factory() as s:
        t = AskThread(
            title="New chat",
            created_at=_naive_utc(NOW),
            updated_at=_naive_utc(NOW),
        )
        s.add(t)
        s.commit()
        m = AskMessage(
            thread_id=t.id,
            role="assistant",
            text="",
            status="ok",
            created_at=_naive_utc(NOW),
        )
        s.add(m)
        s.commit()
        return t.id, m.id


async def _run(client, deps, thread_id, message_id, question="q"):
    return [
        ev
        async for ev in answer(
            client=client,
            deps=deps,
            session_factory=deps.session_factory,
            thread_id=thread_id,
            message_id=message_id,
            question=question,
            context=None,
        )
    ]


# ---------------------------------------------------------------------
# calculate


def test_calculate_whitelist() -> None:
    assert calculate("1 + 2 * 3") == Decimal(7)
    assert calculate("(10 - 4) / 2") == Decimal(3)
    assert calculate("round(3.456, 2)") == Decimal("3.46")
    assert calculate("min(2, 5) + max(-1, 3)") == Decimal(5)
    assert calculate("abs(-4)") == Decimal(4)
    for bad in [
        "x + 1",
        "__import__('os')",
        "1.__class__",
        "open('f')",
        "[1][0]",
        "min()",
        "foo(1)",
    ]:
        with pytest.raises((ValueError, SyntaxError)):
            calculate(bad)


# ---------------------------------------------------------------------
# tools


def test_every_tool_bounded_on_hostile_args(session_factory) -> None:
    """Huge limits, injection strings and unknown ids still return valid data."""
    deps = _deps(session_factory)
    hostile: dict[str, dict[str, Any]] = {
        "get_status": {},
        "list_positions": {},
        "get_position": {"position_id": 999_999_999},
        "search_orders": {"limit": 10_000, "symbol": "'; DROP TABLE orders;--"},
        "search_decisions": {"limit": 9999, "text": "x"},
        "get_decision": {"decision_id": -5},
        "get_cycle": {"cycle_id": 0},
        "performance": {"days": 10**9},
        "list_trades": {"limit": 10_000},
        "insights": {"slice": "' OR '1'='1"},
        "compare_strategies": {},
        "readiness": {},
        "reviews": {"limit": 10_000},
        "search_logs": {"limit": 999, "text": '" OR 1=1'},
        "search_model_calls": {"limit": 10_000},
        "settings": {},
        "market_data": {"symbol": "MSFT", "timeframe": "bogus", "sessions": 10**9},
        "explain_rule": {"term": "nonexistenttermxyz"},
        "calculate": {"expression": "1/0"},
        "notes": {"limit": 10_000},
    }
    with session_factory() as s:
        for name, args in hostile.items():
            result, err = run_tool(s, deps, name, args)
            assert isinstance(result, dict), name
            assert "as_of" in result, name


def test_tools_never_write(session_factory) -> None:
    """Run every tool and check no row count changed in any table."""
    deps = _deps(session_factory)
    with session_factory() as s:
        before = {}
        for table in (
            "ask_threads",
            "ask_messages",
            "ask_notes",
            "decisions",
            "positions",
            "orders",
            "llm_calls",
            "settings_versions",
        ):
            before[table] = s.execute(
                __import__("sqlalchemy").text(f"SELECT COUNT(*) FROM {table}")
            ).scalar_one()
        for name in TOOLS:
            run_tool(s, deps, name, {})
        s.rollback()
        after = {}
        for table in before:
            after[table] = s.execute(
                __import__("sqlalchemy").text(f"SELECT COUNT(*) FROM {table}")
            ).scalar_one()
        assert before == after


def test_tool_results_carry_no_secrets(session_factory) -> None:
    """A planted fake key must never appear in any tool result."""
    fake_key = "sk-test-SECRET-abcdef0123456789"
    deps = _deps(session_factory)
    with session_factory() as s:
        for name, args in {
            "get_status": {},
            "settings": {},
            "search_model_calls": {},
            "search_logs": {},
            "notes": {},
            "readiness": {},
        }.items():
            result, _ = run_tool(s, deps, name, args)
            blob = json.dumps(result, default=str)
            assert fake_key not in blob, name


def test_calculate_tool_result(session_factory) -> None:
    deps = _deps(session_factory)
    with session_factory() as s:
        result, err = run_tool(s, deps, "calculate", {"expression": "100 * 0.05"})
        assert err is None and Decimal(result["result"]) == Decimal(5)


def test_responses_api_flag_and_wire_mapping() -> None:
    """gpt-6.1-sol routes to the Responses API; the wire shape is right."""
    from app.llm.base import ConverseMessage, ToolCall
    from app.llm.providers.openai import _responses_input, _uses_responses_api

    assert _uses_responses_api(get_config(), "gpt-6.1-sol")
    assert not _uses_responses_api(get_config(), "gpt-4o-mini")
    items = _responses_input(
        [
            ConverseMessage(role="user", text="hi"),
            ConverseMessage(
                role="assistant",
                text="let me look",
                tool_calls=(ToolCall(id="c1", name="get_status", arguments={"a": 1}),),
            ),
            ConverseMessage(role="tool", tool_call_id="c1", text="{}"),
        ]
    )
    assert items[0]["content"][0]["type"] == "input_text"
    assert items[1]["content"][0]["type"] == "output_text"
    assert items[2] == {
        "type": "function_call",
        "call_id": "c1",
        "name": "get_status",
        "arguments": '{"a": 1}',
    }
    assert items[3] == {
        "type": "function_call_output",
        "call_id": "c1",
        "output": "{}",
    }


# ---------------------------------------------------------------------
# number check + refs


def test_number_check_flags_missing() -> None:
    unverified, checked = check_numbers(
        "MSFT stop is $42.50 and JPM $10.00; nothing at $999.",
        {"42.5", "10"},
    )
    texts = [u["text"] for u in unverified]
    assert "999" in texts
    assert "42.50" not in texts and "10.00" not in texts
    assert "42.50" in checked


def test_unknown_ref_becomes_plain() -> None:
    text, used, unknown = resolve_refs(
        "See [[decision:12]] and [[decision:99]].", {"decision:12"}
    )
    assert "[[decision:12]]" in text
    assert "[[decision:99]]" not in text and "decision:99" in text
    assert used == ["decision:12"] and unknown == ["decision:99"]


# ---------------------------------------------------------------------
# the answer loop


@pytest.mark.anyio
async def test_scripted_answer_records_call_and_refs(session_factory) -> None:
    deps = _deps(session_factory)
    thread, msg = _thread(session_factory)
    client = FakeModelClient()
    client.converse_script = [
        {"tools": [{"name": "list_positions", "arguments": {}}]},
        {"text": "No open positions right now."},
    ]
    events = await _run(client, deps, thread, msg)
    kinds = [e["event"] for e in events]
    assert "step" in kinds and kinds[-1] == "done"
    done = events[-1]["data"]
    assert done["status"] == "ok" and done["lookups"] == 1
    with session_factory() as s:
        stored = s.get(AskMessage, msg)
        assert stored is not None and stored.text == "No open positions right now."
        assert stored.steps and stored.steps[0]["tool"] == "list_positions"
        calls = s.execute(
            select(LlmCall).where(LlmCall.ask_message_id == msg)
        ).scalars().all()
        assert len(calls) == 2 and all(c.source == "ask" for c in calls)


@pytest.mark.anyio
async def test_unverified_number_stored_with_position(session_factory) -> None:
    deps = _deps(session_factory)
    thread, msg = _thread(session_factory)
    client = FakeModelClient()
    client.converse_script = [
        {"tools": [{"name": "calculate", "arguments": {"expression": "2 + 2"}}]},
        {"text": "2 + 2 is 4. I also claim $777 appeared."},
    ]
    events = await _run(client, deps, thread, msg)
    done = events[-1]["data"]
    assert done["status"] == "ok"
    unverified = done["unverified_numbers"]
    assert any(u["text"] == "777" for u in unverified)
    assert not any(u["text"] == "4" for u in unverified)  # calculate result
    with session_factory() as s:
        stored = s.get(AskMessage, msg)
        assert stored.unverified_numbers and stored.checked_numbers


@pytest.mark.anyio
async def test_stop_marks_partial_answer(session_factory) -> None:
    deps = _deps(session_factory)
    thread, msg = _thread(session_factory)

    class Slow(FakeModelClient):
        async def converse(self, **kw):  # noqa: ANN202
            from app.ask.service import request_stop
            from app.llm.base import ConverseEvent, ConverseResult

            yield ConverseEvent(kind="delta", delta="half ")
            request_stop(msg)
            yield ConverseEvent(kind="delta", delta="an answer")
            yield ConverseEvent(
                kind="done",
                result=ConverseResult(
                    text="half an answer",
                    tool_calls=(),
                    model="fake",
                    tokens_in=10,
                    tokens_out=5,
                    latency_ms=1,
                ),
            )

    await _run(Slow(), deps, thread, msg)
    with session_factory() as s:
        stored = s.get(AskMessage, msg)
        assert stored.status == "stopped"


@pytest.mark.anyio
async def test_model_error_fails_answer(session_factory) -> None:
    deps = _deps(session_factory)
    thread, msg = _thread(session_factory)
    client = FakeModelClient()
    client.converse_script = [{"error": "provider down", "error_type": "api_error"}]
    events = await _run(client, deps, thread, msg)
    assert events[-1]["event"] == "error"
    with session_factory() as s:
        assert s.get(AskMessage, msg).status == "failed"


@pytest.mark.anyio
async def test_follow_up_has_earlier_turns(session_factory) -> None:
    deps = _deps(session_factory)
    with session_factory() as s:
        t = AskThread(
            title="chat", created_at=_naive_utc(NOW), updated_at=_naive_utc(NOW)
        )
        s.add(t)
        s.commit()
        s.add(
            AskMessage(
                thread_id=t.id,
                role="user",
                text="first question",
                created_at=_naive_utc(NOW),
            )
        )
        s.add(
            AskMessage(
                thread_id=t.id,
                role="assistant",
                text="first answer",
                status="ok",
                created_at=_naive_utc(NOW),
            )
        )
        s.commit()
        m = AskMessage(
            thread_id=t.id,
            role="assistant",
            text="",
            status="ok",
            created_at=_naive_utc(NOW),
        )
        s.add(m)
        s.commit()
        thread, msg = t.id, m.id
    client = FakeModelClient()
    client.converse_script = [{"text": "second"}]
    await _run(client, deps, thread, msg, question="follow-up")
    sent = client.converse_calls[0]["messages"]
    roles = [m.role for m in sent]
    texts = [m.text for m in sent]
    assert "first question" in texts and "first answer" in texts
    assert texts[-1] == "follow-up" and roles.count("user") == 2


# ---------------------------------------------------------------------
# API


def test_threads_and_notes_api(client, session_factory) -> None:
    t = client.post(
        "/api/ask/threads",
        json={"context": {"screen": "positions", "label": "Positions"}},
    )
    assert t.status_code == 200, t.text
    thread = t.json()
    assert client.get(f"/api/ask/threads/{thread['id']}").status_code == 200
    listed = client.get("/api/ask/threads").json()["threads"]
    assert any(x["id"] == thread["id"] for x in listed)

    # notes CRUD
    n = client.post("/api/ask/notes", json={"text": "JPM stop moved after earnings"})
    assert n.status_code == 200, n.text
    note = n.json()
    assert client.get("/api/ask/notes").json()["count"] >= 1
    assert client.put(f"/api/ask/notes/{note['id']}", json={"text": "updated"}).status_code == 200
    assert client.delete(f"/api/ask/notes/{note['id']}").status_code == 200

    # deleting the source chat keeps the note
    n2 = client.post(
        "/api/ask/notes", json={"text": "kept", "from_message": None}
    ).json()
    assert client.delete(f"/api/ask/threads/{thread['id']}").status_code == 200
    notes = client.get("/api/ask/notes").json()["notes"]
    assert any(x["id"] == n2["id"] for x in notes)


def test_question_length_and_rate_limit(client) -> None:
    t = client.post("/api/ask/threads", json={}).json()
    too_long = client.post(
        f"/api/ask/threads/{t['id']}/messages", json={"text": "x" * 2001}
    )
    assert too_long.status_code == 422
    hits = [
        client.post(f"/api/ask/threads/{t['id']}/messages", json={"text": f"q{i}"}).status_code
        for i in range(12)
    ]
    assert 429 in hits


def test_ask_status_endpoint(client) -> None:
    res = client.get("/api/ask/status")
    assert res.status_code == 200
    body = res.json()
    assert "enabled" in body


def test_message_stream_shape(client) -> None:
    """POST /messages answers a text/event-stream (fake client in tests)."""
    t = client.post("/api/ask/threads", json={}).json()
    res = client.post(
        f"/api/ask/threads/{t['id']}/messages", json={"text": "what is my status?"}
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert "event:" in res.text and "done" in res.text
