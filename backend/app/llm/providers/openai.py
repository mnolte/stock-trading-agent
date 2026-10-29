"""The OpenAI adapter (docs/SPEC.md → Model providers).

The OpenAI Python SDK with strict structured outputs and the task's
temperature (``decision.temperature`` = 0, ``reviews.temperature`` = 0.2).
The prompt file is the system message, the input snapshot the user
message; one try, never retried. The only module outside this one that
may touch the ``openai`` package is a test that checks this boundary
(hard rule 21).
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from app.decide.output import DECISION_JSON_SCHEMA
from app.llm.base import ModelResponse
from app.llm.models import model_timeout_seconds

if TYPE_CHECKING:
    from app.config import AppConfig

logger = logging.getLogger("model.openai")


def _classify_error(exc: Exception) -> str:
    """"timeout" or "api_error" for the llm_calls row (docs/SPEC.md)."""
    from openai import APITimeoutError

    if isinstance(exc, APITimeoutError | TimeoutError):
        return "timeout"
    return "api_error"


class OpenAIAdapter:
    """Any model whose provider is ``openai``, via the OpenAI SDK."""

    provider = "openai"

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
        self.temperature = float(
            config.get("decision", "temperature", default=0)
        )
        self._build(api_key)

    def _build(self, api_key: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, max_retries=0)

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
        from openai.types.chat.completion_create_params import ResponseFormat

        use_model = model or self.default_model
        use_temperature = (
            float(temperature) if temperature is not None else self.temperature
        )
        timeout = model_timeout_seconds(self._config, use_model)
        started = time.monotonic()
        try:
            response = await self._client.chat.completions.with_raw_response.create(
                model=use_model,
                temperature=use_temperature,
                response_format=cast(
                    ResponseFormat,
                    {
                        "type": "json_schema",
                        "json_schema": schema or DECISION_JSON_SCHEMA,
                    },
                ),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                timeout=timeout,
            )
            request_id = response.headers.get("x-request-id")
            completion = response.parse()
        except Exception as exc:  # noqa: BLE001
            kind = _classify_error(exc)
            return ModelResponse(
                raw=None,
                model=use_model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=str(exc),
                error_type=kind,
                http_status=getattr(exc, "status_code", None),
                error_code=getattr(exc, "code", None),
                request_id=getattr(exc, "request_id", None),
                provider=self.provider,
            )
        usage = completion.usage
        message = completion.choices[0].message if completion.choices else None
        raw = message.content if message else None
        parsed: Any = None
        error: str | None = None
        error_type: str | None = None
        if raw is not None:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                error = "not JSON"
                error_type = "invalid_output"
        return ModelResponse(
            raw=raw,
            model=use_model,
            tokens_in=usage.prompt_tokens if usage else None,
            tokens_out=usage.completion_tokens if usage else None,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=error,
            error_type=error_type,
            request_id=request_id,
            refused=bool(getattr(message, "refusal", None)),
            provider=self.provider,
            parsed=parsed,
        )


async def test_key(key: str) -> tuple[bool, str | None]:
    """The spec's key check: one OpenAI list-models call."""
    from openai import AsyncOpenAI

    try:
        client = AsyncOpenAI(api_key=key, timeout=15)
        await client.models.list()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
