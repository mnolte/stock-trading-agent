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
    cached_tokens_in: int | None = None,
    cache_write_tokens_in: int | None = None,
) -> ModelResponse:
    return ModelResponse(
        raw=None if error else "{}",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=1,
        error=error,
        cached_tokens_in=cached_tokens_in,
        cache_write_tokens_in=cache_write_tokens_in,
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


def test_cached_read_prices_at_cached_input() -> None:
    """OpenAI reports cached tokens inside prompt_tokens: 2000 in with
    800 cached on gpt-4o-mini = 1200 x $0.15 + 800 x $0.075 + 500 x $0.60
    per MTok = $0.00054."""
    cost = token_cost(
        _response(cached_tokens_in=800), AppConfig()
    )
    assert cost == Decimal("0.00054")


def test_cache_write_prices_at_cache_write() -> None:
    """Anthropic sums its three buckets into tokens_in: 2000 in with
    500 cached reads and 200 cache writes on claude-haiku-5-5 =
    1300 x $0.10 + 500 x $0.01 + 200 x $0.125 + 400 x $0.50 = $0.00036."""
    cost = token_cost(
        _response(
            tokens_out=400,
            model="claude-haiku-5-5",
            cached_tokens_in=500,
            cache_write_tokens_in=200,
        ),
        AppConfig(),
    )
    assert cost == Decimal("0.00036")


def test_mixed_buckets_price_each_at_its_rate() -> None:
    """claude-sonnet-5-5: 3000 in = 2000 plain + 700 cached + 300
    written; 1000 out. (2000 x 2.00 + 700 x 0.20 + 300 x 2.50 +
    1000 x 10.00) / 1e6 = $0.01489."""
    cost = token_cost(
        _response(
            tokens_in=3000,
            tokens_out=1000,
            model="claude-sonnet-5-5",
            cached_tokens_in=700,
            cache_write_tokens_in=300,
        ),
        AppConfig(),
    )
    assert cost == Decimal("0.01489")


def test_missing_cached_prices_fall_back_to_input() -> None:
    """A model entry without cached_input/cache_write prices every
    input token at input, exactly like before."""
    config = AppConfig()
    config._active["models"]["gpt-4.1-mini"].pop("cached_input")
    config._active["models"]["claude-haiku-5-5"].pop("cached_input")
    config._active["models"]["claude-haiku-5-5"].pop("cache_write")
    # 1000 in, 400 cached, gpt-4.1-mini without cached_input:
    # 600 x 0.40 + 400 x 0.40 + 100 x 1.60 = $0.00056.
    assert token_cost(
        _response(
            tokens_in=1000,
            tokens_out=100,
            model="gpt-4.1-mini",
            cached_tokens_in=400,
        ),
        config,
    ) == Decimal("0.00056")
    # 1000 in, 300 written, haiku without either key: all at $0.10.
    assert token_cost(
        _response(
            tokens_in=1000,
            tokens_out=0,
            model="claude-haiku-5-5",
            cache_write_tokens_in=300,
        ),
        config,
    ) == Decimal("0.0001")


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
        "gpt-6.1-sol",
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
    assert any("not in the models list" in e for e in exc.value.errors)
    cleaned, _ = await validate_settings(
        {"decision": {"model": "gpt-4.1-mini"}},
        active,
        broker=None,  # type: ignore[arg-type]
        held_symbols=set(),
    )
    assert cleaned["decision"]["model"] == "gpt-4.1-mini"
