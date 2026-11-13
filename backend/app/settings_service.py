"""Versioned settings (docs/SPEC.md, Settings).

The GUI edits a fixed set of keys. A save validates, stores the full
settings tree as a new ``settings_versions`` row and marks it pending on
the config; the pending version is promoted at the start of the next
decision cycle. Anything not listed here stays read-only in the API.
"""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.cache import _naive_utc
from app.decide.prompt import (
    PromptError,
    UnknownPromptVersion,
    load_prompt,
    prompt_spec,
)
from app.errors import safe_error
from app.market.clock import NY_TZ, SessionClock
from app.notifications.events import EVENT_DEFS, EVENTS

if TYPE_CHECKING:
    from app.broker.base import Broker
    from app.config import AppConfig
    from app.db.models import SettingsVersion
    from app.market.calendar import CalendarProvider
    from app.market.clock import Clock

SYMBOL_RE = re.compile(r"^[A-Z]{1,6}(\.[A-Z])?$")

# section -> key -> (kind, min, max). kind: number | int | bool | mode | str
EDITABLE: dict[str, dict[str, tuple[str, float | None, float | None]]] = {
    "execution": {
        "mode": ("mode", None, None),
        "entry_limit_offset_pct": ("number", 0, 10),
        "entry_timeout_minutes": ("int", 1, 240),
        "max_spread_pct": ("number", 0, 10),
        # Trading → Prices and timeouts (docs/SPEC.md, GUI — Settings).
        "quote_max_age_seconds": ("int", 1, 3600),
        "quote_max_deviation_pct": ("number", 0, 100),
        "tighten_quote_max_age_seconds": ("int", 1, 86400),
        "fill_settle_seconds": ("int", 0, 3600),
    },
    "rules": {
        "rsi_min": ("number", 0, 100),
        "rsi_max": ("number", 0, 100),
    },
    "decision": {
        "model": ("str", None, None),
        "temperature": ("number", 0, 2),
        "prompt": ("str", None, None),
        "min_confidence_enter": ("number", 0, 1),
        "stop_atr_min": ("number", 0, 20),
        "stop_atr_max": ("number", 0, 20),
        "min_reward_risk": ("number", 0, 20),
        "max_calls_per_cycle": ("int", 1, 100),
        # Decision gates (docs/SPEC.md, GUI — Settings → Decision gates).
        "entry_gate": ("entry_gate", None, None),
        "min_ev_r": ("number", 0, 20),
        "v3": ("submap", None, None),
        "v4": ("submap", None, None),
        "v5": ("submap", None, None),
        "v6": ("submap", None, None),
    },
    "gui": {
        "near_stop_atr": ("number", 0, 20),
    },
    "inspector": {
        "slow_call_seconds": ("int", 0, 3600),
    },
    "api": {
        "broker_timeout_seconds": ("int", 1, 120),
    },
    "selection": {
        "mode": ("selection_mode", None, None),
        "confidence_weight": ("number", 0, 1),
        "last_slot_min_ev_r": ("null_or_positive", None, None),
    },
    "risk": {
        "risk_per_trade_pct": ("number", 0, 5),
        "max_position_pct": ("number", 0, 100),
        "max_open_risk_pct": ("number", 0, 100),
        "max_open_positions": ("int", 1, 50),
        "max_per_sector": ("int", 1, 20),
        "daily_loss_limit_pct": ("number", 0, 100),
        "losing_streak": ("int", 0, 20),
        "shock_brake_1h_pct": ("number", 0, 100),
        "shock_brake_session_pct": ("number", 0, 100),
        "earnings_blackout_days": ("int", 0, 30),
        "hold_through_earnings": ("bool", None, None),
        "drawdown_kill_pct": ("number", 0, 100),
        "max_order_pct": ("number", 0, 100),
        "max_orders_per_cycle": ("int", 1, 50),
    },
    "performance": {
        "slippage_pct_per_side": ("number", 0, 10),
    },
    "reviews": {
        "briefing_enabled": ("bool", None, None),
        "monthly_cost_cap_usd": ("number", 0, 100),
        "models": ("review_models", None, None),
        "prompts": ("review_prompts", None, None),
    },
    "schedule": {
        # Each entry "HH:MM" New York; the list itself is validated below.
        "cycles": ("cycles", None, None),
    },
    "events": {
        # Economic releases (item 067): the FOMC window switch and its
        # minute fields. Dates, times and FRED release ids stay in the
        # config file — the form echoes them read-only.
        "fomc": ("submap", None, None),
    },
    "auth": {
        "idle_hours": ("int", 1, 168),
    },
    # ``ops`` is gone on purpose (item 081): the heartbeat URL is a
    # write-only secret saved through /api/server/heartbeat, never a
    # settings value — it must not land in a settings version.
    "notifications": {
        "repeat_limit_minutes": ("int", 0, 1440),
        "quiet_hours": ("quiet_hours", None, None),
        "events": ("notify_events", None, None),
    },
    "ask": {
        "enabled": ("bool", None, None),
        "model": ("str", None, None),
        "monthly_cost_cap_usd": ("number", 0, 1000),
        "max_cost_per_answer_usd": ("number", 0, 100),
        "keep_days": ("int", 1, 3650),
        "instructions": ("instructions", None, None),
    },
    "readiness": {
        # Thresholds only: the zero-tolerance criteria and the manual
        # items are fixed by the spec (docs/SPEC.md, Readiness).
        "min_closed_trades": ("int", 1, 10000),
        "min_sessions": ("int", 1, 1000),
        "max_drawdown_pct": ("number", 0, 100),
        "min_avg_r": ("number", -10, 10),
        "min_profit_factor": ("number", 0, 100),
        "ratio_min_trades": ("int", 1, 1000),
        "min_proposal_pass_pct": ("number", 0, 100),
        "max_calibration_gap_pts": ("number", 0, 100),
        "min_cycles_completed_pct": ("number", 0, 100),
        "max_price_blocked_pct": ("number", 0, 100),
        "error_auto_resolve_minutes": ("int", 1, 1440),
    },
}

# Per-sub-key specs for the "submap" kind — nested settings groups like
# decision.v3/v4/v5 (docs/SPEC.md, GUI — Settings → Decision gates).
SUBSPECS: dict[tuple[str, str], dict[str, tuple[str, float | None, float | None]]] = {
    ("decision", "v3"): {
        "min_room_atr": ("number", 0, 20),
        "min_edge": ("number", 0, 1),
    },
    ("decision", "v4"): {
        "near_level_atr": ("number", 0, 20),
    },
    ("decision", "v5"): {
        "trend_gate": ("bool", None, None),
        "trend_gate_regimes": ("strlist", None, None),
        "max_rr": ("number", 0, 20),
        "base_rate_sessions": ("int", 0, 260),
        "price_vs_signal_adverse_atr": ("number", 0, 20),
    },
    ("decision", "v6"): {
        "sideways_max_target_atr": ("number", 0, 20),
    },
    ("events", "fomc"): {
        "enabled": ("bool", None, None),
        "block_minutes_before": ("int", 0, 240),
        "block_minutes_after": ("int", 0, 240),
    },
}

# Members a "strlist" submap value may contain, keyed (section, key, sub).
# ``down`` is deliberately absent: the trend gate never passes it
# (docs/SPEC.md, Decision v5 — Trend gate in code).
SUBSPEC_LIST_VALUES: dict[tuple[str, str, str], tuple[str, ...]] = {
    ("decision", "v5", "trend_gate_regimes"): ("up", "sideways"),
}

