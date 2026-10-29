"""Snapshot v3, option builder version 2 and the v3 checks.

decision.v3 only (docs/SPEC.md, Decision v3): code works out the checks
(trend, regime, room, earnings, news, momentum), merges levels at the same
price into one id, measures every distance in ATR and computes each
option's break-even confidence. Options whose first level in the way sits
closer than ``decision.v3.min_room_atr`` are removed by code — never by
the model. v1 and v2 inputs are untouched.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from app.config import AppConfig
from app.data.models import Bar1h, Bar5m, DailyBar
from app.decide.levels import (
    Level,
    OptionsConfig,
    find_levels,
    options_config,
)
from app.decide.options import (
    EntryOption,
    EntryOptions,
    build_entry_options,
)
from app.decide.snapshot import UNAVAILABLE
from app.decide.v2inputs import V2Build
from app.market.clock import NY_TZ, UTC

if TYPE_CHECKING:
    from app.market.calendar import CalendarProvider
    from app.signals.regime import MarketRegime
    from app.signals.snapshot import IndicatorSnapshot

CENT = Decimal("0.01")
OPTIONS_VERSION_V3 = "v3"

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


# ----------------------------------------------------------------------
# config


@dataclass(frozen=True)
class V3Config:
    """The ``decision.v3`` thresholds (docs/SPEC.md, Decision v3)."""

    min_room_atr: Decimal = Decimal("1.0")
    room_level_kinds: tuple[str, ...] = (
        "swing_high",
        "swing_low",
        "session_high",
        "prior_session_high",
        "prior_session_low",
    )
    min_edge: Decimal = Decimal("0.10")
    min_stop_move_atr: Decimal = Decimal("0.25")
    breakeven_min_r: Decimal = Decimal("1.0")
    held_stop_min_atr: Decimal = Decimal("1.0")
    level_merge_atr: Decimal = Decimal("0.05")


def _dec(value: Any, default: Decimal) -> Decimal:
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return default


def v3_config(config: AppConfig) -> V3Config:
    """Read ``decision.v3`` from a (possibly strategy-overlaid) config."""
    base = V3Config()
    kinds = config.get("decision", "v3", "room_level_kinds", default=None)
    return V3Config(
        min_room_atr=_dec(
            config.get("decision", "v3", "min_room_atr", default=None),
            base.min_room_atr,
        ),
        room_level_kinds=(
            tuple(str(k) for k in kinds) if isinstance(kinds, list) and kinds
            else base.room_level_kinds
        ),
        min_edge=_dec(
            config.get("decision", "v3", "min_edge", default=None),
            base.min_edge,
        ),
        min_stop_move_atr=_dec(
            config.get("decision", "v3", "min_stop_move_atr", default=None),
            base.min_stop_move_atr,
        ),
        breakeven_min_r=_dec(
            config.get("decision", "v3", "breakeven_min_r", default=None),
            base.breakeven_min_r,
        ),
        held_stop_min_atr=_dec(
            config.get("decision", "v3", "held_stop_min_atr", default=None),
            base.held_stop_min_atr,
        ),
        level_merge_atr=_dec(
            config.get("decision", "v3", "level_merge_atr", default=None),
            base.level_merge_atr,
        ),
    )


# ----------------------------------------------------------------------
# rounding (docs/SPEC.md, Snapshot v3 — Rounded)


def _r2(value: Decimal | None) -> float | None:
    return float(round(value, 2)) if value is not None else None


def _r4(value: Decimal | None) -> float | None:
    return float(round(value, 4)) if value is not None else None


def _money(value: Decimal | None) -> float | None:
    """Prices and levels to cents."""
    return _r2(value)


# ----------------------------------------------------------------------
# merged levels


# Kind strength for merging: swings (the structure stops and targets sit
# on) win over session markers; a same-price VWAP or prior close is noise
# next to a real level.
_KIND_RANK = {
    "swing_high": 0,
    "swing_low": 0,
    "session_high": 1,
    "session_low": 1,
    "prior_session_high": 2,
    "prior_session_low": 2,
    "prior_session_close": 3,
    "vwap": 4,
}


def merge_levels(levels: Sequence[Level], atr_1h: Decimal, merge_atr: Decimal) -> list[Level]:
    """Merge levels within ``merge_atr`` × ATR of each other into one.

    The survivor keeps the highest-ranked kind (and its id), lists every
    kind in ``kinds`` and drops the absorbed levels — the remaining ids
    are unchanged, so a level keeps its id across the cycle.
    """
    within = merge_atr * atr_1h
    if within <= 0 or not levels:
        return list(levels)
    out: list[Level] = []
    for level in levels:
        hit = next(
            (k for k in out if abs(k.price - level.price) <= within), None
        )
        if hit is None:
            out.append(level)
            continue
        kinds = list(dict.fromkeys([*(hit.kinds or [hit.kind]), level.kind]))
        kinds.sort(key=lambda k: _KIND_RANK.get(k, 9))
        if _KIND_RANK.get(level.kind, 9) < _KIND_RANK.get(hit.kind, 9):
            out[out.index(hit)] = Level(
                id=level.id,
                price=level.price,
                kind=level.kind,
                timeframe=level.timeframe,
                time=level.time,
                kinds=tuple(kinds),
            )
        else:
            out[out.index(hit)] = Level(
                id=hit.id,
                price=hit.price,
                kind=hit.kind,
                timeframe=hit.timeframe,
                time=hit.time,
                kinds=tuple(kinds),
            )
    return out


def level_json_v3(
    level: Level, anchor: Decimal | None, atr_1h: Decimal | None
) -> dict[str, Any]:
    """A v3 level: rounded price, ``kinds`` when merged, ``distance_atr``
    from the anchor (entry limit for candidates, market price for held)."""
    out: dict[str, Any] = {
        "id": level.id,
        "price": _money(level.price),
        "kind": level.kind,
        "timeframe": level.timeframe,
        "time": level.time.isoformat() if level.time else None,
        "distance_atr": (
            _r2((level.price - anchor) / atr_1h)
            if anchor is not None and atr_1h is not None and atr_1h > 0
            else None
        ),
    }
    if level.kinds and len(level.kinds) > 1:
        out["kinds"] = list(level.kinds)
    return out


def _levels_from_stored(raw: Any) -> list[Level]:
    """Stored v2/v3 level dicts back to Level objects (replay derives v3
    from the stored v2 input — no refetch, no bar rebuild needed)."""
    out: list[Level] = []
    for i, lv in enumerate(raw or []):
        if not isinstance(lv, dict):
            continue
        try:
            when = (
                datetime.fromisoformat(str(lv["time"]))
                if lv.get("time")
                else None
            )
        except ValueError:
            when = None
        out.append(
            Level(
                id=str(lv.get("id") or f"L{i + 1}"),
                price=Decimal(str(lv.get("price"))),
                kind=str(lv.get("kind")),
                timeframe=str(lv.get("timeframe") or ""),
                time=when,
                kinds=tuple(lv["kinds"]) if lv.get("kinds") else None,
            )
        )
    return out


# ----------------------------------------------------------------------
# checks


def _trend(close: Decimal | None, fast: Decimal | None, slow: Decimal | None) -> str:
    """up: close > fast > slow; down: close < fast < slow; else mixed."""
    if close is None or fast is None or slow is None:
        return UNAVAILABLE
    if close > fast > slow:
        return "up"
    if close < fast < slow:
        return "down"
    return "mixed"


def _momentum(rs_vs_spy_pct: Decimal | None, obv_trend: str | None) -> str:
    if rs_vs_spy_pct is None or not obv_trend:
        return UNAVAILABLE
    if rs_vs_spy_pct > 0 and obv_trend == "up":
        return "strong"
    if rs_vs_spy_pct < 0 and obv_trend == "down":
        return "weak"
    return "mixed"


def room_check(
    levels: Sequence[Level],
    limit: Decimal,
    atr_1h: Decimal,
    room_kinds: Sequence[str],
) -> tuple[Level | None, Decimal | None]:
    """The first level of a room kind above ``limit`` and its distance in
    ATR — the snapshot's ``checks.room_level`` / ``checks.room_atr``."""
    kinds = set(room_kinds)
    first = next(
        (
            lv
            for lv in sorted(levels, key=lambda lv: lv.price)
            if lv.price > limit and (lv.kind in kinds or set(lv.kinds or ()) & kinds)
        ),
        None,
    )
    if first is None or atr_1h <= 0:
        return None, None
    return first, (first.price - limit) / atr_1h


