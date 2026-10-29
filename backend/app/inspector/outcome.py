"""The call outcome rule (docs/SPEC.md, LLM call history — Outcome).

One pure function names a call's outcome and reasons. It is used at write
time — live, shadow and review rows — and by the migration backfill, so a
stored call always means the same thing. The outcome is set once when the
row is written and never recomputed.

Outcomes: ``success`` (a valid response used as the model chose),
``warning`` (a valid response, but something needs a look) and ``error``
(no usable response). An error outranks every warning; a call can carry
several warning reasons.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OUTCOMES = ("success", "warning", "error")

WARNING_REASONS = (
    "changed_by_code",
    "input_incomplete",
    "number_check_failed",
    "unsupported_claim",
    "slow",
)
ERROR_REASONS = ("timeout", "api_error", "invalid_output", "refusal")

# Snapshot values the data layer writes when a field could not be fetched.
UNAVAILABLE = "unavailable"
INCOMPLETE_NEWS = ("truncated", "failed")

# Fields that are absent by design rather than because input was missing:
# fixed mode never ranks, and the synthetic review facts carry no market
# inputs. Everything else that says "unavailable" is real missing input.
_IGNORED_KEYS = {"rank"}


@dataclass(frozen=True)
class CallFacts:
    """Everything the outcome rule reads about one call."""

    # Transport classification of a failed call: "timeout" | "api_error".
    error_type: str | None = None
    # The structured-output refusal field was set on the response.
    refused: bool = False
    # A response came back but was unparseable or failed schema/Pydantic.
    output_invalid: bool = False
    # What the model chose vs what the code finally did (after checks,
    # gates and a deferred exit). Equal actions with a gate-reduced size
    # are not a warning.
    model_action: str | None = None
    final_action: str | None = None
    input_incomplete: bool = False
    # Review text failed the number check; the plain template was used.
    number_check_failed: bool = False
    # decision.v2: a level id or decimal in the explanation matched
    # nothing in the snapshot or the chosen option.
    unsupported_claim: bool = False
    latency_ms: int | None = None
    # inspector.slow_call_seconds converted to ms; None disables the check.
    slow_ms: int | None = None


def classify(facts: CallFacts) -> tuple[str, list[str]]:
    """Return ``(outcome, reasons)`` for one call."""
    errors = [
        reason
        for reason in ERROR_REASONS
        if facts.error_type == reason
        or (reason == "invalid_output" and facts.output_invalid)
        or (reason == "refusal" and facts.refused)
    ]
    if errors:
        return "error", errors

    warnings: list[str] = []
    if (
        facts.model_action is not None
        and facts.final_action is not None
        and facts.model_action != facts.final_action
    ):
        warnings.append("changed_by_code")
    if facts.input_incomplete:
        warnings.append("input_incomplete")
    if facts.number_check_failed:
        warnings.append("number_check_failed")
    if facts.unsupported_claim:
        warnings.append("unsupported_claim")
    if (
        facts.latency_ms is not None
        and facts.slow_ms is not None
        and facts.latency_ms > facts.slow_ms
    ):
        warnings.append("slow")
    if warnings:
        return "warning", warnings
    return "success", []


def _has_unavailable(value: Any, ignored: frozenset[str]) -> bool:
    if isinstance(value, dict):
        return any(
            _has_unavailable(child, ignored)
            for key, child in value.items()
            if key not in ignored
        )
    if isinstance(value, list):
        return any(_has_unavailable(child, ignored) for child in value)
    return bool(value == UNAVAILABLE)


def input_incomplete(snapshot: object) -> bool:
    """True when the stored input snapshot had missing inputs.

    A field that is ``"unavailable"`` or headlines that were ``truncated``
    or ``failed`` make the input incomplete (docs/SPEC.md, Outcome).
    """
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("news_status") in INCOMPLETE_NEWS:
        return True
    return _has_unavailable(snapshot, frozenset(_IGNORED_KEYS))
