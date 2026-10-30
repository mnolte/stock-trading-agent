"""Model client for reviews (docs/SPEC.md, Reviews).

Same model as decisions (gpt-4o-mini, temperature 0.2) but a separate
client and its own monthly cost cap: review calls can never use budget or
rate limits needed by trading decisions. The client sits behind a
protocol so tests and simulated mode never touch the network. A missing
API key is not an error here — the caller falls back to the plain
template and labels the reason.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, cast

from app.decide.client import ModelResponse, _classify_error

if TYPE_CHECKING:
    from app.config import AppConfig

# OpenAI structured-outputs schemas (strict mode). The model writes only
# text; every fact is computed in code before the call.
WEEKLY_JSON_SCHEMA: dict[str, Any] = {
    "name": "weekly_review",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "observations": {"type": "array", "items": {"type": "string"}},
            "suggestion": {"type": ["string", "null"]},
        },
        "required": ["summary", "observations", "suggestion"],
    },
}

BRIEFING_JSON_SCHEMA: dict[str, Any] = {
    "name": "daily_briefing",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}

VERDICT_JSON_SCHEMA: dict[str, Any] = {
    "name": "decision_verdict",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["good_call", "bad_call", "unclear"],
            },
            "reason": {"type": "string"},
        },
        "required": ["verdict", "reason"],
    },
}

# weekly.v2 (PR 32): suggestion is {text, evidence} or null.
WEEKLY_V2_JSON_SCHEMA: dict[str, Any] = {
    "name": "weekly_review_v2",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "observations": {"type": "array", "items": {"type": "string"}},
            "suggestion": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["text", "evidence"],
            },
        },
        "required": ["summary", "observations", "suggestion"],
    },
}

# verdict.v2 (PR 32): no verdict field — the label is code's draft_verdict.
VERDICT_V2_JSON_SCHEMA: dict[str, Any] = {
    "name": "decision_verdict_v2",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reason": {"type": "string"},
            "thesis_check": {
                "type": "string",
                "enum": ["consistent", "contradicts", "unclear"],
            },
            "contradiction": {"type": ["string", "null"]},
        },
        "required": ["reason", "thesis_check", "contradiction"],
    },
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "weekly": WEEKLY_JSON_SCHEMA,
    "briefing": BRIEFING_JSON_SCHEMA,
    "verdict": VERDICT_JSON_SCHEMA,
    "weekly.v2": WEEKLY_V2_JSON_SCHEMA,
    "verdict.v2": VERDICT_V2_JSON_SCHEMA,
}


class ReviewClient(Protocol):
    """Sends one review prompt and returns the raw completion."""

    async def complete(
        self, *, system: str, user: str, schema: dict[str, Any]
    ) -> ModelResponse:
        """Complete one review call; ``error`` is set on failure."""
        ...


class OpenAIReviewClient:
    """Configured review model via the OpenAI SDK, strict JSON schema."""

    def __init__(self, config: AppConfig, api_key: str) -> None:
        self.model = str(config.get("reviews", "model", default="gpt-4o-mini"))
        self.temperature = float(config.get("reviews", "temperature", default=0.2))
        self._timeout = float(
            config.get("decision", "call_timeout_seconds", default=30)
        )
        self._build(api_key)

    def _build(self, api_key: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key, timeout=self._timeout, max_retries=0
        )

    def update_key(self, api_key: str) -> None:
        """Swap the key in place after a Settings save."""
        self._build(api_key)

    async def complete(
        self, *, system: str, user: str, schema: dict[str, Any]
    ) -> ModelResponse:
        from openai.types.chat.completion_create_params import ResponseFormat

        started = time.monotonic()
        try:
            response = await self._client.chat.completions.with_raw_response.create(
                model=self.model,
                temperature=self.temperature,
                response_format=cast(
                    ResponseFormat,
                    {"type": "json_schema", "json_schema": schema},
                ),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            request_id = response.headers.get("x-request-id")
            completion = response.parse()
        except Exception as exc:  # noqa: BLE001
            return ModelResponse(
                raw=None,
                model=self.model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=str(exc),
                error_type=_classify_error(exc),
                http_status=getattr(exc, "status_code", None),
                error_code=getattr(exc, "code", None),
                request_id=getattr(exc, "request_id", None),
            )
        usage = completion.usage
        message = completion.choices[0].message if completion.choices else None
        return ModelResponse(
            raw=message.content if message else None,
            model=self.model,
            tokens_in=usage.prompt_tokens if usage else None,
            tokens_out=usage.completion_tokens if usage else None,
            latency_ms=int((time.monotonic() - started) * 1000),
            request_id=request_id,
            refused=bool(getattr(message, "refusal", None)),
        )


# A test/simulated handler answers from the system prompt and user payload.
FakeHandler = Callable[[str, str], str]


def default_fake_reply(system: str, user: str) -> str:
    """Answer with the plain template built from the given facts.

    Simulated mode gets review text that passes the number check by
    construction — the template only quotes numbers it was given.
    """
    from app.reviews.templates import plain_content

    payload = json.loads(user)
    return json.dumps(
        plain_content(str(payload.get("kind", "weekly")), payload.get("facts", {}))
    )


class FakeReviewClient:
    """Deterministic review model for tests and simulated mode."""

    def __init__(
        self,
        handler: FakeHandler | None = None,
        error: str | None = None,
        error_type: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.handler = handler or default_fake_reply
        self.error = error
        self.error_type = error_type
        self.http_status = http_status
        self.model = "fake-review"

    async def complete(
        self, *, system: str, user: str, schema: dict[str, Any]
    ) -> ModelResponse:
        started = time.monotonic()
        if self.error is not None:
            return ModelResponse(
                raw=None,
                model=self.model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=self.error,
                error_type=self.error_type,
                http_status=self.http_status,
            )
        raw = self.handler(system, user)
        # Deterministic pseudo-usage so cost tracking is exercised end to end.
        tokens_in = len(system + user) // 4
        tokens_out = len(raw) // 4
        return ModelResponse(
            raw=raw,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=int((time.monotonic() - started) * 1000) or 1,
        )


def make_review_client(
    config: AppConfig, api_key: str | None = None
) -> ReviewClient | None:
    """Simulated mode uses the fake; without an OpenAI key the caller falls
    back to the plain template (spec: "no OpenAI key" is a fallback reason,
    never a crash)."""
    if config.env.mode == "simulated":
        return FakeReviewClient()
    if not api_key:
        return None
    return OpenAIReviewClient(config, api_key=api_key)
