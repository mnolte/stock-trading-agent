"""Snapshot v4 on top of v3's (docs/SPEC.md, Decision v4).

decision.v4 strategies only: the model sees its own previous decision on
the symbol this session plus the facts code computed since then
(``previous_decision.since``), headlines marked ``about_symbol``/``new``
and capped, levels capped by distance for the sent copy, a ``stop_group``
per option, and on held positions ``checks_at_entry``, ``thesis_holds``
and the time held. The trend rules and the room filter stay exactly as
v3's. v1–v3 snapshots are untouched.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.data.models import Bar1h, Bar5m, DailyBar
from app.db.models import Cycle, Decision, ShadowDecision
from app.decide.snapshot import UNAVAILABLE
from app.decide.v2inputs import V2Build
from app.decide.v3 import (
    _dec_or_none,
    _momentum,
    _r2,
    _trend,
    build_v3_fields,
    v3_send_snapshot,
)
from app.market.clock import NY_TZ, UTC

if TYPE_CHECKING:
    from app.market.calendar import CalendarProvider
    from app.signals.regime import MarketRegime
    from app.signals.snapshot import IndicatorSnapshot

OPTIONS_VERSION_V4 = "v4"

# The categorical checks compared for ``since.checks_changed`` and
# ``thesis_holds`` — room and earnings move every cycle and are not
# check changes (docs/SPEC.md, Decision v4 — Snapshot).
_CHECK_NAMES = ("trend_1h", "trend_daily", "regime_trend", "momentum")
_CHANGED_NAMES = (*_CHECK_NAMES, "regime_volatility")

# A check turned against the trade (docs/SPEC.md — thesis_holds):
# up → mixed or down, mixed → down; momentum strong → mixed or weak,
# mixed → weak.
_ADVERSE = {
    "up": {"mixed", "down"},
    "mixed": {"down"},
    "strong": {"mixed", "weak"},
}


# ----------------------------------------------------------------------
# config


@dataclass(frozen=True)
class V4Config:
    """The ``decision.v4`` thresholds (docs/SPEC.md, Decision v4)."""

    max_headlines: int = 12
    max_level_distance_atr: Decimal = Decimal("4.0")
    flip_min_move_atr: Decimal = Decimal("0.5")
    max_confidence_above_anchor: Decimal = Decimal("0.30")
    checks_gate: bool = False


def _dec(value: Any, default: Decimal) -> Decimal:
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return default


def v4_config(config: AppConfig) -> V4Config:
    """Read ``decision.v4`` from a (possibly strategy-overlaid) config."""
    base = V4Config()
    headlines = config.get("decision", "v4", "max_headlines", default=None)
    return V4Config(
        max_headlines=(
            int(headlines)
            if isinstance(headlines, (int, float)) and headlines
            else base.max_headlines
        ),
        max_level_distance_atr=_dec(
            config.get("decision", "v4", "max_level_distance_atr", default=None),
            base.max_level_distance_atr,
        ),
        flip_min_move_atr=_dec(
            config.get("decision", "v4", "flip_min_move_atr", default=None),
            base.flip_min_move_atr,
        ),
        max_confidence_above_anchor=_dec(
            config.get("decision", "v4", "max_confidence_above_anchor", default=None),
            base.max_confidence_above_anchor,
        ),
        checks_gate=bool(
            config.get("decision", "v4", "checks_gate", default=False)
        ),
    )


def symbol_names(config: AppConfig) -> dict[str, str]:
    """``universe.names``: the company name behind each ticker — what
    ``about_symbol`` matches a headline against (docs/SPEC.md, Snapshot
    v4 — the name from the pool or config)."""
    raw = config.get("universe", "names", default=None)
    if not isinstance(raw, dict):
        return {}
    return {
        str(symbol): str(name)
        for symbol, name in raw.items()
        if str(name).strip()
    }


# ----------------------------------------------------------------------
# about_symbol


def about_symbol(title: str, symbol: str, names: dict[str, str]) -> bool:
    """True when the title names the symbol's company or its ticker.

    A title naming a *different* known company is not about this symbol
    even when the ticker appears in it — "Nvidia RTX laptops" is about
    NVDA, not RTX.
    """
    low = title.lower()
    own = (names.get(symbol) or "").strip().lower()
    if own and own in low:
        return True
    if not re.search(rf"\b{re.escape(symbol)}\b", title, re.IGNORECASE):
        return False
    for other, other_name in names.items():
        if other == symbol:
            continue
        other_low = (other_name or "").strip().lower()
        if other_low and other_low in low:
            return False
    return True


# ----------------------------------------------------------------------
# the previous decision and `since`


def _level_key(level: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """A level's identity across cycles: kind, timeframe, time and price —
    ids are renumbered every cycle (docs/SPEC.md, Decision v3)."""
    kind = level.get("kind")
    if not kind:
        return None
    return (
        str(kind),
        str(level.get("timeframe") or ""),
        str(level.get("time") or ""),
        str(level.get("price") or ""),
    )


@dataclass(frozen=True)
class PreviousV4:
    """The model's last decision on the symbol this session, plus the
    stored input facts ``since`` is computed against."""

    time: datetime | None
    action: str | None  # the model's action; None when no usable answer
    option_id: str | None
    best_option_id: str | None
    confidence: float | None
    conflicts: tuple[str, ...] = ()
    price: Decimal | None = None
    checks: dict[str, Any] = field(default_factory=dict)
    headline_ids: frozenset[str] = frozenset()
    level_keys: frozenset[tuple[str, str, str, str]] = frozenset()

    def to_json(self) -> dict[str, Any]:
        return {
            "time": _ny_iso(self.time) if self.time is not None else None,
            "action": self.action,
            "option_id": self.option_id,
            "best_option_id": self.best_option_id,
            "confidence": self.confidence,
            "conflicts": list(self.conflicts),
            "price": _r2(self.price),
        }


def _ny_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(NY_TZ).isoformat()


def _conflict_texts(output: dict[str, Any]) -> tuple[str, ...]:
    """Conflict text from a stored model output: v4 objects carry
    ``text``; v3/v2 carried plain strings (``risks`` for v1/v2)."""
    raw = output.get("conflicts")
    if raw is None:
        raw = output.get("risks")
    out: list[str] = []
    for item in raw or []:
        text = item.get("text") if isinstance(item, dict) else item
        if text:
            out.append(str(text))
    return tuple(out)


def _snapshot_facts(snapshot: dict[str, Any] | None) -> tuple[
    dict[str, Any], frozenset[str], frozenset[tuple[str, str, str, str]], Decimal | None
]:
    """(checks, headline ids, level identities, price) from a stored
    input snapshot — what ``since`` diffs against."""
    snap = snapshot or {}
    raw_checks = snap.get("checks")
    checks: dict[str, Any] = dict(raw_checks) if isinstance(raw_checks, dict) else {}
    headline_ids = frozenset(
        str(h.get("id"))
        for h in (snap.get("headlines") or [])
        if isinstance(h, dict) and h.get("id") is not None
    )
    level_keys = frozenset(
        key
        for lv in (snap.get("levels") or [])
        if isinstance(lv, dict)
        for key in [_level_key(lv)]
        if key is not None
    )
    return checks, headline_ids, level_keys, _dec_or_none(snap.get("price"))


def _previous_from_row(
    row: Any, scheduled: datetime | None, snapshot: dict[str, Any] | None
) -> PreviousV4:
    snap = snapshot or (row.input_snapshot or {})
    checks, headline_ids, level_keys, price = _snapshot_facts(snap)
    # Level identities prefer the stored levels column (v2+); fall back
    # to the input's level list.
    stored = (row.levels or {}).get("levels") if isinstance(row.levels, dict) else None
    if stored:
        level_keys = frozenset(
            key
            for lv in stored
            if isinstance(lv, dict)
            for key in [_level_key(lv)]
            if key is not None
        )
    output = row.model_output or {}
    return PreviousV4(
        time=scheduled,
        action=(row.validation_result or {}).get("model_action")
        or (str(output.get("action")) if output.get("action") else None),
        option_id=row.chosen_option
        or (str(output.get("option_id")) if output.get("option_id") else None),
        best_option_id=(
            str(output.get("best_option_id"))
            if output.get("best_option_id")
            else None
        ),
        confidence=(
            float(output["confidence"]) if output.get("confidence") is not None else None
        ),
        conflicts=_conflict_texts(output),
        price=price,
        checks=checks,
        headline_ids=headline_ids,
        level_keys=level_keys,
    )


def _session_window(day_ny: datetime | None) -> tuple[datetime, datetime] | None:
    if day_ny is None:
        return None
    start = datetime(day_ny.year, day_ny.month, day_ny.day, tzinfo=NY_TZ)
    end = start + timedelta(days=1)
    return start.astimezone(UTC).replace(tzinfo=None), end.astimezone(UTC).replace(tzinfo=None)


def previous_decision_v4(
    session: Session,
    *,
    symbol: str,
    day_ny: Any,
    strategy_id: int | None = None,
    before: datetime | None = None,
) -> PreviousV4 | None:
    """The model's last decision on ``symbol`` in the New York session
    ``day_ny`` — live rows for live, the strategy's rows for a shadow
    (docs/SPEC.md, Decision v4 — previous_decision). ``no_call`` and
    missed shadow rows are not model decisions and don't count.
    ``before`` bounds the search to cycles scheduled earlier (eval and
    replays must not look ahead)."""
    window = _session_window(day_ny)
    if window is None:
        return None
    start, end = window
    if before is not None:
        bound = before if before.tzinfo is None else before.astimezone(UTC).replace(tzinfo=None)
        end = min(end, bound)
    found: Any = None
    if strategy_id is None:
        found = session.execute(
            select(Decision, Cycle.scheduled_time)
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .where(
                Cycle.kind == "cycle",
                Cycle.scheduled_time >= start,
                Cycle.scheduled_time < end,
                Decision.symbol == symbol,
                Decision.no_call_reason.is_(None),
            )
            .order_by(Cycle.scheduled_time.desc(), Decision.id.desc())
            .limit(1)
        ).first()
    else:
        found = session.execute(
            select(ShadowDecision, Cycle.scheduled_time)
            .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
            .where(
                Cycle.kind == "cycle",
                Cycle.scheduled_time >= start,
                Cycle.scheduled_time < end,
                ShadowDecision.symbol == symbol,
                ShadowDecision.strategy_id == strategy_id,
                ShadowDecision.no_call_reason.is_(None),
                ShadowDecision.missed.is_(False),
            )
            .order_by(Cycle.scheduled_time.desc(), ShadowDecision.id.desc())
            .limit(1)
        ).first()
    if found is None:
        return None
    row, scheduled = found
    snapshot: dict[str, Any] | None = None
    live_id = getattr(row, "live_decision_id", None)
    if not row.input_snapshot and live_id:
        live = session.get(Decision, live_id)
        if live is not None:
            snapshot = live.input_snapshot
    return _previous_from_row(row, scheduled, snapshot)


def _since(
    previous: PreviousV4,
    *,
    fields: dict[str, Any],
    headlines: list[dict[str, Any]],
    price: Decimal | None,
    atr_1h: Decimal | None,
) -> dict[str, Any]:
    """What changed since the previous decision, computed by code
    (docs/SPEC.md, Snapshot v4 — since)."""
    checks_changed: list[str] = []
    current = fields.get("checks") or {}
    for name in _CHANGED_NAMES:
        old = previous.checks.get(name)
        new = current.get(name)
        if old is not None and new is not None and old != new:
            checks_changed.append(f"{name}: {old} → {new}")
    price_move = (
        (price - previous.price) / atr_1h
        if price is not None and previous.price is not None and atr_1h is not None and atr_1h > 0
        else None
    )
    new_levels = [
        str(lv.get("id"))
        for lv in (fields.get("levels") or [])
        if isinstance(lv, dict)
        and lv.get("id") is not None
        and lv.get("distance_atr") is not None
        and abs(float(lv["distance_atr"])) <= 1.0
        and _level_key(lv) not in previous.level_keys
    ]
    return {
        "price_move_atr": _r2(price_move),
        "checks_changed": checks_changed,
        "new_headlines": [
            str(h.get("id")) for h in headlines if h.get("new") and h.get("id") is not None
        ],
        "new_levels_within_1_atr": new_levels,
    }


# ----------------------------------------------------------------------
# checks at entry, thesis_holds, time held


def checks_at_entry(entry_decision: Any) -> dict[str, str]:
    """``position.checks_at_entry``: the stored v3/v4 checks of the entry
    decision, or the v3 rules run on its stored input for older rows
    (docs/SPEC.md, Snapshot v4)."""
    if entry_decision is None:
        return {}
    snap = entry_decision.input_snapshot or {}
    checks = snap.get("checks")
    if isinstance(checks, dict) and any(checks.get(k) for k in _CHECK_NAMES):
        return {k: str(checks.get(k)) for k in _CHECK_NAMES}
    i1h = snap.get("indicators_1h") or {}
    iday = snap.get("indicators_daily") or {}
    regime = snap.get("regime") or {}
    signal = snap.get("signal_bar") or {}
    close_1h = _dec_or_none(signal.get("close"))
    if close_1h is None:
        close_1h = _dec_or_none(snap.get("price"))
    return {
        "trend_1h": _trend(
            close_1h,
            _dec_or_none(i1h.get("ema20")),
            _dec_or_none(i1h.get("ema50")),
        ),
        "trend_daily": _trend(
            _dec_or_none(iday.get("close")),
            _dec_or_none(iday.get("sma50")),
            _dec_or_none(iday.get("sma200")),
        ),
        "regime_trend": str(regime.get("trend") or UNAVAILABLE),
        "momentum": _momentum(
            _dec_or_none(i1h.get("rs_vs_spy_pct")),
            str(i1h.get("obv_trend") or "") or None,
        ),
    }


def thesis_holds(
    at_entry: dict[str, str], checks_now: dict[str, Any]
) -> bool | None:
    """False when 2 or more of the four entry checks turned adverse
    (docs/SPEC.md, Snapshot v4 — a fact for the model, never a gate)."""
    if not at_entry:
        return None
    adverse = 0
    for name in _CHECK_NAMES:
        old = at_entry.get(name)
        new = checks_now.get(name)
        if not old or not new or old == UNAVAILABLE or new == UNAVAILABLE:
            continue
        if str(new) in _ADVERSE.get(str(old), set()):
            adverse += 1
    return adverse < 2


def session_time_held(
    opened_at: datetime | None,
    now_ny: datetime,
    calendar: CalendarProvider | None,
) -> tuple[float | None, int | None]:
    """(regular session hours since the entry fill, sessions held) —
    regular hours only (docs/SPEC.md, Snapshot v4)."""
    if opened_at is None or calendar is None:
        return None, None
    opened = opened_at if opened_at.tzinfo is not None else opened_at.replace(tzinfo=UTC)
    now = now_ny if now_ny.tzinfo is not None else now_ny.replace(tzinfo=UTC)
    today = now.astimezone(NY_TZ).date()
    day = opened.astimezone(NY_TZ).date()
    hours = 0.0
    sessions = 0
    while day <= today:
        session = calendar.session_for(day)
        if session is not None:
            sessions += 1
            open_dt = session.open if session.open.tzinfo else session.open.replace(tzinfo=UTC)
            close_dt = session.close if session.close.tzinfo else session.close.replace(tzinfo=UTC)
            lo, hi = max(opened, open_dt), min(now, close_dt)
            if hi > lo:
                hours += (hi - lo).total_seconds() / 3600
        day += timedelta(days=1)
    return hours, sessions


def minutes_left(now_ny: datetime, calendar: CalendarProvider | None) -> int | None:
    """``session.minutes_left`` until the close, early closes included."""
    if calendar is None:
        return None
    now = now_ny if now_ny.tzinfo is not None else now_ny.replace(tzinfo=UTC)
    session = calendar.session_for(now.astimezone(NY_TZ).date())
    if session is None:
        return None
    close_dt = session.close if session.close.tzinfo else session.close.replace(tzinfo=UTC)
    return max(0, int((close_dt - now).total_seconds() // 60))


# ----------------------------------------------------------------------
# the snapshot itself


@dataclass(frozen=True)
class V4Build(V2Build):
    """Same shape as the v3 build — fields merged into the stored
    snapshot, the options column and the no_options reason."""


def _headlines_v4(
    base: dict[str, Any],
    symbol: str,
    names: dict[str, str],
    previous: PreviousV4 | None,
    v4: V4Config,
) -> list[dict[str, Any]] | Any:
    """Headlines with ``about_symbol`` and ``new``, newest first, capped
    at ``max_headlines`` (docs/SPEC.md, Snapshot v4 — Headlines)."""
    raw = base.get("headlines")
    if not isinstance(raw, list):
        return raw
    seen = previous.headline_ids if previous is not None else frozenset()
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        entry["about_symbol"] = about_symbol(
            str(item.get("title") or ""), symbol, names
        )
        entry["new"] = (
            str(item.get("id")) not in seen
            if item.get("id") is not None
            else True
        )
        out.append(entry)
    out.sort(key=lambda h: str(h.get("time") or ""), reverse=True)
    return out[: v4.max_headlines]


def _stop_groups(options: list[dict[str, Any]]) -> None:
    """``stop_group`` S1, S2, … — options sharing a stop share a group
    (docs/SPEC.md, Snapshot v4 — Options)."""
    groups: dict[str, str] = {}
    for option in options:
        key = str(option.get("stop"))
        if key not in groups:
            groups[key] = f"S{len(groups) + 1}"
        option["stop_group"] = groups[key]


def build_v4_fields(
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
    previous: PreviousV4 | None = None,
    entry_checks: dict[str, str] | None = None,
    names: dict[str, str] | None = None,
    calendar: CalendarProvider | None = None,
    config: AppConfig,
    cycle_time_ny: datetime,
) -> V4Build:
    """The v4 snapshot fields: v3's build plus memory, news marks, stop
    groups and the held-thesis facts (docs/SPEC.md, Decision v4).

    ``previous`` is the model's last decision on the symbol this session
    (``previous_decision_v4``); ``entry_checks`` the entry decision's
    checks (``checks_at_entry``) for held positions.
    """
    v3 = build_v3_fields(
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
        calendar=calendar,
        config=config,
        cycle_time_ny=cycle_time_ny,
    )
    v4 = v4_config(config)
    fields: dict[str, Any] = dict(v3.fields)
    symbol = str(base.get("symbol") or "")

    fields["session"] = {"minutes_left": minutes_left(cycle_time_ny, calendar)}
    headlines = _headlines_v4(base, symbol, names or {}, previous, v4)
    fields["headlines"] = headlines

    if isinstance(fields.get("options"), list):
        _stop_groups(fields["options"])

    sent_headlines = headlines if isinstance(headlines, list) else []
    if previous is not None:
        fields["previous_decision"] = previous.to_json() | {
            "since": _since(
                previous,
                fields=fields,
                headlines=sent_headlines,
                price=price,
                atr_1h=atr_1h,
            )
        }
    else:
        fields["previous_decision"] = None

    if (
        context == "held"
        and held is not None
        and isinstance(fields.get("position"), dict)
    ):
        at_entry = entry_checks or {}
        hours, sessions = session_time_held(held.opened_at, cycle_time_ny, calendar)
        fields["position"]["checks_at_entry"] = at_entry or None
        fields["position"]["thesis_holds"] = thesis_holds(
            at_entry, fields.get("checks") or {}
        )
        fields["position"]["held_session_hours"] = (
            round(hours, 1) if hours is not None else None
        )
        fields["position"]["sessions_held"] = sessions

    column = dict(v3.options_column)
    column["options_version"] = OPTIONS_VERSION_V4
    return V4Build(fields=fields, options_column=column, reason=v3.reason)


# ----------------------------------------------------------------------
# provenance stripping + the level cap for the sent snapshot


def v4_send_snapshot(stored: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    """The snapshot as the model sees it: v3's strip, then the level cap —
    only levels within ``max_level_distance_atr`` of the anchor plus any
    level an option or the position's stop names (docs/SPEC.md, Snapshot
    v4 — Levels)."""
    out = v3_send_snapshot(stored)
    v4 = v4_config(config)
    keep: set[str] = set()
    for option in [
        *(out.get("options") or []),
        *(out.get("stop_options") or []),
    ]:
        if not isinstance(option, dict):
            continue
        for key in ("stop_level", "target_level"):
            if option.get(key):
                keep.add(str(option[key]))
        for lv in option.get("levels_in_way") or []:
            if isinstance(lv, dict) and lv.get("id"):
                keep.add(str(lv["id"]))
    position = out.get("position") or {}
    if isinstance(position, dict):
        if position.get("stop_level"):
            keep.add(str(position["stop_level"]))
        for lv in position.get("levels_to_target") or []:
            if isinstance(lv, dict) and lv.get("id"):
                keep.add(str(lv["id"]))
    out["levels"] = [
        lv
        for lv in (out.get("levels") or [])
        if not isinstance(lv, dict)
        or lv.get("distance_atr") is None
        or abs(float(lv["distance_atr"])) <= float(v4.max_level_distance_atr)
        or str(lv.get("id")) in keep
    ]
    return out
