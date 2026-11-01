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
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import AppConfig
from app.risk.math import trade_risk

CANDIDATE_ACTIONS = {"enter", "skip"}
HELD_ACTIONS = {"hold", "tighten_stop", "exit"}

FALLBACK = {"candidate": "skip", "held": "hold"}

# Reasons a decision was stored without a usable model answer.
NO_CALL_REASONS = {
    "rules", "call_cap", "price_stale", "price_suspect", "model_error",
    "no_options", "reference_missing", "checks",
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


# decision.v2: the model picks one code-built option or skips; it never
# writes a price (docs/SPEC.md, Decision v2 — Model output).
DECISION_V2_JSON_SCHEMA: dict[str, Any] = {
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
            "option_id": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
            "thesis": {"type": "string"},
            "levels_used": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "news_used": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "symbol",
            "action",
            "option_id",
            "confidence",
            "thesis",
            "levels_used",
            "risks",
            "news_used",
        ],
    },
}


class ModelOutputV2(BaseModel):
    """The v2 decision JSON: an option id instead of prices."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    action: Literal["enter", "skip", "hold", "tighten_stop", "exit"]
    option_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str = ""
    levels_used: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    news_used: list[str] = Field(default_factory=list)


# decision.v3: conflicts and data flags before the decision fields
# (docs/SPEC.md, Decision v3 — Output schema). ``conflicts`` takes the
# place of v2's ``risks`` in storage and in the drawer.
DATA_FLAGS = ("earnings_date_conflict", "price_conflict", "stale_or_missing_data")

DECISION_V3_JSON_SCHEMA: dict[str, Any] = {
    "name": "decision",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "symbol": {"type": "string"},
            "conflicts": {"type": "array", "items": {"type": "string"}},
            "data_flags": {
                "type": "array",
                "items": {"type": "string", "enum": list(DATA_FLAGS)},
            },
            "thesis": {"type": "string"},
            "action": {
                "type": "string",
                "enum": ["enter", "skip", "hold", "tighten_stop", "exit"],
            },
            "option_id": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
            "levels_used": {"type": "array", "items": {"type": "string"}},
            "news_used": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "symbol",
            "conflicts",
            "data_flags",
            "thesis",
            "action",
            "option_id",
            "confidence",
            "levels_used",
            "news_used",
        ],
    },
}


class ModelOutputV3(BaseModel):
    """The v3 decision JSON (docs/SPEC.md, Decision v3 — Output schema)."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    conflicts: list[str] = Field(default_factory=list, max_length=5)
    data_flags: list[
        Literal["earnings_date_conflict", "price_conflict", "stale_or_missing_data"]
    ] = Field(default_factory=list)
    thesis: str = ""
    action: Literal["enter", "skip", "hold", "tighten_stop", "exit"]
    option_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    levels_used: list[str] = Field(default_factory=list)
    news_used: list[str] = Field(default_factory=list)


# decision.v4: conflicts say what outweighs them, confidence is defined
# for every action and a skip names its best option (docs/SPEC.md,
# Decision v4 — Output schema).
DECISION_V4_JSON_SCHEMA: dict[str, Any] = {
    "name": "decision",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "symbol": {"type": "string"},
            "conflicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "text": {"type": "string"},
                        "outweighed_by": {"type": ["string", "null"]},
                    },
                    "required": ["text", "outweighed_by"],
                },
            },
            "data_flags": {
                "type": "array",
                "items": {"type": "string", "enum": list(DATA_FLAGS)},
            },
            "thesis": {"type": "string"},
            "what_changed": {"type": ["string", "null"]},
            "action": {
                "type": "string",
                "enum": ["enter", "skip", "hold", "tighten_stop", "exit"],
            },
            "option_id": {"type": ["string", "null"]},
            "best_option_id": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
            "levels_used": {"type": "array", "items": {"type": "string"}},
            "news_used": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "effect": {"type": "string"},
                    },
                    "required": ["id", "effect"],
                },
            },
        },
        "required": [
            "symbol",
            "conflicts",
            "data_flags",
            "thesis",
            "what_changed",
            "action",
            "option_id",
            "best_option_id",
            "confidence",
            "levels_used",
            "news_used",
        ],
    },
}


class V4Conflict(BaseModel):
    """One conflict with what outweighs it (null = nothing does)."""

    model_config = ConfigDict(extra="forbid")

    text: str
    outweighed_by: str | None = None


class V4NewsUsed(BaseModel):
    """One cited headline with its effect (at most 12 words)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    effect: str

    @field_validator("effect")
    @classmethod
    def _word_limit(cls, value: str) -> str:
        if len(value.split()) > 12:
            raise ValueError("effect over 12 words")
        return value


class ModelOutputV4(BaseModel):
    """The v4 decision JSON (docs/SPEC.md, Decision v4 — Output schema)."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    conflicts: list[V4Conflict] = Field(default_factory=list, max_length=5)
    data_flags: list[
        Literal["earnings_date_conflict", "price_conflict", "stale_or_missing_data"]
    ] = Field(default_factory=list)
    thesis: str = ""
    what_changed: str | None = None
    action: Literal["enter", "skip", "hold", "tighten_stop", "exit"]
    option_id: str | None = None
    best_option_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    levels_used: list[str] = Field(default_factory=list)
    news_used: list[V4NewsUsed] = Field(default_factory=list, max_length=5)


@dataclass(frozen=True)
class ExplanationCheck:
    """Result of the v2 explanation check (docs/SPEC.md, Decision v2).

    Every level id in ``levels_used``, ``thesis`` and ``risks`` must exist
    in the snapshot; every decimal number in the text must match a number
    in the snapshot or the chosen option after rounding to the text's
    precision (whole numbers are ignored — they are mostly indicator names
    and dates). A failure never changes the decision.
    """

    passed: bool
    bad_ids: list[str] = field(default_factory=list)
    bad_numbers: list[str] = field(default_factory=list)
    # decision.v4: which claim kinds fired ("level", "locks_in", "path");
    # empty on v1–v3 rows.
    kinds: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out = {
            "passed": self.passed,
            "bad_ids": self.bad_ids,
            "bad_numbers": self.bad_numbers,
        }
        if self.kinds:
            out["kinds"] = self.kinds
        return out


def _decimal_tokens(text: str) -> list[str]:
    """Decimal literals (numbers with a fractional part) in free text.

    Dates and times are masked out; whole numbers are skipped by spec.
    """
    from app.reviews.numbercheck import _SEGMENT, _mask_dates

    out: list[str] = []
    for match in _SEGMENT.finditer(_mask_dates(text)):
        literal = match.group(0)
        if "." in literal:
            out.append(literal)
    return out


def check_explanation(
    output: ModelOutputV2,
    snapshot: dict[str, Any],
    chosen: dict[str, Any] | None,
) -> ExplanationCheck:
    """Verify level ids and decimal claims against the given input."""
    return _check_explanation(
        [output.thesis, *output.risks], output.levels_used, snapshot, chosen
    )


def check_explanation_v3(
    output: ModelOutputV3,
    snapshot: dict[str, Any],
    chosen: dict[str, Any] | None,
) -> ExplanationCheck:
    """The v3 explanation check: thesis and conflicts against the rounded
    v3 snapshot (docs/SPEC.md, Decision v3 — Code checks)."""
    return _check_explanation(
        [output.thesis, *output.conflicts], output.levels_used, snapshot, chosen
    )


