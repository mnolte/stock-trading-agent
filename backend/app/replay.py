"""Replay a decision prompt over stored snapshots (docs/SPEC.md, Replays).

``make replay PROMPT=decision.v2 FROM=2026-10-20 TO=2026-10-25 [--yes]
[APPROX=1]`` runs a prompt on the live run's stored input snapshots:
for each candidate and held decision the levels and options are rebuilt
from the cached 5-minute bars as they were at that cycle's time — never
later — then the model picks an option. The chosen option is scored on
the bars of the next five sessions (first touch and R), and the
rules-only baseline answers the same snapshots for comparison.

Every snapshot gets one status before anything else: ``replayed`` (the
builder ran — ``no_options`` only ever means the builder itself returned
none), ``not_replayable`` with a reason (``missing_reference``,
``price_unavailable``, ``missing_atr``, ``missing_bars`` — no model call,
no cost, excluded from every rate), or ``replayed_approx`` when
``--approx-reference`` fills a missing ``stop_reference_price`` with
min(price, last closed 5-minute bar close) — reported separately, never
in the main rates.

Replays never write to the live tables: every artefact lands in
``data/replays/<id>/`` (``results.jsonl``, ``report.md``, ``meta.json``).
The estimated cost is shown first; above
``decision.replay.confirm_above_usd`` the run refuses without ``--yes``.
Replay spend counts toward the backtest monthly cap.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import BarCache, _naive_utc
from app.data.models import Bar5m, CalendarDay
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Outcome, Position, Run
from app.decide.client import DecisionClient, ModelResponse, token_cost
from app.decide.output import (
    ModelOutputV3,
    ValidationResult,
    validate_model_output,
    validate_model_output_v2,
    validate_model_output_v3,
)
from app.decide.prompt import cached_prompt, prompt_spec
from app.decide.v2inputs import (
    HeldContext,
    V2Build,
    build_v2_fields,
    rebuild_bars,
)
from app.decide.v3 import (
    HeldV3,
    build_v3_fields,
    stop_basis_info,
    v3_send_snapshot,
)
from app.llm.clients import make_model_clients
from app.llm.models import model_provider
from app.market.calendar import StaticCalendarProvider
from app.market.clock import NY_TZ
from app.risk.earnings import next_earnings

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")

REPLAYED = "replayed"
REPLAYED_APPROX = "replayed_approx"
NOT_REPLAYABLE = "not_replayable"
NOT_REPLAYABLE_REASONS = (
    "missing_reference",
    "price_unavailable",
    "missing_atr",
    "missing_bars",
)


# ----------------------------------------------------------------------
# sessions from the bar cache — replay needs no calendar service


def _sessions_from_bars(
    cache: BarCache, symbol: str, start: datetime, end: datetime
) -> list[CalendarDay]:
    """CalendarDays derived from cached 5m bars (no network, no service)."""
    bars = cache.get(symbol, start, end)
    days: dict[date, list[Bar5m]] = {}
    for bar in bars:
        days.setdefault(bar.start.astimezone(NY_TZ).date(), []).append(bar)
    return [
        CalendarDay(
            date=d,
            open=min(b.start for b in day_bars),
            close=max(b.start for b in day_bars) + timedelta(minutes=5),
        )
        for d, day_bars in sorted(days.items())
    ]


# ----------------------------------------------------------------------
# scoring: first touch of stop vs target over the next sessions


@dataclass(frozen=True)
class Score:
    touched: str  # "stop" | "target" | "none" | "no_fill"
    r_multiple: Decimal | None
    sessions: int


def _score_option(
    bars: list[Bar5m],
    *,
    limit: Decimal | None,
    stop: Decimal,
    target: Decimal,
    horizon_end: datetime,
) -> Score:
    """First touch of stop or target once the limit filled.

    The entry fills at the limit when a bar trades through it; after that
    the first bar touching the stop or the target decides the outcome —
    a bar touching both counts the stop (the conservative order).
    """
    if limit is None:
        return Score("no_fill", None, 0)
    risk = limit - stop
    if risk <= 0:
        return Score("no_fill", None, 0)
    filled = False
    for bar in bars:
        if bar.start > horizon_end:
            break
        if not filled:
            if bar.low > limit:
                continue
            filled = True
        if bar.low <= stop:
            r = (stop - limit) / risk
            return Score("stop", r, _sessions_in(bars, bar.start))
        if bar.high >= target:
            r = (target - limit) / risk
            return Score("target", r, _sessions_in(bars, bar.start))
    if not filled:
        return Score("no_fill", None, 0)
    last = bars[-1].close if bars else limit
    return Score("none", (last - limit) / risk, _sessions_in(bars, horizon_end))


def _sessions_in(bars: list[Bar5m], until: datetime) -> int:
    return len({b.start.astimezone(NY_TZ).date() for b in bars if b.start <= until})


def _dec(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except ArithmeticError:
        return None


def _last_closed_bar(cache: BarCache, symbol: str, at: datetime) -> Bar5m | None:
    """The last 5-minute bar that closed at or before ``at`` (the
    ``--approx-reference`` bar close)."""
    bars = cache.get(symbol, at - timedelta(days=10), at)
    closed = [b for b in bars if b.start + timedelta(minutes=5) <= at]
    return closed[-1] if closed else None


# ----------------------------------------------------------------------
# one replayed decision


@dataclass
class ReplayRow:
    cycle_id: int
    symbol: str
    context: str
    at: datetime
    decision_id: int
    v1_action: str | None
    model_action: str | None
    final_action: str
    option_id: str | None
    option_basis: dict[str, str | None]
    valid: bool
    explanation_passed: bool | None
    explanation_detail: dict[str, Any] | None
    score: Score | None
    cost: Decimal | None
    error: str | None
    status: str = REPLAYED
    offered: list[str] | None = None
    # decision.v3 details for the report additions (docs/SPEC.md,
    # Decision v3 → Replay); None on v1/v2 rows.
    detail: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "symbol": self.symbol,
            "context": self.context,
            "at": self.at.isoformat(),
            "decision_id": self.decision_id,
            "status": self.status,
            "v1_action": self.v1_action,
            "model_action": self.model_action,
            "final_action": self.final_action,
            "offered": self.offered,
            "option_id": self.option_id,
            "stop_basis": self.option_basis.get("stop_basis"),
            "target_basis": self.option_basis.get("target_basis"),
            "valid": self.valid,
            "explanation_passed": self.explanation_passed,
            "explanation": self.explanation_detail,
            "touched": self.score.touched if self.score else None,
            "r_multiple": (
                float(round(self.score.r_multiple, 3))
                if self.score and self.score.r_multiple is not None
                else None
            ),
            "score_sessions": self.score.sessions if self.score else None,
            "cost_usd": float(self.cost) if self.cost is not None else None,
            "error": self.error,
        }


@dataclass(frozen=True)
class SnapshotRow:
    """The columns a replay needs — the live table may predate the v2
    columns (read-only, no migration required)."""

    id: int
    cycle_id: int
    symbol: str
    context: str
    input_snapshot: dict[str, Any] | None
    final_action: str | None


def _candidate_snapshots(
    session: Session, run_start: datetime, from_day: date, to_day: date
) -> list[tuple[SnapshotRow, datetime]]:
    """Stored input snapshots of the live run within the session range."""
    start = datetime.combine(from_day, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(to_day + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    rows = session.execute(
        select(
            Decision.id,
            Decision.cycle_id,
            Decision.symbol,
            Decision.context,
            Decision.input_snapshot,
            Decision.final_action,
            Cycle.scheduled_time,
        )
        .join(Cycle, Decision.cycle_id == Cycle.id)
        .where(
            Cycle.kind == "cycle",
            Cycle.scheduled_time >= _naive_utc(run_start),
            Cycle.scheduled_time >= _naive_utc(start),
            Cycle.scheduled_time < _naive_utc(end),
            Decision.context.in_(("candidate", "held")),
        )
        .order_by(Cycle.scheduled_time, Decision.id)
    ).all()
    out: list[tuple[SnapshotRow, datetime]] = []
    for _id, cycle_id, symbol, context, snap, action, scheduled in rows:
        # Every stored snapshot gets a status — a missing price becomes
        # not_replayable/price_unavailable, never silently dropped.
        out.append(
            (
                SnapshotRow(
                    id=_id,
                    cycle_id=cycle_id,
                    symbol=symbol,
                    context=context,
                    input_snapshot=snap,
                    final_action=action,
                ),
                scheduled,
            )
        )
    return out


async def replay(
    *,
    prompt_version: str,
    from_day: date,
    to_day: date,
    model: str | None,
    assume_yes: bool,
    approx_reference: bool = False,
    session_factory: sessionmaker[Session] | None = None,
    client: DecisionClient | None = None,
    config: AppConfig | None = None,
    out_dir: Path | None = None,
    sessions_provider: Any = None,
) -> Path | None:
    """Run the replay; returns the replay directory or None when refused."""
    config = config or AppConfig()
    sf = session_factory or get_session_maker()
    cache = BarCache(sf)
    prompt = cached_prompt(prompt_version)
    spec = prompt_spec(prompt_version)
    use_model = str(model or config.get("decision", "model", default="gpt-4o-mini"))

    with sf() as session:
        run_start = session.execute(
            select(Run.started_at).order_by(Run.id.desc()).limit(1)
        ).scalar()
        decisions = _candidate_snapshots(
            session,
            run_start or datetime(2000, 1, 1, tzinfo=UTC),
            from_day,
            to_day,
        )
    if not decisions:
        print("replay: no stored snapshots in the range")
        return None

    # --- estimate before spending -------------------------------------
    est_tokens_in = int(len(prompt.text) / 4) + 900  # snapshot ~900 tokens
    est_tokens_out = 120
    unit = token_cost(
        ModelResponse(
            raw=None,
            model=use_model,
            tokens_in=est_tokens_in,
            tokens_out=est_tokens_out,
            latency_ms=0,
        ),
        config,
        model=use_model,
    )
    estimate_usd = (unit or Decimal(0)) * len(decisions)
    threshold = Decimal(str(config.get("decision", "replay", "confirm_above_usd", default=2.0)))
    print(
        f"replay: {len(decisions)} snapshots, est. ${estimate_usd:.2f} "
        f"(prompt {prompt_version}, model {use_model})"
    )
    if unit is not None and estimate_usd > threshold and not assume_yes:
        print(
            f"replay: estimated cost ${estimate_usd:.2f} exceeds "
            f"decision.replay.confirm_above_usd ${threshold} — "
            "rerun with --yes to confirm"
        )
        return None

    if client is None:
        from app.market.clock import RealClock
        from app.secrets_service import SecretsStore

        secrets = SecretsStore(config, RealClock())
        client = make_model_clients(config, secrets)

    # Sessions for the level builder come from the bar cache itself —
    # replay never needs a calendar service or the network for bars.
    if sessions_provider is None:
        first_symbol = decisions[0][0].symbol
        days = _sessions_from_bars(
            cache,
            first_symbol,
            datetime.combine(from_day, datetime.min.time(), tzinfo=UTC) - timedelta(days=120),
            datetime.now(UTC),
        )
        sessions_provider = StaticCalendarProvider(days)

    replay_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = Path(config.env.sa_data_dir) / "replays" / replay_id
    if out_dir is not None:
        root = out_dir
    root.mkdir(parents=True, exist_ok=True)

    rows: list[ReplayRow] = []
    rules_rows: list[ReplayRow] = []
    total_cost = Decimal(0)
    stop_atr = Decimal(str(config.get("shadows", "rules_only", "stop_atr", default=2.0)))
    target_r = Decimal(str(config.get("shadows", "rules_only", "target_r", default=2.0)))
    horizon_days = 5

    for decision, scheduled in decisions:
        snap = cast("dict[str, Any]", dict(decision.input_snapshot or {}))
        symbol = decision.symbol
        price = _dec(snap.get("price"))
        atr_1h = _dec((snap.get("indicators_1h") or {}).get("atr14"))
        reference = _dec(snap.get("stop_reference_price"))
        pos = cast("dict[str, Any]", snap.get("position") or {})
        held = None
        if decision.context == "held" and pos:
            held = HeldContext(
                entry=_dec(pos.get("entry")),
                current_stop=_dec(pos.get("stop")),
                initial_stop=_dec(pos.get("initial_stop")),
                opened_at=(
                    datetime.fromisoformat(str(pos["opened_at"])) if pos.get("opened_at") else None
                ),
            )
        bars_1h, daily, todays = rebuild_bars(
            symbol,
            scheduled.replace(tzinfo=UTC),
            calendar=sessions_provider,
            bars_reader=cache.get,
        )

        # --- one status before anything else (docs/SPEC.md, Replays)
        status = REPLAYED
        error: str | None = None
        if price is None:
            status, error = NOT_REPLAYABLE, "price_unavailable"
        elif atr_1h is None or atr_1h <= 0:
            status, error = NOT_REPLAYABLE, "missing_atr"
        elif not (bars_1h or daily or todays):
            status, error = NOT_REPLAYABLE, "missing_bars"
        elif reference is None:
            if approx_reference:
                last_bar = _last_closed_bar(cache, symbol, scheduled.replace(tzinfo=UTC))
                if last_bar is None:
                    status, error = NOT_REPLAYABLE, "missing_bars"
                else:
                    # min(stored price, last closed 5m close); the
                    # plausible ask isn't stored pre-PR 15, so the
                    # reference can only sit too high (docs/SPEC.md).
                    reference = min(price, last_bar.close)
                    snap["stop_reference_price"] = str(reference)
                    status = REPLAYED_APPROX
            else:
                status, error = NOT_REPLAYABLE, "missing_reference"

        v2: V2Build | None = None
        offered: list[str] | None = None
        held3: HeldV3 | None = None
        earnings3: tuple[date, str] | None = None
        stored_first_touch: str | None = None
        if spec.is_v3:
            with sf() as qsession:
                earnings3 = next_earnings(
                    qsession, [symbol], scheduled.replace(tzinfo=UTC)
                ).get(symbol)
                out_row = qsession.execute(
                    select(Outcome).where(Outcome.decision_id == decision.id)
                ).scalar_one_or_none()
                stored_first_touch = (
                    out_row.first_touch if out_row is not None else None
                )
                if decision.context == "held" and pos:
                    prow = qsession.execute(
                        select(Position)
                        .where(
                            Position.symbol == symbol,
                            Position.opened_at <= _naive_utc(scheduled.replace(tzinfo=UTC)),
                        )
                        .order_by(Position.opened_at.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                    entry_dec = (
                        qsession.get(Decision, prow.entry_decision_id)
                        if prow is not None and prow.entry_decision_id is not None
                        else None
                    )
                    opened = pos.get("opened_at")
                    thesis = pos.get("original_thesis")
                    held3 = HeldV3(
                        entry=_dec(pos.get("entry")),
                        qty=int(pos["qty"]) if pos.get("qty") is not None else None,
                        initial_stop=_dec(pos.get("initial_stop")),
                        current_stop=_dec(pos.get("stop")),
                        target=_dec(pos.get("target")),
                        opened_at=(
                            datetime.fromisoformat(str(opened))
                            if opened else None
                        ),
                        entry_thesis=str(thesis) if thesis else None,
                        stop_basis=stop_basis_info(entry_dec),
                        earnings=earnings3,
                        entry_levels=tuple(
                            cast(
                                "list[dict[str, Any]]",
                                (entry_dec.levels or {}).get("levels") or (),
                            )
                        )
                        if entry_dec is not None
                        else (),
                    )
        if status != NOT_REPLAYABLE:
            if spec.is_v3:
                from app.shadows.runner import (
                    indicators_from_snapshot,
                    regime_from_snapshot,
                )

                v2 = build_v3_fields(
                    context=decision.context,
                    base=snap,
                    price=price,
                    reference=reference,
                    atr_1h=atr_1h,
                    indicators=indicators_from_snapshot(symbol, snap),
                    regime=regime_from_snapshot(snap, config),
                    bars_1h=bars_1h,
                    daily=daily,
                    today_5m=todays,
                    earnings=earnings3,
                    held=held3,
                    calendar=sessions_provider,
                    config=config,
                    cycle_time_ny=scheduled.replace(tzinfo=UTC),
                )
            else:
                v2 = build_v2_fields(
                    context=decision.context,
                    bars_1h=bars_1h,
                    daily=daily,
                    today_5m=todays,
                    price=price,
                    reference=reference,
                    atr_1h=atr_1h,
                    config=config,
                    held=held,
                )
            snap.update(v2.fields)
            offered = [
                str(o.get("id"))
                for o in (v2.fields.get("options") or v2.fields.get("stop_options") or [])
            ]

        # --- model call for this snapshot
        score: Score | None = None
        option_basis: dict[str, str | None] = {}
        option_id: str | None = None
        final_action = "no_call" if decision.context == "candidate" else "hold"
        model_action: str | None = None
        valid = False
        explanation_passed: bool | None = None
        explanation_detail: dict[str, Any] | None = None
        cost: Decimal | None = None
        if v2 is None:
            pass  # not_replayable: no builder run, no model call, no cost
        elif decision.context == "candidate" and not v2.fields["options"]:
            # The builder ran and no code-built option survived — the
            # model is never asked (docs/SPEC.md).
            final_action = "no_call"
            error = f"no_options: {v2.reason or 'no option met the rules'}"
        else:
            response = await client.complete(
                system=prompt.text,
                user=json.dumps(
                    v3_send_snapshot(snap) if spec.is_v3 else snap, indent=2
                ),
                schema=spec.json_schema,
                model=use_model,
                temperature=float(
                    config.get("decision", "temperature", default=0)
                ),
            )
            cost = token_cost(response, config, model=use_model)
            if cost is not None:
                total_cost += cost
            if spec.is_v3:
                validation: ValidationResult = validate_model_output_v3(
                    response.raw,
                    symbol=symbol,
                    context=decision.context,
                    snapshot=snap,
                    atr_1h=atr_1h,
                    current_stop=(
                        held3.current_stop if held3 is not None
                        else held.current_stop if held else None
                    ),
                    call_error=response.error,
                    config=config,
                )
                if isinstance(validation.output, ModelOutputV3):
                    for flag in validation.output.data_flags:
                        logger.warning(
                            "model.data_flag %s: model flagged a data conflict (%s)",
                            symbol, flag,
                        )
            elif spec.uses_options:
                validation = validate_model_output_v2(
                    response.raw,
                    symbol=symbol,
                    context=decision.context,
                    snapshot=snap,
                    atr_1h=atr_1h,
                    current_stop=held.current_stop if held else None,
                    call_error=response.error,
                    config=config,
                )
            else:
                validation = validate_model_output(
                    response.raw,
                    symbol=symbol,
                    context=decision.context,
                    price=price,
                    atr_1h=atr_1h,
                    current_stop=held.current_stop if held else None,
                    call_error=response.error,
                    config=config,
                    position=(
                        snap.get("position")
                        if isinstance(snap.get("position"), dict)
                        else None
                    ),
                )
            model_action = validation.model_action
            final_action = validation.final_action
            valid = validation.valid
            error = validation.error
            if validation.explanation is not None:
                explanation_passed = validation.explanation.passed
                explanation_detail = validation.explanation.to_json()
            chosen = validation.chosen_option
            if chosen is not None:
                option_id = str(chosen.get("id"))
                option_basis = {
                    "stop_basis": str(chosen.get("stop_basis") or chosen.get("basis")),
                    "target_basis": (
                        str(chosen.get("target_basis")) if chosen.get("target_basis") else None
                    ),
                }
                stop = _dec(chosen.get("stop"))
                target = _dec(chosen.get("target"))
                if final_action == "enter" and stop is not None and target is not None:
                    future = cache.get(
                        symbol,
                        scheduled.replace(tzinfo=UTC),
                        scheduled.replace(tzinfo=UTC) + timedelta(days=horizon_days * 2 + 4),
                    )
                    horizon_sessions = {
                        b.start.astimezone(NY_TZ).date()
                        for b in future
                        if b.start > scheduled.replace(tzinfo=UTC)
                    }
                    last_day = sorted(horizon_sessions)[:horizon_days]
                    horizon_end = (
                        datetime.combine(last_day[-1], datetime.max.time(), tzinfo=UTC)
                        if last_day
                        else scheduled.replace(tzinfo=UTC)
                    )
                    score = _score_option(
                        [b for b in future if b.start > scheduled.replace(tzinfo=UTC)],
                        limit=_dec((snap.get("constraints") or {}).get("entry_limit")),
                        stop=stop,
                        target=target,
                        horizon_end=horizon_end,
                    )
        # --- decision.v3 report details (docs/SPEC.md, Decision v3 →
        # Replay): everything the v3 sections need, from the build and
        # the validation, plus the stored decision's outcome.
        detail: dict[str, Any] | None = None
        if spec.is_v3 and v2 is not None:
            v3_out = (
                validation.output
                if "validation" in dir()
                and isinstance(validation.output, ModelOutputV3)
                else None
            )
            chosen3 = (
                validation.chosen_option if "validation" in dir() else None
            )
            held_stop: dict[str, Any] | None = None
            if (
                decision.context == "held"
                and final_action == "tighten_stop"
                and chosen3 is not None
                and held3 is not None
            ):
                new_stop = _dec(chosen3.get("stop"))
                if new_stop is not None:
                    future = cache.get(
                        symbol,
                        scheduled.replace(tzinfo=UTC),
                        scheduled.replace(tzinfo=UTC)
                        + timedelta(days=horizon_days * 2 + 4),
                    )
                    sessions_seen: set[date] = set()
                    hit = False
                    for b in future:
                        if b.start <= scheduled.replace(tzinfo=UTC):
                            continue
                        if len(sessions_seen) >= horizon_days:
                            break
                        sessions_seen.add(b.start.astimezone(NY_TZ).date())
                        if b.low <= new_stop:
                            hit = True
                            break
                    initial_risk = (
                        held3.entry - held3.initial_stop
                        if held3.entry is not None
                        and held3.initial_stop is not None
                        and held3.entry > held3.initial_stop
                        else None
                    )
                    r_at_old = (
                        (held3.current_stop - held3.entry) / initial_risk
                        if initial_risk
                        and held3.current_stop is not None
                        and held3.entry is not None
                        else None
                    )
                    locked = _dec(chosen3.get("locked_r"))
                    held_stop = {
                        "stop": float(new_stop),
                        "locked_r": float(locked) if locked is not None else None,
                        "stopped_out_in_5": hit,
                        "r_given_up": (
                            float(locked - r_at_old)
                            if locked is not None and r_at_old is not None
                            else None
                        ),
                    }
            detail = {
                "room_removed": v2.options_column.get("removed") or [],
                "room_reason": v2.reason,
                "reason": (
                    validation.reason if "validation" in dir() else None
                ),
                "confidence": (
                    float(v3_out.confidence) if v3_out is not None else None
                ),
                "conflicts": list(v3_out.conflicts) if v3_out is not None else [],
                "data_flags": (
                    list(v3_out.data_flags) if v3_out is not None else []
                ),
                "chosen": (
                    {
                        "id": chosen3.get("id"),
                        "rr": (
                            float(_dec(chosen3.get("rr")) or 0)
                            if _dec(chosen3.get("rr")) is not None
                            else None
                        ),
                        "breakeven_confidence": (
                            float(_dec(chosen3.get("breakeven_confidence")) or 0)
                            if _dec(chosen3.get("breakeven_confidence")) is not None
                            else None
                        ),
                    }
                    if chosen3 is not None
                    else None
                ),
                "dropped_stop_options": (
                    v2.options_column.get("dropped_stop_options") or []
                ),
                "neutral_probability": (
                    (snap.get("position") or {}).get("neutral_probability")
                    if decision.context == "held"
                    else None
                ),
                "claims": (
                    validation.claims.to_json()
                    if "validation" in dir()
                    and validation.claims is not None
                    else None
                ),
                "held_stop": held_stop,
                "stored_first_touch": stored_first_touch,
            }
        rows.append(
            ReplayRow(
                cycle_id=decision.cycle_id,
                symbol=symbol,
                context=decision.context,
                at=scheduled.replace(tzinfo=UTC),
                decision_id=decision.id,
                v1_action=decision.final_action,
                model_action=model_action,
                final_action=final_action,
                option_id=option_id,
                option_basis=option_basis,
                valid=valid,
                explanation_passed=explanation_passed,
                explanation_detail=explanation_detail,
                score=score,
                cost=cost,
                error=error,
                status=status,
                offered=offered,
                detail=detail,
            )
        )

        # --- rules-only baseline on the same snapshot
        r_score: Score | None = None
        if decision.context == "candidate" and price is not None and atr_1h:
            r_stop = price - stop_atr * atr_1h
            r_target = price + target_r * (price - r_stop)
            future = cache.get(
                symbol,
                scheduled.replace(tzinfo=UTC),
                scheduled.replace(tzinfo=UTC) + timedelta(days=horizon_days * 2 + 4),
            )
            horizon_sessions = {
                b.start.astimezone(NY_TZ).date()
                for b in future
                if b.start > scheduled.replace(tzinfo=UTC)
            }
            last_day = sorted(horizon_sessions)[:horizon_days]
            horizon_end = (
                datetime.combine(last_day[-1], datetime.max.time(), tzinfo=UTC)
                if last_day
                else scheduled.replace(tzinfo=UTC)
            )
            r_score = _score_option(
                [b for b in future if b.start > scheduled.replace(tzinfo=UTC)],
                limit=price.quantize(CENT, rounding=ROUND_HALF_UP),
                stop=r_stop,
                target=r_target,
                horizon_end=horizon_end,
            )
        rules_rows.append(
            ReplayRow(
                cycle_id=decision.cycle_id,
                symbol=symbol,
                context=decision.context,
                at=scheduled.replace(tzinfo=UTC),
                decision_id=decision.id,
                v1_action=decision.final_action,
                model_action="enter" if decision.context == "candidate" else "hold",
                final_action="enter" if decision.context == "candidate" else "hold",
                option_id=None,
                option_basis={
                    "stop_basis": f"{stop_atr.normalize()} × ATR",
                    "target_basis": f"{target_r.normalize()}R",
                },
                valid=True,
                explanation_passed=None,
                explanation_detail=None,
                score=r_score,
                cost=None,
                error=None,
                status="rules_only",
            )
        )

    # --- artefacts -----------------------------------------------------
    results_path = root / "results.jsonl"
    with results_path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row.to_json()) + "\n")
    with (root / "rules_only.jsonl").open("w") as fh:
        for row in rules_rows:
            fh.write(json.dumps(row.to_json()) + "\n")
    report = _report(
        replay_id,
        prompt_version,
        use_model,
        model_provider(config, use_model),
        from_day,
        to_day,
        rows,
        rules_rows,
        estimate_usd,
        total_cost,
    )
    (root / "report.md").write_text(report)
    (root / "meta.json").write_text(
        json.dumps(
            {
                "id": replay_id,
                "prompt": prompt_version,
                "model": use_model,
                "from": from_day.isoformat(),
                "to": to_day.isoformat(),
                "snapshots": len(decisions),
                "approx_reference": approx_reference,
                "statuses": {
                    status: sum(1 for r in rows if r.status == status)
                    for status in (REPLAYED, REPLAYED_APPROX, NOT_REPLAYABLE)
                },
                "estimated_usd": float(estimate_usd),
                "cost_usd": float(total_cost),
                "not_repeatable": (
                    "the model has no temperature 0"
                    if model_provider(config, use_model) == "anthropic"
                    else None
                ),
                "created_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    )
    print(f"replay: wrote {root}")
    return root


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return (sum(values, Decimal(0)) / len(values)) if values else None


def _no_options_reason(error: str | None) -> str:
    return (error or "").removeprefix("no_options: ") or "unknown"


def _entries_per_session(entered: list[ReplayRow]) -> list[str]:
    """Entries and their R grouped by New York session — same-day
    entries ride one market move, so the distinct-session count is the
    honest sample size (docs/SPEC.md, Replays — entries per session)."""
    by_session: dict[date, list[ReplayRow]] = {}
    for r in entered:
        by_session.setdefault(r.at.astimezone(NY_TZ).date(), []).append(r)
    if not by_session:
        return ["- entries per session: —"]
    n = len(by_session)
    lines = [f"- entries per session: {n} distinct session{'s' if n != 1 else ''}"]
    for day in sorted(by_session):
        day_rows = by_session[day]
        rs = [
            r.score.r_multiple
            for r in day_rows
            if r.score is not None and r.score.r_multiple is not None
        ]
        mean_r = _mean(rs)
        lines.append(
            f"  - {day}: {len(day_rows)} "
            f"{'entry' if len(day_rows) == 1 else 'entries'}"
            + (f", mean R {float(mean_r):+.2f} (n={len(rs)})" if mean_r is not None else "")
        )
    return lines


def _report(
    replay_id: str,
    prompt: str,
    model: str,
    provider: str,
    from_day: date,
    to_day: date,
    rows: list[ReplayRow],
    rules_rows: list[ReplayRow],
    estimate: Decimal,
    cost: Decimal,
) -> str:
    replayed = [r for r in rows if r.status == REPLAYED]
    approx = [r for r in rows if r.status == REPLAYED_APPROX]
    not_replayable = [r for r in rows if r.status == NOT_REPLAYABLE]

    # --- counts per status first: no_options is a builder outcome,
    # never a missing input (docs/SPEC.md, Replays).
    status_lines = [
        "## Statuses",
        "",
    ]
    for label, group in (("replayed", replayed), ("replayed_approx", approx)):
        if not group:
            continue
        calls = sum(1 for r in group if r.model_action is not None)
        no_opts = [r for r in group if (r.error or "").startswith("no_options")]
        status_lines.append(f"- {label}: {len(group)} (calls {calls} · no_options {len(no_opts)})")
        reasons: dict[str, int] = {}
        for r in no_opts:
            key = _no_options_reason(r.error)
            reasons[key] = reasons.get(key, 0) + 1
        for reason, n in sorted(reasons.items()):
            status_lines.append(f"  - no_options: {reason} — {n}")
    nr_reasons: dict[str, int] = {}
    for r in not_replayable:
        nr_reasons[r.error or "?"] = nr_reasons.get(r.error or "?", 0) + 1
    nr_detail = " · ".join(
        f"{reason} {nr_reasons.get(reason, 0)}"
        for reason in NOT_REPLAYABLE_REASONS
        if nr_reasons.get(reason)
    )
    status_lines += [
        f"- not_replayable: {len(not_replayable)}" + (f" — {nr_detail}" if nr_detail else ""),
        f"- total snapshots: {len(rows)}",
        "",
    ]

    def stats(group: list[ReplayRow], what: str) -> list[str]:
        """Every rate over the rows in ``group`` only, with its base
        stated (docs/SPEC.md, Replays — base of the rates)."""
        candidates = [r for r in group if r.context == "candidate"]
        called = [r for r in group if r.model_action is not None]
        invalid = [r for r in called if not r.valid]
        expl_fail = [r for r in group if r.explanation_passed is False]
        v1_same = [r for r in group if r.model_action is not None and r.v1_action == r.final_action]
        entered = [r for r in candidates if r.final_action == "enter"]
        touched = [r.score.touched for r in candidates if r.score is not None]
        rs = [
            r.score.r_multiple
            for r in candidates
            if r.score is not None and r.score.r_multiple is not None
        ]
        mean_r = _mean(rs)
        bases: dict[str, int] = {}
        for r in group:
            if r.option_id is not None:
                key = (
                    ("level" if (r.option_basis.get("stop_basis") or "").find("L") >= 0 else "atr")
                    + "/"
                    + (
                        "level"
                        if (r.option_basis.get("target_basis") or "").find("L") >= 0
                        else "r"
                    )
                )
                bases[key] = bases.get(key, 0) + 1
        enter_rate = f"{len(entered) / len(candidates) * 100:.0f}%" if candidates else "—"
        return [
            f"- snapshots: {len(group)} · candidates: {len(candidates)} · calls: {len(called)}",
            f"- enter rate (candidates): {enter_rate}"
            f" ({len(entered)} of {len(candidates)} {what} candidates)",
            f"- invalid choices: {len(invalid)}"
            + (
                f" ({len(invalid) / len(called) * 100:.0f}% of {len(called)} calls)"
                if called
                else ""
            ),
            f"- explanation failures: {len(expl_fail)} of {len(called)} calls",
            f"- agreement with v1: {len(v1_same)} of {len(called)} calls",
            "- choices by stop/target basis: "
            + (", ".join(f"{k}: {n}" for k, n in sorted(bases.items())) or "—"),
            "- first touch: stop "
            f"{touched.count('stop')}, target {touched.count('target')}, "
            f"neither {touched.count('none')} (of {len(entered)} entered)",
            (
                f"- mean R over the next sessions: {float(mean_r):+.2f} (n={len(rs)})"
                if mean_r is not None
                else "- mean R: —"
            ),
            *_entries_per_session(entered),
        ]

    # --- live entries: every snapshot where live v1 entered, with what
    # v2 offered and chose, or why it couldn't be replayed.
    entry_lines = ["## Live entries", ""]
    entries = [r for r in rows if r.v1_action == "enter"]
    if not entries:
        entry_lines.append("- none in range")
    for r in entries:
        when = r.at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        head = f"- #{r.decision_id} {r.symbol} · {when} · v1 enter"
        if r.status == NOT_REPLAYABLE:
            entry_lines.append(f"{head} · not_replayable: {r.error}")
        else:
            offered = ",".join(r.offered or []) or "none"
            if r.option_id is not None:
                chose = f"chose {r.option_id}"
            elif r.model_action is not None:
                chose = f"model said {r.model_action}" + (
                    "" if r.valid else f" (invalid: {r.error})"
                )
            else:
                chose = r.error or "no options"
            entry_lines.append(f"{head} · {r.status} · offered {offered} · {chose}")
    entry_lines.append("")

    out = [
        f"# Replay {replay_id}",
        "",
        f"prompt `{prompt}` · model `{model}` · {from_day} → {to_day}",
        f"estimated ${estimate:.2f} · actual ${cost:.4f}",
        *(
            ["", "not repeatable: the model has no temperature 0"]
            if provider == "anthropic"
            else []
        ),
        "",
        *status_lines,
        "## decision prompt",
        "",
        *stats(replayed, "replayed"),
        "",
    ]
    if approx:
        out += [
            "## decision prompt — approximate reference",
            "",
            "Reference = min(stored price, last closed 5m bar close); the"
            " plausible ask isn't stored for these snapshots. Counted"
            " separately, never in the rates above.",
            "",
            *stats(approx, "replayed_approx"),
            "",
        ]
    rules_candidates = [r for r in rules_rows if r.context == "candidate"]
    rules_entered = [r for r in rules_candidates if r.final_action == "enter"]
    rules_touched = [r.score.touched for r in rules_candidates if r.score is not None]
    rules_rs = [
        r.score.r_multiple
        for r in rules_candidates
        if r.score is not None and r.score.r_multiple is not None
    ]
    rules_mean = _mean(rules_rs)
    out += [
        *entry_lines,
        "## rules-only baseline",
        "",
        f"- snapshots: {len(rules_rows)} · candidates: {len(rules_candidates)}",
        "- enter rate (candidates): "
        + (
            f"{len(rules_entered) / len(rules_candidates) * 100:.0f}%"
            f" ({len(rules_entered)} of {len(rules_candidates)} candidates)"
            if rules_candidates
            else "—"
        ),
        "- first touch: stop "
        f"{rules_touched.count('stop')}, "
        f"target {rules_touched.count('target')}, "
        f"neither {rules_touched.count('none')}",
        (
            f"- mean R over the next sessions: {float(rules_mean):+.2f} (n={len(rules_rs)})"
            if rules_mean is not None
            else "- mean R: —"
        ),
        *_entries_per_session(rules_entered),
        "",
    ]
    if prompt == "decision.v3":
        out += _v3_report_lines(rows)
    return "\n".join(out)


def _det(row: ReplayRow) -> dict[str, Any]:
    return row.detail or {}


def _v3_report_lines(rows: list[ReplayRow]) -> list[str]:
    """The decision.v3 report additions (docs/SPEC.md, Decision v3 →
    Replay): room-filter removals with the entries they would have
    blocked, code skips, confidence against break-even, conflicts and
    data flags, v2-vs-v3, and the held section."""
    lines: list[str] = ["## decision.v3", ""]
    replayed = [r for r in rows if r.status != NOT_REPLAYABLE]
    detailed = [r for r in replayed if r.detail is not None]

    # --- room filter: snapshots/options removed, and which stored
    # entries the filter would have blocked, with their outcomes.
    room = [
        r for r in detailed
        if (r.error or "").startswith("no_options: room")
        or any(
            "room" in (x.get("reason") or "")
            for x in _det(r)["room_removed"]
        )
    ]
    blocked = [r for r in room if r.v1_action == "enter"]
    touches = [_det(r)["stored_first_touch"] for r in blocked if _det(r)["stored_first_touch"]]
    lines += [
        f"- room filter: {len(room)} snapshots had options removed or"
        f" were emptied ({sum(len(_det(r)['room_removed']) for r in room)} options removed)",
        f"  - would have blocked {len(blocked)} stored entr{'y' if len(blocked) == 1 else 'ies'}"
        + (
            f" — outcomes: target {touches.count('target')},"
            f" stop {touches.count('stop')},"
            f" neither/unknown {len(blocked) - touches.count('target') - touches.count('stop')}"
            if blocked
            else ""
        ),
    ]

    # --- skips by code: edge_too_small and data_flag.
    skips: dict[str, int] = {}
    for r in detailed:
        reason = _det(r).get("reason")
        if r.final_action == "skip" and reason:
            skips[reason] = skips.get(reason, 0) + 1
    lines.append(
        "- skips by code: "
        + (", ".join(f"{k} {n}" for k, n in sorted(skips.items())) or "none")
    )

    # --- confidence against breakeven_confidence, and calibration per
    # 0.1 bucket against first-touch outcomes where known.
    entered = [
        r for r in detailed
        if r.final_action == "enter"
        and (_det(r).get("chosen") or {}).get("breakeven_confidence")
        is not None
    ]
    over = [
        r for r in entered
        if _det(r)["confidence"] is not None
        and _det(r)["confidence"] >= _det(r)["chosen"]["breakeven_confidence"]
    ]
    buckets: dict[str, list[ReplayRow]] = {}
    for r in entered:
        conf = _det(r).get("confidence")
        if conf is None:
            continue
        key = f"{int(conf * 10) / 10:.1f}–{int(conf * 10) / 10 + 0.1:.1f}"
        buckets.setdefault(key, []).append(r)
    def _touches(group: list[ReplayRow], what: str) -> int:
        return sum(
            1
            for x in group
            if x.score is not None and x.score.touched == what
        )

    cal = []
    for key in sorted(buckets):
        group = buckets[key]
        known = [x for x in group if x.score is not None]
        cal.append(
            f"{key}: {len(group)}"
            + (
                f" — target {_touches(known, 'target')},"
                f" stop {_touches(known, 'stop')},"
                f" neither {len(known) - _touches(known, 'target') - _touches(known, 'stop')}"
                f" (of {len(known)} scored)"
                if known
                else " — unscored"
            )
        )
    lines += [
        f"- confidence ≥ break-even: {len(over)} of {len(entered)} entered",
        "- calibration per 0.1: "
        + ("; ".join(cal) or "—"),
    ]

    # --- conflicts and data flags.
    conflicts: dict[str, int] = {}
    for r in detailed:
        for c in _det(r)["conflicts"]:
            conflicts[c] = conflicts.get(c, 0) + 1
    flags = [
        (r.symbol, r.at.astimezone(UTC).strftime("%Y-%m-%d %H:%M"), f)
        for r in detailed
        for f in _det(r)["data_flags"]
    ]
    lines += [
        "- most frequent conflicts: "
        + (
            "; ".join(
                f"{c} — {n}"
                for c, n in sorted(conflicts.items(), key=lambda kv: -kv[1])[:5]
            )
            or "none"
        ),
        f"- data flags: {len(flags)}"
        + ("".join(f"; {s} {w} {f}" for s, w, f in flags[:10]) if flags else ""),
    ]

    # --- v2/live against v3 on the same snapshots.
    candidates = [r for r in detailed if r.context == "candidate"]
    v3_enter = [r for r in candidates if r.final_action == "enter"]
    v2_enter = [r for r in candidates if r.v1_action == "enter"]
    called = [r for r in candidates if r.model_action is not None]
    same = [r for r in called if r.final_action == r.v1_action]
    rs = [
        r.score.r_multiple
        for r in v3_enter
        if r.score is not None and r.score.r_multiple is not None
    ]
    mean_r = _mean(rs)
    lines += [
        f"- v2/live entered {len(v2_enter)} of {len(candidates)}"
        f" candidates · v3 entered {len(v3_enter)}"
        + (
            f" ({len(v3_enter) / len(candidates) * 100:.0f}%)"
            if candidates
            else ""
        ),
        f"- agreement: {len(same)} of {len(called)} calls"
        + (f" ({len(same) / len(called) * 100:.0f}%)" if called else ""),
        (
            f"- mean R of chosen options: {float(mean_r):+.2f} (n={len(rs)})"
            if mean_r is not None
            else "- mean R of chosen options: —"
        ),
    ]

    # --- held: actions, drops per reason, chosen-stop outcomes,
    # confidence against neutral_probability, claim failures by kind.
    held = [r for r in detailed if r.context == "held"]
    held_actions: dict[str, int] = {}
    for r in held:
        held_actions[r.final_action] = held_actions.get(r.final_action, 0) + 1
    drops: dict[str, int] = {}
    for r in held:
        for d in _det(r)["dropped_stop_options"]:
            key = str(d.get("reason") or "?").split(" (")[0].split(";")[0]
            drops[key] = drops.get(key, 0) + 1
    stops = [_det(r)["held_stop"] for r in held if _det(r).get("held_stop")]
    hits = sum(1 for s in stops if s.get("stopped_out_in_5"))
    givens = [s["r_given_up"] for s in stops if s.get("r_given_up") is not None]
    pairs = [
        r for r in held
        if _det(r).get("neutral_probability") is not None
        and _det(r).get("confidence") is not None
    ]
    claims: dict[str, int] = {}
    for r in held:
        c = _det(r).get("claims")
        if c and c.get("passed") is False:
            key = ",".join(sorted(c.get("bad_ids") or [])) or "claim"
            claims[key] = claims.get(key, 0) + 1
    gaps = [
        _det(r)["confidence"] - _det(r)["neutral_probability"]
        for r in pairs
    ]
    gap_mean = (sum(gaps) / len(gaps)) if gaps else None
    lines += [
        "- held: "
        + (", ".join(f"{a} {n}" for a, n in sorted(held_actions.items())) or "none"),
        "- stop options dropped: "
        + (", ".join(f"{k} — {n}" for k, n in sorted(drops.items())) or "none"),
        f"- chosen stops: {len(stops)} tighten{'s' if len(stops) != 1 else ''}"
        f" · stopped out within 5 sessions: {hits}"
        + (
            f" · mean R given up vs the old stop: {sum(givens) / len(givens):+.2f}"
            if givens
            else ""
        ),
        "- held confidence vs neutral_probability: "
        f"{len(pairs)} pairs"
        + (f" (mean gap {gap_mean:+.2f})" if gap_mean is not None else ""),
        "- claim-check failures by kind: "
        + (", ".join(f"{k} — {n}" for k, n in sorted(claims.items())) or "none"),
        "",
    ]
    return lines


def replay_spent_usd(data_dir: Path, since: datetime) -> Decimal:
    """Replay cost already recorded under ``data/replays/*/meta.json``."""
    total = Decimal(0)
    root = Path(data_dir) / "replays"
    if not root.exists():
        return total
    for meta in root.glob("*/meta.json"):
        try:
            blob = json.loads(meta.read_text())
            created = datetime.fromisoformat(str(blob.get("created_at")))
            if created.replace(tzinfo=None) >= since.replace(tzinfo=None):
                total += Decimal(str(blob.get("cost_usd") or 0))
        except (OSError, ValueError):
            continue
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.replay")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--from", dest="from_day", required=True)
    parser.add_argument("--to", dest="to_day", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--approx-reference", action="store_true")
    args = parser.parse_args(argv)
    result = asyncio.run(
        replay(
            prompt_version=args.prompt,
            from_day=date.fromisoformat(args.from_day),
            to_day=date.fromisoformat(args.to_day),
            model=args.model,
            assume_yes=args.yes,
            approx_reference=args.approx_reference,
        )
    )
    return 0 if result is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
