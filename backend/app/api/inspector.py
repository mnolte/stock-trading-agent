"""LLM call history API (docs/SPEC.md, LLM call history — API).

Four endpoints:

- ``GET /api/llm-calls`` — filtered page, newest first by default, with a
  keyset cursor on (sent_at, id) so pages never gap or duplicate while
  new calls arrive; ``limit`` up to 100, ``order=asc`` for oldest first.
- ``GET /api/llm-calls/summary`` — totals, outcome shares, cost by
  source, latency median/p95, per-session buckets and the option counts
  every filter shows under the other filters.
- ``GET /api/llm-calls/{id}`` — the full call with the prompt rebuilt
  from the versioned prompt file plus the linked row's stored input;
  a hash mismatch is reported, not hidden.
- ``GET /api/llm-calls/export.csv`` — the filtered calls' metadata only;
  prompts and responses are never exported.

Session dates are New York dates; a call outside market hours belongs to
the New York date it was sent. Numbers go out as numbers.
"""

from __future__ import annotations

import csv
import io
import json
import typing
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import String, asc, cast, desc, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.db.base import get_session_maker
from app.db.models import (
    Cycle,
    Decision,
    LlmCall,
    Review,
    ShadowDecision,
    Strategy,
    Verdict,
)
from app.decide.prompt import PromptError, cached_prompt

router = APIRouter()

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100
_SOURCES = ("live", "shadow", "review")
_OUTCOMES = ("success", "warning", "error")

_FILTER_KEYS = (
    "from", "to", "outcome", "source", "symbol", "action", "reason",
    "model", "prompt", "strategy", "cycle",
)


def _iso(dt: datetime | None) -> str | None:
    return f"{dt.isoformat()}Z" if dt is not None else None


def _num(value: Decimal | float | None) -> float | None:
    return float(value) if value is not None else None


def _parse_day(raw: str | None, name: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(422, f"{name} is not a date (YYYY-MM-DD)") from exc


def _cycle_cond(value: str) -> ColumnElement[bool]:
    """``?cycle=`` takes a cycle id or the cycle's scheduled instant —
    feed links carry the scheduled time."""
    if value.isdigit():
        return LlmCall.cycle_id == int(value)
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, "cycle is not an id or a time") from exc
    return LlmCall.cycle_id.in_(
        select(Cycle.id).where(Cycle.scheduled_time == at.replace(tzinfo=None))
    )


def _conditions(
    q: dict[str, str | None], *, skip: str | None = None
) -> list[ColumnElement[bool]]:
    """The shared filter set; ``skip`` drops one dim for option counts
    (``period`` drops the date range so the session list stays complete)."""
    conds: list[ColumnElement[bool]] = []
    if skip != "period":
        if (from_ := _parse_day(q.get("from"), "from")) is not None:
            conds.append(LlmCall.session_date >= from_)
        if (to := _parse_day(q.get("to"), "to")) is not None:
            conds.append(LlmCall.session_date <= to)
    if skip != "outcome" and (outcome := q.get("outcome")):
        conds.append(LlmCall.outcome == outcome)
    if skip != "source" and (source := q.get("source")):
        conds.append(LlmCall.source.in_(source.split(",")))
    if skip != "symbol" and (symbol := q.get("symbol")):
        conds.append(LlmCall.symbol == symbol.upper())
    if skip != "action" and (action := q.get("action")):
        conds.append(LlmCall.final_action == action)
    if skip != "reason" and (reason := q.get("reason")):
        je = func.json_each(LlmCall.reasons).table_valued("value").alias("je")
        conds.append(select(je.c.value).where(je.c.value == reason).exists())
    if skip != "model" and (model := q.get("model")):
        conds.append(LlmCall.model == model)
    if skip != "prompt" and (prompt := q.get("prompt")):
        conds.append(LlmCall.prompt_version == prompt)
    if skip != "strategy" and (strategy := q.get("strategy")):
        conds.append(
            LlmCall.strategy_id.in_(
                select(Strategy.id).where(
                    (Strategy.name == strategy)
                    | (cast(Strategy.id, String) == strategy)
                )
            )
        )
    if skip != "cycle" and (cycle := q.get("cycle")):
        conds.append(_cycle_cond(cycle))
    return conds


