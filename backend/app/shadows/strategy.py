"""Strategy configurations (docs/SPEC.md, Strategies and shadows).

A strategy is a kind (``llm`` or ``rules_only``), a prompt version, model
and temperature, plus overrides of the entry rules and code checks. Risk
gates, sizing, the day-trade rule and earnings handling are never
overridable: a strategy runs the live functions on ``config.overlay(...)``
with only the keys in ``OVERRIDE_KEYS`` changed. Live is the strategy
defined by the current settings version — it has no row.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.decide.client import model_price, prices_for, require_price
from app.decide.prompt import PromptError, load_prompt
from app.settings_service import EDITABLE, LABELS

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.db.models import Strategy

KINDS = ("llm", "rules_only")
NON_ARCHIVED = ("active", "paused")

# Override name -> (settings section, key). Nothing else is overridable.
OVERRIDE_KEYS: dict[str, tuple[str, str]] = {
    "rsi_min": ("rules", "rsi_min"),
    "rsi_max": ("rules", "rsi_max"),
    "min_confidence_enter": ("decision", "min_confidence_enter"),
    "stop_atr_min": ("decision", "stop_atr_min"),
    "stop_atr_max": ("decision", "stop_atr_max"),
    "min_reward_risk": ("decision", "min_reward_risk"),
}
# Model-layer keys an llm strategy sets itself (compared with live too).
MODEL_KEYS: dict[str, tuple[str, str]] = {
    "prompt_version": ("decision", "prompt"),
    "model": ("decision", "model"),
    "temperature": ("decision", "temperature"),
}
# Code checks only apply to model answers; rules_only ignores them.
CHECK_KEYS = {"min_confidence_enter", "stop_atr_min", "stop_atr_max", "min_reward_risk"}
# decision.v3 thresholds sit one level deeper than OVERRIDE_KEYS allows;
# these map an override name to its full settings path (docs/SPEC.md,
# Decision v3 — Strategy overrides).
OVERRIDE_V3: dict[str, tuple[str, ...]] = {
    "min_room_atr": ("decision", "v3", "min_room_atr"),
    "room_level_kinds": ("decision", "v3", "room_level_kinds"),
    "min_stop_move_atr": ("decision", "v3", "min_stop_move_atr"),
    "breakeven_min_r": ("decision", "v3", "breakeven_min_r"),
    "held_stop_min_atr": ("decision", "v3", "held_stop_min_atr"),
}
_V3_BOUNDS: dict[str, tuple[float, float]] = {
    "min_room_atr": (0.0, 20.0),
    "min_stop_move_atr": (0.0, 20.0),
    "breakeven_min_r": (0.0, 5.0),
    "held_stop_min_atr": (0.0, 20.0),
}
_LEVEL_KINDS = (
    "swing_high", "swing_low", "session_high", "session_low",
    "prior_session_high", "prior_session_low", "prior_session_close", "vwap",
)


def _tree_set(tree: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = tree
    for part in path[:-1]:
        node = node.setdefault(part, {})
    node[path[-1]] = value


def _tree_get(tree: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = tree
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class StrategyError(Exception):
    """A New shadow request failed validation; ``errors`` go to the dialog."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _num(value: Any) -> float:
    return float(Decimal(str(value)))


def overrides_tree(strategy: Strategy) -> dict[str, Any]:
    """The settings sub-tree a strategy changes, ready for ``overlay``."""
    tree: dict[str, dict[str, Any]] = {}
    if strategy.kind == "llm":
        for attr, (section, key) in MODEL_KEYS.items():
            value = getattr(strategy, attr)
            if value is not None:
                tree.setdefault(section, {})[key] = value
    for name, value in (strategy.overrides or {}).items():
        if name == "entry_cycles" and isinstance(value, list) and value:
            tree.setdefault("rules", {})["entry_cycles"] = value
            continue
        target = OVERRIDE_KEYS.get(name)
        if target is not None and value is not None:
            tree.setdefault(target[0], {})[target[1]] = value
            continue
        path = OVERRIDE_V3.get(name)
        if path is not None and value is not None:
            _tree_set(tree, path, value)
    return tree


