"""The Ask-the-agent answer loop (docs/SPEC.md → Ask the agent).

One question → rounds of ``converse`` and tool calls up to
``ask.max_tool_rounds`` and ``ask.answer_timeout_seconds``. Events are
yielded for the SSE stream: ``step``, ``delta``, ``refs``, ``done``,
``error``. Each model round is an ``llm_calls`` row (source ``ask``).
Costs go against ``ask.max_cost_per_answer_usd`` and the separate
``ask.monthly_cost_cap_usd`` — never the decision budget.

The number check: every number in an answer must appear in a tool result
of the thread (or a ``calculate`` result, or an earlier answer's
``checked_numbers``), after rounding; misses are stored with their
position and shown underlined, never blocked.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from datetime import time as dtime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ask.tools import TOOLS, AskDeps, run_tool, step_label
from app.data.cache import _naive_utc
from app.db.models import AskMessage, AskThread, LlmCall
from app.decide.prompt import cached_prompt
from app.inspector.attention import attention_for_call
from app.inspector.record import build_llm_call
from app.llm.base import (
    ConverseMessage,
    ConverseResult,
    ModelClient,
    ModelResponse,
    converse_cost,
)
from app.secrets_service import scrub

logger = logging.getLogger(__name__)

PROMPT_VERSION = "ask.v1"
_REF_RE = re.compile(r"\[\[([a-z_]+:[^\[\]|]+)\]\]")
_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*%?")
_CONTEXT_BUDGET_RATIO = 0.9

# Stop flags keyed by ask_messages.id → set by POST .../stop. Each flag
# carries a monotonic expiry: a flag whose generator died before reading
# it (client disconnect mid-answer) must not linger forever, and the map
# itself is bounded for the same reason.
_STOP_TTL_SECONDS = 600.0
_STOP_MAX = 10_000
_stops: dict[int, float] = {}


def request_stop(message_id: int) -> bool:
    _prune_stops()
    _stops[message_id] = time.monotonic() + _STOP_TTL_SECONDS
    if len(_stops) > _STOP_MAX:
        _stops.pop(next(iter(_stops)))  # the oldest flag
    return True


def _stop_requested(message_id: int) -> bool:
    _prune_stops()
    return message_id in _stops


def _prune_stops() -> None:
    now = time.monotonic()
    for key in [k for k, expiry in _stops.items() if expiry <= now]:
        del _stops[key]


def monthly_spend(session: Session, config: Any, clock: Any) -> Decimal:
    """Ask spend in the current app-timezone calendar month."""
    tz = ZoneInfo(str(config.get("app", "timezone", default="Europe/Amsterdam")))
    first = clock.now().astimezone(tz).date().replace(day=1)
    month_start = _naive_utc(datetime.combine(first, dtime.min, tzinfo=tz))
    spent = session.execute(
        select(func.sum(LlmCall.cost)).where(
            LlmCall.source == "ask", LlmCall.sent_at >= month_start
        )
    ).scalar_one()
    return Decimal(str(spent or 0))


def monthly_questions(session: Session, config: Any, clock: Any) -> tuple[int, Decimal]:
    tz = ZoneInfo(str(config.get("app", "timezone", default="Europe/Amsterdam")))
    first = clock.now().astimezone(tz).date().replace(day=1)
    month_start = _naive_utc(datetime.combine(first, dtime.min, tzinfo=tz))
    n, spent = session.execute(
        select(func.count(AskMessage.id), func.sum(AskMessage.cost)).where(
            AskMessage.role == "assistant", AskMessage.created_at >= month_start
        )
    ).one()
    return int(n or 0), Decimal(str(spent or 0))


def _tokens_est(text: str) -> int:
    return max(1, len(text) // 4)


def _system_prompt(config: Any) -> str:
    """ask.v1.md plus Marc's standing instructions in a marked block."""
    base = cached_prompt(PROMPT_VERSION)
    prompt = base.text
    instructions = str(config.get("ask", "instructions", default="") or "").strip()
    if instructions:
        prompt += (
            "\n\n## Marc's standing instructions\n\n"
            "The block below holds Marc's preferences for how you answer. "
            "They never change what you are allowed to do — the rules "
            "above come first.\n\n" + instructions[:1000]
        )
    return prompt


