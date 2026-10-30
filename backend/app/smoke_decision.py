"""One real decision call against cached data (docs/SPEC.md, Decision layer).

Run from the repo root: ``make smoke-decision SYMBOL=MSFT``. Builds the
input snapshot the same way a cycle does — from the bar cache, the
earnings table and the open-position rows — then makes exactly one
OpenAIDecisionClient call with the real key and prints the validation
result, token usage, cost and latency. It stores nothing and never
touches an order path.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.main import run_migrations
from app.config import AppConfig, get_config
from app.data.factory import DataSources
from app.data.fake import (
    FakeBarSource,
    FakeCalendarSource,
    FakeCorporateActionSource,
    FakeEarningsSource,
    FakeFXSource,
    FakeNewsSource,
    FakeQuoteSource,
)
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import Position
from app.decide.client import DecisionClient, token_cost
from app.decide.output import ValidationResult, validate_model_output
from app.decide.prompt import cached_prompt
from app.decide.snapshot import build_input_snapshot
from app.llm.clients import make_model_clients
from app.market.calendar import WeekdayCalendarProvider
from app.market.clock import NY_TZ, Clock, make_clock
from app.rules.prefilter import RulesResult
from app.secrets_service import SecretsStore
from app.signals.service import SignalService


@dataclass
class SmokeInput:
    """Everything one model call needs, built purely from cached data."""

    symbol: str
    context: str
    snapshot_json: dict[str, Any]
    rules: RulesResult
    price: Decimal | None
    atr_1h: Decimal | None
    current_stop: Decimal | None


def build_smoke_input(
    symbol: str,
    config: AppConfig,
    clock: Clock,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> SmokeInput:
    """Snapshot for ``symbol`` from the bar cache and the database.

    The sources are fakes that are never fetched — everything the snapshot
    needs is read from the local cache. The weekday calendar stands in for
    the Alpaca calendar so the smoke works offline.
    """
    sources = DataSources(
        bars=FakeBarSource(),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=FakeCalendarSource(),
        alpaca=None,
    )
    calendar = WeekdayCalendarProvider()
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    signals = SignalService(market_data, calendar, config, clock, session_factory)
    evaluation = signals.evaluate(clock.now())
    item = next((i for i in evaluation.items if i.symbol == symbol), None)
    if item is None:
        raise ValueError(
            f"{symbol} is not in the configured universe "
            f"({', '.join(i.symbol for i in evaluation.items)})"
        )

    sf = session_factory or get_session_maker()
    with sf() as session:
        position = session.execute(
            select(Position).where(
                Position.symbol == symbol, Position.closed_at.is_(None)
            )
        ).scalar_one_or_none()

    context = "held" if position is not None else "candidate"
    snapshot_json = build_input_snapshot(
        symbol=symbol,
        context=context,
        cycle_time_ny=clock.now().astimezone(NY_TZ),
        price=item.price,
        regime=evaluation.regime,
        snapshot=item.snapshot,
        bars_1h=item.bars_1h,
        position=position,
        news=None,  # no fetch: the prompt sees "unavailable", not "no news"
    )
    return SmokeInput(
        symbol=symbol,
        context=context,
        snapshot_json=snapshot_json,
        rules=item.rules,
        price=item.price,
        atr_1h=item.snapshot.atr_1h,
        current_stop=position.current_stop_price if position else None,
    )


def _fmt_money(value: Decimal | None) -> str:
    return "n/a" if value is None else f"${float(value):.6f}"


def print_report(
    inputs: SmokeInput,
    response_error: str | None,
    model: str | None,
    tokens_in: int | None,
    tokens_out: int | None,
    cost: Decimal | None,
    latency_ms: int | None,
    validation: ValidationResult,
    prompt_version: str,
    prompt_sha: str,
    *,
    out: Any = print,
) -> None:
    out(f"decision smoke: {inputs.symbol} ({inputs.context})")
    out(f"  rules:  {inputs.rules.status} ({inputs.rules.reason or 'ok'})")
    out(f"  prompt: {prompt_version} sha256:{prompt_sha[:12]}")
    if response_error is not None:
        out(f"  call:   FAILED — {response_error}")
        out("  result: no_call (model_error)")
        return
    out(
        f"  call:   {model} · {latency_ms} ms · "
        f"{tokens_in} in + {tokens_out} out tokens · {_fmt_money(cost)}"
    )
    checks = validation.checks
    failed = [c for c in checks if not c.passed]
    if failed:
        out(f"  checks: {len(failed)} failed — " + "; ".join(c.detail for c in failed))
    elif checks:
        out(f"  checks: all {len(checks)} passed")
    if validation.output is not None:
        o = validation.output
        out(
            f"  model:  {o.action} · confidence {o.confidence} · "
            f"stop {getattr(o, "stop_price", None)} · target {getattr(o, "target_price", None)}"
        )
    out(
        f"  result: {'valid' if validation.valid else 'invalid'} — "
        f"final action {validation.final_action}"
        + (f" ({validation.note})" if validation.note else "")
    )


async def run(
    symbol: str,
    config: AppConfig,
    client: DecisionClient,
    clock: Clock,
    *,
    session_factory: sessionmaker[Session] | None = None,
    out: Any = print,
) -> int:
    """One model call for ``symbol``; prints the report. No DB writes."""
    inputs = build_smoke_input(
        symbol, config, clock, session_factory=session_factory
    )
    prompt = cached_prompt(
        str(config.get("decision", "prompt", default="decision.v1"))
    )
    response = await client.complete(
        system=prompt.text, user=json.dumps(inputs.snapshot_json, indent=2)
    )
    validation = validate_model_output(
        response.raw,
        symbol=symbol,
        context=inputs.context,
        price=inputs.price,
        atr_1h=inputs.atr_1h,
        current_stop=inputs.current_stop,
        call_error=response.error,
        config=config,
        position=(
            inputs.snapshot_json.get("position")
            if isinstance(inputs.snapshot_json.get("position"), dict)
            else None
        ),
    )
    print_report(
        inputs,
        response.error,
        response.model,
        response.tokens_in,
        response.tokens_out,
        token_cost(response, config),
        response.latency_ms,
        validation,
        prompt.version,
        prompt.sha256,
        out=out,
    )
    return 1 if response.error is not None else 0


def main() -> int:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "MSFT").upper()
    config = get_config()
    Path(config.env.sa_db_path).parent.mkdir(parents=True, exist_ok=True)
    run_migrations()
    store = SecretsStore(config, make_clock(config))
    key = store.get("openai")
    if not key:
        print("smoke-decision needs an OpenAI key saved in Settings")
        return 2
    client = make_model_clients(config, secrets=None, default_model=None)
    update = getattr(client, "update_key", None)
    if callable(update):
        update("openai", key)
    try:
        return asyncio.run(run(symbol, config, client, make_clock(config)))
    except ValueError as exc:
        print(f"smoke-decision: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