def room_failure_reason(
    level: Level, distance_atr: Decimal, min_room_atr: Decimal
) -> str:
    """"room: L7 1h swing high 0.31 ATR above the limit (min 1.0)"."""
    kind_text = {
        "swing_high": f"{level.timeframe} swing high",
        "swing_low": f"{level.timeframe} swing low",
        "prior_session_high": "prior session high",
        "prior_session_low": "prior session low",
        "session_high": "session high",
        "session_low": "session low",
    }.get(level.kind, level.kind.replace("_", " "))
    return (
        f"room: {level.id} {kind_text} {distance_atr:.2f} ATR above "
        f"the limit (min {min_room_atr})"
    )


def room_recheck_failure(
    levels: Sequence[dict[str, Any]],
    limit: Decimal,
    atr_1h: Decimal,
    v3: V3Config,
) -> str | None:
    """The order-time room check: the fresh limit must leave at least
    ``min_room_atr`` to the first room-kind level (docs/SPEC.md, Decision
    v3 — Room at order time). ``levels`` are the stored level dicts."""
    parsed = _levels_from_stored(levels)
    level, distance = room_check(parsed, limit, atr_1h, v3.room_level_kinds)
    if level is not None and distance is not None and distance < v3.min_room_atr:
        return room_failure_reason(level, distance, v3.min_room_atr)
    return None