def _check_explanation(
    text_parts: list[str],
    levels_used: list[str],
    snapshot: dict[str, Any],
    chosen: dict[str, Any] | None,
) -> ExplanationCheck:
    """Verify level ids and decimal claims against the given input."""
    from app.reviews.numbercheck import fact_values

    levels = snapshot.get("levels") or []
    known_ids = {
        str(lv.get("id"))
        for lv in levels
        if isinstance(lv, dict) and lv.get("id") is not None
    }
    mentioned = set(levels_used)
    for part in text_parts:
        mentioned.update(re.findall(r"\bL\d+\b", part))
    bad_ids = sorted(mentioned - known_ids)

    allowed = list(fact_values(snapshot)) + list(fact_values(chosen or {}))
    bad_numbers: list[str] = []
    for part in text_parts:
        for literal in _decimal_tokens(part):
            n = Decimal(literal.replace(",", ""))
            precision = len(literal.rsplit(".", 1)[1])
            tolerance = Decimal(1).scaleb(-precision) / 2
            if not any(abs(abs(v) - n) <= tolerance for v in allowed):
                bad_numbers.append(literal)
    return ExplanationCheck(
        passed=not bad_ids and not bad_numbers,
        bad_ids=bad_ids,
        bad_numbers=bad_numbers,
    )


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
    output: ModelOutput | ModelOutputV2 | ModelOutputV3 | ModelOutputV4 | None
    model_action: str | None  # action the model chose (None when unparseable)
    final_action: str  # action code allows: may differ from the model's
    checks: list[CodeCheck] = field(default_factory=list)
    error: str | None = None  # why the output was invalid
    note: str = ""  # one-line summary for the inspector banner
    # decision.v3: why code changed an allowed action (edge_too_small,
    # data_flag) — the machine-readable skip reason.
    reason: str | None = None
    # decision.v2: the offered option the model picked (stored verbatim,
    # code never moves prices) and the explanation check result.
    chosen_option: dict[str, Any] | None = None
    explanation: ExplanationCheck | None = None
    # decision.v3: claim checks (locks-in and stop/target level claims)
    claims: ExplanationCheck | None = None
    # decision.v3: the stop/target basis line code writes next to the
    # thesis ("stop 409.64, 0.1 ATR below the 13 Oct 14:30 1h swing low
    # (L8) · target 435.92, 2R").
    code_written: str | None = None
    # decision.v4: warning reasons stored and counted, never gating
    # ("confidence_outlier", "unresolved_conflict") and the anchor the
    # confidence was judged against (break-even or neutral probability).
    warnings: list[str] = field(default_factory=list)
    confidence_anchor: float | None = None

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


def _breakeven_guard(
    *,
    new_stop: Decimal | None,
    position: Any,
    price: Decimal | None,
    min_r: Decimal,
) -> str | None:
    """The break-even guard (docs/SPEC.md, Decision layer — Code checks).

    A tighten to a stop at or below the entry locks in 0 R or less;
    while the position is less than ``decision.breakeven_min_r`` in
    profit the action becomes ``hold`` with reason
    ``breakeven_too_early``. Returns that reason when the guard refuses
    the tighten, else None. Applies to every prompt version.
    """
    if new_stop is None or not isinstance(position, dict):
        return None
    entry = _decimal_or_none(position.get("entry"))
    if entry is None or new_stop > entry:
        return None
    r_now = _decimal_or_none(position.get("r_now"))
    if r_now is None:
        r_now = _decimal_or_none(position.get("r_so_far"))
    if r_now is None:
        initial = _decimal_or_none(position.get("initial_stop"))
        if price is not None and initial is not None and entry > initial:
            r_now = (price - entry) / (entry - initial)
    if r_now is not None and r_now >= min_r:
        return None
    return "breakeven_too_early"


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
    position: dict[str, Any] | None = None,
) -> ValidationResult:
    """Parse ``raw`` and run the spec's code checks for the given context."""
    allowed = CANDIDATE_ACTIONS if context == "candidate" else HELD_ACTIONS
    min_conf = float(config.get("decision", "min_confidence_enter", default=0.55))
    atr_min = Decimal(str(config.get("decision", "stop_atr_min", default=1.5)))
    atr_max = Decimal(str(config.get("decision", "stop_atr_max", default=4.0)))
    min_rr = Decimal(str(config.get("decision", "min_reward_risk", default=1.5)))
    min_r = Decimal(str(config.get("decision", "breakeven_min_r", default=1.0)))

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
        guard_reason = _breakeven_guard(
            new_stop=output.stop_price,
            position=position,
            price=price,
            min_r=min_r,
        )
        guard = CodeCheck(
            "Break-even guard",
            passed=guard_reason is None,
            detail=guard_reason or "stop above entry or enough profit",
            value=_fmt(output.stop_price),
        )
        checks = [check, distance, guard, json_check]
        if raises and distance_ok and guard_reason is None:
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
            reason=guard_reason,
            note=(
                "Valid output · code changed tighten_stop → hold "
                f"({guard_reason or 'stop not raised'})"
            ),
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


def _option_by_id(options: list[dict[str, Any]], option_id: str | None) -> dict[str, Any] | None:
    if option_id is None:
        return None
    return next(
        (o for o in options if str(o.get("id")) == option_id), None
    )


