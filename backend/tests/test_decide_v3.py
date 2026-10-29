"""decision.v3: snapshot builder, room filter, code checks, held context.

The fixtures are the stored v2 inputs of the 28 Oct 2026 SBUX and ACN
decisions (docs/SPEC.md, Decision v3; PR 30 kickoff tests).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from app.config import AppConfig
from app.data.models import Bar1h, Bar5m, CalendarDay, DailyBar
from app.decide.levels import Level, options_config
from app.decide.output import (
    ModelOutputV3,
    check_explanation_v3,
    validate_model_output_v3,
)
from app.decide.v3 import (
    HeldV3,
    StopBasisInfo,
    V3Build,
    V3Config,
    build_stop_options_v3,
    build_v3_fields,
    merge_levels,
    room_check,
    room_recheck_failure,
    scheduled_exit_text,
    v3_send_snapshot,
)
from app.market.calendar import StaticCalendarProvider
from app.shadows.runner import indicators_from_snapshot, regime_from_snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "v3"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _dec(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _load_bars(name: str) -> tuple[list[Bar1h], list[DailyBar], list[Bar5m]]:
    raw = _fixture(name)
    b1 = [
        Bar1h(
            symbol="T",
            start=datetime.fromisoformat(b["start"]),
            end=datetime.fromisoformat(b["end"]),
            open=Decimal(b["open"]),
            high=Decimal(b["high"]),
            low=Decimal(b["low"]),
            close=Decimal(b["close"]),
            volume=b["volume"],
            feed="test",
            complete=True,
        )
        for b in raw["bars_1h"]
    ]
    daily = [
        DailyBar(
            symbol="T",
            date=date.fromisoformat(b["date"]),
            open=Decimal(b["open"]),
            high=Decimal(b["high"]),
            low=Decimal(b["low"]),
            close=Decimal(b["close"]),
            volume=b["volume"],
        )
        for b in raw["daily"]
    ]
    m5 = [
        Bar5m(
            symbol="T",
            start=datetime.fromisoformat(b["start"]),
            open=Decimal(b["open"]),
            high=Decimal(b["high"]),
            low=Decimal(b["low"]),
            close=Decimal(b["close"]),
            volume=b["volume"],
            feed="test",
        )
        for b in raw["today_5m"]
    ]
    return b1, daily, m5


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


def _build(
    fixture: str,
    bars: str,
    *,
    context: str,
    held: HeldV3 | None = None,
    earnings: tuple[date, str] | None = None,
    overlay: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], V3Build, AppConfig]:
    """build_v3_fields on a stored v2 input and its cached bars."""
    snap = _fixture(fixture)
    b1, daily, m5 = _load_bars(bars)
    config = AppConfig().overlay(overlay or {})
    days = _calendar(
        *[str(b.date) for b in daily],
        str(daily[-1].date + timedelta(days=1)),
    )
    build = build_v3_fields(
        context=context,
        base=snap,
        price=_dec(snap.get("price")),
        reference=_dec(snap.get("stop_reference_price")),
        atr_1h=_dec((snap.get("indicators_1h") or {}).get("atr14")),
        indicators=indicators_from_snapshot(str(snap["symbol"]), snap),
        regime=regime_from_snapshot(snap, config),
        bars_1h=b1,
        daily=daily,
        today_5m=m5,
        earnings=earnings,
        held=held,
        calendar=days,
        config=config,
        cycle_time_ny=datetime.fromisoformat(str(snap["time_ny"])),
    )
    return snap, build, config


def _v3_enter(
    option: dict[str, Any],
    *,
    confidence: float,
    data_flags: list[str] | None = None,
    thesis: str = "thesis",
) -> str:
    return json.dumps(
        {
            "symbol": "T",
            "conflicts": [],
            "data_flags": data_flags or [],
            "thesis": thesis,
            "action": "enter",
            "option_id": option["id"],
            "confidence": confidence,
            "levels_used": [],
            "news_used": [],
        }
    )


def _v3_option() -> dict[str, Any]:
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
    }


def _v3_snapshot() -> dict[str, Any]:
    return {
        "symbol": "T",
        "context": "candidate",
        "price": 100.0,
        "stop_reference_price": 100.0,
        "levels": [
            {"id": "L1", "price": 104.0, "kind": "swing_high",
             "timeframe": "1h", "time": None, "distance_atr": 4.0},
            {"id": "L2", "price": 99.0, "kind": "swing_low",
             "timeframe": "1h", "time": None, "distance_atr": -1.0},
        ],
        "checks": {"room_atr": 2.0, "room_level": "L1"},
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
        "options": [_v3_option()],
    }


# ----------------------------------------------------------------------
# SBUX candidate: no room → no_options, no model call


def test_sbux_room_no_options() -> None:
    snap, v, _ = _build(
        "sbux_candidate.json", "sbux_candidate_bars.json", context="candidate"
    )
    assert v.fields["options"] == []
    assert v.reason == (
        "room: L7 1h swing high 0.31 ATR above the limit (min 1.0)"
    )
    checks = v.fields["checks"]
    assert checks["room_level"] == "L7"
    assert checks["room_atr"] == 0.31
    # trend_1h: close 103.165 > EMA20 > EMA50 → up
    assert checks["trend_1h"] == "up"
    # trend_daily: close 102.68 > SMA50 99.31 < SMA200 99.64 → mixed
    assert checks["trend_daily"] == "mixed"
    # rs 0.48 > 0 and OBV up → strong
    assert checks["momentum"] == "strong"


def test_room_override_keeps_option() -> None:
    """min_room_atr 0.3 keeps an option that the default 1.0 removes."""
    _, removed, _ = _build(
        "sbux_candidate.json", "sbux_candidate_bars.json", context="candidate"
    )
    assert removed.fields["options"] == []
    snap, kept, _ = _build(
        "sbux_candidate.json",
        "sbux_candidate_bars.json",
        context="candidate",
        overlay={"decision": {"v3": {"min_room_atr": 0.3}}},
    )
    assert kept.reason is None
    options = kept.fields["options"]
    assert options
    for option in options:
        assert "breakeven_confidence" in option
        assert option["rr"] is not None
        for lv in option["levels_in_way"]:
            assert set(lv) == {"id", "distance_atr"}
            assert isinstance(lv["distance_atr"], float)
    breakeven = options[0]["breakeven_confidence"]
    assert breakeven == pytest.approx(round(1 / (1 + options[0]["rr"]), 2))


def test_vwap_and_prior_close_do_not_count_for_room() -> None:
    levels = [
        Level(id="L1", price=Decimal("101.5"), kind="swing_high",
              timeframe="1h", time=None),
        Level(id="L2", price=Decimal("100.2"), kind="vwap",
              timeframe="session", time=None),
        Level(id="L3", price=Decimal("100.2"), kind="prior_session_close",
              timeframe="session", time=None),
    ]
    level, distance = room_check(
        levels, Decimal("100.05"), Decimal("0.5"),
        V3Config().room_level_kinds,
    )
    assert level is not None and level.id == "L1"
    assert distance == pytest.approx(Decimal("2.9"), abs=Decimal("0.001"))


def test_merged_levels_keep_survivor_id() -> None:
    levels = [
        Level(id="L1", price=Decimal("100.0"), kind="swing_high",
              timeframe="1h", time=None),
        Level(id="L2", price=Decimal("100.01"), kind="session_high",
              timeframe="session", time=None),
        Level(id="L3", price=Decimal("99.0"), kind="swing_low",
              timeframe="1h", time=None),
    ]
    merged = merge_levels(levels, Decimal("0.5"), Decimal("0.05"))
    assert [lv.id for lv in merged] == ["L1", "L3"]
    assert list(merged[0].kinds or []) == ["swing_high", "session_high"]


def test_send_snapshot_strips_provenance() -> None:
    snap, v, _ = _build(
        "sbux_candidate.json", "sbux_candidate_bars.json", context="candidate"
    )
    stored = {**snap, **v.fields}
    sent = v3_send_snapshot(stored)
    for key in ("stop_reference_price", "last_bar_close", "last_bar_time",
                "days_to_earnings", "signal_bar_time", "news_status"):
        assert key not in sent
        assert key in stored
    quote = sent["quote"]
    assert set(quote) == {"bid", "ask", "plausible", "spread_unknown"}
    assert quote["plausible"] is False
    assert quote["bid"] is None and quote["ask"] is None
    assert stored["quote"]["quote_timestamp"] is not None


# ----------------------------------------------------------------------
# code checks: confidence gate, data flags, order-time room


def test_confidence_gate_edge_too_small() -> None:
    snap = _v3_snapshot()
    result = validate_model_output_v3(
        _v3_enter(_v3_option(), confidence=0.40),
        symbol="T",
        context="candidate",
        snapshot=snap,
        atr_1h=Decimal("1.0"),
        current_stop=None,
        config=AppConfig(),
    )
    # break-even 0.33 + 0.10 = 0.43 < min 0.55; 0.40 < 0.55 → edge_too_small
    assert result.final_action == "skip"
    assert result.reason == "edge_too_small"
    passing = validate_model_output_v3(
        _v3_enter(_v3_option(), confidence=0.60),
        symbol="T",
        context="candidate",
        snapshot=snap,
        atr_1h=Decimal("1.0"),
        current_stop=None,
        config=AppConfig(),
    )
    assert passing.final_action == "enter"
    assert passing.valid


def test_data_flag_skips_enter() -> None:
    snap = _v3_snapshot()
    result = validate_model_output_v3(
        _v3_enter(
            _v3_option(), confidence=0.7,
            data_flags=["earnings_date_conflict"],
        ),
        symbol="T",
        context="candidate",
        snapshot=snap,
        atr_1h=Decimal("1.0"),
        current_stop=None,
        config=AppConfig(),
    )
    assert result.final_action == "skip"
    assert result.reason == "data_flag"
    assert isinstance(result.output, ModelOutputV3)
    assert result.output.data_flags == ["earnings_date_conflict"]


def test_data_flag_keeps_held_action() -> None:
    snap = _v3_snapshot()
    snap["context"] = "held"
    snap["position"] = {
        "entry": 100.0, "stop": 98.0, "initial_stop": 98.0,
        "stop_level": "L2", "target": 103.0,
    }
    snap["stop_options"] = []
    raw = json.dumps({
        "symbol": "T", "conflicts": [], "data_flags": ["price_conflict"],
        "thesis": "holding", "action": "hold", "option_id": None,
        "confidence": 0.6, "levels_used": [], "news_used": [],
    })
    result = validate_model_output_v3(
        raw,
        symbol="T",
        context="held",
        snapshot=snap,
        atr_1h=Decimal("1.0"),
        current_stop=Decimal("98.0"),
        config=AppConfig(),
    )
    assert result.final_action == "hold"
    assert isinstance(result.output, ModelOutputV3)
    assert result.output.data_flags == ["price_conflict"]


def test_order_time_room_recheck() -> None:
    levels = [
        {"id": "L1", "price": 100.9, "kind": "swing_high",
         "timeframe": "1h", "time": None},
        {"id": "L2", "price": 99.0, "kind": "swing_low",
         "timeframe": "1h", "time": None},
    ]
    cfg = AppConfig()
    # A fresh limit at 100.0 leaves 0.9 ATR (< 1.0) → room failure.
    reason = room_recheck_failure(
        levels, Decimal("100.0"), Decimal("1.0"),
        __import__("app.decide.v3", fromlist=["v3_config"]).v3_config(cfg),
    )
    assert reason is not None and reason.startswith("room:")
    ok = room_recheck_failure(
        levels, Decimal("99.8"), Decimal("1.0"),
        __import__("app.decide.v3", fromlist=["v3_config"]).v3_config(cfg),
    )
    assert ok is None


def test_explanation_check_uses_rounded_snapshot() -> None:
    snap = _v3_snapshot()
    good = ModelOutputV3(
        symbol="T", thesis="0.31 ATR of room", action="skip",
        confidence=0.5, levels_used=[], conflicts=[], data_flags=[],
        news_used=[],
    )
    snap["checks"]["room_atr"] = 0.31
    result = check_explanation_v3(good, snap, None)
    assert result.passed
    bad = ModelOutputV3(
        symbol="T", thesis="0.312 ATR of room", action="skip",
        confidence=0.5, levels_used=[], conflicts=[], data_flags=[],
        news_used=[],
    )
    assert not check_explanation_v3(bad, snap, None).passed


# ----------------------------------------------------------------------
# held positions


def _sbux_held() -> HeldV3:
    return HeldV3(
        entry=Decimal("103.15"),
        qty=19,
        initial_stop=Decimal("101.74"),
        current_stop=Decimal("101.74"),
        target=Decimal("106.10"),
        opened_at=datetime(2026, 10, 28, 10, 47, tzinfo=UTC),
        entry_thesis="entry thesis",
        stop_basis=StopBasisInfo(basis="2 × ATR"),
        earnings=(date(2026, 10, 29), "after_close"),
    )


def test_sbux_held_stop_options_and_position() -> None:
    _, v, config = _build(
        "sbux_held.json",
        "sbux_held_bars.json",
        context="held",
        held=_sbux_held(),
        earnings=(date(2026, 10, 29), "after_close"),
    )
    offered = v.fields["stop_options"]
    dropped = v.options_column["dropped_stop_options"]
    offered_ids = {o["id"] for o in offered}
    dropped_by_id = {o["id"]: o for o in dropped}
    # T1 swing low ~101.77: 0.05 ATR move → dropped
    assert "T1" in dropped_by_id
    assert "moves the stop" in dropped_by_id["T1"]["drop_reason"]
    # T2 break-even: +0.49R < 1.0R → dropped
    assert "T2" in dropped_by_id
    assert "break-even from +1.0R" in dropped_by_id["T2"]["drop_reason"]
    # T3 trail: offered with locked_r −0.07 and distance_atr 1.21
    assert "T3" in offered_ids
    t3 = next(o for o in offered if o["id"] == "T3")
    assert t3["locked_r"] == pytest.approx(-0.07, abs=0.01)
    assert t3["distance_atr"] == pytest.approx(1.21, abs=0.01)

    pos = v.fields["position"]
    assert pos["scheduled_exit"] == "earnings exit Thu 29 Oct 15:46 NY"
    assert pos["invalidation"] == "below 101.74: 2 × ATR stop"
    assert pos["entry_thesis"] == "entry thesis"
    assert pos["r_now"] == pytest.approx(0.49, abs=0.01)


def test_locks_profit_boundaries() -> None:
    config = AppConfig()
    cfg = options_config(config)
    atr = Decimal("0.5")
    # break-even stop exactly at entry → locks_profit False
    options = build_stop_options_v3(
        bars_1h=(),
        entry=Decimal("100"),
        opened_at=None,
        current_stop=Decimal("98"),
        initial_stop=Decimal("98"),
        reference=Decimal("105"),
        price=Decimal("105"),
        atr_1h=atr,
        cfg=cfg,
        v3=V3Config(),
    )
    be = next(o for o in options if o.basis == "break-even")
    assert be.stop == Decimal("100")
    assert be.locks_profit is False
    # a stop just above the entry → True (a trail at 100.5 for entry 99.9)
    bars = [
        Bar1h(
            symbol="T",
            start=datetime(2026, 10, 28, 10, 30, tzinfo=UTC),
            end=datetime(2026, 10, 28, 11, 30, tzinfo=UTC),
            open=Decimal("100"), high=Decimal("102"), low=Decimal("99"),
            close=Decimal("101"), volume=1, feed="test", complete=True,
        )
    ]
    options2 = build_stop_options_v3(
        bars_1h=bars,
        entry=Decimal("99.9"),
        opened_at=None,
        current_stop=Decimal("98"),
        initial_stop=Decimal("98"),
        reference=Decimal("105"),
        price=Decimal("105"),
        atr_1h=atr,
        cfg=cfg,
        v3=V3Config(),
    )
    trail = next(o for o in options2 if o.basis != "break-even")
    assert trail.stop > Decimal("99.9")
    assert trail.locks_profit is True


def _claim_snapshot() -> dict[str, Any]:
    return {
        "symbol": "ACN",
        "context": "held",
        "position": {
            "entry": 243.78,
            "stop": 237.91,
            "initial_stop": 237.91,
            "target": 254.12,
            "stop_level": "L14",
        },
        "stop_options": [],
        "levels": [],
    }


def _v3_held(action: str, thesis: str, option_id: str | None = None) -> str:
    return json.dumps(
        {
            "symbol": "ACN", "conflicts": [], "data_flags": [],
            "thesis": thesis, "action": action, "option_id": option_id,
            "confidence": 0.6, "levels_used": [], "news_used": [],
        }
    )


def test_locks_in_claim_unsupported() -> None:
    """'locks in ... profit' with the stop at locked_r 0 → unsupported_claim."""
    snap = _claim_snapshot()
    snap["position"]["stop"] = 243.78  # stop exactly at entry → locked_r 0
    result = validate_model_output_v3(
        _v3_held("hold", "the stop locks in the current 0.49 R profit"),
        symbol="ACN",
        context="held",
        snapshot=snap,
        atr_1h=Decimal("3.0"),
        current_stop=Decimal("243.78"),
        config=AppConfig(),
    )
    assert result.claims is not None and not result.claims.passed
    assert result.final_action == "hold"  # the decision stands


def test_level_claim_wrong_and_right() -> None:
    snap = _claim_snapshot()
    wrong = validate_model_output_v3(
        _v3_held("hold", "the stop is below L13"),
        symbol="ACN",
        context="held",
        snapshot=snap,
        atr_1h=Decimal("3.0"),
        current_stop=Decimal("237.91"),
        config=AppConfig(),
    )
    assert wrong.claims is not None and not wrong.claims.passed
    right = validate_model_output_v3(
        _v3_held("hold", "the stop is below L14"),
        symbol="ACN",
        context="held",
        snapshot=snap,
        atr_1h=Decimal("3.0"),
        current_stop=Decimal("237.91"),
        config=AppConfig(),
    )
    assert right.claims is not None and right.claims.passed


def _acn_held() -> HeldV3:
    entry = _fixture("acn_entry.json")
    pos = _fixture("acn_position.json")
    option = next(
        o for o in entry["options"]["options"] if o["id"] == entry["chosen_option"]
    )
    level = next(
        lv
        for lv in entry["levels"]["levels"]
        if lv["id"] == option["stop_level"]
    )
    info = StopBasisInfo(
        basis=option["stop_basis"],
        level_kind=level["kind"],
        level_timeframe=level["timeframe"],
        level_time=datetime.fromisoformat(level["time"]),
        level_price=Decimal(str(level["price"])),
    )
    return HeldV3(
        entry=_dec(pos["entry"]),
        qty=pos["qty"],
        initial_stop=_dec(pos["initial_stop"]),
        current_stop=_dec("237.91"),
        target=_dec(pos["target"]),
        opened_at=datetime.fromisoformat(pos["opened_at"]),
        entry_thesis=entry["thesis"],
        stop_basis=info,
    )


def test_acn_held_position_facts() -> None:
    _, v, _ = _build(
        "acn_held.json",
        "acn_held_bars.json",
        context="held",
        held=_acn_held(),
    )
    pos = v.fields["position"]
    # stop 5.83 and target 10.38 from 243.74 → 0.36
    assert pos["neutral_probability"] == 0.36
    assert "20 Oct 12:30 1h swing low" in pos["invalidation"]
    assert "(238.22)" in pos["invalidation"]
    assert pos["stop_level"] == "L14"
    checks = v.fields["checks"]
    assert checks["momentum"] == "weak"  # rs −3.82, OBV down
    merged = next(
        lv for lv in v.fields["levels"] if lv["price"] == 246.79
    )
    assert merged["kinds"] == ["swing_high", "session_high"]
    assert merged["id"] == "L5"


def test_scheduled_exit_text() -> None:
    config = AppConfig()
    text = scheduled_exit_text(
        (date(2026, 10, 29), "after_close"),
        datetime(2026, 10, 28, 14, 46, tzinfo=UTC),
        calendar=_calendar("2026-10-28", "2026-10-29"),
        config=config,
    )
    assert text == "earnings exit Thu 29 Oct 15:46 NY"


def test_v2_builder_output_unchanged() -> None:
    """The v2 builder still emits the stored shape: same option ids, same
    level ids in the way, prices unchanged (cached bars rebuild to the
    same values the live run saw)."""
    from app.decide.v2inputs import build_v2_fields

    snap = _fixture("sbux_candidate.json")
    b1, daily, m5 = _load_bars("sbux_candidate_bars.json")
    build = build_v2_fields(
        context="candidate",
        bars_1h=b1,
        daily=daily,
        today_5m=m5,
        price=_dec(snap.get("price")),
        reference=_dec(snap.get("stop_reference_price")),
        atr_1h=_dec((snap.get("indicators_1h") or {}).get("atr14")),
        config=AppConfig(),
    )
    built = build.fields["options"]
    stored = snap["options"]
    assert [o["id"] for o in built] == [o["id"] for o in stored]
    for new, old in zip(built, stored, strict=True):
        assert new["levels_in_way"] == old["levels_in_way"]
        for key in ("stop", "target", "risk", "rr"):
            assert new[key] == pytest.approx(old[key], abs=0.01)
    assert [lv["id"] for lv in build.fields["levels"]] == [
        lv["id"] for lv in snap["levels"]
    ]