def strategy_config(config: AppConfig, strategy: Strategy) -> AppConfig:
    """Live settings with the strategy's rule/check/model values on top."""
    return config.overlay(overrides_tree(strategy))


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def differences(config: AppConfig, strategy: Strategy) -> dict[str, Any]:
    """What differs from live and what is the same (Strategies screen).

    Each entry is ``{key, label, live, shadow}`` with ``key`` the settings
    path (``decision.model``); ``same`` entries carry the shared value.
    """
    differs: list[dict[str, str]] = []
    same: list[dict[str, str]] = []
    if strategy.kind == "rules_only":
        differs.append({"key": "kind", "label": "Kind", "live": "llm", "shadow": "rules only"})
    allowed = (strategy.overrides or {}).get("entry_cycles")
    if isinstance(allowed, list) and allowed:
        differs.append(
            {
                "key": "rules.entry_cycles",
                "label": "Entry cycles",
                "live": "all cycles",
                "shadow": ", ".join(str(c) for c in allowed),
            }
        )
    keys = dict(OVERRIDE_KEYS)
    if strategy.kind == "llm":
        keys = {**MODEL_KEYS, **keys}
    shadow_tree = overrides_tree(strategy)
    for name, (section, key) in keys.items():
        if strategy.kind == "rules_only" and name in CHECK_KEYS:
            continue
        live_value = config.get(section, key)
        shadow_value = shadow_tree.get(section, {}).get(key, live_value)
        row = {
            "key": f"{section}.{key}",
            "label": LABELS.get((section, key), name),
            "live": _fmt(live_value),
            "shadow": _fmt(shadow_value),
        }
        if (
            live_value is not None
            and shadow_value is not None
            and (
                str(live_value) == str(shadow_value)
                or (not isinstance(live_value, str) and _num(live_value) == _num(shadow_value))
            )
        ):
            same.append(row)
        else:
            differs.append(row)
    if strategy.kind == "llm":
        for name, path in OVERRIDE_V3.items():
            if strategy.overrides is None or name not in (strategy.overrides or {}):
                continue
            live_value = config.get(*path)
            shadow_value = _tree_get(shadow_tree, path)
            differs.append(
                {
                    "key": ".".join(path),
                    "label": LABELS.get((path[0], path[-1]), name),
                    "live": _fmt(live_value),
                    "shadow": _fmt(shadow_value),
                }
            )
    return {"differs": differs, "same": same}


