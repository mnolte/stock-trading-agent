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
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.config import AppConfig


@dataclass(frozen=True)
class ModelResponse:
    """One completed (or failed) model call."""

    raw: str | None
    model: str
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int
    error: str | None = None

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


def token_cost(response: ModelResponse, config: AppConfig) -> Decimal | None:
    """USD cost from the configured per-token prices; None without usage."""
    if response.tokens_in is None or response.tokens_out is None:
        return None
    in_price = Decimal(
        str(config.get("decision", "input_cost_per_mtok", default=0))
    )
    out_price = Decimal(
        str(config.get("decision", "output_cost_per_mtok", default=0))
    )
    return (
        Decimal(response.tokens_in) * in_price
        + Decimal(response.tokens_out) * out_price
    ) / Decimal(1_000_000)


class OpenAIDecisionClient:
    """gpt-4o-mini (or configured model) via the OpenAI SDK, JSON output."""

    def __init__(self, config: AppConfig) -> None:
        from openai import AsyncOpenAI

        self.model = str(config.get("decision", "model", default="gpt-4o-mini"))
        self.temperature = float(
            config.get("decision", "temperature", default=0)
        )
        timeout = float(config.get("decision", "timeout_seconds", default=60))
        self._client = AsyncOpenAI(
            api_key=config.env.openai_api_key, timeout=timeout
        )

    async def complete(self, *, system: str, user: str) -> ModelResponse:
        started = time.monotonic()
        try:
            completion = await self._client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            return ModelResponse(
                raw=None,
                model=self.model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=str(exc),
            )
        usage = completion.usage
        return ModelResponse(
            raw=completion.choices[0].message.content if completion.choices else None,
            model=self.model,
            tokens_in=usage.prompt_tokens if usage else None,
            tokens_out=usage.completion_tokens if usage else None,
            latency_ms=int((time.monotonic() - started) * 1000),
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
        if isinstance(override, dict):
            return ModelResponse(
                raw=None,
                model=self.model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=override.get("error", "fake error"),
            )
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


def make_decision_client(config: AppConfig) -> DecisionClient:
    """Simulated mode uses the fake; paper mode needs OPENAI_API_KEY."""
    if config.env.mode == "simulated":
        return FakeDecisionClient()
    if not config.env.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required unless MODE=simulated")
    return OpenAIDecisionClient(config)
