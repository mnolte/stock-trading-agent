"""Code-built entry and held-stop options for decision.v2.

The model never writes a price: code builds every stop and target
combination that meets the rules and the model picks an option id or
skips (docs/SPEC.md, Decision v2). A strategy's check overrides apply
through its overlaid config.

"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from app.data.models import Bar1h
from app.decide.levels import (
    CENT,
    LEVEL_KINDS_HIGH,
    LEVEL_KINDS_LOW,
    Level,
    OptionsConfig,
    _swings,
)

if TYPE_CHECKING:
    pass


# ----------------------------------------------------------------------
# entry options


@dataclass(frozen=True)
class _Stop:
    price: Decimal
    level: Level | None  # level-based wins a merge with a plain ATR stop
    basis: str

    @property
    def is_level(self) -> bool:
        return self.level is not None


@dataclass(frozen=True)
class _Target:
    price: Decimal
    level: Level | None
    basis: str

    @property
    def is_level(self) -> bool:
        return self.level is not None


@dataclass(frozen=True)
class EntryOption:
    """One offered entry: fixed stop and target the model picks by id."""

    id: str  # A–F
    stop: Decimal
    stop_basis: str  # e.g. "0.1 ATR below L8 · 1h swing low 13 Oct 14:30"
    stop_atr: Decimal  # stop distance in ATR from the reference
    target: Decimal
    target_basis: str  # e.g. "0.1 ATR below L1 · October high" | "2R"
    rr: Decimal
    risk: Decimal  # per share: entry limit - stop
    levels_in_way: list[str]
    stop_level: str | None
    target_level: str | None


@dataclass(frozen=True)
class EntryOptions:
    """The builder's result: options plus display notes and a no-options
    reason for the ``no_call`` row."""

    options: list[EntryOption]
    # Per-level display notes, e.g. "stop 409.64 · 2.73 ATR" or
    # "too close for a stop · 1.04 ATR" (the drawer's level list).
    level_notes: dict[str, str] = field(default_factory=dict)
    merged: list[str] = field(default_factory=list)  # "2R from … into A"
    reason: str | None = None  # why no options exist, when empty


def _basis_text(buffer_atr: Decimal, level: Level) -> str:
    when = (
        f"{level.time.day} {_MONTHS[level.time.month - 1]}"
        + (f" {level.time:%H:%M}" if level.time.hour or level.time.minute else "")
        if level.time is not None
        else ""
    )
    kind_text = {
        "swing_low": f"{level.timeframe} swing low",
        "swing_high": f"{level.timeframe} swing high",
        "prior_session_high": "prior session high",
        "prior_session_low": "prior session low",
        "prior_session_close": "prior session close",
        "session_high": "session high",
        "session_low": "session low",
        "vwap": "session VWAP",
    }[level.kind]
    return (
        f"{buffer_atr.normalize()} ATR below {level.id} · {kind_text}"
        + (f" {when}" if when else "")
    )


_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _merge_stops(
    candidates: list[_Stop], within: Decimal
) -> tuple[list[_Stop], list[str]]:
    """Merge stops closer than ``within``; a level-based stop wins."""
    kept: list[_Stop] = []
    merged: list[str] = []
    for stop in sorted(candidates, key=lambda s: s.price, reverse=True):
        hit = next(
            (k for k in kept if abs(k.price - stop.price) <= within), None
        )
        if hit is None:
            kept.append(stop)
        elif stop.is_level and not hit.is_level:
            kept[kept.index(hit)] = stop
            merged.append(
                f"{hit.basis} ({_money(hit.price)}) into {stop.basis}"
            )
        else:
            merged.append(f"{stop.basis} ({_money(stop.price)}) into {hit.basis}")
    return kept, merged


def _merge_targets(
    candidates: list[_Target], within: Decimal
) -> tuple[list[_Target], list[str]]:
    kept: list[_Target] = []
    merged: list[str] = []
    for target in sorted(candidates, key=lambda t: t.price):
        hit = next(
            (k for k in kept if abs(k.price - target.price) <= within), None
        )
        if hit is None:
            kept.append(target)
        elif target.is_level and not hit.is_level:
            kept[kept.index(hit)] = target
            merged.append(
                f"{hit.basis} ({_money(hit.price)}) into {target.basis}"
            )
        else:
            merged.append(
                f"{target.basis} ({_money(target.price)}) into {hit.basis}"
            )
    return kept, merged


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _fmt_atr(value: Decimal) -> str:
    return f"{value:.2f} ATR"


def build_entry_options(
    *,
    levels: Sequence[Level],
    price: Decimal,
    reference: Decimal,
    limit: Decimal,
    atr_1h: Decimal,
    cfg: OptionsConfig,
) -> EntryOptions:
    """The offered entry options for one candidate.

    ``reference`` is the stop-distance anchor (lowest of market price,
    plausible ask and last closed bar close); ``limit`` is the entry limit
    (market price + the offset); targets and R/R are measured from it.
    """
    band_lo = cfg.stop_atr_min * atr_1h
    band_hi = cfg.stop_atr_max * atr_1h
    level_notes: dict[str, str] = {}
    rejected: list[str] = []

    stops: list[_Stop] = []
    for level in levels:
        if level.kind not in LEVEL_KINDS_LOW:
            continue
        if level.price >= price:
            continue
        stop_price = level.price - cfg.level_buffer_atr * atr_1h
        distance = reference - stop_price
        dist_atr = distance / atr_1h
        if distance < band_lo or distance > band_hi:
            why = "too close" if distance < band_lo else "too far"
            level_notes[level.id] = (
                f"{why} for a stop · {_fmt_atr(dist_atr)}"
            )
            rejected.append(
                f"{level.id} ({level.kind.replace('_', ' ')}) "
                f"{_fmt_atr(dist_atr)} away"
            )
            continue
        level_notes[level.id] = (
            f"stop {_money(stop_price)} · {_fmt_atr(dist_atr)}"
        )
        stops.append(
            _Stop(stop_price, level, _basis_text(cfg.level_buffer_atr, level))
        )
    for k in cfg.atr_stop_multiples:
        atr_stop = reference - k * atr_1h
        distance = reference - atr_stop
        if band_lo <= distance <= band_hi:
            stops.append(_Stop(atr_stop, None, f"{k.normalize()} × ATR"))
        else:
            rejected.append(
                f"{k.normalize()} × ATR stop {_fmt_atr(k)} away — "
                "outside the band"
            )

    kept_stops, stop_merges = _merge_stops(stops, cfg.dedupe_atr * atr_1h)
    kept_stops = kept_stops[: cfg.max_stops]

    options: list[EntryOption] = []
    merged = list(stop_merges)
    target_rejected = False
    for stop in kept_stops:
        risk = limit - stop.price
        if risk <= 0:
            continue
        targets: list[_Target] = []
        for level in levels:
            if level.kind not in LEVEL_KINDS_HIGH or level.price <= limit:
                continue
            lvl_target = level.price - cfg.level_buffer_atr * atr_1h
            rr = (lvl_target - limit) / risk
            if rr < cfg.min_reward_risk:
                target_rejected = True
                continue
            targets.append(
                _Target(lvl_target, level, _basis_text(cfg.level_buffer_atr, level))
            )
        for r in cfg.target_r_multiples:
            r_target = limit + r * risk
            rr = (r_target - limit) / risk
            if rr < cfg.min_reward_risk:
                target_rejected = True
                continue
            targets.append(_Target(r_target, None, f"{r.normalize()}R"))
        kept_targets, target_merges = _merge_targets(
            targets, cfg.dedupe_atr * atr_1h
        )
        merged += target_merges
        kept_targets.sort(key=lambda t: (t.price - limit) / risk)
        for target in kept_targets:
            rr = (target.price - limit) / risk
            in_way = [
                level.id
                for level in levels
                if limit < level.price < target.price
            ]
            options.append(
                EntryOption(
                    id="",
                    stop=stop.price,
                    stop_basis=stop.basis,
                    stop_atr=(reference - stop.price) / atr_1h,
                    target=target.price,
                    target_basis=target.basis,
                    rr=rr,
                    risk=risk,
                    levels_in_way=in_way,
                    stop_level=stop.level.id if stop.level else None,
                    target_level=(
                        target.level.id if target.level else None
                    ),
                )
            )

    # By stop, nearest first, then by reward to risk (docs/SPEC.md).
    options.sort(key=lambda o: (-o.stop, o.rr))
    ordered: list[EntryOption] = []
    for letter, option in zip(
        "ABCDEF", options[: cfg.max_options], strict=False
    ):
        ordered.append(replace(option, id=letter))

    if ordered:
        return EntryOptions(options=ordered, level_notes=level_notes, merged=merged)

    reason_parts = rejected[:3]
    if target_rejected or kept_stops:
        reason_parts.append(f"no target with rr ≥ {cfg.min_reward_risk.normalize()}")
    if not reason_parts:
        reason_parts.append("no levels below the price for a stop")
    return EntryOptions(
        options=[], level_notes=level_notes, merged=merged,
        reason="; ".join(reason_parts),
    )


# ----------------------------------------------------------------------
# held stop options


@dataclass(frozen=True)
class StopOption:
    """One offered new stop for a held position (T1–T3)."""

    id: str
    stop: Decimal
    basis: str  # "below L4 · 1h swing low …" | "break-even" | "2.0 × ATR trail"
    locks_profit: bool
    r_at_stop: Decimal | None  # R at that stop against the initial risk
    level: str | None = None


def build_stop_options(
    *,
    bars_1h: Sequence[Bar1h],
    entry: Decimal | None,
    opened_at: datetime | None,
    current_stop: Decimal | None,
    initial_stop: Decimal | None,
    reference: Decimal,
    atr_1h: Decimal,
    cfg: OptionsConfig,
) -> list[StopOption]:
    """T1–T3 for a held position, each above the current stop and at least
    ``tighten_min_atr_below_price`` × ATR below ``reference`` (the tighten
    rule: lowest of market price, plausible bid and bar close)."""
    margin = cfg.tighten_min_atr_below_price * atr_1h
    initial_risk = (
        entry - initial_stop
        if initial_stop is not None and entry is not None and entry > initial_stop
        else None
    )

    candidates: list[tuple[str, Decimal, str | None]] = []
    # The most recent confirmed 1h swing low above the current stop.
    _highs, lows = _swings(
        bars_1h, cfg.swing_strength, cfg.swing_lookback_bars_1h
    )
    swing = next(
        (
            s for s in reversed(lows) if current_stop is None or s.price > current_stop
        ),
        None,
    )
    if swing is not None:
        candidates.append(
            (
                f"{cfg.level_buffer_atr.normalize()} ATR below the "
                f"{swing.time.day} {_MONTHS[swing.time.month - 1]} "
                f"{swing.time:%H:%M} swing low",
                swing.price - cfg.level_buffer_atr * atr_1h,
                None,
            )
        )
    if entry is not None:
        candidates.append(("break-even", entry, None))
    since_entry = [b for b in bars_1h if opened_at is None or b.end > opened_at]
    if since_entry:
        highest_close = max(b.close for b in since_entry)
        candidates.append(
            (
                f"{cfg.held_trail_atr.normalize()} × ATR trail from "
                f"{_money(highest_close)}",
                highest_close - cfg.held_trail_atr * atr_1h,
                None,
            )
        )

    options: list[StopOption] = []
    for basis, stop, level in candidates:
        if (current_stop is not None and stop <= current_stop) or reference - stop < margin:
            continue
        options.append(
            StopOption(
                id=f"T{len(options) + 1}",
                stop=stop,
                basis=basis,
                locks_profit=entry is not None and stop >= entry,
                r_at_stop=(
                    (stop - entry) / initial_risk
                    if (
                        initial_risk is not None
                        and initial_risk > 0
                        and entry is not None
                    )
                    else None
                ),
                level=level,
            )
        )
        if len(options) >= 3:
            break
    return options


# ----------------------------------------------------------------------
# JSON for the snapshot and the stored columns


def option_json(option: EntryOption) -> dict[str, Any]:
    return {
        "id": option.id,
        "stop": float(option.stop),
        "stop_basis": option.stop_basis,
        "stop_atr": float(round(option.stop_atr, 2)),
        "stop_level": option.stop_level,
        "target": float(option.target),
        "target_basis": option.target_basis,
        "target_level": option.target_level,
        "rr": float(round(option.rr, 2)),
        "risk": float(round(option.risk, 4)),
        "levels_in_way": option.levels_in_way,
    }


def stop_option_json(option: StopOption) -> dict[str, Any]:
    return {
        "id": option.id,
        "stop": float(option.stop),
        "basis": option.basis,
        "locks_profit": option.locks_profit,
        "r_at_stop": (
            float(round(option.r_at_stop, 2))
            if option.r_at_stop is not None
            else None
        ),
        "level": option.level,
    }
