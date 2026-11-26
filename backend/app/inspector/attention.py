"""The attention level of one stored call (docs/SPEC.md → LLM call
history → Does it need me?; item 074).

``none`` · ``watch`` · ``act`` is decided once, when the call row is
written, by one pure rule set over the call, the input it was sent and
the earlier calls it needs — same session, the model's last 20 calls,
the last 5 reviews of one kind. It is stored as ``llm_calls.attention``
and never recomputed.

The rule numbers are the spec's fixed thresholds.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import LlmCall

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

NONE = "none"
WATCH = "watch"
ACT = "act"
ATTENTION_LEVELS = (NONE, WATCH, ACT)

# Spec thresholds (docs/SPEC.md → Does it need me?).
ACT_429_PER_SESSION = 3
ACT_ERROR_CYCLES = 3
ACT_INCOMPLETE_CYCLES = 3
ACT_BAD_OF_LAST = 3
ACT_BAD_WINDOW = 20
ACT_NUMBER_CHECK_OF = 3
ACT_NUMBER_CHECK_WINDOW = 5
WATCH_ERROR_CYCLE_GAP = 2
WATCH_SLOW_PER_SESSION = 3
WATCH_CLAIM_SHARE = 0.20
WATCH_CLAIM_WINDOW = 20

_CLAIM_WARNINGS = (
    "unsupported_claim",
    "unresolved_conflict",
    "confidence_outlier",
)
_NY = ZoneInfo("America/New_York")


def _reasons(call: LlmCall) -> list[str]:
    reasons = call.reasons
    return [str(r) for r in reasons] if isinstance(reasons, list) else []


def _has(call: LlmCall, reason: str) -> bool:
    return reason in _reasons(call)


def _is_timeout_or_5xx(call: LlmCall) -> bool:
    if _has(call, "timeout"):
        return True
    return _has(call, "api_error") and (
        call.http_status is not None and 500 <= call.http_status <= 599
    )


def _is_provider_error(call: LlmCall) -> bool:
    """A timeout or an api_error of any status — the watch rule's
    "second timeout or provider error"."""
    return _has(call, "timeout") or _has(call, "api_error")


def _cycle_streak(
    calls: list[LlmCall],
    call: LlmCall,
    predicate: Callable[[LlmCall], bool],
) -> int:
    """Consecutive cycles, ending at the call's own, that each hold at
    least one call satisfying ``predicate``.

    ``calls`` are the same session/source/kind rows with a cycle, oldest
    first; cycle ids order chronologically within a session. A cycle
    with no matching call breaks the streak.
    """
    by_cycle: dict[int, bool] = {}
    for row in calls:
        if row.cycle_id is None:
            continue
        by_cycle[row.cycle_id] = by_cycle.get(row.cycle_id, False) or predicate(row)
    if call.cycle_id is None or call.cycle_id not in by_cycle:
        return 0
    streak = 0
    for cycle_id in sorted(
        (c for c in by_cycle if c <= call.cycle_id), reverse=True
    ):
        if not by_cycle[cycle_id]:
            break
        streak += 1
    return streak


def _incomplete_signature(payload: dict[str, Any] | None) -> str | None:
    """Which data was missing — the "same source" of the spec's rule.

    The grain is the top-level group of the unavailable fields (two
    cycles missing different fields of the same feed still count as the
    same source failing), plus the headline status.
    """
    if not isinstance(payload, dict):
        return None
    missing: set[str] = set()

    def walk(value: Any, path: str) -> None:
        if value == "unavailable":
            missing.add(path)
        elif isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(payload, "")
    news = payload.get("news_status")
    if news in ("truncated", "failed"):
        missing.add(f"news_status.{news}")
    if not missing:
        return None
    return ",".join(sorted({m.split(".")[0].split("[")[0] for m in missing}))


def _held_overnight(payload: dict[str, Any] | None, session_date: date) -> bool:
    """hold_below_breakeven on a position held overnight: the stored
    snapshot's position was opened on an earlier New York date."""
    position = payload.get("position") if isinstance(payload, dict) else None
    if not isinstance(position, dict):
        return False
    opened = position.get("opened_at") or position.get("opened")
    if not opened:
        return False
    try:
        dt = datetime.fromisoformat(str(opened).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(_NY).date() < session_date


def attention_for_call(
    session: Session,
    call: LlmCall,
    input_payload: dict[str, Any] | None = None,
) -> str:
    """The level to store on ``call`` — decided once, at write time.

    Only warnings and errors can leave ``none``; a success is always
    ``none``. ``act`` beats ``watch``. ``input_payload`` is the input the
    call was sent (the decision snapshot, the review facts), the same
    object the outcome rule read.
    """
    if call.outcome == "success":
        return NONE
    reasons = set(_reasons(call))
    session_date = call.session_date

    # The session maker runs with autoflush off: earlier calls added
    # earlier in this transaction are still pending and invisible to
    # the queries below until flushed (the commit happens later).
    session.flush()

    earlier = list(
        session.execute(
            select(LlmCall)
            .where(
                LlmCall.session_date == session_date,
                LlmCall.sent_at <= call.sent_at,
            )
            .order_by(LlmCall.sent_at, LlmCall.id)
        ).scalars()
    )
    earlier = [row for row in earlier if row.id != call.id]
    # The call itself may not be flushed yet; the "of the last N" rules
    # count it explicitly.
    same_session = [*earlier, call]

    # ---- act ------------------------------------------------------
    if "api_error" in reasons and call.http_status in (401, 403, 404):
        return ACT
    if "api_error" in reasons and call.http_status == 429:
        count = sum(
            1
            for row in same_session
            if _has(row, "api_error") and row.http_status == 429
        )
        if count >= ACT_429_PER_SESSION:
            return ACT
    if call.cycle_id is not None and _is_timeout_or_5xx(call):
        peers = [
            row
            for row in same_session
            if row.source == call.source
            and row.kind == call.kind
            and row.cycle_id is not None
        ]
        if _cycle_streak(peers, call, _is_timeout_or_5xx) >= ACT_ERROR_CYCLES:
            return ACT
    if "input_incomplete" in reasons and call.cycle_id is not None:
        # The signature reads the same shape for this call and its
        # earlier peers: the sent copy when the row kept it.
        signature = _incomplete_signature(
            call.sent_input
            if isinstance(call.sent_input, dict)
            else input_payload
        )
        if signature is not None:
            peers = [
                row
                for row in same_session
                if row.source == call.source
                and row.kind == call.kind
                and row.cycle_id is not None
                and _has(row, "input_incomplete")
            ]
            if (
                _cycle_streak(
                    peers,
                    call,
                    lambda row: _has(row, "input_incomplete")
                    and _signature_of(row) == signature,
                )
                >= ACT_INCOMPLETE_CYCLES
            ):
                return ACT
    if reasons & {"invalid_output", "refusal"} and call.model is not None:
        model_calls = [
            row
            for row in session.execute(
                select(LlmCall)
                .where(LlmCall.model == call.model)
                .order_by(LlmCall.sent_at.desc(), LlmCall.id.desc())
                .limit(ACT_BAD_WINDOW)
            ).scalars()
            if row.id != call.id
        ]
        recent = [call, *model_calls][:ACT_BAD_WINDOW]
        bad = sum(
            1
            for row in recent
            if _has(row, "invalid_output") or _has(row, "refusal")
        )
        if bad >= ACT_BAD_OF_LAST:
            return ACT
    if "number_check_failed" in reasons:
        # The last 5 reviews of this kind across sessions — reviews are
        # daily/weekly, not cycle-bound (docs/SPEC.md → Does it need me?).
        review_calls = [
            call,
            *(
                row
                for row in session.execute(
                    select(LlmCall)
                    .where(
                        LlmCall.source == "review",
                        LlmCall.kind == call.kind,
                    )
                    .order_by(LlmCall.sent_at.desc(), LlmCall.id.desc())
                    .limit(ACT_NUMBER_CHECK_WINDOW)
                ).scalars()
                if row.id != call.id
            ),
        ][:ACT_NUMBER_CHECK_WINDOW]
        if len(review_calls) >= ACT_NUMBER_CHECK_WINDOW and sum(
            1 for row in review_calls if _has(row, "number_check_failed")
        ) >= ACT_NUMBER_CHECK_OF:
            return ACT
    if "hold_below_breakeven" in reasons and _held_overnight(
        input_payload, session_date
    ):
        return ACT

    # ---- watch -----------------------------------------------------
    if _is_provider_error(call):
        if call.cycle_id is not None:
            near = any(
                row.cycle_id is not None
                and row.cycle_id < call.cycle_id
                and call.cycle_id - row.cycle_id <= WATCH_ERROR_CYCLE_GAP
                and row.source == call.source
                and row.kind == call.kind
                and _is_provider_error(row)
                for row in earlier
            )
        else:
            near = any(
                row.source == call.source
                and row.kind == call.kind
                and _is_provider_error(row)
                for row in earlier
            )
        if near:
            return WATCH
    if _has(call, "slow"):
        slow_count = sum(1 for row in same_session if _has(row, "slow"))
        if slow_count >= WATCH_SLOW_PER_SESSION:
            return WATCH
    if reasons & set(_CLAIM_WARNINGS) and call.kind == "decision":
        decision_calls = [
            row
            for row in session.execute(
                select(LlmCall)
                .where(LlmCall.kind == "decision")
                .order_by(LlmCall.sent_at.desc(), LlmCall.id.desc())
                .limit(WATCH_CLAIM_WINDOW)
            ).scalars()
            if row.id != call.id
        ]
        recent = [call, *decision_calls][:WATCH_CLAIM_WINDOW]
        flagged = sum(
            1
            for row in recent
            if set(_reasons(row)) & set(_CLAIM_WARNINGS)
        )
        if flagged / len(recent) >= WATCH_CLAIM_SHARE:
            return WATCH

    return NONE


def _signature_of(call: LlmCall) -> str | None:
    """The missing-source signature of an earlier stored call — read
    from its ``sent_input`` (decision calls) or, failing that, its
    linked row is not consulted: rows without a stored input simply
    never match a signature."""
    payload = call.sent_input
    return _incomplete_signature(payload) if isinstance(payload, dict) else None
