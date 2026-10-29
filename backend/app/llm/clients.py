"""The provider dispatcher (docs/SPEC.md → Model providers).

Callers hold one :class:`ProviderClients` and every call routes to the
adapter for the requested model's provider — live decisions, shadows,
reviews, replays and backtests never branch on the provider themselves
(hard rule 21). Keys come from the secrets store per provider; a saved
key rebuilds its adapter in place.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.llm.base import ModelClient, ModelResponse
from app.llm.fake import FakeModelClient
from app.llm.models import PROVIDER_KEY, model_provider
from app.secrets_service import MISSING_KEY

if TYPE_CHECKING:
    from app.config import AppConfig

logger = logging.getLogger(__name__)

KeyFor = Callable[[str], "str | None"]


def make_adapter(
    provider: str, config: AppConfig, api_key: str
) -> ModelClient:
    """The one adapter for a provider; lazily imports its SDK module."""
    if provider == "anthropic":
        from app.llm.providers.anthropic import AnthropicAdapter

        return AnthropicAdapter(config, api_key=api_key)
    from app.llm.providers.openai import OpenAIAdapter

    return OpenAIAdapter(config, api_key=api_key)


class ProviderClients:
    """Routes each call to the adapter for the model's provider.

    Implements the :class:`ModelClient` contract, so every existing
    call site keeps working — ``model`` picks the provider per call.
    """

    provider = "multi"

    def __init__(
        self,
        config: AppConfig,
        key_for: KeyFor,
        default_model: str | None = None,
    ) -> None:
        self._config = config
        self._key_for = key_for
        self.default_model = default_model or str(
            config.get("decision", "model", default="gpt-4o-mini")
        )
        self.model = self.default_model
        self._adapters: dict[str, ModelClient] = {}

    def _adapter(self, provider: str) -> ModelClient:
        adapter = self._adapters.get(provider)
        if adapter is None:
            adapter = make_adapter(
                provider,
                self._config,
                self._key_for(provider) or MISSING_KEY,
            )
            self._adapters[provider] = adapter
        return adapter

    def update_key(self, provider: str, api_key: str) -> None:
        """Swap one provider's key in place after a Settings save."""
        adapter = self._adapters.get(provider)
        if adapter is not None:
            update = getattr(adapter, "update_key", None)
            if callable(update):
                update(api_key)
            else:
                self._adapters.pop(provider, None)

    def drop(self, provider: str) -> None:
        """Forget a provider's adapter (its key was removed)."""
        self._adapters.pop(provider, None)

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
        adapter = self._adapter(model_provider(self._config, use_model))
        return await adapter.complete(
            system=system,
            user=user,
            schema=schema,
            model=use_model,
            temperature=temperature,
        )


def secrets_key_for(secrets: Any) -> KeyFor:
    """``provider → stored key`` from a SecretsStore (None = not set)."""

    def get(provider: str) -> str | None:
        name = PROVIDER_KEY.get(provider)
        if secrets is None or name is None:
            return None
        return secrets.get(name)

    return get


def make_model_clients(
    config: AppConfig,
    secrets: Any = None,
    default_model: str | None = None,
) -> ModelClient:
    """Simulated mode uses the fake; paper mode the dispatcher.

    A missing key is no crash — the adapter exists and its calls fail as
    ordinary model errors until a key is saved in Settings.
    """
    if config.env.mode == "simulated":
        return FakeModelClient(config=config)
    return ProviderClients(
        config, key_for=secrets_key_for(secrets), default_model=default_model
    )
