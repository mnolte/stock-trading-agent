"""Model client compatibility (PR 29): the implementation moved to
``app/llm`` — one interface, one adapter per provider, a fake for tests
(docs/SPEC.md → Model providers). The names below keep existing callers
and tests working unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.llm.base import ModelClient, ModelResponse, token_cost  # noqa: F401
from app.llm.clients import ProviderClients, make_model_clients  # noqa: F401
from app.llm.fake import FakeModelClient  # noqa: F401
from app.llm.models import (  # noqa: F401
    UnpricedModelError,
    model_price,
    priced_models,
    prices_for,
    require_price,
)
from app.llm.providers.openai import OpenAIAdapter  # noqa: F401
from app.secrets_service import MISSING_KEY  # noqa: F401

if TYPE_CHECKING:
    from app.config import AppConfig

# Old names → the unified interface.
DecisionClient = ModelClient
FakeDecisionClient = FakeModelClient
OpenAIDecisionClient = OpenAIAdapter


def make_decision_client(
    config: AppConfig, api_key: str | None = None
) -> ModelClient:
    """Simulated mode uses the fake; paper mode the provider dispatcher.

    ``api_key`` (the stored OpenAI key) is kept for old callers; a real
    dispatcher is usually built from the secrets store instead.
    """
    if config.env.mode == "simulated":
        return FakeModelClient()
    return ProviderClients(
        config, key_for=lambda provider: api_key if provider == "openai" else None
    )
