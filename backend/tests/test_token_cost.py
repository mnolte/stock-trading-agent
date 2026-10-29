"""token_cost: USD cost from the ``model_prices`` config block.

``model_prices`` is the only price source (docs/SPEC.md, Strategies and
shadows — Cost). Every call is priced by the model it was made for; an
unpriced model records no cost (never a silent $0) and can't be selected.
"""

from decimal import Decimal

import pytest
from app.config import AppConfig
from app.decide.client import (
    ModelResponse,
    UnpricedModelError,
    model_price,
    priced_models,
    require_price,
    token_cost,
)
from app.settings_service import SettingsError, validate_settings

pytestmark = pytest.mark.anyio


def _response(
    tokens_in: int | None = 2000,
    tokens_out: int | None = 500,
    error: str | None = None,
    model: str = "gpt-4o-mini",
) -> ModelResponse:
    return ModelResponse(
        raw=None if error else "{}",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=1,
        error=error,
    )


def test_cost_uses_model_prices() -> None:
    """2000 in x $0.15/MTok + 500 out x $0.60/MTok = $0.0006."""
    assert token_cost(_response(), AppConfig()) == Decimal("0.0006")


def test_other_model_uses_its_own_price() -> None:
    """gpt-4.1-mini: 2000 x $0.40 + 500 x $1.60 per MTok = $0.0016."""
    cost = token_cost(_response(model="gpt-4.1-mini"), AppConfig())
    assert cost == Decimal("0.0016")


def test_configured_model_overrides_reported_name() -> None:
    """The fake client reports its own name; the configured model prices it."""
    cost = token_cost(_response(model="fake-decision"), AppConfig(), model="gpt-4o-mini")
    assert cost == Decimal("0.0006")


def test_old_price_keys_are_gone() -> None:
    config = AppConfig()
    assert config.get("decision", "input_cost_per_mtok") is None
    assert config.get("reviews", "output_cost_per_mtok") is None


def test_unpriced_model_records_no_cost() -> None:
    """Never a silent $0 for a model the table doesn't know."""
    assert token_cost(_response(model="gpt-9"), AppConfig()) is None


def test_cost_none_without_usage() -> None:
    assert token_cost(_response(None, None, error="boom"), AppConfig()) is None


def test_cost_none_with_partial_usage() -> None:
    assert token_cost(_response(None, 500), AppConfig()) is None
    assert token_cost(_response(2000, None), AppConfig()) is None


def test_cost_zero_usage_is_zero() -> None:
    assert token_cost(_response(0, 0), AppConfig()) == Decimal("0")


def test_price_change_never_rewrites_stored_cost() -> None:
    """A cost is computed once at call time; a later price edit only
    affects later calls."""
    config = AppConfig()
    stored = token_cost(_response(), config)
    config._active["models"]["gpt-4o-mini"]["input"] = 1.0
    config._active["models"]["gpt-4o-mini"]["output"] = 1.0
    assert stored == Decimal("0.0006")
    assert token_cost(_response(), config) == Decimal("0.0025")


def test_priced_models_and_require_price() -> None:
    config = AppConfig()
    assert priced_models(config) == [
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "gpt-4.1-mini",
        "gpt-4o-mini",
    ]
    require_price(config, "gpt-4o-mini", "Live")
    with pytest.raises(UnpricedModelError, match="gpt-9"):
        require_price(config, "gpt-9", "Live")
    assert model_price({"m": {"input": 1}}, "m") is None  # incomplete entry


async def test_settings_refuse_unpriced_model() -> None:
    active = AppConfig().effective()
    with pytest.raises(SettingsError) as exc:
        await validate_settings(
            {"decision": {"model": "gpt-9"}},
            active,
            broker=None,  # type: ignore[arg-type]
            held_symbols=set(),
        )
    assert any("no price" in e for e in exc.value.errors)
    cleaned, _ = await validate_settings(
        {"decision": {"model": "gpt-4.1-mini"}},
        active,
        broker=None,  # type: ignore[arg-type]
        held_symbols=set(),
    )
    assert cleaned["decision"]["model"] == "gpt-4.1-mini"
