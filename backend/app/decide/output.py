"""Model output validation and code checks (docs/SPEC.md, Decision layer).

The model answers with strict JSON validated by Pydantic. Code then checks
what the model may never decide for itself: the stop sits 1.5–4 × the 1h ATR
below the current price, reward to risk is at least the configured minimum,
confidence is at least the configured minimum for ``enter``, and a
``tighten_stop`` only raises the stop. Invalid or unparseable output counts
as ``skip`` for a candidate and ``hold`` for a held position; the raw text is
kept on the decision row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import AppConfig
from app.risk.math import trade_risk

CANDIDATE_ACTIONS = {"enter", "skip"}
HELD_ACTIONS = {"hold", "tighten_stop", "exit"}

FALLBACK = {"candidate": "skip", "held": "hold"}

# Reasons a decision was stored without a usable model answer.
NO_CALL_REASONS = {
    "rules", "call_cap", "price_stale", "price_suspect", "model_error",
}

# OpenAI structured-outputs schema for the decision object (strict mode).
# Mirrors ModelOutput; Pydantic still validates the returned JSON.
DECISION_JSON_SCHEMA: dict[str, Any] = {
    "name": "decision",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "symbol": {"type": "string"},
            "action": {
                "type": "string",
                "enum": ["enter", "skip", "hold", "tighten_stop", "exit"],
            },
            "confidence": {"type": "number"},
            "stop_price": {"type": ["number", "null"]},
            "target_price": {"type": ["number", "null"]},
            "thesis": {"type": "string"},
            "invalidation": {"type": ["string", "null"]},
            "risks": {"type": "array", "items": {"type": "string"}},
            "news_used": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "symbol",
            "action",
            "confidence",
            "stop_price",
            "target_price",
            "thesis",
            "invalidation",
            "risks",
            "news_used",
        ],
    },
}


class ModelOutput(BaseModel):
    """The JSON object the prompt requires; unknown fields are invalid."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    action: Literal["enter", "skip", "hold", "tighten_stop", "exit"]
    confidence: float = Field(ge=0.0, le=1.0)
    stop_price: Decimal | None = None
    target_price: Decimal | None = None
    thesis: str = ""
    invalidation: str | None = None
    risks: list[str] = Field(default_factory=list)
    news_used: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class CodeCheck:
    name: str
    passed: bool
    detail: str
    value: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of parsing + code checks on one raw model response."""

    valid: bool  # parsed, allowed and well-formed for the action
    output: ModelOutput | None
    model_action: str | None  # action the model chose (None when unparseable)
    final_action: str  # action code allows: may differ from the model's
    checks: list[CodeCheck] = field(default_factory=list)
    error: str | None = None  # why the output was invalid
    note: str = ""  # one-line summary for the inspector banner

    @property
    def changed_by_code(self) -> bool:
        return self.model_action is not None and self.final_action != self.model_action


def _fmt(value: Decimal | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _invalid(
    context: str, error: str, output: ModelOutput | None = None
) -> ValidationResult:
    return ValidationResult(
        valid=False,
        output=output,
        model_action=output.action if output else None,
        final_action=FALLBACK[context],
        error=error,
        note=f"Invalid output — {error}",
    )


def validate_model_output(
    raw: str | None,
    *,
    symbol: str,
    context: str,
    price: Decimal | None,
    atr_1h: Decimal | None,
    current_stop: Decimal | None,
    call_error: str | None = None,
    config: AppConfig,
) -> ValidationResult:
    """Parse ``raw`` and run the spec's code checks for the given context."""
    allowed = CANDIDATE_ACTIONS if context == "candidate" else HELD_ACTIONS
    min_conf = float(config.get("decision", "min_confidence_enter", default=0.55))
    atr_min = Decimal(str(config.get("decision", "stop_atr_min", default=1.5)))
    atr_max = Decimal(str(config.get("decision", "stop_atr_max", default=4.0)))
    min_rr = Decimal(str(config.get("decision", "min_reward_risk", default=1.5)))

    if call_error is not None:
        return _invalid(context, f"model call failed: {call_error}")
    if raw is None or not raw.strip():
        return _invalid(context, "empty response")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _invalid(context, f"not JSON: {exc.msg}")

    try:
        output = ModelOutput.model_validate(parsed)
    except ValidationError as exc:
        return _invalid(context, f"schema: {exc.errors()[0]['msg']}")

    if output.symbol != symbol:
        return _invalid(context, f"symbol mismatch: {output.symbol!r}", output)
    if output.action not in allowed:
        return _invalid(
            context,
            f"action {output.action!r} not allowed for {context}",
            output,
        )

    json_check = CodeCheck(
        "Valid JSON, allowed action", passed=True, detail="", value=output.action
    )

    if output.action == "enter":
        checks: list[CodeCheck] = []
        risk = (
            trade_risk(price, output.stop_price, output.target_price, atr_1h)
            if price is not None
            and output.stop_price is not None
            and output.target_price is not None
            else None
        )
        stop_ok = (
            risk is not None
            and risk.valid
            and risk.atr_multiple is not None
            and atr_min <= risk.atr_multiple <= atr_max
        )
        if risk is not None and risk.atr_multiple is not None:
            detail = (
                f"{_fmt(price)} − {_fmt(output.stop_price)} = "
                f"{_fmt(risk.stop_distance)} · ATR {_fmt(atr_1h)}"
            )
            atr_value = f"{_fmt(risk.atr_multiple)} × ATR"
        else:
            detail = "stop missing, above price or price/ATR unavailable"
            atr_value = "—"
        checks.append(
            CodeCheck(
                f"Stop below price, {_fmt(atr_min)}–{_fmt(atr_max)} × ATR",
                passed=stop_ok,
                detail=detail,
                value=atr_value,
            )
        )

        rr_ok = (
            risk is not None
            and risk.reward_to_risk is not None
            and risk.reward_to_risk >= min_rr
        )
        rr_detail = (
            f"({_fmt(output.target_price)} − {_fmt(price)}) / {_fmt(risk.stop_distance)}"
            if risk is not None and risk.stop_distance > 0
            else "target or stop missing"
        )
        checks.append(
            CodeCheck(
                f"Reward to risk ≥ {_fmt(min_rr)}",
                passed=rr_ok,
                detail=rr_detail,
                value=(
                    _fmt(risk.reward_to_risk)
                    if risk is not None and risk.reward_to_risk is not None
                    else "—"
                ),
            )
        )

        conf_ok = output.confidence >= min_conf
        checks.append(
            CodeCheck(
                f"Confidence ≥ {_fmt(Decimal(str(min_conf)))} for enter",
                passed=conf_ok,
                detail="",
                value=_fmt(_decimal_or_none(output.confidence)),
            )
        )
        checks.append(json_check)

        if all(c.passed for c in checks):
            return ValidationResult(
                valid=True,
                output=output,
                model_action="enter",
                final_action="enter",
                checks=checks,
                note="Valid · passed all checks",
            )
        first_fail = next(c for c in checks if not c.passed)
        return ValidationResult(
            valid=True,
            output=output,
            model_action="enter",
            final_action="skip",
            checks=checks,
            note=f"Valid output · code changed enter → skip ({first_fail.name})",
        )

    if output.action == "tighten_stop":
        raises = (
            output.stop_price is not None
            and current_stop is not None
            and output.stop_price > current_stop
        )
        check = CodeCheck(
            "Tighten stop only raises the stop",
            passed=raises,
            detail=(
                f"{_fmt(output.stop_price)} > {_fmt(current_stop)}"
                if output.stop_price is not None and current_stop is not None
                else "new stop or current stop missing"
            ),
            value=_fmt(output.stop_price),
        )
        margin = Decimal(str(config.get("decision", "tighten_min_atr_below_price")))
        distance_ok = (
            price is not None and atr_1h is not None and atr_1h > 0
            and output.stop_price is not None
            and price - output.stop_price >= margin * atr_1h
        )
        distance = CodeCheck(
            "Tighten stop market distance", passed=distance_ok,
            detail="passed" if distance_ok else (
                "reference_missing" if price is None or atr_1h is None
                else "tighten_market_distance"
            ), value=_fmt(output.stop_price),
        )
        checks = [check, distance, json_check]
        if raises and distance_ok:
            return ValidationResult(
                valid=True,
                output=output,
                model_action="tighten_stop",
                final_action="tighten_stop",
                checks=checks,
                note="Valid · passed all checks",
            )
        return ValidationResult(
            valid=True,
            output=output,
            model_action="tighten_stop",
            final_action="hold",
            checks=checks,
            note="Valid output · code changed tighten_stop → hold (stop not raised)",
        )

    # skip / hold / exit need no further checks.
    return ValidationResult(
        valid=True,
        output=output,
        model_action=output.action,
        final_action=output.action,
        checks=[json_check],
        note="Valid",
    )
