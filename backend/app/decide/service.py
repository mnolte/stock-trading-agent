"""Decision storage: one model call → one decision row (docs/SPEC.md).

Every decision — including skips and failed calls — is stored with its input
snapshot, rules result, parsed model output, validation result, final action,
prompt version and hash, model, token usage, cost, latency and the raw
response. An empty ``outcomes`` row is created alongside; the outcome worker
(PR 7) fills in the prices.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Outcome
from app.decide.client import DecisionClient, ModelResponse, token_cost
from app.decide.levels import OPTIONS_VERSION
from app.decide.llm_cache import LLMCacheStore
from app.decide.output import (
    NO_CALL_REASONS,
    ModelOutput,
    ModelOutputV2,
    ValidationResult,
    validate_model_output,
    validate_model_output_v2,
)
from app.decide.prompt import Prompt, PromptSpec, cached_prompt, prompt_spec
from app.decide.v2inputs import V2Build
from app.inspector.record import build_llm_call
from app.logging_config import ev
from app.market.clock import Clock, make_clock
from app.notifications.service import Notifier, NullNotifier
from app.rules.prefilter import RulesResult

logger = logging.getLogger("model.openai")

# Final action recorded when no usable model answer exists (no call made,
# or the call failed). ``no_call_reason`` says why: rules, call_cap,
# price_stale, price_suspect or model_error.
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


def _levels_column(v2: V2Build | None) -> dict[str, Any] | None:
    return {"levels": v2.fields.get("levels")} if v2 is not None else None


def _options_column(v2: V2Build | None) -> dict[str, Any] | None:
    return v2.options_column if v2 is not None else None


def model_output_json(
    output: ModelOutput | ModelOutputV2
) -> dict[str, Any]:
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
        api_key: str | None = None,
        notifier: Notifier | None = None,
        llm_cache: LLMCacheStore | None = None,
        cache_reads: bool = False,
        before_call: Callable[[], Awaitable[None]] | None = None,
        clock: Clock | None = None,
        secrets: Callable[[], list[str]] | None = None,
    ) -> None:
        self.client = client
        self.config = config
        # Kept for reload(): a promoted model/prompt rebuilds the client
        # with the same stored key (keys live in the secrets table).
        self.api_key = api_key
        self._notifier = notifier or NullNotifier()
        self._session_factory = session_factory or get_session_maker()
        self._clock = clock or make_clock(config)
        # Scrubbed stored keys for call-history error messages; None in
        # tests means nothing is stored yet, so nothing needs stripping.
        self._secrets = secrets
        # The shared answer cache: every successful call writes a row; a
        # backtest also reads it so a rerun on the same inputs is free
        # (docs/SPEC.md, Backtesting, Model calls).
        self._cache = llm_cache
        self._cache_reads = cache_reads
        # An optional async gate before a real call — the backtest worker
        # waits here while the app's pause flag is set.
        self._before_call = before_call
        version = str(config.get("decision", "prompt", default="decision.v1"))
        self._prompt: Prompt = cached_prompt(version)
        self._spec: PromptSpec = prompt_spec(version)

    @property
    def spec(self) -> PromptSpec:
        """The prompt version's code contract (schema, option building)."""
        return self._spec

    @property
    def prompt(self) -> Prompt:
        return self._prompt

    def reload(self) -> None:
        """Pick up promoted settings: new prompt version and/or model."""
        from app.decide.client import make_decision_client

        self.client = make_decision_client(self.config, api_key=self.api_key)
        version = str(self.config.get("decision", "prompt", default="decision.v1"))
        self._prompt = cached_prompt(version)
        self._spec = prompt_spec(version)

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
        v2: V2Build | None = None,
    ) -> Decision:
        """Call the model, validate the output and store the decision row."""
        user = json.dumps(snapshot_json, indent=2)
        response: ModelResponse | None = None
        sent_at = None
        from_cache = False
        if self._cache is not None and self._cache_reads:
            hit = self._cache.lookup(
                model=str(self.config.get("decision", "model", default="")),
                temperature=float(
                    self.config.get("decision", "temperature", default=0.2)
                ),
                prompt_hash=self._prompt.sha256,
                user=user,
            )
            if hit is not None:
                response = ModelResponse(
                    raw=hit["response"],
                    model=str(self.config.get("decision", "model", default="")),
                    tokens_in=hit["tokens_in"],
                    tokens_out=hit["tokens_out"],
                    latency_ms=0,
                )
                from_cache = True
        if response is None:
            if self._before_call is not None:
                await self._before_call()
            sent_at = _naive_utc(self._clock.now())
            ev(
                logger, logging.DEBUG, "model.call_sent",
                f"{symbol}: model call sent "
                f"({self._prompt.version}, hash {self._prompt.sha256[:8]})",
                symbol=symbol,
            )
            response = await self.client.complete(
                system=self._prompt.text, user=user, schema=self._spec.json_schema
            )
            ev(
                logger,
                logging.DEBUG if response.error is None else logging.WARNING,
                "model.call_returned" if response.error is None else "model.call_failed",
                (
                    f"{symbol}: {response.model} returned "
                    f"{response.tokens_in}+{response.tokens_out} tok in "
                    f"{response.latency_ms}ms"
                    if response.error is None
                    else f"{symbol}: model call failed ({response.error_type}: "
                         f"{response.error})"
                ),
                symbol=symbol,
            )
            if (
                self._cache is not None
                and response.error is None
                and response.raw is not None
            ):
                cycle = session.get(Cycle, cycle_id)
                self._cache.store(
                    model=str(
                        self.config.get("decision", "model", default="")
                    ),
                    temperature=float(
                        self.config.get("decision", "temperature", default=0.2)
                    ),
                    prompt_version=self._prompt.version,
                    prompt_hash=self._prompt.sha256,
                    user=user,
                    cycle_time=(
                        cycle.scheduled_time if cycle is not None else None
                    ),
                    response=response.raw,
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                )
        if self._spec.uses_options:
            validation = validate_model_output_v2(
                response.raw,
                symbol=symbol,
                context=context,
                snapshot=snapshot_json,
                atr_1h=atr_1h,
                current_stop=current_stop,
                call_error=response.error,
                config=self.config,
            )
        else:
            validation = validate_model_output(
                response.raw,
                symbol=symbol,
                context=context,
                price=(
                    Decimal(str(snapshot_json["stop_reference_price"]))
                    if snapshot_json.get("stop_reference_price") is not None else None
                ) if context == "held" else price,
                atr_1h=atr_1h,
                current_stop=current_stop,
                call_error=response.error,
                config=self.config,
            )
        # A failed call (error or refusal) left no output to fall back
        # on; "no_call" says so.
        failed = response.error is not None or response.refused
        if failed:
            self._notifier.notify(
                "model_failing",
                f"{symbol}: model call failed ({response.error or 'refusal'})",
                link="/inspector",
            )
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
            cached=from_cache,
            sent_at=sent_at,
            v2=v2,
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
        v2: V2Build | None = None,
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
            v2=v2,
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
        cached: bool = False,
        sent_at: datetime | None = None,
        v2: V2Build | None = None,
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
            cost=(
                None
                if cached or response is None
                else token_cost(
                    response,
                    self.config,
                    model=str(self.config.get("decision", "model", default="")),
                )
            ),
            latency_ms=response.latency_ms if response else None,
            raw_response=response.raw if response else None,
            cached=cached,
            cost_source="cached" if cached else "priced",
            levels=_levels_column(v2),
            options=_options_column(v2),
            chosen_option=(
                validation.chosen_option.get("id")
                if validation.chosen_option is not None
                else None
            ),
            explanation_check=(
                validation.explanation.to_json()
                if validation.explanation is not None
                else None
            ),
            options_version=OPTIONS_VERSION if v2 is not None else None,
        )
        session.add(decision)
        session.flush()
        session.add(Outcome(decision_id=decision.id))
        session.flush()
        if response is not None and not cached:
            # One llm_calls row per request actually sent, in the same
            # transaction as the decision it made (docs/SPEC.md, LLM call
            # history). A cache hit made no request; a gates-changed final
            # action is folded in by apply_decision's sync.
            session.add(
                build_llm_call(
                    source="live",
                    kind="decision",
                    sent_at=sent_at or _naive_utc(self._clock.now()),
                    response=response,
                    input_snapshot=snapshot_json,
                    model_action=validation.model_action,
                    final_action=final_action,
                    output_invalid=not validation.valid
                    and response.error is None
                    and not response.refused,
                    error_detail=validation.error,
                    unsupported_claim=(
                        validation.explanation is not None
                        and not validation.explanation.passed
                    ),
                    cycle_id=cycle_id,
                    decision_id=decision.id,
                    symbol=symbol,
                    context=context,
                    model=response.model,
                    temperature=float(
                        self.config.get("decision", "temperature", default=0)
                    ),
                    prompt_version=self._prompt.version,
                    prompt_hash=self._prompt.sha256,
                    cost=decision.cost,
                    cost_source=decision.cost_source,
                    slow_ms=self._slow_ms(),
                    scrub_values=self._secrets,
                )
            )
        return decision

    def _slow_ms(self) -> int:
        return int(
            float(
                self.config.get("inspector", "slow_call_seconds", default=15)
            )
            * 1000
        )
