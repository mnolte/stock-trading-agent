"""Item 100: the readiness "What moves this forward" payload
(docs/SPEC.md → Readiness). A pure function of the stored snapshot."""

from __future__ import annotations

from typing import Any

from app.market.calendar import WeekdayCalendarProvider
from app.readiness.forward import forward

CAL = WeekdayCalendarProvider()


def _crit(
    key: str,
    status: str,
    *,
    value: str = "0",
    target: str = "0",
    label: str | None = None,
    sample: int | None = None,
    needs: str | None = None,
    sub: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "group": "Operations",
        "label": label or key,
        "value": value,
        "target": target,
        "progress": None,
        "status": status,
        "sample": sample,
        "sub": sub,
        "hint": "",
        "estimate": None,
        "estimate_date": None,
        "needs": needs,
        "detail": detail or {},
    }


def _act(criteria: list[dict[str, Any]], key: str) -> dict[str, Any]:
    out = forward(criteria, calendar=CAL, manual_open=0)
    return next(a for a in out["act"] if a["key"] == key)


def test_empty_act_list() -> None:
    out = forward(
        [_crit("reconciliation", "pass")], calendar=CAL, manual_open=0
    )
    assert out["act"] == []
    assert out["time"] == []


def test_log_errors_names_and_sentence() -> None:
    row = _act(
        [
            _crit(
                "log_errors",
                "not_yet",
                value="2",
                sample=2,
                detail={"names": ["SMTP sign-in", "decision call"]},
            )
        ],
        "log_errors",
    )
    assert "2 error problems" in row["sentence"]
    assert "SMTP sign-in, decision call" in row["sentence"]
    assert row["button"] == "Review on Logs"
    assert row["href"] == "/logs"
    assert "2 unreviewed · target 0" in row["value_text"]


def test_log_errors_names_clause_dropped() -> None:
    row = _act(
        [_crit("log_errors", "not_yet", value="1", sample=1)],
        "log_errors",
    )
    assert "(" not in row["sentence"]
    assert "1 error problems on Logs" in row["sentence"]


def test_reconciliation_and_unresolved() -> None:
    rows = forward(
        [
            _crit("reconciliation", "not_yet", sample=2),
            _crit("unresolved_errors", "not_yet", sample=3),
        ],
        calendar=CAL,
        manual_open=0,
    )["act"]
    assert rows[0]["key"] == "reconciliation"
    assert "2 mismatches with Alpaca" in rows[0]["sentence"]
    assert rows[0]["href"] == "/positions"
    assert "3 errors that touched a decision" in rows[1]["sentence"]
    assert rows[1]["href"] == "/#needs"


def test_log_critical_date_clause() -> None:
    crit = _crit(
        "log_critical",
        "not_yet",
        sample=4,
        detail={"count": 4, "last_date": "2026-10-14", "window_sessions": 20},
    )
    assert "last on 14 Oct" in _act([crit], "log_critical")["sentence"]
    crit["detail"]["last_date"] = None
    sentence = _act([crit], "log_critical")["sentence"]
    assert "last on" not in sentence
    assert "20 sessions pass without one" in sentence


def test_stops_missing_date2() -> None:
    # 14 Oct 2026 + 30 weekday sessions → 25 Nov 2026.
    row = _act(
        [
            _crit(
                "stops_missing",
                "not_yet",
                sample=2,
                detail={
                    "count": 2,
                    "last_date": "2026-10-14",
                    "window_sessions": 30,
                },
            )
        ],
        "stops_missing",
    )
    assert "no stop at Alpaca 2 times, the last on 14 Oct" in row["sentence"]
    assert "30-session window, after 25 Nov" in row["sentence"]
    assert row["button"] == "Open Logs for 14 Oct"
    assert row["href"] == "/logs?from=2026-10-14&to=2026-10-14"