def validate_model_output_v2(
    raw: str | None,
    *,
    symbol: str,
    context: str,
    snapshot: dict[str, Any],
    atr_1h: Decimal | None,
    current_stop: Decimal | None,
    call_error: str | None = None,
    config: AppConfig,
) -> ValidationResult:
    """Parse a decision.v2 answer: the model picks a code-built option id.

    An ``option_id`` that was not offered (or is missing for ``enter`` /
    ``tighten_stop``) is invalid output — ``skip`` / ``hold``, same as v1.
    The chosen option's stop distance and reward to risk are checked again
    against the snapshot's reference and entry limit, then confidence.
    The explanation check never changes the action.
    """
    allowed = CANDIDATE_ACTIONS if context == "candidate" else HELD_ACTIONS
    min_conf = float(config.get("decision", "min_confidence_enter", default=0.55))
    atr_min = Decimal(str(config.get("decision", "stop_atr_min", default=1.5)))
    atr_max = Decimal(str(config.get("decision", "stop_atr_max", default=4.0)))
    min_rr = Decimal(str(config.get("decision", "min_reward_risk", default=1.5)))

    def invalid(error: str, output: ModelOutputV2 | None = None) -> ValidationResult:
        return ValidationResult(
            valid=False,
            output=output,
            model_action=output.action if output else None,
            final_action=FALLBACK[context],
            error=error,
            note=f"Invalid output — {error}",
        )

    if call_error is not None:
        return invalid(f"model call failed: {call_error}")
    if raw is None or not raw.strip():
        return invalid("empty response")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return invalid(f"not JSON: {exc.msg}")
    try:
        output = ModelOutputV2.model_validate(parsed)
    except ValidationError as exc:
        return invalid(f"schema: {exc.errors()[0]['msg']}")
    if output.symbol != symbol:
        return invalid(f"symbol mismatch: {output.symbol!r}", output)
    if output.action not in allowed:
        return invalid(
            f"action {output.action!r} not allowed for {context}", output
        )

    json_check = CodeCheck(
        "Valid JSON, allowed action", passed=True, detail="", value=output.action
    )
    options = [
        o for o in (snapshot.get("options") or [])
        if isinstance(o, dict)
    ] if context == "candidate" else [
        o for o in (snapshot.get("stop_options") or [])
        if isinstance(o, dict)
    ]
    chosen = _option_by_id(options, output.option_id)

    if output.action == "enter":
        checks: list[CodeCheck] = []
        option_ok = chosen is not None
        checks.append(
            CodeCheck(
                f"Option {output.option_id or '—'} offered",
                passed=option_ok,
                detail=(
                    "valid choice"
                    if option_ok
                    else f"option {output.option_id!r} was not offered"
                ),
                value=output.option_id,
            )
        )
        if not option_ok:
            return ValidationResult(
                valid=False,
                output=output,
                model_action="enter",
                final_action="skip",
                checks=[json_check, *checks],
                error=f"invalid option_id {output.option_id!r}",
                note="Invalid output — option id was not offered",
            )
        assert chosen is not None  # noqa: S101 - internal invariant
        stop = _decimal_or_none(chosen.get("stop"))
        target = _decimal_or_none(chosen.get("target"))
        limit = _decimal_or_none(
            (snapshot.get("constraints") or {}).get("entry_limit")
        )
        reference = _decimal_or_none(snapshot.get("stop_reference_price"))
        entry = limit if limit is not None else _decimal_or_none(snapshot.get("price"))
        risk = trade_risk(entry, stop, target, atr_1h) if (
            entry is not None and stop is not None and target is not None
        ) else None
        stop_distance = (
            (reference - stop) if reference is not None and stop is not None else None
        )
        atr_multiple = (
            stop_distance / atr_1h
            if stop_distance is not None and atr_1h is not None and atr_1h > 0
            else None
        )
        stop_ok = (
            atr_multiple is not None and atr_min <= atr_multiple <= atr_max
        )
        checks.append(
            CodeCheck(
                f"Stop {_fmt(atr_min)}–{_fmt(atr_max)} × ATR (1h)",
                passed=stop_ok,
                detail=(
                    f"{_fmt(stop_distance)} = {_fmt(atr_multiple)} × ATR"
                    if atr_multiple is not None
                    else "reference or ATR missing"
                ),
                value=f"{_fmt(atr_multiple)} × ATR" if atr_multiple else "—",
            )
        )
        rr = risk.reward_to_risk if risk is not None else None
        rr_ok = rr is not None and rr >= min_rr
        checks.append(
            CodeCheck(
                f"Reward / risk ≥ {_fmt(min_rr)}",
                passed=rr_ok,
                detail=f"{_fmt(rr)}",
                value=_fmt(rr),
            )
        )
        conf_ok = output.confidence >= min_conf
        checks.append(
            CodeCheck(
                f"Confidence ≥ {_fmt(Decimal(str(min_conf)))}",
                passed=conf_ok,
                detail="",
                value=_fmt(_decimal_or_none(output.confidence)),
            )
        )
        explanation = check_explanation(output, snapshot, chosen)
        checks.append(
            CodeCheck(
                "Explanation check",
                passed=explanation.passed,
                detail=(
                    "no unsupported numbers"
                    if explanation.passed
                    else "; ".join(
                        filter(
                            None,
                            [
                                (
                                    "unknown ids "
                                    + ", ".join(explanation.bad_ids)
                                    if explanation.bad_ids
                                    else ""
                                ),
                                (
                                    "unsupported numbers "
                                    + ", ".join(explanation.bad_numbers)
                                    if explanation.bad_numbers
                                    else ""
                                ),
                            ],
                        )
                    )
                ),
                value=", ".join(output.levels_used) or None,
            )
        )
        checks.append(json_check)
        # The explanation check never changes the action (docs/SPEC.md).
        passed = all(c.passed for c in checks[:-1]) and all(
            c.passed for c in checks if c.name != "Explanation check"
        )
        passed = all(
            c.passed for c in checks if c.name != "Explanation check"
        )
        if passed:
            return ValidationResult(
                valid=True,
                output=output,
                model_action="enter",
                final_action="enter",
                checks=checks,
                note="Valid · passed all checks",
                chosen_option=chosen,
                explanation=explanation,
            )
        first_fail = next(
            c for c in checks if not c.passed and c.name != "Explanation check"
        )
        return ValidationResult(
            valid=True,
            output=output,
            model_action="enter",
            final_action="skip",
            checks=checks,
            note=(
                "Valid output · code changed enter → skip "
                f"({first_fail.name})"
            ),
            chosen_option=chosen,
            explanation=explanation,
        )

    if output.action == "tighten_stop":
        option_ok = chosen is not None
        new_stop = _decimal_or_none(chosen.get("stop")) if chosen else None
        raises = (
            new_stop is not None
            and current_stop is not None
            and new_stop > current_stop
        )
        checks = [
            CodeCheck(
                f"Option {output.option_id or '—'} offered",
                passed=option_ok,
                detail=(
                    "valid choice"
                    if option_ok
                    else f"option {output.option_id!r} was not offered"
                ),
                value=output.option_id,
            ),
            CodeCheck(
                "Tighten stop only raises the stop",
                passed=raises,
                detail=(
                    f"{_fmt(new_stop)} > {_fmt(current_stop)}"
                    if new_stop is not None and current_stop is not None
                    else "new stop or current stop missing"
                ),
                value=_fmt(new_stop),
            ),
        ]
        margin = Decimal(str(config.get("decision", "tighten_min_atr_below_price")))
        price = _decimal_or_none(snapshot.get("stop_reference_price"))
        distance_ok = (
            price is not None and atr_1h is not None and atr_1h > 0
            and new_stop is not None
            and price - new_stop >= margin * atr_1h
        )
        checks.append(
            CodeCheck(
                "Tighten stop market distance",
                passed=distance_ok,
                detail=(
                    "passed"
                    if distance_ok
                    else (
                        "reference_missing"
                        if price is None or atr_1h is None
                        else "tighten_market_distance"
                    )
                ),
                value=_fmt(new_stop),
            )
        )
        guard_reason = _breakeven_guard(
            new_stop=new_stop,
            position=snapshot.get("position"),
            price=price,
            min_r=Decimal(
                str(config.get("decision", "breakeven_min_r", default=1.0))
            ),
        )
        checks.append(
            CodeCheck(
                "Break-even guard",
                passed=guard_reason is None,
                detail=guard_reason or "stop above entry or enough profit",
                value=_fmt(new_stop),
            )
        )
        explanation = check_explanation(output, snapshot, chosen)
        checks.append(
            CodeCheck(
                "Explanation check",
                passed=explanation.passed,
                detail="no unsupported numbers" if explanation.passed else "failed",
            )
        )
        checks.append(json_check)
        if not option_ok:
            return ValidationResult(
                valid=False,
                output=output,
                model_action="tighten_stop",
                final_action="hold",
                checks=checks,
                error=f"invalid option_id {output.option_id!r}",
                note="Invalid output — option id was not offered",
                chosen_option=chosen,
                explanation=explanation,
            )
        if raises and distance_ok and guard_reason is None:
            return ValidationResult(
                valid=True,
                output=output,
                model_action="tighten_stop",
                final_action="tighten_stop",
                checks=checks,
                note="Valid · passed all checks",
                chosen_option=chosen,
                explanation=explanation,
            )
        return ValidationResult(
            valid=True,
            output=output,
            model_action="tighten_stop",
            final_action="hold",
            checks=checks,
            reason=guard_reason,
            note=(
                "Valid output · code changed tighten_stop → hold "
                f"({guard_reason or 'stop not raised'})"
            ),
            chosen_option=chosen,
            explanation=explanation,
        )

    # skip / hold / exit: still run the explanation check for the audit.
    explanation = check_explanation(output, snapshot, None)
    checks = [
        json_check,
        CodeCheck(
            "Explanation check",
            passed=explanation.passed,
            detail="no unsupported numbers" if explanation.passed else "failed",
        ),
    ]
    return ValidationResult(
        valid=True,
        output=output,
        model_action=output.action,
        final_action=output.action,
        checks=checks,
        note="Valid",
        explanation=explanation,
    )