# Channels the user may pick per event (``app`` is always on).
NOTIFY_CHANNELS = {"browser", "email"}

# Keys that may appear inside ``universe`` in a save body.
UNIVERSE_KEYS = {"symbols", "sectors", "mode", "screen"}

# Editable liquidity-screen thresholds (docs/SPEC.md, Larger universe).
SCREEN_KEYS: dict[str, tuple[str, float | None, float | None]] = {
    "min_price": ("number", 0, None),
    "min_avg_dollar_volume": ("number", 0, None),
    "lookback_sessions": ("int", 1, 260),
    "drop_after_failed_screens": ("int", 1, 20),
}

SCREEN_LABELS: dict[str, str] = {
    "min_price": "Min price",
    "min_avg_dollar_volume": "Min avg dollar volume",
    "lookback_sessions": "Screen lookback",
    "drop_after_failed_screens": "Drops after",
}

# universe keys the form echoes but never edits — tolerated when unchanged.
UNIVERSE_READ_ONLY = {"rank_by", "watchlist_rows", "max_pool", "pool_file"}

LABELS: dict[tuple[str, str], str] = {
    ("execution", "mode"): "Execution mode",
    ("execution", "entry_limit_offset_pct"): "Entry limit above ask",
    ("execution", "entry_timeout_minutes"): "Cancel unfilled entry after",
    ("execution", "max_spread_pct"): "Max spread",
    ("rules", "rsi_min"): "RSI from",
    ("rules", "rsi_max"): "RSI to",
    ("decision", "model"): "Model",
    ("decision", "temperature"): "Temperature",
    ("decision", "prompt"): "Prompt version",
    ("decision", "min_confidence_enter"): "Min confidence to enter",
    ("decision", "stop_atr_min"): "Stop distance from",
    ("decision", "stop_atr_max"): "Stop distance to",
    ("decision", "min_reward_risk"): "Min reward to risk",
    ("decision", "max_calls_per_cycle"): "Max model calls per cycle",
    ("decision", "entry_gate"): "Entry gate",
    ("decision", "min_ev_r"): "Min expected R (ev gate)",
    ("decision", "v3.min_room_atr"): "v3 room to first level",
    ("decision", "v3.min_edge"): "v3 min edge over break-even",
    ("decision", "v4.near_level_atr"): "v4 near-a-level distance",
    ("decision", "v5.trend_gate"): "v5 trend gate",
    ("decision", "v5.trend_gate_regimes"): "v5 trend gate regimes",
    ("decision", "v5.max_rr"): "v5 max reward to risk",
    ("decision", "v5.base_rate_sessions"): "v5 base rate window",
    ("decision", "v5.price_vs_signal_adverse_atr"): "v5 adverse price move",
    ("decision", "v6.sideways_max_target_atr"): "v6 sideways high-volatility target",
    ("gui", "near_stop_atr"): "Near-stop warning",
    ("inspector", "slow_call_seconds"): "Slow call",
    ("api", "broker_timeout_seconds"): "Broker answer timeout",
    ("execution", "quote_max_age_seconds"): "Quote max age",
    ("execution", "quote_max_deviation_pct"): "Quote max deviation",
    ("execution", "tighten_quote_max_age_seconds"): "Tighten quote max age",
    ("execution", "fill_settle_seconds"): "Fill settle window",
    ("selection", "mode"): "Entry selection",
    ("selection", "confidence_weight"): "Confidence weight",
    ("selection", "last_slot_min_ev_r"): "Last slot min expected R",
    ("risk", "risk_per_trade_pct"): "Risk per trade",
    ("risk", "max_position_pct"): "Max position size",
    ("risk", "max_open_risk_pct"): "Total open risk",
    ("risk", "max_open_positions"): "Max open positions",
    ("risk", "max_per_sector"): "Max positions per sector",
    ("risk", "daily_loss_limit_pct"): "Daily loss limit",
    ("risk", "losing_streak"): "Losing streak",
    ("risk", "shock_brake_1h_pct"): "Shock brake, last 1h",
    ("risk", "shock_brake_session_pct"): "Shock brake, since open",
    ("risk", "earnings_blackout_days"): "Blackout before earnings",
    ("risk", "hold_through_earnings"): "Hold through earnings",
    ("risk", "drawdown_kill_pct"): "Drawdown kill",
    ("risk", "max_order_pct"): "Max single order",
    ("risk", "max_orders_per_cycle"): "Max orders per cycle",
    ("performance", "slippage_pct_per_side"): "Assumed slippage",
    ("reviews", "briefing_enabled"): "Daily briefing",
    ("reviews", "models.weekly"): "Weekly review model",
    ("reviews", "models.briefing"): "Daily briefing model",
    ("reviews", "models.verdict"): "Verdict model",
    ("reviews", "prompts.weekly"): "Weekly review prompt",
    ("reviews", "prompts.briefing"): "Daily briefing prompt",
    ("reviews", "prompts.verdict"): "Verdict prompt",
    ("reviews", "monthly_cost_cap_usd"): "Review cost cap",
    ("schedule", "cycles"): "Cycle times",
    ("events", ""): "Economic releases",
    ("events", "fomc.enabled"): "FOMC window",
    ("events", "fomc.block_minutes_before"): "FOMC window blocks from",
    ("events", "fomc.block_minutes_after"): "FOMC window blocks until",
    ("notifications", "repeat_limit_minutes"): "Repeat limit",
    ("notifications", "quiet_hours"): "Quiet hours",
    ("notifications", "quiet_hours.start"): "Quiet hours from",
    ("notifications", "quiet_hours.end"): "Quiet hours to",
    ("notifications", "events"): "Notification channels",
    ("auth", "idle_hours"): "Sign out after inactivity",
    ("ops", "heartbeat_url"): "Heartbeat URL",
    ("ask", "enabled"): "Ask the agent",
    ("ask", "model"): "Ask model",
    ("ask", "monthly_cost_cap_usd"): "Ask monthly cap",
    ("ask", "max_cost_per_answer_usd"): "Ask max cost per answer",
    ("ask", "keep_days"): "Ask chats kept for",
    ("ask", "instructions"): "Ask standing instructions",
    ("readiness", "min_closed_trades"): "Readiness closed trades",
    ("readiness", "min_sessions"): "Readiness paper sessions",
    ("readiness", "max_drawdown_pct"): "Readiness max drawdown",
    ("readiness", "min_avg_r"): "Readiness average R",
    ("readiness", "min_profit_factor"): "Readiness profit factor",
    ("readiness", "ratio_min_trades"): "Readiness minimum trades for ratios",
    ("readiness", "min_proposal_pass_pct"): "Readiness proposals passing",
    ("readiness", "max_calibration_gap_pts"): "Readiness calibration gap",
    ("readiness", "min_cycles_completed_pct"): "Readiness cycles completed",
    ("readiness", "max_price_blocked_pct"): "Readiness price checks blocked",
    ("readiness", "error_auto_resolve_minutes"): "Errors auto-resolve after",
    ("universe", "symbols"): "Universe",
    ("universe", "mode"): "Universe mode",
    ("universe", "screen.min_price"): "Min price",
    ("universe", "screen.min_avg_dollar_volume"): "Min avg dollar volume",
    ("universe", "screen.lookback_sessions"): "Screen lookback",
    ("universe", "screen.drop_after_failed_screens"): "Drops after",
}
LABELS.update(
    {("notifications", f"events.{d.key}"): d.label for d in EVENT_DEFS}
)


