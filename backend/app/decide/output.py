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
    "no_options", "reference_missing",
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

    def to_json(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "bad_ids": self.bad_ids,
            "bad_numbers": self.bad_numbers,
        }


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
    from app.reviews.numbercheck import fact_values

    levels = snapshot.get("levels") or []
    known_ids = {
        str(lv.get("id"))
        for lv in levels
        if isinstance(lv, dict) and lv.get("id") is not None
    }
    text_parts = [output.thesis, *output.risks]
    mentioned = set(output.levels_used)
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
    output: ModelOutput | ModelOutputV2 | None
    model_action: str | None  # action the model chose (None when unparseable)
    final_action: str  # action code allows: may differ from the model's
    checks: list[CodeCheck] = field(default_factory=list)
    error: str | None = None  # why the output was invalid
    note: str = ""  # one-line summary for the inspector banner
    # decision.v2: the offered option the model picked (stored verbatim,
    # code never moves prices) and the explanation check result.
    chosen_option: dict[str, Any] | None = None
    explanation: ExplanationCheck | None = None

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
        assert chosen is not None
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
        if raises and distance_ok:
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
            note="Valid output · code changed tighten_stop → hold (stop not raised)",
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
