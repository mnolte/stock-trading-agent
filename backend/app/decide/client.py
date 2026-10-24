"""Model client (docs/SPEC.md, Decision layer).

One call per candidate or held symbol: the system prompt is the versioned
prompt file; the user message is the input snapshot JSON. The client sits
behind a protocol so tests and simulated mode never touch the network. A
failed call is returned as an error — it is never retried with a different
model silently, and never retried automatically at all in this layer.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, cast

from app.decide.output import DECISION_JSON_SCHEMA

if TYPE_CHECKING:
    from app.config import AppConfig

# Placeholder used when no OpenAI key is stored yet (same constant the
# secrets store uses for unset keys).
from app.secrets_service import MISSING_KEY


@dataclass(frozen=True)
class ModelResponse:
    """One completed (or failed) model call."""

    raw: str | None
    model: str
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int
    error: str | None = None
    # Failure classification for the call-history row (docs/SPEC.md, LLM
    # call history): "timeout" or "api_error" (HTTP error or connection
    # failure), with the HTTP status and OpenAI's error code when it sent
    # them and the x-request-id response header.
    error_type: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    request_id: str | None = None
    # The structured-output refusal field was set — no usable response.
    refused: bool = False

    @property
    def tokens(self) -> int | None:
        if self.tokens_in is None or self.tokens_out is None:
            return None
        return self.tokens_in + self.tokens_out


class DecisionClient(Protocol):
    """Sends one prompt and returns the raw completion."""

    async def complete(self, *, system: str, user: str) -> ModelResponse:
        """Complete one decision call; ``error`` is set on failure."""
        ...


class UnpricedModelError(RuntimeError):
    """A model is configured for use but has no entry in ``model_prices``."""


def model_price(
    prices: dict[str, Any] | None, model: str
) -> tuple[Decimal, Decimal] | None:
    """(input, output) USD per million tokens for ``model``, or None.

    ``model_prices`` in config.yaml is the only price source (docs/SPEC.md,
    Strategies and shadows — Cost)."""
    entry = (prices or {}).get(model)
    if not isinstance(entry, dict):
        return None
    try:
        return Decimal(str(entry["input"])), Decimal(str(entry["output"]))
    except (KeyError, ArithmeticError, ValueError):
        return None


def priced_models(config: AppConfig) -> list[str]:
    """Every model with a complete price — the only ones that may be picked."""
    prices = config.get("model_prices", default={}) or {}
    return sorted(m for m in prices if model_price(prices, m) is not None)


def require_price(config: AppConfig, model: str, what: str) -> None:
    """Raise when ``model`` (used for ``what``) has no price."""
    if model_price(config.get("model_prices", default={}), model) is None:
        raise UnpricedModelError(
            f"{what} model {model!r} has no price in model_prices"
        )


def _classify_error(exc: Exception) -> str:
    """"timeout" or "api_error" for the llm_calls row (docs/SPEC.md)."""
    from openai import APITimeoutError

    if isinstance(exc, APITimeoutError | TimeoutError):
        return "timeout"
    return "api_error"


def token_cost(
    response: ModelResponse, config: AppConfig, *, model: str | None = None
) -> Decimal | None:
    """USD cost of one call from ``model_prices``; None without usage.

    ``model`` is the configured model the call was made for (the fake
    clients report their own name); it defaults to ``response.model``.
    The cost is stored on the row when the call is made, so a later price
    change never rewrites past costs. Startup refuses unpriced models, so
    a missing price here means the config changed underneath — no cost is
    recorded rather than a silent $0.
    """
    if response.tokens_in is None or response.tokens_out is None:
        return None
    price = model_price(
        config.get("model_prices", default={}), model or response.model
    )
    if price is None:
        return None
    in_price, out_price = price
    return (
        Decimal(response.tokens_in) * in_price
        + Decimal(response.tokens_out) * out_price
    ) / Decimal(1_000_000)


class OpenAIDecisionClient:
    """gpt-4o-mini (or configured model) via the OpenAI SDK, strict JSON schema."""

    def __init__(self, config: AppConfig, api_key: str) -> None:
        self.model = str(config.get("decision", "model", default="gpt-4o-mini"))
        self.temperature = float(
            config.get("decision", "temperature", default=0)
        )
        self._timeout = float(
            config.get("decision", "call_timeout_seconds", default=30)
        )
        self._build(api_key)

    def _build(self, api_key: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key, timeout=self._timeout, max_retries=0
        )

    def update_key(self, api_key: str) -> None:
        """Swap the key in place after a Settings save."""
        self._build(api_key)

    async def complete(self, *, system: str, user: str) -> ModelResponse:
        from openai.types.chat.completion_create_params import ResponseFormat

        started = time.monotonic()
        try:
            response = await self._client.chat.completions.with_raw_response.create(
                model=self.model,
                temperature=self.temperature,
                response_format=cast(
                    ResponseFormat,
                    {
                        "type": "json_schema",
                        "json_schema": DECISION_JSON_SCHEMA,
                    },
                ),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            request_id = response.headers.get("x-request-id")
            completion = response.parse()
        except Exception as exc:  # noqa: BLE001
            kind = _classify_error(exc)
            return ModelResponse(
                raw=None,
                model=self.model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=str(exc),
                error_type=kind,
                http_status=getattr(exc, "status_code", None),
                error_code=getattr(exc, "code", None),
                request_id=getattr(exc, "request_id", None),
            )
        usage = completion.usage
        message = completion.choices[0].message if completion.choices else None
        return ModelResponse(
            raw=message.content if message else None,
            model=self.model,
            tokens_in=usage.prompt_tokens if usage else None,
            tokens_out=usage.completion_tokens if usage else None,
            latency_ms=int((time.monotonic() - started) * 1000),
            request_id=request_id,
            refused=bool(getattr(message, "refusal", None)),
        )


class FakeDecisionClient:
    """Deterministic model for tests and simulated mode.

    ``responses`` maps ``(symbol, context)`` or a bare symbol to a raw reply
    string (or an ``{"error": ...}`` dict for a failed call). Without an
    override the fake answers from the input snapshot: a valid ``enter`` for
    symbols in ``enter_symbols`` (stop 2 × ATR below price, target at 2R),
    ``skip`` otherwise, and ``hold`` for held positions.
    """

    def __init__(
        self,
        responses: dict[str | tuple[str, str], str | dict[str, str]] | None = None,
        enter_symbols: set[str] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.enter_symbols = enter_symbols or {"MSFT"}
        self.model = "fake-decision"

    def _default(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        symbol = snapshot["symbol"]
        if snapshot["context"] == "held":
            return {
                "symbol": symbol,
                "action": "hold",
                "confidence": 0.55,
                "stop_price": None,
                "target_price": None,
                "thesis": f"{symbol}: no change; the entry thesis still holds.",
                "invalidation": None,
                "risks": [],
                "news_used": [],
            }
        price = snapshot.get("price")
        atr = snapshot.get("indicators_1h", {}).get("atr14")
        if (
            symbol in self.enter_symbols
            and isinstance(price, int | float)
            and isinstance(atr, int | float)
            and atr > 0
        ):
            stop = round(price - 2 * atr, 4)
            target = round(price + 2 * (price - stop), 4)
            return {
                "symbol": symbol,
                "action": "enter",
                "confidence": 0.62,
                "stop_price": stop,
                "target_price": target,
                "thesis": f"{symbol}: trend continuation above session VWAP.",
                "invalidation": f"Hourly close below {stop}.",
                "risks": ["Simulated decision"],
                "news_used": [],
            }
        return {
            "symbol": symbol,
            "action": "skip",
            "confidence": 0.4,
            "stop_price": None,
            "target_price": None,
            "thesis": f"{symbol}: signals conflict; waiting for a cleaner setup.",
            "invalidation": None,
            "risks": [],
            "news_used": [],
        }

    async def complete(self, *, system: str, user: str) -> ModelResponse:
        started = time.monotonic()
        snapshot = json.loads(user)
        override = self.responses.get(
            (snapshot["symbol"], snapshot["context"]), self.responses.get(snapshot["symbol"])
        )
        if isinstance(override, dict) and "raw" not in override:
            # Tests exercise the error paths (a timeout, an HTTP 503, a
            # refusal) by adding error_type/http_status/request_id/refused.
            refused = bool(override.get("refused"))
            status = override.get("http_status")
            return ModelResponse(
                raw=None,
                model=self.model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=int(override.get("latency_ms")
                               or (time.monotonic() - started) * 1000),
                error=None if refused else override.get("error", "fake error"),
                error_type=override.get("error_type"),
                http_status=int(status) if status is not None else None,
                error_code=override.get("error_code"),
                request_id=override.get("request_id"),
                refused=refused,
            )
        if isinstance(override, dict):
            override = str(override["raw"])
        raw = override if isinstance(override, str) else json.dumps(self._default(snapshot))
        # Deterministic pseudo-usage so cost tracking is exercised end to end.
        tokens_in = len(system + user) // 4
        tokens_out = len(raw) // 4
        return ModelResponse(
            raw=raw,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=int((time.monotonic() - started) * 1000) or 1,
        )


def make_decision_client(
    config: AppConfig, api_key: str | None = None
) -> DecisionClient:
    """Simulated mode uses the fake. In paper mode a missing key is no
    crash — the client exists and its calls fail as ordinary model errors
    until a key is saved in Settings."""
    if config.env.mode == "simulated":
        return FakeDecisionClient()
    return OpenAIDecisionClient(config, api_key=api_key or MISSING_KEY)