_LOCKS_IN_RE = re.compile(r"\blocks?\s+in\b", re.IGNORECASE)
_LEVEL_ID_RE = re.compile(r"\bL\d+\b")
_SENTENCE_RE = re.compile(r"[^.!?\n]+")


def _code_written(
    context: str,
    snapshot: dict[str, Any],
    chosen: dict[str, Any] | None,
) -> str | None:
    """The stop and target basis code writes next to the thesis (drawer,
    feed, stored decision) — level ids renumber, so the stop's level is
    named by kind, time and its own id (docs/SPEC.md, Decision v3)."""
    if chosen is None:
        return None
    levels = {
        str(lv.get("id")): lv
        for lv in (snapshot.get("levels") or [])
        if isinstance(lv, dict)
    }

    def _basis_text(basis: str, level_id: str | None) -> str:
        lv = levels.get(level_id or "")
        if lv is None:
            return basis
        buffer = basis.split(" ATR below")[0]
        kind_text = (
            f"{lv.get('timeframe')} {str(lv.get('kind')).replace('_', ' ')}"
            if lv.get("timeframe") in ("1h", "daily")
            else str(lv.get("kind")).replace("_", " ")
        )
        when = ""
        if lv.get("time"):
            try:
                t = datetime.fromisoformat(str(lv["time"]))
                months = ("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec")
                when = f"{t.day} {months[t.month - 1]}"
                if t.hour or t.minute:
                    when += f" {t:%H:%M}"
            except ValueError:
                when = ""
        ref = f"the {when} {kind_text}".replace("  ", " ")
        return f"{buffer} ATR below {ref} ({lv.get('id')})"

    parts: list[str] = []
    stop = _decimal_or_none(chosen.get("stop"))
    if stop is not None:
        basis = str(chosen.get("stop_basis") or chosen.get("basis") or "")
        sid = str(chosen["stop_level"]) if chosen.get("stop_level") else None
        text = _basis_text(basis, sid) if sid else basis
        parts.append(f"stop {stop:.2f}, {text}".rstrip(", "))
    target = _decimal_or_none(chosen.get("target"))
    if target is not None:
        basis = str(chosen.get("target_basis") or "")
        parts.append(f"target {target:.2f}, {basis}".rstrip(", "))
    return " · ".join(parts) or None


def _claim_checks(
    output: ModelOutputV3,
    *,
    context: str,
    snapshot: dict[str, Any],
    chosen: dict[str, Any] | None,
) -> ExplanationCheck:
    """The v3 claim checks (docs/SPEC.md, Decision v3 — Claim checks).

    A thesis or conflict that says the stop "locks in" profit fails when
    the relevant stop's locked R is 0 or less; a sentence naming the stop
    or the target together with a level id other than that level's own id
    fails too. Like the explanation check, it never changes the decision.
    """
    position = snapshot.get("position") or {}
    stop_level: str | None = None
    target_level: str | None = None
    locked_r: Decimal | None = None
    if context == "candidate":
        if chosen is not None:
            stop_level = (
                str(chosen["stop_level"]) if chosen.get("stop_level") else None
            )
            target_level = (
                str(chosen["target_level"]) if chosen.get("target_level") else None
            )
        locked_r = None  # no position: the locks-in check does not apply
    else:
        # Held: stop claims are read against the position's own stop
        # level; for a tighten the chosen stop option sets locked_r.
        stop_level = (
            str(position["stop_level"])
            if isinstance(position, dict) and position.get("stop_level")
            else None
        )
        if chosen is not None and chosen.get("locked_r") is not None:
            locked_r = _decimal_or_none(chosen.get("locked_r"))
        elif isinstance(position, dict):
            entry = _decimal_or_none(position.get("entry"))
            stop = _decimal_or_none(position.get("stop"))
            initial = _decimal_or_none(position.get("initial_stop"))
            if entry is not None and stop is not None and initial is not None:
                risk = entry - initial
                if risk > 0:
                    locked_r = (stop - entry) / risk

    bad: list[str] = []
    for part in [output.thesis, *output.conflicts]:
        for match in _SENTENCE_RE.finditer(part):
            sentence = match.group(0)
            ids = set(_LEVEL_ID_RE.findall(sentence))
            if not ids:
                continue
            low = sentence.lower()
            if "stop" not in low and "target" not in low:
                # Only stop/target attributions are claims: naming a
                # level in any other role (a level in the way, an entry
                # reference) is not a claim about the stop or target.
                continue
            allowed: set[str] = set()
            if "stop" in low and stop_level is not None:
                allowed.add(stop_level)
            if "target" in low and target_level is not None:
                allowed.add(target_level)
            bad.extend(sorted(ids - allowed))
        if _LOCKS_IN_RE.search(part) and locked_r is not None and locked_r <= 0:
            bad.append("locks-in claim")
    return ExplanationCheck(passed=not bad, bad_ids=sorted(set(bad)))


