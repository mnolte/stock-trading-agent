"""The pre-run estimate (docs/SPEC.md, Backtesting, Model calls).

Calls per LLM strategy = sessions × cycles × that strategy's average real
calls per cycle in the current run — a shadow uses its own average once it
has at least 20 cycles, otherwise live's; with no history the 10-call cap;
rules-only is 0. Cache hits = ``llm_cache`` rows with the same model,
temperature and prompt hash whose cycle time falls in the period. Cost =
(calls − hits) × the strategy's average tokens per call × ``model_prices``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.data.models import CalendarDay
from app.db.models import (
    Backtest,
    Cycle,
    Decision,
    LLMCache,
    Run,
    ShadowDecision,
    Strategy,
)
from app.decide.client import model_price
from app.decide.llm_cache import cache_hit_count
from app.decide.prompt import cached_prompt
from app.market.calendar import CalendarProvider


@dataclass
class StrategySpec:
    """One picked strategy for a run or an estimate."""

    ref: str  # "live" | "rules_only" | "strategy:<id>"
    name: str
    kind: str  # "llm" | "rules_only"
    slug: str
    model: str | None = None
    prompt_version: str | None = None
    temperature: float | None = None
    strategy: Strategy | None = None  # the shadow row, when a shadow runs


@dataclass
class StrategyEstimate:
    ref: str
    name: str
    kind: str
    model: str | None
    calls: int
    cache_hits: int
    avg_tokens: float | None
    cost_usd: Decimal | None
    cutoff: date | None  # knowledge cutoff — None when the strategy is free


@dataclass
class Estimate:
    sessions: int
    cycles_per_session: int
    bars_to_download: int
    download_calls: int
    download_minutes: int
    strategies: list[StrategyEstimate] = field(default_factory=list)
    calls: int = 0
    cache_hits: int = 0
    cost_usd: Decimal = Decimal(0)
    cap_cost_usd: Decimal = Decimal(0)
    cap_remaining_usd: Decimal = Decimal(0)
    cutoff: date | None = None
    cutoff_label: str | None = None
    may_stop_early: bool = False
    disabled_reason: str | None = None


def _prompt_hash(config: AppConfig, prompt_version: str | None) -> str | None:
    version = prompt_version or str(
        config.get("decision", "prompt", default="decision.v1")
    )
    try:
        return cached_prompt(version).sha256
    except Exception:  # noqa: BLE001 — unknown version
        return None


def _live_calls_per_cycle(
    session: Session, max_calls: int
) -> tuple[float, int]:
    """Average real model calls per cycle in the current run."""
    run = session.execute(
        select(Run).order_by(Run.id.desc()).limit(1)
    ).scalar_one_or_none()
    if run is None:
        return float(max_calls), 0
    cycles = session.execute(
        select(func.count(Cycle.id)).where(
            Cycle.kind == "cycle", Cycle.scheduled_time >= run.started_at
        )
    ).scalar_one()
    calls = session.execute(
        select(func.count(Decision.id))
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .where(
            Cycle.kind == "cycle",
            Cycle.scheduled_time >= run.started_at,
            Decision.latency_ms.is_not(None),
        )
    ).scalar_one()
    if not cycles:
        return float(max_calls), 0
    return calls / cycles, cycles


def _shadow_calls_per_cycle(
    session: Session, strategy: Strategy, max_calls: int
) -> tuple[float, int] | None:
    """A shadow's own average once it has ≥ 20 cycles, else None."""
    cycles = session.execute(
        select(func.count(func.distinct(ShadowDecision.cycle_id))).where(
            ShadowDecision.strategy_id == strategy.id,
            ShadowDecision.missed.is_(False),
        )
    ).scalar_one()
    if cycles < 20:
        return None
    calls = session.execute(
        select(func.count(ShadowDecision.id)).where(
            ShadowDecision.strategy_id == strategy.id,
            ShadowDecision.missed.is_(False),
            ShadowDecision.raw_response.is_not(None),
        )
    ).scalar_one()
    return calls / cycles, cycles


