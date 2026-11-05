"""Snapshot v5 on top of v4's (docs/SPEC.md, Decision v5).

decision.v5 strategies only: the expected-value entry gate (per-option
``min_confidence`` from ``decision.min_ev_r``), the trend gate in code,
the far-target cap (``max_rr``), per-option base rates from comparable
option touches, and on top of the v4 snapshot ``session.last_cycle``,
``overnight.gap_p90_atr``, per-option ``target_atr``/``min_confidence``/
``base_rate``/``base_n`` and on held positions ``price_vs_signal`` plus
the fifth ``thesis_holds`` check (``price_vs_ema20``). v1–v4 snapshots
are untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.data.models import DailyBar
from app.db.models import Cycle, Decision, Outcome, ShadowDecision
from app.decide.v2inputs import V2Build
from app.decide.v3 import _dec_or_none, _r2
from app.decide.v4 import _adverse_key, build_v4_fields
from app.market.clock import NY_TZ, UTC, RealClock, SessionClock

if TYPE_CHECKING:
    from app.data.models import Bar1h, Bar5m
    from app.market.calendar import CalendarProvider
    from app.signals.regime import MarketRegime
    from app.signals.snapshot import IndicatorSnapshot

OPTIONS_VERSION_V5 = "v5"


# ----------------------------------------------------------------------
# config


@dataclass(frozen=True)
class V5Config:
    """The ``decision.v5`` thresholds (docs/SPEC.md, Decision v5)."""

    exit_margin: Decimal = Decimal("0.03")
    overnight_margin: Decimal = Decimal("0.10")
    gap_sessions: int = 60
    long_target_minutes: int = 120
    trend_gate: bool = True
    max_rr: Decimal = Decimal("3.0")
    base_rate_sessions: int = 60
    base_rate_min_n: int = 30
    price_vs_signal_adverse_atr: Decimal = Decimal("0.5")


def _dec(value: Any, default: Decimal) -> Decimal:
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def v5_config(config: AppConfig) -> V5Config:
    """Read ``decision.v5`` from a (possibly strategy-overlaid) config."""
    base = V5Config()
    get = config.get
    return V5Config(
        exit_margin=_dec(
            get("decision", "v5", "exit_margin", default=None), base.exit_margin
        ),
        overnight_margin=_dec(
            get("decision", "v5", "overnight_margin", default=None),
            base.overnight_margin,
        ),
        gap_sessions=_int(
            get("decision", "v5", "gap_sessions", default=None),
            base.gap_sessions,
        ),
        long_target_minutes=_int(
            get("decision", "v5", "long_target_minutes", default=None),
            base.long_target_minutes,
        ),
        trend_gate=bool(
            get("decision", "v5", "trend_gate", default=base.trend_gate)
        ),
        max_rr=_dec(
            get("decision", "v5", "max_rr", default=None), base.max_rr
        ),
        base_rate_sessions=_int(
            get("decision", "v5", "base_rate_sessions", default=None),
            base.base_rate_sessions,
        ),
        base_rate_min_n=_int(
            get("decision", "v5", "base_rate_min_n", default=None),
            base.base_rate_min_n,
        ),
        price_vs_signal_adverse_atr=_dec(
            get(
                "decision", "v5", "price_vs_signal_adverse_atr",
                default=None,
            ),
            base.price_vs_signal_adverse_atr,
        ),
    )


def entry_gate(config: AppConfig, *, v5: bool = False) -> str:
    """``floor`` (today's rule) or ``ev`` — a strategy override; v5
    strategies default to ``ev`` (docs/SPEC.md, Decision v5 — Entry gate
    on expected value)."""
    raw = config.get("decision", "entry_gate", default=None)
    if raw is not None and str(raw) in ("floor", "ev"):
        return str(raw)
    return "ev" if v5 else "floor"


def min_ev_r(config: AppConfig) -> Decimal:
    return _dec(
        config.get("decision", "min_ev_r", default=None), Decimal("0.25")
    )


def min_confidence_ev(rr: Decimal | None, ev_r: Decimal) -> Decimal | None:
    """(1 + min_ev_r) ÷ (1 + rr), rounded up to 2 decimals — the
    per-option ``min_confidence`` of the ev gate: 0.50 at 1.5R, 0.42 at
    2R, 0.32 at 3R with ``min_ev_r`` 0.25 (docs/SPEC.md)."""
    if rr is None or rr <= 0:
        return None
    return ((1 + ev_r) / (1 + rr)).quantize(
        Decimal("0.01"), rounding=ROUND_CEILING
    )


# ----------------------------------------------------------------------
# the trend gate


def trend_gate_detail(fields: dict[str, Any]) -> str | None:
    """``trend_gate: trend_1h=mixed`` when a v5 check fails — the v4
    ``checks_gate`` with ``trend_daily`` left to the model (docs/SPEC.md,
    Decision v5 — Trend gate in code)."""
    checks = fields.get("checks") or {}
    failing = [
        name for name in ("trend_1h", "regime_trend") if checks.get(name) != "up"
    ]
    if not failing:
        return None
    return "trend_gate: " + ", ".join(
        f"{name}={checks.get(name)}" for name in failing
    )


# ----------------------------------------------------------------------
# overnight gap p90


def _p90(values: Sequence[Decimal]) -> Decimal | None:
    """The 90th percentile by linear interpolation (numpy's method)."""
    if not values:
        return None
    ordered = sorted(values)
    pos = Decimal("0.9") * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def gap_p90_atr(
    daily: Sequence[DailyBar | dict[str, Any]],
    atr_1h: Decimal | None,
    *,
    sessions: int = 60,
) -> float | None:
    """p90 of |open − previous close| over the last ``sessions`` stored
    daily bars, in 1-hour ATR; null with fewer than 20 gaps or no ATR
    (docs/SPEC.md, Snapshot v5 — Overnight)."""
    if atr_1h is None or atr_1h <= 0:
        return None

    def _get(bar: Any, key: str) -> Decimal | None:
        return _dec_or_none(
            bar.get(key) if isinstance(bar, dict) else getattr(bar, key, None)
        )

    bars = list(daily)[-sessions:]
    gaps = [
        abs(o - c)
        for prev, cur in zip(bars, bars[1:], strict=False)
        if (o := _get(cur, "open")) is not None
        and (c := _get(prev, "close")) is not None
    ]
    if len(gaps) < 20:
        return None
    return _r2((_p90(gaps) or Decimal(0)) / atr_1h)


# ----------------------------------------------------------------------
# base rates per option


def _rr_bucket(rr: Decimal | None) -> str | None:
    if rr is None:
        return None
    return "le2" if rr <= 2 else "2_3"


def _option_rows(options: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Offered and removed options of one stored row — both count toward
    the base rates, offered and counterfactual alike (docs/SPEC.md,
    Decision v5 — Base rates)."""
    column = options or {}
    return [
        o
        for key in ("options", "removed")
        for o in (column.get(key) or [])
        if isinstance(o, dict)
    ]


def base_rates(
    session: Session,
    *,
    options: list[dict[str, Any]],
    regime_volatility: str | None,
    momentum: str | None,
    v5: V5Config,
    day_ny: Any,
    strategy_id: int | None = None,
) -> dict[str, tuple[float | None, int]]:
    """``{option id: (base_rate, base_n)}`` — the shrunk target-first
    rate of comparable options over the last ``base_rate_sessions``
    sessions (docs/SPEC.md, Decision v5 — Base rates).

    Comparable means the same rr bucket (≤ 2, 2–3), the same regime
    volatility and the same momentum. Only decided touches count
    (target or stop); offered and counterfactual options both count.
    ``base_rate`` is (n × rate + 30 × break-even) ÷ (n + 30) and shows
    only from ``base_rate_min_n`` comparable decided options.
    """
    out: dict[str, tuple[float | None, int]] = {}
    if not options:
        return out

    if isinstance(day_ny, datetime):
        day = (
            day_ny.astimezone(NY_TZ).date()
            if day_ny.tzinfo is not None
            else day_ny.date()
        )
    elif isinstance(day_ny, date):
        day = day_ny
    else:
        day = datetime.fromisoformat(str(day_ny)).astimezone(NY_TZ).date()
    # A generous calendar window; the session-date filter below applies
    # the real ``base_rate_sessions`` count.
    bound = datetime.combine(day, datetime.min.time(), tzinfo=NY_TZ).astimezone(
        UTC
    ).replace(tzinfo=None)
    start = bound - timedelta(days=v5.base_rate_sessions * 2 + 14)

    # (bucket, session date, decided touch) per comparable option.
    triples: list[tuple[str, date, str]] = []
    if strategy_id is None:
        found = session.execute(
            select(Decision, Cycle.scheduled_time, Outcome.option_touches)
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .join(Outcome, Outcome.decision_id == Decision.id)
            .where(
                Decision.context == "candidate",
                Decision.options.is_not(None),
                Cycle.scheduled_time >= start,
                Cycle.scheduled_time < bound,
                Outcome.option_touches.is_not(None),
            )
        ).all()
        for row, scheduled, touches in found:
            snap_checks = (
                row.input_snapshot
                if isinstance(row.input_snapshot, dict)
                else {}
            )
            checks = cast("dict[str, Any]", snap_checks.get("checks") or {})
            if (
                str(checks.get("regime_volatility"))
                != str(regime_volatility)
                or str(checks.get("momentum")) != str(momentum)
            ):
                continue
            touch_map = touches if isinstance(touches, dict) else {}
            for option in _option_rows(row.options):
                touch = touch_map.get(str(option.get("id") or ""))
                bucket = _rr_bucket(_dec_or_none(option.get("rr")))
                if touch in ("target", "stop") and bucket is not None:
                    triples.append(
                        (
                            bucket,
                            scheduled.astimezone(NY_TZ).date()
                            if scheduled.tzinfo
                            else scheduled.replace(tzinfo=UTC)
                            .astimezone(NY_TZ)
                            .date(),
                            str(touch),
                        )
                    )
    else:
        sfound = session.execute(
            select(ShadowDecision, Cycle.scheduled_time)
            .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
            .where(
                ShadowDecision.strategy_id == strategy_id,
                ShadowDecision.context == "candidate",
                ShadowDecision.options.is_not(None),
                Cycle.scheduled_time >= start,
                Cycle.scheduled_time < bound,
            )
        ).all()
        for shadow, sscheduled in sfound:
            touches = (shadow.options or {}).get("option_touches")
            if not isinstance(touches, dict):
                continue
            shadow_snap = (
                shadow.input_snapshot
                if isinstance(shadow.input_snapshot, dict)
                else {}
            )
            checks = cast("dict[str, Any]", shadow_snap.get("checks") or {})
            if (
                str(checks.get("regime_volatility"))
                != str(regime_volatility)
                or str(checks.get("momentum")) != str(momentum)
            ):
                continue
            for option in _option_rows(shadow.options):
                touch = touches.get(str(option.get("id") or ""))
                bucket = _rr_bucket(_dec_or_none(option.get("rr")))
                if touch in ("target", "stop") and bucket is not None:
                    triples.append(
                        (
                            bucket,
                            sscheduled.astimezone(NY_TZ).date()
                            if sscheduled.tzinfo
                            else sscheduled.replace(tzinfo=UTC)
                            .astimezone(NY_TZ)
                            .date(),
                            str(touch),
                        )
                    )

    # The last ``base_rate_sessions`` distinct New York session dates.
    keep = set(
        sorted({sday for _b, sday, _t in triples})[-v5.base_rate_sessions:]
    )
    comp: dict[str, dict[str, int]] = {}
    for bucket, sday, touch in triples:
        if sday not in keep:
            continue
        slot = comp.setdefault(bucket, {"n": 0, "target": 0})
        slot["n"] += 1
        if touch == "target":
            slot["target"] += 1

    for option in options:
        oid = str(option.get("id") or "")
        rr = _dec_or_none(option.get("rr"))
        bucket = _rr_bucket(rr)
        if bucket is None:
            out[oid] = (None, 0)
            continue
        slot = comp.get(bucket, {"n": 0, "target": 0})
        n = slot["n"]
        if n < v5.base_rate_min_n:
            out[oid] = (None, n)
            continue
        be = _dec_or_none(option.get("breakeven_confidence"))
        if be is None and rr is not None and rr > 0:
            be = Decimal(1) / (1 + rr)
        rate = Decimal(slot["target"]) / Decimal(n)
        shrunk = (
            (Decimal(n) * rate + Decimal(30) * be) / (Decimal(n) + 30)
            if be is not None
            else rate
        )
        out[oid] = (float(shrunk.quantize(Decimal("0.01"))), n)
    return out


# ----------------------------------------------------------------------
# thesis_holds with the fifth check

_CHECK_NAMES = ("trend_1h", "trend_daily", "regime_trend", "momentum")
_ADVERSE = {
    "up": {"mixed", "down"},
    "mixed": {"down"},
    "strong": {"mixed", "weak"},
}
UNAVAILABLE = "unavailable"


def price_vs_ema20(
    price: Decimal | None, indicators: dict[str, Any] | None
) -> str | None:
    """``below``/``above`` — the price against the 1-hour EMA 20 from the
    stored ``indicators_1h`` block (the fifth thesis check, docs/SPEC.md,
    Snapshot v5)."""
    ema20 = _dec_or_none((indicators or {}).get("ema20"))
    if price is None or ema20 is None:
        return None
    return "below" if price < ema20 else "above"


def thesis_holds_v5(
    at_entry: dict[str, str],
    checks_now: dict[str, Any],
    *,
    vs_ema20: str | None,
    price_vs_signal: Decimal | None,
    adverse_atr: Decimal = Decimal("0.5"),
) -> bool | None:
    """v4's ``thesis_holds`` with a fifth comparable check,
    ``price_vs_ema20``: adverse when the price is below the 1-hour EMA 20
    and ``price_vs_signal`` ≤ −0.5 (docs/SPEC.md, Snapshot v5)."""
    adverse = 0
    comparable = 0
    for name in _CHECK_NAMES:
        old, new = at_entry.get(name), checks_now.get(name)
        if (
            not old
            or not new
            or old == UNAVAILABLE
            or new == UNAVAILABLE
        ):
            continue
        comparable += 1
        ok, nk = _adverse_key(old), _adverse_key(new)
        if nk in _ADVERSE.get(ok, set()) or (
            name == "momentum" and ok == "mixed" and nk == "weak"
        ):
            adverse += 1
    if vs_ema20 is not None:
        comparable += 1
        if (
            vs_ema20 == "below"
            and price_vs_signal is not None
            and price_vs_signal <= -adverse_atr
        ):
            adverse += 1
    if comparable < 2:
        return None
    return adverse < 2


# ----------------------------------------------------------------------
# the snapshot itself


@dataclass(frozen=True)
class V5Build(V2Build):
    """Same shape as the v4 build."""


def _options_v5(
    fields: dict[str, Any],
    column: dict[str, Any],
    *,
    limit: Decimal | None,
    atr_1h: Decimal | None,
    v5: V5Config,
    ev_r: Decimal,
) -> None:
    """v5 per-option fields and the ``max_rr`` cap on the built options:
    ``target_atr`` and ``min_confidence`` per option; options above
    ``max_rr`` move to ``removed`` with their reason (docs/SPEC.md,
    Decision v5 — Far targets capped; the counterfactual touches keep
    measuring them)."""
    raw = fields.get("options") or []
    if not isinstance(raw, list):
        return
    kept: list[dict[str, Any]] = []
    removed = list(column.get("removed") or [])
    for option in raw:
        if not isinstance(option, dict):
            continue
        rr = _dec_or_none(option.get("rr"))
        target = _dec_or_none(option.get("target"))
        option["target_atr"] = (
            _r2((target - limit) / atr_1h)
            if target is not None
            and limit is not None
            and atr_1h is not None
            and atr_1h > 0
            else None
        )
        option["min_confidence"] = (
            float(v)
            if (v := min_confidence_ev(rr, ev_r)) is not None
            else None
        )
        if rr is not None and rr > v5.max_rr:
            removed.append(
                {
                    **option,
                    "reason": f"rr {_r2(rr)} above the {v5.max_rr} cap",
                }
            )
            continue
        kept.append(option)
    fields["options"] = kept
    column["options"] = kept
    column["removed"] = removed


def build_v5_fields(
    *,
    context: str,
    base: dict[str, Any],
    price: Decimal | None,
    reference: Decimal | None,
    atr_1h: Decimal | None,
    indicators: IndicatorSnapshot | None,
    regime: MarketRegime | None,
    bars_1h: Sequence[Bar1h] = (),
    daily: Sequence[DailyBar] = (),
    today_5m: Sequence[Bar5m] = (),
    earnings: tuple[Any, str] | None = None,
    held: Any = None,
    previous: Any = None,
    entry_checks: dict[str, str] | None = None,
    names: dict[str, str] | None = None,
    calendar: CalendarProvider | None = None,
    config: AppConfig,
    cycle_time_ny: datetime,
    last_cycle: bool = False,
    db_session: Session | None = None,
    strategy_id: int | None = None,
) -> V5Build:
    """The v5 snapshot fields: the v4 build plus the v5 additions
    (docs/SPEC.md, Snapshot v5)."""
    v5 = v5_config(config)
    v4 = build_v4_fields(
        context=context,
        base=base,
        price=price,
        reference=reference,
        atr_1h=atr_1h,
        indicators=indicators,
        regime=regime,
        bars_1h=bars_1h,
        daily=daily,
        today_5m=today_5m,
        earnings=earnings,
        held=held,
        previous=previous,
        entry_checks=entry_checks,
        names=names,
        calendar=calendar,
        config=config,
        cycle_time_ny=cycle_time_ny,
    )
    fields: dict[str, Any] = dict(v4.fields)
    column = dict(v4.options_column)
    column["options_version"] = OPTIONS_VERSION_V5

    session_block = dict(fields.get("session") or {})
    session_block["last_cycle"] = bool(last_cycle)
    fields["session"] = session_block
    fields["overnight"] = {
        "gap_p90_atr": gap_p90_atr(daily, atr_1h, sessions=v5.gap_sessions)
    }

    constraints = fields.get("constraints") or {}
    if isinstance(constraints, dict):
        fields["constraints"] = dict(constraints) | {
            "min_ev_r": float(min_ev_r(config)),
        }

    _options_v5(
        fields,
        column,
        limit=_dec_or_none(constraints.get("entry_limit"))
        if isinstance(constraints, dict)
        else None,
        atr_1h=atr_1h,
        v5=v5,
        ev_r=min_ev_r(config),
    )

    offered = fields.get("options") or []
    if context == "candidate" and isinstance(offered, list) and offered:
        checks = fields.get("checks") or {}
        rates = (
            base_rates(
                db_session,
                options=[o for o in offered if isinstance(o, dict)],
                regime_volatility=(
                    str(checks.get("regime_volatility"))
                    if checks.get("regime_volatility") is not None
                    else None
                ),
                momentum=(
                    str(checks.get("momentum"))
                    if checks.get("momentum") is not None
                    else None
                ),
                v5=v5,
                day_ny=cycle_time_ny,
                strategy_id=strategy_id,
            )
            if db_session is not None
            else {}
        )
        for option in offered:
            if isinstance(option, dict):
                rate, n = rates.get(str(option.get("id")), (None, 0))
                option["base_rate"] = rate
                option["base_n"] = n

    position = fields.get("position")
    if context == "held" and isinstance(position, dict):
        signal_close = _dec_or_none(
            (fields.get("signal_bar") or {}).get("close")
        )
        vs_signal = (
            (price - signal_close) / atr_1h
            if price is not None
            and signal_close is not None
            and atr_1h is not None
            and atr_1h > 0
            else None
        )
        position["price_vs_signal"] = _r2(vs_signal)
        vs_ema20 = price_vs_ema20(price, base.get("indicators_1h"))
        position["price_vs_ema20"] = vs_ema20
        position["thesis_holds"] = thesis_holds_v5(
            position.get("checks_at_entry") or {},
            fields.get("checks") or {},
            vs_ema20=vs_ema20,
            price_vs_signal=vs_signal,
            adverse_atr=v5.price_vs_signal_adverse_atr,
        )

    return V5Build(fields=fields, options_column=column, reason=v4.reason)


def last_cycle_for(
    config: AppConfig,
    calendar: CalendarProvider | None,
    cycle_time_ny: datetime,
) -> bool:
    """True when ``cycle_time_ny`` is the session's last active cycle —
    ``session.last_cycle`` (docs/SPEC.md, Snapshot v5)."""
    if calendar is None:
        return False
    clock = SessionClock(RealClock(), config, calendar)
    day = cycle_time_ny.astimezone(clock.market_tz).date()
    session = calendar.session_for(day)
    if session is None:
        return False
    cycles = clock.active_cycles(session)
    return not cycles or cycle_time_ny >= cycles[-1]
