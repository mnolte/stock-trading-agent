"""Replay a decision prompt over stored snapshots (docs/SPEC.md, Replays).

``make replay PROMPT=decision.v2 FROM=2026-10-20 TO=2026-10-25 [--yes]``
runs a prompt on the live run's stored input snapshots: for each
candidate and held decision the levels and options are rebuilt from the
cached 5-minute bars as they were at that cycle's time — never later —
then the model picks an option. The chosen option is scored on the bars
of the next five sessions (first touch and R), and the rules-only
baseline answers the same snapshots for comparison.

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
from app.db.models import Cycle, Decision, Run
from app.decide.client import DecisionClient, ModelResponse, make_decision_client, token_cost
from app.decide.output import (
    ValidationResult,
    validate_model_output,
    validate_model_output_v2,
)
from app.decide.prompt import cached_prompt, prompt_spec
from app.decide.v2inputs import HeldContext, build_v2_fields, rebuild_bars
from app.market.calendar import StaticCalendarProvider
from app.market.clock import NY_TZ

CENT = Decimal("0.01")


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

    def to_json(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "symbol": self.symbol,
            "context": self.context,
            "at": self.at.isoformat(),
            "decision_id": self.decision_id,
            "v1_action": self.v1_action,
            "model_action": self.model_action,
            "final_action": self.final_action,
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
        if (snap or {}).get("price"):
            out.append(
                (
                    SnapshotRow(
                        id=_id, cycle_id=cycle_id, symbol=symbol,
                        context=context, input_snapshot=snap,
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
    use_model = str(
        model or config.get("decision", "model", default="gpt-4o-mini")
    )

    with sf() as session:
        run_start = session.execute(
            select(Run.started_at).order_by(Run.id.desc()).limit(1)
        ).scalar()
        decisions = _candidate_snapshots(
            session,
            run_start or datetime(2000, 1, 1),
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
    threshold = Decimal(
        str(config.get("decision", "replay", "confirm_above_usd", default=2.0))
    )
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
        client = make_decision_client(config, api_key=secrets.get("openai"))

    # Sessions for the level builder come from the bar cache itself —
    # replay never needs a calendar service or the network for bars.
    if sessions_provider is None:
        first_symbol = decisions[0][0].symbol
        days = _sessions_from_bars(
            cache,
            first_symbol,
            datetime.combine(from_day, datetime.min.time(), tzinfo=UTC)
            - timedelta(days=120),
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
    stop_atr = Decimal(
        str(config.get("shadows", "rules_only", "stop_atr", default=2.0))
    )
    target_r = Decimal(
        str(config.get("shadows", "rules_only", "target_r", default=2.0))
    )
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
                    datetime.fromisoformat(str(pos["opened_at"]))
                    if pos.get("opened_at")
                    else None
                ),
            )
        bars_1h, daily, todays = rebuild_bars(
            symbol,
            scheduled.replace(tzinfo=UTC),
            calendar=sessions_provider,
            bars_reader=cache.get,
        )
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

        # --- model call for this snapshot
        score: Score | None = None
        option_basis: dict[str, str | None] = {}
        option_id: str | None = None
        final_action = "no_call"
        model_action: str | None = None
        valid = False
        explanation_passed: bool | None = None
        explanation_detail: dict[str, Any] | None = None
        error: str | None = None
        cost: Decimal | None = None
        if v2.reason is not None and decision.context == "candidate":
            final_action = "no_call"
            error = f"no_options: {v2.reason}"
        else:
            response = await client.complete(
                system=prompt.text,
                user=json.dumps(snap, indent=2),
                schema=spec.json_schema,
            )
            cost = token_cost(response, config, model=use_model)
            if cost is not None:
                total_cost += cost
            if spec.uses_options:
                validation: ValidationResult = validate_model_output_v2(
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
                        str(chosen.get("target_basis"))
                        if chosen.get("target_basis")
                        else None
                    ),
                }
                stop = _dec(chosen.get("stop"))
                target = _dec(chosen.get("target"))
                if final_action == "enter" and stop is not None and target is not None:
                    future = cache.get(
                        symbol,
                        scheduled.replace(tzinfo=UTC),
                        scheduled.replace(tzinfo=UTC)
                        + timedelta(days=horizon_days * 2 + 4),
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
                        limit=_dec(
                            (snap.get("constraints") or {}).get("entry_limit")
                        ),
                        stop=stop,
                        target=target,
                        horizon_end=horizon_end,
                    )
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
        replay_id, prompt_version, use_model, from_day, to_day,
        rows, rules_rows, estimate_usd, total_cost,
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
                "estimated_usd": float(estimate_usd),
                "cost_usd": float(total_cost),
                "created_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    )
    print(f"replay: wrote {root}")
    return root


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return (sum(values, Decimal(0)) / len(values)) if values else None


def _report(
    replay_id: str,
    prompt: str,
    model: str,
    from_day: date,
    to_day: date,
    rows: list[ReplayRow],
    rules_rows: list[ReplayRow],
    estimate: Decimal,
    cost: Decimal,
) -> str:
    called = [r for r in rows if r.model_action is not None]
    candidates = [r for r in rows if r.context == "candidate"]
    invalid = [r for r in called if not r.valid]
    expl_fail = [r for r in rows if r.explanation_passed is False]
    v1_same = [
        r for r in rows if r.model_action is not None and r.v1_action == r.final_action
    ]
    bases: dict[str, int] = {}
    for r in rows:
        if r.option_id is not None:
            key = (
                ("level" if (r.option_basis.get("stop_basis") or "").find("L") >= 0 else "atr")
                + "/"
                + ("level" if (r.option_basis.get("target_basis") or "").find("L") >= 0 else "r")
            )
            bases[key] = bases.get(key, 0) + 1

    def stats(lines: list[ReplayRow]) -> list[str]:
        enter_rate = (
            f"{len([r for r in lines if r.final_action == 'enter']) / len(lines) * 100:.0f}%"
            if lines
            else "—"
        )
        touched = [r.score.touched for r in lines if r.score is not None]
        rs = [
            r.score.r_multiple
            for r in lines
            if r.score is not None and r.score.r_multiple is not None
        ]
        mean_r = _mean(rs)
        return [
            f"- snapshots: {len(lines)}, calls: "
            f"{sum(1 for r in lines if r.model_action is not None)}",
            f"- enter rate (candidates): {enter_rate}",
            "- first touch: stop "
            f"{touched.count('stop')}, target {touched.count('target')}, "
            f"neither {touched.count('none')}",
            (
                f"- mean R over the next sessions: {float(mean_r):+.2f}"
                if mean_r is not None
                else "- mean R: —"
            ),
        ]

    out = [
        f"# Replay {replay_id}",
        "",
        f"prompt `{prompt}` · model `{model}` · {from_day} → {to_day}",
        f"estimated ${estimate:.2f} · actual ${cost:.4f}",
        "",
        "## decision prompt",
        "",
        *stats(candidates),
        f"- invalid choices: {len(invalid)} ({len(invalid) / len(called) * 100:.0f}% of calls)"
        if called
        else "- invalid choices: 0",
        f"- explanation failures: {len(expl_fail)}",
        f"- agreement with v1: {len(v1_same)}/{len(called)} rows",
        "- choices by stop/target basis: "
        + (", ".join(f"{k}: {n}" for k, n in sorted(bases.items())) or "—"),
        "",
        "## rules-only baseline",
        "",
        *stats(rules_rows),
        "",
    ]
    return "\n".join(out)


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
    args = parser.parse_args(argv)
    result = asyncio.run(
        replay(
            prompt_version=args.prompt,
            from_day=date.fromisoformat(args.from_day),
            to_day=date.fromisoformat(args.to_day),
            model=args.model,
            assume_yes=args.yes,
        )
    )
    return 0 if result is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
