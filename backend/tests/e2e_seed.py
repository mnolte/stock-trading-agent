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

from datetime import datetime
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


def seed_v2_decision() -> None:
    """One decision.v2 row for the drawer's PR 21 screenshots.

    The e2e run's live prompt stays decision.v1; this synthetic row is
    the same JSON the v2 path writes (levels, options, chosen option,
    checks) so the drawer can be shot with real layout. Test-only —
    the e2e DB is scratch data.
    """
    from app.db.base import get_session_maker
    from app.db.models import Cycle, Decision

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
        "time_ny": "2026-10-15T16:46:00-04:00",
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
            scheduled_time=datetime(2026, 10, 15, 20, 46),
            started_at=datetime(2026, 10, 15, 20, 46),
            finished_at=datetime(2026, 10, 15, 20, 46, 12),
            duration_seconds=12.0,
            status="done",
        )
        session.add(cycle)
        session.flush()
        session.add(
            Decision(
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
                    "checks": [
                        {"name": "risk per trade", "passed": True, "detail": "0.80% equity"},
                        {"name": "open risk", "passed": True, "detail": "1.9% of 3%"},
                        {"name": "positions", "passed": True, "detail": "2 of 5"},
                        {"name": "day trades", "passed": True, "detail": "0 of 3"},
                        {"name": "daily loss", "passed": True, "detail": "-0.2% of -2%"},
                    ],
                    "sizing": {"shares": 4, "limited_by": []},
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
            )
        )
        session.commit()


if __name__ == "__main__":
    main()
