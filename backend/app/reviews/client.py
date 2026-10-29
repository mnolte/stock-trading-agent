"""Review call schemas and compat (PR 29): model calls moved to the
shared ``app/llm`` interface — reviews use the same client with their
own model (``reviews.models.*``), temperature (``reviews.temperature``,
OpenAI models only) and monthly cost cap.

A missing or failing provider key is never an error here — the caller
falls back to the plain template and labels the reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.llm.base import ModelClient, ModelResponse  # noqa: F401
from app.llm.clients import ProviderClients  # noqa: F401
from app.llm.fake import FakeHandler, FakeModelClient, default_review_reply  # noqa: F401
from app.llm.providers.openai import OpenAIAdapter  # noqa: F401

if TYPE_CHECKING:
    from app.config import AppConfig

# Structured-outputs schemas. The model writes only text; every fact is
# computed in code before the call.
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

# Old names → the unified interface.
ReviewClient = ModelClient
OpenAIReviewClient = OpenAIAdapter
FakeReviewClient = FakeModelClient
default_fake_reply = default_review_reply


def make_review_client(
    config: AppConfig, api_key: str | None = None
) -> ModelClient | None:
    """Simulated mode uses the fake; without a key the caller falls
    back to the plain template (a missing key is never a crash)."""
    if config.env.mode == "simulated":
        return FakeModelClient()
    if not api_key:
        return None
    return ProviderClients(
        config, key_for=lambda provider: api_key if provider == "openai" else None
    )