def _query_params(request: Request) -> dict[str, str | None]:
    return {k: request.query_params.get(k) for k in _FILTER_KEYS}


def _row_json(call: LlmCall, strategy_names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": call.id,
        "sent_at": _iso(call.sent_at),
        "session_date": call.session_date.isoformat(),
        "source": call.source,
        "kind": call.kind,
        "strategy_id": call.strategy_id,
        "strategy": (
            strategy_names.get(call.strategy_id)
            if call.strategy_id is not None
            else None
        ),
        "decision_id": call.decision_id,
        "shadow_decision_id": call.shadow_decision_id,
        "review_id": call.review_id,
        "verdict_id": call.verdict_id,
        "cycle_id": call.cycle_id,
        "symbol": call.symbol,
        "context": call.context,
        "model": call.model,
        "temperature": call.temperature,
        "prompt_version": call.prompt_version,
        "prompt_hash": call.prompt_hash,
        "tokens_in": call.tokens_in,
        "tokens_out": call.tokens_out,
        "cost": _num(call.cost),
        "cost_source": call.cost_source,
        "latency_ms": call.latency_ms,
        "final_action": call.final_action,
        "outcome": call.outcome,
        "reasons": call.reasons,
        "error_type": call.error_type,
        "http_status": call.http_status,
        "backfilled": call.backfilled,
    }


def _strategy_names(session: Session) -> dict[int, str]:
    return {
        int(row[0]): str(row[1])
        for row in session.execute(select(Strategy.id, Strategy.name))
    }


def _encode_cursor(call: LlmCall) -> str:
    return f"{call.sent_at.isoformat()}|{call.id}"


def _decode_cursor(raw: str) -> tuple[datetime, int]:
    try:
        sent, ident = raw.rsplit("|", 1)
        return datetime.fromisoformat(sent), int(ident)
    except ValueError as exc:
        raise HTTPException(422, "bad cursor") from exc