def test_kill_switch_and_cycles() -> None:
    kill = _crit(
        "kill_switch",
        "not_yet",
        sample=1,
        detail={"count": 1, "last_date": "2026-10-14", "window_sessions": 30},
    )
    sentence = _act([kill], "kill_switch")["sentence"]
    assert "turned on 1 time, the last on 14 Oct" in sentence
    assert "after 25 Nov" in sentence

    cycles = _crit(
        "cycles",
        "not_yet",
        value="96% · 52 of 54",
        target="≥ 98%",
        detail={
            "count": 2,
            "dates": ["2026-10-14"],
            "reason": "shutdown during the session",
            "window_sessions": 20,
        },
    )
    row = _act([cycles], "cycles")
    assert "2 cycles were missed on 14 Oct (shutdown during the session)" in row["sentence"]
    assert "20-session window after 11 Nov" in row["sentence"]
    assert "96% · 52 of 54 · target 98%" in row["value_text"]


def test_model_and_evidence_sentences() -> None:
    criteria = [
        _crit("proposals", "not_yet", value="3% · 1 of 33", target="≥ 75%",
              detail={"failed": 32, "total": 33}),
        _crit("calibration", "not_yet", detail={"gap_pts": 12.4}),
        _crit("price_blocked", "not_yet", value="7.5%", detail={"pct": 7.5}),
        _crit("return_vs_spy", "not_yet", detail={"gap_pts": -0.63}),
        _crit("max_drawdown", "not_yet", detail={"dd_pct": 6.5, "max_pct": 6}),
        _crit("avg_r", "not_yet", detail={"avg_r": 0.11, "min_r": 0.2}),
        _crit("profit_factor", "not_yet", detail={"pf": 1.1, "min_pf": 1.3}),
        _crit("beats_rules_only", "not_yet",
              detail={"live_r": 0.1, "shadow_r": 0.25}),
    ]
    act = {a["key"]: a for a in forward(criteria, calendar=CAL, manual_open=0)["act"]}
    assert "32 of 33 proposals were turned down" in act["proposals"]["sentence"]
    assert "12.4 points from the actual win rate" in act["calibration"]["sentence"]
    assert "7.5% of candidate checks" in act["price_blocked"]["sentence"]
    assert "0.63 points behind SPY" in act["return_vs_spy"]["sentence"]
    assert "deepest fall was 6.5%, over the 6% target" in act["max_drawdown"]["sentence"]
    assert "earn 0.11 R on average, under 0.2 R" in act["avg_r"]["sentence"]
    assert "1.1 times what losers lost, under 1.3" in act["profit_factor"]["sentence"]
    assert "Live's average R (0.1) is not above" in act["beats_rules_only"]["sentence"]
    assert act["beats_rules_only"]["href"] == "/strategies"


def test_act_order_follows_the_table() -> None:
    criteria = [
        _crit("avg_r", "not_yet"),
        _crit("log_errors", "not_yet", sample=1),
        _crit("cycles", "not_yet"),
    ]
    keys = [
        a["key"]
        for a in forward(criteria, calendar=CAL, manual_open=0)["act"]
    ]
    assert keys == ["log_errors", "cycles", "avg_r"]


def test_time_counts_and_needs_data_grouping() -> None:
    criteria = [
        _crit("closed_trades", "not_yet", value="3", target="≥ 100",
              label="Closed trades", sub="≈ 290 sessions at the current pace"),
        _crit("sessions", "not_yet", value="9", target="≥ 60",
              label="Paper sessions in this run", sub="≈ 4 Jan 2027"),
        _crit("avg_r", "waiting", label="Average R (adjusted)",
              sample=3, needs="30 closed trades"),
        _crit("profit_factor", "waiting", label="Profit factor",
              sample=3, needs="30 closed trades"),
        _crit("beats_rules_only", "waiting", label="Beats rules-only",
              sample=3, needs="30 closed trades"),
        _crit("calibration", "waiting", label="Confidence calibration",
              sample=3, needs="50 filled entries"),
    ]
    out = forward(criteria, calendar=CAL, manual_open=0)
    assert [t["text"] for t in out["time"] if t["kind"] == "count"] == [
        "3 of 100 · about 290 sessions at the current pace",
        "9 of 60 · about 4 Jan 2027",
    ]
    needs = [t["text"] for t in out["time"] if t["kind"] == "needs"]
    assert (
        "Average R, Profit factor and Beats rules-only need 30 closed "
        "trades (3 so far)" in needs
    )
    assert "Confidence calibration needs 50 filled entries (3 so far)" in needs
    assert out["time_count"] == 6


def test_manual_open_count() -> None:
    out = forward([], calendar=CAL, manual_open=5)
    assert out["manual_open"] == 5