def validate_model_output_v3(
    raw: str | None,
    *,
    symbol: str,
    context: str,
    snapshot: dict[str, Any],
    atr_1h: Decimal | None,
    current_stop: Decimal | None,
    call_error: str | None = None,
    config: AppConfig,
) -> ValidationResult:
    """Parse a decision.v3 answer and run the v3 code checks.

    On top of v2: the confidence gate (``edge_too_small``), data flags
    (``data_flag``) and the claim checks (never gate the action).
    """
    allowed = CANDIDATE_ACTIONS if context == "candidate" else HELD_ACTIONS
    min_conf = float(config.get("decision", "min_confidence_enter", default=0.55))
    min_edge = float(config.get("decision", "v3", "min_edge", default=0.10))
    min_room = float(config.get("decision", "v3", "min_room_atr", default=1.0))
    atr_min = Decimal(str(config.get("decision", "stop_atr_min", default=1.5)))
    atr_max = Decimal(str(config.get("decision", "stop_atr_max", default=4.0)))
    min_rr = Decimal(str(config.get("decision", "min_reward_risk", default=1.5)))

    def invalid(error: str, output: ModelOutputV3 | None = None) -> ValidationResult:
        return ValidationResult(
            valid=False,
            output=output,
            model_action=output.action if output else None,
            final_action=FALLBACK[context],
            error=error,
            note=f"Invalid output — {error}",
        )

    if call_error is not None:
        return invalid(f"model call failed: {call_error}")
    if raw is None or not raw.strip():
        return invalid("empty response")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return invalid(f"not JSON: {exc.msg}")
    try:
        output = ModelOutputV3.model_validate(parsed)
    except ValidationError as exc:
        return invalid(f"schema: {exc.errors()[0]['msg']}")
    if output.symbol != symbol:
        return invalid(f"symbol mismatch: {output.symbol!r}", output)
    if output.action not in allowed:
        return invalid(
            f"action {output.action!r} not allowed for {context}", output
        )

    json_check = CodeCheck(
        "Valid JSON, allowed action", passed=True, detail="", value=output.action
    )
    options = [
        o for o in (snapshot.get("options") or [])
        if isinstance(o, dict)
    ] if context == "candidate" else [
        o for o in (snapshot.get("stop_options") or [])
        if isinstance(o, dict)
    ]
    chosen = _option_by_id(options, output.option_id)
    explanation = check_explanation_v3(output, snapshot, chosen)
    claims = _claim_checks(output, context=context, snapshot=snapshot, chosen=chosen)
    claims_check = CodeCheck(
        "Claim checks",
        passed=claims.passed,
        detail=(
            "no unsupported claims"
            if claims.passed
            else "; ".join(claims.bad_ids)
        ),
    )
    flags = list(output.data_flags)
    flags_check = CodeCheck(
        "Data flags",
        passed=(context != "candidate" or output.action != "enter" or not flags),
        detail=", ".join(flags) if flags else "none",
        value=", ".join(flags) or None,
    )
    checks_tail = [
        CodeCheck(
            "Explanation check",
            passed=explanation.passed,
            detail=(
                "no unsupported numbers"
                if explanation.passed
                else "; ".join(
                    filter(
                        None,
                        [
                            (
                                "unknown ids " + ", ".join(explanation.bad_ids)
                                if explanation.bad_ids
                                else ""
                            ),
                            (
                                "unsupported numbers "
                                + ", ".join(explanation.bad_numbers)
                                if explanation.bad_numbers
                                else ""
                            ),
                        ],
                    )
                )
            ),
            value=", ".join(output.levels_used) or None,
        ),
        claims_check,
        json_check,
    ]

    if output.action == "enter":
        checks: list[CodeCheck] = []
        option_ok = chosen is not None
        checks.append(
            CodeCheck(
                f"Option {output.option_id or '—'} offered",
                passed=option_ok,
                detail=(
                    "valid choice"
                    if option_ok
                    else f"option {output.option_id!r} was not offered"
                ),
                value=output.option_id,
            )
        )
        if not option_ok:
            return ValidationResult(
                valid=False,
                output=output,
                model_action="enter",
                final_action="skip",
                checks=[json_check, *checks],
                error=f"invalid option_id {output.option_id!r}",
                note="Invalid output — option id was not offered",
            )
        assert chosen is not None  # noqa: S101 - internal invariant
        stop = _decimal_or_none(chosen.get("stop"))
        target = _decimal_or_none(chosen.get("target"))
        limit = _decimal_or_none(
            (snapshot.get("constraints") or {}).get("entry_limit")
        )
        reference = _decimal_or_none(snapshot.get("stop_reference_price"))
        entry = limit if limit is not None else _decimal_or_none(snapshot.get("price"))
        risk = trade_risk(entry, stop, target, atr_1h) if (
            entry is not None and stop is not None and target is not None
        ) else None
        stop_distance = (
            (reference - stop) if reference is not None and stop is not None else None
        )
        atr_multiple = (
            stop_distance / atr_1h
            if stop_distance is not None and atr_1h is not None and atr_1h > 0
            else None
        )
        stop_ok = atr_multiple is not None and atr_min <= atr_multiple <= atr_max
        checks.append(
            CodeCheck(
                f"Stop {_fmt(atr_min)}–{_fmt(atr_max)} × ATR (1h)",
                passed=stop_ok,
                detail=(
                    f"{_fmt(stop_distance)} = {_fmt(atr_multiple)} × ATR"
                    if atr_multiple is not None
                    else "reference or ATR missing"
                ),
                value=f"{_fmt(atr_multiple)} × ATR" if atr_multiple else "—",
            )
        )
        rr = risk.reward_to_risk if risk is not None else None
        checks.append(
            CodeCheck(
                f"Reward / risk ≥ {_fmt(min_rr)}",
                passed=rr is not None and rr >= min_rr,
                detail=f"{_fmt(rr)}",
                value=_fmt(rr),
            )
        )
        snap_checks = snapshot.get("checks") or {}
        room_atr = snap_checks.get("room_atr")
        checks.append(
            CodeCheck(
                f"Room to first level ≥ {_fmt(Decimal(str(min_room)))} ATR",
                passed=True,
                detail=(
                    f"{snap_checks.get('room_level')} · {room_atr}"
                    if room_atr is not None
                    else "no level in the way"
                ),
                value=str(room_atr) if room_atr is not None else None,
            )
        )
        breakeven = _decimal_or_none(chosen.get("breakeven_confidence"))
        required = max(min_conf, float(breakeven or 0) + min_edge)
        conf_ok = output.confidence >= required
        checks.append(
            CodeCheck(
                f"Confidence ≥ max({_fmt(Decimal(str(min_conf)))}, "
                f"break-even + {_fmt(Decimal(str(min_edge)))})",
                passed=conf_ok,
                detail=(
                    f"{output.confidence} ≥ max({_fmt(Decimal(str(min_conf)))}, "
                    f"{_fmt(Decimal(str(required)))})"
                ),
                value=_fmt(_decimal_or_none(output.confidence)),
            )
        )
        checks.append(flags_check)
        checks.extend(checks_tail)
        gating = [c for c in checks if c.name not in ("Explanation check", "Claim checks")]
        if all(c.passed for c in gating):
            return ValidationResult(
                valid=True, output=output, model_action="enter",
                final_action="enter", checks=checks,
                note="Valid · passed all checks",
                chosen_option=chosen, explanation=explanation, claims=claims,
                code_written=_code_written(context, snapshot, chosen),
            )
        if not flags_check.passed:
            reason = "data_flag"
            label = "data flag"
        else:
            first_fail = next(c for c in gating if not c.passed)
            reason = (
                "edge_too_small"
                if first_fail.name.startswith("Confidence")
                else first_fail.name
            )
            label = first_fail.name
        return ValidationResult(
            valid=True, output=output, model_action="enter",
            final_action="skip", checks=checks, reason=reason,
            note=f"Valid output · code changed enter → skip ({label})",
            chosen_option=chosen, explanation=explanation, claims=claims,
        )

    if output.action == "tighten_stop":
        option_ok = chosen is not None
        new_stop = _decimal_or_none(chosen.get("stop")) if chosen else None
        raises = (
            new_stop is not None
            and current_stop is not None
            and new_stop > current_stop
        )
        checks = [
            CodeCheck(
                f"Option {output.option_id or '—'} offered",
                passed=option_ok,
                detail=(
                    "valid choice"
                    if option_ok
                    else f"option {output.option_id!r} was not offered"
                ),
                value=output.option_id,
            ),
            CodeCheck(
                "Tighten stop only raises the stop",
                passed=raises,
                detail=(
                    f"{_fmt(new_stop)} > {_fmt(current_stop)}"
                    if new_stop is not None and current_stop is not None
                    else "new stop or current stop missing"
                ),
                value=_fmt(new_stop),
            ),
        ]
        margin = Decimal(str(config.get("decision", "tighten_min_atr_below_price")))
        price = _decimal_or_none(snapshot.get("stop_reference_price"))
        distance_ok = (
            price is not None and atr_1h is not None and atr_1h > 0
            and new_stop is not None
            and price - new_stop >= margin * atr_1h
        )
        checks.append(
            CodeCheck(
                "Tighten stop market distance",
                passed=distance_ok,
                detail=(
                    "passed"
                    if distance_ok
                    else (
                        "reference_missing"
                        if price is None or atr_1h is None
                        else "tighten_market_distance"
                    )
                ),
                value=_fmt(new_stop),
            )
        )
        guard_reason = _breakeven_guard(
            new_stop=new_stop,
            position=snapshot.get("position"),
            price=price,
            min_r=Decimal(
                str(config.get("decision", "breakeven_min_r", default=1.0))
            ),
        )
        checks.append(
            CodeCheck(
                "Break-even guard",
                passed=guard_reason is None,
                detail=guard_reason or "stop above entry or enough profit",
                value=_fmt(new_stop),
            )
        )
        checks.extend(checks_tail)
        if not option_ok:
            return ValidationResult(
                valid=False, output=output, model_action="tighten_stop",
                final_action="hold", checks=checks,
                error=f"invalid option_id {output.option_id!r}",
                note="Invalid output — option id was not offered",
                chosen_option=chosen, explanation=explanation, claims=claims,
            )
        if raises and distance_ok and guard_reason is None:
            return ValidationResult(
                valid=True, output=output, model_action="tighten_stop",
                final_action="tighten_stop", checks=checks,
                note="Valid · passed all checks",
                chosen_option=chosen, explanation=explanation, claims=claims,
                code_written=_code_written(context, snapshot, chosen),
            )
        return ValidationResult(
            valid=True, output=output, model_action="tighten_stop",
            final_action="hold", checks=checks,
            reason=guard_reason,
            note=(
                "Valid output · code changed tighten_stop → hold "
                f"({guard_reason or 'stop not raised'})"
            ),
            chosen_option=chosen, explanation=explanation, claims=claims,
        )

    # skip / hold / exit: action stands; checks still run for the audit.
    checks = [flags_check, *checks_tail]
    return ValidationResult(
        valid=True, output=output, model_action=output.action,
        final_action=output.action, checks=checks, note="Valid",
        explanation=explanation, claims=claims,
    )


