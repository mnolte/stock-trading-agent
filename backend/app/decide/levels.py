"""Level finder for decision.v2 (docs/SPEC.md, Decision v2 — Levels).

Pure functions over cached bars: confirmed 1h and daily swing highs and
lows, the prior session's high/low/close, today's session high/low and the
session VWAP — at most ``max_levels_each_side`` on each side of the market
price, numbered L1… from the highest price down.

"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.data.models import Bar1h, Bar5m, DailyBar
from app.signals.indicators import session_vwap

if TYPE_CHECKING:
    from app.config import AppConfig

CENT = Decimal("0.01")

# The format of the stored levels/options JSON; bump when the shape changes.
OPTIONS_VERSION = "v2"

LEVEL_KINDS_LOW = frozenset({"swing_low", "prior_session_low", "session_low"})
LEVEL_KINDS_HIGH = frozenset({"swing_high", "prior_session_high", "session_high"})


@dataclass(frozen=True)
class OptionsConfig:
    """The builder's knobs, read once from a (possibly overlaid) config."""

    swing_strength: int = 2
    swing_lookback_bars_1h: int = 60
    swing_lookback_sessions: int = 60
    max_levels_each_side: int = 8
    level_buffer_atr: Decimal = Decimal("0.1")
    atr_stop_multiples: tuple[Decimal, ...] = (Decimal("2.0"),)
    target_r_multiples: tuple[Decimal, ...] = (Decimal("2.0"), Decimal("3.0"))
    dedupe_atr: Decimal = Decimal("0.5")
    max_stops: int = 3
    max_options: int = 6
    held_trail_atr: Decimal = Decimal("2.0")
    # The check overrides a strategy may set (decision.*).
    stop_atr_min: Decimal = Decimal("1.5")
    stop_atr_max: Decimal = Decimal("4.0")
    min_reward_risk: Decimal = Decimal("1.5")
    tighten_min_atr_below_price: Decimal = Decimal("0.25")


