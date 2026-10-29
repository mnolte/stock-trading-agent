"""The ``models`` config block — the only place that names models
(docs/SPEC.md → Model providers, Model list).

Each entry carries ``provider`` (``openai`` or ``anthropic``), prices in
USD per million tokens (``input``/``output``), a knowledge ``cutoff``
and, for Anthropic models, an ``effort``; ``timeout_seconds`` may
override ``decision.call_timeout_seconds`` per model. The legacy
``model_prices`` and ``backtest.model_cutoffs`` blocks are folded in for
one release, with a warning when ``models`` is missing entirely.

Every name a task can pick — ``decision.model``, ``reviews.models.*``,
strategies, backtests and replays — must resolve to an entry here; an
unknown name fails startup or the save with a clear message.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.config import AppConfig

logger = logging.getLogger(__name__)

PROVIDERS = ("openai", "anthropic")
EFFORTS = ("low", "medium", "high")
PROVIDER_LABELS = {"openai": "OpenAI", "anthropic": "Anthropic"}
# Secret name (the ``secrets`` group) holding each provider's key.
PROVIDER_KEY = {"openai": "openai", "anthropic": "anthropic"}
REVIEW_TASKS = ("weekly", "briefing", "verdict")

_legacy_warned = False


class UnknownModelError(ValueError):
    """A task named a model that has no entry in ``models``."""


def _legacy_table(config: AppConfig) -> dict[str, dict[str, Any]]:
    """Effective model table when ``models`` is absent (one release)."""
    global _legacy_warned
    prices = config.get("model_prices", default=None)
    cutoffs = config.get("backtest", "model_cutoffs", default=None)
    table: dict[str, dict[str, Any]] = {}
    if isinstance(prices, dict):
        for name, entry in prices.items():
            if isinstance(entry, dict):
                table[str(name)] = {**entry, "provider": "openai"}
    if isinstance(cutoffs, dict):
        for name, cutoff in cutoffs.items():
            if str(name) in table:
                table[str(name)]["cutoff"] = cutoff
    if table and not _legacy_warned:
        _legacy_warned = True
        logger.warning(
            "config has no 'models' block; reading legacy 'model_prices' "
            "and 'backtest.model_cutoffs' for one release (docs/SPEC.md → "
            "Model providers)"
        )
    return table


def models_table(config: AppConfig) -> dict[str, dict[str, Any]]:
    """The effective ``{model: {provider, input, output, …}}`` table."""
    models = config.get("models", default=None)
    if isinstance(models, dict) and models:
        return {str(k): v for k, v in models.items() if isinstance(v, dict)}
    return _legacy_table(config)


def model_entry(config: AppConfig, model: str) -> dict[str, Any] | None:
    return models_table(config).get(model)


def known_model(config: AppConfig, model: str) -> bool:
    return model_entry(config, model) is not None


def model_provider(config: AppConfig, model: str) -> str:
    """``openai`` or ``anthropic``; legacy entries read as OpenAI."""
    entry = model_entry(config, model) or {}
    provider = entry.get("provider")
    return str(provider) if provider in PROVIDERS else "openai"


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


def model_effort(config: AppConfig, model: str) -> str:
    """The Anthropic sampling effort; ``low`` when the entry says nothing."""
    entry = model_entry(config, model) or {}
    effort = str(entry.get("effort") or "low")
    return effort if effort in EFFORTS else "low"


def model_timeout_seconds(config: AppConfig, model: str) -> float:
    """The per-model ``timeout_seconds`` else the shared call timeout."""
    entry = model_entry(config, model) or {}
    raw = entry.get("timeout_seconds")
    if raw is not None:
        return float(raw)
    return float(config.get("decision", "call_timeout_seconds", default=30))


def model_cutoff(config: AppConfig, model: str) -> date | None:
    """The model's reliable knowledge cutoff, for the backtest check."""
    entry = model_entry(config, model) or {}
    raw = entry.get("cutoff")
    if raw is None:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def review_model(config: AppConfig, kind: str) -> str:
    """The model configured for one review task — ``reviews.models`` is
    one per task; a legacy scalar ``reviews.model`` is honoured for one
    release (docs/SPEC.md → Model providers)."""
    models = config.get("reviews", "models", default=None)
    if isinstance(models, dict) and models.get(kind):
        return str(models[kind])
    return str(config.get("reviews", "model", default="gpt-4o-mini"))