# ----------------------------------------------------------------------
# entry options with the room filter (option builder version 2)


def _levels_in_way_v3(
    option_levels: Sequence[str],
    levels: Sequence[Level],
    limit: Decimal,
    atr_1h: Decimal,
) -> list[dict[str, Any]]:
    by_id = {lv.id: lv for lv in levels}
    out = []
    for lid in option_levels:
        lv = by_id.get(lid)
        if lv is None:
            continue
        out.append({"id": lid, "distance_atr": _r2((lv.price - limit) / atr_1h)})
    return out


def option_json_v3(
    option: EntryOption,
    levels: Sequence[Level],
    limit: Decimal,
    atr_1h: Decimal,
) -> dict[str, Any]:
    """Rounded v3 option: distances and ratios to 2 decimals."""
    rr = option.rr
    return {
        "id": option.id,
        "stop": _money(option.stop),
        "stop_basis": option.stop_basis,
        "stop_atr": _r2(option.stop_atr),
        "stop_level": option.stop_level,
        "target": _money(option.target),
        "target_basis": option.target_basis,
        "target_level": option.target_level,
        "rr": _r2(rr),
        "breakeven_confidence": _r2(Decimal(1) / (1 + rr)),
        "risk": _r2(option.risk),
        "levels_in_way": _levels_in_way_v3(
            option.levels_in_way, levels, limit, atr_1h
        ),
    }


def _room_blocks(
    option: EntryOption,
    levels: Sequence[Level],
    limit: Decimal,
    atr_1h: Decimal,
    v3: V3Config,
) -> tuple[Level, Decimal] | None:
    """The first room-kind level in this option's way — the nearest one
    above the limit, since ``levels_in_way`` runs highest price first —
    when it sits closer than ``min_room_atr``."""
    kinds = set(v3.room_level_kinds)
    by_id = {lv.id: lv for lv in levels}
    first: Level | None = None
    for lid in option.levels_in_way:
        lv = by_id.get(lid)
        if lv is None or lv.price <= limit or lv.price >= option.target:
            continue
        if lv.kind not in kinds and not (set(lv.kinds or ()) & kinds):
            continue
        if first is None or lv.price < first.price:
            first = lv
    if first is None:
        return None
    distance = (first.price - limit) / atr_1h
    if distance < v3.min_room_atr:
        return first, distance
    return None


def apply_room_filter(
    result: EntryOptions,
    levels: Sequence[Level],
    limit: Decimal,
    atr_1h: Decimal,
    v3: V3Config,
) -> tuple[list[EntryOption], list[dict[str, Any]], str | None]:
    """Remove options without room; returns (kept, removed, reason).

    ``removed`` carries {id, reason} for storage; ``reason`` is the
    no_options detail when nothing is left.
    """
    kept: list[EntryOption] = []
    removed: list[dict[str, Any]] = []
    reasons: list[str] = []
    for option in result.options:
        blocked = _room_blocks(option, levels, limit, atr_1h, v3)
        if blocked is None:
            kept.append(option)
            continue
        level, distance = blocked
        opt_reason = room_failure_reason(level, distance, v3.min_room_atr)
        removed.append({"id": option.id, "reason": opt_reason})
        reasons.append(opt_reason)
    reason = reasons[0] if not kept and reasons else None
    return kept, removed, reason


# ----------------------------------------------------------------------
# held stop options (option builder version 2)


@dataclass(frozen=True)
class StopOptionV3:
    """One offered or dropped new stop for a held position."""

    id: str
    stop: Decimal
    basis: str
    locks_profit: bool
    locked_r: Decimal | None
    distance_atr: Decimal | None
    offered: bool
    drop_reason: str | None = None


def stop_option_json_v3(option: StopOptionV3) -> dict[str, Any]:
    return {
        "id": option.id,
        "stop": _money(option.stop),
        "basis": option.basis,
        "locks_profit": option.locks_profit,
        "locked_r": _r2(option.locked_r),
        "distance_atr": _r2(option.distance_atr),
        "offered": option.offered,
        "drop_reason": option.drop_reason,
    }


