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

from dataclasses import dataclass, field
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


# ---------------------------------------------------------------------
# converse() — the multi-round tool call type for Ask the agent (PR 36)


@dataclass(frozen=True)
class ConverseTool:
    """One tool the model may call, described by a JSON schema."""

    name: str
    description: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """One requested tool call: provider id, tool name, parsed arguments."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConverseMessage:
    """A provider-agnostic chat turn.

    ``role`` is ``user``, ``assistant`` or ``tool``. An assistant turn may
    carry ``tool_calls``; a ``tool`` turn carries the result for one
    ``tool_call_id`` (``tool_name`` rides along for providers that want it).
    """

    role: str
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    tool_name: str = ""


@dataclass(frozen=True)
class ConverseResult:
    """One finished (or failed) converse round.

    ``tool_calls`` are the calls the model asked for this round — the
    caller runs them and appends their results as ``tool`` messages.
    ``cached_tokens_in`` counts input tokens the provider's prompt cache
    served (priced at ``models.<id>.cached_input``, else input);
    ``cache_write_tokens_in`` counts Anthropic cache-creation tokens.
    """

    text: str
    tool_calls: tuple[ToolCall, ...]
    model: str
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int
    cached_tokens_in: int | None = None
    cache_write_tokens_in: int | None = None
    error: str | None = None
    error_type: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    request_id: str | None = None
    refused: bool = False
    provider: str = "openai"

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls) and self.error is None


@dataclass(frozen=True)
class ConverseEvent:
    """One streamed item: a text ``delta`` or the final ``done`` result."""

    kind: str  # "delta" | "done"
    delta: str = ""
    result: ConverseResult | None = None


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

    def converse(
        self,
        *,
        system: str,
        messages: list[ConverseMessage],
        tools: list[ConverseTool],
        model: str | None = None,
        stream: bool = True,
    ) -> Any:
        """One converse round; yields ``delta`` events then one ``done``.

        An async iterator: adapters stream text deltas when ``stream`` is
        on, always finish with a ``done`` event carrying the
        :class:`ConverseResult` (``error`` set on failure — the iterator
        never raises for provider errors)."""
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
        Decimal(response.tokens_in) * in_price + Decimal(response.tokens_out) * out_price
    ) / Decimal(1_000_000)


def converse_cost(
    result: ConverseResult, config: AppConfig, *, model: str | None = None
) -> Decimal | None:
    """USD cost of one converse round; cached tokens at their own price.

    Uncached input is ``tokens_in − cached − cache_write``; cached reads
    price at ``models.<id>.cached_input`` (else the input price) and
    Anthropic cache writes at ``cache_write`` (else input). None without
    usage or a price, exactly like :func:`token_cost`.
    """
    if result.tokens_in is None or result.tokens_out is None:
        return None
    table = models_table(config)
    price = model_price(table, model or result.model)
    if price is None:
        return None
    in_price, out_price = price
    entry = table.get(model or result.model) or {}
    cached_price = Decimal(str(entry.get("cached_input", in_price)))
    write_price = Decimal(str(entry.get("cache_write", in_price)))
    cached = result.cached_tokens_in or 0
    written = result.cache_write_tokens_in or 0
    plain = max(result.tokens_in - cached - written, 0)
    return (
        Decimal(plain) * in_price
        + Decimal(cached) * cached_price
        + Decimal(written) * write_price
        + Decimal(result.tokens_out) * out_price
    ) / Decimal(1_000_000)