def named_models(config: AppConfig, session: Session | None = None) -> list[tuple[str, str]]:
    """Every (what, model) the app is configured to call — live decisions,
    the three review tasks and every non-archived llm strategy."""
    out: list[tuple[str, str]] = [
        ("Live decisions", str(config.get("decision", "model", default="")))
    ]
    review_models = config.get("reviews", "models", default=None)
    if isinstance(review_models, dict) and review_models:
        for task in REVIEW_TASKS:
            if review_models.get(task):
                out.append((f"Reviews ({task})", str(review_models[task])))
    else:
        out.append(("Reviews", str(config.get("reviews", "model", default=""))))
    if session is not None:
        from sqlalchemy import select

        from app.db.models import Strategy

        for strategy in session.execute(
            select(Strategy).where(
                Strategy.status == "active", Strategy.kind == "llm"
            )
        ).scalars():
            out.append((f"Shadow {strategy.name!r}", str(strategy.model)))
    return out


def check_models(config: AppConfig, session: Session | None = None) -> None:
    """Validate the ``models`` block and every name that refers to it.

    Raises :class:`UnknownModelError`/`ValueError` with a clear message;
    the startup wrapper turns it into a refusal.
    """
    table = models_table(config)
    for name, entry in table.items():
        if str(entry.get("provider")) not in PROVIDERS:
            raise ValueError(
                f"models.{name}: provider must be one of {', '.join(PROVIDERS)}"
            )
        if entry.get("input") is None or entry.get("output") is None:
            raise ValueError(
                f"models.{name}: input and output prices are required"
            )
        if entry.get("effort") is not None and str(entry["effort"]) not in EFFORTS:
            raise ValueError(
                f"models.{name}: effort must be one of {', '.join(EFFORTS)}"
            )
        if entry.get("cutoff") is not None:
            try:
                date.fromisoformat(str(entry["cutoff"]))
            except ValueError:
                raise ValueError(
                    f"models.{name}: cutoff must be an ISO date"
                ) from None
    for what, model in named_models(config, session):
        if not known_model(config, model):
            raise UnknownModelError(
                f"{what} model {model!r} is not in the models list"
            )


def check_models_at_startup(config: AppConfig, session: Session) -> None:
    """Refuse to start when a configured model is unknown or misconfigured."""
    check_models(config, session)


def model_price(
    prices: dict[str, Any] | None, model: str
) -> tuple[Decimal, Decimal] | None:
    """(input, output) USD per million tokens for ``model``, or None."""
    entry = (prices or {}).get(model)
    if not isinstance(entry, dict):
        return None
    try:
        return Decimal(str(entry["input"])), Decimal(str(entry["output"]))
    except (KeyError, ArithmeticError, ValueError):
        return None


class UnpricedModelError(RuntimeError):
    """A model is configured for use but has no entry in ``models``."""


def prices_for(config: AppConfig) -> dict[str, Any]:
    """The effective ``{model: {input, output, …}}`` table (price source)."""
    return models_table(config)


def priced_models(config: AppConfig) -> list[str]:
    """Every model with a complete price — the only ones that may be picked."""
    prices = models_table(config)
    return sorted(m for m in prices if model_price(prices, m) is not None)


def require_price(config: AppConfig, model: str, what: str) -> None:
    """Raise when ``model`` (used for ``what``) has no price."""
    if model_price(models_table(config), model) is None:
        raise UnpricedModelError(f"{what} model {model!r} has no price in models")
