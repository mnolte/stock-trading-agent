"""Assemble decision.v2 snapshot fields: levels, constraints, options.

Shared by the live cycle, shadow runner, backtests and replay — the same
builder over the same bars at the decision time (docs/SPEC.md, Decision
v2). Everything here is pure Python over bar lists the caller already
has, cut at the decision time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.config import AppConfig
from app.data.models import Bar1h, Bar5m, CalendarDay, DailyBar
from app.decide.levels import OPTIONS_VERSION, find_levels, level_json, options_config
from app.decide.options import (
    build_entry_options,
    build_stop_options,
    option_json,
    stop_option_json,
)
from app.market.calendar import CalendarProvider

# Same call shape as the shadow runner BarReader (BarCache.get).
BarReader = Callable[[str, datetime, datetime], list[Bar5m]]

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class HeldContext:
    """The position facts build_stop_options needs."""

    entry: Decimal | None
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


def rebuild_bars(
    symbol: str,
    at: datetime,
    *,
    calendar: CalendarProvider,
    bars_reader: BarReader,
    sessions_needed: int = 62,
) -> tuple[list[Bar1h], list[DailyBar], list[Bar5m]]:
    """The builder's bar inputs from the 5-minute cache at time ``at``.

    Shadows, backtests and replays rebuild levels and options for a
    decision at its cycle time — never after (no lookahead). ``calendar``
    is a CalendarProvider; ``bars_reader`` is ``BarCache.get``.
    """
    from app.data.aggregate import aggregate_1h, bars_per_session
    from app.market.clock import NY_TZ

    today = at.astimezone(NY_TZ).date()
    sessions: list[CalendarDay] = []
    cursor = today
    while len(sessions) < sessions_needed:
        session = calendar.session_for(cursor)
        if session is not None:
            sessions.append(session)
        prev = calendar.previous_trading_day(cursor)
        if prev is None:
            break
        cursor = prev.date
    sessions.sort(key=lambda s: s.date)
    if not sessions:
        return [], [], []
    bars_5m = bars_reader(symbol, sessions[0].open, at)
    per_session = bars_per_session(bars_5m, sessions)
    bars_1h = [
        bar
        for session in sessions
        for bar in aggregate_1h(symbol, per_session[session.date], session)
        if bar.end <= at
    ]
    prior = [s for s in sessions if s.date < today]
    daily = [
        DailyBar(
            symbol=symbol,
            date=s.date,
            open=per_session[s.date][0].open,
            high=max(b.high for b in per_session[s.date]),
            low=min(b.low for b in per_session[s.date]),
            close=per_session[s.date][-1].close,
            volume=sum(b.volume for b in per_session[s.date]),
        )
        for s in prior
        if per_session.get(s.date) and s.close <= at
    ]
    today_5m = [
        b
        for b in per_session.get(today, [])
        if b.start + timedelta(minutes=5) <= at
    ]
    return bars_1h, daily, today_5m
