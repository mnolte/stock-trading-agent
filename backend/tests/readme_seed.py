"""Seed README screenshot data before the readme-screens server starts.

Test-only (never imported by the app). Writes the same row shapes the live
engine writes for a finished stretch of paper sessions — closed trades with
their bracket legs, fills, outcomes, equity curve, model calls, reviews and
verdicts — so every screen renders real layout without real account data.
All symbols, prices and texts are fixtures; no keys, account numbers or
addresses appear.

Usage (from backend/, after e2e_seed, with the readme env):
``../.venv/bin/python -m tests.readme_seed``
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

RUN_START = date(2026, 9, 14)  # Monday
TRADE_DAYS = [
    date(2026, 9, 15), date(2026, 9, 17), date(2026, 9, 21), date(2026, 9, 24),
    date(2026, 9, 28), date(2026, 9, 30), date(2026, 10, 2), date(2026, 10, 6),
    date(2026, 10, 7), date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13),
]
STARTING_EQUITY = Decimal(10000)

# (symbol, entry, stop, target, session index into TRADE_DAYS for open,
#  close-day offset in sessions, exit reason). R is computed from the geometry.
TRADES: list[tuple[str, str, str, str, int, int, str]] = [
    ("MSFT", "420", "412", "436", 0, 2, "target"),
    ("AAPL", "225", "219", "240", 0, 3, "target"),
    ("NVDA", "135", "130", "147.5", 1, 2, "stop"),
    ("AMZN", "190", "184", "202", 2, 3, "agent_exit"),
    ("GOOGL", "165", "160", "175", 3, 4, "target"),
    ("META", "585", "570", "615", 4, 3, "stop"),
    ("JPM", "230", "224", "242", 5, 4, "earnings_exit"),
    ("QQQ", "490", "480", "510", 6, 4, "target"),
    ("XOM", "120", "116", "128", 7, 2, "agent_exit"),
    ("MSFT", "424", "416", "440", 8, 3, "target"),
    ("NVDA", "138", "133", "150", 9, 3, "target"),
    ("AAPL", "228", "222", "243", 10, 2, "stop"),
]

# (symbol, action, confidence, day index, note) — model skips recorded with a
# call but no trade, plus one rules no_call.
SKIPS: list[tuple[str, str, str, int]] = [
    ("GOOGL", "skip", "0.41", 1),
    ("XOM", "skip", "0.48", 4),
    ("JPM", "skip", "0.52", 8),
]


def _ny_open(day: date, minutes: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, 13, 30) + timedelta(minutes=minutes)


def _cycle_time(day: date, slot: int) -> datetime:
    """Naive UTC of the ``slot``-th configured cycle on ``day`` — seeded
    cycles sit on the live schedule (tests/test_seed_shapes.py)."""
    from app.config import get_config
    from app.market.clock import NY_TZ

    cycles = get_config().get("schedule", "cycles")
    hh, mm = (int(part) for part in str(cycles[slot]).split(":"))
    at = datetime(day.year, day.month, day.day, hh, mm, tzinfo=NY_TZ)
    return at.astimezone(UTC).replace(tzinfo=None)


def _cycle(session: Any, day: date, slot: int) -> Any:
    from app.db.models import Cycle

    at = _cycle_time(day, slot)
    c = Cycle(
        kind="cycle",
        scheduled_time=at,
        started_at=at,
        finished_at=at + timedelta(minutes=2),
        duration_seconds=120.0,
        status="done",
    )
    session.add(c)
    session.flush()
    return c


def _decision(
    session: Any,
    cycle: Any,
    symbol: str,
    action: str,
    confidence: float,
    *,
    stop: Decimal | None = None,
    target: Decimal | None = None,
    price: Decimal | None = None,
    entry: bool = True,
) -> Any:
    from app.db.models import Decision

    d = Decision(
        cycle_id=cycle.id,
        symbol=symbol,
        context="candidate",
        input_snapshot={
            "symbol": symbol,
            "price": str(price) if price is not None else "0",
            "regime": {"trend": "up", "volatility": "normal"},
        },
        rules_result={
            "status": "pass",
            "reason": None,
            "checks": [
                {"name": "RSI range", "passed": True, "detail": "RSI 55 in 40–70"},
                {"name": "Daily above SMA 50", "passed": True, "detail": "close above SMA 50"},
                {"name": "EMA 20 > EMA 50", "passed": True, "detail": "uptrend"},
            ],
        },
        model_output={
            "action": action,
            "confidence": confidence,
            "stop_price": float(stop) if stop is not None else None,
            "target_price": float(target) if target is not None else None,
            "thesis": f"{symbol}: trend continuation above session VWAP.",
            "invalidation": f"Hourly close below {stop}." if stop is not None else None,
            "risks": ["Simulated fixture"],
            "news_used": [],
        },
        validation_result={
            "valid": True,
            "model_action": action,
            "final_action": action,
            "changed_by_code": False,
            "checks": [
                {"name": "Stop distance", "passed": True, "detail": "2.0 × ATR"},
                {"name": "Reward to risk", "passed": True, "detail": "rr 2.0 ≥ 1.5"},
                {
                    "name": "Confidence",
                    "passed": action == "enter",
                    "detail": f"{confidence} vs 0.55",
                },
            ],
        },
        gates_result=(
            {
                "allowed": True,
                "blocked_by": None,
                "risk_factor": "1.00",
                "sizing": {"shares": 0, "limited_by": None},
                # Live gate names (app/risk/gates.py), never retired ones.
                "checks": [
                    {"name": "kill_switch", "passed": True, "detail": "off"},
                    {"name": "paused", "passed": True, "detail": "entries active"},
                    {"name": "max_open_positions", "passed": True, "detail": "1 of 4"},
                    {"name": "daily_loss_limit", "passed": True, "detail": "session P&L 0.00%"},
                    {
                        "name": "position_size",
                        "passed": True,
                        "detail": "6 shares, limited by risk_per_trade",
                    },
                ],
            }
            if entry
            else {"allowed": False, "blocked_by": None, "checks": []}
        ),
        final_action=action,
        prompt_version="decision.v1",
        prompt_hash="ab" * 32,
        model="gpt-4o-mini",
        tokens=3200,
        tokens_in=2800,
        tokens_out=400,
        cost=Decimal("0.00066"),
        latency_ms=840,
    )
    session.add(d)
    session.flush()
    return d


def _llm_call(session: Any, d: Any, day: date, outcome: str = "success") -> None:
    from app.db.models import LlmCall

    session.add(
        LlmCall(
            sent_at=_ny_open(day, 77),
            session_date=day,
            source="live",
            kind="decision",
            decision_id=d.id,
            cycle_id=d.cycle_id,
            symbol=d.symbol,
            context="candidate",
            model="gpt-4o-mini",
            temperature=0,
            prompt_version="decision.v1",
            prompt_hash="ab" * 32,
            tokens_in=2800,
            tokens_out=400,
            cost=Decimal("0.00066"),
            latency_ms=840,
            final_action=d.final_action,
            outcome=outcome,
            reasons=[] if outcome == "success" else ["slow"],
        )
    )


def _trade(session: Any, d: Any, day: date, open_day: date, close_day: date,
           entry: Decimal, stop: Decimal, target: Decimal, reason: str, qty: int) -> Decimal:
    """Bracket legs + fills + closed position; returns realized P&L."""
    from app.db.models import Fill, Order, Outcome, Position

    oid = 0

    def order(
        leg: str, otype: str, price: Decimal | None, status: str,
        filled: datetime | None = None, parent: str | None = None,
    ) -> Order:
        nonlocal oid
        oid += 1
        o = Order(
            alpaca_order_id=f"fix-{d.id}-{oid}",
            client_order_id=f"sa-readme-{d.id}-{leg}",
            symbol=d.symbol,
            parent_alpaca_order_id=parent,
            decision_id=d.id,
            side="buy" if leg == "entry" else "sell",
            order_type=otype,
            leg=leg,
            qty=qty,
            limit_price=price if otype == "limit" else None,
            stop_price=price if otype == "stop" else None,
            status=status,
            created_at=_ny_open(day, 80),
            submitted_at=_ny_open(day, 80),
            filled_at=filled,
        )
        session.add(o)
        session.flush()
        return o

    eo = order("entry", "limit", entry, "filled", _ny_open(day, 92))
    session.add(
        Fill(order_id=eo.id, qty=qty, price=entry,
             filled_at=_ny_open(day, 92), slippage=Decimal("0"))
    )
    if reason == "target":
        exit_price = target
        order("stop", "stop", stop, "cancelled", parent=eo.alpaca_order_id)
        xo = order(
            "target", "limit", target, "filled",
            _ny_open(close_day, 300), parent=eo.alpaca_order_id,
        )
    elif reason == "stop":
        exit_price = stop
        order("target", "limit", target, "cancelled", parent=eo.alpaca_order_id)
        xo = order(
            "stop", "stop", stop, "filled",
            _ny_open(close_day, 200), parent=eo.alpaca_order_id,
        )
    else:  # agent_exit | earnings_exit → market sell, both legs cancelled
        exit_price = (entry + target) / 2 if reason == "agent_exit" else entry * Decimal("1.015")
        order("stop", "stop", stop, "cancelled", parent=eo.alpaca_order_id)
        order("target", "limit", target, "cancelled", parent=eo.alpaca_order_id)
        xo = order("exit", "market", None, "filled", _ny_open(close_day, 250))
    session.add(
        Fill(order_id=xo.id, qty=qty, price=exit_price,
             filled_at=xo.filled_at, slippage=Decimal("0"))
    )
    pnl = (exit_price - entry) * qty
    session.add(
        Position(
            symbol=d.symbol,
            origin="agent",
            entry_decision_id=d.id,
            qty=qty,
            avg_entry_price=entry,
            initial_stop=stop,
            current_stop_price=stop,
            target_price=target,
            opened_at=_ny_open(day, 92),
            closed_at=xo.filled_at,
            exit_reason=reason,
            realized_pnl_raw=pnl,
            realized_pnl_adjusted=pnl,
        )
    )
    r = (exit_price - entry) / (entry - stop)
    session.add(
        Outcome(
            decision_id=d.id,
            price_1h=entry * (Decimal(1) + r / 200),
            price_1h_at=_ny_open(day, 152),
            price_1session=exit_price,
            price_1session_at=_ny_open(close_day, 0),
            price_5sessions=exit_price * Decimal("1.01"),
            price_5sessions_at=_ny_open(close_day + timedelta(days=7), 0),
            best_price=target if reason == "target" else exit_price * Decimal("1.005"),
            best_price_at=xo.filled_at,
            worst_price=stop if reason == "stop" else entry * Decimal("0.995"),
            worst_price_at=_ny_open(day, 180),
            first_touch="target" if reason != "stop" else "stop",
        )
    )
    return pnl


def _equity(session: Any, day: date, equity: Decimal, spy: Decimal) -> None:
    from app.db.models import EquitySnapshot

    for i in range(3):
        t = _ny_open(day, 120 + i * 120)
        session.add(
            EquitySnapshot(
                time=t,
                equity=equity + Decimal(i) * 2,
                cash=equity * Decimal("0.75"),
                spy_benchmark_value=spy * (Decimal(1) + Decimal(i) / 1000),
            )
        )



# Weekly-review fixture rows for the 12 seeded trades (symbol, opened,
# closed, exit, r, pnl_usd, regime) — best is index 7, worst index 5.
_WEEKLY_TRADE_ROWS: list[tuple[str, str, str, str, float, float, str]] = [
    ("MSFT", "2026-09-15", "2026-09-21", "target", 1.9, 412.5, "Up · normal vol"),
    ("AAPL", "2026-09-15", "2026-09-24", "target", 2.0, 356.0, "Up · normal vol"),
    ("NVDA", "2026-09-17", "2026-09-21", "stop", -1.0, -188.0, "Up · high vol"),
    ("AMZN", "2026-09-21", "2026-09-28", "agent_exit", 0.8, 96.0, "Sideways · any"),
    ("GOOGL", "2026-09-24", "2026-10-06", "target", 1.8, 298.0, "Up · normal vol"),
    ("META", "2026-09-28", "2026-10-06", "stop", -1.0, -402.0,
     "Down · normal or low"),
    ("JPM", "2026-09-30", "2026-10-09", "earnings_exit", -0.6, -54.0,
     "Up · normal vol"),
    ("QQQ", "2026-10-02", "2026-10-12", "target", 2.1, 488.0, "Up · normal vol"),
    ("XOM", "2026-10-06", "2026-10-09", "agent_exit", -0.7, -61.0,
     "Sideways · any"),
    ("MSFT", "2026-10-07", "2026-10-13", "target", 1.9, 371.0, "Up · normal vol"),
    ("NVDA", "2026-10-09", "2026-10-13", "target", 1.9, 265.0, "Up · high vol"),
    ("AAPL", "2026-10-09", "2026-10-13", "stop", -1.0, -142.0, "Up · normal vol"),
]


def _wt(
    symbol: str, opened: str, closed: str, exit_: str,
    r: float, pnl: float, regime: str,
) -> dict[str, Any]:
    """One facts.trades row, the shape app/reviews/facts.py writes."""
    return {
        "symbol": symbol,
        "opened": opened,
        "closed": closed,
        "exit": exit_,
        "r": r,
        "pnl_usd": pnl,
        "regime": regime,
    }


def _agg(name: str, trades: int, won: int, avg_r: float, pnl: float) -> dict[str, Any]:
    return {"name": name, "trades": trades, "won": won, "avg_r": avg_r, "pnl_usd": pnl}


def main() -> None:
    from app.config import get_config
    from app.db.base import get_session_maker
    from app.db.models import Decision, Review, Run, Verdict

    config = get_config()
    sf = get_session_maker()
    with sf() as session:
        run = session.execute(
            __import__("sqlalchemy").select(Run).order_by(Run.id).limit(1)
        ).scalar_one_or_none()
        if run is None:
            run = Run(started_at=_ny_open(RUN_START), starting_equity=STARTING_EQUITY)
            session.add(run)
            session.flush()
        elif run.started_at > _ny_open(RUN_START):
            # Seeded trades start at RUN_START — pull an existing run's
            # start back so they count toward since-start metrics.
            run.started_at = _ny_open(RUN_START)

        equity = STARTING_EQUITY
        spy = STARTING_EQUITY
        first_decisions: list[Any] = []
        # One cycle per (day, slot): a day's trades take the first slots,
        # its skip slot 3 and the rules no_call slot 4.
        trades_on: dict[int, int] = {}
        for symbol, e, s, t, day_i, close_off, reason in TRADES:
            day = TRADE_DAYS[day_i]
            close_day = TRADE_DAYS[min(day_i + close_off, len(TRADE_DAYS) - 1)]
            slot = trades_on.get(day_i, 0)
            trades_on[day_i] = slot + 1
            c = _cycle(session, day, slot)
            entry, stop, target = Decimal(e), Decimal(s), Decimal(t)
            d = _decision(
                session, c, symbol, "enter", 0.62,
                stop=stop, target=target, price=entry,
            )
            first_decisions.append(d)
            _llm_call(session, d, day)
            qty = max(1, int(Decimal(50) / (entry - stop)))
            pnl = _trade(session, d, day, day, close_day, entry, stop, target, reason, qty)
            equity += pnl
            spy *= Decimal("1.002")
            _equity(session, day, equity, spy)

        for symbol, _action, conf, day_i in SKIPS:
            day = TRADE_DAYS[day_i]
            c = _cycle(session, day, 3)
            d = _decision(session, c, symbol, "skip", float(conf), entry=False)
            _llm_call(session, d, day)

        # One rules-fail no_call (no model call, no llm_calls row).
        c = _cycle(session, TRADE_DAYS[5], 4)
        d = Decision(
            cycle_id=c.id,
            symbol="QQQ",
            context="candidate",
            input_snapshot={"symbol": "QQQ", "regime": {"trend": "up", "volatility": "normal"}},
            rules_result={
                "status": "fail",
                "reason": "RSI 74 > 70",
                "checks": [{"name": "RSI range", "passed": False, "detail": "RSI 74 > 70"}],
            },
            final_action="skip",
            no_call_reason="rules",
            prompt_version="decision.v1",
            prompt_hash="cd" * 32,
            model="gpt-4o-mini",
            cached=False,
        )
        session.add(d)

        # The same shape app/reviews/facts.py writes for a real weekly
        # review (056): facts.trades is the per-trade list, not a count.
        weekly = Review(
            kind="weekly",
            period="2026-W41",
            facts={
                "period": {
                    "id": "2026-W41",
                    "week": 41,
                    "iso_year": 2026,
                    "start": "2026-10-05",
                    "end": "2026-10-09",
                    "label": "5 – 9 Oct",
                    "start_day": 5,
                    "end_day": 9,
                    "due": "2026-10-10",
                    "trading_days": 5,
                },
                "limits": {
                    "weekly_summary_words": 120,
                    "verdict_after_sessions": 5,
                    "skip_flat_pct": 2.0,
                    "min_closed_trades_for_suggestion": 10,
                },
                "return_pct": 3.4,
                "return_usd": 340.0,
                "equity_start": 10000.0,
                "equity_end": 10340.0,
                "spy_return_pct": 2.6,
                "vs_spy_pts": 0.8,
                "max_drawdown_pct": -1.9,
                "trades_closed": 12,
                "won": 7,
                "lost": 5,
                "avg_r": 0.72,
                "model_cost_usd": 0.42,
                "model_calls": 96,
                "reviews_cost_usd": 0.0015,
                "trades": [_wt(*row) for row in _WEEKLY_TRADE_ROWS],
                "best_trade": _wt(*(_WEEKLY_TRADE_ROWS[7])),
                "worst_trade": _wt(*(_WEEKLY_TRADE_ROWS[5])),
                "skips": {
                    "total": 3,
                    "with_outcome": 2,
                    "pending": 1,
                    "up": 1,
                    "flat": 1,
                    "down": 1,
                    "biggest_miss": {
                        "symbol": "GOOGL",
                        "date": "2026-09-17",
                        "confidence": 0.41,
                        "pct": 6.2,
                    },
                },
                "verdicts": {
                    "good_call": 2,
                    "bad_call": 0,
                    "unclear": 1,
                    "total": 3,
                    "entered": {"good_call": 2, "bad_call": 0, "unclear": 0},
                    "blocked": {"good_call": 0, "bad_call": 0, "unclear": 0, "reasons": {}},
                    "skips": {"good_call": 0, "bad_call": 0, "unclear": 1},
                    "thesis_contradictions": 0,
                },
                "sample": {
                    "closed_trades": {"week": 12, "run": 12},
                    "model_skips_with_outcome": {"week": 2, "run": 2},
                    "confirmed_verdicts": {"week": 3, "run": 3},
                },
                "suggestion_allowed": True,
                "by_regime": [
                    _agg("Up · normal vol", 7, 5, 1.2, 1234.5),
                    _agg("Up · high vol", 2, 1, 0.45, 123.0),
                    _agg("Sideways · any", 2, 0, 0.05, 35.0),
                    _agg("Down · normal or low", 1, 0, -1.0, -402.0),
                ],
                "by_symbol": [
                    _agg("MSFT", 2, 2, 1.8, 783.5),
                    _agg("NVDA", 2, 1, 0.45, 77.0),
                    _agg("AAPL", 2, 1, 0.45, 214.0),
                ],
            },
            text=(
                "Week 41: +3.4% vs SPY +2.6%. 12 trades closed, 7 winners, "
                "average +0.72R. The two NVDA stops filled inside the "
                "planned range; no position ran without a stop."
            ),
            content={
                "observations": [
                    "Target exits beat the rules-only baseline by 0.4R.",
                    "Morning cycles produced all entries this week.",
                ],
                "suggestion": "Not applied: consider tightening entry spread on thin IEX books.",
            },
            source="model",
            model="gpt-4o-mini",
            prompt_version="weekly.v1",
            tokens=4100,
            cost=Decimal("0.0011"),
            due_at=_ny_open(date(2026, 10, 10), 510),
            created_at=_ny_open(date(2026, 10, 10), 511),
        )
        session.add(weekly)
        briefing = Review(
            kind="briefing",
            period="2026-10-14",
            facts={"open_positions": 0, "earnings": ["JPM tomorrow"], "regime": "Up / Normal"},
            text=(
                "No open positions. JPM reports before tomorrow's open — "
                "its blackout already blocks new entries. Regime Up/Normal. "
                "Yesterday: +0.4%."
            ),
            source="model",
            model="gpt-4o-mini",
            prompt_version="briefing.v1",
            tokens=1800,
            cost=Decimal("0.0004"),
            due_at=_ny_open(date(2026, 10, 14), 540),
            created_at=_ny_open(date(2026, 10, 14), 541),
        )
        session.add(briefing)
        session.flush()
        for i, d in enumerate(first_decisions[:3]):
            session.add(
                Verdict(
                    decision_id=d.id,
                    verdict="good_call" if i != 2 else "unclear",
                    reason=(
                        "Target reached inside 3 sessions."
                        if i != 2 else "Stopped on a market-wide dip."
                    ),
                    source="draft",
                    facts={"r": 2.0, "exit": "target"},
                    created_at=_ny_open(TRADE_DAYS[i + 4], 400),
                    updated_at=_ny_open(TRADE_DAYS[i + 4], 400),
                )
            )
        session.commit()

    # Daily snapshots the session-close jobs would have written. The app's
    # own jobs only run them on a live clock, so the seed calls the same
    # functions the jobs call, for the last seeded session.

    from app.data.fake import FakeCalendarSource
    from app.insights.service import write_snapshots as write_insights
    from app.market.clock import FakeClock
    from app.readiness.service import write_snapshot as write_readiness

    cal = FakeCalendarSource()
    clock = FakeClock(datetime(2026, 10, 15, 17, 0, tzinfo=UTC))
    last = TRADE_DAYS[-1]
    with sf() as session:
        write_insights(session, config=config, calendar=cal, clock=clock, session_date=last)
        session.commit()
        write_readiness(session, config=config, calendar=cal, clock=clock, session_date=last)
        session.commit()


if __name__ == "__main__":
    main()