def promotion_changes(config: AppConfig, strategy: Strategy) -> dict[str, Any]:
    """The shadow's differences as a partial settings tree for Settings."""
    out: dict[str, dict[str, Any]] = {}
    for row in differences(config, strategy)["differs"]:
        if row["key"] == "kind":
            continue
        parts = row["key"].split(".")
        value = _tree_get(overrides_tree(strategy), tuple(parts))
        if value is None:
            continue
        node = out.setdefault(parts[0], {})
        for part in parts[1:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def describe(strategy: Strategy, config: AppConfig) -> str:
    """One line under the strategy name, e.g. "decision.v2 · gpt-4o-mini"."""
    if strategy.kind == "rules_only":
        stop_atr = config.get("shadows", "rules_only", "stop_atr", default=2.0)
        target_r = config.get("shadows", "rules_only", "target_r", default=2.0)
        return f"no model · stop {_fmt(float(stop_atr))} × ATR · target {_fmt(float(target_r))}R"
    return f"{strategy.prompt_version} · {strategy.model}"


def slots_used(session: Session) -> int:
    from app.db.models import Strategy

    return int(
        session.execute(
            select(func.count(Strategy.id)).where(Strategy.status.in_(NON_ARCHIVED))
        ).scalar_one()
    )


def validate_new_strategy(
    body: dict[str, Any], config: AppConfig, session: Session
) -> dict[str, Any]:
    """Check a New shadow request; returns the cleaned column values."""
    errors: list[str] = []
    name = str(body.get("name") or "").strip()
    if not name:
        errors.append("Name: required")
    elif len(name) > 64:
        errors.append("Name: at most 64 characters")
    kind = str(body.get("kind") or "")
    if kind not in KINDS:
        errors.append("Kind: llm or rules_only")
    max_active = int(config.get("shadows", "max_active", default=3))
    if slots_used(session) >= max_active:
        errors.append(f"All {max_active} shadow slots are in use; archive one first")

    cleaned: dict[str, Any] = {"name": name, "kind": kind}
    if kind == "llm":
        prompt = str(body.get("prompt_version") or config.get("decision", "prompt"))
        try:
            load_prompt(prompt)
        except (OSError, PromptError):
            errors.append(f"Prompt version {prompt!r} does not exist")
        model = str(body.get("model") or config.get("decision", "model"))
        if model_price(prices_for(config), model) is None:
            errors.append(f"Model {model!r} has no price in models")
        try:
            temperature = _num(
                body.get("temperature", config.get("decision", "temperature", default=0))
            )
            if not 0 <= temperature <= 2:
                raise ValueError
        except (ArithmeticError, ValueError):
            errors.append("Temperature: a number from 0 to 2")
            temperature = 0.0
        cleaned.update(prompt_version=prompt, model=model, temperature=temperature)
    else:
        cleaned.update(prompt_version=None, model=None, temperature=None)

    overrides: dict[str, Any] = {}
    raw = body.get("overrides") or {}
    if not isinstance(raw, dict):
        errors.append("Overrides: expected an object")
        raw = {}
    known_cycles = set(config.get("schedule", "cycles", default=[]) or [])
    for item, value in raw.items():
        if str(item) == "entry_cycles":
            # The cycles (New York "HH:MM") the strategy may enter in;
            # every name must be a configured cycle.
            if value is None or value == "":
                continue
            if not isinstance(value, list) or not all(
                isinstance(c, str) for c in value
            ):
                errors.append("Entry cycles: a list of cycle times")
                continue
            bad = [c for c in value if c not in known_cycles]
            if bad:
                errors.append(
                    f"Entry cycles: not scheduled cycles: {', '.join(bad)}"
                )
                continue
            if not value:
                errors.append("Entry cycles: at least one cycle")
                continue
            overrides["entry_cycles"] = sorted(set(value))
            continue
        target = OVERRIDE_KEYS.get(str(item))
        if target is None:
            path = OVERRIDE_V3.get(str(item))
            if path is None:
                # Risk gates, sizing, the day-trade rule and earnings
                # handling are never overridable.
                errors.append(f"{item}: not overridable")
                continue
            if kind == "rules_only" or value is None or value == "":
                continue
            label = ".".join(path)
            if item == "room_level_kinds":
                if not isinstance(value, list) or not all(
                    isinstance(k, str) for k in value
                ):
                    errors.append(f"{label}: a list of level kinds")
                    continue
                bad = [k for k in value if k not in _LEVEL_KINDS]
                if not value or bad:
                    errors.append(
                        f"{label}: unknown or no level kinds ({', '.join(bad)})"
                    )
                    continue
                overrides[str(item)] = list(dict.fromkeys(value))
                continue
            v3_low, v3_high = _V3_BOUNDS[item]
            try:
                num = _num(value)
            except (ArithmeticError, ValueError):
                errors.append(f"{label}: not a number")
                continue
            if num < v3_low or num > v3_high:
                errors.append(
                    f"{label}: must be from {v3_low:g} to {v3_high:g}"
                )
                continue
            overrides[str(item)] = num
            continue
        if value is None or value == "":
            continue
        if kind == "rules_only" and item in CHECK_KEYS:
            continue
        _kind, low, high = EDITABLE[target[0]][target[1]]
        label = LABELS.get(target, item)
        try:
            num = _num(value)
        except (ArithmeticError, ValueError):
            errors.append(f"{label}: not a number")
            continue
        if (low is not None and num < low) or (high is not None and num > high):
            errors.append(f"{label}: must be from {low:g} to {high:g}")
            continue
        overrides[str(item)] = num

    def eff(item: str) -> float:
        section, key = OVERRIDE_KEYS[item]
        value = overrides.get(item)
        if isinstance(value, bool | int | float | str):
            return _num(value)
        return _num(config.get(section, key, default=0))

    if eff("rsi_min") >= eff("rsi_max"):
        errors.append("RSI from must be below RSI to")
    if kind == "llm" and eff("stop_atr_min") >= eff("stop_atr_max"):
        errors.append("Stop distance from must be below stop distance to")
    if errors:
        raise StrategyError(errors)
    cleaned["overrides"] = overrides or None
    return cleaned


def check_model_prices(config: AppConfig, session: Session) -> None:
    """Startup refuses an unpriced live, reviews or active shadow model."""
    from app.db.models import Strategy

    require_price(config, str(config.get("decision", "model", default="")), "Live")
    # ``reviews.models`` is one model per task (docs/SPEC.md → Model
    # providers); a legacy scalar ``reviews.model`` is still honoured.
    review_models = config.get("reviews", "models", default=None)
    if isinstance(review_models, dict) and review_models:
        for task, model in review_models.items():
            require_price(config, str(model), f"Reviews ({task})")
    else:
        require_price(
            config, str(config.get("reviews", "model", default="")), "Reviews"
        )
    for strategy in session.execute(
        select(Strategy).where(Strategy.status == "active", Strategy.kind == "llm")
    ).scalars():
        require_price(config, str(strategy.model), f"Shadow {strategy.name!r}")


def strategy_start(strategy: Strategy, run_started_at: datetime) -> datetime:
    """A shadow's virtual portfolio for the current run starts here — a new
    run restarts every shadow."""
    return max(strategy.created_at, run_started_at)
