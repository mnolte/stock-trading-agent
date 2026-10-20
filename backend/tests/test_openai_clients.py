"""The OpenAI SDK defaults to 2 retries; the spec says no retry. Both real
clients must pin ``max_retries=0`` — including after a key swap."""

import pytest
from app.config import AppConfig
from app.decide.client import OpenAIDecisionClient
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
