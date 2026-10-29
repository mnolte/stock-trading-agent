"""PR 29 — model providers: OpenAI and Anthropic (docs/SPEC.md → Model
providers).

No network: the Anthropic adapter is exercised with a stub client whose
``messages.with_raw_response.create`` replays recorded responses, and
every gate test uses the fake stores.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from app.config import AppConfig
from app.llm.models import (
    UnknownModelError,
    check_models,
    model_provider,
    models_table,
)
from app.llm.providers.anthropic import (
    _UNSUPPORTED_KEYS,
    AnthropicAdapter,
    anthropic_safe_schema,
)

pytestmark = pytest.mark.anyio

# ---------------------------------------------------------------------
# schema transform


def _find_unsupported(node: Any, banned: set[str] | frozenset[str]) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in banned or (key == "additionalProperties" and value is not False):
                found.append(key)
            found += _find_unsupported(value, banned)
    elif isinstance(node, list):
        for value in node:
            found += _find_unsupported(value, banned)
    return found


@pytest.mark.parametrize("version", ["decision.v1", "decision.v2"])
def test_sent_schema_has_no_unsupported_keywords(version: str) -> None:
    from app.decide.prompt import prompt_spec

    spec = prompt_spec(version)
    sent = anthropic_safe_schema(dict(spec.json_schema["schema"]))
    assert _find_unsupported(sent, _UNSUPPORTED_KEYS) == []


def test_additional_properties_other_than_false_stripped() -> None:
    schema = {
        "type": "object",
        "additionalProperties": {"type": "string"},
        "properties": {"a": {"type": "string", "minLength": 1}},
    }
    out = anthropic_safe_schema(schema)
    assert "additionalProperties" not in out
    assert "minLength" not in out["properties"]["a"]
    assert schema["additionalProperties"] == {"type": "string"}  # untouched


def test_full_schema_still_enforced_locally() -> None:
    """Pydantic rejects what the stripped wire schema no longer blocks."""
    from decimal import Decimal

    from app.decide.output import validate_model_output, validate_model_output_v2

    config = _config()
    v1 = validate_model_output(
        json.dumps(
            {
                "symbol": "MSFT",
                "action": "enter",
                "confidence": 1.5,
                "stop_price": 400.0,
                "target_price": 450.0,
                "thesis": "t",
                "invalidation": "i",
                "risks": [],
                "news_used": [],
            }
        ),
        symbol="MSFT",
        context="candidate",
        price=Decimal("425"),
        atr_1h=Decimal("2"),
        current_stop=None,
        config=config,
    )
    assert not v1.valid
    snapshot = {
        "symbol": "MSFT",
        "options": [
            {"id": "o1", "limit": 100.0, "stop": 95.0, "target": 115.0}
        ],
    }
    v2 = validate_model_output_v2(
        json.dumps(
            {
                "symbol": "MSFT",
                "action": "enter",
                "option_id": "not-offered",
                "confidence": 0.6,
                "thesis": "t",
                "levels_used": [],
                "risks": [],
                "news_used": [],
            }
        ),
        symbol="MSFT",
        context="candidate",
        snapshot=snapshot,
        atr_1h=Decimal("2"),
        current_stop=None,
        config=config,
    )
    assert not v2.valid


# ---------------------------------------------------------------------
# anthropic adapter with a stubbed client


class _StubRawResponse:
    def __init__(self, message: Any = None, exc: Exception | None = None) -> None:
        self._message = message
        self._exc = exc
        self.headers = {"request-id": "req_stub_1"}

    async def parse(self) -> Any:
        if self._exc is not None:
            raise self._exc
        return self._message


class _StubMessages:
    def __init__(self, raw: _StubRawResponse, calls: list[dict]) -> None:
        self.with_raw_response = SimpleNamespace(
            create=self._create(raw, calls)
        )

    @staticmethod
    def _create(raw: _StubRawResponse, calls: list[dict]):
        async def create(**kwargs):
            calls.append(kwargs)
            return raw

        return create


def _adapter(config: AppConfig, raw: _StubRawResponse, calls: list[dict]) -> AnthropicAdapter:
    adapter = AnthropicAdapter(config, api_key="sk-ant-test")
    adapter._client = SimpleNamespace(messages=_StubMessages(raw, calls))
    return adapter


def _message(text: str, stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def _config() -> AppConfig:
    return AppConfig()


async def test_anthropic_success_parses_and_sends_no_temperature() -> None:
    calls: list[dict] = []
    raw = _StubRawResponse(_message('{"a": 1}'))
    adapter = _adapter(_config(), raw, calls)
    resp = await adapter.complete(
        system="sys", user='{"symbol": "MSFT"}', schema=None,
        model="claude-haiku-5-5", temperature=0,
    )
    assert resp.error is None and resp.parsed == {"a": 1}
    assert resp.provider == "anthropic" and resp.request_id == "req_stub_1"
    sent = calls[0]
    assert "temperature" not in sent and "top_p" not in sent and "top_k" not in sent
    assert sent["output_config"]["effort"] == "low"
    fmt = sent["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert _find_unsupported(fmt["schema"], _UNSUPPORTED_KEYS) == []
    assert sent["max_tokens"] == 2048


async def test_anthropic_refusal_maps_to_refused() -> None:
    adapter = _adapter(_config(), _StubRawResponse(_message("", "refusal")), [])
    resp = await adapter.complete(system="s", user="u", model="claude-haiku-5-5")
    assert resp.refused is True and resp.error is None


async def test_anthropic_max_tokens_is_truncated_invalid_output() -> None:
    adapter = _adapter(
        _config(), _StubRawResponse(_message('{"partial":', "max_tokens")), []
    )
    resp = await adapter.complete(system="s", user="u", model="claude-haiku-5-5")
    assert resp.error_type == "invalid_output" and resp.error == "truncated"
    assert resp.raw == ""


async def test_anthropic_529_is_api_error_with_type() -> None:
    import httpx
    from anthropic import InternalServerError

    response = httpx.Response(
        529,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        headers={"request-id": "req_529"},
    )
    exc = InternalServerError(
        "overloaded",
        response=response,
        body={"error": {"type": "overloaded_error", "message": "busy"}},
    )
    adapter = _adapter(_config(), _StubRawResponse(exc=exc), [])
    resp = await adapter.complete(system="s", user="u", model="claude-haiku-5-5")
    assert resp.error_type == "api_error"
    assert resp.http_status == 529
    assert resp.error_code == "overloaded_error"
    assert resp.request_id == "req_529"


async def test_anthropic_timeout_maps_to_timeout() -> None:
    import httpx
    from anthropic import APITimeoutError

    exc = APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    adapter = _adapter(_config(), _StubRawResponse(exc=exc), [])
    resp = await adapter.complete(system="s", user="u", model="claude-haiku-5-5")
    assert resp.error_type == "timeout"


async def test_only_final_text_stored() -> None:
    calls: list[dict] = []
    msg = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="hmm"),
            SimpleNamespace(type="text", text='{"ok": true}'),
        ],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    adapter = _adapter(_config(), _StubRawResponse(msg), calls)
    resp = await adapter.complete(system="s", user="u", model="claude-haiku-5-5")
    assert resp.raw == '{"ok": true}'


# ---------------------------------------------------------------------
# config: the models list is the only name source


def test_unknown_decision_model_fails_startup() -> None:
    config = _config()
    config.set_pending({**config.effective(), "decision": {"model": "gpt-9"}}, 99)
    config.promote_pending()
    with pytest.raises(UnknownModelError, match="gpt-9"):
        check_models(config)


def test_legacy_model_prices_warn(caplog) -> None:
    """With no ``models`` block the legacy keys load, with a warning."""
    import app.llm.models as models_mod

    class _Stub:
        def get(self, *keys: str, default=None):
            tree = {
                "model_prices": {
                    "gpt-4o-mini": {"input": 0.15, "output": 0.60}
                },
                "backtest": {"model_cutoffs": {"gpt-4o-mini": "2023-10-01"}},
            }
            node = tree
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node

    models_mod._legacy_warned = False
    with caplog.at_level("WARNING"):
        table = models_table(_Stub())  # type: ignore[arg-type]
    assert table["gpt-4o-mini"]["provider"] == "openai"
    assert table["gpt-4o-mini"]["cutoff"] == "2023-10-01"
    assert any("models" in r.message for r in caplog.records)


def test_model_provider_and_cutoff() -> None:
    config = _config()
    assert model_provider(config, "claude-haiku-5-5") == "anthropic"
    assert model_provider(config, "gpt-4o-mini") == "openai"
    from app.llm.models import model_cutoff

    assert model_cutoff(config, "claude-haiku-5-5") == date(2026, 6, 30)


def test_costs_use_model_list_prices_both_providers() -> None:
    from app.llm.base import ModelResponse, token_cost

    config = _config()
    for model in ("gpt-4o-mini", "claude-haiku-5-5"):
        resp = ModelResponse(
            raw="{}", model=model, tokens_in=1_000_000, tokens_out=1_000_000,
            latency_ms=1, provider=model_provider(config, model),
        )
        cost = token_cost(resp, config)
        entry = models_table(config)[model]
        expected = Decimal(str(entry["input"])) + Decimal(str(entry["output"]))
        assert cost == expected
