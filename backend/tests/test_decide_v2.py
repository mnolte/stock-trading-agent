"""decision.v2 validation, explanation check and the v1 regression
(docs/SPEC.md, Decision v2)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, cast

from app.config import AppConfig
from app.decide.output import (
    NO_CALL_REASONS,
    ModelOutputV2,
    check_explanation,
    decision_trade_prices,
    validate_model_output_v2,
)
from app.decide.prompt import prompt_spec
from app.decide.snapshot import build_input_snapshot
from app.signals.regime import MarketRegime
from app.signals.snapshot import IndicatorSnapshot

CFG = AppConfig()

_SNAPSHOT: dict[str, Any] = {
    "symbol": "ABC",
    "context": "candidate",
    "price": 100.0,
    "stop_reference_price": "99.80",
    "indicators_1h": {"atr14": 2.0},
    "levels": [
        {"id": "L1", "price": 110.0, "kind": "swing_high", "timeframe": "daily", "time": None},
        {"id": "L2", "price": 104.5, "kind": "swing_high", "timeframe": "1h", "time": None},
        {"id": "L3", "price": 96.0, "kind": "swing_low", "timeframe": "1h", "time": None},
    ],
    "constraints": {
        "atr_1h": 2.0, "entry_limit": 100.05, "stop_atr_min": 1.5,
        "stop_atr_max": 4.0, "min_reward_risk": 1.5,
    },
    "options": [
        {
            "id": "A",
            "stop": 95.8,
            "stop_basis": "0.1 ATR below L3 · 1h swing low 13 Oct",
            "stop_atr": 2.0,
            "stop_level": "L3",
            "target": 108.6,
            "target_basis": "2R",
            "target_level": None,
            "rr": 2.02,
            "risk": 4.25,
            "levels_in_way": ["L2"],
        },
        {
            "id": "B",
            "stop": 95.8,
            "stop_basis": "0.1 ATR below L3 · 1h swing low 13 Oct",
            "stop_atr": 2.0,
            "stop_level": "L3",
            "target": 112.85,
            "target_basis": "3R",
            "target_level": None,
            "rr": 3.02,
            "risk": 4.25,
            "levels_in_way": ["L2", "L1"],
        },
    ],
}


def _v2(
    action: str = "enter", option_id: str | None = "A",
    thesis: str = "breakout above L2 at 104.50",
    levels_used: list[str] | None = None, confidence: float = 0.62,
) -> str:
    return json.dumps(
        {
            "symbol": "ABC",
            "action": action,
            "option_id": option_id,
            "confidence": confidence,
            "thesis": thesis,
            "levels_used": levels_used if levels_used is not None else ["L3"],
            "risks": [],
            "news_used": [],
        }
    )


class TestRegistry:
    def test_v2_spec(self) -> None:
        spec = prompt_spec("decision.v2")
        assert spec.uses_options
        schema = cast("dict[str, Any]", spec.json_schema)
        assert "option_id" in schema["schema"]["properties"]

    def test_v1_spec_unchanged(self) -> None:
        spec = prompt_spec("decision.v1")
        assert not spec.uses_options
        schema = cast("dict[str, Any]", spec.json_schema)
        assert "stop_price" in schema["schema"]["properties"]

    def test_unknown_version_gets_v1(self) -> None:
        assert prompt_spec("decision.v99").uses_options is False


class TestV2Validation:
    def test_valid_enter(self) -> None:
        v = validate_model_output_v2(
            _v2(), symbol="ABC", context="candidate", snapshot=_SNAPSHOT,
            atr_1h=Decimal("2"), current_stop=None, config=CFG,
        )
        assert v.valid and v.final_action == "enter"
        assert v.chosen_option is not None and v.chosen_option["id"] == "A"
        assert v.explanation is not None and v.explanation.passed

    def test_invalid_option_id_is_skip(self) -> None:
        v = validate_model_output_v2(
            _v2(option_id="Z"), symbol="ABC", context="candidate",
            snapshot=_SNAPSHOT, atr_1h=Decimal("2"), current_stop=None,
            config=CFG,
        )
        assert not v.valid and v.final_action == "skip"
        assert "option_id" in (v.error or "")

    def test_missing_option_id_is_skip(self) -> None:
        v = validate_model_output_v2(
            _v2(option_id=None), symbol="ABC", context="candidate",
            snapshot=_SNAPSHOT, atr_1h=Decimal("2"), current_stop=None,
            config=CFG,
        )
        assert not v.valid and v.final_action == "skip"

    def test_invalid_option_id_is_hold_for_held(self) -> None:
        snap = {**_SNAPSHOT, "context": "held", "options": [],
                "stop_options": [{"id": "T1", "stop": 97.0, "basis": "trail",
                                   "locks_profit": False, "r_at_stop": -0.5,
                                   "level": None}]}
        v = validate_model_output_v2(
            _v2(action="tighten_stop", option_id="T9"),
            symbol="ABC", context="held", snapshot=snap,
            atr_1h=Decimal("2"), current_stop=Decimal("94"), config=CFG,
        )
        assert not v.valid and v.final_action == "hold"

    def test_price_fields_rejected(self) -> None:
        raw = json.loads(_v2())
        raw["stop_price"] = 95.8
        v = validate_model_output_v2(
            json.dumps(raw), symbol="ABC", context="candidate",
            snapshot=_SNAPSHOT, atr_1h=Decimal("2"), current_stop=None,
            config=CFG,
        )
        assert not v.valid and v.final_action == "skip"

    def test_low_confidence_skips(self) -> None:
        v = validate_model_output_v2(
            _v2(confidence=0.3), symbol="ABC", context="candidate",
            snapshot=_SNAPSHOT, atr_1h=Decimal("2"), current_stop=None,
            config=CFG,
        )
        assert v.valid and v.final_action == "skip"

    def test_exit_has_no_option(self) -> None:
        snap = {**_SNAPSHOT, "context": "held", "stop_options": []}
        v = validate_model_output_v2(
            _v2(action="exit", option_id=None),
            symbol="ABC", context="held", snapshot=snap,
            atr_1h=Decimal("2"), current_stop=Decimal("94"), config=CFG,
        )
        assert v.valid and v.final_action == "exit"

    def test_no_options_reason_enumerated(self) -> None:
        assert "no_options" in NO_CALL_REASONS
        assert "reference_missing" in NO_CALL_REASONS


class TestExplanationCheck:
    def _out(
        self, thesis: str = "", levels: list[str] | None = None,
        risks: list[str] | None = None,
    ) -> ModelOutputV2:
        return ModelOutputV2(
            symbol="ABC", action="enter", option_id="A", confidence=0.6,
            thesis=thesis, levels_used=levels or [], risks=risks or [],
        )

    def test_pass(self) -> None:
        r = check_explanation(
            self._out("stop sits at 95.80 below L3", levels=["L3"]),
            _SNAPSHOT, _SNAPSHOT["options"][0],
        )
        assert r.passed

    def test_unknown_level_id(self) -> None:
        r = check_explanation(
            self._out("clean", levels=["L9"]), _SNAPSHOT, _SNAPSHOT["options"][0],
        )
        assert not r.passed and r.bad_ids == ["L9"]

    def test_invented_decimal(self) -> None:
        r = check_explanation(
            self._out("momentum at 88.44"), _SNAPSHOT, _SNAPSHOT["options"][0],
        )
        assert not r.passed and "88.44" in r.bad_numbers

    def test_whole_numbers_ignored(self) -> None:
        r = check_explanation(
            self._out("14 bars up, 2 sessions flat", levels=["L3"]),
            _SNAPSHOT, _SNAPSHOT["options"][0],
        )
        assert r.passed

    def test_decimals_matching_snapshot_pass(self) -> None:
        r = check_explanation(
            self._out("VWAP near 100.00 and target 108.60",
                      levels=["L1"], risks=["headline-driven"]),
            _SNAPSHOT, _SNAPSHOT["options"][0],
        )
        assert r.passed


class TestTradePrices:
    def test_v2_resolves_chosen_option(self) -> None:
        stop, target = decision_trade_prices(
            {"option_id": "A"}, {"options": _SNAPSHOT["options"]}, "A",
        )
        assert stop == Decimal("95.8") and target == Decimal("108.6")

    def test_v1_prices_unchanged(self) -> None:
        stop, target = decision_trade_prices(
            {"stop_price": 95.0, "target_price": 110.0}, None, None,
        )
        assert stop == Decimal("95.0") and target == Decimal("110.0")

    def test_unknown_id_yields_none(self) -> None:
        stop, target = decision_trade_prices(
            {"option_id": "Z"}, {"options": _SNAPSHOT["options"]}, "Z",
        )
        assert stop is None and target is None


def test_v1_snapshot_keys_unchanged() -> None:
    """A v1-shaped snapshot carries no v2 keys (registry gate keeps the
    builder out of the v1 path)."""
    snap = build_input_snapshot(
        symbol="ABC", context="candidate",
        cycle_time_ny=__import__("datetime").datetime(2026, 10, 15, 14, 0),
        price=Decimal("100"),
        regime=MarketRegime(
            trend="up", volatility="normal", key="up_normal",
            label="Up · normal vol", blocks_entries=False, atr_percentile=None,
        ),
        snapshot=IndicatorSnapshot(
            symbol="ABC",
            price=Decimal("100"), ema_fast=None, ema_slow=None, rsi=None,
            atr_1h=None, session_vwap=None, obv_trend=None, rs_vs_spy_pct=None,
            daily_sma_fast=None, daily_sma_slow=None, daily_atr_percentile=None,
            daily_close=None, opening_gap_pct=None,
            days_to_earnings=None, earnings_available=True,
        ),
        bars_1h=[], position=None, news=[],
    )
    assert "options" not in snap and "constraints" not in snap
    assert "stop_options" not in snap
    assert isinstance(snap["levels"], (dict, str))  # v1's swing dict, not a list
