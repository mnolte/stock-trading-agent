"""The one model client interface (docs/SPEC.md → Model providers).

Engine code sends a prompt (``system``), an input snapshot (``user``),
a JSON schema and the model to call; it gets back a :class:`ModelResponse`
with the final answer text, the parsed JSON, token counts, latency and
the provider's request id — or a typed error (``timeout``, ``api_error``,
``invalid_output``, ``refusal``). A call is tried once and never
retried; decision, review, shadow, replay and backtest code all go
through this interface and never branch on the provider (hard rule 21).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from app.llm.models import model_price, models_table

if TYPE_CHECKING:
    from app.config import AppConfig

# Typed call errors (docs/SPEC.md → Model providers, LLM call history).
ERROR_TYPES = ("timeout", "api_error", "invalid_output", "refusal")


@dataclass(frozen=True)
class ModelResponse:
    """One completed (or failed) model call."""

    raw: str | None
    model: str
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int
    error: str | None = None
    # Failure classification for the call-history row: "timeout" or
    # "api_error" (HTTP error or connection failure, with the HTTP status
    # and the provider's error type when it sent them), "invalid_output"
    # (unparseable or truncated) or "refusal".
    error_type: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    request_id: str | None = None
    # The structured-output refusal was set (or Anthropic's stop reason
    # was refusal) — no usable response.
    refused: bool = False
    provider: str = "openai"
    # json.loads(raw) when the answer parsed; None otherwise.
    parsed: Any = None

    @property
    def tokens(self) -> int | None:
        if self.tokens_in is None or self.tokens_out is None:
            return None
        return self.tokens_in + self.tokens_out


class ModelClient(Protocol):
    """Sends one prompt and returns the answer (or a typed error)."""

    provider: str

    async def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        """Complete one call for ``model``; ``error`` is set on failure."""
        ...


def token_cost(
    response: ModelResponse, config: AppConfig, *, model: str | None = None
) -> Decimal | None:
    """USD cost of one call from the ``models`` list; None without usage.

    ``model`` is the configured model the call was made for (the fake
    clients report their own name). The cost is stored on the row when
    the call is made, so a later price change never rewrites past costs.
    Startup refuses unpriced models, so a missing price here means the
    config changed underneath — no cost is recorded rather than a
    silent $0.
    """
    if response.tokens_in is None or response.tokens_out is None:
        return None
    price = model_price(models_table(config), model or response.model)
    if price is None:
        return None
    in_price, out_price = price
    return (
        Decimal(response.tokens_in) * in_price
        + Decimal(response.tokens_out) * out_price
    ) / Decimal(1_000_000)
