"""The Anthropic adapter (docs/SPEC.md → Model providers).

The official ``anthropic`` Python SDK and the Messages API: the prompt
file is the system prompt, the input snapshot the user message. JSON
output goes through ``output_config.format`` with type ``json_schema``;
schema keywords Anthropic doesn't accept are removed from the sent copy
only — Pydantic still enforces every one of them on the answer. No
``temperature``/``top_p``/``top_k`` is ever sent (current Claude models
reject them); the model's ``effort`` from the ``models`` list goes in
their place. ``max_tokens`` is ``decision.max_output_tokens``. Only the
final text is stored — never thinking. One try, no retry.

Stop reasons: ``refusal`` → the ``refusal`` error; ``max_tokens`` →
``invalid_output`` with reason ``truncated``. HTTP 529/5xx and
connection errors → ``api_error`` with the status and Anthropic's error
type. The only module outside this one that may touch the ``anthropic``
package is a test that checks this boundary (hard rule 21).
"""

from __future__ import annotations

import copy
import json
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from app.decide.output import DECISION_JSON_SCHEMA
from app.llm.base import ModelResponse
from app.llm.models import model_effort, model_timeout_seconds

if TYPE_CHECKING:
    from anthropic.types import OutputConfigParam

    from app.config import AppConfig

logger = logging.getLogger("model.anthropic")

# JSON Schema keywords Anthropic's structured outputs reject (numeric,
# string and array constraints, and additionalProperties other than
# false). They are stripped from the copy that is sent; Pydantic still
# validates the answer against the full schema, so the rules are
# identical for both providers.
_UNSUPPORTED_KEYS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)


def anthropic_safe_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema as it may be sent to Anthropic.

    A deep copy with every unsupported keyword removed; ``schema``
    itself is never mutated. ``additionalProperties`` survives only as
    ``false``.
    """
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key in _UNSUPPORTED_KEYS:
                    continue
                if key == "additionalProperties" and value is not False:
                    continue
                out[key] = walk(value)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return cast(dict[str, Any], walk(copy.deepcopy(schema)))


def _inner_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """The JSON schema inside a strict-outputs envelope."""
    if schema is None:
        inner_default = DECISION_JSON_SCHEMA["schema"]
        return dict(inner_default) if isinstance(inner_default, dict) else {}
    inner = schema.get("schema")
    return inner if isinstance(inner, dict) else schema


def _error_type(exc: Exception) -> str | None:
    """Anthropic's error type (``overloaded_error``, ``authentication_error``…)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("type"), str):
            return str(err["type"])
    return getattr(exc, "type", None)


def _classify(exc: Exception) -> str:
    """"timeout" or "api_error" for the llm_calls row (docs/SPEC.md)."""
    from anthropic import APITimeoutError

    if isinstance(exc, APITimeoutError | TimeoutError):
        return "timeout"
    return "api_error"


class AnthropicAdapter:
    """Any model whose provider is ``anthropic``, via the Messages API."""

    provider = "anthropic"

    def __init__(
        self,
        config: AppConfig,
        api_key: str,
        default_model: str | None = None,
    ) -> None:
        self._config = config
        self.default_model = default_model or str(
            config.get("decision", "model", default="gpt-4o-mini")
        )
        self.model = self.default_model
        self._build(api_key)

    def _build(self, api_key: str) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key, max_retries=0)

    def update_key(self, api_key: str) -> None:
        """Swap the key in place after a Settings save."""
        self._build(api_key)

    async def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        use_model = model or self.default_model
        effort = model_effort(self._config, use_model)
        timeout = model_timeout_seconds(self._config, use_model)
        max_tokens = int(
            self._config.get("decision", "max_output_tokens", default=2048)
        )
        started = time.monotonic()
        try:
            response = await self._client.messages.with_raw_response.create(
                model=use_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=cast(
                    "OutputConfigParam",
                    {
                        "effort": effort,
                        "format": {
                            "type": "json_schema",
                            "schema": anthropic_safe_schema(_inner_schema(schema)),
                        },
                    },
                ),
                timeout=timeout,
            )
            request_id = response.headers.get("request-id")
            message = await response.parse()
        except Exception as exc:  # noqa: BLE001
            return ModelResponse(
                raw=None,
                model=use_model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=str(exc),
                error_type=_classify(exc),
                http_status=getattr(exc, "status_code", None),
                error_code=_error_type(exc),
                request_id=getattr(exc, "request_id", None),
                provider=self.provider,
            )
        usage = message.usage
        # Only the final text is kept — thinking is billed but never stored.
        raw = "".join(
            block.text for block in message.content if block.type == "text"
        )
        refused = message.stop_reason == "refusal"
        error: str | None = None
        error_type: str | None = None
        parsed: Any = None
        if message.stop_reason == "max_tokens":
            error = "truncated"
            error_type = "invalid_output"
            raw = ""
        elif raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                error = "not JSON"
                error_type = "invalid_output"
        return ModelResponse(
            raw=raw if not refused else (raw or None),
            model=use_model,
            tokens_in=usage.input_tokens if usage else None,
            tokens_out=usage.output_tokens if usage else None,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=error,
            error_type=error_type,
            request_id=request_id,
            refused=refused,
            provider=self.provider,
            parsed=parsed,
        )


async def test_key(key: str) -> tuple[bool, str | None]:
    """The spec's key check: one Anthropic list-models call."""
    from anthropic import AsyncAnthropic

    try:
        client = AsyncAnthropic(api_key=key, timeout=15)
        await client.models.list()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