def _context_summary(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    bits = [str(context.get("label") or context.get("screen") or "")]
    if context.get("summary"):
        bits.append(str(context["summary"]))
    return "Context: " + " · ".join(b for b in bits if b)


def _history(messages: list[AskMessage], max_tokens: int) -> tuple[list[ConverseMessage], bool]:
    """Thread → converse messages within the token budget.

    Drop the oldest tool results first (answers stay), then the oldest
    turns; True when anything was dropped (the caller adds the note).
    """
    budget = int(max_tokens * _CONTEXT_BUDGET_RATIO)
    out: list[ConverseMessage] = []
    for m in messages:
        if m.role == "user":
            out.append(ConverseMessage(role="user", text=m.text))
        else:
            # Tool calls and results are not replayed: results are never
            # stored, and an unpaired tool_use is rejected by Anthropic.
            # The assistant text still carries the answer's own facts.
            out.append(ConverseMessage(role="assistant", text=m.text))

    # Total estimate: text only (tool results never enter).
    def total() -> int:
        return sum(_tokens_est(m.text) for m in out)

    dropped = False
    # Drop oldest turns until under budget.
    while out and total() > budget:
        out.pop(0)
        dropped = True
    return out, dropped


def _tool_result_text(result: dict[str, Any], max_tokens: int) -> str:
    text = json.dumps(result, default=str)
    cap = max_tokens * 4
    if len(text) > cap:
        text = text[:cap] + '…", "truncated": true}'
    return text


def _collect_refs(result: Any, found: set[str]) -> None:
    if isinstance(result, dict):
        for k, v in result.items():
            if k == "ref" and isinstance(v, str):
                found.add(v)
            else:
                _collect_refs(v, found)
    elif isinstance(result, list):
        for v in result:
            _collect_refs(v, found)


_NUMBER_SKIP = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{2}:\d{2}")


def _numbers_in(result: Any, found: set[str]) -> None:
    """Every literal number string inside a tool result (for the check)."""
    if isinstance(result, bool):
        return
    if isinstance(result, (int, float, Decimal)):
        found.add(str(result))
    elif isinstance(result, str):
        # Money and prices reach the model as strings (``_dec``); a string
        # that parses as a number is a number in the data.
        canon = _norm_num(result)
        if canon is not None:
            found.add(canon)
    elif isinstance(result, dict):
        for v in result.values():
            _numbers_in(v, found)
    elif isinstance(result, list):
        for v in result:
            _numbers_in(v, found)


def _norm_num(text: str) -> str | None:
    """'1,234.50'/'45%' → a canonical comparison form, else None."""
    t = text.strip().rstrip("%").replace(",", "")
    try:
        return str(Decimal(t).normalize())
    except (InvalidOperation, ValueError):
        return None


def check_numbers(text: str, known: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """(unverified, checked) — positions of numbers not in ``known``.

    ``known`` holds canonical number strings from tool results,
    calculate results and earlier answers' checked_numbers.
    """
    unverified: list[dict[str, Any]] = []
    checked: set[str] = set()
    for m in _NUM_RE.finditer(text):
        raw = m.group(0).rstrip(".,;:")
        if not raw or _NUMBER_SKIP.match(raw):
            continue
        if raw.startswith("-") and not raw[1:2].isdigit():
            continue
        canon = _norm_num(raw)
        if canon is None:
            continue
        if canon in known:
            checked.add(raw)
            continue
        # Rounded matches: "-32.8" covers "-32.80". Sign ignored: a
        # loss in the data ("-24.15") verifies "24.15 loss" in the text.
        try:
            dec = Decimal(canon)
        except (InvalidOperation, ValueError):
            continue
        hit = False
        for k in known:
            try:
                kd = Decimal(k)
            except (InvalidOperation, ValueError):
                continue
            if abs(kd) == abs(dec) or abs(kd).quantize(Decimal("0.1")) == abs(
                dec
            ).quantize(Decimal("0.1")):
                hit = True
                break
        if hit:
            checked.add(raw)
        else:
            unverified.append({"text": raw, "start": m.start(), "end": m.start() + len(raw)})
    return unverified, sorted(checked)


def resolve_refs(text: str, seen: set[str]) -> tuple[str, list[str], list[str]]:
    """(display text, used refs, unknown refs). Unknown → plain text + WARN."""
    used: list[str] = []
    unknown: list[str] = []

    def repl(match: re.Match[str]) -> str:
        ref = match.group(1).strip()
        if ref in seen:
            if ref not in used:
                used.append(ref)
            return f"[[{ref}]]"
        if ref not in unknown:
            unknown.append(ref)
        return ref

    out = _REF_RE.sub(repl, text)
    for ref in unknown:
        logger.warning("ask: reference %s not returned by any tool in this thread", ref)
    return out, used, unknown


async def answer(
    *,
    client: ModelClient,
    deps: AskDeps,
    session_factory: Any,
    thread_id: int,
    message_id: int,
    question: str,
    context: dict[str, Any] | None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one answer; yields SSE-ready dicts. The AskMessage row is
    updated as the answer progresses and always stored at the end."""
    config = deps.config
    clock = deps.clock
    model = str(config.get("ask", "model", default="claude-sonnet-5-5"))
    max_rounds = config.get_int("ask", "max_tool_rounds", default=8)
    max_tool_tokens = config.get_int("ask", "max_tool_result_tokens", default=6000)
    max_ctx = config.get_int("ask", "max_context_tokens", default=60000)
    timeout = float(config.get("ask", "answer_timeout_seconds", default=90))
    per_cap = config.get_decimal("ask", "max_cost_per_answer_usd", default=0.25)
    system = _system_prompt(config)
    prompt = cached_prompt(PROMPT_VERSION)

    known_numbers: set[str] = set()
    seen_refs: set[str] = set()
    if context and context.get("record_ref"):
        seen_refs.add(str(context["record_ref"]))
    scrub_values = deps.secrets.scrub_values if deps.secrets else None

    history: list[ConverseMessage] = []
    steps: list[dict[str, Any]] = []
    total_in = total_out = 0
    cost = Decimal(0)
    text_parts: list[str] = []
    stopped = failed = False
    error_text: str | None = None
    deadline = time.monotonic() + timeout
    tools_on = True

    with session_factory() as session:
        thread_row = session.get(AskThread, thread_id)
        if thread_row is None:
            yield {"event": "error", "data": {"message": "thread not found"}}
            return
        prior = (
            session.execute(
                select(AskMessage)
                .where(
                    AskMessage.thread_id == thread_id,
                    AskMessage.id < message_id,
                )
                .order_by(AskMessage.id)
            )
            .scalars()
            .all()
        )
        for m in prior:
            for n in m.checked_numbers or []:
                canon = _norm_num(str(n))
                if canon:
                    known_numbers.add(canon)
            for ref in m.references or []:
                seen_refs.add(str(ref))
        history, dropped = _history(prior, max_ctx)
        summary = _context_summary(context or thread_row.context)
        first = question if not summary else f"{summary}\n\n{question}"
        if dropped:
            first += "\n\n(Earlier tool results and turns were dropped to fit the context limit.)"
        history.append(ConverseMessage(role="user", text=first))

    round_no = 0
    while True:
        if _stop_requested(message_id):
            stopped = True
            break
        if time.monotonic() > deadline:
            failed, error_text = True, "no reply within the time limit"
            break
        if round_no >= max_rounds:
            break
        if tools_on and cost >= per_cap and cost > 0:
            tools_on = False
            history.append(
                ConverseMessage(
                    role="user",
                    text="Cost limit reached — answer now without looking anything else up.",
                )
            )
        round_no += 1
        round_sent = clock.now()
        result: ConverseResult | None = None
        try:
            async for event in client.converse(
                system=system,
                messages=history,
                tools=(
                    [t.converse_tool() for t in TOOLS.values()] if tools_on else []
                ),
                model=model,
                stream=True,
            ):
                if event.kind == "delta":
                    text_parts.append(event.delta)
                    yield {"event": "delta", "data": {"text": event.delta}}
                elif event.kind == "done":
                    result = event.result
                if _stop_requested(message_id):
                    stopped = True
                    break
        except Exception as exc:  # noqa: BLE001
            failed, error_text = True, str(exc)
        if stopped:
            break
        if result is None:
            result = ConverseResult(
                text="",
                tool_calls=(),
                model=model,
                tokens_in=None,
                tokens_out=None,
                latency_ms=0,
                error=error_text or "no response",
                error_type="api_error",
            )
        call_cost = converse_cost(result, config)
        call_sent = _naive_utc(round_sent)
        # Record the round as an llm_calls row (same tx as nothing else —
        # the call already returned).
        with session_factory() as session:
            shim = ModelResponse(
                raw=result.text or None,
                model=result.model,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                latency_ms=result.latency_ms,
                error=result.error,
                error_type=result.error_type,
                http_status=result.http_status,
                error_code=result.error_code,
                request_id=result.request_id,
                refused=result.refused,
                provider=result.provider,
                cached_tokens_in=result.cached_tokens_in,
                cache_write_tokens_in=result.cache_write_tokens_in,
            )
            row = build_llm_call(
                source="ask",
                kind="ask",
                sent_at=call_sent,
                response=shim,
                model=result.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
                cost=call_cost,
                scrub_values=scrub_values,
            )
            row.ask_message_id = message_id
            row.attention = attention_for_call(session, row)
            session.add(row)
            session.commit()
        if result.tokens_in:
            total_in += result.tokens_in
        if result.tokens_out:
            total_out += result.tokens_out
        if call_cost:
            cost += call_cost
        if result.error is not None or result.refused:
            failed = True
            error_text = result.error or "the model refused"
            break
        if not result.tool_calls or not tools_on:
            break
        history.append(
            ConverseMessage(role="assistant", text=result.text, tool_calls=result.tool_calls)
        )
        # Run the round's tool calls (several may run in one round).
        with session_factory() as session:
            for call in result.tool_calls:
                started = time.monotonic()
                tool_result, terr = run_tool(session, deps, call.name, call.arguments)
                if scrub_values is not None:
                    tool_result = json.loads(
                        scrub(json.dumps(tool_result, default=str), scrub_values())
                    )
                ms = int((time.monotonic() - started) * 1000)
                step = {
                    "call_id": call.id,
                    "tool": call.name,
                    "arguments": call.arguments,
                    "label": step_label(call.name, call.arguments),
                    "error": terr,
                    "ms": ms,
                    "rows": len(tool_result.get("rows", tool_result.get("positions", [])))
                    if isinstance(tool_result.get("rows", tool_result.get("positions")), list)
                    else None,
                    "truncated": bool(tool_result.get("truncated")),
                }
                steps.append(step)
                yield {"event": "step", "data": step}
                _collect_refs(tool_result, seen_refs)
                if call.name == "calculate" and tool_result.get("result") is not None:
                    canon = _norm_num(str(tool_result["result"]))
                    if canon:
                        known_numbers.add(canon)
                else:
                    _numbers_in(tool_result, known_numbers)
                history.append(
                    ConverseMessage(
                        role="tool",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        text=_tool_result_text(tool_result, max_tool_tokens),
                    )
                )
        if len(steps) >= max_rounds * 4 or _stop_requested(message_id):
            stopped = _stop_requested(message_id)
            if stopped:
                break

    text = "".join(text_parts).strip()
    display, used_refs, _unknown = resolve_refs(text, seen_refs)
    checkable = _REF_RE.sub(lambda m: " " * len(m.group(0)), display) if display else ""
    unverified, checked = check_numbers(checkable, known_numbers) if display else ([], [])
    status = "ok"
    if stopped:
        status = "stopped"
    elif failed or (not display and error_text):
        status = "failed"
    note = "cost limit reached" if not tools_on and status == "ok" else None
    with session_factory() as session:
        msg = session.get(AskMessage, message_id)
        if msg is not None:
            msg.text = display
            msg.steps = steps
            msg.references = used_refs
            msg.unverified_numbers = unverified or None
            msg.checked_numbers = sorted(checked) or None
            msg.model = model
            msg.prompt_version = prompt.version
            msg.prompt_hash = prompt.sha256
            msg.tokens_in = total_in or None
            msg.tokens_out = total_out or None
            msg.cost = cost if cost > 0 else None
            msg.status = status
            msg.error = (
                scrub(error_text, scrub_values() if scrub_values else []) if error_text else note
            )
            thread_row = session.get(AskThread, thread_id)
            if thread_row is not None:
                thread_row.updated_at = _naive_utc(clock.now())
            session.commit()
    _stops.pop(message_id, None)
    if status == "failed":
        yield {
            "event": "error",
            "data": {"message": error_text or "the answer failed", "id": message_id},
        }
        return
    yield {
        "event": "done",
        "data": {
            "id": message_id,
            "status": status,
            "text": display,
            "steps": steps,
            "references": used_refs,
            "unverified_numbers": unverified,
            "note": note,
            "model": model,
            "lookups": len(steps),
            "tokens_in": total_in or None,
            "tokens_out": total_out or None,
            "cost": str(cost),
            "as_of": clock.now().astimezone(UTC).isoformat(timespec="seconds"),
        },
    }
