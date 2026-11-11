"""Prepare the shared Playwright database before the e2e server starts.

Test-only (never imported by the app). The e2e server's clock is frozen at
Thursday 15 October 2026; a run that starts at that same moment never has
a weekly review due, so the Reviews e2e had nothing to open. Starting the
e2e run on Monday 5 October makes the Saturday 10 October weekly review
due, and the app's own startup catch-up writes it (docs/SPEC.md, Reviews —
Missed runs).

Usage (from backend/, with the e2e env): ``../.venv/bin/python -m tests.e2e_seed``
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

E2E_RUN_START = datetime(2026, 10, 5, 13, 30)  # Monday 09:30 New York, naive UTC


def main() -> None:
    from app.config import get_config
    from app.db.base import get_session_maker
    from app.db.models import Run

    Path(get_config().env.sa_db_path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    with get_session_maker()() as session:
        first = session.execute(select(Run).order_by(Run.id).limit(1)).scalar_one_or_none()
        if first is None:
            starting = Decimal(
                str(get_config().get("performance", "starting_equity", default=10000))
            )
            session.add(Run(started_at=E2E_RUN_START, starting_equity=starting, spy_close=None))
        elif first.started_at > E2E_RUN_START:
            first.started_at = E2E_RUN_START
        session.commit()
    seed_v2_decision()
    seed_economic_events()


def seed_economic_events() -> None:
    """Item 067 seed: release-calendar rows the e2e server picks up.

    CPI lands the next session at 08:30 — the dashboard's next-release
    line and the Settings "08:30 releases tracked" row read the stored
    rows (the app's startup refresh also syncs the config FOMC dates).
    The seeded v2 decision gets the ``release_next_morning`` tag so its
    API payload shows the entry-release metadata.
    """
    from app.db.base import get_session_maker
    from app.db.models import Decision, EconomicEvent

    rows = [
        # Friday 16 Oct — one session away from the frozen 15 Oct.
        ("cpi", date(2026, 10, 16), "08:30", "fred"),
        # Monday 19 Oct — within the 2-session next_release horizon only
        # if CPI weren't first; still tracked in the calendar.
        ("pce", date(2026, 10, 19), "08:30", "fred"),
    ]
    with get_session_maker()() as session:
        for kind, day, hhmm, source in rows:
            exists = session.execute(
                select(EconomicEvent.id).where(
                    EconomicEvent.release_type == kind,
                    EconomicEvent.date == day,
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    EconomicEvent(
                        release_type=kind,
                        date=day,
                        time=hhmm,
                        source=source,
                        fetched_at=datetime(2026, 10, 15, 12, 0),
                    )
                )
        decision = session.execute(
            select(Decision).where(Decision.prompt_version == "decision.v2")
        ).scalar_one_or_none()
        if decision is not None:
            decision.release_next_morning = [
                {
                    "type": "cpi",
                    "label": "CPI",
                    "date": "2026-10-16",
                    "time": "08:30",
                    "at": "2026-10-16T12:30:00+00:00",
                }
            ]
        session.commit()


def seed_v2_decision() -> None:
    """One decision.v2 row for the drawer's PR 21 screenshots.

    The e2e run's live prompt stays decision.v1; this synthetic row is
    the same JSON the v2 path writes (levels, options, chosen option,
    checks) so the drawer can be shot with real layout. Test-only —
    the e2e DB is scratch data.
    """
    from app.db.base import get_session_maker
    from app.db.models import Cycle, Decision, LlmCall

    levels = [
        {
            "id": "L1", "price": 433.40,
            "kind": "swing_high", "timeframe": "daily", "time": "2026-10-08",
        },
        {
            "id": "L2", "price": 427.85,
            "kind": "swing_high", "timeframe": "daily", "time": "2026-10-01",
        },
        {
            "id": "L3", "price": 423.20,
            "kind": "swing_high", "timeframe": "1h", "time": "2026-10-14",
        },
        {
            "id": "L4", "price": 421.75,
            "kind": "prior_session_high", "timeframe": "session", "time": None,
        },
        {"id": "L5", "price": 417.90, "kind": "vwap", "timeframe": "session", "time": None},
        {"id": "L6", "price": 416.40, "kind": "session_low", "timeframe": "session", "time": None},
        {
            "id": "L7", "price": 414.95,
            "kind": "prior_session_low", "timeframe": "session", "time": None,
        },
        {
            "id": "L8", "price": 410.50,
            "kind": "swing_low", "timeframe": "1h", "time": "2026-10-14",
        },
        {
            "id": "L9", "price": 408.85,
            "kind": "swing_low", "timeframe": "daily", "time": "2026-10-06",
        },
    ]
    options = [
        {"id": "A", "stop": 411.90, "stop_basis": "2.0 × ATR", "stop_atr": 2.00,
         "stop_level": None, "target": 431.59, "target_basis": "below L1 · October high",
         "target_level": "L1", "rr": 2.03, "risk": 6.50, "levels_in_way": ["L3", "L2"]},
        {"id": "B", "stop": 411.90, "stop_basis": "2.0 × ATR", "stop_atr": 2.00,
         "stop_level": None, "target": 437.90, "target_basis": "3R",
         "target_level": None, "rr": 3.00, "risk": 6.50, "levels_in_way": ["L3", "L2", "L1"]},
        {"id": "C", "stop": 409.64, "stop_basis": "below L8 · 1h swing low", "stop_atr": 2.73,
         "stop_level": "L8", "target": 431.59, "target_basis": "below L1 · October high",
         "target_level": "L1", "rr": 1.51, "risk": 8.76, "levels_in_way": ["L3", "L2"]},
        {"id": "D", "stop": 409.64, "stop_basis": "below L8 · 1h swing low", "stop_atr": 2.73,
         "stop_level": "L8", "target": 435.92, "target_basis": "2R",
         "target_level": None, "rr": 2.00, "risk": 8.76, "levels_in_way": ["L3", "L2", "L1"]},
        {"id": "E", "stop": 409.64, "stop_basis": "below L8 · 1h swing low", "stop_atr": 2.73,
         "stop_level": "L8", "target": 444.68, "target_basis": "3R",
         "target_level": None, "rr": 3.00, "risk": 8.76, "levels_in_way": ["L3", "L2", "L1"]},
    ]
    snapshot = {
        "symbol": "MSFT", "context": "candidate",
        "time_ny": "2026-10-15T15:46:00-04:00",
        "price": 418.40, "stop_reference_price": "418.05", "last_bar_close": "418.05",
        "indicators_1h": {"atr14": 3.21, "rsi14": 58.2, "ema20": 415.60, "ema50": 412.30,
                          "vwap": 417.90, "obv": "up"},
        "indicators_daily": {"close": 416.55, "sma50": 398.20, "sma200": 375.40,
                             "atr_percentile": 52.0, "gap_pct": 0.44},
        "constraints": {"atr_1h": 3.21, "entry_limit": 418.40,
                        "stop_atr_min": 1.5, "stop_atr_max": 4.0, "min_reward_risk": 1.5},
        "levels": levels, "options": options,
        "headlines": [], "watchlist_rank": 3,
        "quote": {"bid": 418.34, "ask": 418.46, "spread_pct": 0.03, "synthetic": False},
    }
    with get_session_maker()() as session:
        if session.execute(
            select(Decision.id).where(Decision.prompt_version == "decision.v2")
        ).scalar_one_or_none() is not None:
            return
        cycle = Cycle(
            kind="cycle",
            scheduled_time=datetime(2026, 10, 15, 19, 46),
            started_at=datetime(2026, 10, 15, 19, 46),
            finished_at=datetime(2026, 10, 15, 19, 46, 12),
            duration_seconds=12.0,
            status="done",
            # Item 068: the breadth shares and sector table the GUI's
            # Sectors now card and watchlist column read.
            breadth_above_sma50_pct=Decimal("31.00"),
            breadth_above_sma200_pct=Decimal("44.00"),
            breadth_ema_up_pct=Decimal("36.00"),
            breadth_up_today_pct=Decimal("44.00"),
            sectors={
                "Technology": {"etf": "XLK", "rs_vs_spy_pct": 2.31, "rank": 2},
                "Financials": {"etf": "XLF", "rs_vs_spy_pct": 0.84, "rank": 5},
                "Communication Services": {"etf": "XLC", "rs_vs_spy_pct": -0.42, "rank": 8},
                "Industrials": {"etf": "XLI", "rs_vs_spy_pct": 0.15, "rank": 7},
                "Consumer Discretionary": {"etf": "XLY", "rs_vs_spy_pct": 1.12, "rank": 4},
                "Materials": {"etf": "XLB", "rs_vs_spy_pct": -1.05, "rank": 10},
                "Health Care": {"etf": "XLV", "rs_vs_spy_pct": 0.61, "rank": 6},
                "Consumer Staples": {"etf": "XLP", "rs_vs_spy_pct": -0.18, "rank": 9},
                "Energy": {"etf": "XLE", "rs_vs_spy_pct": 1.84, "rank": 3},
                "Utilities": {"etf": "XLU", "rs_vs_spy_pct": -1.62, "rank": 11},
                "Real Estate": {"etf": "XLRE", "rs_vs_spy_pct": 2.97, "rank": 1},
            },
        )
        session.add(cycle)
        session.flush()
        decision = Decision(
                cycle_id=cycle.id,
                symbol="MSFT",
                context="candidate",
                input_snapshot=snapshot,
                rules_result={
                    "status": "pass",
                    "checks": [
                        {"name": "market up", "passed": True, "detail": "SPY above 50 SMA"},
                        {"name": "rsi window", "passed": True, "detail": "58.2"},
                        {"name": "atr band", "passed": True, "detail": "52nd percentile"},
                    ],
                },
                model_output={
                    "symbol": "MSFT", "action": "enter", "option_id": "D",
                    "confidence": 0.61,
                    "levels_used": ["L1", "L8"],
                    "thesis": "Breakout above the 1h consolidation; D keeps the stop "
                            "under the 1h swing low L8 while reaching the 2R target "
                            "just under the October high L1.",
                    "risks": ["earnings within two weeks", "sector breadth narrowing"],
                    "news_used": [],
                },
                validation_result={
                    "valid": True, "model_action": "enter",
                    "checks": [
                        {"name": "option id", "passed": True, "value": "Option D offered"},
                        {"name": "explanation check", "passed": True, "value": "passed"},
                    ],
                },
                gates_result={
                    "blocked_by": None,
                    # Live gate names (app/risk/gates.py), never retired ones.
                    "checks": [
                        {"name": "kill_switch", "passed": True, "detail": "off"},
                        {"name": "paused", "passed": True, "detail": "entries active"},
                        {"name": "max_open_positions", "passed": True, "detail": "2 of 4"},
                        {"name": "daily_loss_limit", "passed": True,
                         "detail": "session P&L -0.20%"},
                        {"name": "position_size", "passed": True,
                         "detail": "4 shares, limited by open_risk"},
                    ],
                    "sizing": {"shares": 4, "limited_by": ["open_risk"]},
                },
                final_action="enter",
                prompt_version="decision.v2",
                prompt_hash="a1" * 32,
                model="gpt-4o-mini",
                levels={"levels": levels},
                options={
                    "options_version": "v2",
                    "options": options,
                    "merged": ["stop under L8 and a 2.7 × ATR stop merged — kept L8 first"],
                    "level_notes": {"L4": "too close for a stop · 1.04 ATR",
                                     "L7": "inside the stop band · no option needed"},
                },
                chosen_option="D",
                explanation_check={"passed": True, "bad_ids": [], "bad_numbers": []},
                market_context={
                    "sector_etf": "XLK",
                    "sector_rank": 2,
                    "sector_rs_vs_spy_pct": 2.31,
                    "rs_vs_sector_pct": 0.64,
                },
        )
        session.add(decision)
        session.flush()
        # A real model decision always has its llm_calls row — the drawer's
        # "Open model call" link and the inspector's input snapshot read it.
        session.add(
            LlmCall(
                sent_at=datetime(2026, 10, 15, 19, 46, 10),
                session_date=date(2026, 10, 15),
                source="live",
                kind="decision",
                decision_id=decision.id,
                cycle_id=cycle.id,
                symbol="MSFT",
                context="candidate",
                model="gpt-4o-mini",
                temperature=0,
                prompt_version="decision.v2",
                prompt_hash="a1" * 32,
                tokens_in=2800,
                tokens_out=400,
                cost=Decimal("0.00066"),
                latency_ms=840,
                final_action="enter",
                outcome="success",
                reasons=[],
            )
        )
        session.commit()


if __name__ == "__main__":
    main()
