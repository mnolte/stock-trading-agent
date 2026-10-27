"""Insights API (docs/SPEC.md, Insights — PR 22).

``GET /api/insights?strategy=&period=`` returns the latest stored
snapshot for the strategy ("live" or a strategy id) and period
(``run`` | ``last20`` | ``all``) — numbers only, never recomputed.

``GET /api/insights/trades?strategy=&period=&dimension=&slice=`` returns
the trades behind one slice row.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.db.base import get_session_maker
from app.db.models import Position, ShadowPosition, Strategy
from app.insights.service import latest_snapshot
from app.shadows.strategy import NON_ARCHIVED

router = APIRouter()

PERIODS = ("run", "last20", "all")


def _strategy(session: Any, raw: str | None) -> int | None:
    """"live" or a strategy id → its row id (None for live)."""
    if raw in (None, "", "live"):
        return None
    try:
        sid = int(str(raw))
    except ValueError:
        raise HTTPException(status_code=422, detail=["unknown strategy"]) from None
    row = session.get(Strategy, sid)
    if row is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return sid


def _strategy_list(session: Any) -> list[dict[str, Any]]:
    out = [{"id": "live", "name": "Live", "kind": "llm"}]
    for s in session.execute(
        select(Strategy)
        .where(Strategy.status.in_(NON_ARCHIVED))
        .order_by(Strategy.id)
    ).scalars():
        out.append({"id": str(s.id), "name": s.name, "kind": s.kind})
    return out


@router.get("/insights")
def insights(request: Request, strategy: str | None = None, period: str = "run") -> dict[str, Any]:
    if period not in PERIODS:
        raise HTTPException(status_code=422, detail=["period: run | last20 | all"])
    with get_session_maker()() as session:
        strategy_id = _strategy(session, strategy)
        snapshot = latest_snapshot(session, strategy_id=strategy_id, period=period)
        data = dict(snapshot.data) if snapshot is not None else {}
        return {
            "strategies": _strategy_list(session),
            "period": period,
            "strategy": strategy or "live",
            "empty": snapshot is None,
            "snapshot": (
                {
                    "session_date": snapshot.session_date.isoformat(),
                    "computed_at": snapshot.computed_at.isoformat(),
                    "period": period,
                    **data,
                }
                if snapshot is not None
                else None
            ),
        }


@router.get("/insights/trades")
def insight_trades(
    strategy: str | None = None,
    period: str = "run",
    dimension: str | None = None,
    slice: str | None = None,
) -> dict[str, Any]:
    """The closed trades behind one slice row of the latest snapshot."""
    if period not in PERIODS:
        raise HTTPException(status_code=422, detail=["period: run | last20 | all"])
    if not dimension or slice is None:
        raise HTTPException(status_code=422, detail=["dimension and slice are required"])
    with get_session_maker()() as session:
        strategy_id = _strategy(session, strategy)
        snapshot = latest_snapshot(session, strategy_id=strategy_id, period=period)
        if snapshot is None:
            return {"trades": [], "empty": True}
        dim = (snapshot.data.get("slices") or {}).get(dimension)
        if dim is None:
            raise HTTPException(status_code=404, detail="unknown dimension")
        row = next(
            (r for r in dim.get("rows") or [] if r.get("slice") == slice), None
        )
        if row is None:
            raise HTTPException(status_code=404, detail="unknown slice")
        ids = row.get("trade_ids") or []
        trades: list[dict[str, Any]] = []
        if strategy_id is None:
            for p in session.execute(
                select(Position).where(Position.id.in_(ids or {0}))
            ).scalars():
                trades.append(
                    {
                        "id": p.id,
                        "symbol": p.symbol,
                        "qty": p.qty,
                        "entry": str(p.avg_entry_price),
                        "opened_at": p.opened_at.isoformat(),
                        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
                        "exit_reason": p.exit_reason,
                        "pnl": str(p.realized_pnl_raw) if p.realized_pnl_raw is not None else None,
                    }
                )
        else:
            for sp in session.execute(
                select(ShadowPosition).where(ShadowPosition.id.in_(ids or {0}))
            ).scalars():
                trades.append(
                    {
                        "id": sp.id,
                        "symbol": sp.symbol,
                        "qty": sp.qty,
                        "entry": str(sp.entry) if sp.entry is not None else None,
                        "opened_at": sp.opened_at.isoformat() if sp.opened_at else None,
                        "closed_at": sp.closed_at.isoformat() if sp.closed_at else None,
                        "exit_reason": sp.exit_reason,
                        "pnl": str(sp.pnl) if sp.pnl is not None else None,
                        "r": str(sp.r) if sp.r is not None else None,
                    }
                )
        return {"trades": trades, "slice": slice, "dimension": dimension}
