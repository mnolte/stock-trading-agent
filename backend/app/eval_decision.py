"""The decision-eval command (docs/SPEC.md, Decision v4 — Evaluation).

``make eval-decision PROMPTS=decision.v3,decision.v4 MODEL=...`` rebuilds
each stored call's input from the bar cache — never refetched — calls
the model with each listed prompt, runs production validation and the
named predicates from ``eval/decision_v4.json``, and reports the pass
rate per category and prompt plus the v3-pass/v4-fail regressions.
Costs count toward the replay spend confirmation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import BarCache
from app.db.base import get_session_maker
from app.db.models import (
    Cycle,
    Decision,
    LlmCall,
    Position,
    ShadowDecision,
    ShadowPosition,
)
from app.decide.client import token_cost
from app.decide.output import (
    ValidationResult,
    validate_model_output_v2,
    validate_model_output_v3,
    validate_model_output_v4,
)
from app.decide.prompt import cached_prompt, prompt_spec
from app.decide.v2inputs import build_v2_fields, rebuild_bars
from app.decide.v3 import HeldV3, build_v3_fields, v3_send_snapshot
from app.decide.v4 import (
    build_v4_fields,
    checks_at_entry,
    previous_decision_v4,
    symbol_names,
    v4_send_snapshot,
)
from app.llm.base import ModelResponse
from app.market.calendar import StaticCalendarProvider
from app.market.clock import NY_TZ
from app.risk.earnings import next_earnings
from app.shadows.runner import (
    indicators_from_snapshot,
    regime_from_snapshot,
)

logger = logging.getLogger(__name__)

EVAL_FILE = Path(__file__).resolve().parents[1] / "eval" / "decision_v4.json"


# ----------------------------------------------------------------------
# predicates: name -> (sent snapshot, parsed output, validation) -> bool

_DAILY_UP_RE = re.compile(
    r"\bdaily\b.{0,30}\b(uptrend|up\s?trend|trend\s+(is\s+)?up)\b"
    r"|\b(uptrend|trending up)\b.{0,20}\bdaily\b",
    re.IGNORECASE | re.DOTALL,
)
_MOMENTUM_RE = re.compile(
    r"\b(strong|supporting|supportive|improving|positive)\s+momentum\b"
    r"|\bmomentum\s+(is\s+)?(strong|supporting|positive)\b",
    re.IGNORECASE,
)
_NO_NEWS_RE = re.compile(
    r"\bno\b.{0,15}\b(fresh|new|meaningful|relevant)?\s*news\b"
    r"|\bnews\b.{0,15}\b(absent|lacking|none)\b",
    re.IGNORECASE,
)
_LOCKS_RE = re.compile(r"\block(s|ed|ing)?\b.{0,20}\bprofit|break-?even\b", re.IGNORECASE)


def _texts(output: Any) -> str:
    if output is None:
        return ""
    parts = [getattr(output, "thesis", "") or ""]
    if getattr(output, "what_changed", None):
        parts.append(output.what_changed)
    for c in getattr(output, "conflicts", []) or []:
        parts.append(
            str(c.get("text") or "") if isinstance(c, dict)
            else getattr(c, "text", str(c))
        )
    return " ".join(parts)


def _offered_ids(snap: dict[str, Any], context: str) -> set[str]:
    key = "options" if context == "candidate" else "stop_options"
    return {
        str(o.get("id"))
        for o in (snap.get(key) or [])
        if isinstance(o, dict) and o.get("id") is not None
    }


def _best_anchor(snap: dict[str, Any], context: str, output: Any) -> float | None:
    if context == "candidate":
        best_id = getattr(output, "best_option_id", None)
        for o in snap.get("options") or []:
            if isinstance(o, dict) and str(o.get("id")) == str(best_id):
                chosen = (
                    o
                    if getattr(output, "option_id", None) in (None, best_id)
                    or getattr(output, "action", None) != "enter"
                    else next(
                        (
                            x
                            for x in snap.get("options") or []
                            if isinstance(x, dict)
                            and str(x.get("id")) == str(output.option_id)
                        ),
                        o,
                    )
                )
                v = chosen.get("breakeven_confidence")
                return float(v) if v is not None else None
        return None
    v = (snap.get("position") or {}).get("neutral_probability")
    return float(v) if v is not None else None


def _claims_kinds(validation: ValidationResult) -> set[str]:
    claims = validation.claims
    if claims is None:
        return set()
    return set(claims.kinds or []) | (
        {"locks_in"} if "locks-in claim" in claims.bad_ids else set()
    ) | ({"path"} if "path claim" in claims.bad_ids else set())


def _checks(snap: dict[str, Any]) -> dict[str, Any]:
    return snap.get("checks") or {}


def _conflicts_resolved(_snap: Any, output: Any, _validation: ValidationResult) -> bool:
    if getattr(output, "action", None) != "enter":
        return True
    conflicts = getattr(output, "conflicts", []) or []
    if not conflicts:
        return True
    for c in conflicts:
        outweighed = (
            c.get("outweighed_by") if isinstance(c, dict)
            else getattr(c, "outweighed_by", None)
        )
        # v3 conflicts are plain strings — treated as unresolved.
        if isinstance(c, str) or outweighed in (None, ""):
            return False
    return True


PREDICATES: dict[str, Callable[[dict[str, Any], Any, ValidationResult], bool]] = {
    "valid_output": lambda s, o, v: v.valid,
    "valid_option": lambda s, o, v: (
        getattr(o, "option_id", None) is None
        or getattr(o, "action", None) in ("skip", "hold", "exit")
        or str(getattr(o, "option_id", "")) in _offered_ids(s, str(s.get("context") or "candidate"))
    ),
    "best_option_valid": lambda s, o, v: (
        getattr(o, "best_option_id", None) is None
        or (
            str(s.get("context") or "candidate") == "candidate"
            and str(o.best_option_id) in _offered_ids(s, "candidate")
        )
    ),
    "no_locks_in_claim": lambda s, o, v: "locks_in" not in _claims_kinds(v),
    "no_daily_uptrend_claim": lambda s, o, v: (
        _checks(s).get("trend_daily") == "up"
        or not _DAILY_UP_RE.search(_texts(o))
    ),
    "no_clear_path_claim": lambda s, o, v: "path" not in _claims_kinds(v),
    "no_momentum_contradiction": lambda s, o, v: (
        _checks(s).get("momentum") != "weak"
        or not _MOMENTUM_RE.search(_texts(o))
    ),
    "conflicts_resolved": _conflicts_resolved,
    "conflicts_on_adverse": lambda s, o, v: (
        not any(
            _checks(s).get(k) in ("down", "weak")
            for k in ("trend_1h", "trend_daily", "regime_trend", "momentum")
        )
        or bool(getattr(o, "conflicts", None))
    ),
    "what_changed_on_flip": lambda s, o, v: (
        getattr(o, "what_changed", None) is not None
        if (s.get("previous_decision") or {}).get("action") is not None
        and getattr(o, "action", None) != (s.get("previous_decision") or {}).get("action")
        else True
    ),
    "conf_within_anchor": lambda s, o, v: (
        _best_anchor(s, str(s.get("context") or "candidate"), o) is None
        or getattr(o, "confidence", 0)
        - (_best_anchor(s, str(s.get("context") or "candidate"), o) or 0)
        <= 0.30
    ),
    "no_no_news_claim": lambda s, o, v: (
        not s.get("headlines") or not _NO_NEWS_RE.search(_texts(o))
    ),
    "news_effect": lambda s, o, v: all(
        bool(
            (n.get("effect") if isinstance(n, dict) else getattr(n, "effect", ""))
            or (n.get("id") if isinstance(n, dict) else "") is not None
        )
        for n in (getattr(o, "news_used", []) or [])
    ),
    "sound_answer": lambda s, o, v: v.valid and not v.warnings,
}


# ----------------------------------------------------------------------
# input rebuild — the same path the replay uses, no refetch


def _day_bounds(day_ny: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day_ny, datetime.min.time(), tzinfo=NY_TZ)
    return start, start + timedelta(days=1)


def _dec(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _held_from_snapshot(snap: dict[str, Any]) -> HeldV3 | None:
    pos = snap.get("position")
    if not isinstance(pos, dict) or pos.get("entry") is None:
        return None
    opened = pos.get("opened_at")
    return HeldV3(
        entry=Decimal(str(pos["entry"])),
        qty=int(float(pos.get("qty") or 0)),
        initial_stop=Decimal(str(pos.get("initial_stop") or pos["entry"])),
        current_stop=Decimal(str(pos.get("current_stop") or pos.get("initial_stop") or 0)),
        target=Decimal(str(pos.get("target") or pos["entry"])),
        opened_at=(
            datetime.fromisoformat(str(opened)).astimezone(UTC)
            if opened else None
        ),
        entry_thesis=pos.get("entry_thesis"),
        stop_basis=None,
    )


def _rebuild_input(
    session: Session,
    *,
    prompt_version: str,
    stored: dict[str, Any],
    symbol: str,
    context: str,
    scheduled: datetime,
    strategy_id: int | None,
    config: AppConfig,
    calendar: StaticCalendarProvider,
    cache: BarCache,
) -> dict[str, Any] | None:
    """Rebuild the sent snapshot for ``prompt_version`` from the stored
    input + bar cache. ``None`` when the row isn't replayable."""
    snap = dict(stored)
    spec = prompt_spec(prompt_version)
    atr_1h = _dec((snap.get("indicators_1h") or {}).get("atr14"))
    price = _dec(snap.get("price"))
    reference = _dec(snap.get("stop_reference_price"))
    if price is None or atr_1h is None:
        return None
    bars_1h, daily, today_5m = rebuild_bars(
        symbol, scheduled, calendar=calendar, bars_reader=cache.get
    )
    held = _held_from_snapshot(snap) if context == "held" else None
    # Rebuild the same inputs the live and replay paths see: earnings on
    # v3/v4, and for held rows the entry decision's checks
    # (position.checks_at_entry → thesis_holds).
    earnings: tuple[date, str] | None = None
    entry_checks: dict[str, str] | None = None
    if spec.is_v3:
        earnings = next_earnings(
            session,
            [symbol],
            scheduled if scheduled.tzinfo else scheduled.replace(tzinfo=UTC),
        ).get(symbol)
        if context == "held" and spec.is_v4:
            naive = (
                scheduled.replace(tzinfo=None) if scheduled.tzinfo else scheduled
            )
            entry_dec: Any = None
            if strategy_id is None:
                prow = session.execute(
                    select(Position)
                    .where(
                        Position.symbol == symbol,
                        Position.opened_at <= naive,
                    )
                    .order_by(Position.opened_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if prow is not None and prow.entry_decision_id is not None:
                    entry_dec = session.get(Decision, prow.entry_decision_id)
            else:
                sp = session.execute(
                    select(ShadowPosition)
                    .where(
                        ShadowPosition.strategy_id == strategy_id,
                        ShadowPosition.symbol == symbol,
                        ShadowPosition.opened_at <= naive,
                    )
                    .order_by(ShadowPosition.opened_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if sp is not None and sp.entry_decision_id is not None:
                    entry_dec = session.get(
                        ShadowDecision, sp.entry_decision_id
                    )
            entry_checks = checks_at_entry(entry_dec) or None
    if spec.is_v4:
        built: Any = build_v4_fields(
            context=context,
            base=snap,
            price=price,
            reference=reference,
            atr_1h=atr_1h,
            indicators=indicators_from_snapshot(symbol, snap),
            regime=regime_from_snapshot(snap, config),
            bars_1h=bars_1h,
            daily=daily,
            today_5m=today_5m,
            earnings=earnings,
            held=held,
            previous=previous_decision_v4(
                session,
                symbol=symbol,
                day_ny=scheduled.astimezone(NY_TZ).date()
                if scheduled.tzinfo else scheduled,
                strategy_id=strategy_id,
                before=scheduled,
            ),
            entry_checks=entry_checks,
            names=symbol_names(config),
            calendar=calendar,
            config=config,
            cycle_time_ny=scheduled.astimezone(NY_TZ) if scheduled.tzinfo else scheduled,
        )
        snap.update(built.fields)
        return v4_send_snapshot(snap, config)
    if spec.is_v3:
        built = build_v3_fields(
            context=context,
            base=snap,
            price=price,
            reference=reference,
            atr_1h=atr_1h,
            indicators=indicators_from_snapshot(symbol, snap),
            regime=regime_from_snapshot(snap, config),
            bars_1h=bars_1h,
            daily=daily,
            today_5m=today_5m,
            earnings=earnings,
            held=held,
            calendar=calendar,
            config=config,
            cycle_time_ny=scheduled.astimezone(NY_TZ) if scheduled.tzinfo else scheduled,
        )
        snap.update(built.fields)
        return v3_send_snapshot(snap)
    built2 = build_v2_fields(
        context=context,
        bars_1h=bars_1h,
        daily=daily,
        today_5m=today_5m,
        price=price,
        reference=reference,
        atr_1h=atr_1h,
        config=config,
    )
    snap.update(built2.fields)
    return snap


def _validate(
    prompt_version: str,
    raw: str | None,
    *,
    symbol: str,
    context: str,
    snapshot: dict[str, Any],
    atr_1h: Decimal | None,
    current_stop: Decimal | None,
    call_error: str | None,
    config: AppConfig,
) -> ValidationResult:
    spec = prompt_spec(prompt_version)
    if spec.is_v4:
        return validate_model_output_v4(
            raw, symbol=symbol, context=context, snapshot=snapshot,
            atr_1h=atr_1h, current_stop=current_stop,
            call_error=call_error, config=config,
        )
    if spec.is_v3:
        return validate_model_output_v3(
            raw, symbol=symbol, context=context, snapshot=snapshot,
            atr_1h=atr_1h, current_stop=current_stop,
            call_error=call_error, config=config,
        )
    return validate_model_output_v2(
        raw, symbol=symbol, context=context, snapshot=snapshot,
        atr_1h=atr_1h, current_stop=current_stop,
        call_error=call_error, config=config,
    )


# ----------------------------------------------------------------------
# run


def _resolve(
    session: Session, call: LlmCall
) -> tuple[Any, dict[str, Any], str, str, datetime, int | None] | None:
    """(row, stored input snapshot, symbol, context, scheduled, strategy_id)."""
    row: Any = None
    strategy_id: int | None = None
    if call.decision_id is not None:
        row = session.get(Decision, call.decision_id)
    elif call.shadow_decision_id is not None:
        row = session.get(ShadowDecision, call.shadow_decision_id)
        if row is not None:
            strategy_id = row.strategy_id
    if row is None:
        return None
    snap = row.input_snapshot or {}
    cycle = session.get(Cycle, row.cycle_id) if row.cycle_id else None
    scheduled = (
        cycle.scheduled_time.replace(tzinfo=UTC)
        if cycle is not None
        else call.sent_at.replace(tzinfo=UTC)
    )
    return row, dict(snap), str(row.symbol), str(row.context), scheduled, strategy_id


async def run_eval(
    *,
    prompts: list[str],
    model: str | None,
    assume_yes: bool,
    eval_path: Path = EVAL_FILE,
    session_factory: sessionmaker[Session] | None = None,
    client: Any = None,
    config: AppConfig | None = None,
) -> int:
    config = config or AppConfig()
    sf = session_factory or get_session_maker()
    cache = BarCache(sf)
    entries = json.loads(eval_path.read_text())["calls"]
    predicates_doc = json.loads(eval_path.read_text()).get("predicates", {})
    use_model = str(model or config.get("decision", "model", default="gpt-4o-mini"))

    with sf() as session:
        calls = {
            c.id: c
            for c in session.execute(
                select(LlmCall).where(
                    LlmCall.id.in_([e["llm_call_id"] for e in entries])
                )
            ).scalars()
        }
        missing = [e["llm_call_id"] for e in entries if e["llm_call_id"] not in calls]
    if missing:
        print(f"eval: {len(missing)} call ids not found: {missing[:10]}")
        entries = [e for e in entries if e["llm_call_id"] in calls]
    if not entries:
        print("eval: nothing to run")
        return 1

    if client is None:
        from app.llm.clients import make_model_clients
        from app.market.clock import RealClock
        from app.secrets_service import SecretsStore

        client = make_model_clients(config, SecretsStore(config, RealClock()))

    # Sessions for rebuild come from the bar cache itself — never the
    # network.
    from app.replay import _sessions_from_bars

    with sf() as session:
        first = calls[entries[0]["llm_call_id"]]
        days = _sessions_from_bars(
            cache,
            str(first.symbol or "SPY"),
            first.sent_at.replace(tzinfo=UTC) - timedelta(days=120),
            datetime.now(UTC),
        )
    calendar = StaticCalendarProvider(days)

    # Estimate before spending — same gate as the replay.
    est = Decimal(0)
    for version in prompts:
        prompt = cached_prompt(version)
        unit = token_cost(
            ModelResponse(
                raw=None,
                model=use_model,
                tokens_in=int(len(prompt.text) / 4) + 900,
                tokens_out=200,
                latency_ms=0,
            ),
            config,
            model=use_model,
        )
        est += (unit or Decimal(0)) * len(entries)
    threshold = Decimal(
        str(config.get("decision", "replay", "confirm_above_usd", default=2.0))
    )
    print(
        f"eval: {len(entries)} calls × {len(prompts)} prompts, "
        f"est. ${est:.2f} on {use_model}"
    )
    if est > threshold and not assume_yes:
        print(
            f"eval: estimated cost ${est:.2f} exceeds "
            f"decision.replay.confirm_above_usd ${threshold} — rerun with --yes"
        )
        return 1

    total = Decimal(0)
    results: dict[str, dict[int, dict[str, Any]]] = {p: {} for p in prompts}
    for entry in entries:
        call = calls[entry["llm_call_id"]]
        with sf() as session:
            resolved = _resolve(session, call)
            if resolved is None:
                print(f"eval: call {call.id} has no linked decision — skipped")
                continue
            row, stored, symbol, context, scheduled, strategy_id = resolved
            atr_1h = _dec((stored.get("indicators_1h") or {}).get("atr14"))
            current_stop = _dec(
                (stored.get("position") or {}).get("current_stop")
            )
            for version in prompts:
                sent = _rebuild_input(
                    session,
                    prompt_version=version,
                    stored=stored,
                    symbol=symbol,
                    context=context,
                    scheduled=scheduled,
                    strategy_id=strategy_id,
                    config=config,
                    calendar=calendar,
                    cache=cache,
                )
                if sent is None:
                    results[version][call.id] = {
                        "category": entry["category"], "skipped": True,
                    }
                    continue
                spec = prompt_spec(version)
                prompt = cached_prompt(version)
                response = await client.complete(
                    system=prompt.text,
                    user=json.dumps(sent, indent=2),
                    schema=spec.json_schema,
                    model=use_model,
                    temperature=float(
                        config.get("decision", "temperature", default=0)
                    ),
                )
                cost = token_cost(response, config, model=use_model)
                if cost is not None:
                    total += cost
                sent["context"] = context
                validation = _validate(
                    version,
                    response.raw,
                    symbol=symbol,
                    context=context,
                    snapshot=sent,
                    atr_1h=atr_1h,
                    current_stop=current_stop,
                    call_error=response.error,
                    config=config,
                )
                checks = {
                    name: bool(PREDICATES[name](sent, validation.output, validation))
                    for name in entry["predicates"]
                    if name in PREDICATES
                }
                unknown = [n for n in entry["predicates"] if n not in PREDICATES]
                results[version][call.id] = {
                    "category": entry["category"],
                    "action": (
                        getattr(validation.output, "action", None)
                        or validation.final_action
                    ),
                    "checks": checks,
                    "unknown": unknown,
                    "latency_ms": response.latency_ms,
                    "tokens_in": response.tokens_in,
                    "tokens_out": response.tokens_out,
                    "cost": float(cost or 0),
                    "passed": all(checks.values()) and not unknown,
                }

    # ---- report
    print(f"\neval: {len(entries)} calls × {len(prompts)} prompts · model {use_model}")
    print(f"total cost ${total:.2f}")
    categories = sorted({e["category"] for e in entries})
    header = f"{'category':<26}" + "".join(f"{p:>16}" for p in prompts)
    print(header)
    for cat in categories:
        cells = f"{cat:<26}"
        for version in prompts:
            rows = [
                r for cid, r in results[version].items()
                if r["category"] == cat and not r.get("skipped")
            ]
            cells += f"{sum(1 for r in rows if r['passed'])}/{len(rows)}".rjust(16)
        print(cells)
    print("\nv3-pass / v4-fail regressions:")
    if len(prompts) >= 2:
        for entry in entries:
            a = results[prompts[0]].get(entry["llm_call_id"], {})
            b = results[prompts[1]].get(entry["llm_call_id"], {})
            if a.get("passed") and not b.get("passed", True):
                print(
                    f"  call {entry['llm_call_id']} ({entry['category']}): "
                    f"v4 fails {b.get('checks')}"
                )
    print(f"\npredicates: {', '.join(predicates_doc) or ', '.join(PREDICATES)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.eval_decision")
    parser.add_argument("--prompts", required=True, help="comma-separated prompt versions")
    parser.add_argument("--model", default=None)
    parser.add_argument("--eval-file", default=None)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(
        run_eval(
            prompts=[p.strip() for p in args.prompts.split(",") if p.strip()],
            model=args.model,
            assume_yes=args.yes,
            eval_path=Path(args.eval_file) if args.eval_file else EVAL_FILE,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