# ----------------------------------------------------------------------
# Which way is looser (item 082, docs/SPEC.md — Every loosening asks
# "Confirm it is you"). One entry per leaf setting in the trading
# sections; the settings API serves the map and ``classify`` turns a
# save's diff into tighter / looser / neutral per change. A key in a
# covered section with no entry — and no covering ancestor — counts as
# looser (fails closed); ``neutral`` is a deliberate opt-out with a
# comment saying why.

Direction = str  # "up" | "down" | "on" | "off" | "wider" | "longer" | "shorter" | "neutral"

DIRECTION_SECTIONS = (
    "risk",
    "execution",
    "rules",
    "regime",
    "events",
    "decision",
    "selection",
    "schedule",
    "universe",
)

DIRECTIONS: dict[str, Direction] = {
    # risk — every raised cap or loosened brake is looser.
    "risk.risk_per_trade_pct": "up",
    "risk.max_position_pct": "up",
    "risk.max_open_risk_pct": "up",
    "risk.max_open_positions": "up",
    "risk.max_per_sector": "up",
    "risk.cash_only": "off",  # off allows margin-style exposure
    "risk.daily_loss_limit_pct": "up",
    "risk.losing_streak": "up",  # more losses before sizing halves
    "risk.losing_streak_risk_factor": "up",  # less reduction per loss
    "risk.shock_brake_1h_pct": "up",  # the brake trips later
    "risk.shock_brake_session_pct": "up",
    "risk.earnings_blackout_days": "down",
    "risk.hold_through_earnings": "on",
    "risk.reentry_same_session": "off",  # on blocks same-session re-entry
    "risk.drawdown_kill_pct": "up",  # the kill switch arms later
    "risk.adopted_stop_atr": "up",  # wider protective stop on adoption
    "risk.protective_stop_fallback_pct": "up",
    "risk.max_order_pct": "up",
    "risk.max_orders_per_cycle": "up",
    "risk.failed_cycles_kill": "up",  # more failures before the kill
    # execution — a wider envelope or a later alert is looser.
    "execution.mode": "neutral",  # supervised → automatic is its own rule
    "execution.entry_limit_offset_pct": "up",
    "execution.entry_timeout_minutes": "up",
    "execution.entry_cutoff": "up",  # HH:MM compares as text; later = more entries
    "execution.pending_exit_failures_alert": "up",  # alerts later = quieter
    "execution.stream_update_grace_seconds": "up",  # degraded marks later
    "execution.stream_degraded_minutes": "down",  # banner clears sooner = quieter
    "execution.fill_settle_seconds": "up",  # qty checks skipped longer
    "execution.exit_in_flight_seconds": "up",  # sells count as protection longer
    "execution.max_spread_pct": "up",
    "execution.quote_max_deviation_pct": "up",
    "execution.quote_max_age_seconds": "up",
    "execution.tighten_quote_max_age_seconds": "up",
    "execution.time_in_force": "neutral",  # order enum, no looser value
    # rules — a narrower RSI band or a dropped filter is looser.
    "rules.rsi_min": "down",
    "rules.rsi_max": "up",
    "rules.require_daily_above_sma50": "off",
    "rules.require_ema20_above_ema50": "off",
    # regime — the block list shrinks = looser; the vol boundaries label
    # regimes and which side is safer depends on the strategy.
    "regime.vol_low_below_pct": "neutral",
    "regime.vol_high_above_pct": "neutral",
    "regime.block_entries_in": "shorter",
    # events — a shorter FOMC window or fewer blocked dates is looser;
    # release ids, times and fetch horizons don't move risk.
    "events.fomc.enabled": "off",
    "events.fomc.time": "neutral",
    "events.fomc.block_minutes_before": "down",
    "events.fomc.block_minutes_after": "down",
    "events.fomc.dates": "shorter",
    "events.fred_releases.cpi": "neutral",
    "events.fred_releases.jobs": "neutral",
    "events.fred_releases.pce": "neutral",
    "events.release_time": "neutral",
    "events.lookahead_days": "neutral",
    "events.next_release_sessions": "neutral",
    # decision — gates: a lower bar, a wider stop band or a shorter
    # regime list is looser. Model and prompt changes are their own rule.
    "decision.model": "neutral",  # a live model change asks on its own
    "decision.temperature": "up",  # more randomness, less disciplined
    "decision.prompt": "neutral",  # a live prompt change asks on its own
    "decision.min_confidence_enter": "down",
    "decision.entry_gate": "neutral",  # floor vs ev: neither is strictly looser
    "decision.min_ev_r": "down",
    "decision.stop_atr_min": "down",
    "decision.stop_atr_max": "up",
    "decision.min_reward_risk": "down",
    "decision.tighten_min_atr_below_price": "neutral",  # stop-placement floor, both ways arguable
    "decision.breakeven_min_r": "up",  # break-even comes later = wider stops longer
    "decision.max_calls_per_cycle": "neutral",  # cost cap, not a risk dial
    "decision.call_timeout_seconds": "neutral",
    "decision.max_output_tokens": "neutral",
    "decision.options.swing_strength": "neutral",
    "decision.options.swing_lookback_bars_1h": "neutral",
    "decision.options.swing_lookback_sessions": "neutral",
    "decision.options.max_levels_each_side": "neutral",
    "decision.options.level_buffer_atr": "up",  # wider buffer = wider stops
    "decision.options.atr_stop_multiples": "neutral",  # offered sizes, the model picks
    "decision.options.target_r_multiples": "neutral",
    "decision.options.dedupe_atr": "neutral",
    "decision.options.max_stops": "neutral",
    "decision.options.max_options": "neutral",
    "decision.options.held_trail_atr": "up",  # wider trail = looser
    "decision.v3.min_room_atr": "down",
    "decision.v3.room_level_kinds": "longer",  # more kinds count as room
    "decision.v3.min_edge": "down",
    "decision.v3.min_stop_move_atr": "neutral",  # held-stop dedupe threshold
    "decision.v3.breakeven_min_r": "up",
    "decision.v3.held_stop_min_atr": "up",  # held stops sit further below price
    "decision.v3.level_merge_atr": "neutral",
    "decision.v4.max_headlines": "neutral",  # context size, not a gate
    "decision.v4.max_level_distance_atr": "neutral",
    "decision.v4.flip_min_move_atr": "neutral",
    "decision.v4.max_confidence_above_anchor": "up",  # the warning fires later
    "decision.v4.checks_gate": "off",  # on = code-enforced checklist
    "decision.v4.near_level_atr": "neutral",  # pinned by the prompt versions
    "decision.v5.exit_margin": "down",  # exits earlier = tighter
    "decision.v5.overnight_margin": "down",  # holds only from a higher neutral
    "decision.v5.gap_sessions": "neutral",  # stats window
    "decision.v5.long_target_minutes": "neutral",
    "decision.v5.trend_gate": "off",
    "decision.v5.trend_gate_regimes": "longer",
    "decision.v5.max_rr": "up",  # higher cap = looser options allowed
    "decision.v5.base_rate_sessions": "neutral",  # stats window
    "decision.v5.base_rate_min_n": "neutral",
    "decision.v5.price_vs_signal_adverse_atr": "up",
    "decision.v6.sideways_max_target_atr": "up",
    "decision.replay.confirm_above_usd": "neutral",  # replay-only threshold
    # selection — rank vs expected profit and the blend weight have no
    # single looser side; the last-slot bar does.
    "selection.mode": "neutral",
    "selection.confidence_weight": "neutral",
    "selection.last_slot_min_ev_r": "down",  # null = off = loosest
    # schedule — more cycles or fewer checks is looser; data windows and
    # display knobs are neutral.
    "schedule.market_timezone": "neutral",  # the exchange clock's zone
    "schedule.cycles": "longer",
    "schedule.last_cycle_no_entries": "off",
    "schedule.position_checks": "shorter",
    "schedule.position_check_failures_alert": "up",  # alerts later = quieter
    "schedule.calendar_refresh": "neutral",
    "schedule.misfire_grace_seconds": "neutral",
    "schedule.history_sessions": "neutral",
    "schedule.daily_history_sessions": "neutral",
    "schedule.history_missing_tolerance": "neutral",
    "schedule.bar_adjustment": "neutral",
    "schedule.bars_5m_keep_sessions": "neutral",
    "schedule.cycle_duration_warn_minutes": "up",  # warns later = quieter
    # universe — a bigger or wider pool is looser; names, the rank and
    # the display row count are neutral. mode has its own rule
    # (fixed → screened asks).
    "universe.symbols": "longer",
    "universe.names": "neutral",  # display names keyed by ticker
    "universe.sectors": "neutral",  # sector labels keyed by ticker
    "universe.mode": "neutral",  # fixed → screened asks on its own
    "universe.pool_file": "neutral",
    "universe.max_pool": "up",
    "universe.screen.time_ny": "neutral",
    "universe.screen.min_price": "down",
    "universe.screen.min_avg_dollar_volume": "down",
    "universe.screen.lookback_sessions": "neutral",
    "universe.screen.drop_after_failed_screens": "up",  # stays in pool longer
    "universe.rank_by": "neutral",
    "universe.watchlist_rows": "neutral",
    "universe.allowed_sectors": "longer",
}


