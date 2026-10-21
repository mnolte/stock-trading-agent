"""Per-strategy run results (docs/SPEC.md, Backtesting, Results).

Computed from the strategy's own database after the run: return, the
difference with SPY buy-and-hold over the same sessions, max drawdown,
closed trades, win rate, average R, profit factor and time in market, the
equity curve with SPY, and breakdowns by regime and symbol.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.db.models import Bar1d, Decision, EquitySnapshot, Position
from app.engine.trades import _trade_r
from app.market.clock import NY_TZ


def summarise(
    sessions: sessionmaker[Session], *, config: AppConfig, equity: Decimal
) -> dict[str, Any]:
    """One strategy's metrics over its own run database."""
    with sessions() as session:
        snaps = session.execute(
            select(EquitySnapshot).order_by(EquitySnapshot.time)
        ).scalars().all()
        curve: dict[date, Decimal] = {}
        for s in snaps:
            curve[_day(s.time)] = s.equity
        points: list[dict[str, Any]] = [
            {"date": d.isoformat(), "equity": float(v)}
            for d, v in sorted(curve.items())
        ]
        final_equity = snaps[-1].equity if snaps else equity
        ret_pct = (final_equity - equity) / equity * 100 if equity else Decimal(0)

        spy = _spy_curve(session)
        spy_ret: Decimal | None = None
        spy_points: list[dict[str, Any]] = []
        if points and spy:
            spy_map = dict(spy)
            day0 = date.fromisoformat(points[0]["date"])
            spy_first = spy_map.get(day0.isoformat())
            if spy_first:
                spy_last = spy_map.get(points[-1]["date"])
                if spy_last:
                    spy_ret = (spy_last - spy_first) / spy_first * 100
                spy_points = [
                    {
                        "date": p["date"],
                        "equity": float(
                            equity
                            * spy_map.get(p["date"], spy_first)
                            / spy_first
                        ),
                    }
                    for p in points
                ]

        closed = session.execute(
            select(Position).where(Position.closed_at.is_not(None))
        ).scalars().all()
        def _adj(p: Position) -> Decimal:
            return (
                p.realized_pnl_adjusted
                if p.realized_pnl_adjusted is not None
                else Decimal(0)
            )

        wins = [p for p in closed if _adj(p) > 0]
        gross_w: Decimal = sum((_adj(p) for p in wins), Decimal(0))
        gross_l: Decimal = abs(sum((_adj(p) for p in closed if _adj(p) <= 0), Decimal(0)))
        # R uses the stop the entry decision proposed, like live's
        # Performance page — not the trailed stop.
        decisions_by_id = {
            d.id: d for d in session.execute(select(Decision)).scalars()
        }

        def _r(p: Position) -> Decimal | None:
            decision = (
                decisions_by_id.get(p.entry_decision_id)
                if p.entry_decision_id is not None
                else None
            )
            stop: Decimal | None = None
            if decision is not None:
                raw = (decision.model_output or {}).get("stop_price")
                if isinstance(raw, int | float):
                    stop = Decimal(str(raw))
            return _trade_r(p, stop)

        rs = [r for p in closed if (r := _r(p)) is not None]
        max_dd = _max_drawdown(points, float(equity))
        decisions = session.execute(
            select(func.count(Decision.id))
        ).scalar_one()
        calls = session.execute(
            select(func.count(Decision.id)).where(
                Decision.latency_ms.is_not(None)
            )
        ).scalar_one()
        cached = session.execute(
            select(func.count(Decision.id)).where(Decision.cached.is_(True))
        ).scalar_one()
        cost = session.execute(
            select(func.coalesce(func.sum(Decision.cost), 0)).where(
                Decision.cached.is_(False)
            )
        ).scalar_one()
        by_symbol = _by_symbol(closed)
        by_regime = _by_regime(session, closed)
        time_in_market = _sessions_in_market(session, closed)

    return {
        "return_pct": float(ret_pct),
        "vs_spy_pts": float(ret_pct - spy_ret) if spy_ret is not None else None,
        "spy_return_pct": float(spy_ret) if spy_ret is not None else None,
        "max_drawdown_pct": max_dd,
        "closed_trades": len(closed),
        "win_rate": float(len(wins) / len(closed)) if closed else None,
        "avg_r": float(sum(rs) / len(rs)) if rs else None,
        "profit_factor": float(gross_w / gross_l) if gross_l else None,
        "time_in_market": time_in_market,
        "decisions": decisions,
        "calls": calls,
        "cache_hits": cached,
        "cost_usd": float(Decimal(str(cost))),
        "curve": points,
        "spy_curve": spy_points,
        "by_symbol": by_symbol,
        "by_regime": by_regime,
    }


