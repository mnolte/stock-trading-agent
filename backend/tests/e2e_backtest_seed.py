"""Seed a finished backtest run before the e2e server starts.

Test-only (never imported by the app). The e2e server's clock is frozen, so
no real backtest can run in the browser tests. This module writes the same
artifacts the worker writes for a finished run — a ``done`` row in the
live database plus ``run-<id>/`` with one strategy database per strategy
and a ``market.db`` — so the Results panels can be exercised end to end
(docs/SPEC.md, Backtesting, Results).

Usage (from backend/, after e2e_seed, with the e2e env):
``../.venv/bin/python -m tests.e2e_backtest_seed``
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

START = date(2026, 9, 14)  # Monday
END = date(2026, 9, 25)  # Friday, 10 sessions
EQUITY = Decimal(10000)

STRATEGIES: list[dict[str, Any]] = [
    {
        "ref": "live",
        "name": "Live settings",
        "kind": "llm",
        "slug": "live",
        "model": "gpt-4o-mini",
        "prompt_version": "decision.v1",
        "temperature": 0.2,
    },
    {
        "ref": "rules_only",
        "name": "Rules only",
        "kind": "rules_only",
        "slug": "rules-only",
        "model": None,
        "prompt_version": None,
        "temperature": None,
    },
]

# (symbol, open date, close date | None, exit reason, pnl)
TRADES = [
    ("AAA", date(2026, 9, 15), date(2026, 9, 17), "target", Decimal("180")),
    ("BBB", date(2026, 9, 16), date(2026, 9, 18), "stop", Decimal("-95")),
    ("AAA", date(2026, 9, 21), date(2026, 9, 23), "earnings", Decimal("60")),
    ("CCC", date(2026, 9, 24), None, None, None),
]


def _sessions() -> list[date]:
    out: list[date] = []
    cursor = START
    while cursor <= END:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _ny_open(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 13, 30)  # naive UTC


def _seed_strategy_db(
    sf: sessionmaker[Session], *, kind: str, final_equity: Decimal
) -> None:
    """Write cycles, decisions, trades, equity and SPY bars the way the
    worker leaves them — so ``summarise`` and the API read real rows."""
    from app.db.models import (
        Bar1d,
        Cycle,
        Decision,
        EquitySnapshot,
        Position,
    )

    sessions = _sessions()
    llm = kind == "llm"
    with sf() as session:
        cycle_ids: dict[date, int] = {}
        for i, day in enumerate(sessions):
            cycle = Cycle(
                kind="cycle",
                scheduled_time=_ny_open(day),
                started_at=_ny_open(day),
                finished_at=_ny_open(day) + timedelta(seconds=42),
                duration_seconds=42.0,
                status="done",
            )
            session.add(cycle)
            session.flush()
            cycle_ids[day] = cycle.id
            t = i / (len(sessions) - 1)
            equity = EQUITY + (final_equity - EQUITY) * Decimal(str(t))
            session.add(
                EquitySnapshot(
                    time=_ny_open(day) + timedelta(hours=6, minutes=30),
                    equity=equity,
                    cash=equity * Decimal("0.4"),
                    spy_benchmark_value=EQUITY * (Decimal(1) + Decimal(i) / 200),
                )
            )
            session.add(
                Bar1d(
                    symbol="SPY",
                    date=day,
                    open=Decimal("550") + i,
                    high=Decimal("552") + i,
                    low=Decimal("549") + i,
                    close=Decimal("551") + i,
                    volume=50_000_000,
                )
            )
        for symbol, opened, closed, reason, pnl in TRADES:
            # The third entry replays an earlier model answer for free.
            cached_hit = llm and symbol == "AAA" and closed is not None and pnl == Decimal("60")
            decision = Decision(
                cycle_id=cycle_ids[opened],
                symbol=symbol,
                context="candidate",
                input_snapshot={
                    "symbol": symbol,
                    "regime": {"trend": "up", "volatility": "normal"},
                },
                rules_result={
                    "status": "pass",
                    "reason": None,
                    "checks": [
                        {
                            "name": "Warming up",
                            "passed": True,
                            "detail": "115 of 90 daily bars",
                        },
                        {
                            "name": "RSI range",
                            "passed": True,
                            "detail": "RSI 55 in 40–70",
                        },
                    ],
                },
                model_output=(
                    {
                        "action": "enter",
                        "confidence": 0.7,
                        "stop_price": 96.0,
                        "target_price": 112.0,
                        "thesis": "Uptrend with room to run; RSI in range.",
                        "risks": ["earnings next month", "thin volume"],
                        "news_used": [],
                    }
                    if llm
                    else None
                ),
                validation_result=(
                    {
                        "valid": True,
                        "model_action": "enter",
                        "final_action": "enter",
                        "changed_by_code": False,
                        "error": None,
                        "note": None,
                        "checks": [],
                    }
                    if llm
                    else None
                ),
                gates_result={
                    "allowed": True,
                    "blocked_by": None,
                    "risk_factor": "1.00",
                    "sizing": {
                        "shares": 10,
                        "risk_budget": "40.00",
                        "limited_by": None,
                    },
                    "checks": [],
                },
                final_action="enter",
                prompt_version="decision.v1" if llm else "none",
                prompt_hash="ab" * 32,
                model="gpt-4o-mini" if llm else "none",
                tokens=None if cached_hit else (3200 if llm else None),
                cost=None if cached_hit else (Decimal("0.00048") if llm else None),
                latency_ms=None if cached_hit else (820 if llm else None),
                cached=cached_hit,
            )
            session.add(decision)
            session.flush()
            session.add(
                Position(
                    symbol=symbol,
                    origin="agent",
                    entry_decision_id=decision.id,
                    qty=10,
                    avg_entry_price=Decimal("100"),
                    current_stop_price=Decimal("96"),
                    target_price=Decimal("112"),
                    opened_at=_ny_open(opened) + timedelta(minutes=15),
                    closed_at=(
                        _ny_open(closed) + timedelta(hours=5)
                        if closed
                        else None
                    ),
                    exit_reason=reason,
                    realized_pnl_raw=pnl,
                    realized_pnl_adjusted=pnl,
                )
            )
        # One cached call and one skip, so the Calls/Cache/SKIP columns show.
        session.add(
            Decision(
                cycle_id=cycle_ids[sessions[2]],
                symbol="DDD",
                context="candidate",
                input_snapshot={"symbol": "DDD"},
                rules_result={
                    "status": "fail",
                    "reason": "RSI 81 > 70",
                    "checks": [
                        {
                            "name": "RSI range",
                            "passed": False,
                            "detail": "RSI 81 > 70",
                        }
                    ],
                },
                final_action="skip",
                no_call_reason="rules",
                prompt_version="decision.v1" if llm else "none",
                prompt_hash="cd" * 32,
                model="gpt-4o-mini" if llm else "none",
                cached=False,
            )
        )
        session.commit()


def main() -> None:
    from app.backtests.bundle import init_strategy_db
    from app.backtests.results import summarise
    from app.backtests.store import (
        dir_size,
        make_market_db,
        run_dir_for,
        session_maker_for,
    )
    from app.config import get_config
    from app.db.base import get_session_maker
    from app.db.models import Backtest

    config = get_config()
    sf = get_session_maker()
    with sf() as session:
        existing = session.execute(
            select(Backtest).where(Backtest.status == "done")
        ).scalars().first()
        if existing is not None:
            if existing.summary:
                return
            session.delete(existing)
            session.commit()
        row = Backtest(
            name=f"{START.isoformat()} – {END.isoformat()}",
            start_date=START,
            end_date=END,
            universe_mode="fixed",
            strategies=STRATEGIES,
            starting_equity=EQUITY,
            slippage_pct=Decimal("0.05"),
            status="done",
            progress="10 of 10 sessions",
            cost_usd=Decimal("0.0048"),
            run_dir="",
            created_at=datetime(2026, 10, 14, 20, 0),
            finished_at=datetime(2026, 10, 14, 20, 4),
        )
        session.add(row)
        session.flush()
        run_dir = run_dir_for(config, row.id)
        run_dir.mkdir(parents=True, exist_ok=True)
        row.run_dir = str(run_dir)
        session.commit()
        run_id = row.id

    make_market_db(run_dir / "market.db", config)
    summary: dict[str, object] = {}
    for spec in STRATEGIES:
        db = run_dir / f"{spec['slug']}.db"
        init_strategy_db(db)
        sdb = session_maker_for(db, config)
        final = Decimal("10310") if spec["kind"] == "llm" else Decimal("10080")
        _seed_strategy_db(sdb, kind=str(spec["kind"]), final_equity=final)
        summary[str(spec["slug"])] = summarise(
            sdb, config=config, equity=EQUITY
        )

    with sf() as session:
        saved = session.get(Backtest, run_id)
        assert saved is not None
        saved.summary = summary
        saved.warnings = [
            "Spreads are assumed, not measured — the free data plan has "
            "no historical quotes.",
            "A bar that touches both stop and target counts the stop "
            "first.",
        ]
        saved.size_bytes = dir_size(run_dir)
        session.commit()


if __name__ == "__main__":
    main()
