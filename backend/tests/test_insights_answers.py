"""The Insights answer sentences and sample states (item 075).

Fixture snapshots are plain dicts in the shape ``compute_insights``
returns, so every branch — including under 10 trades — is checked by
hand.
"""

from __future__ import annotations

from typing import Any

from app.insights.answers import (
    answer_conditions,
    answer_costs,
    answer_edge,
    answer_stands_out,
    answer_trades_lost,
    compute_answers,
    with_sample_states,
)


def snapshot(**over: Any) -> dict[str, Any]:
    """A mid-size snapshot: enough trades for every section."""
    snap: dict[str, Any] = {
        "trades": 112,
        "headline": {
            "expectancy": {
                "avg_r": "0.21",
                "lo": "0.02",
                "hi": "0.40",
                "n": 112,
            },
            "edge_over_spy": {"pts": "1.70"},
            "beta": {"beta": "0.31", "correlation": "0.55", "sessions": 58},
            "model_cost": {"per_trade_usd": "0.13", "total_usd": "14.80"},
        },
        "funnel": {
            "steps": [
                {"key": "symbol_checks", "count": 24708, "losses": []},
                {
                    "key": "rules",
                    "count": 3410,
                    "losses": [{"label": "rules", "count": 21298}],
                },
                {"key": "called", "count": 2060, "losses": []},
                {"key": "proposed", "count": 214, "losses": []},
                {
                    "key": "code_checks",
                    "count": 205,
                    "losses": [{"label": "explanation check", "count": 9}],
                },
                {
                    "key": "risk_gates",
                    "count": 140,
                    "losses": [
                        {"label": "sector cap", "count": 21},
                        {"label": "open risk", "count": 18},
                        {"label": "max positions", "count": 15},
                        {"label": "other", "count": 11},
                    ],
                },
                {
                    "key": "filled",
                    "count": 116,
                    "losses": [{"label": "unfilled limits", "count": 24}],
                },
                {
                    "key": "closed",
                    "count": 112,
                    "losses": [{"label": "still open", "count": 4}],
                },
            ]
        },
        "findings": [
            {
                "dimension": "volatility",
                "dimension_label": "Volatility",
                "slice": "high third",
                "status": "finding",
            },
            {
                "dimension": "confidence",
                "dimension_label": "Confidence",
                "slice": "0.6–0.7",
                "status": "finding",
            },
            {
                "dimension": "days_to_earnings",
                "dimension_label": "Days to earnings",
                "slice": "4–10 days",
                "status": "early",
            },
        ],
        "skipped": {
            "total": 1846,
            "scored": 1840,
            "missed_pct": "19.00",
            "avoided_pct": "34.00",
        },
        "slices": {
            "entry_cycle": {
                "label": "Entry cycle",
                "rows": [
                    {"slice": "13:46", "avg_r": "0.41", "status": "early"},
                    {"slice": "14:46", "avg_r": "0.37", "status": "early"},
                    {"slice": "10:46", "avg_r": "0.12", "status": "early"},
                ],
            }
        },
        "calibration": {
            "entries": 93,
            "weighted_gap_pts": "20.00",
            "min_entries": 50,
        },
        "execution": {"gaps_count": 7, "gaps_extra_r": "2.90"},
        "overnight": {
            "in_session_usd": "406.00",
            "overnight_usd": "-84.00",
            "nights": 214,
        },
        "filters_removed": [
            {"filter": "rules", "decided": 140, "r_made": "-0.31"},
        ],
    }
    snap.update(over)
    return snap


# ----------------------------------------------------------------------
# question 1 — Is there an edge?


def test_edge_probably_not_proven() -> None:
    text = answer_edge(snapshot())
    assert text.startswith("Probably, but not proven yet.")
    assert "+0.21R" in text
    assert "+0.02R" in text and "+0.40R" in text
    assert "under 0.1R" in text
    assert "beta 0.31" in text.lower() or "Beta 0.31" in text


def test_edge_not_shown_yet_when_range_includes_zero() -> None:
    snap = snapshot()
    snap["headline"]["expectancy"] = {
        "avg_r": "0.21", "lo": "-0.02", "hi": "0.40", "n": 112,
    }
    assert answer_edge(snap).startswith("Not shown yet.")


def test_edge_yes_from_0_1r_low_end() -> None:
    snap = snapshot()
    snap["headline"]["expectancy"] = {
        "avg_r": "0.21", "lo": "0.10", "hi": "0.40", "n": 112,
    }
    assert answer_edge(snap).startswith("Yes.")


def test_edge_probably_not_below_zero() -> None:
    snap = snapshot()
    snap["headline"]["expectancy"] = {
        "avg_r": "-0.20", "lo": "-0.40", "hi": "-0.05", "n": 112,
    }
    text = answer_edge(snap)
    assert text.startswith("Probably not.")
    assert "below zero" in text


def test_edge_under_10_trades() -> None:
    snap = snapshot(trades=4)
    snap["headline"]["expectancy"] = {
        "avg_r": None, "lo": None, "hi": None, "n": 0,
    }
    text = answer_edge(snap)
    assert "Not enough closed trades" in text