def _day(dt: datetime | date) -> date:
    if not isinstance(dt, datetime):
        return dt
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(NY_TZ).date()


def _spy_curve(session: Session) -> list[tuple[str, Decimal]]:
    rows = session.execute(
        select(Bar1d).where(Bar1d.symbol == "SPY").order_by(Bar1d.date)
    ).scalars().all()
    return [(r.date.isoformat(), r.close) for r in rows]


def _max_drawdown(points: list[dict[str, Any]], equity: float) -> float | None:
    if not points:
        return None
    peak = equity
    worst = 0.0
    for p in points:
        eq = float(p["equity"])
        peak = max(peak, eq)
        if peak:
            worst = min(worst, (eq - peak) / peak * 100)
    return worst


def _sessions_in_market(
    session: Session, closed: Sequence[Position]
) -> float | None:
    """Share of sessions with at least one position open."""
    days = session.execute(
        select(func.distinct(func.date(EquitySnapshot.time)))
    ).scalars().all()
    if not days:
        return None
    session_days = {
        date.fromisoformat(d) if isinstance(d, str) else d for d in days
    }
    covered: set[date] = set()
    for p in closed:
        start = _day(p.opened_at)
        end = _day(p.closed_at) if p.closed_at else start
        cursor = start
        while cursor <= end:
            if cursor in session_days:
                covered.add(cursor)
            cursor += timedelta(days=1)
    return float(len(covered) / len(session_days))


def _by_symbol(closed: Sequence[Position]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in closed:
        row = out.setdefault(p.symbol, {"trades": 0, "pnl": 0.0, "wins": 0})
        row["trades"] += 1
        row["pnl"] += float(p.realized_pnl_adjusted or 0)
        if (p.realized_pnl_adjusted or 0) > 0:
            row["wins"] += 1
    return out


def _by_regime(
    session: Session, closed: Sequence[Position]
) -> dict[str, dict[str, Any]]:
    """Closed trades grouped by the regime at their entry decision."""
    out: dict[str, dict[str, Any]] = {}
    for p in closed:
        regime = "unknown"
        entry_stop: Decimal | None = None
        if p.entry_decision_id is not None:
            d = session.get(Decision, p.entry_decision_id)
            if d is not None:
                raw_stop = (d.model_output or {}).get("stop_price")
                if isinstance(raw_stop, int | float):
                    entry_stop = Decimal(str(raw_stop))
                if d.input_snapshot:
                    raw = cast(
                        dict[str, Any], d.input_snapshot.get("regime") or {}
                    )
                    regime = (
                        f"{raw.get('trend') or 'unknown'}"
                        f"_{raw.get('volatility') or 'unknown'}"
                    )
        row = out.setdefault(regime, {"trades": 0, "pnl": 0.0, "rs": []})
        row["trades"] += 1
        row["pnl"] += float(p.realized_pnl_adjusted or 0)
        r = _trade_r(p, entry_stop)
        if r is not None:
            row["rs"].append(r)
    return {
        regime: {
            "trades": row["trades"],
            "pnl": row["pnl"],
            "avg_r": (
                float(sum(row["rs"]) / len(row["rs"])) if row["rs"] else None
            ),
        }
        for regime, row in out.items()
    }
