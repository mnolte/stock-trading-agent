"""Model client compatibility (PR 29): the implementation moved to
``app/llm`` — one interface, one adapter per provider, a fake for tests
(docs/SPEC.md → Model providers). The names below keep existing callers
and tests working unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.llm.base import (  # noqa: F401
    ModelClient as ModelClient,
)
from app.llm.base import (
    ModelResponse as ModelResponse,
)
from app.llm.base import (
    token_cost as token_cost,
)
from app.llm.clients import (  # noqa: F401
    ProviderClients as ProviderClients,
)
from app.llm.clients import (
    make_model_clients as make_model_clients,
)
from app.llm.fake import FakeModelClient as FakeModelClient  # noqa: F401
from app.llm.models import (  # noqa: F401
    UnpricedModelError as UnpricedModelError,
)
from app.llm.models import (
    model_price as model_price,
)
from app.llm.models import (
    priced_models as priced_models,
)
from app.llm.models import (
    prices_for as prices_for,
)
from app.llm.models import (
    require_price as require_price,
)
from app.llm.providers.openai import OpenAIAdapter as OpenAIAdapter  # noqa: F401
from app.secrets_service import MISSING_KEY as MISSING_KEY  # noqa: F401

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
