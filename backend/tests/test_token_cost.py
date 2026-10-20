"""token_cost: USD cost from configured per-million-token prices.

Decision and review calls each use their own section of the config;
a config that lacks the keys falls back to gpt-4o-mini prices instead
of silently recording $0.
"""

from decimal import Decimal

import pytest
from app.config import AppConfig
from app.decide.client import ModelResponse, token_cost

pytestmark = pytest.mark.anyio


def _response(
    tokens_in: int | None = 2000,
    tokens_out: int | None = 500,
    error: str | None = None,
) -> ModelResponse:
    return ModelResponse(
        raw=None if error else "{}",
        model="gpt-4o-mini",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=1,
        error=error,
    )


def test_decision_cost_uses_decision_prices() -> None:
    """2000 in x $0.15/MTok + 500 out x $0.60/MTok = $0.0006."""
    cost = token_cost(_response(), AppConfig())
    assert cost == Decimal("0.0006")


def test_review_cost_uses_reviews_prices() -> None:
    """Review calls read the reviews section, not the decision section."""
    cost = token_cost(_response(), AppConfig(), section="reviews")
    assert cost == Decimal("0.0006")


def test_cost_none_without_usage() -> None:
    """A failed call reports no usage, so no cost."""
    assert token_cost(_response(None, None, error="boom"), AppConfig()) is None


def test_cost_none_with_partial_usage() -> None:
    """Missing either token count means no cost."""
    assert token_cost(_response(None, 500), AppConfig()) is None
    assert token_cost(_response(2000, None), AppConfig()) is None


def test_cost_zero_usage_is_zero() -> None:
    """Zero tokens is a real $0 cost, distinct from no usage."""
    assert token_cost(_response(0, 0), AppConfig()) == Decimal("0")


def test_missing_price_keys_fall_back_to_gpt4o_mini() -> None:
    """An old config.yaml without the price keys must not record $0."""
    config = AppConfig()
    config._active.pop("decision", None)
    assert token_cost(_response(), config) == Decimal("0.0006")