def _avg_tokens(
    session: Session, model: str, temperature: float, prompt_hash: str | None
) -> float | None:
    """Average tokens per call for this model/temp/prompt — cache rows first,
    then live decisions, then any recorded call."""
    row = session.execute(
        select(func.avg(LLMCache.tokens)).where(
            LLMCache.model == model,
            LLMCache.temperature == temperature,
            LLMCache.prompt_hash == prompt_hash,
        )
    ).scalar_one_or_none()
    if row is not None:
        return float(row)
    row = session.execute(
        select(func.avg(Decision.tokens)).where(
            Decision.model == model, Decision.tokens.is_not(None)
        )
    ).scalar_one_or_none()
    if row is not None:
        return float(row)
    row = session.execute(select(func.avg(LLMCache.tokens))).scalar_one_or_none()
    return float(row) if row is not None else None


def spent_this_month(
    session_factory: sessionmaker[Session], today: date
) -> Decimal:
    """Backtest spend already used this calendar month (the $20 cap)."""
    first = today.replace(day=1)
    with session_factory() as session:
        rows = session.execute(
            select(Backtest.cost_usd).where(
                Backtest.created_at
                >= _naive_utc(datetime.combine(first, datetime.min.time())),
                Backtest.status.in_(
                    [
                        "done",
                        "cancelled",
                        "stopped_at_cost_cap",
                        "interrupted",
                        "running",
                        "downloading",
                        "queued",
                    ]
                ),
            )
        ).scalars().all()
    return sum((r for r in rows), Decimal(0))


