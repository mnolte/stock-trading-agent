"""The OpenAI adapter (docs/SPEC.md → Model providers).

The OpenAI Python SDK with strict structured outputs and the task's
temperature (``decision.temperature`` = 0, ``reviews.temperature`` = 0.2).
The prompt file is the system message, the input snapshot the user
message; one try, never retried. The only module outside this one that
may touch the ``openai`` package is a test that checks this boundary
(hard rule 21).
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from app.decide.output import DECISION_JSON_SCHEMA
from app.llm.base import (
    ConverseEvent,
    ConverseMessage,
    ConverseResult,
    ConverseTool,
    ModelResponse,
    ToolCall,
)
from app.llm.models import model_timeout_seconds

if TYPE_CHECKING:
    from app.config import AppConfig

logger = logging.getLogger(__name__)


def _classify_error(exc: Exception) -> str:
    """ "timeout" or "api_error" for the llm_calls row (docs/SPEC.md)."""
    from openai import APITimeoutError

    if isinstance(exc, APITimeoutError | TimeoutError):
        return "timeout"
    return "api_error"


class OpenAIAdapter:
    """Any model whose provider is ``openai``, via the OpenAI SDK."""

    provider = "openai"

    def __init__(
        self,
        config: AppConfig,
        api_key: str,
        default_model: str | None = None,
    ) -> None:
        self._config = config
        self.default_model = default_model or str(
            config.get("decision", "model", default="gpt-4o-mini")
        )
        self.model = self.default_model
        self.temperature = float(config.get("decision", "temperature", default=0))
        self._build(api_key)

    def _build(self, api_key: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=model_timeout_seconds(self._config, self.default_model),
            max_retries=0,
        )

    def update_key(self, api_key: str) -> None:
        """Swap the key in place after a Settings save."""
        self._build(api_key)

    def _temperature_kwargs(self, model: str, temperature: float) -> dict[str, Any]:
        if self._config.get("models", model, "supports_temperature", default=True) is False:
            return {}
        return {"temperature": temperature}

    async def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        from openai.types.chat.completion_create_params import ResponseFormat

        use_model = model or self.default_model
        use_temperature = float(temperature) if temperature is not None else self.temperature
        timeout = model_timeout_seconds(self._config, use_model)
        started = time.monotonic()
        try:
            response = await self._client.chat.completions.with_raw_response.create(
                model=use_model,
                **self._temperature_kwargs(use_model, use_temperature),
                response_format=cast(
                    ResponseFormat,
                    {
                        "type": "json_schema",
                        "json_schema": schema or DECISION_JSON_SCHEMA,
                    },
                ),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                timeout=timeout,
            )
            request_id = response.headers.get("x-request-id")
            completion = response.parse()
        except Exception as exc:  # noqa: BLE001
            kind = _classify_error(exc)
            return ModelResponse(
                raw=None,
                model=use_model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=str(exc),
                error_type=kind,
                http_status=getattr(exc, "status_code", None),
                error_code=getattr(exc, "code", None),
                request_id=getattr(exc, "request_id", None),
                provider=self.provider,
            )
        usage = completion.usage
        # OpenAI's automatic prompt caching: cached_tokens is a subset of
        # prompt_tokens, priced at the model's cached_input rate.
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details else None
        message = completion.choices[0].message if completion.choices else None
        finish = (
            getattr(completion.choices[0], "finish_reason", None)
            if completion.choices
            else None
        )
        raw = message.content if message else None
        parsed: Any = None
        error: str | None = None
        error_type: str | None = None
        if finish == "length":
            # A truncated answer is not a valid decision — flag it like
            # Anthropic's max_tokens instead of passing half-JSON on.
            error = "truncated"
            error_type = "invalid_output"
            raw = ""
        elif raw is not None:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                error = "not JSON"
                error_type = "invalid_output"
        return ModelResponse(
            raw=raw,
            model=use_model,
            tokens_in=usage.prompt_tokens if usage else None,
            tokens_out=usage.completion_tokens if usage else None,
            cached_tokens_in=cached,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=error,
            error_type=error_type,
            request_id=request_id,
            refused=bool(getattr(message, "refusal", None)),
            provider=self.provider,
            parsed=parsed,
        )

    async def _converse_responses(
        self,
        *,
        system: str,
        messages: list[ConverseMessage],
        tools: list[ConverseTool],
        model: str,
        stream: bool,
    ) -> Any:
        """One converse round via the Responses API (function tools).

        Required for models whose tool calls are not served by chat
        completions (gpt-6.1-sol). Same event contract as converse().
        """
        timeout = model_timeout_seconds(self._config, model)
        started = time.monotonic()
        wire_tools = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.schema,
            }
            for t in tools
        ]
        try:
            response_stream = await self._client.responses.create(
                model=model,
                instructions=system,
                input=cast(Any, _responses_input(messages)),
                tools=cast(Any, wire_tools) if wire_tools else cast(Any, None),
                stream=True,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            yield ConverseEvent(
                kind="done",
                result=_converse_error(model, exc, started, self.provider),
            )
            return
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        usage: Any = None
        truncated = False
        try:
            async for event in response_stream:
                etype = getattr(event, "type", "")
                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        text_parts.append(delta)
                        if stream:
                            yield ConverseEvent(kind="delta", delta=delta)
                elif etype == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if getattr(item, "type", "") == "function_call":
                        try:
                            args = json.loads(
                                getattr(item, "arguments", "") or "{}"
                            )
                        except json.JSONDecodeError:
                            args = {}
                        calls.append(
                            ToolCall(
                                id=getattr(item, "call_id", "") or "",
                                name=getattr(item, "name", "") or "",
                                arguments=args
                                if isinstance(args, dict)
                                else {},
                            )
                        )
                elif etype == "response.completed":
                    usage = getattr(
                        getattr(event, "response", None), "usage", None
                    )
                elif etype == "response.incomplete":
                    truncated = True
                    usage = getattr(
                        getattr(event, "response", None), "usage", None
                    )
        except Exception as exc:  # noqa: BLE001
            yield ConverseEvent(
                kind="done",
                result=_converse_error(model, exc, started, self.provider),
            )
            return
        details = getattr(usage, "input_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details else None
        yield ConverseEvent(
            kind="done",
            result=ConverseResult(
                text="".join(text_parts),
                tool_calls=tuple(calls),
                model=model,
                tokens_in=getattr(usage, "input_tokens", None),
                tokens_out=getattr(usage, "output_tokens", None),
                cached_tokens_in=cached,
                latency_ms=int((time.monotonic() - started) * 1000),
                error="truncated" if truncated else None,
                error_type="invalid_output" if truncated else None,
                provider=self.provider,
            ),
        )

    async def converse(
        self,
        *,
        system: str,
        messages: list[ConverseMessage],
        tools: list[ConverseTool],
        model: str | None = None,
        stream: bool = True,
    ) -> Any:
        """One converse round via chat completions with tools (PR 36).

        Yields ``delta`` events for streamed text and finishes with a
        ``done`` event carrying the ConverseResult (tool calls included).
        OpenAI's automatic prompt caching applies to the stable prefix —
        no explicit marker is sent. One try, never retried.

        Models flagged ``api: responses`` in the models list (OpenAI
        requires the Responses API for tool calls on them, e.g.
        gpt-6.1-sol) take the Responses path; everything else uses chat
        completions.
        """
        use_model = model or self.default_model
        if _uses_responses_api(self._config, use_model):
            async for event in self._converse_responses(
                system=system,
                messages=messages,
                tools=tools,
                model=use_model,
                stream=stream,
            ):
                yield event
            return
        timeout = model_timeout_seconds(self._config, use_model)
        started = time.monotonic()
        wire_messages = _wire_messages(system, messages)
        wire_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                },
            }
            for t in tools
        ]
        try:
            response = await self._client.chat.completions.create(
                model=use_model,
                **self._temperature_kwargs(use_model, 0.2),
                messages=cast(Any, wire_messages),
                tools=cast(Any, wire_tools) if wire_tools else cast(Any, None),
                stream=True,
                stream_options={"include_usage": True},
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            yield ConverseEvent(
                kind="done",
                result=_converse_error(use_model, exc, started, self.provider),
            )
            return
        text_parts: list[str] = []
        # OpenAI streams tool calls as (index → {id, name, arguments}) fragments.
        call_parts: dict[int, dict[str, Any]] = {}
        usage: Any = None
        request_id: str | None = None
        finish = ""
        refused = False
        try:
            async for chunk in response:
                if request_id is None:
                    request_id = getattr(chunk, "id", None)
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish = choice.finish_reason or finish
                delta = choice.delta
                if delta.content:
                    text_parts.append(delta.content)
                    if stream:
                        yield ConverseEvent(kind="delta", delta=delta.content)
                for tc in delta.tool_calls or []:
                    part = call_parts.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        part["id"] += tc.id
                    fn = tc.function
                    if fn is not None:
                        if fn.name:
                            part["name"] += fn.name
                        if fn.arguments:
                            part["arguments"] += fn.arguments
        except Exception as exc:  # noqa: BLE001
            yield ConverseEvent(
                kind="done",
                result=_converse_error(use_model, exc, started, self.provider),
            )
            return
        if finish == "content_filter":
            refused = True
        # A round cut by the token cap reads as success downstream — flag
        # it as invalid output like Anthropic's max_tokens does.
        truncated = finish == "length"
        calls: list[ToolCall] = []
        for idx in sorted(call_parts):
            part = call_parts[idx]
            try:
                args = json.loads(part["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(
                ToolCall(
                    id=part["id"] or f"call_{idx}",
                    name=part["name"],
                    arguments=args if isinstance(args, dict) else {},
                )
            )
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details else None
        yield ConverseEvent(
            kind="done",
            result=ConverseResult(
                text="".join(text_parts),
                tool_calls=tuple(calls),
                model=use_model,
                tokens_in=usage.prompt_tokens if usage else None,
                tokens_out=usage.completion_tokens if usage else None,
                cached_tokens_in=cached,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=(
                    "content_filter refusal"
                    if refused
                    else ("truncated" if truncated else None)
                ),
                error_type=(
                    "refusal"
                    if refused
                    else ("invalid_output" if truncated else None)
                ),
                request_id=request_id,
                refused=refused,
                provider=self.provider,
            ),
        )


def _uses_responses_api(config: AppConfig, model: str) -> bool:
    """``models.<id>.api == "responses"`` — the provider requires the
    Responses API (tool calls are chat-completions-only otherwise)."""
    from app.llm.models import models_table

    entry = models_table(config).get(model) or {}
    return entry.get("api") == "responses"


def _responses_input(messages: list[ConverseMessage]) -> list[dict[str, Any]]:
    """Provider-agnostic turns → Responses ``input`` items."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            if m.text:
                out.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": m.text}],
                    }
                )
            out.extend(
                {
                    "type": "function_call",
                    "call_id": c.id,
                    "name": c.name,
                    "arguments": json.dumps(c.arguments),
                }
                for c in m.tool_calls
            )
        elif m.role == "tool":
            out.append(
                {
                    "type": "function_call_output",
                    "call_id": m.tool_call_id,
                    "output": m.text,
                }
            )
        else:
            out.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": m.text}],
                }
            )
    return out


def _wire_messages(system: str, messages: list[ConverseMessage]) -> list[dict[str, Any]]:
    """Provider-agnostic turns → chat.completions message dicts."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.text or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.name,
                            "arguments": json.dumps(c.arguments),
                        },
                    }
                    for c in m.tool_calls
                ]
            out.append(msg)
        elif m.role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.text,
                }
            )
        else:
            out.append({"role": "user", "content": m.text})
    return out


def _converse_error(model: str, exc: Exception, started: float, provider: str) -> ConverseResult:
    return ConverseResult(
        text="",
        tool_calls=(),
        model=model,
        tokens_in=None,
        tokens_out=None,
        latency_ms=int((time.monotonic() - started) * 1000),
        error=str(exc),
        error_type=_classify_error(exc),
        http_status=getattr(exc, "status_code", None),
        error_code=getattr(exc, "code", None),
        request_id=getattr(exc, "request_id", None),
        provider=provider,
    )


async def test_key(key: str) -> tuple[bool, str | None]:
    """The spec's key check: one OpenAI list-models call."""
    from openai import AsyncOpenAI

    try:
        client = AsyncOpenAI(api_key=key, timeout=15)
        await client.models.list()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