def build_stop_options_v3(
    *,
    bars_1h: Sequence[Bar1h],
    entry: Decimal | None,
    opened_at: datetime | None,
    current_stop: Decimal | None,
    initial_stop: Decimal | None,
    reference: Decimal,
    price: Decimal | None,
    atr_1h: Decimal,
    cfg: OptionsConfig,
    v3: V3Config,
) -> list[StopOptionV3]:
    """T1–T3 with the v3 drops: a move under ``min_stop_move_atr``,
    break-even before ``breakeven_min_r`` and a trail or swing-low stop
    closer than ``held_stop_min_atr`` to the price are listed as not
    offered, with their reason (docs/SPEC.md, Decision v3 — Stop options)."""
    initial_risk = (
        entry - initial_stop
        if initial_stop is not None and entry is not None and entry > initial_stop
        else None
    )
    r_now = (
        (price - entry) / initial_risk
        if initial_risk is not None
        and initial_risk > 0
        and price is not None
        and entry is not None
        else None
    )
    # Candidates are reported in slot order T1–T3: swing low, break-even,
    # trail. build_stop_options drops a stop that does not raise the
    # current one; the v3 drops need every candidate, so the raw
    # candidates are rebuilt here with the same rules.
    raw = _stop_candidates(
        bars_1h=bars_1h,
        entry=entry,
        opened_at=opened_at,
        current_stop=current_stop,
        reference=reference,
        atr_1h=atr_1h,
        cfg=cfg,
    )
    out: list[StopOptionV3] = []
    for i, cand in enumerate(raw):
        basis, stop, is_breakeven = cand
        distance_atr = (
            (price - stop) / atr_1h
            if price is not None and atr_1h > 0
            else None
        )
        locked_r = (
            (stop - entry) / initial_risk
            if initial_risk is not None and initial_risk > 0 and entry is not None
            else None
        )
        locks_profit = entry is not None and stop > entry
        move_atr = (
            (stop - current_stop) / atr_1h
            if current_stop is not None and atr_1h > 0
            else None
        )
        reason = _drop_reason(
            stop=stop,
            is_breakeven=is_breakeven,
            move_atr=move_atr,
            distance_atr=distance_atr,
            r_now=r_now,
            v3=v3,
        )
        out.append(
            StopOptionV3(
                id=f"T{i + 1}",
                stop=stop,
                basis=basis,
                locks_profit=locks_profit,
                locked_r=locked_r,
                distance_atr=distance_atr,
                offered=reason is None,
                drop_reason=reason,
            )
        )
    return out


def _stop_candidates(
    *,
    bars_1h: Sequence[Bar1h],
    entry: Decimal | None,
    opened_at: datetime | None,
    current_stop: Decimal | None,
    reference: Decimal,
    atr_1h: Decimal,
    cfg: OptionsConfig,
) -> list[tuple[str, Decimal, bool]]:
    """The raw T1–T3 candidates with the v2 keep rules (above the current
    stop, at least the tighten margin below the reference)."""
    from app.decide.levels import _swings

    margin = cfg.tighten_min_atr_below_price * atr_1h
    if opened_at is not None and opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=UTC)
    candidates: list[tuple[str, Decimal, bool]] = []
    _highs, lows = _swings(bars_1h, cfg.swing_strength, cfg.swing_lookback_bars_1h)
    swing = next(
        (s for s in reversed(lows) if current_stop is None or s.price > current_stop),
        None,
    )
    if swing is not None:
        candidates.append(
            (
                f"{cfg.level_buffer_atr.normalize()} ATR below the "
                f"{swing.time.day} {_MONTHS[swing.time.month - 1]} "
                f"{swing.time:%H:%M} swing low",
                swing.price - cfg.level_buffer_atr * atr_1h,
                False,
            )
        )
    if entry is not None:
        candidates.append(("break-even", entry, True))
    since_entry = [b for b in bars_1h if opened_at is None or b.end > opened_at]
    if since_entry:
        highest_close = max(b.close for b in since_entry)
        candidates.append(
            (
                f"{cfg.held_trail_atr.normalize()} × ATR trail from "
                f"{highest_close.quantize(CENT, rounding=ROUND_HALF_UP)}",
                highest_close - cfg.held_trail_atr * atr_1h,
                False,
            )
        )
    return [
        (basis, stop, be)
        for basis, stop, be in candidates
        if (current_stop is None or stop > current_stop)
        and reference - stop >= margin
    ]


def _drop_reason(
    *,
    stop: Decimal,
    is_breakeven: bool,
    move_atr: Decimal | None,
    distance_atr: Decimal | None,
    r_now: Decimal | None,
    v3: V3Config,
) -> str | None:
    """Why a candidate is not offered, in the order the rules apply."""
    if move_atr is not None and move_atr < v3.min_stop_move_atr:
        return (
            f"moves the stop {move_atr:.2f} ATR "
            f"(min {v3.min_stop_move_atr})"
        )
    if is_breakeven and (r_now is None or r_now < v3.breakeven_min_r):
        return (
            f"break-even from +{v3.breakeven_min_r}R; "
            f"now {f'{r_now:+.2f}' if r_now is not None else 'unavailable'}R"
        )
    if (
        not is_breakeven
        and distance_atr is not None
        and distance_atr < v3.held_stop_min_atr
    ):
        return (
            f"{distance_atr:.2f} ATR below the price "
            f"(min {v3.held_stop_min_atr})"
        )
    return None


