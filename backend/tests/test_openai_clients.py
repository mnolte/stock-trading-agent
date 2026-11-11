"""The OpenAI SDK defaults to 2 retries; the spec says no retry. Both real
clients must pin ``max_retries=0`` — including after a key swap."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.config import AppConfig
from app.decide.client import OpenAIDecisionClient, token_cost
from app.llm.base import ConverseMessage
from app.llm.providers.openai import OpenAIAdapter
from app.reviews.client import OpenAIReviewClient

pytestmark = pytest.mark.anyio


class _Recorder:
    """Captures AsyncOpenAI construction kwargs without a real client."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _record(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def factory(**kwargs: object) -> _Recorder:
        calls.append(kwargs)
        return _Recorder(**kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", factory)
    return calls


def test_decision_client_no_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record(monkeypatch)
    config = AppConfig()

    client = OpenAIDecisionClient(config, api_key="sk-test")

    assert calls[-1]["max_retries"] == 0
    assert calls[-1]["timeout"] == float(
        config.get("decision", "call_timeout_seconds", default=30)
    )
    assert calls[-1]["api_key"] == "sk-test"

    client.update_key("sk-new")
    assert calls[-1]["max_retries"] == 0


def test_review_client_no_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record(monkeypatch)
    config = AppConfig()

    client = OpenAIReviewClient(config, api_key="sk-test")

    assert calls[-1]["max_retries"] == 0
    assert calls[-1]["timeout"] == float(
        config.get("decision", "call_timeout_seconds", default=30)
    )

    client.update_key("sk-new")
    assert calls[-1]["max_retries"] == 0


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-4o-mini"])
@pytest.mark.parametrize("method", ["complete", "converse"])
async def test_temperature_capability(
    monkeypatch: pytest.MonkeyPatch, model: str, method: str,
) -> None:
    config = AppConfig()
    config._active["models"][model].pop("api", None)
    completion = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}", refusal=None))],
    )
    raw = SimpleNamespace(headers={}, parse=lambda: completion)

    async def chunks() -> Any:
        empty: list[Any] = []
        for chunk in empty:
            yield chunk

    create = AsyncMock(return_value=raw if method == "complete" else chunks())
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=create, with_raw_response=SimpleNamespace(create=create),
    )))
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **kwargs: client)
    adapter = OpenAIAdapter(config, api_key="sk-test")
    if method == "complete":
        result = await adapter.complete(
            system="system", user="user", model=model, temperature=0.2,
        )
        assert result.error is None
    else:
        events = [event async for event in adapter.converse(
            system="system", messages=[ConverseMessage(role="user", text="user")],
            tools=[], model=model,
        )]
        assert events[-1].result.error is None
    assert create.await_count == 1
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == model
    if model == "gpt-6.1-sol":
        assert "temperature" not in kwargs
    else:
        assert kwargs["temperature"] == 0.2


async def test_complete_reports_cached_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """086: prompt_tokens_details.cached_tokens reaches the response and
    token_cost prices it at cached_input (subset of prompt_tokens)."""
    config = AppConfig()
    completion = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=2000,
            completion_tokens=500,
            prompt_tokens_details=SimpleNamespace(cached_tokens=800),
        ),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="{}", refusal=None),
                finish_reason="stop",
            )
        ],
    )
    raw = SimpleNamespace(headers={}, parse=lambda: completion)
    create = AsyncMock(return_value=raw)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=create, with_raw_response=SimpleNamespace(create=create),
    )))
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **kwargs: client)
    adapter = OpenAIAdapter(config, api_key="sk-test")
    resp = await adapter.complete(system="s", user="u", model="gpt-4o-mini")
    assert resp.error is None
    assert resp.tokens_in == 2000
    assert resp.cached_tokens_in == 800
    # 1200 x 0.15 + 800 x 0.075 + 500 x 0.60 per MTok.
    assert token_cost(resp, config) == Decimal("0.00054")