def _direction_for(path: str) -> Direction | None:
    """The direction entry for a dotted leaf path: exact match first,
    then the nearest covering ancestor (``universe.names.SPY`` reads
    ``universe.names``). ``None`` = no entry — the caller fails closed."""
    node = path
    while node:
        found = DIRECTIONS.get(node)
        if found is not None:
            return found
        node = node.rpartition(".")[0]
    return None


def _flatten_leaves(tree: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """``{"a.b.c": value}`` for every non-dict leaf; dicts recurse."""
    out: dict[str, Any] = {}
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten_leaves(value, path))
        else:
            out[path] = value
    return out


def _order_key(value: Any) -> tuple[int, float | str]:
    """Total order for up/down compares: null sorts lowest, numbers
    numerically, everything else as text (``HH:MM`` times compare right)."""
    if value is None:
        return (0, 0.0)
    if isinstance(value, bool):
        return (1, float(value))
    try:
        return (1, float(value))
    except (TypeError, ValueError):
        return (2, str(value))


def _set_members(value: Any) -> set[Any] | None:
    if isinstance(value, (list, tuple, set, frozenset)):
        try:
            return set(value)
        except TypeError:
            return None
    return None


def _classify_change(kind: Direction, old: Any, new: Any) -> str:
    """``"looser" | "tighter" | "neutral"`` for one changed leaf."""
    if kind == "neutral":
        return "neutral"
    if kind in ("on", "off"):
        was_on = bool(old)
        is_on = bool(new)
        if was_on == is_on:
            return "neutral"
        return "looser" if is_on == (kind == "on") else "tighter"
    if kind in ("up", "down"):
        o, n = _order_key(old), _order_key(new)
        if o == n:
            return "neutral"
        rose = o < n
        return "looser" if rose == (kind == "up") else "tighter"
    if kind in ("longer", "shorter"):
        old_set = _set_members(old)
        new_set = _set_members(new)
        if old_set is None or new_set is None:
            return "neutral"
        added, removed = new_set - old_set, old_set - new_set
        grew = bool(added) and not removed
        shrank = bool(removed) and not added
        if kind == "longer":
            if grew:
                return "looser"
            if shrank:
                return "tighter"
        else:
            if shrank:
                return "looser"
            if grew:
                return "tighter"
        return "neutral"
    if kind == "wider":
        # A [low, high] pair (or {"min","max"}): wider is looser.
        pair_old = _range_pair(old)
        pair_new = _range_pair(new)
        if pair_old is None or pair_new is None:
            return "neutral"
        lo_old, hi_old = (_order_key(v) for v in pair_old)
        lo_new, hi_new = (_order_key(v) for v in pair_new)
        widened = lo_new < lo_old or hi_new > hi_old
        narrowed = lo_new > lo_old or hi_new < hi_old
        if widened and not narrowed:
            return "looser"
        if narrowed and not widened:
            return "tighter"
        return "neutral"
    return "neutral"


def _range_pair(value: Any) -> tuple[Any, Any] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    if isinstance(value, dict) and "min" in value and "max" in value:
        return value["min"], value["max"]
    return None


