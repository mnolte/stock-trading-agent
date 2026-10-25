"""Assemble decision.v2 snapshot fields: levels, constraints, options.

Shared by the live cycle, shadow runner, backtests and replay — the same
builder over the same bars at the decision time (docs/SPEC.md, Decision
v2). Everything here is pure Python over bar lists the caller already
has, cut at the decision time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.config import AppConfig
from app.data.models import Bar1h, Bar5m, DailyBar
from app.decide.levels import OPTIONS_VERSION, find_levels, level_json, options_config
from app.decide.options import (
    build_entry_options,
    build_stop_options,
    option_json,
    stop_option_json,
)

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class HeldContext:
    """The position facts build_stop_options needs."""

    entry: Decimal
    current_stop: Decimal | None
    initial_stop: Decimal | None
    opened_at: Any  # datetime | None


@dataclass(frozen=True)
class V2Build:
    """Fields to merge into the model snapshot, plus the stored-column
    payload. ``reason`` is the no_options detail — set only for candidates
    when no entry option survives (held positions with no stop option
    still get called: hold and exit remain)."""

    fields: dict[str, Any]
    options_column: dict[str, Any]
    reason: str | None


def build_v2_fields(
    *,
    context: str,
    bars_1h: Sequence[Bar1h],
    daily: Sequence[DailyBar],
    today_5m: Sequence[Bar5m],
    price: Decimal | None,
    reference: Decimal | None,
    atr_1h: Decimal | None,
    config: AppConfig,
    held: HeldContext | None = None,
) -> V2Build:
    """Levels + options for one decision; ``price``/``reference`` are the
    market price and stop reference the cycle already computed (the
    reference is the lowest of price, plausible ask and bar close for
    entries; lowest of price, plausible bid and bar close for held)."""
    cfg = options_config(config)
    anchor = price if price is not None else reference
    levels = (
        find_levels(
            bars_1h=bars_1h,
            daily=daily,
            today_5m=today_5m,
            price=anchor,
            cfg=cfg,
        )
        if anchor is not None
        else []
    )
    base: dict[str, Any] = {"levels": [level_json(lv) for lv in levels]}
    column: dict[str, Any] = {"options_version": OPTIONS_VERSION}

    if context == "held":
        stop_opts = (
            build_stop_options(
                bars_1h=bars_1h,
                entry=held.entry,
                opened_at=held.opened_at,
                current_stop=held.current_stop,
                initial_stop=held.initial_stop,
                reference=reference,
                atr_1h=atr_1h,
                cfg=cfg,
            )
            if held is not None and reference is not None and atr_1h
            else []
        )
        base["constraints"] = {
            "atr_1h": float(atr_1h) if atr_1h is not None else None,
        }
        base["stop_options"] = [stop_option_json(o) for o in stop_opts]
        column["stop_options"] = base["stop_options"]
        return V2Build(fields=base, options_column=column, reason=None)

    offset = Decimal(
        str(config.get("execution", "entry_limit_offset_pct", default=0.05))
    )
    limit = (
        (price * (1 + offset / 100)).quantize(_CENT, rounding=ROUND_HALF_UP)
        if price is not None
        else None
    )
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
    base["constraints"] = {
        "atr_1h": float(atr_1h) if atr_1h is not None else None,
        "entry_limit": float(limit) if limit is not None else None,
        "stop_atr_min": float(cfg.stop_atr_min),
        "stop_atr_max": float(cfg.stop_atr_max),
        "min_reward_risk": float(cfg.min_reward_risk),
    }
    base["options"] = [option_json(o) for o in options]
    column.update(
        {
            "options": base["options"],
            "merged": result.merged if result is not None else [],
            "level_notes": result.level_notes if result is not None else {},
        }
    )
    return V2Build(
        fields=base,
        options_column=column,
        reason=result.reason if result is not None and not options else None,
    )
