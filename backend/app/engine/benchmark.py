"""SPY's total return over a span, dividends included (item 108,
docs/SPEC.md → Paper realism → Dividends, like for like).

Every "vs SPY" figure — the Performance page, the shadow portfolio, the
readiness state, the weekly review, backtest results and the insights —
uses this one function. Dividends are not reinvested: they simply add
to the end value.

Two shapes of the same number:

- ``spy_return`` is a percentage over an arbitrary span, from two SPY
  prices and the stored corporate actions.
- ``spy_benchmark_with_dividends`` grows a stored
  ``equity_snapshots.spy_benchmark_value`` — which stays price-only —
  by the dividends that fell due since the run started, so the equity
  curve can draw SPY with its dividends without rewriting old rows.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CorporateAction, EquitySnapshot, Run
from app.market.clock import NY_TZ, UTC


def _ny_date(dt: datetime) -> date:
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(NY_TZ).date()

__all__ = [
    "spy_benchmark_with_dividends",
    "spy_dividends_per_share",
    "spy_return",
]


def spy_dividends_per_share(
    session: Session,
    start_day: date,
    end_day: date,
    symbol: str = "SPY",
) -> Decimal:
    """Σ cash dividends per share with ``start_day < ex-date ≤ end_day``.

    Read at call time from the stored corporate actions — a refresh that
    lands later corrects every figure computed after it.
    """
    rows = session.execute(
        select(CorporateAction).where(
            CorporateAction.symbol == symbol,
            CorporateAction.action_type == "dividend",
            CorporateAction.date >= datetime.combine(start_day, time.min),
            CorporateAction.date
            < datetime.combine(end_day + timedelta(days=1), time.min),
        )
    ).scalars().all()
    # ``date`` is a datetime at midnight; the span is on the ex-date
    # itself, the same rule _dividends uses for positions.
    return sum(
        (
            row.ratio_or_amount
            for row in rows
            if start_day < row.date.date() <= end_day
            and row.ratio_or_amount is not None
        ),
        Decimal(0),
    )


def spy_return(
    session: Session,
    start_day: date,
    end_day: date,
    price_start: Decimal,
    price_end: Decimal,
    symbol: str = "SPY",
) -> Decimal | None:
    """SPY's total return over [``start_day``, ``end_day``] in percent:

        (price_end + Σ dividends in span − price_start) ÷ price_start × 100

    ``None`` when either price is missing or zero — there is no baseline
    to divide by.
    """
    if not price_start or price_end is None:
        return None
    dividends = spy_dividends_per_share(session, start_day, end_day, symbol)
    return (price_end + dividends - price_start) / price_start * 100


def spy_benchmark_with_dividends(
    session: Session,
    run: Run,
    snapshot: EquitySnapshot,
    symbol: str = "SPY",
) -> Decimal | None:
    """The snapshot's stored price-only benchmark plus SPY's dividends.

    ``spy_benchmark_value`` = ``starting_equity × spy ÷ run.spy_close``,
    so one share of the benchmark is worth ``starting_equity ÷
    run.spy_close`` — each per-share dividend in the span adds exactly
    that share of the book. The span is ``run start < ex ≤ snapshot
    day``, matching ``spy_return``.
    """
    stored = snapshot.spy_benchmark_value
    if stored is None or run.spy_close in (None, 0):
        return stored
    day = _ny_date(snapshot.time)
    run_start = _ny_date(run.started_at)
    dividends = spy_dividends_per_share(session, run_start, day, symbol)
    if not dividends:
        return stored
    return stored + run.starting_equity * dividends / run.spy_close
