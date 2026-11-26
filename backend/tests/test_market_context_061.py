"""Focused tests for the item-061 market-context analysis script."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from scripts.analysis.market_context_061 import (
    Bar1d,
    Bar5,
    OptionRow,
    bootstrap_mean_range,
    conflict_pattern_hits,
    load_earnings,
    merge_completed_daily,
    render_holding_rules,
    simulate_counterfactual_hold,
    tercile_edges,
)

D = Decimal


def dt(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def bar(at: datetime, open_: str, high: str, low: str, close: str) -> Bar5:
    return Bar5(at, D(open_), D(high), D(low), D(close))


def test_bootstrap_mean_range_is_deterministic_and_seeded_per_cell() -> None:
    values = [-1.0, -0.5, 0.0, 1.0, 2.0]
    first = bootstrap_mean_range(values)
    second = bootstrap_mean_range(values)
    assert first == second
    assert first is not None
    assert first[0] == sum(values) / len(values)
    assert bootstrap_mean_range([1.25]) == (1.25, 1.25, 1.25)


def test_conflict_patterns_match_time_close_and_overnight_text() -> None:
    hits = conflict_pattern_hits(
        {
            "conflicts": [
                {"text": "Only 14 minutes left in session for the target to be reached."},
                {"text": "The market close is near."},
                "An overnight hold adds gap risk.",
            ]
        }
    )
    assert hits == {"time left", "the close", "overnight hold"}
    assert conflict_pattern_hits({"conflicts": [{"text": "Earnings are tomorrow."}]}) == set()


def test_counterfactual_exit_gap_through_stop_fills_at_open() -> None:
    decision_day = date(2026, 11, 16)
    bars5 = [
        bar(dt(decision_day, 20, 50), "99", "100", "99", "99.5"),
        bar(dt(date(2026, 11, 17), 14, 30), "92", "96", "91", "95"),
    ]
    daily = [
        Bar1d(date(2026, 11, 17), D("92"), D("95")),
        Bar1d(date(2026, 11, 18), D("96"), D("97")),
        Bar1d(date(2026, 11, 19), D("97"), D("98")),
        Bar1d(date(2026, 11, 20), D("98"), D("99")),
        Bar1d(date(2026, 11, 23), D("99"), D("101")),
    ]
    result = simulate_counterfactual_hold(
        entry=D("100"),
        initial_stop=D("90"),
        stop=D("95"),
        target=D("110"),
        decision_time=dt(decision_day, 20, 46),
        decision_day=decision_day,
        bars5=bars5,
        daily=daily,
    )
    assert result.touch.outcome == "stop"
    assert result.touch.price == D("92")
    assert result.next_open_r == -0.8
    assert result.next_close_r == -0.8
    assert result.five_session_r == -0.8
    assert result.status == "complete"


def test_counterfactual_exit_target_gap_supersedes_later_horizons() -> None:
    decision_day = date(2026, 11, 16)
    bars5 = [bar(dt(date(2026, 11, 17), 14, 30), "106", "107", "104", "105")]
    daily = [Bar1d(date(2026, 11, 17), D("106"), D("105"))]
    result = simulate_counterfactual_hold(
        entry=D("100"),
        initial_stop=D("90"),
        stop=D("95"),
        target=D("105"),
        decision_time=dt(decision_day, 20, 46),
        decision_day=decision_day,
        bars5=bars5,
        daily=daily,
    )
    assert result.touch.outcome == "target"
    assert result.touch.price == D("106")
    assert result.next_open_r == 0.6
    assert result.next_close_r == 0.6
    assert result.five_session_r is None
    assert result.status == "pending"


def test_counterfactual_exit_neither_uses_fifth_session_close() -> None:
    decision_day = date(2026, 11, 16)
    bars5 = [bar(dt(date(2026, 11, 17), 14, 30), "101", "103", "99", "102")]
    daily = [
        Bar1d(date(2026, 11, 17), D("101"), D("102")),
        Bar1d(date(2026, 11, 18), D("102"), D("103")),
        Bar1d(date(2026, 11, 19), D("103"), D("104")),
        Bar1d(date(2026, 11, 20), D("104"), D("105")),
        Bar1d(date(2026, 11, 23), D("105"), D("108")),
    ]
    result = simulate_counterfactual_hold(
        entry=D("100"),
        initial_stop=D("90"),
        stop=D("95"),
        target=D("112"),
        decision_time=dt(decision_day, 20, 46),
        decision_day=decision_day,
        bars5=bars5,
        daily=daily,
    )
    assert result.touch.outcome == "neither"
    assert result.next_open_r == 0.1
    assert result.next_close_r == 0.2
    assert result.five_session_r == 0.8
    assert result.status == "complete"


def test_counterfactual_exit_without_a_next_session_is_pending() -> None:
    decision_day = date(2026, 11, 16)
    result = simulate_counterfactual_hold(
        entry=D("100"),
        initial_stop=D("90"),
        stop=D("95"),
        target=D("110"),
        decision_time=dt(decision_day, 20, 46),
        decision_day=decision_day,
        bars5=[],
        daily=[],
    )
    assert result.status == "pending"
    assert result.next_open_r is None


def test_merge_completed_daily_synthesizes_only_a_complete_regular_session() -> None:
    day = date(2026, 11, 17)
    start = dt(day, 14, 30)
    complete = [
        bar(start + timedelta(minutes=5 * i), "100", "101", "99", str(100 + i / 100))
        for i in range(78)
    ]
    merged = merge_completed_daily([], complete)
    assert merged == [Bar1d(day, D("100"), D("100.77"))]

    partial = complete[:73]
    assert merge_completed_daily([], partial) == []

    stored = [Bar1d(day, D("99"), D("101"))]
    assert merge_completed_daily(stored, complete) == stored


def test_earnings_date_stays_on_its_calendar_day() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE earnings (symbol TEXT, date TEXT, time_of_day TEXT)")
    conn.execute("INSERT INTO earnings VALUES ('ABC', '2026-11-18 00:00:00.000000', 'before_open')")
    assert load_earnings(conn) == {("ABC", date(2026, 11, 18))}
    conn.close()


def test_tercile_edges_split_a_sample_in_thirds() -> None:
    assert tercile_edges([float(i) for i in range(90)]) == pytest.approx((89 * 1 / 3, 89 * 2 / 3))
    assert tercile_edges([1.0, 2.0]) is None


def _option(cycle_time: str, r: float) -> OptionRow:
    return OptionRow(
        source_kind="live",
        source_label="live v6",
        prompt_version="decision.v6",
        decision_id=1,
        symbol="ABC",
        cycle_time=cycle_time,
        session_day=date(2026, 11, 11),
        final_action="skip",
        option_id="A",
        touch="stop",
        r_made=r,
        dims={},
    )


def _time_rule_line(late_r: float, early_r: float) -> str:
    options = [_option("15:46", late_r) for _ in range(20)]
    options += [_option("10:46", early_r) for _ in range(20)]
    return next(
        line for line in render_holding_rules([], options) if line.startswith("1. **Time-left")
    )


def test_time_left_rule_is_inconclusive_below_the_evidence_gap() -> None:
    line = _time_rule_line(-0.70, -0.75)
    assert "inconclusive" in line
    assert "no reason to skip" in line


def test_time_left_rule_removal_needs_late_skips_to_be_less_costly() -> None:
    line = _time_rule_line(-0.50, -0.75)
    assert "supported for removal" in line


def test_time_left_rule_is_kept_when_late_skips_are_much_worse() -> None:
    line = _time_rule_line(-1.00, -0.75)
    assert "supported for keeping" in line
    assert "overnight hold" in line