# ----------------------------------------------------------------------
# the v3 position block (held)


@dataclass(frozen=True)
class StopBasisInfo:
    """What the entry's chosen option says about the initial stop, used
    to write ``invalidation`` and find ``stop_level`` (docs/SPEC.md,
    Decision v3 — Position context)."""

    basis: str  # e.g. "0.1 ATR below L13 · 1h swing low 20 Oct 12:30" | "2 × ATR"
    level_kind: str | None = None
    level_timeframe: str | None = None
    level_time: datetime | None = None
    level_price: Decimal | None = None


@dataclass(frozen=True)
class HeldV3:
    """The held inputs the v3 position block and stop options need."""

    entry: Decimal | None
    qty: int | None
    initial_stop: Decimal | None
    current_stop: Decimal | None
    target: Decimal | None
    opened_at: datetime | None
    entry_thesis: str | None
    stop_basis: StopBasisInfo | None
    earnings: tuple[date, str] | None = None  # (date, time_of_day)
    # The entry decision's stored levels, so entry_thesis ids can be
    # rewritten to this cycle's ids (docs/SPEC.md, Decision v3).
    entry_levels: tuple[dict[str, Any], ...] = ()


def stop_basis_info(decision: Any) -> StopBasisInfo | None:
    """The stored entry decision's chosen stop basis, with its level
    resolved by kind, timeframe, time and price (docs/SPEC.md, Decision
    v3 — Position context). Works on live ``Decision`` and
    ``ShadowDecision`` rows alike."""
    if decision is None:
        return None
    chosen = decision.chosen_option
    column = decision.options or {}
    entries = column.get("options") if isinstance(column, dict) else None
    option = next(
        (
            o
            for o in (entries or [])
            if isinstance(o, dict) and o.get("id") == chosen
        ),
        None,
    )
    if option is None:
        return None
    basis = str(option.get("stop_basis") or "")
    level_id = option.get("stop_level")
    levels = (decision.levels or {}).get("levels") or []
    level = next(
        (
            lv
            for lv in levels
            if isinstance(lv, dict) and lv.get("id") == level_id
        ),
        None,
    ) if level_id else None
    if level is None:
        return StopBasisInfo(basis=basis)
    try:
        when = (
            datetime.fromisoformat(str(level["time"]))
            if level.get("time")
            else None
        )
    except ValueError:
        when = None
    price = _dec_or_none(level.get("price"))
    return StopBasisInfo(
        basis=basis,
        level_kind=str(level.get("kind")),
        level_timeframe=str(level.get("timeframe") or ""),
        level_time=when,
        level_price=price,
    )


def held_v3(
    position: Any,
    entry_decision: Any,
    earnings: tuple[date, str] | None = None,
) -> HeldV3:
    """HeldV3 from a live ``Position`` row and its entry decision."""
    thesis = None
    if entry_decision is not None:
        reasoning = entry_decision.model_output or {}
        thesis = reasoning.get("thesis")
    return HeldV3(
        entry=position.avg_entry_price,
        qty=position.qty,
        initial_stop=position.initial_stop,
        current_stop=position.current_stop_price,
        target=position.target_price,
        opened_at=position.opened_at,
        entry_thesis=thesis,
        stop_basis=stop_basis_info(entry_decision),
        earnings=earnings,
        entry_levels=tuple(
            lv
            for lv in ((entry_decision.levels or {}).get("levels") or ())
            if isinstance(lv, dict)
        )
        if entry_decision is not None
        else (),
    )


def _fmt_level_ref(kind: str, timeframe: str, when: datetime | None) -> str:
    """"the 20 Oct 12:30 1h swing low" — a level by kind, time and price."""
    kind_text = f"{timeframe} {kind.replace('_', ' ')}" if timeframe else kind.replace("_", " ")
    if when is None:
        return f"the {kind_text}"
    stamp = f"{when.day} {_MONTHS[when.month - 1]}"
    if when.hour or when.minute:
        stamp += f" {when:%H:%M}"
    return f"the {stamp} {kind_text}"


def invalidation_v3(initial_stop: Decimal | None, info: StopBasisInfo | None) -> str:
    """"below 237.91: 0.1 ATR below the 20 Oct 12:30 1h swing low
    (238.22)" or "below 101.74: 2 × ATR stop" (docs/SPEC.md)."""
    if initial_stop is None:
        return UNAVAILABLE
    if info is None or not info.basis:
        return UNAVAILABLE
    if info.level_kind is not None:
        buffer = info.basis.split(" ATR below")[0]
        ref = _fmt_level_ref(info.level_kind, info.level_timeframe or "", info.level_time)
        price = f" ({info.level_price:.2f})" if info.level_price is not None else ""
        return f"below {initial_stop:.2f}: {buffer} ATR below {ref}{price}"
    basis = info.basis.split(" · ")[0]
    return f"below {initial_stop:.2f}: {basis} stop"