@router.get("/llm-calls/export.csv")
def export_calls(request: Request) -> StreamingResponse:
    """Metadata of the filtered calls — never prompts or responses."""
    conds = _conditions(_query_params(request))
    with get_session_maker()() as session:
        names = _strategy_names(session)
        rows = session.execute(
            select(LlmCall)
            .where(*conds)
            .order_by(desc(LlmCall.sent_at), desc(LlmCall.id))
        ).scalars().all()

    def gen() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "time", "session_date", "source", "strategy", "kind",
                "symbol", "context", "action", "outcome", "reasons",
                "model", "prompt", "tokens_in", "tokens_out", "cost",
                "latency_ms",
            ]
        )
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()
        for call in rows:
            writer.writerow(
                [
                    _iso(call.sent_at),
                    call.session_date.isoformat(),
                    call.source,
                    (
                        names.get(call.strategy_id)
                        if call.strategy_id is not None
                        else ""
                    ),
                    call.kind,
                    call.symbol or "",
                    call.context or "",
                    call.final_action or "",
                    call.outcome,
                    " ".join(call.reasons),
                    call.model or "",
                    call.prompt_version or "",
                    call.tokens_in if call.tokens_in is not None else "",
                    call.tokens_out if call.tokens_out is not None else "",
                    str(call.cost) if call.cost is not None else "",
                    call.latency_ms if call.latency_ms is not None else "",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    return StreamingResponse(
        gen(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="llm-calls.csv"'},
    )


@router.get("/llm-calls/summary")
def calls_summary(request: Request) -> dict[str, Any]:
    """Totals, per-session buckets and option counts for the filters."""
    q = _query_params(request)
    conds = _conditions(q)
    with get_session_maker()() as session:
        # One covering-index scan (session_date × source × outcome) feeds
        # the total, the outcome totals, the session buckets, the session
        # list and every cost figure — the alternative is five separate
        # full-table aggregations.
        wide_rows = session.execute(
            select(
                LlmCall.session_date,
                LlmCall.source,
                LlmCall.outcome,
                func.count(),
                func.sum(LlmCall.cost),
            )
            .where(*conds)
            .group_by(LlmCall.session_date, LlmCall.source, LlmCall.outcome)
            .order_by(desc(LlmCall.session_date))
        ).all()
        total = 0
        by_outcome = dict.fromkeys(_OUTCOMES, 0)
        cost_by_source: dict[str, Decimal] = {}
        day_costs: list[Decimal] = []
        days: dict[str, dict[str, Any]] = {}
        for day, source, outcome, n, cost in wide_rows:
            total += int(n)
            if outcome in by_outcome:
                by_outcome[outcome] += int(n)
            if cost is not None:
                day_costs.append(cost)
                cost_by_source[str(source)] = (
                    cost_by_source.get(str(source), Decimal(0)) + cost
                )
            bucket = days.setdefault(
                str(day),
                {
                    "session_date": str(day),
                    "calls": 0,
                    "success": 0,
                    "warning": 0,
                    "error": 0,
                },
            )
            bucket["calls"] += int(n)
            if outcome in bucket:
                bucket[outcome] += int(n)
        buckets = list(days.values())
        # The session calendar under the other filters (the date range is
        # skipped) — the period options resolve against the full list.
        # With no from/to at all the buckets already carry it.
        if q.get("from") is None and q.get("to") is None:
            session_dates = [b["session_date"] for b in buckets]
        else:
            session_dates = [
                str(d)
                for (d,) in session.execute(
                    select(LlmCall.session_date)
                    .where(*_conditions(q, skip="period"))
                    .group_by(LlmCall.session_date)
                    .order_by(desc(LlmCall.session_date))
                )
            ]
        cost_total = sum(day_costs, Decimal(0)) if day_costs else None

        # Median/p95 without pulling every latency into Python: two
        # ordered offset lookups against the count.
        n_lat = session.execute(
            select(func.count())
            .select_from(LlmCall)
            .where(*conds, LlmCall.latency_ms.is_not(None))
        ).scalar_one()

        def latency_at(offset: int) -> int | None:
            return session.execute(
                select(LlmCall.latency_ms)
                .where(*conds, LlmCall.latency_ms.is_not(None))
                .order_by(LlmCall.latency_ms)
                .limit(1)
                .offset(offset)
            ).scalar_one_or_none()

        median = latency_at((n_lat - 1) // 2) if n_lat else None
        p95 = latency_at(min(n_lat - 1, int(0.95 * n_lat))) if n_lat else None

        # Option counts: each dimension counted with every other filter
        # applied, so the options show what a choice would yield. One
        # covering-index scan grouped by all dims replaces a per-dim
        # aggregation — the combos are few, Python fans them out.
        names = _strategy_names(session)
        dim_cols = {
            "outcome": LlmCall.outcome,
            "source": LlmCall.source,
            "symbol": LlmCall.symbol,
            "action": LlmCall.final_action,
            "model": LlmCall.model,
            "prompt": LlmCall.prompt_version,
            "strategy": LlmCall.strategy_id,
        }

        def dim_passes(param: str, row_value: Any) -> bool:
            """Whether the row's value passes that param's own filter."""
            wanted = q.get(param)
            if not wanted:
                return True
            if param == "source":
                return row_value in wanted.split(",")
            if param == "symbol":
                return bool(row_value == wanted.upper())
            if param == "strategy":
                return bool(
                    names.get(row_value) == wanted
                    or (row_value is not None and str(row_value) == wanted)
                )
            return bool(row_value == wanted)

        base_q = {k: q.get(k) for k in _FILTER_KEYS if k not in dim_cols}
        dim_rows = session.execute(
            select(*dim_cols.values(), func.count())
            .where(*_conditions(base_q))
            .group_by(*dim_cols.values())
        ).all()
        options: dict[str, dict[str, int]] = {dim: {} for dim in dim_cols}
        for row in dim_rows:
            values = dict(zip(dim_cols, row[:-1], strict=True))
            n = int(typing.cast(int, row[-1]))
            for dim, value in values.items():
                if value is None:
                    continue
                if all(
                    dim_passes(other, values[other])
                    for other in values
                    if other != dim
                ):
                    key = (
                        names.get(typing.cast(int, value), str(value))
                        if dim == "strategy"
                        else str(value)
                    )
                    options[dim][key] = options[dim].get(key, 0) + n

        # Reasons are few distinct JSON lists: counting the raw values
        # uses the reasons index, then Python fans each list out into
        # per-reason counts — json_each per row was the slow option.
        reason_options: dict[str, int] = {}
        for raw, n in session.execute(
            select(LlmCall.reasons, func.count())
            .where(*_conditions(q, skip="reason"))
            .group_by(LlmCall.reasons)
        ):
            for reason in raw if isinstance(raw, list) else []:
                reason_options[reason] = reason_options.get(reason, 0) + int(n)
        options["reason"] = reason_options

    return {
        "total": int(total),
        "outcomes": {o: by_outcome.get(o, 0) for o in _OUTCOMES},
        "cost_total": _num(cost_total),
        "cost_by_source": {s: _num(cost_by_source.get(s)) for s in _SOURCES},
        "latency": {"median_ms": median, "p95_ms": p95, "counted": n_lat},
        "buckets": buckets,
        "session_dates": session_dates,
        "options": options,
    }


@router.get("/llm-calls/{call_id}")
def call_detail(request: Request, call_id: int) -> dict[str, Any]:
    """One call with the prompt rebuilt from the versioned prompt file.

    The system part comes from ``backend/prompts/<version>.md``; when its
    hash differs from the stored hash the mismatch is reported and the
    text withheld. The user part is the linked row's stored input
    snapshot or facts — never re-fetched.
    """
    with get_session_maker()() as session:
        call = session.get(LlmCall, call_id)
        if call is None:
            raise HTTPException(404, "call not found")
        names = _strategy_names(session)
        body = _row_json(call, names)
        body["error"] = {
            "type": call.error_type,
            "http_status": call.http_status,
            "code": call.error_code,
            "message": call.error_message,
            "request_id": call.request_id,
        }
        body["raw_response"] = call.raw_response

        # Rebuild the prompt: the versioned file plus the stored input.
        prompt_mismatch = False
        system = None
        if call.prompt_version:
            try:
                prompt = cached_prompt(call.prompt_version)
            except (FileNotFoundError, KeyError, OSError, PromptError):
                prompt = None
            if prompt is not None and (
                call.prompt_hash is None or prompt.sha256 == call.prompt_hash
            ):
                system = prompt.text
            else:
                prompt_mismatch = True
        body["prompt"] = {
            "system": system,
            "user": _user_part(session, call),
            "stored_hash": call.prompt_hash,
            "mismatch": prompt_mismatch,
        }

        body["result"], body["explanation"] = _result_text(session, call)
        body["link"] = _link(call)
    return body


def _user_part(session: Session, call: LlmCall) -> str | None:
    """The user message, from the linked row's stored input."""
    payload: dict[str, Any] | None = None
    if call.decision_id is not None and call.source == "live":
        decision = session.get(Decision, call.decision_id)
        payload = decision.input_snapshot if decision else None
    elif call.shadow_decision_id is not None:
        shadow = session.get(ShadowDecision, call.shadow_decision_id)
        if shadow is not None:
            payload = shadow.input_snapshot
            if payload is None and shadow.live_decision_id is not None:
                live = session.get(Decision, shadow.live_decision_id)
                payload = live.input_snapshot if live else None
    elif call.review_id is not None:
        review = session.get(Review, call.review_id)
        if review is not None:
            payload = {"kind": review.kind, "facts": review.facts}
    elif call.verdict_id is not None:
        verdict = session.get(Verdict, call.verdict_id)
        if verdict is not None and verdict.facts is not None:
            payload = {"kind": "verdict", "facts": verdict.facts}
        if payload is None:
            payload = {"kind": "verdict", "facts": None}
    return json.dumps(payload, indent=2) if payload is not None else None


def _result_text(session: Session, call: LlmCall) -> tuple[str, str | None]:
    """The outcome block's one-line result and explanation."""
    if call.outcome == "error":
        label = {
            "timeout": "No usable response — the call timed out",
            "api_error": "No usable response — the API call failed",
            "invalid_output": "No usable response — output failed the checks",
            "refusal": "No usable response — the model refused",
        }.get(call.error_type or "", "No usable response")
        return label, call.error_message

    explanation: str | None = None
    model_action: str | None = None
    row: Decision | ShadowDecision | None = None
    if call.source == "live" and call.decision_id is not None:
        row = session.get(Decision, call.decision_id)
    elif call.source == "shadow" and call.shadow_decision_id is not None:
        row = session.get(ShadowDecision, call.shadow_decision_id)
    if row is not None:
        validation = row.validation_result or {}
        raw_action = validation.get("model_action")
        model_action = raw_action if isinstance(raw_action, str) else None
        gates = row.gates_result or {}
        blocked_by = gates.get("blocked_by")
        note = validation.get("note") or validation.get("error")
        explanation = (
            str(note)
            if note
            else (f"Blocked by {blocked_by}" if blocked_by else None)
        )
    elif call.review_id is not None:
        review = session.get(Review, call.review_id)
        if review is not None and review.reason:
            explanation = f"The plain template was used ({review.reason})"
    elif call.verdict_id is not None:
        verdict = session.get(Verdict, call.verdict_id)
        if verdict is not None and verdict.draft_reason:
            explanation = f"The plain template was used ({verdict.draft_reason})"

    if "changed_by_code" in call.reasons and model_action:
        result = f"Code changed {model_action} → {call.final_action}"
    elif call.outcome == "warning":
        result = "Valid response · " + ", ".join(call.reasons)
    else:
        result = "Valid · used as chosen"
    return result, explanation


def _link(call: LlmCall) -> str | None:
    if call.source == "live" and call.decision_id is not None:
        return f"/decisions/{call.decision_id}"
    if call.source == "shadow" and call.strategy_id is not None:
        return f"/strategies?strategy={call.strategy_id}"
    if call.source == "review":
        return "/reviews"
    return None


@router.get("/llm-calls")
def list_calls(
    request: Request,
    limit: int = Query(_DEFAULT_LIMIT, le=_MAX_LIMIT, ge=1),
    cursor: str | None = None,
    order: str = "desc",
) -> dict[str, Any]:
    """One filtered page of calls plus the total and next cursor.

    Keyset paging on (sent_at, id): rows arriving while paging never
    shift a boundary, so a session's calls can neither gap nor repeat.
    """
    filter_conds = _conditions(_query_params(request))
    conds = list(filter_conds)
    ascending = order == "asc"
    if cursor:
        sent, ident = _decode_cursor(cursor)
        keyset = (
            ((LlmCall.sent_at > sent) | ((LlmCall.sent_at == sent) & (LlmCall.id > ident)))
            if ascending
            else ((LlmCall.sent_at < sent) | ((LlmCall.sent_at == sent) & (LlmCall.id < ident)))
        )
        conds.append(keyset)
    with get_session_maker()() as session:
        total = session.execute(
            select(func.count()).select_from(LlmCall).where(*filter_conds)
        ).scalar_one()
        rows = session.execute(
            select(LlmCall)
            .where(*conds)
            .order_by(
                asc(LlmCall.sent_at) if ascending else desc(LlmCall.sent_at),
                asc(LlmCall.id) if ascending else desc(LlmCall.id),
            )
            .limit(limit + 1)
        ).scalars().all()
        names = _strategy_names(session)
    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "calls": [_row_json(call, names) for call in page],
        "total": int(total),
        "has_more": has_more,
        "next_cursor": _encode_cursor(page[-1]) if has_more and page else None,
    }
