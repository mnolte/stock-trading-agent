"""Build ``llm_calls`` rows (docs/SPEC.md, LLM call history — Storage).

One row is written for every request actually sent to OpenAI — live
decisions, shadow decisions and reviews — in the same transaction as the
linked row, after the call returned (hard rule 13). Calls that never
happened (rules ``no_call``, the call cap, price checks, a missing key, a
reached cost cap, missed shadow calls) get no row.

Error messages go through the same key scrubbing as logs; request headers
and API keys are never stored.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LlmCall
from app.decide.client import ModelResponse
from app.inspector.outcome import CallFacts, classify, input_incomplete
from app.market.clock import NY_TZ
from app.secrets_service import scrub as _scrub

if TYPE_CHECKING:
    from app.db.models import Decision, ShadowDecision

ScrubValues = Callable[[], list[str]]


def session_date(sent_at: datetime) -> date:
    """The New York date a call belongs to (its ``sent_at`` instant).

    Calls outside market hours — the Saturday weekly review, a verdict
    after the close — belong to the New York date they were sent.
    """
    return sent_at.replace(tzinfo=UTC).astimezone(NY_TZ).date()


def _scrubbed(message: str | None, scrub_values: ScrubValues | None) -> str | None:
    if not message:
        return None
    values = scrub_values() if scrub_values is not None else []
    return _scrub(message, values)


def build_llm_call(
    *,
    source: str,
    kind: str,
    sent_at: datetime,
    response: ModelResponse,
    input_snapshot: dict[str, Any] | None = None,
    model_action: str | None = None,
    final_action: str | None = None,
    output_invalid: bool = False,
    error_detail: str | None = None,
    number_check_failed: bool = False,
    unsupported_claim: bool = False,
    cycle_id: int | None = None,
    decision_id: int | None = None,
    shadow_decision_id: int | None = None,
    review_id: int | None = None,
    verdict_id: int | None = None,
    strategy_id: int | None = None,
    symbol: str | None = None,
    context: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    prompt_version: str | None = None,
    prompt_hash: str | None = None,
    cost: Decimal | None = None,
    cost_source: str | None = None,
    slow_ms: int | None = None,
    scrub_values: ScrubValues | None = None,
) -> LlmCall:
    """One call-history row for a completed request.

    ``sent_at`` is naive UTC. ``final_action``/``model_action`` reflect the
    linked row at write time; ``apply_final_action`` folds in later gate
    changes inside the same write.
    """
    outcome, reasons = classify(
        CallFacts(
            error_type=response.error_type,
            refused=response.refused,
            output_invalid=output_invalid,
            model_action=model_action,
            final_action=final_action,
            input_incomplete=input_incomplete(input_snapshot),
            number_check_failed=number_check_failed,
            unsupported_claim=unsupported_claim,
            latency_ms=response.latency_ms,
            slow_ms=slow_ms,
        )
    )
    return LlmCall(
        sent_at=sent_at,
        session_date=session_date(sent_at),
        source=source,
        kind=kind,
        strategy_id=strategy_id,
        decision_id=decision_id,
        shadow_decision_id=shadow_decision_id,
        review_id=review_id,
        verdict_id=verdict_id,
        cycle_id=cycle_id,
        symbol=symbol,
        context=context,
        model=model or response.model,
        temperature=temperature,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        cost=cost,
        cost_source=cost_source or "priced",
        latency_ms=response.latency_ms,
        final_action=final_action,
        outcome=outcome,
        reasons=reasons,
        error_type=reasons[0] if outcome == "error" else None,
        http_status=response.http_status,
        error_code=response.error_code,
        error_message=_scrubbed(error_detail or response.error, scrub_values),
        request_id=response.request_id,
        # A transport failure left nothing usable behind; an invalid or
        # refused response still keeps its raw text for the audit.
        raw_response=response.raw if response.error is None else None,
        backfilled=False,
    )


def apply_final_action(call: LlmCall, model_action: str | None, final_action: str) -> None:
    """Fold the gates' final action into a stored call row.

    Gate changes (blocked, deferred, a tighten-stop no-op) land on the
    linked row after the model call was recorded; this renames the row's
    action and refreshes ``changed_by_code`` inside the same write. Error
    rows never change: there was no usable answer to gate.
    """
    call.final_action = final_action
    if call.outcome == "error":
        return
    warnings = [r for r in (call.reasons or []) if r != "changed_by_code"]
    if model_action is not None and final_action != model_action:
        warnings.insert(0, "changed_by_code")
    call.reasons = warnings
    call.outcome = "warning" if warnings else "success"


def sync_llm_call(session: Session, linked: Decision | ShadowDecision) -> None:
    """Refresh a call row after the gates settled the linked row's action.

    The gates mutate ``final_action`` on the decision row after the model
    call's row was written; this brings the call row in line inside the
    same transaction (blocked, deferred, a tighten-stop no-op — and
    restores it when nothing changed).
    """
    from app.db.models import Decision

    key = (
        LlmCall.decision_id
        if isinstance(linked, Decision)
        else LlmCall.shadow_decision_id
    )
    call = session.execute(
        select(LlmCall).where(key == linked.id)
    ).scalar_one_or_none()
    if call is None or linked.final_action == call.final_action:
        return
    validation = linked.validation_result or {}
    model_action = validation.get("model_action")
    apply_final_action(
        call,
        model_action if isinstance(model_action, str) else None,
        linked.final_action,
    )