def _dec_list(values: Any, fallback: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    try:
        return tuple(Decimal(str(v)) for v in values)
    except (TypeError, ArithmeticError):
        return fallback


def options_config(config: AppConfig) -> OptionsConfig:
    """Read ``decision.options`` and the check thresholds from a config."""
    base = OptionsConfig()

    def dec(key: str, default: Decimal) -> Decimal:
        return Decimal(
            str(config.get("decision", "options", key, default=default))
        )

    return OptionsConfig(
        swing_strength=int(
            config.get("decision", "options", "swing_strength", default=2)
        ),
        swing_lookback_bars_1h=int(
            config.get(
                "decision", "options", "swing_lookback_bars_1h", default=60
            )
        ),
        swing_lookback_sessions=int(
            config.get(
                "decision", "options", "swing_lookback_sessions", default=60
            )
        ),
        max_levels_each_side=int(
            config.get("decision", "options", "max_levels_each_side", default=8)
        ),
        level_buffer_atr=dec("level_buffer_atr", base.level_buffer_atr),
        atr_stop_multiples=_dec_list(
            config.get(
                "decision", "options", "atr_stop_multiples", default=None
            ),
            base.atr_stop_multiples,
        ),
        target_r_multiples=_dec_list(
            config.get(
                "decision", "options", "target_r_multiples", default=None
            ),
            base.target_r_multiples,
        ),
        dedupe_atr=dec("dedupe_atr", base.dedupe_atr),
        max_stops=int(config.get("decision", "options", "max_stops", default=3)),
        max_options=int(
            config.get("decision", "options", "max_options", default=6)
        ),
        held_trail_atr=dec("held_trail_atr", base.held_trail_atr),
        stop_atr_min=Decimal(
            str(config.get("decision", "stop_atr_min", default=1.5))
        ),
        stop_atr_max=Decimal(
            str(config.get("decision", "stop_atr_max", default=4.0))
        ),
        min_reward_risk=Decimal(
            str(config.get("decision", "min_reward_risk", default=1.5))
        ),
        tighten_min_atr_below_price=Decimal(
            str(
                config.get(
                    "decision", "tighten_min_atr_below_price", default=0.25
                )
            )
        ),
    )


# ----------------------------------------------------------------------
# levels


@dataclass(frozen=True)
class Level:
    """One price level offered to the model (docs/SPEC.md, Decision v2)."""

    id: str  # L1, L2, … from the highest price down
    price: Decimal
    # swing_low | swing_high | prior_session_high | prior_session_low |
    # prior_session_close | session_high | session_low | vwap
    kind: str
    timeframe: str  # "1h" | "daily" | "session"
    time: datetime | None  # the swing bar's start / session open


@dataclass(frozen=True)
class _Swing:
    price: Decimal
    time: datetime
    kind: str  # swing_high | swing_low


def _swings(
    bars: Sequence[Any],
    strength: int,
    lookback: int,
) -> tuple[list[_Swing], list[_Swing]]:
    """Confirmed swing highs and lows over the last ``lookback`` bars.

    A swing high is strictly above the ``strength`` bars on each side, a
    swing low strictly below; the newest ``strength`` bars can never be
    one (the swing is not confirmed yet).
    """
    window = list(bars)[-lookback:] if lookback > 0 else list(bars)
    highs: list[_Swing] = []
    lows: list[_Swing] = []
    for i in range(strength, len(window) - strength):
        bar = window[i]
        before = window[i - strength : i]
        after = window[i + 1 : i + 1 + strength]
        when = bar.start if isinstance(bar, Bar1h) else datetime.combine(
            bar.date, datetime.min.time()
        )
        if all(bar.high > b.high for b in before + after):
            highs.append(_Swing(bar.high, when, "swing_high"))
        if all(bar.low < b.low for b in before + after):
            lows.append(_Swing(bar.low, when, "swing_low"))
    return highs, lows


def find_levels(
    *,
    bars_1h: Sequence[Bar1h],
    daily: Sequence[DailyBar],
    today_5m: Sequence[Bar5m],
    price: Decimal,
    cfg: OptionsConfig,
) -> list[Level]:
    """The level set for one symbol at one decision time.

    ``bars_1h`` are closed session-aligned 1h bars, ``daily`` completed
    daily bars (``daily[-1]`` is the prior session), ``today_5m`` today's
    5-minute bars that closed so far. ``price`` is the market price the
    "nearest first" ordering is measured against.
    """
    candidates: list[tuple[Decimal, str, str, datetime | None]] = []
    # 1h and daily swings share the same rule over their lookbacks.
    swings_1h = _swings(bars_1h, cfg.swing_strength, cfg.swing_lookback_bars_1h)
    swings_d = _swings(
        daily, cfg.swing_strength, cfg.swing_lookback_sessions
    )
    for group, tf in ((swings_1h, "1h"), (swings_d, "daily")):
        for swing in (*group[0], *group[1]):
            candidates.append((swing.price, swing.kind, tf, swing.time))
    prior = daily[-1] if daily else None
    if prior is not None:
        when = datetime.combine(prior.date, datetime.min.time())
        candidates += [
            (prior.high, "prior_session_high", "session", when),
            (prior.low, "prior_session_low", "session", when),
            (prior.close, "prior_session_close", "session", when),
        ]
    if today_5m:
        today = datetime.combine(
            today_5m[-1].start.date(), datetime.min.time()
        )
        candidates += [
            (max(b.high for b in today_5m), "session_high", "session", today),
            (min(b.low for b in today_5m), "session_low", "session", today),
        ]
        vwap = session_vwap(today_5m)
        if vwap is not None:
            candidates.append((vwap, "vwap", "session", today))

    below = sorted(
        (c for c in candidates if c[0] <= price),
        key=lambda c: c[0],
        reverse=True,
    )[: cfg.max_levels_each_side]
    above = sorted(
        (c for c in candidates if c[0] > price), key=lambda c: c[0]
    )[: cfg.max_levels_each_side]
    ordered = sorted(below + above, key=lambda c: c[0], reverse=True)
    return [
        Level(
            id=f"L{i + 1}",
            price=lv,
            kind=kind,
            timeframe=tf,
            time=when,
        )
        for i, (lv, kind, tf, when) in enumerate(ordered)
    ]




def level_json(level: Level) -> dict[str, Any]:
    """The level as stored in snapshots and the ``levels`` column."""
    return {
        "id": level.id,
        "price": float(level.price),
        "kind": level.kind,
        "timeframe": level.timeframe,
        "time": level.time.isoformat() if level.time else None,
    }