def test_edge_beta_words() -> None:
    snap = snapshot()
    snap["headline"]["beta"]["beta"] = "1.40"
    assert "follows the market closely" in answer_edge(snap)
    snap["headline"]["beta"]["beta"] = "-0.20"
    assert "against the market" in answer_edge(snap)
    snap["headline"]["beta"]["beta"] = None
    assert "Beta" not in answer_edge(snap)


# ----------------------------------------------------------------------
# question 2 — What stands out?


def test_stands_out_counts_and_titles() -> None:
    text = answer_stands_out(snapshot())
    assert text.startswith("2 findings and 1 early signal.")
    assert "Volatility high third" in text
    assert "Confidence 0.6–0.7" in text
    assert "Days to earnings 4–10 days" in text


def test_stands_out_none() -> None:
    assert "Nothing stands out" in answer_stands_out(snapshot(findings=[]))


def test_stands_out_under_10_trades() -> None:
    text = answer_stands_out(snapshot(trades=4, findings=[]))
    assert "Nothing to compare yet" in text
    assert "10 closed trades" in text or "10" in text
    assert "30" in text
    assert "4" in text


# ----------------------------------------------------------------------
# question 3 — Where are trades lost?


def test_trades_lost_rules_and_top_two() -> None:
    text = answer_trades_lost(snapshot())
    # rules: 21298 / 24708 = 86%
    assert "86% of symbol checks" in text
    # after the model: risk gates 65 > unfilled limits 24 > code checks 9
    assert "risk gates (65)" in text
    assert "unfilled limits (24)" in text
    assert "code checks" not in text
    # avoided 34% > missed 19% → skipping adds value
    assert "Skipping adds value" in text
    assert "34%" in text and "19%" in text


def test_trades_lost_skips_leaving_money() -> None:
    snap = snapshot()
    snap["skipped"]["missed_pct"] = "40.00"
    snap["skipped"]["avoided_pct"] = "20.00"
    assert "leaving trades on the table" in answer_trades_lost(snap)


def test_trades_lost_rules_only_skips_na() -> None:
    snap = snapshot()
    snap["skipped"] = {"na": "rules only makes no model skips"}
    text = answer_trades_lost(snap)
    assert "symbol checks" in text
    assert "Skipping" not in text


def test_trades_lost_empty() -> None:
    assert "No trades" in answer_trades_lost({"funnel": {"steps": []}})


# ----------------------------------------------------------------------
# question 4 — Which conditions work?


def test_conditions_best_slices() -> None:
    text = answer_conditions(snapshot())
    assert text.startswith("Entry cycle:")
    assert "13:46 (+0.41R)" in text
    assert "14:46 (+0.37R)" in text
    assert "early signal" in text


def test_conditions_hidden_slices() -> None:
    snap = snapshot()
    snap["slices"]["entry_cycle"]["rows"] = []
    assert "counting" in answer_conditions(snap)


# ----------------------------------------------------------------------
# question 5 — What do confidence, fills and nights cost?


def test_costs_full() -> None:
    text = answer_costs(snapshot())
    assert "off by about 20 points" in text
    assert "7 stops filled through the stop" in text
    assert "2.90R extra" in text
    assert "Overnight holds earned −$84" in text
    assert "session earned +$406" in text


def test_costs_calibration_needs_entries() -> None:
    snap = snapshot()
    snap["calibration"] = {"entries": 12, "weighted_gap_pts": None}
    assert "needs 50 entries" in answer_costs(snap)
    assert "12" in answer_costs(snap)


def test_costs_within_target() -> None:
    snap = snapshot()
    snap["calibration"]["weighted_gap_pts"] = "6.00"
    assert "within its target" in answer_costs(snap)


def test_costs_rules_only() -> None:
    snap = snapshot()
    snap["calibration"] = {"na": "rules only has no model confidence"}
    snap["execution"]["gaps_count"] = 0
    text = answer_costs(snap)
    assert "Calibration" not in text
    assert "No stops filled through the stop" in text


# ----------------------------------------------------------------------
# the stored block and sample states


def test_compute_answers_has_five_keys() -> None:
    answers = compute_answers(snapshot())
    assert set(answers) == {
        "edge",
        "stands_out",
        "trades_lost",
        "conditions",
        "costs",
    }
    assert all(isinstance(v, str) and v for v in answers.values())


def test_sample_states_needs_and_have() -> None:
    data = with_sample_states(snapshot(trades=4))
    exp = data["headline"]["expectancy"]
    assert exp["needs"] == 10 and exp["have"] == 4
    edge = data["headline"]["edge_over_spy"]
    assert edge["needs"] == 10 and edge["have"] == 4
    beta = data["headline"]["beta"]
    assert beta["needs"] == 3 and beta["have"] == 58
    cal = data["calibration"]
    assert cal["needs"] == 50 and cal["have"] == 93
    row = data["filters_removed"][0]
    assert row["needs"] == 100 and row["have"] == 140
