"""Hand-calculated checks on the pure insights function (PR 22).

Every fixture is built so the expected number is computable by hand —
the spec's "done when" asks exactly that.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.insights.compute import (
    Bar5m,
    DailyBar,
    EquityDay,
    InsightsDecision,
    InsightsOrder,
    InsightsSkip,
    InsightsTrade,
    compute_insights,
)

D = Decimal
UTC_ = UTC

# Mon 5 – Fri 16 Jan 2027 are trading days for the fixtures.
SESSIONS = [
    date(2027, 1, 4) + timedelta(days=i)
    for i in range(13)
    if (date(2027, 1, 4) + timedelta(days=i)).weekday() < 5
]
assert date(2027, 1, 4).weekday() == 0  # Monday


def trade(
    i: int,
    *,
    symbol: str = "AAA",
    qty: int = 1,
    entry: str = "100",
    stop: str = "90",
    pnl: str = "10",
    exit_reason: str = "target",
    opened: datetime | None = None,
    closed: datetime | None = None,
    cycle: str | None = "10:46",
    weekday: int | None = 0,
    sector: str | None = "Technology",
    regime: str | None = "Up · normal vol",
    dte: int | None = 30,
    gap: str | None = "0.2",
    vwap: str | None = "99",
    ema20: str | None = "100",
    atr: str | None = "10",
    rs_spy: str | None = "1",
    spy_day: str | None = "0.1",
    atr_pct: str | None = "50",
    conf: str | None = "0.6",
    stop_basis: str | None = None,
    target_basis: str | None = None,
    held: int | None = 1,
    prompt: str | None = "decision.v2",
    model: str | None = "gpt-4o-mini",
    first_touch: str | None = "target",
    stop_leg: str | None = None,
    stop_fill: str | None = None,
    exit_price: str | None = "110",
    entry_ny: date | None = None,
    exit_ny: date | None = None,
    after_move: str | None = None,
) -> InsightsTrade:
    opened = opened or datetime(2027, 1, 4, 15, 0, tzinfo=UTC_)
    closed = closed or datetime(2027, 1, 4, 20, 0, tzinfo=UTC_)
    return InsightsTrade(
        id=i,
        symbol=symbol,
        qty=qty,
        entry=D(entry),
        exit_price=D(exit_price) if exit_price else None,
        initial_stop=D(stop) if stop else None,
        adjusted_pnl=D(pnl),
        exit_reason=exit_reason,
        opened_at=opened,
        closed_at=closed,
        first_touch=first_touch,
        entry_cycle=cycle,
        weekday=weekday,
        sector=sector,
        regime=regime,
        days_to_earnings=dte,
        opening_gap_pct=D(gap) if gap is not None else None,
        entry_vwap=D(vwap) if vwap else None,
        ema20_1h=D(ema20) if ema20 else None,
        atr_1h=D(atr) if atr else None,
        rs_vs_spy_pct=D(rs_spy) if rs_spy else None,
        spy_day_pct=D(spy_day) if spy_day else None,
        atr_percentile=D(atr_pct) if atr_pct else None,
        confidence=D(conf) if conf else None,
        stop_basis=stop_basis,
        target_basis=target_basis,
        sessions_held=held,
        prompt_version=prompt,
        model=model,
        stop_leg_price=D(stop_leg) if stop_leg else None,
        stop_fill_price=D(stop_fill) if stop_fill else None,
        entry_ny=entry_ny,
        exit_ny=exit_ny,
        after_exit_move_pct=D(after_move) if after_move else None,
    )


def base_kwargs(**over):
    kw: dict = {
        "equity_daily": [],
        "spy_daily": [],
        "daily_bars_for": lambda symbol: [],
        "bars_5m_for": lambda symbol, start, end: [],
        "session_dates": SESSIONS,
        "return_pct": None,
        "model_cost": D("0"),
        "dividends_usd": D("0"),
        "dividend_positions": 0,
        "open_positions": 0,
    }
    kw.update(over)
    return kw


def dec(i: int, action: str, *, reason=None, model_action=None,
        checks=(), gate=None, filled=None, ctx="candidate") -> InsightsDecision:
    return InsightsDecision(
        id=i, context=ctx, final_action=action, no_call_reason=reason,
        model_action=model_action, code_checks_failed=tuple(checks),
        gate=gate, entry_filled=filled,
    )


# ----------------------------------------------------------------------
# headline: expectancy, edge, beta, cost


def test_expectancy_and_cost() -> None:
    trades = [
        trade(1, pnl="20"),   # +2.0R
        trade(2, pnl="-10"),  # −1.0R
        trade(3, pnl="5"),    # +0.5R
        trade(4, pnl="-5"),   # −0.5R
    ]
    out = compute_insights(trades, [], [], [], **base_kwargs(model_cost=D("4")))
    h = out["headline"]
    # mean R = 1.0/4 = 0.25; sd of {2,−1,.5,−.5}: mean .25 →
    # deviations 1.75,−1.25,.25,−.75 → var (3.0625+1.5625+.0625+.5625)/3 = 1.75
    # se = sqrt(1.75)/2 ≈ 0.6614; 90% range = 0.25 ± 1.64×0.6614 = ±1.0847
    assert h["expectancy"]["avg_r"] == "0.25"
    assert h["expectancy"]["n"] == 4
    assert h["expectancy"]["lo"] == "-0.83"
    assert h["expectancy"]["hi"] == "1.33"
    assert h["model_cost"]["per_trade_usd"] == "1.00"
    # gross profit = 25 → 4/25 = 16%
    assert h["model_cost"]["pct_gross_profit"] == "16.00"


def test_edge_beta_and_correlation() -> None:
    equity = [
        EquityDay(date(2027, 1, 4), D("10000"), D("10000")),
        EquityDay(date(2027, 1, 5), D("10100"), D("5050")),
        EquityDay(date(2027, 1, 6), D("10000"), D("10000")),
        EquityDay(date(2027, 1, 7), D("10200"), D("5100")),
    ]
    spy = [
        DailyBar(date(2027, 1, 4), D("100"), D("100"), D("100"), D("100")),
        DailyBar(date(2027, 1, 5), D("100"), D("100"), D("100"), D("102")),
        DailyBar(date(2027, 1, 6), D("100"), D("100"), D("100"), D("100")),
        DailyBar(date(2027, 1, 7), D("100"), D("100"), D("100"), D("104")),
    ]
    out = compute_insights(
        [],
        [],
        [],
        [],
        **base_kwargs(
            equity_daily=equity,
            spy_daily=spy,
            return_pct=D("2.0"),
        )
    )
    h = out["headline"]
    # SPY 100→104 over the window = +4%; exposure per day:
    # 0%, 50%, 0%, 50% → mean 25%; edge = 2.0 − 4.0×0.25 = +1.00 pts
    assert h["edge_over_spy"]["spy_return_pct"] == "4.00"
    assert h["edge_over_spy"]["exposure"] == "0.25"
    assert h["edge_over_spy"]["pts"] == "1.00"
    # strategy returns: +1%, −0.9901%, +2%; SPY: +2%, −1.9608%, +4%
    # strat = 2×spy on day 2 vs day 3 pattern: r_strat = r_spy/2 each day
    # → beta 0.5, correlation 1.00
    assert h["beta"]["beta"] == "0.50"
    assert h["beta"]["correlation"] == "1.00"
    assert h["beta"]["sessions"] == 3


# ----------------------------------------------------------------------
# overnight split with a gap day


def test_overnight_split_counts_the_gap() -> None:
    mon, tue, wed = date(2027, 1, 4), date(2027, 1, 5), date(2027, 1, 6)
    bars = [
        DailyBar(date(2027, 1, 1), D("95"), D("96"), D("94"), D("95")),
        DailyBar(mon, D("99"), D("103"), D("98"), D("102")),
        DailyBar(tue, D("101"), D("104"), D("100"), D("103")),
        DailyBar(wed, D("98"), D("99"), D("97"), D("98.5")),
    ]
    t = trade(
        1,
        qty=1,
        exit_price="99",
        entry_ny=mon,
        exit_ny=wed,
        opened=datetime(2027, 1, 4, 15, 0, tzinfo=UTC_),
        closed=datetime(2027, 1, 6, 16, 0, tzinfo=UTC_),
    )
    out = compute_insights(
        [t], [], [], [], **base_kwargs(daily_bars_for=lambda s: bars)
    )
    o = out["overnight"]
    # nights: Tue 101−102 = −1; Wed 98−103 = −5 → overnight −6
    # in session: Mon 102−100 = +2; Tue 103−101 = +2; Wed 99−98 = +1 → +5
    assert o["overnight_usd"] == "-6.00"
    assert o["in_session_usd"] == "+5.00" or o["in_session_usd"] == "5.00"
    assert o["nights"] == 2
    assert o["gapped_against"] == 2
    assert o["largest_gap"]["r"] == "-0.50"  # −5 / risk 10
    assert o["largest_gap"]["symbol"] == "AAA"
    assert o["largest_gap"]["date"] == "2027-01-06"


# ----------------------------------------------------------------------
# gap through the stop


def test_gap_through_stop_extra_r() -> None:
    # stop leg 95, filled 94: extra = 1/5 = 0.2R > 0.1 → counted
    gapped = trade(
        1, stop="95", exit_reason="stop", exit_price="94",
        pnl="-6", first_touch="stop",
        stop_leg="95", stop_fill="94",
    )
    # filled at the stop: no gap
    clean = trade(
        2, stop="95", exit_reason="stop", exit_price="95",
        pnl="-5", first_touch="stop", stop_leg="95", stop_fill="95",
    )
    out = compute_insights([gapped, clean], [], [], [], **base_kwargs())
    assert out["execution"]["gaps_count"] == 1
    assert out["execution"]["gaps_extra_r"] == "0.20"
    assert out["headline"]["gaps"]["count"] == 1


# ----------------------------------------------------------------------
# funnel


def test_funnel_counts_and_losses() -> None:
    decisions = (
        [dec(i, "no_call", reason="rules") for i in range(10)]
        + [dec(10 + i, "no_call", reason="call_cap") for i in range(5)]
        + [dec(20 + i, "no_call", reason="price_stale") for i in range(3)]
        + [dec(30 + i, "no_call", reason="no_options") for i in range(2)]
        + [dec(40 + i, "no_call", reason="model_error") for i in range(4)]
        + [
            dec(50, "skip", model_action="enter", checks=("Stop distance",)),
            dec(51, "blocked", model_action="enter", gate="sector cap"),
            dec(52, "enter", model_action="enter", filled=True),
            dec(53, "enter", model_action="enter", filled=False),
        ]
        + [dec(60 + i, "skip", model_action="skip") for i in range(10)]
        + [dec(70 + i, "skip", model_action=None) for i in range(2)]
    )
    out = compute_insights(
        [trade(1)], decisions, [], [], **base_kwargs(open_positions=0)
    )
    steps = {s["key"]: s for s in out["funnel"]["steps"]}
    assert steps["symbol_checks"]["count"] == 40
    assert steps["rules"]["count"] == 30
    assert {item["label"]: item["count"] for item in steps["rules"]["losses"]} == {"rules": 10}
    assert steps["called"]["count"] == 20
    losses = {item["label"]: item["count"] for item in steps["called"]["losses"]}
    assert losses == {"call cap": 5, "price checks": 3, "no options": 2}
    assert steps["proposed"]["count"] == 4
    losses = {item["label"]: item["count"] for item in steps["proposed"]["losses"]}
    assert losses == {"model skipped": 10, "invalid output": 2, "call failed": 4}
    assert steps["code_checks"]["count"] == 3
    assert {item["label"]: item["count"] for item in steps["code_checks"]["losses"]} == {"Stop distance": 1}
    assert steps["risk_gates"]["count"] == 2
    assert {item["label"]: item["count"] for item in steps["risk_gates"]["losses"]} == {"sector cap": 1}
    assert steps["filled"]["count"] == 1
    assert steps["closed"]["count"] == 1


# ----------------------------------------------------------------------
# slices and findings


def _bulk(start: int, n: int, r_pnl: str, **kw) -> list[InsightsTrade]:
    return [trade(start + i, pnl=r_pnl, **kw) for i in range(n)]


def test_planted_difference_is_a_finding() -> None:
    # 30 Energy trades at a constant +0.8R against 30 alternating
    # +0.4R/+0.6R elsewhere (mean +0.5, sd 0.1015). The difference is
    # 0.3R and the noise ≈ 0.0185 → sigma ≈ 16, a clear finding.
    a = _bulk(0, 30, "8", sector="Energy")
    b = [
        trade(100 + i, pnl="6" if i % 2 else "4", sector="Financials")
        for i in range(30)
    ]
    out = compute_insights(a + b, [], [], [], **base_kwargs())
    findings = [f for f in out["findings"] if f["dimension"] == "sector"]
    assert len(findings) == 2  # Energy up, Financials down — same gap
    f = findings[0]
    assert f["slice"] == "Energy"
    assert f["status"] == "finding"
    assert f["trades"] == 30 and f["avg_r"] == "0.80"
    assert f["rest_trades"] == 30 and f["rest_avg_r"] == "0.50"
    assert f["sigma"] == "16.16"


def test_finding_direction() -> None:
    a = _bulk(0, 30, "8", sector="Energy")   # +0.8R
    b = _bulk(100, 30, "4", sector="Financials")  # +0.4R
    out = compute_insights(a + b, [], [], [], **base_kwargs())
    sectors = {f["slice"]: f for f in out["findings"]}
    # Energy +0.8 vs rest +0.4; constant groups → se 0 → finding either way
    assert sectors["Energy"]["status"] == "finding"
    assert sectors["Financials"]["status"] == "finding"


def test_sample_statuses_switch_at_10_and_30() -> None:
    # 9 trades in Energy (hidden), 12 in Financials (early), 30 in Tech.
    group = (
        _bulk(0, 9, "8", sector="Energy")
        + _bulk(20, 12, "8", sector="Financials")
        + _bulk(50, 30, "4", sector="Technology")
    )
    out = compute_insights(group, [], [], [], **base_kwargs())
    rows = {r["slice"]: r for r in out["slices"]["sector"]["rows"]}
    assert "Energy" not in rows  # hidden under 10
    assert out["slices"]["sector"]["hidden"] == 9
    assert rows["Financials"]["status"] == "early"
    assert rows["Technology"]["status"] == "counted" or rows["Technology"]["status"] == "finding"


def test_noise_finds_no_findings() -> None:
    # Same R everywhere: no dimension can produce a finding.
    trades = [
        trade(
            i,
            pnl="5",
            sector=["Energy", "Technology", "Financials"][i % 3],
            cycle=["10:46", "11:46", "12:46"][i % 3],
            weekday=i % 5,
            dte=[5, 15, 30][i % 3],
            gap=["-2", "0", "2"][i % 3],
            vwap=["99", "101"][i % 2],
            ema20=["98", "102"][i % 2],
            rs_spy=str(i % 10),
            spy_day=["-1", "0", "1"][i % 3],
            atr_pct=str(i % 100),
            conf=str(50 + (i % 5)) / 100 if False else D("0.5") + D(i % 5) / D(10),
            held=[1, 2, 5][i % 3],
            prompt=["decision.v1", "decision.v2"][i % 2],
            model=["gpt-4o-mini", "gpt-4.1-mini"][i % 2],
            stop_basis=["swing low", "2 ATR"][i % 2],
            target_basis=["resistance", "2R"][i % 2],
        )
        for i in range(120)
    ]
    out = compute_insights(trades, [], [], [], **base_kwargs())
    assert out["findings"] == []


def test_entry_cycle_rule_rows() -> None:
    trades = _bulk(0, 12, "5", cycle="10:46")
    out = compute_insights(trades, [], [], [], **base_kwargs())
    rows = {r["slice"]: r for r in out["slices"]["entry_cycle"]["rows"]}
    assert rows["15:46"]["rule"].startswith("No entries in the last cycle")
    assert rows["15:46"]["trades"] == 0
    # a cycle the strategy's override excludes shows the override rule
    out2 = compute_insights(
        trades, [], [], [], **base_kwargs(entry_cycles=["10:46"])
    )
    rows2 = {r["slice"]: r for r in out2["slices"]["entry_cycle"]["rows"]}
    assert "entry_cycles" in rows2["11:46"]["rule"]


# ----------------------------------------------------------------------
# calibration


def test_calibration_buckets() -> None:
    members = [
        # stated ~0.6, half reach target first
        trade(i, conf="0.6", first_touch="target" if i % 2 else "stop")
        for i in range(12)
    ] + [
        # stated ~0.8, three quarters reach target first
        trade(20 + i, conf="0.8", first_touch="stop" if i % 4 == 0 else "target")
        for i in range(12)
    ]
    out = compute_insights(members, [], [], [], **base_kwargs())
    cal = out["calibration"]
    buckets = {b["label"]: b for b in cal["buckets"]}
    assert buckets["0.6–0.7"]["actual"] == "0.50"
    assert buckets["0.8–0.9"]["actual"] == "0.75"
    # weighted gap needs 50 entries — under that it's None
    assert cal["weighted_gap_pts"] is None
    # 24 entries → both buckets are "early" (10–29)
    assert buckets["0.6–0.7"]["status"] == "early"


def test_rules_only_has_no_calibration_or_skips() -> None:
    trades = _bulk(0, 5, "5")
    out = compute_insights(
        trades, [], [], [], **base_kwargs(strategy_kind="rules_only")
    )
    assert "na" in out["calibration"]
    assert out["skipped"]["total"] == 0


# ----------------------------------------------------------------------
# skipped setups


def _skip(i: int, symbol: str, at: datetime, session: date) -> InsightsSkip:
    return InsightsSkip(
        id=i, symbol=symbol, decided_at=at, session_date=session,
        cycle_time="10:46", price=D("100"), atr_1h=D("10"), dims={},
    )


def test_skip_scored_missed_avoided_neither() -> None:
    mon = date(2027, 1, 4)
    at = datetime(2027, 1, 4, 15, 0, tzinfo=UTC_)
    # limit = 100.05, stop = 80.05, target = 140.05
    missed_bars = [
        Bar5m(at, D("100"), D("101"), D("100"), D("100.5")),      # fills
        Bar5m(at + timedelta(minutes=5), D("101"), D("141"), D("100"), D("140")),
    ]
    avoided_bars = [
        Bar5m(at, D("100"), D("101"), D("100"), D("100.5")),
        Bar5m(at + timedelta(minutes=5), D("101"), D("101"), D("79"), D("80")),
    ]
    neither_bars = [
        Bar5m(at, D("100"), D("101"), D("100"), D("100.5")),
        Bar5m(at + timedelta(minutes=5), D("100.5"), D("110"), D("95"), D("105")),
    ]
    skips = [_skip(1, "M", at, mon), _skip(2, "A", at, mon), _skip(3, "N", at, mon)]
    feed = {"M": missed_bars, "A": avoided_bars, "N": neither_bars}
    out = compute_insights(
        [], [], skips, [],
        **base_kwargs(
            bars_5m_for=lambda s, a, b: feed[s],
        )
    )
    sk = out["skipped"]
    assert sk["missed"] == 1 and sk["avoided"] == 1 and sk["neither"] == 1
    # baseline: missed +2R, avoided −1R, neither (105−100.05)/20 ≈ +0.2475
    # mean ≈ (2 − 1 + 0.2475)/3 = 0.4158 → 0.42
    assert sk["baseline_r"] == "0.42"


def test_skip_unfilled_is_neither() -> None:
    mon = date(2027, 1, 4)
    at = datetime(2027, 1, 4, 15, 0, tzinfo=UTC_)
    bars = [
        Bar5m(at, D("100"), D("200"), D("101"), D("150")),  # never ≤ 100.05
    ]
    out = compute_insights(
        [], [], [_skip(1, "X", at, mon)], [],
        **base_kwargs(bars_5m_for=lambda s, a, b: bars)
    )
    assert out["skipped"]["neither"] == 1


# ----------------------------------------------------------------------
# execution


def test_execution_fill_rate_and_slippage() -> None:
    orders = [
        InsightsOrder("entry", "10:46", D("100"), D("100.05"), True, D("100.10"), 10),
        InsightsOrder("entry", "10:46", D("100"), D("100.05"), False, None, None),
        InsightsOrder("entry", "11:46", D("100"), D("100.05"), True, D("99.90"), 10),
        InsightsOrder("stop", "10:46", None, D("90"), True, D("89.50"), 10, "stop"),
        InsightsOrder("target", "10:46", None, D("120"), True, D("120"), 10, "target"),
        InsightsOrder("exit", "10:46", D("105"), D("105"), True, D("104.80"), 10, "exit"),
    ]
    out = compute_insights([], [], [], orders, **base_kwargs())
    ex = out["execution"]
    assert ex["placed"] == 3 and ex["filled"] == 2
    assert ex["fill_rate"] == "66.67"
    assert ex["worst_cycle"]["cycle"] == "10:46"
    # entry slippage: (100.10−100)/100 = +10 bps; (99.90−100)/100 = −10 → 0
    assert ex["entry_slippage_bps"] == "0.00"
    splits = {s["reason"]: s["bps"] for s in ex["exit_slippage_splits"]}
    assert splits["stop"] == "55.56"     # (90−89.50)/90 ≈ 55.6 bps
    assert splits["target"] == "0.00"
    assert splits["exit"] == "19.05"     # (105−104.80)/105 ≈ 19 bps


def test_same_day_exits_and_earnings() -> None:
    mon, tue = date(2027, 1, 4), date(2027, 1, 5)
    same_day = trade(
        1, exit_reason="exit", pnl="-5", entry_ny=mon, exit_ny=mon,
    )
    later = trade(
        2, exit_reason="exit", pnl="10", entry_ny=mon, exit_ny=tue,
        closed=datetime(2027, 1, 5, 20, 0, tzinfo=UTC_),
    )
    earn = trade(
        3, exit_reason="earnings_exit", pnl="20", entry_ny=mon, exit_ny=tue,
        closed=datetime(2027, 1, 5, 20, 0, tzinfo=UTC_), after_move="-2",
    )
    out = compute_insights(
        [same_day, later, earn], [], [], [], **base_kwargs()
    )
    ex = out["execution"]
    assert ex["same_day_exits"] == 1
    assert ex["same_day_avg_r"] == "-0.50"
    assert ex["earnings_exits"] == 1
    assert ex["earnings_avg_r"] == "2.00"
    assert ex["earnings_median_move_pct"] == "-2.00"


def test_before_data_under_10_trades() -> None:
    out = compute_insights(_bulk(0, 5, "5"), [], [], [], **base_kwargs())
    assert out["trades"] == 5
    # the page reads trades < early_trades for its before-data state
    assert all(
        dim["hidden"] == 5 or not dim["rows"]
        for dim in out["slices"].values()
    )


