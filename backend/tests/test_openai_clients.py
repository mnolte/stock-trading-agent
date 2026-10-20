"""The OpenAI SDK defaults to 2 retries; the spec says no retry. Both real
clients must pin ``max_retries=0`` — including after a key swap."""

from decimal import Decimal

import pytest
from app.config import AppConfig
from app.decide.client import ModelResponse, OpenAIDecisionClient, token_cost
from app.reviews.client import OpenAIReviewClient

pytestmark = pytest.mark.anyio


class _Recorder:
    """Captures AsyncOpenAI construction kwargs without a real client."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _record(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def factory(**kwargs: object) -> _Recorder:
        calls.append(kwargs)
        return _Recorder(**kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", factory)
    return calls


def test_decision_client_no_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record(monkeypatch)
    config = AppConfig()

    client = OpenAIDecisionClient(config, api_key="sk-test")

    assert calls[-1]["max_retries"] == 0
    assert calls[-1]["timeout"] == float(
        config.get("decision", "call_timeout_seconds", default=30)
    )
    assert calls[-1]["api_key"] == "sk-test"

    client.update_key("sk-new")
    assert calls[-1]["max_retries"] == 0


def test_token_cost_uses_configured_prices() -> None:
    """Cost = tokens x configured price per million tokens (USD)."""
    config = AppConfig()
    response = ModelResponse(
        raw="{}", model="gpt-4o-mini",
        tokens_in=2000, tokens_out=500, latency_ms=1,
    )
    in_price = Decimal(str(config.get("decision", "input_cost_per_mtok")))
    out_price = Decimal(str(config.get("decision", "output_cost_per_mtok")))
    expected = (2000 * in_price + 500 * out_price) / 1_000_000
    cost = token_cost(response, config)
    assert cost == expected
    assert cost is not None and cost > 0


def test_token_cost_none_without_usage() -> None:
    """A failed call reports no usage, so no cost."""
    response = ModelResponse(
        raw=None, model="gpt-4o-mini",
        tokens_in=None, tokens_out=None, latency_ms=1, error="boom",
    )
    assert token_cost(response, AppConfig()) is None


def test_review_client_no_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record(monkeypatch)
    config = AppConfig()

    client = OpenAIReviewClient(config, api_key="sk-test")

    assert calls[-1]["max_retries"] == 0
    assert calls[-1]["timeout"] == float(
        config.get("decision", "call_timeout_seconds", default=30)
    )

    client.update_key("sk-new")
    assert calls[-1]["max_retries"] == 0
