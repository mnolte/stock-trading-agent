"""decision.v4: snapshot v4, the v4 code checks and the v4 output schema.

Synthetic snapshots over the v3 shape — the kickoff tests
(docs/kickoffs/pr-34.md — Tests).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.config import AppConfig
from app.data.models import CalendarDay
from app.decide.output import (
    ModelOutputV4,
    validate_model_output_v4,
)
from app.decide.prompt import prompt_spec
from app.decide.v4 import (
    PreviousV4,
    about_symbol,
    build_v4_fields,
    checks_at_entry,
    session_time_held,
    thesis_holds,
    v4_config,
    v4_send_snapshot,
)
from app.market.calendar import StaticCalendarProvider
from app.shadows.runner import indicators_from_snapshot, regime_from_snapshot


def _dec(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _calendar(*day_strs: str) -> StaticCalendarProvider:
    days = [
        CalendarDay(
            date=date.fromisoformat(d),
            open=datetime.fromisoformat(f"{d}T13:30:00+00:00"),
            close=datetime.fromisoformat(f"{d}T20:00:00+00:00"),
        )
        for d in day_strs
    ]
    return StaticCalendarProvider(days)


def _option(levels_in_way: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
        "levels_in_way": levels_in_way or [],
        "stop_group": "S1",
    }


def _snapshot(
    *,
    context: str = "candidate",
    options: list[dict[str, Any]] | None = None,
    levels_in_way: list[dict[str, Any]] | None = None,
    previous: dict[str, Any] | None = None,
    headlines: list[dict[str, Any]] | None = None,
    checks: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
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
        "previous_decision": previous,
        "headlines": headlines if headlines is not None else [],
        "session": {"minutes_left": 120},
    }
    if context == "candidate":
        snap["options"] = options or [
            _option(levels_in_way),
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


def _v4(
    *,
    action: str,
    option_id: str | None = None,
    best_option_id: str | None = None,
    confidence: float = 0.7,
    conflicts: list[dict[str, Any]] | None = None,
    thesis: str = "thesis",
    what_changed: str | None = None,
    news_used: list[dict[str, Any]] | None = None,
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
            "news_used": news_used or [],
        }
    )


def _validate(raw: str, snap: dict[str, Any], **overlay: Any):
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


def _check(validation: Any, name: str):
    return next(c for c in validation.checks if c.name == name)


# ----------------------------------------------------------------------
# flip-flop guard (the TSLA case)


def _previous_skip(move_atr: float = 0.21) -> dict[str, Any]:
    return {
        "time": "2026-10-31T13:46:00-04:00",
        "action": "skip",
        "option_id": None,
        "best_option_id": "A",
        "confidence": 0.38,
        "conflicts": [],
        "price": 100.0,
        "since": {
            "price_move_atr": move_atr,
            "checks_changed": [],
            "new_headlines": [],
            "new_levels_within_1_atr": [],
        },
    }


def test_flip_flop_blocks_enter_after_skip_small_move() -> None:
    snap = _snapshot(previous=_previous_skip())
    v = _validate(_v4(action="enter", option_id="A", best_option_id="A"), snap)
    assert v.final_action == "skip"
    assert v.reason == "flip_flop"
    assert v.model_action == "enter"


def test_flip_flop_enter_allowed_with_new_about_symbol_headline() -> None:
    snap = _snapshot(
        previous=_previous_skip(),
        headlines=[{"id": "n1", "about_symbol": True, "new": True,
                    "title": "Tesla Q3 deliveries beat"}],
    )
    v = _validate(_v4(action="enter", option_id="A", best_option_id="A"), snap)
    assert v.final_action == "enter"


def test_flip_flop_enter_allowed_on_bigger_move() -> None:
    snap = _snapshot(previous=_previous_skip(move_atr=0.8))
    v = _validate(_v4(action="enter", option_id="A", best_option_id="A"), snap)
    assert v.final_action == "enter"


def test_flip_flop_enter_allowed_when_checks_changed() -> None:
    prev = _previous_skip()
    prev["since"]["checks_changed"] = ["momentum: strong → mixed"]
    snap = _snapshot(previous=prev)
    v = _validate(_v4(action="enter", option_id="A", best_option_id="A"), snap)
    assert v.final_action == "enter"


def test_flip_flop_never_blocks_enter_to_skip() -> None:
    prev = _previous_skip()
    prev["action"] = "enter"
    snap = _snapshot(previous=prev)
    v = _validate(_v4(action="skip", best_option_id="A"), snap)
    assert v.final_action == "skip"
    assert v.reason != "flip_flop"


# ----------------------------------------------------------------------
# confidence sanity + unresolved conflicts (the PG case)


def test_confidence_outlier_warns_action_stands() -> None:
    snap = _snapshot(
        context="held",
        position={
            "entry": 100.0, "qty": 10, "initial_stop": 97.0,
            "current_stop": 98.0, "target": 103.0,
            "neutral_probability": 0.15, "levels_to_target": [],
        },
    )
    v = _validate(_v4(action="hold", confidence=0.85), snap)
    assert v.final_action == "hold"
    assert "confidence_outlier" in v.warnings
    assert v.confidence_anchor == 0.15
    assert not _check(v, "Confidence sanity").passed


def test_confidence_within_anchor_no_warning() -> None:
    snap = _snapshot()
    v = _validate(_v4(action="enter", option_id="A", best_option_id="A",
                      confidence=0.6), snap)
    assert v.final_action == "enter"
    assert "confidence_outlier" not in v.warnings


def test_unresolved_conflict_warns_enter_stands() -> None:
    snap = _snapshot()
    v = _validate(
        _v4(
            action="enter", option_id="A", best_option_id="A",
            confidence=0.7,
            conflicts=[{"text": "RSI is near the top of the range",
                        "outweighed_by": None}],
        ),
        snap,
    )
    assert v.final_action == "enter"
    assert "unresolved_conflict" in v.warnings


def test_resolved_conflict_no_warning() -> None:
    snap = _snapshot()
    v = _validate(
        _v4(
            action="enter", option_id="A", best_option_id="A",
            confidence=0.7,
            conflicts=[{"text": "RSI near top", "outweighed_by": "trend strong"}],
        ),
        snap,
    )
    assert v.final_action == "enter"
    assert "unresolved_conflict" not in v.warnings


# ----------------------------------------------------------------------
# thesis_holds / checks_at_entry


def test_thesis_holds_false_on_two_adverse() -> None:
    at_entry = {"trend_1h": "up", "trend_daily": "up",
                "regime_trend": "up", "momentum": "strong"}
    now = {"trend_1h": "down", "trend_daily": "up",
           "regime_trend": "up", "momentum": "weak"}
    assert thesis_holds(at_entry, now) is False


def test_thesis_holds_true_on_one_adverse() -> None:
    at_entry = {"trend_1h": "up", "trend_daily": "up",
                "regime_trend": "up", "momentum": "strong"}
    now = {"trend_1h": "mixed", "trend_daily": "up",
           "regime_trend": "up", "momentum": "strong"}
    assert thesis_holds(at_entry, now) is True


# ----------------------------------------------------------------------
# best_option_id validity


def test_skip_with_invalid_best_option_is_invalid() -> None:
    snap = _snapshot()
    v = _validate(_v4(action="skip", best_option_id="Z"), snap)
    assert not v.valid
    assert v.final_action == "skip"


def test_skip_requires_best_option() -> None:
    snap = _snapshot()
    v = _validate(_v4(action="skip", best_option_id=None), snap)
    assert not v.valid


def test_held_with_best_option_is_invalid() -> None:
    snap = _snapshot(context="held")
    v = _validate(_v4(action="hold", best_option_id="A"), snap)
    assert not v.valid
    assert v.final_action == "hold"


def test_held_without_best_option_valid() -> None:
    snap = _snapshot(context="held")
    v = _validate(_v4(action="hold", confidence=0.3), snap)
    assert v.valid
    assert v.final_action == "hold"


# ----------------------------------------------------------------------
# path claim


def test_path_claim_fails_with_level_within_1_atr() -> None:
    snap = _snapshot(levels_in_way=[{"id": "L9", "distance_atr": 0.6}])
    v = _validate(
        _v4(action="enter", option_id="A", best_option_id="A",
            thesis="Clear path to the target."),
        snap,
    )
    assert v.claims is not None and not v.claims.passed
    assert "path claim" in v.claims.bad_ids
    assert "path" in v.claims.kinds
    assert v.final_action == "enter"  # claims never gate


def test_path_claim_passes_beyond_1_atr() -> None:
    snap = _snapshot(levels_in_way=[{"id": "L9", "distance_atr": 1.4}])
    v = _validate(
        _v4(action="enter", option_id="A", best_option_id="A",
            thesis="Clear path to the target."),
        snap,
    )
    assert v.claims is not None and v.claims.passed


def test_levels_in_way_sentence_not_a_claim() -> None:
    # B1: naming levels "in the way" is not an unsupported stop/target
    # claim.
    snap = _snapshot()
    v = _validate(
        _v4(
            action="skip", best_option_id="A",
            conflicts=[{"text": "levels L2 and L1 in the way",
                        "outweighed_by": None}],
        ),
        snap,
    )
    assert v.claims is not None and v.claims.passed


# ----------------------------------------------------------------------
# about_symbol


def test_about_symbol() -> None:
    names = {"NVDA": "Nvidia", "RTX": "RTX Corporation", "TSLA": "Tesla"}
    assert about_symbol("Nvidia RTX laptops get new models", "RTX", names) is False
    assert about_symbol("Nvidia RTX laptops get new models", "NVDA", names) is True
    assert about_symbol("Tesla Q3 deliveries beat estimates", "TSLA", names) is True
    assert about_symbol("Market closes higher", "TSLA", names) is False
    assert about_symbol("TSLA rises on deliveries", "TSLA", names) is True


# ----------------------------------------------------------------------
# the v4 snapshot builder


def _base_snapshot() -> dict[str, Any]:
    return {
        "symbol": "T",
        "time_ny": "2026-10-30T14:00:00-04:00",
        "price": "100.00",
        "stop_reference_price": "100.00",
        "indicators_1h": {"ema20": "99", "ema50": "97", "atr14": "1.0",
                          "rsi14": "55", "vwap": "99.5",
                          "rs_vs_spy_pct": "1.0", "obv_trend": "up"},
        "indicators_daily": {"close": "98", "sma50": "95", "sma200": "90"},
        "regime": {"trend": "up", "volatility": "normal"},
        "checks": {"trend_1h": "up", "trend_daily": "up",
                   "regime_trend": "up", "regime_volatility": "normal",
                   "momentum": "strong"},
        "headlines": [
            {"id": "h1", "time": "2026-10-30T08:00:00-04:00",
             "source": "X", "title": "Tesla beats"},
            {"id": "h2", "time": "2026-10-30T13:00:00-04:00",
             "source": "Y", "title": "Market rally"},
        ],
        "levels": [],
    }


def _prev(skip: bool = True) -> PreviousV4:
    return PreviousV4(
        time=datetime(2026, 10, 30, 13, 30, tzinfo=UTC),
        action="skip" if skip else "enter",
        option_id=None,
        best_option_id="A",
        confidence=0.4,
        conflicts=("weak momentum",),
        price=Decimal("99.0"),
        checks={"trend_1h": "mixed", "trend_daily": "up",
                "regime_trend": "up", "momentum": "strong"},
        headline_ids=frozenset({"h1"}),
        level_keys=frozenset(),
    )


def test_v4_build_previous_decision_and_since() -> None:
    config = AppConfig().overlay({"universe": {"names": {"T": "Tesla"}}})
    base = _base_snapshot()
    build = build_v4_fields(
        context="candidate",
        base=base,
        price=Decimal("100"),
        reference=Decimal("100"),
        atr_1h=Decimal("1.0"),
        indicators=indicators_from_snapshot("T", base),
        regime=regime_from_snapshot(base, config),
        previous=_prev(),
        names={"T": "Tesla"},
        calendar=_calendar("2026-10-30"),
        config=config,
        cycle_time_ny=datetime(2026, 10, 30, 18, 0, tzinfo=UTC),
    )
    prev = build.fields["previous_decision"]
    assert prev["action"] == "skip"
    assert prev["conflicts"] == ["weak momentum"]
    assert prev["since"]["price_move_atr"] == 1.0
    assert prev["since"]["checks_changed"] == ["trend_1h: mixed → up"]
    assert prev["since"]["new_headlines"] == ["h2"]
    heads = {h["id"]: h for h in build.fields["headlines"]}
    assert heads["h1"]["new"] is False
    assert heads["h2"]["new"] is True
    assert heads["h1"]["about_symbol"] is True
    assert heads["h2"]["about_symbol"] is False
    # newest first
    assert [h["id"] for h in build.fields["headlines"]] == ["h2", "h1"]
    assert build.fields["session"]["minutes_left"] == 120
    assert build.options_column["options_version"] == "v4"


def test_v4_headline_cap_newest_first() -> None:
    config = AppConfig()
    heads = [
        {"id": f"n{i}", "time": f"2026-10-30T{i:02d}:00:00-04:00",
         "source": "X", "title": f"news {i}"}
        for i in range(20)
    ]
    base = {**_base_snapshot(), "headlines": heads}
    build = build_v4_fields(
        context="candidate", base=base, price=Decimal("100"),
        reference=Decimal("100"), atr_1h=Decimal("1.0"),
        indicators=indicators_from_snapshot("T", base),
        regime=regime_from_snapshot(base, config), names={},
        calendar=_calendar("2026-10-30"), config=config,
        cycle_time_ny=datetime(2026, 10, 30, 18, 0, tzinfo=UTC),
    )
    assert len(build.fields["headlines"]) == 12
    assert build.fields["headlines"][0]["id"] == "n19"


def test_v4_stop_groups() -> None:
    config = AppConfig()
    base = _base_snapshot()
    build = build_v4_fields(
        context="candidate", base=base, price=Decimal("100"),
        reference=Decimal("100"), atr_1h=Decimal("1.0"),
        indicators=indicators_from_snapshot("T", base),
        regime=regime_from_snapshot(base, config), names={},
        calendar=_calendar("2026-10-30"), config=config,
        cycle_time_ny=datetime(2026, 10, 30, 18, 0, tzinfo=UTC),
    )
    for opt in build.fields["options"] or []:
        assert "stop_group" in opt
    groups = [o["stop_group"] for o in build.fields["options"]]
    assert len(set(groups)) == len({str(o["stop"]) for o in build.fields["options"]})


def test_v4_level_cap_keeps_option_and_stop_levels() -> None:
    config = AppConfig()
    stored = {
        "levels": [
            {"id": "L1", "distance_atr": 1.0},
            {"id": "L2", "distance_atr": 6.0},
            {"id": "L3", "distance_atr": 8.0},
            {"id": "L4", "distance_atr": 0.5},
        ],
        "options": [{"id": "A", "levels_in_way": [{"id": "L2"}],
                     "stop_level": "L3", "target_level": "L4"}],
    }
    sent = v4_send_snapshot(stored, config)
    kept = {lv["id"] for lv in sent["levels"]}
    # L2 and L3 beyond 4 ATR stay: an option names them.
    assert kept == {"L1", "L2", "L3", "L4"}


def test_v4_level_cap_drops_unnamed_far_levels() -> None:
    config = AppConfig()
    stored = {
        "levels": [
            {"id": "L1", "distance_atr": 1.0},
            {"id": "L9", "distance_atr": 9.0},
        ],
        "options": [{"id": "A", "levels_in_way": [], "stop_level": None,
                     "target_level": None}],
    }
    sent = v4_send_snapshot(stored, config)
    assert [lv["id"] for lv in sent["levels"]] == ["L1"]


def test_checks_at_entry_from_stored_checks() -> None:
    entry = type(
        "D", (),
        {"input_snapshot": {"checks": {"trend_1h": "up", "trend_daily": "up",
                                       "regime_trend": "up",
                                       "momentum": "strong"}}},
    )()
    got = checks_at_entry(entry)
    assert got["trend_1h"] == "up"
    assert got["momentum"] == "strong"


def test_checks_at_entry_computed_for_old_rows() -> None:
    entry = type(
        "D", (),
        {"input_snapshot": {
            "indicators_1h": {"ema20": "99", "ema50": "97",
                              "rs_vs_spy_pct": "1.0", "obv_trend": "up"},
            "indicators_daily": {"close": "98", "sma50": "95",
                                 "sma200": "90"},
            "regime": {"trend": "up"},
            "signal_bar": {"close": "99.5"},
            "price": "100",
        }},
    )()
    got = checks_at_entry(entry)
    assert got == {"trend_1h": "up", "trend_daily": "up",
                   "regime_trend": "up", "momentum": "strong"}


def test_session_time_held() -> None:
    cal = _calendar("2026-10-30", "2026-11-02")
    opened = datetime(2026, 10, 30, 17, 46, tzinfo=UTC)
    now = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
    hours, sessions = session_time_held(opened, now, cal)
    # Fri 13:46–16:00 = 2.23h + Mon 09:30–14:00 = 4.5h → ~6.7h, 2 sessions
    assert sessions == 2
    assert hours is not None and 6.0 < hours < 7.5


def test_v4_config_defaults() -> None:
    v4 = v4_config(AppConfig())
    assert v4.max_headlines == 12
    assert v4.max_level_distance_atr == Decimal("4.0")
    assert v4.flip_min_move_atr == Decimal("0.5")
    assert v4.max_confidence_above_anchor == Decimal("0.30")
    assert v4.checks_gate is False


def test_prompt_spec_v4() -> None:
    spec = prompt_spec("decision.v4")
    assert spec.is_v4
    assert spec.is_v3  # v4 keeps the v3 builder shape
    assert spec.uses_options


def test_v4_schema_field_order() -> None:
    required = prompt_spec("decision.v4").json_schema["schema"]["required"]  # type: ignore[index]
    assert required == [
        "symbol", "conflicts", "data_flags", "thesis", "what_changed",
        "action", "option_id", "best_option_id", "confidence",
        "levels_used", "news_used",
    ]


def test_v4_output_schema_parse() -> None:
    out = ModelOutputV4.model_validate(json.loads(_v4(
        action="enter", option_id="A", best_option_id="A",
        conflicts=[{"text": "x", "outweighed_by": "y"}],
        news_used=[{"id": "h1", "effect": "bullish for demand"}],
    )))
    assert out.conflicts[0].outweighed_by == "y"
    assert out.news_used[0].effect == "bullish for demand"


def test_v4_news_effect_word_limit() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ModelOutputV4.model_validate(json.loads(_v4(
            action="skip", best_option_id="A",
            news_used=[{"id": "h1", "effect": " ".join(["word"] * 13)}],
        )))


def test_held_thesis_fields_in_snapshot() -> None:
    from app.data.models import Bar1h
    from app.decide.v3 import HeldV3

    config = AppConfig()
    held = HeldV3(
        entry=Decimal("100"), qty=Decimal("10"),
        initial_stop=Decimal("97"), current_stop=Decimal("98"),
        target=Decimal("103"),
        opened_at=datetime(2026, 10, 30, 17, 46, tzinfo=UTC),
        entry_thesis=None,
        stop_basis=None,
    )
    base = _base_snapshot()
    # The current 1h bar closed under the fast EMA (mixed) with momentum
    # weak — two adverse turns against the entry checks.
    base["indicators_1h"]["rs_vs_spy_pct"] = "-1.5"
    base["indicators_1h"]["obv_trend"] = "down"
    bar = Bar1h(
        symbol="T",
        start=datetime(2026, 10, 30, 17, 0, tzinfo=UTC),
        end=datetime(2026, 10, 30, 18, 0, tzinfo=UTC),
        open=Decimal("99"), high=Decimal("99.5"), low=Decimal("97.5"),
        close=Decimal("98"), volume=1000, feed="test", complete=True,
    )
    build = build_v4_fields(
        bars_1h=[bar],
        context="held", base=base, price=Decimal("100"),
        reference=Decimal("100"), atr_1h=Decimal("1.0"),
        indicators=indicators_from_snapshot("T", base),
        regime=regime_from_snapshot(base, config), held=held,
        entry_checks={"trend_1h": "up", "trend_daily": "up",
                      "regime_trend": "up", "momentum": "strong"},
        names={}, calendar=_calendar("2026-10-30"), config=config,
        cycle_time_ny=datetime(2026, 10, 30, 18, 0, tzinfo=UTC),
    )
    pos = build.fields["position"]
    assert pos["checks_at_entry"]["trend_1h"] == "up"
    assert pos["thesis_holds"] is False  # trend_1h down + momentum weak
    assert pos["held_session_hours"] is not None
    assert pos["sessions_held"] == 1
