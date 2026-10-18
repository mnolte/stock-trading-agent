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
from app.decide.prompt import load_prompt
from app.market.clock import NY_TZ, SessionClock

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
    },
    "schedule": {
        # Each entry "HH:MM" New York; the list itself is validated below.
        "cycles": ("cycles", None, None),
    },
}

# Keys that may appear inside ``universe`` in a save body.
UNIVERSE_KEYS = {"symbols", "sectors"}

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
    ("reviews", "monthly_cost_cap_usd"): "Review cost cap",
    ("schedule", "cycles"): "Cycle times",
    ("universe", "symbols"): "Universe",
}


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


async def validate_settings(
    submitted: dict[str, Any],
    active: dict[str, Any],
    *,
    broker: Broker,
    held_symbols: set[str],
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
                changes.append(f"{label}: {_fmt_change(old)} → {_fmt_change(out[key])}")
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
    prompt = str(decision.get("prompt", ""))
    if prompt:
        try:
            load_prompt(prompt)
        except OSError:
            errors.append(f"Prompt version {prompt!r} does not exist")
    if errors:
        raise SettingsError(errors)
    return cleaned, changes


def _validate_universe(
    body: object,
    active: dict[str, Any],
    held_symbols: set[str],
) -> tuple[dict[str, Any], list[str], list[str]] | str:
    """Returns (cleaned universe dict, changes, symbols needing asset checks)."""
    if not isinstance(body, dict):
        return "universe: only symbols and sectors are editable"
    active_allowed = active.get("universe", {}).get("allowed_sectors")
    for key, value in body.items():
        if key in UNIVERSE_KEYS:
            continue
        # The form echoes the read-only sector list back on a full save;
        # tolerate it unchanged, but never let a client move it.
        if key == "allowed_sectors" and value == active_allowed:
            continue
        return "universe: only symbols and sectors are editable"
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
    allowed_sectors = active.get("universe", {}).get("allowed_sectors", [])
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
    old_symbols = list(active.get("universe", {}).get("symbols", []))
    new_symbols = [s for s in cleaned_symbols if s not in old_symbols]
    removed = [s for s in old_symbols if s not in cleaned_symbols]
    changes: list[str] = []
    if new_symbols:
        changes.append(f"Universe: added {', '.join(new_symbols)}")
    if removed:
        changes.append(f"Universe: removed {', '.join(removed)}")
    for symbol in cleaned_symbols:
        if active.get("universe", {}).get("sectors", {}).get(
            symbol
        ) != cleaned_sectors.get(symbol) and symbol not in new_symbols:
            changes.append(
                f"Universe: {symbol} sector → {cleaned_sectors[symbol]}"
            )
    return (
        {"symbols": cleaned_symbols, "sectors": cleaned_sectors},
        changes,
        new_symbols,
    )


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
        settings=settings,
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
                    if last and config.get(
                        "schedule", "last_cycle_no_entries", default=True
                    )
                    else "yes"
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