_PATH_WORD_RE = re.compile(r"\b(clear|open|easy|manageable)\b", re.IGNORECASE)
_PATH_NOUN_RE = re.compile(r"\b(path|way|room)\b", re.IGNORECASE)


def _v4_text_parts(output: ModelOutputV4) -> list[str]:
    parts = [output.thesis]
    if output.what_changed:
        parts.append(output.what_changed)
    for conflict in output.conflicts:
        parts.append(conflict.text)
        if conflict.outweighed_by:
            parts.append(conflict.outweighed_by)
    return parts


def check_explanation_v4(
    output: ModelOutputV4,
    snapshot: dict[str, Any],
    chosen: dict[str, Any] | None,
) -> ExplanationCheck:
    """The v4 explanation check: thesis, what_changed and the conflicts'
    text and resolution against the rounded snapshot."""
    return _check_explanation(
        _v4_text_parts(output), output.levels_used, snapshot, chosen
    )


def _claim_checks_v4(
    output: ModelOutputV4,
    *,
    context: str,
    snapshot: dict[str, Any],
    chosen: dict[str, Any] | None,
) -> ExplanationCheck:
    """The v4 claim checks (docs/SPEC.md, Decision v4 — Code checks).

    v3's locks-in and stop/target level checks, plus the path claim: a
    sentence calling the path "clear", "open", "easy" or "manageable"
    fails while the chosen option (held: ``levels_to_target``) has a
    level within 1 ATR. Never changes the decision.
    """
    proxy = ModelOutputV3(
        symbol=output.symbol,
        conflicts=[c.text for c in output.conflicts]
        + [c.outweighed_by for c in output.conflicts if c.outweighed_by],
        thesis=output.thesis,
        action=output.action,
        confidence=output.confidence,
        levels_used=output.levels_used,
    )
    base = _claim_checks(proxy, context=context, snapshot=snapshot, chosen=chosen)
    bad: list[str] = list(base.bad_ids)
    kinds: list[str] = ["level" if i.startswith("L") else i for i in base.bad_ids]
    kinds = ["locks_in" if k == "locks-in claim" else k for k in kinds]

    near = False
    if context == "candidate":
        if chosen is not None:
            near = any(
                _decimal_or_none(lv.get("distance_atr")) is not None
                and abs(_decimal_or_none(lv.get("distance_atr"))) < 1  # type: ignore[arg-type]
                for lv in (chosen.get("levels_in_way") or [])
                if isinstance(lv, dict)
            )
    else:
        position = snapshot.get("position") or {}
        near = any(
            _decimal_or_none(lv.get("distance_atr")) is not None
            and abs(_decimal_or_none(lv.get("distance_atr"))) < 1  # type: ignore[arg-type]
            for lv in (position.get("levels_to_target") or [])
            if isinstance(lv, dict)
        )
    if near:
        for part in _v4_text_parts(output):
            for match in _SENTENCE_RE.finditer(part):
                sentence = match.group(0)
                if _PATH_WORD_RE.search(sentence) and _PATH_NOUN_RE.search(sentence):
                    bad.append("path claim")
                    kinds.append("path")
    seen: set[str] = set()
    bad_unique: list[str] = []
    for b in bad:
        if b not in seen:
            seen.add(b)
            bad_unique.append(b)
    return ExplanationCheck(
        passed=not bad_unique,
        bad_ids=sorted(bad_unique),
        bad_numbers=base.bad_numbers,
        kinds=sorted(set(kinds)) if bad_unique else [],
    )


def _flip_flop_detail(output: ModelOutputV4, prev: dict[str, Any], since: dict[str, Any]) -> str:
    bits = [
        f"previous {prev.get('action')}",
        f"moved {since.get('price_move_atr')} ATR",
    ]
    if since.get("checks_changed"):
        bits.append(f"checks changed: {'; '.join(since['checks_changed'])}")
    else:
        bits.append("checks the same")
    return " · ".join(bits)


