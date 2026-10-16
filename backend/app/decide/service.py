"""Decision storage: one model call → one decision row (docs/SPEC.md).

Every decision — including skips and failed calls — is stored with its input
snapshot, rules result, parsed model output, validation result, final action,
prompt version and hash, model, token usage, cost, latency and the raw
response. An empty ``outcomes`` row is created alongside; the outcome worker
(PR 7) fills in the prices.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, cast

from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.db.base import get_session_maker
from app.db.models import Decision, Outcome
from app.decide.client import DecisionClient, ModelResponse, token_cost
from app.decide.output import (
    NO_CALL_REASONS,
    ModelOutput,
    ValidationResult,
    validate_model_output,
)
from app.decide.prompt import Prompt, cached_prompt
from app.rules.prefilter import RulesResult

# Final action recorded when no usable model answer exists (no call made,
# or the call failed). ``no_call_reason`` says why: rules, call_cap,
# quote_missing or model_error.
NO_CALL = "no_call"


def _jsonable(value: Any) -> Any:
    """Recursively convert Decimals to floats for JSON storage."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def model_output_json(output: ModelOutput) -> dict[str, Any]:
    return cast(dict[str, Any], _jsonable(output.model_dump()))


def rules_result_json(rules: RulesResult | None) -> dict[str, Any] | None:
    if rules is None:
        return None
    return {
        "status": rules.status,
        "reason": rules.reason,
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail}
            for c in rules.checks
        ],
    }


def validation_result_json(result: ValidationResult) -> dict[str, Any]:
    return {
        "valid": result.valid,
        "model_action": result.model_action,
        "final_action": result.final_action,
        "changed_by_code": result.changed_by_code,
        "error": result.error,
        "note": result.note,
        "checks": [
            {
                "name": c.name,
                "passed": c.passed,
                "detail": c.detail,
                "value": c.value,
            }
            for c in result.checks
        ],
    }


class DecisionService:
    """Runs one model call per symbol/context and stores the decision."""

    def __init__(
        self,
        client: DecisionClient,
        config: AppConfig,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self._session_factory = session_factory or get_session_maker()
        version = str(config.get("decision", "prompt", default="decision.v1"))
        self._prompt: Prompt = cached_prompt(version)

    @property
    def prompt(self) -> Prompt:
        return self._prompt

    def reload(self) -> None:
        """Pick up promoted settings: new prompt version and/or model."""
        from app.decide.client import make_decision_client

        self.client = make_decision_client(self.config)
        version = str(self.config.get("decision", "prompt", default="decision.v1"))
        self._prompt = cached_prompt(version)

    async def decide(
        self,
        session: Session,
        *,
        cycle_id: int,
        symbol: str,
        context: str,
        snapshot_json: dict[str, Any],
        rules: RulesResult | None,
        price: Decimal | None,
        atr_1h: Decimal | None,
        current_stop: Decimal | None,
    ) -> Decision:
        """Call the model, validate the output and store the decision row."""
        response = await self.client.complete(
            system=self._prompt.text, user=json.dumps(snapshot_json, indent=2)
        )
        validation = validate_model_output(
            response.raw,
            symbol=symbol,
            context=context,
            price=price,
            atr_1h=atr_1h,
            current_stop=current_stop,
            call_error=response.error,
            config=self.config,
        )
        # A failed call left no output to fall back on; "no_call" says so.
        failed = response.error is not None
        decision = self._store(
            session,
            cycle_id=cycle_id,
            symbol=symbol,
            context=context,
            snapshot_json=snapshot_json,
            rules=rules,
            response=response,
            validation=validation,
            final_action=NO_CALL if failed else validation.final_action,
            no_call_reason="model_error" if failed else None,
        )
        return decision

    def record_no_call(
        self,
        session: Session,
        *,
        cycle_id: int,
        symbol: str,
        context: str,
        snapshot_json: dict[str, Any],
        rules: RulesResult | None,
        reason: str,
        detail: str | None = None,
    ) -> Decision:
        """Store a decision row when code blocked the call before it happened."""
        if reason not in NO_CALL_REASONS:
            raise ValueError(f"unknown no_call reason {reason!r}")
        validation = ValidationResult(
            valid=False,
            output=None,
            model_action=None,
            final_action=NO_CALL,
            error=detail or reason,
            note=f"No call — {detail or reason}",
        )
        return self._store(
            session,
            cycle_id=cycle_id,
            symbol=symbol,
            context=context,
            snapshot_json=snapshot_json,
            rules=rules,
            response=None,
            validation=validation,
            final_action=NO_CALL,
            no_call_reason=reason,
        )

    def _store(
        self,
        session: Session,
        *,
        cycle_id: int,
        symbol: str,
        context: str,
        snapshot_json: dict[str, Any],
        rules: RulesResult | None,
        response: ModelResponse | None,
        validation: ValidationResult,
        final_action: str,
        no_call_reason: str | None = None,
    ) -> Decision:
        decision = Decision(
            cycle_id=cycle_id,
            symbol=symbol,
            context=context,
            input_snapshot=snapshot_json,
            rules_result=rules_result_json(rules),
            model_output=(
                model_output_json(validation.output) if validation.output else None
            ),
            validation_result=validation_result_json(validation),
            gates_result=None,
            final_action=final_action,
            no_call_reason=no_call_reason,
            prompt_version=self._prompt.version,
            prompt_hash=self._prompt.sha256,
            model=response.model if response else "none",
            tokens=response.tokens if response else None,
            tokens_in=response.tokens_in if response else None,
            tokens_out=response.tokens_out if response else None,
            cost=token_cost(response, self.config) if response else None,
            latency_ms=response.latency_ms if response else None,
            raw_response=response.raw if response else None,
        )
        session.add(decision)
        session.flush()
        session.add(Outcome(decision_id=decision.id))
        session.flush()
        return decision