def stop_level_match(
    info: StopBasisInfo | None, levels: Sequence[Level]
) -> str | None:
    """This cycle's id of the level the initial stop was built on,
    matched by kind, timeframe, time and price (ids renumber)."""
    if info is None or info.level_kind is None:
        return None
    for level in levels:
        kinds = set(level.kinds or (level.kind,))
        if info.level_kind not in kinds or level.timeframe != info.level_timeframe:
            continue
        if info.level_price is not None and level.price != info.level_price:
            continue
        if info.level_time is not None and level.time != info.level_time:
            continue
        return level.id
    return None


def rewrite_thesis_ids(
    thesis: str | None,
    stored_levels: Sequence[dict[str, Any]],
    levels: Sequence[Level],
) -> str | None:
    """Rewrite level ids in the stored entry thesis to this cycle's ids
    (ids are renumbered every cycle). A level that is no longer listed
    becomes its description — "the 20 Oct 12:30 1h swing low (238.22)"
    (docs/SPEC.md, Decision v3 — Position context). The stored thesis
    keeps its original text; only the sent copy is rewritten."""
    if not thesis:
        return thesis
    by_id = {
        str(lv.get("id")): lv
        for lv in stored_levels
        if isinstance(lv, dict) and lv.get("id")
    }

    def _replace(match: re.Match[str]) -> str:
        stored = by_id.get(match.group(0))
        if stored is None:
            return match.group(0)
        try:
            when = (
                datetime.fromisoformat(str(stored["time"]))
                if stored.get("time")
                else None
            )
        except ValueError:
            when = None
        info = StopBasisInfo(
            basis="",
            level_kind=str(stored.get("kind")),
            level_timeframe=str(stored.get("timeframe") or ""),
            level_time=when,
            level_price=_dec_or_none(stored.get("price")),
        )
        new_id = stop_level_match(info, levels)
        if new_id is not None:
            return new_id
        price = (
            f" ({info.level_price:.2f})" if info.level_price is not None else ""
        )
        return f"{_fmt_level_ref(info.level_kind or '', info.level_timeframe or '', when)}{price}"

    return re.sub(r"\bL\d+\b", _replace, thesis)


def scheduled_exit_text(
    earnings: tuple[date, str] | None,
    now: datetime,
    *,
    calendar: CalendarProvider,
    config: AppConfig,
) -> str | None:
    """"earnings exit Thu 29 Oct 15:46 NY" — when code will sell the
    position anyway (the earnings exit, hold-through off)."""
    if config.get("risk", "hold_through_earnings", default=False):
        return None
    if earnings is None:
        return None
    report_date, time_of_day = earnings
    today = now.astimezone(NY_TZ).date()
    exit_day: date | None
    if time_of_day == "after_close":
        exit_day = report_date
    else:
        prev = calendar.previous_trading_day(report_date)
        exit_day = prev.date if prev is not None else None
    if exit_day is None or exit_day < today:
        return None
    cycles = list(config.get("schedule", "cycles", default=[]) or [])
    last = cycles[-1] if cycles else "15:46"
    return (
        f"earnings exit {exit_day:%a} {exit_day.day} "
        f"{_MONTHS[exit_day.month - 1]} {last} NY"
    )


def position_block_v3(
    held: HeldV3,
    *,
    price: Decimal | None,
    atr_1h: Decimal | None,
    levels: Sequence[Level],
    scheduled_exit: str | None,
) -> dict[str, Any]:
    """The v3 ``position`` object (docs/SPEC.md, Held positions in v3)."""
    initial_risk = (
        held.entry - held.initial_stop
        if held.entry is not None
        and held.initial_stop is not None
        and held.entry > held.initial_stop
        else None
    )
    r_now = (
        (price - held.entry) / initial_risk
        if initial_risk is not None
        and initial_risk > 0
        and price is not None
        and held.entry is not None
        else None
    )
    stop_distance = (
        (price - held.current_stop) / atr_1h
        if price is not None and held.current_stop is not None and atr_1h
        else None
    )
    target_distance = (
        (held.target - price) / atr_1h
        if price is not None and held.target is not None and atr_1h
        else None
    )
    neutral = None
    if (
        price is not None
        and held.current_stop is not None
        and held.target is not None
        and held.target > held.current_stop
    ):
        sd = price - held.current_stop
        td = held.target - price
        if sd > 0 and td > 0:
            neutral = _r2(sd / (sd + td))
    levels_to_target = (
        [
            {
                "id": lv.id,
                "distance_atr": _r2((lv.price - price) / atr_1h),
            }
            for lv in sorted(levels, key=lambda lv: lv.price)
            if price is not None
            and held.target is not None
            and atr_1h
            and price < lv.price < held.target
        ]
        if atr_1h
        else []
    )
    return {
        "entry": _money(held.entry),
        "qty": held.qty,
        "initial_stop": _money(held.initial_stop),
        "stop": _money(held.current_stop),
        "target": _money(held.target),
        "opened_at": (
            (
                held.opened_at
                if held.opened_at.tzinfo is not None
                else held.opened_at.replace(tzinfo=UTC)
            ).astimezone(NY_TZ).isoformat()
            if held.opened_at is not None
            else None
        ),
        "entry_thesis": (
            rewrite_thesis_ids(held.entry_thesis, held.entry_levels, levels)
            or UNAVAILABLE
        ),
        "invalidation": invalidation_v3(held.initial_stop, held.stop_basis),
        "stop_level": stop_level_match(held.stop_basis, levels),
        "r_now": _r2(r_now),
        "stop_distance_atr": _r2(stop_distance),
        "target_distance_atr": _r2(target_distance),
        "neutral_probability": neutral,
        "levels_to_target": levels_to_target,
        "scheduled_exit": scheduled_exit,
    }


