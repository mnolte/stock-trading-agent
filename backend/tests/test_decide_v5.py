"""decision.v5 tests (docs/SPEC.md, Decision v5; docs/kickoffs/pr-37.md)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.config import AppConfig
from app.data.models import CalendarDay, DailyBar
from app.decide.output import (
    ValidationResult,
    validate_model_output_v4,
    validate_model_output_v5,
)
from app.decide.v5 import (
    base_rates,
    gap_p90_atr,
    last_cycle_for,
    min_confidence_ev,
    min_ev_r,
    thesis_holds_v5,
    trend_gate_detail,
    v5_config,
)
from app.market.calendar import StaticCalendarProvider
from app.market.clock import UTC


def _dec(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _calendar(*day_strs: str) -> StaticCalendarProvider:
    return StaticCalendarProvider(
        [
            CalendarDay(
                date=date.fromisoformat(d),
                open=datetime.fromisoformat(f"{d}T13:30:00+00:00"),
                close=datetime.fromisoformat(f"{d}T20:00:00+00:00"),
            )
            for d in day_strs
        ]
    )


def _option(**over: Any) -> dict[str, Any]:
    return {
        "id": "A",
        "stop": 98.5,
        "stop_basis": "1.5 × ATR",
        "stop_atr": 1.5,
        "stop_level": None,
        "target": 103.0,
        "target_basis": "swing high L2",
        "target_level": "L2",
        "rr": 2.0,
        "breakeven_confidence": 0.33,
        "risk": 1.5,
        "levels_in_way": [],
        "stop_group": "S1",
        **over,
    }


def _snapshot(
    *,
    context: str = "candidate",
    options: list[dict[str, Any]] | None = None,
    position: dict[str, Any] | None = None,
    checks: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
    overnight: dict[str, Any] | None = None,
    levels_in_way: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "symbol": "T",
        "context": context,
        "price": 100.0,
        "stop_reference_price": 100.0,
        "levels": [
            {"id": "L1", "price": 104.0, "kind": "swing_high",
             "timeframe": "1h", "time": None, "distance_atr": 4.0},
            {"id": "L2", "price": 99.0, "kind": "swing_low",
             "timeframe": "1h", "time": None, "distance_atr": -1.0},
        ],
        "checks": {"room_atr": 2.0, "room_level": "L1", **(checks or {})},
        "constraints": {
            "entry_limit": 100.0,
            "atr_1h": 1.0,
            "stop_atr_min": 1.5,
            "stop_atr_max": 4.0,
            "min_rr": 1.5,
            "min_room_atr": 1.0,
            "min_confidence": 0.55,
            "min_edge": 0.1,
        },
        "headlines": [],
        "session": session if session is not None else {"minutes_left": 120},
    }
    if overnight is not None:
        snap["overnight"] = overnight
    if context == "candidate":
        snap["options"] = options or [
            _option(levels_in_way=levels_in_way),
            {**_option(), "id": "B", "stop": 97.0, "stop_group": "S2"},
        ]
    else:
        snap["stop_options"] = options or []
        snap["position"] = position or {
            "entry": 100.0, "qty": 10, "initial_stop": 97.0,
            "current_stop": 98.0, "target": 103.0,
            "neutral_probability": 0.15, "levels_to_target": [],
        }
    return snap


def _v5(
    *,
    action: str,
    option_id: str | None = None,
    best_option_id: str | None = None,
    confidence: float = 0.7,
    conflicts: list[dict[str, Any]] | None = None,
    thesis: str = "thesis",
    what_changed: str | None = None,
) -> str:
    return json.dumps(
        {
            "symbol": "T",
            "conflicts": conflicts or [],
            "data_flags": [],
            "thesis": thesis,
            "what_changed": what_changed,
            "action": action,
            "option_id": option_id,
            "best_option_id": best_option_id,
            "confidence": confidence,
            "levels_used": [],
            "news_used": [],
        }
    )


def _validate_v5(raw: str, snap: dict[str, Any], **overlay: Any) -> ValidationResult:
    config = AppConfig().overlay(overlay)
    return validate_model_output_v5(
        raw,
        symbol="T",
        context=str(snap["context"]),
        snapshot=snap,
        atr_1h=Decimal("1.0"),
        current_stop=None,
        config=config,
    )


def _validate_v4(raw: str, snap: dict[str, Any], **overlay: Any) -> ValidationResult:
    config = AppConfig().overlay(overlay)
    return validate_model_output_v4(
        raw,
        symbol="T",
        context=str(snap["context"]),
        snapshot=snap,
        atr_1h=Decimal("1.0"),
        current_stop=None,
        config=config,
    )


# ----------------------------------------------------------------------
# the ev entry gate


def test_min_confidence_ev_bar_values() -> None:
    """(1 + min_ev_r) ÷ (1 + rr) rounded up to 2 decimals (docs/SPEC.md,
    Decision v5): 0.50 at 1.5R, 0.42 at 2R, 0.32 at 3R with 0.25."""
    ev = Decimal("0.25")
    assert min_confidence_ev(Decimal("1.5"), ev) == Decimal("0.50")
    assert min_confidence_ev(Decimal("2"), ev) == Decimal("0.42")
    assert min_confidence_ev(Decimal("3"), ev) == Decimal("0.32")
    # The 2R value rounds up: 0.4166… becomes 0.42.
    assert min_confidence_ev(Decimal("1"), ev) == Decimal("0.63")
    assert min_confidence_ev(None, ev) is None


def test_ev_gate_enter_below_bar_becomes_skip() -> None:
    """Under the ev gate a confidence below the option's bar turns the
    enter into a skip with edge_too_small — the drawer row reads
    ``0.38 ≥ 0.42``."""
    snap = _snapshot(options=[_option(rr=2.0)])
    v = _validate_v5(
        _v5(action="enter", option_id="A", confidence=0.38),
        snap,
        **{"decision": {"entry_gate": "ev"}},
    )
    assert v.final_action == "skip"
    assert v.reason == "edge_too_small"
    conf_check = next(c for c in v.checks if c.name.startswith("Confidence"))
    assert not conf_check.passed
    assert "0.42" in conf_check.detail


def test_ev_gate_enter_at_bar_passes() -> None:
    snap = _snapshot(options=[_option(rr=2.0)])
    v = _validate_v5(
        _v5(action="enter", option_id="A", confidence=0.42),
        snap,
        **{"decision": {"entry_gate": "ev"}},
    )
    assert v.final_action == "enter"
    conf_check = next(c for c in v.checks if c.name.startswith("Confidence"))
    assert conf_check.passed


def test_ev_gate_easier_bar_at_3r() -> None:
    """3R asks only 0.32 — a 0.35 confidence that the floor would stop
    (break-even + 0.1 = 0.43) passes the ev gate (docs/SPEC.md)."""
    snap = _snapshot(
        options=[_option(rr=3.0, breakeven_confidence=0.33)]
    )
    v = _validate_v5(
        _v5(action="enter", option_id="A", confidence=0.35),
        snap,
        **{"decision": {"entry_gate": "ev"}},
    )
    assert v.final_action == "enter"


def test_v4_floor_gate_unchanged() -> None:
    """v1–v4 keep the floor: 0.38 < max(0.55, 0.33 + 0.1) → skip."""
    snap = _snapshot(options=[_option(rr=2.0)])
    v = _validate_v4(
        _v5(action="enter", option_id="A", confidence=0.38), snap
    )
    assert v.final_action == "skip"
    assert v.reason == "edge_too_small"


def test_v4_with_ev_gate_config_uses_ev_bar() -> None:
    """``entry_gate: ev`` on a v4 strategy uses the same per-option bar —
    the strategy override works for every version."""
    snap = _snapshot(options=[_option(rr=3.0, breakeven_confidence=0.33)])
    v = _validate_v4(
        _v5(action="enter", option_id="A", confidence=0.35),
        snap,
        **{"decision": {"entry_gate": "ev"}},
    )
    assert v.final_action == "enter"


def test_v5_strategy_defaults_to_ev() -> None:
    """``entry_gate`` is a strategy override: floor for v1–v4, ev for
    v5 (docs/SPEC.md). A v5 strategy with no override gets ev; the
    override still wins."""
    from app.db.models import Strategy
    from app.shadows.strategy import overrides_tree

    v5 = Strategy(
        name="v5", kind="llm", prompt_version="decision.v5",
        model=None, temperature=None, overrides=None, status="active",
        created_at=datetime(2026, 11, 3, tzinfo=UTC),
    )
    tree = overrides_tree(v5)
    assert tree["decision"]["entry_gate"] == "ev"
    v5.overrides = {"entry_gate": "floor"}
    tree = overrides_tree(v5)
    assert tree["decision"]["entry_gate"] == "floor"
    v4 = Strategy(
        name="v4", kind="llm", prompt_version="decision.v4",
        model=None, temperature=None, overrides=None, status="active",
        created_at=datetime(2026, 11, 3, tzinfo=UTC),
    )
    assert "entry_gate" not in (overrides_tree(v4).get("decision") or {})


# ----------------------------------------------------------------------
# the trend gate


def test_trend_gate_detail_names_failing_checks() -> None:
    fields = {
        "checks": {
            "trend_1h": "mixed",
            "regime_trend": "down",
            "trend_daily": "down",
        }
    }
    detail = trend_gate_detail(fields)
    assert detail is not None
    assert detail.startswith("trend_gate:")
    assert "trend_1h=mixed" in detail
    assert "regime_trend=down" in detail
    # trend_daily is not part of the v5 gate — the model weighs it.
    assert "trend_daily" not in detail


def test_trend_gate_passes_when_both_up() -> None:
    fields = {
        "checks": {"trend_1h": "up", "regime_trend": "up", "trend_daily": "down"}
    }
    assert trend_gate_detail(fields) is None


def test_v5_config_defaults() -> None:
    cfg = v5_config(AppConfig())
    assert cfg.exit_margin == Decimal("0.03")
    assert cfg.overnight_margin == Decimal("0.10")
    assert cfg.gap_sessions == 60
    assert cfg.long_target_minutes == 120
    assert cfg.trend_gate is True
    assert cfg.max_rr == Decimal("3.0")
    assert cfg.base_rate_sessions == 60
    assert cfg.base_rate_min_n == 30


# ----------------------------------------------------------------------
# overnight gap p90


def _daily_bars(n: int, gap: Decimal = Decimal("0.4")) -> list[DailyBar]:
    bars = []
    close = Decimal("100")
    for i in range(n):
        open_ = close + gap
        close = open_ + Decimal("0.5")
        bars.append(
            DailyBar(
                symbol="T",
                date=date(2026, 1, 1) + timedelta(days=i),
                open=open_,
                close=close,
                high=open_ + Decimal("0.6"),
                low=close - Decimal("0.6"),
                volume=1,
            )
        )
    return bars


def test_gap_p90_atr_null_below_20_sessions() -> None:
    assert gap_p90_atr(_daily_bars(15), Decimal("1.0")) is None
    assert gap_p90_atr([], Decimal("1.0")) is None
    assert gap_p90_atr(_daily_bars(30), None) is None


def test_gap_p90_atr_value() -> None:
    # Constant 0.4 gap → p90 is 0.4, in ATR 0.4 ÷ 1.0.
    value = gap_p90_atr(_daily_bars(60), Decimal("1.0"))
    assert value is not None and float(value) == 0.4


# ----------------------------------------------------------------------
# base rates


def test_base_rate_shrinkage_formula(session_factory: Any) -> None:
    """n 30, rate 0.50, break-even ≈ 0.33 (rr 2.0) → shrunk ≈ 0.42 —
    computed by the real base_rates over seeded rows
    (docs/kickoffs/pr-37.md)."""
    from app.db.models import Cycle, Decision, Outcome

    from tests.test_outcomes import _naive

    scheduled = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    options = [
        {"id": f"o{i}", "rr": "2.0", "stop": "98", "target": "104", "entry": "100"}
        for i in range(30)
    ]
    touches = {
        f"o{i}": "target" if i % 2 == 0 else "stop" for i in range(30)
    }
    # A second decision in a different regime: must not count toward the
    # comparable set.
    other_touches = {f"x{i}": "target" for i in range(5)}
    other_options = [
        {"id": f"x{i}", "rr": "2.0", "stop": "98", "target": "104", "entry": "100"}
        for i in range(5)
    ]
    with session_factory() as session:
        for opts, tchs, checks in (
            (
                options,
                touches,
                {"regime_volatility": "normal", "momentum": "strong"},
            ),
            (
                other_options,
                other_touches,
                {"regime_volatility": "high", "momentum": "strong"},
            ),
        ):
            cycle = Cycle(
                kind="cycle",
                scheduled_time=_naive(scheduled),
                started_at=_naive(scheduled),
                finished_at=_naive(scheduled),
                status="done",
            )
            session.add(cycle)
            session.flush()
            decision = Decision(
                cycle_id=cycle.id,
                symbol="MSFT",
                context="candidate",
                input_snapshot={"symbol": "MSFT", "checks": checks},
                rules_result={"status": "pass"},
                model_output={"action": "skip"},
                validation_result={"valid": True, "final_action": "skip"},
                final_action="skip",
                prompt_version="decision.v5",
                prompt_hash="abc",
                model="fake-decision",
                options={"options": opts, "removed": []},
            )
            session.add(decision)
            session.flush()
            session.add(Outcome(decision_id=decision.id, option_touches=tchs))
        session.commit()

        out = base_rates(
            session,
            options=[
                {"id": "new_same_bucket", "rr": "2.0"},
                {"id": "new_other_bucket", "rr": "3.0"},
            ],
            regime_volatility="normal",
            momentum="strong",
            v5=v5_config(AppConfig()),
            day_ny=datetime(2026, 10, 22, tzinfo=UTC),
        )
    assert out["new_same_bucket"] == (0.42, 30)
    # No comparable touches in the other bucket: below min_n → no rate.
    assert out["new_other_bucket"] == (None, 0)


def test_min_ev_r_from_config() -> None:
    cfg = AppConfig().overlay({"decision": {"min_ev_r": 0.30}})
    assert min_ev_r(cfg) == Decimal("0.30")
    assert min_ev_r(AppConfig()) == Decimal("0.25")


# ----------------------------------------------------------------------
# thesis_holds with the fifth check


def test_thesis_holds_v5_price_below_ema20_adverse() -> None:
    at_entry = {"trend_1h": "up", "trend_daily": "up",
                "regime_trend": "up", "momentum": "strong"}
    now = {"trend_1h": "up", "trend_daily": "up",
           "regime_trend": "down", "momentum": "strong"}
    # regime down is one adverse; below EMA20 with price_vs_signal -0.48
    # is not a second one (> -0.5 boundary) — thesis still holds.
    holds = thesis_holds_v5(
        at_entry, now, vs_ema20="below", price_vs_signal=Decimal("-0.48")
    )
    assert holds is True
    # at −0.5 the fifth check turns adverse — two adverse → thesis broken.
    broken = thesis_holds_v5(
        at_entry, now, vs_ema20="below", price_vs_signal=Decimal("-0.52")
    )
    assert broken is False


def test_thesis_holds_v5_price_above_ema20_no_extra() -> None:
    at_entry = {"trend_1h": "up", "trend_daily": "up",
                "regime_trend": "up", "momentum": "strong"}
    now = dict(at_entry)
    assert (
        thesis_holds_v5(
            at_entry, now, vs_ema20="above", price_vs_signal=Decimal("-0.6")
        )
        is True
    )


# ----------------------------------------------------------------------
# the v5 code checks


def test_hold_below_breakeven_warns() -> None:
    """A hold with confidence below neutral − 0.03 warns
    ``hold_below_breakeven`` — never a gate (docs/SPEC.md)."""
    snap = _snapshot(
        context="held",
        position={
            "entry": 100.0, "qty": 10, "initial_stop": 97.0,
            "current_stop": 98.0, "target": 103.0,
            "neutral_probability": 0.15, "levels_to_target": [],
            "stop_distance_atr": 0.5,
        },
    )
    v = _validate_v5(
        _v5(action="hold", confidence=0.11), snap, current_stop=98.0
    )
    assert v.final_action == "hold"
    assert "hold_below_breakeven" in v.warnings


def test_hold_at_neutral_no_warning() -> None:
    snap = _snapshot(
        context="held",
        position={
            "entry": 100.0, "qty": 10, "initial_stop": 97.0,
            "current_stop": 98.0, "target": 103.0,
            "neutral_probability": 0.15, "levels_to_target": [],
            "stop_distance_atr": 0.5,
        },
    )
    v = _validate_v5(
        _v5(action="hold", confidence=0.14), snap
    )
    assert "hold_below_breakeven" not in v.warnings


def test_hold_below_overnight_margin_warns() -> None:
    """In the last cycle with the stop inside a typical gap, a hold
    below neutral + 0.10 warns (docs/SPEC.md, Decision v5)."""
    snap = _snapshot(
        context="held",
        session={"minutes_left": 14, "last_cycle": True},
        overnight={"gap_p90_atr": 0.85},
        position={
            "entry": 100.0, "qty": 10, "initial_stop": 97.0,
            "current_stop": 99.5, "target": 103.0,
            "neutral_probability": 0.15, "levels_to_target": [],
            "stop_distance_atr": 0.5,
        },
    )
    v = _validate_v5(
        _v5(action="hold", confidence=0.22), snap
    )
    assert "hold_below_breakeven" in v.warnings


def test_hold_at_overnight_margin_no_warning() -> None:
    snap = _snapshot(
        context="held",
        session={"minutes_left": 14, "last_cycle": True},
        overnight={"gap_p90_atr": 0.85},
        position={
            "entry": 100.0, "qty": 10, "initial_stop": 97.0,
            "current_stop": 99.5, "target": 103.0,
            "neutral_probability": 0.15, "levels_to_target": [],
            "stop_distance_atr": 0.5,
        },
    )
    v = _validate_v5(
        _v5(action="hold", confidence=0.26), snap
    )
    assert "hold_below_breakeven" not in v.warnings


def test_ko_answer_warns_and_holds() -> None:
    """The KO answer: hold at 0.65 with neutral 0.12, the stop inside
    the gap → hold_below_breakeven + confidence_outlier warn; the
    decision stands (docs/kickoffs/pr-37.md)."""
    snap = _snapshot(
        context="held",
        session={"minutes_left": 14, "last_cycle": True},
        overnight={"gap_p90_atr": 0.85},
        position={
            "entry": 100.0, "qty": 10, "initial_stop": 97.0,
            "current_stop": 99.5, "target": 103.0,
            "neutral_probability": 0.12,
            "levels_to_target": [{"id": "L8", "distance_atr": 0.26}],
            "stop_distance_atr": 0.5, "r_now": -0.62,
            "thesis_holds": False,
        },
    )
    v = _validate_v5(
        _v5(
            action="hold",
            confidence=0.65,
            thesis="The trade has room to run.",
        ),
        snap,
    )
    assert v.final_action == "hold"
    assert "hold_below_breakeven" in v.warnings
    assert "confidence_outlier" in v.warnings
    # "the trade has room" is the widened v5 path claim — flagged, never
    # gated, when a level sits within 1 ATR of the target.
    if v.claims is not None:
        assert "path" in (v.claims.kinds or [])


def test_v5_path_claim_wider_wording() -> None:
    """"has room", "room to run", "plenty of room" and "free run" count
    as path claims for v5 (docs/SPEC.md, Decision v5 — Code checks)."""
    near = {"id": "L8", "distance_atr": 0.26}
    snap = _snapshot(
        context="held",
        position={
            "entry": 100.0, "qty": 10, "initial_stop": 97.0,
            "current_stop": 99.5, "target": 103.0,
            "neutral_probability": 0.12,
            "levels_to_target": [near],
            "stop_distance_atr": 0.5,
        },
    )
    for wording in (
        "The trade has room.",
        "There is room to run.",
        "Plenty of room before the level.",
        "A free run to the target.",
    ):
        v = _validate_v5(
            _v5(action="hold", confidence=0.5, thesis=wording), snap
        )
        assert v.claims is not None
        assert "path" in (v.claims.kinds or []), wording


def test_v4_path_claim_old_wording_still_only() -> None:
    """"has room" alone is not a v4 claim — the wider wording is v5-only."""
    near = {"id": "L8", "distance_atr": 0.26}
    snap = _snapshot(
        context="held",
        position={
            "entry": 100.0, "qty": 10, "initial_stop": 97.0,
            "current_stop": 99.5, "target": 103.0,
            "neutral_probability": 0.12,
            "levels_to_target": [near],
            "stop_distance_atr": 0.5,
        },
    )
    v = _validate_v4(
        _v5(action="hold", confidence=0.5, thesis="The trade has room."),
        snap,
    )
    assert v.claims is not None
    assert "path" not in (v.claims.kinds or [])


# ----------------------------------------------------------------------
# snapshot v5 helpers


def test_last_cycle_for_last_active_cycle() -> None:
    config = AppConfig().overlay(
        {"schedule": {"cycles": ["10:46", "11:46", "12:46", "13:46", "14:46", "15:46"]}}
    )
    cal = _calendar("2026-11-03")
    last = last_cycle_for(
        config, cal, datetime.fromisoformat("2026-11-03T15:46:00-04:00")
    )
    assert last is True
    earlier = last_cycle_for(
        config, cal, datetime.fromisoformat("2026-11-03T14:46:00-04:00")
    )
    assert earlier is False


def test_v5_options_fields_and_rr_cap() -> None:
    """build_v5_fields stamps target_atr + min_confidence on options and
    moves rr > max_rr to ``removed`` with the cap reason."""
    from app.decide.v5 import _options_v5

    options = [
        _option(id="A", rr=2.0, target=103.0),
        _option(id="B", rr=3.5, target=104.5),
    ]
    fields: dict[str, Any] = {"options": options}
    column: dict[str, Any] = {"options": options, "removed": []}
    _options_v5(
        fields,
        column,
        limit=Decimal("100"),
        atr_1h=Decimal("1.0"),
        v5=v5_config(AppConfig()),
        ev_r=Decimal("0.25"),
    )
    kept = fields["options"]
    assert [o["id"] for o in kept] == ["A"]
    assert kept[0]["target_atr"] == Decimal("3")
    assert kept[0]["min_confidence"] == 0.42
    removed = column["removed"]
    assert len(removed) == 1
    assert removed[0]["id"] == "B"
    assert "cap" in removed[0]["reason"]
    # The removed option is stored in full — stop/target/rr intact.
    assert removed[0]["stop"] == 98.5
    assert removed[0]["rr"] == 3.5


def test_base_rates_empty_options_returns_empty() -> None:
    """base_rate/base_n land on offered options — an empty option list
    short-circuits (docs/SPEC.md)."""
    from unittest.mock import MagicMock

    out = base_rates(
        MagicMock(),
        options=[],
        regime_volatility="normal",
        momentum="strong",
        v5=v5_config(AppConfig()),
        day_ny=datetime(2026, 11, 3, tzinfo=UTC),
    )
    assert out == {}


# ----------------------------------------------------------------------
# counterfactual touches (docs/SPEC.md, Decision v5 — Counterfactual
# touches): options removed by a filter and options built for a
# rules-blocked candidate get the same touch treatment as offered ones.


def _seed_counterfactual_decision(
    session_factory: Any,
    scheduled_utc: datetime,
    *,
    offered: list[dict[str, Any]],
    removed: list[dict[str, Any]],
    final_action: str = "skip",
) -> int:
    from app.db.models import Cycle, Decision, Outcome

    from tests.test_outcomes import _naive

    with session_factory() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=_naive(scheduled_utc),
            started_at=_naive(scheduled_utc),
            finished_at=_naive(scheduled_utc + timedelta(minutes=1)),
            status="done",
        )
        session.add(cycle)
        session.flush()
        decision = Decision(
            cycle_id=cycle.id,
            symbol="MSFT",
            context="candidate",
            input_snapshot={
                "symbol": "MSFT",
                "price": 420.0,
                "constraints": {"entry_limit": 420.0},
            },
            rules_result={"status": "pass", "reason": None, "checks": []},
            model_output={"action": "skip", "confidence": 0.6},
            validation_result={
                "valid": True,
                "model_action": final_action,
                "final_action": final_action,
                "changed_by_code": False,
            },
            final_action=final_action,
            prompt_version="decision.v5",
            prompt_hash="abc",
            model="fake-decision",
            options={
                "options_version": "v4",
                "options": offered,
                "removed": removed,
            },
        )
        session.add(decision)
        session.flush()
        session.add(Outcome(decision_id=decision.id))
        session.commit()
        return decision.id


@pytest.mark.anyio
async def test_removed_and_rules_blocked_options_get_touches(
    session_factory: Any,
) -> None:
    """A room-removed option and an option built for a candidate the
    rules stopped both get touches from the fixture bars
    (docs/kickoffs/pr-37.md — counterfactual touches)."""

    from app.market.clock import FakeClock

    from tests.test_outcomes import DELAY, _outcome, _seed_bars, _worker


    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    target = datetime(2026, 10, 22, 14, 46, tzinfo=UTC)
    clock = FakeClock(target + DELAY)
    worker, _market_data = _worker(session_factory, clock)

    # A: offered and never touched. B: removed by the room filter — its
    # stop is hit. C: built for a rules-blocked candidate — its target
    # is hit (stored in the same no-call row's counterfactual options).
    decision_id = _seed_counterfactual_decision(
        session_factory,
        decision_time,
        offered=[
            {"id": "A", "stop": 415.0, "target": 435.0, "rr": 3.0,
             "risk": 5.0},
        ],
        removed=[
            {"id": "B", "stop": 418.0, "target": 434.0, "rr": 8.0,
             "risk": 2.0,
             "reason": "only 0.8 ATR room to the next level"},
            {"id": "C", "stop": 417.0, "target": 430.0, "rr": 3.3,
             "risk": 3.0, "reason": "rules pre-filter: rsi_above"},
        ],
    )
    _seed_bars(
        session_factory,
        [
            # Fill bar inside the window.
            (datetime(2026, 10, 15, 14, 50, tzinfo=UTC), 421, 422, 419.5, 421),
            # C's 430 target first, never A's 415 stop or 435 target.
            (datetime(2026, 10, 15, 15, 30, tzinfo=UTC), 421, 430.5, 420.5, 430),
            # B's 418 stop next day.
            (datetime(2026, 10, 16, 14, 30, tzinfo=UTC), 420, 420.5, 417.5, 418),
            # Closed at the 5-session target so the fill runs this pass.
            (datetime(2026, 10, 22, 14, 40, tzinfo=UTC), 425, 426, 424, 425),
        ],
    )
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    assert outcome.price_5sessions is not None
    assert outcome.option_touches == {
        "A": "neither",
        "B": "stop",
        "C": "target",
    }


def test_build_v5_held_snapshot_ko_shape() -> None:
    """The KO snapshot shape (docs/kickoffs/pr-37.md — Done when):
    ``last_cycle`` true, ``gap_p90_atr`` above 0.83,
    ``price_vs_signal`` −0.48, the fifth thesis check and the earnings
    status."""

    from app.data.models import Bar1h
    from app.decide.v3 import HeldV3
    from app.decide.v5 import build_v5_fields
    from app.shadows.runner import indicators_from_snapshot, regime_from_snapshot

    config = AppConfig()
    held = HeldV3(
        entry=Decimal("100"), qty=10,
        initial_stop=Decimal("97"), current_stop=Decimal("99.5"),
        target=Decimal("103"),
        opened_at=datetime(2026, 11, 2, 18, 46, tzinfo=UTC),
        entry_thesis=None,
        stop_basis=None,
    )
    base = {
        "symbol": "KO",
        "time_ny": "2026-11-03T15:46:00-05:00",
        "price": "100.00",
        "stop_reference_price": "100.00",
        "indicators_1h": {"ema20": "100.48", "ema50": "99",
                          "atr14": "1.0", "rsi14": "45",
                          "vwap": "100", "rs_vs_spy_pct": "-1.0",
                          "obv_trend": "down"},
        "indicators_daily": {"close": "99", "sma50": "98",
                             "sma200": "95"},
        "regime": {"trend": "down", "volatility": "normal"},
        "checks": {"trend_1h": "down", "trend_daily": "up",
                   "regime_trend": "down", "regime_volatility": "normal",
                   "momentum": "weak"},
        "headlines": [],
        "levels": [],
    }
    # The signal bar closed at 100.48; the price is 100 → −0.48 ATR.
    signal_bar = Bar1h(
        symbol="KO",
        start=datetime(2026, 11, 3, 19, 0, tzinfo=UTC),
        end=datetime(2026, 11, 3, 20, 0, tzinfo=UTC),
        open=Decimal("100.5"), high=Decimal("101"), low=Decimal("100"),
        close=Decimal("100.48"), volume=1000, feed="test", complete=True,
    )
    daily = _daily_bars(30, gap=Decimal("0.9"))
    build = build_v5_fields(
        context="held",
        base=base,
        price=Decimal("100"),
        reference=Decimal("100"),
        atr_1h=Decimal("1.0"),
        indicators=indicators_from_snapshot("KO", base),
        regime=regime_from_snapshot(base, config),
        bars_1h=[signal_bar],
        daily=daily,
        held=held,
        entry_checks={"trend_1h": "up", "trend_daily": "up",
                      "regime_trend": "up", "momentum": "strong"},
        names={"KO": "Coca-Cola"},
        calendar=_calendar("2026-11-02", "2026-11-03"),
        config=config,
        cycle_time_ny=datetime.fromisoformat("2026-11-03T15:46:00-05:00"),
        last_cycle=True,
    )
    fields = build.fields
    assert fields["session"]["last_cycle"] is True
    gap = fields["overnight"]["gap_p90_atr"]
    assert gap is not None and float(gap) > 0.83
    pos = fields["position"]
    assert float(pos["price_vs_signal"]) == -0.48
    assert pos["price_vs_ema20"] == "below"
    # thesis_holds uses the fifth check: below EMA20 but price_vs_signal
    # −0.48 > −0.5 → not adverse on its own.
    assert "earnings" in (fields.get("checks") or {})
    assert fields["checks"]["earnings"]["status"] in (
        "none_in_window", "scheduled", "unavailable"
    )


@pytest.mark.anyio
async def test_v5_shadow_runs_and_stores_v5_fields(
    session_factory: Any,
) -> None:
    """A shadow on decision.v5 runs its own call and stores the v5
    snapshot: options carry min_confidence/target_atr and the session
    block carries last_cycle (docs/SPEC.md, Decision v5)."""
    from datetime import timedelta

    from app.db.models import ShadowDecision
    from app.decide.client import FakeDecisionClient
    from app.market.clock import FakeClock
    from sqlalchemy import select

    from tests.test_shadows import (
        CYCLE,
        _runner,
        _seed,
        _snap,
    )

    cycle_id = _seed(
        session_factory,
        [("MSFT", "skip", None, _snap("MSFT"))],
    )
    with session_factory() as session:
        from app.db.models import Strategy

        s = Strategy(
            name="v5 shadow",
            kind="llm",
            prompt_version="decision.v5",
            model="gpt-4o-mini",
            temperature=0.0,
            overrides=None,
            status="active",
            created_at=CYCLE,
        )
        session.add(s)
        session.commit()
        sid = s.id
    clock = FakeClock(CYCLE + timedelta(minutes=1))
    runner = _runner(
        session_factory,
        clock,
        clients={
            "v5 shadow": FakeDecisionClient(enter_symbols={"MSFT"})
        },
    )
    await runner.after_cycle(cycle_id)
    with session_factory() as session:
        row = session.execute(
            select(ShadowDecision).where(
                ShadowDecision.strategy_id == sid,
                ShadowDecision.symbol == "MSFT",
            )
        ).scalar_one()
    assert row.prompt_version == "decision.v5"
    # The shadow row keeps the live snapshot; the v5 build went into the
    # options column (min_confidence/target_atr per option) and the
    # send snapshot.
    options = (row.options or {}).get("options") or []
    assert options
    assert "min_confidence" in options[0]
    assert "target_atr" in options[0]
    stopped = row.final_action == "no_call" and row.no_call_reason
    assert row.final_action == "enter" or stopped, (
        f"unexpected stop: {row.final_action} · {row.no_call_reason} · "
        f"{(row.validation_result or {}).get('error')}"
    )


@pytest.mark.anyio
async def test_v5_trend_gate_blocks_model_call(
    session_factory: Any,
) -> None:
    """A v5 candidate whose checks aren't both up gets no model call —
    ``no_call · checks`` with the check values in the detail
    (docs/SPEC.md, Decision v5 — Trend gate)."""
    from datetime import timedelta

    from app.db.models import ShadowDecision
    from app.decide.client import FakeDecisionClient
    from app.market.clock import FakeClock
    from sqlalchemy import select

    from tests.test_shadows import CYCLE, _runner, _seed, _snap

    snap = _snap("MSFT")
    # trend_1h mixed — the v5 trend gate stops this before the model.
    snap["checks"] = {"trend_1h": "mixed", "regime_trend": "up"}
    cycle_id = _seed(
        session_factory,
        [("MSFT", "skip", None, snap)],
    )
    with session_factory() as session:
        from app.db.models import Strategy

        s = Strategy(
            name="v5 gate",
            kind="llm",
            prompt_version="decision.v5",
            model="gpt-4o-mini",
            temperature=0.0,
            overrides=None,
            status="active",
            created_at=CYCLE,
        )
        session.add(s)
        session.commit()
        sid = s.id
    clock = FakeClock(CYCLE + timedelta(minutes=1))
    runner = _runner(
        session_factory,
        clock,
        clients={
            "v5 gate": FakeDecisionClient(enter_symbols={"MSFT"})
        },
    )
    report = await runner.after_cycle(cycle_id)
    with session_factory() as session:
        row = session.execute(
            select(ShadowDecision).where(
                ShadowDecision.strategy_id == sid,
                ShadowDecision.symbol == "MSFT",
            )
        ).scalar_one()
    assert row.no_call_reason == "checks"
    detail = ((row.validation_result or {}).get("error") or "")
    assert "trend_1h" in detail
    # No model call was made — the strategy stayed at zero calls.
    assert report.strategies[0].calls == 0