def classify(old_tree: dict[str, Any], new_tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Every changed leaf in the trading sections, tagged tighter /
    looser / neutral (item 082). A covered key without a DIRECTIONS
    entry counts as looser — the map fails closed."""
    out: list[dict[str, Any]] = []
    old_flat = _flatten_leaves(old_tree)
    new_flat = _flatten_leaves(new_tree)
    missing = object()
    for path in sorted(set(old_flat) | set(new_flat)):
        section = path.partition(".")[0]
        if section not in DIRECTION_SECTIONS:
            continue
        old = old_flat.get(path, missing)
        new = new_flat.get(path, missing)
        if old is missing:
            old = None
        if new is missing:
            new = None
        if old == new:
            continue
        kind = _direction_for(path)
        direction = (
            "looser"
            if kind is None
            else _classify_change(kind, old, new)
        )
        out.append({"key": path, "old": old, "new": new, "direction": direction})
    return out


def _field_label(path: str) -> str:
    section, _, rest = path.partition(".")
    return LABELS.get((section, rest)) or LABELS.get((section, "")) or path


def _fmt_field(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "—"
    return str(value)


def _confirm_entry(path: str, old: Any, new: Any) -> dict[str, str]:
    return {
        "key": path,
        "label": _field_label(path),
        "old": _fmt_field(old),
        "new": _fmt_field(new),
    }


def _quiet_hours_minutes(value: Any) -> int | None:
    """Quiet-hours span in minutes; overnight windows count past
    midnight. ``None`` when the shape isn't ``{start, end}``."""
    if not isinstance(value, dict):
        return None
    try:
        start = datetime.strptime(str(value["start"]), "%H:%M")  # noqa: DTZ007
        end = datetime.strptime(str(value["end"]), "%H:%M")  # noqa: DTZ007
    except (KeyError, ValueError):
        return None
    span = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return span % (24 * 60) or 24 * 60


def _quiet_hours_text(value: Any) -> str:
    if isinstance(value, dict):
        return f"{value.get('start', '?')}–{value.get('end', '?')}"
    return _fmt_field(value)


def confirm_fields(
    old_tree: dict[str, Any], new_tree: dict[str, Any]
) -> list[dict[str, str]]:
    """The changes a save must confirm (item 082): every looser leaf
    from ``classify`` plus the rules that sit outside the direction
    map — supervised → automatic, an alert event losing a channel,
    longer quiet hours, fixed → screened, and a live model or prompt
    change. Each entry is ``{key, label, old, new}`` for the 403
    ``confirm_required`` answer and the dialog's list."""
    fields = [
        _confirm_entry(c["key"], c["old"], c["new"])
        for c in classify(old_tree, new_tree)
        if c["direction"] == "looser"
    ]
    # execution.mode: only supervised → automatic asks.
    old_mode = _nested_get(old_tree, "execution", "mode")
    new_mode = _nested_get(new_tree, "execution", "mode")
    if old_mode == "supervised" and new_mode == "automatic":
        fields.append(_confirm_entry("execution.mode", old_mode, new_mode))
    # universe.mode: only fixed → screened asks.
    old_uni = _nested_get(old_tree, "universe", "mode")
    new_uni = _nested_get(new_tree, "universe", "mode")
    if old_uni == "fixed" and new_uni == "screened":
        fields.append(_confirm_entry("universe.mode", old_uni, new_uni))
    # A live decision model or prompt change alters every decision.
    for key in ("model", "prompt"):
        old_value = _nested_get(old_tree, "decision", key)
        new_value = _nested_get(new_tree, "decision", key)
        if old_value != new_value:
            fields.append(_confirm_entry(f"decision.{key}", old_value, new_value))
    # A notification event losing a channel, or quiet hours getting
    # longer, makes the agent quieter.
    old_events = _nested_get(old_tree, "notifications", "events") or {}
    new_events = _nested_get(new_tree, "notifications", "events") or {}
    if isinstance(old_events, dict) and isinstance(new_events, dict):
        for event in sorted(set(old_events) | set(new_events)):
            old_channels = old_events.get(event) or []
            new_channels = new_events.get(event) or []
            if set(old_channels) - set(new_channels):
                fields.append(
                    _confirm_entry(
                        f"notifications.events.{event}", old_channels, new_channels
                    )
                )
    old_quiet = _nested_get(old_tree, "notifications", "quiet_hours")
    new_quiet = _nested_get(new_tree, "notifications", "quiet_hours")
    if old_quiet != new_quiet:
        old_minutes = _quiet_hours_minutes(old_quiet)
        new_minutes = _quiet_hours_minutes(new_quiet)
        if (
            old_minutes is not None
            and new_minutes is not None
            and new_minutes > old_minutes
        ):
            fields.append(
                _confirm_entry(
                    "notifications.quiet_hours",
                    _quiet_hours_text(old_quiet),
                    _quiet_hours_text(new_quiet),
                )
            )
    return fields


class SettingsError(Exception):
    """Validation failed; ``errors`` are shown on the settings form."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _clean_number(
    value: object, minimum: float | None, maximum: float | None, label: str
) -> float:
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SettingsError([f"{label}: not a number"]) from None
    if minimum is not None and num < minimum:
        raise SettingsError([f"{label}: must be at least {minimum:g}"])
    if maximum is not None and num > maximum:
        raise SettingsError([f"{label}: must be at most {maximum:g}"])
    return num


def _clean_cycle_list(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SettingsError(["Cycle times: a non-empty list of HH:MM times"])
    result: list[str] = []
    for item in value:
        text = str(item)
        try:
            parsed = datetime.strptime(text, "%H:%M")  # noqa: DTZ007
        except ValueError:
            raise SettingsError(
                [f"Cycle times: {text!r} is not an HH:MM time"]
            ) from None
        result.append(parsed.strftime("%H:%M"))
    return sorted(set(result))


def _clean_quiet_hours(value: object) -> dict[str, str]:
    """``{"start": "HH:MM", "end": "HH:MM"}`` in Amsterdam time."""
    if not isinstance(value, dict):
        raise SettingsError(["Quiet hours: start and end times are required"])
    out: dict[str, str] = {}
    for name in ("start", "end"):
        text = str(value.get(name, ""))
        try:
            parsed = datetime.strptime(text, "%H:%M")  # noqa: DTZ007
        except ValueError:
            raise SettingsError(
                [f"Quiet hours {name}: {text!r} is not an HH:MM time"]
            ) from None
        out[name] = parsed.strftime("%H:%M")
    return out


def _clean_notify_events(value: object) -> dict[str, list[str]]:
    """``{event: [browser, email]}`` — unknown events/channels rejected.

    ``app`` is not a valid value here (it is always on); critical events
    may list fewer channels — the service still forces the browser
    channel for them at send time.
    """
    if not isinstance(value, dict):
        raise SettingsError(["Notification channels: expected an event map"])
    out: dict[str, list[str]] = {}
    for key, channels in value.items():
        definition = EVENTS.get(str(key))
        if definition is None:
            raise SettingsError([f"Notification channels: unknown event {key!r}"])
        if not isinstance(channels, list):
            raise SettingsError(
                [f"{definition.label}: expected a list of channels"]
            )
        cleaned: list[str] = []
        for channel in channels:
            if channel not in NOTIFY_CHANNELS:
                raise SettingsError(
                    [f"{definition.label}: unknown channel {channel!r}"]
                )
            if channel not in cleaned:
                cleaned.append(channel)
        out[str(key)] = cleaned
    return out


class _MergedConfig:
    """``config.get`` over a plain merged settings tree."""

    def __init__(self, tree: dict[str, Any]) -> None:
        self._tree = tree

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self._tree
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


async def validate_settings(
    submitted: dict[str, Any],
    active: dict[str, Any],
    *,
    broker: Broker,
    held_symbols: set[str],
    provider_ready: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    """Validate a GUI save; returns (validated subset, human-readable changes).

    ``submitted`` is a partial settings tree. The result is the same subset
    with cleaned values, ready to merge onto the active tree.
    """
    errors: list[str] = []
    cleaned: dict[str, Any] = {}
    changes: list[str] = []

    for section, body in submitted.items():
        if section == "universe":
            uni = _validate_universe(body, active, held_symbols)
            if isinstance(uni, str):
                errors.append(uni)
            else:
                cleaned["universe"], uni_changes, asset_checks = uni
                changes.extend(uni_changes)
                for symbol in asset_checks:
                    asset_result = await broker.get_asset(symbol)
                    asset = asset_result.data if asset_result.error is None else None
                    if asset is None or not asset.tradable or asset.status != "active":
                        errors.append(
                            f"{symbol}: not an active, tradable US stock or ETF at Alpaca"
                        )
                    elif asset.asset_class != "us_equity":
                        errors.append(f"{symbol}: {asset.asset_class} is not a US stock or ETF")
            continue
        allowed = EDITABLE.get(section)
        if allowed is None or not isinstance(body, dict):
            errors.append(f"{section}: not editable")
            continue
        out: dict[str, Any] = {}
        for key, value in body.items():
            spec = allowed.get(key)
            label = LABELS.get((section, key), f"{section}.{key}")
            if spec is None:
                errors.append(f"{label}: not editable")
                continue
            kind, minimum, maximum = spec
            try:
                if kind == "bool":
                    if not isinstance(value, bool):
                        raise SettingsError([f"{label}: must be true or false"])
                    out[key] = value
                elif kind == "mode":
                    if value not in ("automatic", "supervised"):
                        raise SettingsError(
                            [f"{label}: must be automatic or supervised"]
                        )
                    out[key] = value
                elif kind == "str":
                    text = str(value).strip()
                    if not text:
                        raise SettingsError([f"{label}: must not be empty"])
                    out[key] = text
                elif kind == "cycles":
                    out[key] = _clean_cycle_list(value)
                elif kind == "review_models":
                    if not isinstance(value, dict):
                        raise SettingsError(
                            ["Review models: expected weekly/briefing/verdict"]
                        )
                    from app.llm.models import REVIEW_TASKS, known_model

                    task_labels = {
                        "weekly": "Weekly review model",
                        "briefing": "Daily briefing model",
                        "verdict": "Verdict model",
                    }
                    out[key] = {}
                    for task, model_name in value.items():
                        if task not in REVIEW_TASKS:
                            raise SettingsError(
                                [f"reviews.models.{task}: unknown review task"]
                            )
                        text = str(model_name).strip()
                        if not text:
                            raise SettingsError(
                                [f"{task_labels[task]}: must not be empty"]
                            )
                        out[key][task] = text
                elif kind == "review_prompts":
                    # Each review task accepts only the prompt versions its
                    # contract knows, and only if the file loads
                    # (docs/SPEC.md, Settings — Reviews; PR 34).
                    if not isinstance(value, dict):
                        raise SettingsError(
                            ["Review prompts: expected weekly/briefing/verdict"]
                        )
                    from app.llm.models import REVIEW_TASKS
                    from app.reviews.service import ReviewService

                    task_labels = {
                        "weekly": "Weekly review prompt",
                        "briefing": "Daily briefing prompt",
                        "verdict": "Verdict prompt",
                    }
                    out[key] = {}
                    for task, version in value.items():
                        if task not in REVIEW_TASKS:
                            raise SettingsError(
                                [f"reviews.prompts.{task}: unknown review task"]
                            )
                        text = str(version).strip()
                        if (
                            not text
                            or text
                            not in ReviewService._REVIEW_CONTRACTS.get(task, set())
                        ):
                            raise SettingsError(
                                [f"unknown_prompt_version: {text}"]
                            )
                        try:
                            load_prompt(text)
                        except (OSError, PromptError) as exc:
                            raise SettingsError(
                                [
                                    f"{task_labels[task]}: prompt {text!r} "
                                    "does not exist"
                                ]
                            ) from exc
                        out[key][task] = text
                elif kind == "instructions":
                    text = str(value)
                    if len(text) > 1000:
                        raise SettingsError(
                            [f"{label}: at most 1,000 characters"]
                        )
                    out[key] = text
                elif kind == "quiet_hours":
                    out[key] = _clean_quiet_hours(value)
                elif kind == "notify_events":
                    out[key] = _clean_notify_events(value)
                elif kind == "submap":
                    subspecs = SUBSPECS.get((section, key), {})
                    if not isinstance(value, dict):
                        raise SettingsError([f"{label}: expected a group"])
                    # Only the editable subs replace; other keys in the
                    # group keep their active value.
                    merged_subs = dict(
                        _nested_get(active, section, key) or {}
                    )
                    for sub, subvalue in value.items():
                        subspec = subspecs.get(sub)
                        sub_label = LABELS.get(
                            (section, f"{key}.{sub}"), f"{section}.{key}.{sub}"
                        )
                        if subspec is None:
                            raise SettingsError([f"{sub_label}: not editable"])
                        sub_kind, sub_min, sub_max = subspec
                        if sub_kind == "bool":
                            if not isinstance(subvalue, bool):
                                raise SettingsError(
                                    [f"{sub_label}: must be true or false"]
                                )
                            merged_subs[sub] = subvalue
                        elif sub_kind == "strlist":
                            allowed_values = SUBSPEC_LIST_VALUES.get(
                                (section, key, sub), ()
                            )
                            if (
                                not isinstance(subvalue, list)
                                or not subvalue
                                or not all(
                                    isinstance(v, str) and v in allowed_values
                                    for v in subvalue
                                )
                            ):
                                raise SettingsError(
                                    [
                                        f"{sub_label}: a non-empty list of "
                                        f"{', '.join(allowed_values)}"
                                    ]
                                )
                            merged_subs[sub] = list(dict.fromkeys(subvalue))
                        else:
                            num = _clean_number(
                                subvalue, sub_min, sub_max, sub_label
                            )
                            merged_subs[sub] = (
                                int(num) if sub_kind == "int" else num
                            )
                    out[key] = merged_subs
                elif kind == "entry_gate":
                    if value not in ("floor", "ev"):
                        raise SettingsError(
                            [f"{label}: must be floor or ev"]
                        )
                    out[key] = value
                elif kind == "selection_mode":
                    if value not in ("rank", "expected_profit"):
                        raise SettingsError(
                            [f"{label}: must be rank or expected_profit"]
                        )
                    out[key] = value
                elif kind == "null_or_positive":
                    if value is None or value == "":
                        out[key] = None
                    else:
                        num = _clean_number(value, 0, None, label)
                        if num <= 0:
                            raise SettingsError([f"{label}: must be above 0"])
                        out[key] = num
                elif kind == "url_or_empty":
                    text = str(value).strip()
                    if text:
                        from app.ops.urls import UrlRejected, validate_public_https_url

                        try:
                            out[key] = validate_public_https_url(text)
                        except UrlRejected as exc:
                            raise SettingsError([f"{label}: {safe_error(exc)}"]) from None
                    else:
                        out[key] = ""
                elif kind == "int":
                    num = _clean_number(value, minimum, maximum, label)
                    if num != int(num):
                        raise SettingsError([f"{label}: must be a whole number"])
                    out[key] = int(num)
                else:
                    out[key] = _clean_number(value, minimum, maximum, label)
            except SettingsError as exc:
                errors.extend(exc.errors)
                continue
            old = _nested_get(active, section, key)
            if old != out[key]:
                if isinstance(old, dict) and isinstance(out[key], dict):
                    for sub in sorted(set(old) | set(out[key])):
                        if old.get(sub) != out[key].get(sub):
                            sub_label = LABELS.get(
                                (section, f"{key}.{sub}"),
                                f"{section}.{key}.{sub}",
                            )
                            changes.append(
                                f"{sub_label}: {_fmt_change(old.get(sub))} "
                                f"→ {_fmt_change(out[key].get(sub))}"
                            )
                else:
                    changes.append(
                        f"{label}: {_fmt_change(old)} → {_fmt_change(out[key])}"
                    )
        if out:
            cleaned[section] = out

    if errors:
        raise SettingsError(errors)

    # Cross-field rules and file checks, only on validated values.
    merged = {**active}
    for section, body in cleaned.items():
        merged[section] = {**merged.get(section, {}), **body}
    rules = merged.get("rules", {})
    if float(rules.get("rsi_min", 0)) >= float(rules.get("rsi_max", 0)):
        errors.append("RSI from must be below RSI to")
    decision = merged.get("decision", {})
    if float(decision.get("stop_atr_min", 0)) >= float(decision.get("stop_atr_max", 0)):
        errors.append("Stop distance from must be below stop distance to")
    # A model without a price can't be selected (docs/SPEC.md, Cost).
    from app.llm.models import REVIEW_TASKS, known_model

    model = str(decision.get("model", ""))
    if model and not known_model(_MergedConfig(merged), model):
        errors.append(f"Model {model!r} is not in the models list")
    reviews = merged.get("reviews", {})
    for task in REVIEW_TASKS:
        rmodel = str((reviews.get("models") or {}).get(task, ""))
        if rmodel and not known_model(_MergedConfig(merged), rmodel):
            errors.append(
                f"{LABELS.get(('reviews', f'models.{task}'))}: "
                f"{rmodel!r} is not in the models list"
            )
    ask_model = str((merged.get("ask") or {}).get("model", ""))
    if ask_model and not known_model(_MergedConfig(merged), ask_model):
        errors.append(f"Ask model {ask_model!r} is not in the models list")
    prompt = str(decision.get("prompt", ""))
    if prompt:
        # Known prompt versions only: a version without a registered
        # contract is refused as unknown_prompt_version (docs/SPEC.md,
        # Decision layer); an unlisted file fails the manifest check.
        try:
            prompt_spec(prompt)
        except UnknownPromptVersion:
            errors.append(f"unknown_prompt_version: {prompt}")
        else:
            try:
                load_prompt(prompt)
            except (OSError, PromptError):
                errors.append(f"Prompt version {prompt!r} does not exist")
    if errors:
        raise SettingsError(errors)
    # A model whose provider has no passing key can't be picked
    # (docs/SPEC.md → Model providers, Keys). Simulated mode needs none.
    if provider_ready is not None:
        from app.llm.models import model_provider, provider_label

        picked = [
            ("Model", str(decision.get("model", ""))),
            *(
                (
                    LABELS.get(("reviews", f"models.{task}"), task),
                    str((reviews.get("models") or {}).get(task, "")),
                )
                for task in REVIEW_TASKS
            ),
        ]
        bad = [
            f"{label} {model!r} needs the "
            f"{provider_label(model_provider(_MergedConfig(merged), model))} key"
            for label, model in picked
            if model
            and not provider_ready(model_provider(_MergedConfig(merged), model))
        ]
        if bad:
            raise SettingsError(bad)
    return cleaned, changes


def _validate_universe(
    body: object,
    active: dict[str, Any],
    held_symbols: set[str],
) -> tuple[dict[str, Any], list[str], list[str]] | str:
    """Returns (cleaned universe dict, changes, symbols needing asset checks)."""
    if not isinstance(body, dict):
        return "universe: not editable"
    active_universe = active.get("universe", {})
    active_allowed = active_universe.get("allowed_sectors")
    for key, value in body.items():
        if key in UNIVERSE_KEYS:
            continue
        # The form echoes read-only keys back on a full save; tolerate them
        # unchanged, but never let a client move them.
        if key == "allowed_sectors" and value == active_allowed:
            continue
        if key in UNIVERSE_READ_ONLY and value == active_universe.get(key):
            continue
        return f"universe.{key}: not editable"

    cleaned: dict[str, Any] = {}
    changes: list[str] = []
    asset_checks: list[str] = []

    if "mode" in body:
        mode = str(body["mode"])
        if mode not in ("fixed", "screened"):
            return "universe.mode: must be fixed or screened"
        cleaned["mode"] = mode
        if mode != active_universe.get("mode"):
            changes.append(
                f"Universe mode: {_fmt_change(active_universe.get('mode'))} "
                f"→ {_fmt_change(mode)}"
            )

    if "screen" in body:
        if not isinstance(body["screen"], dict):
            return "universe.screen: not editable"
        active_screen = active_universe.get("screen") or {}
        cleaned_screen: dict[str, Any] = {}
        for key, value in body["screen"].items():
            spec = SCREEN_KEYS.get(key)
            label = SCREEN_LABELS.get(key, f"screen.{key}")
            if spec is None:
                if value == active_screen.get(key):
                    continue  # read-only echo (time_ny)
                return f"universe.screen.{key}: not editable"
            kind, minimum, maximum = spec
            try:
                num = _clean_number(value, minimum, maximum, label)
                if kind == "int":
                    if num != int(num):
                        raise SettingsError([f"{label}: must be a whole number"])
                    cleaned_screen[key] = int(num)
                else:
                    cleaned_screen[key] = num
            except SettingsError as exc:
                return "; ".join(exc.errors)
            if cleaned_screen[key] != active_screen.get(key):
                changes.append(
                    f"{label}: {_fmt_change(active_screen.get(key))} "
                    f"→ {_fmt_change(cleaned_screen[key])}"
                )
        if cleaned_screen:
            cleaned["screen"] = cleaned_screen

    if "symbols" in body or "sectors" in body:
        symbols = body.get("symbols")
        sectors = body.get("sectors") or {}
        if not isinstance(symbols, list) or not symbols:
            return "universe.symbols: a non-empty list of tickers"
        cleaned_symbols: list[str] = []
        for raw in symbols:
            symbol = str(raw).upper().strip()
            if not SYMBOL_RE.match(symbol):
                return f"{raw!r}: not a valid ticker"
            cleaned_symbols.append(symbol)
        if len(set(cleaned_symbols)) != len(cleaned_symbols):
            return "universe.symbols: duplicates"
        if "SPY" not in cleaned_symbols:
            return "SPY stays in the universe: the regime and the shock brake need it"
        held_removed = held_symbols - set(cleaned_symbols)
        if held_removed:
            return (
                f"{', '.join(sorted(held_removed))}: a symbol you hold can't be "
                "removed until it is closed"
            )
        allowed_sectors = active_universe.get("allowed_sectors", [])
        cleaned_sectors: dict[str, str] = {}
        missing: list[str] = []
        for symbol in cleaned_symbols:
            sector = sectors.get(symbol)
            if sector is None:
                missing.append(symbol)
                continue
            if sector not in allowed_sectors:
                return f"{symbol}: sector must be one of {', '.join(allowed_sectors)}"
            cleaned_sectors[symbol] = str(sector)
        if missing:
            return f"{', '.join(missing)}: a sector is required"
        old_symbols = list(active_universe.get("symbols", []))
        new_symbols = [s for s in cleaned_symbols if s not in old_symbols]
        removed = [s for s in old_symbols if s not in cleaned_symbols]
        if new_symbols:
            changes.append(f"Universe: added {', '.join(new_symbols)}")
        if removed:
            changes.append(f"Universe: removed {', '.join(removed)}")
        for symbol in cleaned_symbols:
            if active_universe.get("sectors", {}).get(
                symbol
            ) != cleaned_sectors.get(symbol) and symbol not in new_symbols:
                changes.append(
                    f"Universe: {symbol} sector → {cleaned_sectors[symbol]}"
                )
        cleaned["symbols"] = cleaned_symbols
        cleaned["sectors"] = cleaned_sectors
        asset_checks.extend(new_symbols)

    return cleaned, changes, asset_checks


def _nested_get(tree: dict[str, Any], *keys: str) -> Any:
    node: Any = tree
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _fmt_change(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "—"
    return str(value)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Same semantics as ``config._deep_merge``: a fully rebuilt tree."""
    merged: dict[str, Any] = {}
    for key, value in base.items():
        over = override.get(key)
        if isinstance(value, dict):
            merged[key] = _deep_merge(
                value, over if isinstance(over, dict) else {}
            )
        else:
            merged[key] = (
                copy.deepcopy(over) if key in override else copy.deepcopy(value)
            )
    for key, value in override.items():
        if key not in base:
            merged[key] = copy.deepcopy(value)
    return merged


# ----------------------------------------------------------------------
# version rows


def latest_version(session: Session) -> SettingsVersion | None:
    from app.db.models import SettingsVersion

    return session.execute(
        select(SettingsVersion).order_by(SettingsVersion.version.desc()).limit(1)
    ).scalar_one_or_none()


def _json_safe(value: Any) -> Any:
    """YAML parses bare dates (``events.fomc.dates``) into ``date``
    objects; the JSON column needs ISO strings instead."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def save_version(
    session: Session,
    clock: Clock,
    *,
    settings: dict[str, Any],
    reason: str,
) -> SettingsVersion:
    """Store the merged settings tree as the next version row."""
    from app.db.models import SettingsVersion

    latest = latest_version(session)
    row = SettingsVersion(
        version=(latest.version + 1) if latest is not None else 1,
        settings=_json_safe(settings),
        changed_at=_naive_utc(clock.now()),
        reason=reason.strip() or None,
    )
    session.add(row)
    session.flush()
    return row


def version_change_summary(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> str:
    """One-line diff for the Settings history table."""
    if previous is None:
        return "Defaults from the spec"
    previous, current = _json_safe(previous), _json_safe(current)
    parts: list[str] = []
    for section in sorted(set(previous) | set(current)):
        prev_body = previous.get(section) or {}
        cur_body = current.get(section) or {}
        if not isinstance(prev_body, dict) or not isinstance(cur_body, dict):
            if prev_body != cur_body:
                label = LABELS.get((section, ""), section)
                parts.append(f"{label}: {_fmt_change(prev_body)} → {_fmt_change(cur_body)}")
            continue
        for key in sorted(set(prev_body) | set(cur_body)):
            if key == "allowed_sectors":
                continue
            prev, cur = prev_body.get(key), cur_body.get(key)
            if prev == cur:
                continue
            if section == "universe" and key == "sectors":
                continue
            if isinstance(prev, dict) and isinstance(cur, dict):
                # Nested groups (universe.screen) diff per leaf.
                for sub in sorted(set(prev) | set(cur)):
                    p2, c2 = prev.get(sub), cur.get(sub)
                    if p2 == c2:
                        continue
                    label = LABELS.get(
                        (section, f"{key}.{sub}"), f"{section}.{key}.{sub}"
                    )
                    parts.append(
                        f"{label}: {_fmt_change(p2)} → {_fmt_change(c2)}"
                    )
                continue
            label = LABELS.get((section, key), f"{section}.{key}")
            if section == "universe" and key == "symbols":
                added = set(cur or []) - set(prev or [])
                removed = set(prev or []) - set(cur or [])
                if added:
                    parts.append(f"Universe: added {', '.join(sorted(added))}")
                if removed:
                    parts.append(f"Universe: removed {', '.join(sorted(removed))}")
                continue
            parts.append(f"{label}: {_fmt_change(prev)} → {_fmt_change(cur)}")
    return "; ".join(parts) or "No effective change"


def pending_change_map(
    active: dict[str, Any], pending: dict[str, Any]
) -> dict[str, Any]:
    """Leaf paths where the pending version differs from the active tree.

    Returns ``{path: pending value}`` so the Settings form can mark each
    saved-but-not-yet-active field ("Pending: Screened pool") — without it a
    save looks lost because the form shows the active values until the next
    cycle promotes the version.
    """
    active, pending = _json_safe(active), _json_safe(pending)
    out: dict[str, Any] = {}
    for section in sorted(set(active) | set(pending)):
        a = active.get(section) or {}
        p = pending.get(section) or {}
        if not isinstance(a, dict) or not isinstance(p, dict):
            if a != p:
                out[section] = p
            continue
        for key in sorted(set(a) | set(p)):
            if key == "allowed_sectors":
                continue
            av, pv = a.get(key), p.get(key)
            if av == pv:
                continue
            if isinstance(av, dict) and isinstance(pv, dict):
                prefix = (
                    "universe.sector"
                    if (section, key) == ("universe", "sectors")
                    else f"{section}.{key}"
                )
                for sub in sorted(set(av) | set(pv)):
                    if av.get(sub) != pv.get(sub):
                        out[f"{prefix}.{sub}"] = pv.get(sub)
                continue
            out[f"{section}.{key}"] = pv
    return out


# ----------------------------------------------------------------------
# schedule + connections payloads


def schedule_table(
    config: AppConfig, clock: Clock, calendar: CalendarProvider
) -> dict[str, Any]:
    """The Schedule card: NY times, Amsterdam today + a DST-shifted week."""
    now = clock.now()
    ny_now = now.astimezone(NY_TZ)
    ams = ZoneInfo(str(config.get("app", "timezone", default="Europe/Amsterdam")))
    today_session = calendar.session_for(ny_now.date())
    session_clock = SessionClock(clock, config, calendar)
    active = (
        {c.strftime("%H:%M") for c in session_clock.active_cycles(today_session)}
        if today_session is not None
        else set()
    )
    cycles = [
        str(t) for t in config.get("schedule", "cycles", default=[]) or []
    ]
    # Find the next week where the NY↔Amsterdam offset differs from today's.
    ref_ny = datetime.combine(ny_now.date(), time(12), tzinfo=NY_TZ)
    ref_offset = ref_ny.astimezone(ams).utcoffset()
    shifted_start: date | None = None
    for i in range(1, 120):
        day = ny_now.date() + timedelta(days=i)
        probe = datetime.combine(day, time(12), tzinfo=NY_TZ)
        if probe.astimezone(ams).utcoffset() != ref_offset:
            shifted_start = day - timedelta(days=day.weekday())
            break
    rows = []
    for index, cycle in enumerate(cycles, start=1):
        hour, minute = (int(p) for p in cycle.split(":")[:2])
        ny_dt = datetime.combine(ny_now.date(), time(hour, minute), tzinfo=NY_TZ)
        ams_time = ny_dt.astimezone(ams).strftime("%H:%M")
        shifted_time = None
        if shifted_start is not None:
            probe_date = shifted_start + timedelta(
                days=ny_now.date().weekday()
            )
            probe = datetime.combine(
                probe_date, time(hour, minute), tzinfo=NY_TZ
            )
            shifted_time = probe.astimezone(ams).strftime("%H:%M")
        bar_end = ny_dt - SessionClock.BAR_DELAY
        bar_start = bar_end - timedelta(minutes=60)
        bar_used = f"{bar_start.strftime('%H:%M')}–{bar_end.strftime('%H:%M')}"
        last = index == len(cycles)
        rows.append(
            {
                "index": index,
                "ny": cycle,
                "amsterdam": ams_time,
                "amsterdam_shifted": shifted_time,
                "bar_used": bar_used,
                "entries": (
                    "no"
                    if last
                    and config.get(
                        "schedule", "last_cycle_no_entries", default=False
                    )
                    else (
                        f"yes, sent by {config.get('execution', 'entry_cutoff')}"
                        if last and config.get("execution", "entry_cutoff")
                        else "yes"
                    )
                ),
                "runs_today": cycle in active if active else None,
            }
        )
    shifted_label = None
    if shifted_start is not None:
        end = shifted_start + timedelta(days=4)
        shifted_label = (
            f"Amsterdam {shifted_start.strftime('%d %b')}–{end.strftime('%d %b')}"
        )
    return {
        "rows": rows,
        "shifted_label": shifted_label,
        "cycles_per_session": len(cycles),
    }