# ----------------------------------------------------------------------
# the snapshot itself


@dataclass(frozen=True)
class V3Build(V2Build):
    """Fields to merge into the stored snapshot, the options column and
    the no_options reason (option builder version 2). Same shape as the
    v2 build, so every ``V2Build`` slot accepts it."""


def _quote_v3(snapshot: dict[str, Any]) -> dict[str, Any]:
    """bid/ask only when plausible (docs/SPEC.md, Snapshot v3 — Quote).

    The stored quote keeps its provenance (timestamps, feed, fetched_at);
    ``v3_send_snapshot`` strips it for the model.
    """
    raw = dict(snapshot.get("quote") or {})
    plausible = bool(raw.get("plausible"))
    bid = _dec_or_none(raw.get("bid")) if plausible else None
    ask = _dec_or_none(raw.get("ask")) if plausible else None
    return raw | {
        "bid": _money(bid),
        "ask": _money(ask),
        "plausible": plausible,
        "spread_unknown": bool(raw.get("spread_unknown", not plausible)),
    }


def _dec_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except ArithmeticError:
        return None


def build_v3_fields(
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
    earnings: tuple[date, str] | None = None,
    held: HeldV3 | None = None,
    calendar: CalendarProvider | None = None,
    config: AppConfig,
    cycle_time_ny: datetime,
) -> V3Build:
    """The v3 snapshot fields for one decision.

    ``base`` is the v1-shaped snapshot dict; its levels/options fields are
    replaced wholesale with the v3 shape. ``earnings`` is the symbol's
    next report (date, time_of_day) when the calendar knows it.
    """
    cfg = options_config(config)
    v3 = v3_config(config)
    anchor = price if price is not None else reference

    # --- levels: found fresh from bars when given, else derived from the
    # stored v2 input's level list (replay path, no refetch).
    if bars_1h or daily or today_5m:
        found = (
            find_levels(
                bars_1h=bars_1h, daily=daily, today_5m=today_5m,
                price=anchor, cfg=cfg,
            )
            if anchor is not None
            else []
        )
    else:
        found = _levels_from_stored(base.get("levels"))
    levels = (
        merge_levels(found, atr_1h, v3.level_merge_atr)
        if atr_1h is not None and atr_1h > 0
        else list(found)
    )

    offset = Decimal(
        str(config.get("execution", "entry_limit_offset_pct", default=0.05))
    )
    limit = (
        (price * (1 + offset / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
        if context == "candidate" and price is not None
        else None
    )
    distance_anchor = limit if context == "candidate" else price

    # --- checks (worked out by code, given as facts)
    close_1h = (
        bars_1h[-1].close
        if bars_1h
        else (price if price is not None else None)
    )
    room_level, room_atr = (
        room_check(levels, limit, atr_1h, v3.room_level_kinds)
        if limit is not None and atr_1h is not None and atr_1h > 0
        else (None, None)
    )
    days_to_earnings: Any = (
        UNAVAILABLE
        if indicators is not None and not indicators.earnings_available
        else (indicators.days_to_earnings if indicators is not None else None)
    )
    checks: dict[str, Any] = {
        "trend_1h": _trend(
            close_1h,
            indicators.ema_fast if indicators is not None else None,
            indicators.ema_slow if indicators is not None else None,
        ),
        "trend_daily": _trend(
            indicators.daily_close if indicators is not None else None,
            indicators.daily_sma_fast if indicators is not None else None,
            indicators.daily_sma_slow if indicators is not None else None,
        ),
        "regime_trend": regime.trend if regime is not None else UNAVAILABLE,
        "regime_volatility": (
            regime.volatility if regime is not None else UNAVAILABLE
        ),
        "room_atr": _r2(room_atr),
        "room_level": room_level.id if room_level is not None else None,
        "earnings": {
            "days": days_to_earnings,
            "date": earnings[0].isoformat() if earnings else None,
            "time_of_day": earnings[1] if earnings else None,
        },
        "news_status": base.get("news_status") or UNAVAILABLE,
        "momentum": _momentum(
            indicators.rs_vs_spy_pct if indicators is not None else None,
            indicators.obv_trend if indicators is not None else None,
        ),
    }

    fields: dict[str, Any] = {
        "levels": [
            level_json_v3(lv, distance_anchor, atr_1h) for lv in levels
        ],
        "checks": checks,
        "quote": _quote_v3(base),
        "price_time": (base.get("quote") or {}).get("trade_timestamp"),
        "signal_bar": {
            "time": base.get("signal_bar_time"),
            "close": (
                _money(close_1h) if close_1h is not None else None
            ),
        },
    }
    column: dict[str, Any] = {"options_version": OPTIONS_VERSION_V3}

    if context == "held":
        stop_opts = (
            build_stop_options_v3(
                bars_1h=bars_1h,
                entry=held.entry,
                opened_at=held.opened_at,
                current_stop=held.current_stop,
                initial_stop=held.initial_stop,
                reference=reference,
                price=price,
                atr_1h=atr_1h,
                cfg=cfg,
                v3=v3,
            )
            if held is not None
            and reference is not None
            and atr_1h is not None
            else []
        )
        fields["constraints"] = {
            "atr_1h": _r4(atr_1h),
        }
        fields["stop_options"] = [
            stop_option_json_v3(o) for o in stop_opts if o.offered
        ]
        column["stop_options"] = fields["stop_options"]
        column["dropped_stop_options"] = [
            stop_option_json_v3(o) for o in stop_opts if not o.offered
        ]
        if held is not None:
            scheduled = (
                scheduled_exit_text(
                    held.earnings, cycle_time_ny, calendar=calendar, config=config
                )
                if calendar is not None
                else None
            )
            fields["position"] = position_block_v3(
                held,
                price=price,
                atr_1h=atr_1h,
                levels=levels,
                scheduled_exit=scheduled,
            )
        return V3Build(fields=fields, options_column=column, reason=None)

    # --- candidates: v2 geometry, then the v3 room filter
    result = (
        build_entry_options(
            levels=levels,
            price=price,
            reference=reference,
            limit=limit,
            atr_1h=atr_1h,
            cfg=cfg,
        )
        if price is not None
        and reference is not None
        and atr_1h is not None
        and atr_1h > 0
        and limit is not None
        else None
    )
    options = result.options if result is not None else []
    removed: list[dict[str, Any]] = []
    reason = result.reason if result is not None else None
    if result is not None and options and limit is not None and atr_1h is not None:
        options, removed, room_reason = apply_room_filter(
            result, levels, limit, atr_1h, v3
        )
        if room_reason is not None:
            reason = room_reason
    min_conf = Decimal(
        str(config.get("decision", "min_confidence_enter", default=0.55))
    )
    fields["constraints"] = {
        "atr_1h": _r4(atr_1h),
        "entry_limit": _money(limit),
        "stop_atr_min": float(cfg.stop_atr_min),
        "stop_atr_max": float(cfg.stop_atr_max),
        "min_rr": float(cfg.min_reward_risk),
        "min_room_atr": float(v3.min_room_atr),
        "min_confidence": float(min_conf),
        "min_edge": float(v3.min_edge),
    }
    fields["options"] = (
        [option_json_v3(o, levels, limit, atr_1h) for o in options]
        if limit is not None and atr_1h is not None
        else []
    )
    column.update(
        {
            "options": fields["options"],
            "merged": result.merged if result is not None else [],
            "level_notes": result.level_notes if result is not None else {},
            "removed": removed,
        }
    )
    return V3Build(
        fields=fields,
        options_column=column,
        reason=reason if not fields["options"] else None,
    )


# ----------------------------------------------------------------------
# provenance stripping for the sent snapshot


_STRIP_TOP = (
    "stop_reference_price",
    "last_bar_close",
    "last_bar_time",
    "days_to_earnings",
    "signal_bar_time",
    "news_status",
)
_STRIP_QUOTE = (
    "last",
    "trade_timestamp",
    "quote_timestamp",
    "feed",
    "fetched_at",
    "last_bar_time",
    "signal_bar_time",
)


def v3_send_snapshot(stored: dict[str, Any]) -> dict[str, Any]:
    """The snapshot as the model sees it: the v3 fields without the
    provenance the decision still stores (docs/SPEC.md, Snapshot v3 —
    No duplicates)."""
    import copy

    out = copy.deepcopy(stored)
    for key in _STRIP_TOP:
        out.pop(key, None)
    if isinstance(out.get("quote"), dict):
        for key in _STRIP_QUOTE:
            out["quote"].pop(key, None)
    return out