def estimate(
    *,
    config: AppConfig,
    session_factory: sessionmaker[Session],
    calendar: CalendarProvider,
    start: date,
    end: date,
    strategies: list[StrategySpec],
    symbols: list[str],
    store: Any,
    today: date,
) -> Estimate:
    """Compute the pre-run estimate shown on the Backtest screen."""
    sessions = [
        day
        for day in _calendar_span(calendar, start, end)
        if start <= day.date <= end
    ]
    n_sessions = len(sessions)
    cycles_per = len(config.get("schedule", "cycles", default=[]) or [])
    max_calls = int(config.get("decision", "max_calls_per_cycle", default=10))

    out = Estimate(
        sessions=n_sessions,
        cycles_per_session=cycles_per,
        bars_to_download=0,
        download_calls=0,
        download_minutes=0,
    )

    # ---- bars ------------------------------------------------------
    # The worker fills the period plus the indicator lookback the live
    # cache keeps, so the estimate counts both ranges.
    lookback = max(
        int(config.get("schedule", "history_sessions", default=60)),
        int(config.get("schedule", "daily_history_sessions", default=260)),
    ) * 2
    fill_sessions = _calendar_span(calendar, start - timedelta(days=lookback), end)
    missing_bars = 0
    calls = 0
    for symbol in symbols:
        covered = store.covered_session_dates(symbol, fill_sessions)
        missing_sessions = [s for s in fill_sessions if s.date not in covered]
        missing_bars += len(missing_sessions) * 78
        # One Alpaca request per symbol per ~63-session quarter.
        calls += max(0, -(-len(missing_sessions) // 63))
    out.bars_to_download = missing_bars
    out.download_calls = calls
    rpm = int(config.get("backtest", "alpaca_calls_per_minute", default=40))
    out.download_minutes = -(-calls // rpm) if calls else 0

    # ---- per-strategy calls, hits, cost -----------------------------
    cap_usd = Decimal(
        str(config.get("backtest", "monthly_cost_cap_usd", default=20))
    )
    out.cap_remaining_usd = cap_usd - spent_this_month(session_factory, today)
    with session_factory() as session:
        live_avg, live_cycles = _live_calls_per_cycle(session, max_calls)
        for spec in strategies:
            if spec.kind == "rules_only":
                out.strategies.append(
                    StrategyEstimate(
                        ref=spec.ref,
                        name=spec.name,
                        kind=spec.kind,
                        model=None,
                        calls=0,
                        cache_hits=0,
                        avg_tokens=None,
                        cost_usd=Decimal(0),
                        cutoff=None,
                    )
                )
                continue
            model = spec.model or str(
                config.get("decision", "model", default="")
            )
            temperature = (
                spec.temperature
                if spec.temperature is not None
                else float(config.get("decision", "temperature", default=0.2))
            )
            prompt_hash = _prompt_hash(config, spec.prompt_version)
            if spec.strategy is not None:
                own = _shadow_calls_per_cycle(session, spec.strategy, max_calls)
                per_cycle = own[0] if own is not None else live_avg
            else:
                per_cycle = live_avg
            calls = int(n_sessions * cycles_per * per_cycle)
            upper = n_sessions * cycles_per * max_calls
            hits = 0
            if prompt_hash:
                hits = cache_hit_count(
                    session_factory,
                    model=model,
                    temperature=temperature,
                    prompt_hash=prompt_hash,
                    start=datetime.combine(start, datetime.min.time()),
                    end=datetime.combine(end, datetime.max.time()),
                )
            hits = min(hits, calls)
            avg_tokens = _avg_tokens(session, model, temperature, prompt_hash)
            price = model_price(config.get("model_prices", default={}), model)
            cost: Decimal | None = None
            if avg_tokens is not None and price is not None:
                in_p, out_p = price
                # tokens split ~85/15 in/out for these prompts; the
                # estimate needs a total → per-token blended price.
                per_tok = Decimal("0.85") * in_p + Decimal("0.15") * out_p
                cost = Decimal(str(calls - hits)) * Decimal(
                    str(avg_tokens)
                ) * per_tok / Decimal(1_000_000)
            cutoff = _model_cutoff(config, model)
            out.strategies.append(
                StrategyEstimate(
                    ref=spec.ref,
                    name=spec.name,
                    kind=spec.kind,
                    model=model,
                    calls=calls,
                    cache_hits=hits,
                    avg_tokens=avg_tokens,
                    cost_usd=cost,
                    cutoff=cutoff,
                )
            )
            out.calls += calls
            out.cache_hits += hits
            if cost is not None:
                out.cost_usd += cost
            # Upper bound: every call made at the 10-call cap.
            if avg_tokens is not None and price is not None:
                in_p, out_p = price
                per_tok = Decimal("0.85") * in_p + Decimal("0.15") * out_p
                out.cap_cost_usd += (
                    Decimal(upper - hits)
                    * Decimal(str(avg_tokens))
                    * per_tok
                    / Decimal(1_000_000)
                )
            else:
                out.cap_cost_usd = Decimal(-1)  # unknown → bound unknown
            if cutoff is not None and start <= cutoff:
                out.cutoff = cutoff
                out.cutoff_label = (
                    f"Period starts before {model}'s knowledge cutoff"
                )
    if out.cap_cost_usd < 0:
        out.cap_cost_usd = out.cost_usd
    if out.cutoff is not None:
        out.disabled_reason = out.cutoff_label
    elif out.cost_usd > out.cap_remaining_usd:
        out.disabled_reason = (
            f"estimated cost ${out.cost_usd:.2f} exceeds the "
            f"${out.cap_remaining_usd:.2f} left on this month's cap"
        )
    elif out.cap_cost_usd > out.cap_remaining_usd:
        out.may_stop_early = True
    return out


def _model_cutoff(config: AppConfig, model: str) -> date | None:
    raw = (config.get("backtest", "model_cutoffs", default={}) or {}).get(model)
    if raw is None:
        return None
    return date.fromisoformat(str(raw))


def _calendar_span(
    calendar: CalendarProvider, start: date, end: date
) -> list[CalendarDay]:
    days: list[CalendarDay] = []
    cursor = start
    while cursor <= end:
        day = calendar.session_for(cursor)
        if day is not None:
            days.append(day)
        cursor = cursor + timedelta(days=1)
    return days