def validate_model_output_v4(
    raw: str | None,
    *,
    symbol: str,
    context: str,
    snapshot: dict[str, Any],
    atr_1h: Decimal | None,
    current_stop: Decimal | None,
    call_error: str | None = None,
    config: AppConfig,
) -> ValidationResult:
    """Parse a decision.v4 answer and run the v4 code checks.

    On top of v3: ``best_option_id`` validity, the flip-flop guard
    (``flip_flop``), confidence sanity (``confidence_outlier``),
    unresolved conflicts (``unresolved_conflict``) and path claims —
    the last three warn, never gate.
    """
    allowed = CANDIDATE_ACTIONS if context == "candidate" else HELD_ACTIONS
    min_conf = float(config.get("decision", "min_confidence_enter", default=0.55))
    min_edge = float(config.get("decision", "v3", "min_edge", default=0.10))
    min_room = float(config.get("decision", "v3", "min_room_atr", default=1.0))
    atr_min = Decimal(str(config.get("decision", "stop_atr_min", default=1.5)))
    atr_max = Decimal(str(config.get("decision", "stop_atr_max", default=4.0)))
    min_rr = Decimal(str(config.get("decision", "min_reward_risk", default=1.5)))
    max_outlier = float(
        config.get("decision", "v4", "max_confidence_above_anchor", default=0.30)
    )
    flip_min = float(
        config.get("decision", "v4", "flip_min_move_atr", default=0.5)
    )

    def invalid(error: str, output: ModelOutputV4 | None = None) -> ValidationResult:
        return ValidationResult(
            valid=False,
            output=output,
            model_action=output.action if output else None,
            final_action=FALLBACK[context],
            error=error,
            note=f"Invalid output — {error}",
        )

    if call_error is not None:
        return invalid(f"model call failed: {call_error}")
    if raw is None or not raw.strip():
        return invalid("empty response")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return invalid(f"not JSON: {exc.msg}")
    try:
        output = ModelOutputV4.model_validate(parsed)
    except ValidationError as exc:
        return invalid(f"schema: {exc.errors()[0]['msg']}")
    if output.symbol != symbol:
        return invalid(f"symbol mismatch: {output.symbol!r}", output)
    if output.action not in allowed:
        return invalid(
            f"action {output.action!r} not allowed for {context}", output
        )

    json_check = CodeCheck(
        "Valid JSON, allowed action", passed=True, detail="", value=output.action
    )
    options = [
        o for o in (snapshot.get("options") or [])
        if isinstance(o, dict)
    ] if context == "candidate" else [
        o for o in (snapshot.get("stop_options") or [])
        if isinstance(o, dict)
    ]
    chosen = _option_by_id(options, output.option_id)
    best = _option_by_id(options, output.best_option_id)

    # --- v4 field rules (docs/SPEC.md, Decision v4 — Best option)
    if context == "candidate":
        if output.best_option_id is None or best is None:
            return invalid(
                f"invalid best_option_id {output.best_option_id!r}", output
            )
    elif output.best_option_id is not None:
        return invalid(
            f"invalid best_option_id {output.best_option_id!r}", output
        )

    explanation = check_explanation_v4(output, snapshot, chosen)
    claims = _claim_checks_v4(output, context=context, snapshot=snapshot, chosen=chosen)
    claims_check = CodeCheck(
        "Claim checks",
        passed=claims.passed,
        detail=(
            "no unsupported claims"
            if claims.passed
            else "; ".join(claims.bad_ids)
        ),
    )
    flags = list(output.data_flags)
    flags_check = CodeCheck(
        "Data flags",
        passed=(context != "candidate" or output.action != "enter" or not flags),
        detail=", ".join(flags) if flags else "none",
        value=", ".join(flags) or None,
    )
    checks_tail = [
        CodeCheck(
            "Explanation check",
            passed=explanation.passed,
            detail=(
                "no unsupported numbers"
                if explanation.passed
                else "; ".join(
                    filter(
                        None,
                        [
                            (
                                "unknown ids " + ", ".join(explanation.bad_ids)
                                if explanation.bad_ids
                                else ""
                            ),
                            (
                                "unsupported numbers "
                                + ", ".join(explanation.bad_numbers)
                                if explanation.bad_numbers
                                else ""
                            ),
                        ],
                    )
                )
            ),
            value=", ".join(output.levels_used) or None,
        ),
        claims_check,
        json_check,
    ]

    # --- warnings, never gates (docs/SPEC.md, Decision v4 — Code checks)
    warnings: list[str] = []
    anchor: Decimal | None = None
    if context == "candidate":
        anchor_option = chosen if output.action == "enter" else best
        anchor = (
            _decimal_or_none(anchor_option.get("breakeven_confidence"))
            if anchor_option is not None
            else None
        )
    else:
        anchor = _decimal_or_none(
            (snapshot.get("position") or {}).get("neutral_probability")
        )
    anchor_f = float(anchor) if anchor is not None else None
    if anchor is not None and output.confidence - float(anchor) > max_outlier:
        warnings.append("confidence_outlier")
    unresolved = [
        c.text for c in output.conflicts if c.outweighed_by is None
    ] if context == "candidate" and output.action == "enter" else []
    if unresolved:
        warnings.append("unresolved_conflict")
    confidence_check = CodeCheck(
        "Confidence sanity",
        passed="confidence_outlier" not in warnings,
        detail=(
            f"{output.confidence} − anchor {_fmt(anchor)} = "
            f"{_fmt(_decimal_or_none(output.confidence - float(anchor)))}"
            if anchor is not None
            else "no anchor"
        ),
        value=_fmt(_decimal_or_none(output.confidence)),
    )
    unresolved_check = CodeCheck(
        "Conflicts resolved",
        passed=not unresolved,
        detail=(
            "every conflict outweighed"
            if not unresolved
            else f"not outweighed: {'; '.join(unresolved)}"
        ),
    )

    if output.action == "enter":
        checks: list[CodeCheck] = []
        option_ok = chosen is not None
        checks.append(
            CodeCheck(
                f"Option {output.option_id or '—'} offered",
                passed=option_ok,
                detail=(
                    "valid choice"
                    if option_ok
                    else f"option {output.option_id!r} was not offered"
                ),
                value=output.option_id,
            )
        )
        if not option_ok:
            return ValidationResult(
                valid=False, output=output, model_action="enter",
                final_action="skip", checks=[json_check, *checks],
                error=f"invalid option_id {output.option_id!r}",
                note="Invalid output — option id was not offered",
                warnings=warnings, confidence_anchor=anchor_f,
            )
        assert chosen is not None  # noqa: S101 - internal invariant
        stop = _decimal_or_none(chosen.get("stop"))
        target = _decimal_or_none(chosen.get("target"))
        limit = _decimal_or_none(
            (snapshot.get("constraints") or {}).get("entry_limit")
        )
        reference = _decimal_or_none(snapshot.get("stop_reference_price"))
        entry = limit if limit is not None else _decimal_or_none(snapshot.get("price"))
        risk = trade_risk(entry, stop, target, atr_1h) if (
            entry is not None and stop is not None and target is not None
        ) else None
        stop_distance = (
            (reference - stop) if reference is not None and stop is not None else None
        )
        atr_multiple = (
            stop_distance / atr_1h
            if stop_distance is not None and atr_1h is not None and atr_1h > 0
            else None
        )
        checks.append(
            CodeCheck(
                f"Stop {_fmt(atr_min)}–{_fmt(atr_max)} × ATR (1h)",
                passed=atr_multiple is not None and atr_min <= atr_multiple <= atr_max,
                detail=(
                    f"{_fmt(stop_distance)} = {_fmt(atr_multiple)} × ATR"
                    if atr_multiple is not None
                    else "reference or ATR missing"
                ),
                value=f"{_fmt(atr_multiple)} × ATR" if atr_multiple else "—",
            )
        )
        rr = risk.reward_to_risk if risk is not None else None
        checks.append(
            CodeCheck(
                f"Reward / risk ≥ {_fmt(min_rr)}",
                passed=rr is not None and rr >= min_rr,
                detail=f"{_fmt(rr)}",
                value=_fmt(rr),
            )
        )
        snap_checks = snapshot.get("checks") or {}
        room_atr = snap_checks.get("room_atr")
        checks.append(
            CodeCheck(
                f"Room to first level ≥ {_fmt(Decimal(str(min_room)))} ATR",
                passed=True,
                detail=(
                    f"{snap_checks.get('room_level')} · {room_atr}"
                    if room_atr is not None
                    else "no level in the way"
                ),
                value=str(room_atr) if room_atr is not None else None,
            )
        )
        breakeven = _decimal_or_none(chosen.get("breakeven_confidence"))
        required = max(min_conf, float(breakeven or 0) + min_edge)
        checks.append(
            CodeCheck(
                f"Confidence ≥ max({_fmt(Decimal(str(min_conf)))}, "
                f"break-even + {_fmt(Decimal(str(min_edge)))})",
                passed=output.confidence >= required,
                detail=(
                    f"{output.confidence} ≥ max({_fmt(Decimal(str(min_conf)))}, "
                    f"{_fmt(Decimal(str(required)))})"
                ),
                value=_fmt(_decimal_or_none(output.confidence)),
            )
        )
        checks.append(confidence_check)
        checks.append(unresolved_check)
        # Flip-flop guard (docs/SPEC.md, Decision v4 — Code checks): a
        # model enter after a model skip with nothing material changed
        # becomes skip · flip_flop. An enter → skip is never blocked.
        prev = snapshot.get("previous_decision") or {}
        since = prev.get("since") or {}
        move = since.get("price_move_atr")
        new_symbol_headline = any(
            h.get("new") and h.get("about_symbol")
            for h in (snapshot.get("headlines") or [])
            if isinstance(h, dict)
        )
        flip = (
            isinstance(prev, dict)
            and prev.get("action") == "skip"
            and move is not None
            and abs(float(move)) < flip_min
            and not since.get("checks_changed")
            and not new_symbol_headline
            and not since.get("new_levels_within_1_atr")
        )
        checks.append(
            CodeCheck(
                "Flip-flop guard",
                passed=not flip,
                detail=_flip_flop_detail(output, prev, since)
                if isinstance(prev, dict) and prev
                else "no previous decision",
            )
        )
        checks.append(flags_check)
        checks.extend(checks_tail)
        gating = [
            c
            for c in checks
            if c.name
            not in (
                "Explanation check",
                "Claim checks",
                "Confidence sanity",
                "Conflicts resolved",
            )
        ]
        if all(c.passed for c in gating):
            return ValidationResult(
                valid=True, output=output, model_action="enter",
                final_action="enter", checks=checks,
                note="Valid · passed all checks",
                chosen_option=chosen, explanation=explanation, claims=claims,
                code_written=_code_written(context, snapshot, chosen),
                warnings=warnings, confidence_anchor=anchor_f,
            )
        if not flags_check.passed:
            reason = "data_flag"
            label = "data flag"
        elif flip:
            reason = "flip_flop"
            label = "flip-flop guard"
        else:
            first_fail = next(c for c in gating if not c.passed)
            reason = (
                "edge_too_small"
                if first_fail.name.startswith("Confidence")
                else first_fail.name
            )
            label = first_fail.name
        return ValidationResult(
            valid=True, output=output, model_action="enter",
            final_action="skip", checks=checks, reason=reason,
            note=f"Valid output · code changed enter → skip ({label})",
            chosen_option=chosen, explanation=explanation, claims=claims,
            warnings=warnings, confidence_anchor=anchor_f,
        )

    if output.action == "tighten_stop":
        option_ok = chosen is not None
        new_stop = _decimal_or_none(chosen.get("stop")) if chosen else None
        raises = (
            new_stop is not None
            and current_stop is not None
            and new_stop > current_stop
        )
        checks = [
            CodeCheck(
                f"Option {output.option_id or '—'} offered",
                passed=option_ok,
                detail=(
                    "valid choice"
                    if option_ok
                    else f"option {output.option_id!r} was not offered"
                ),
                value=output.option_id,
            ),
            CodeCheck(
                "Tighten stop only raises the stop",
                passed=raises,
                detail=(
                    f"{_fmt(new_stop)} > {_fmt(current_stop)}"
                    if new_stop is not None and current_stop is not None
                    else "new stop or current stop missing"
                ),
                value=_fmt(new_stop),
            ),
        ]
        margin = Decimal(str(config.get("decision", "tighten_min_atr_below_price")))
        price = _decimal_or_none(snapshot.get("stop_reference_price"))
        distance_ok = (
            price is not None and atr_1h is not None and atr_1h > 0
            and new_stop is not None
            and price - new_stop >= margin * atr_1h
        )
        checks.append(
            CodeCheck(
                "Tighten stop market distance",
                passed=distance_ok,
                detail=(
                    "passed"
                    if distance_ok
                    else (
                        "reference_missing"
                        if price is None or atr_1h is None
                        else "tighten_market_distance"
                    )
                ),
                value=_fmt(new_stop),
            )
        )
        guard_reason = _breakeven_guard(
            new_stop=new_stop,
            position=snapshot.get("position"),
            price=price,
            min_r=Decimal(
                str(config.get("decision", "breakeven_min_r", default=1.0))
            ),
        )
        checks.append(
            CodeCheck(
                "Break-even guard",
                passed=guard_reason is None,
                detail=guard_reason or "stop above entry or enough profit",
                value=_fmt(new_stop),
            )
        )
        checks.append(confidence_check)
        checks.extend(checks_tail)
        if not option_ok:
            return ValidationResult(
                valid=False, output=output, model_action="tighten_stop",
                final_action="hold", checks=checks,
                error=f"invalid option_id {output.option_id!r}",
                note="Invalid output — option id was not offered",
                chosen_option=chosen, explanation=explanation, claims=claims,
                warnings=warnings, confidence_anchor=anchor_f,
            )
        if raises and distance_ok and guard_reason is None:
            return ValidationResult(
                valid=True, output=output, model_action="tighten_stop",
                final_action="tighten_stop", checks=checks,
                note="Valid · passed all checks",
                chosen_option=chosen, explanation=explanation, claims=claims,
                code_written=_code_written(context, snapshot, chosen),
                warnings=warnings, confidence_anchor=anchor_f,
            )
        return ValidationResult(
            valid=True, output=output, model_action="tighten_stop",
            final_action="hold", checks=checks,
            reason=guard_reason,
            note=(
                "Valid output · code changed tighten_stop → hold "
                f"({guard_reason or 'stop not raised'})"
            ),
            chosen_option=chosen, explanation=explanation, claims=claims,
            warnings=warnings, confidence_anchor=anchor_f,
        )

    # skip / hold / exit: action stands; checks still run for the audit.
    checks = [flags_check, confidence_check, unresolved_check, *checks_tail]
    return ValidationResult(
        valid=True, output=output, model_action=output.action,
        final_action=output.action, checks=checks, note="Valid",
        explanation=explanation, claims=claims,
        warnings=warnings, confidence_anchor=anchor_f,
    )


def decision_trade_prices(
    model_output: dict[str, Any] | None,
    options: dict[str, Any] | None,
    chosen_option: str | None,
) -> tuple[Decimal | None, Decimal | None]:
    """The (stop, target) a stored decision trades at.

    v1 rows carry them in the model output; v2 rows resolve the chosen
    option id inside the stored options column — code never moves a level
    or picks another option.
    """
    out = model_output or {}
    if out.get("stop_price") is not None or out.get("target_price") is not None:
        return (
            _decimal_or_none(out.get("stop_price")),
            _decimal_or_none(out.get("target_price")),
        )
    if chosen_option is None or not options:
        return None, None
    pool = [
        *(options.get("options") or []),
        *(options.get("stop_options") or []),
    ]
    chosen = _option_by_id(pool, chosen_option)
    if chosen is None:
        return None, None
    return (
        _decimal_or_none(chosen.get("stop")),
        _decimal_or_none(chosen.get("target")),
    )
