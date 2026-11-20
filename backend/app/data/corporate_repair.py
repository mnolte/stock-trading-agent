"""One-time corporate-actions repair (item 108).

Before item 108 the Alpaca fetch stopped at the SDK's default page and
parsed ``stock_dividends`` rows as cash ``dividend`` events, so a
stock dividend's rate (new shares per share) was stored and later
credited as a dollar amount per share. The first start after this
change refetches corporate actions for every universe symbol from the
oldest run's start date through today + 90 days — the same call the
daily refresh makes, now unpaged — and the refresh's upsert/prune
rewrites the mislabelled rows: a ``stock_dividend`` row appears and
the stale ``dividend`` row at the same (symbol, ex-date) is removed
unless a genuine cash dividend shares that date.

The job runs once, flagged by an ``events`` row like the item 081
migrations; a failed fetch leaves the flag unset so the next start
retries. The event's details carry the row counts for the deploy
report.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import CorporateAction, Event, Position, Run
from app.market.clock import NY_TZ, UTC


def _ny_date(dt: datetime) -> date:
    """Stored timestamps are naive UTC; Run.started_at may be aware."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(NY_TZ).date()

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.data.service import MarketDataService
    from app.market.clock import Clock

logger = logging.getLogger(__name__)

REPAIR_EVENT = "corporate_actions_stock_dividend_repair"


def _snapshot(session: Session) -> dict[tuple[str, str, date], Decimal | None]:
    """(symbol, action_type, ex-date) -> amount for dividend rows."""
    rows = session.execute(
        select(CorporateAction).where(
            CorporateAction.action_type.in_(("dividend", "stock_dividend"))
        )
    ).scalars().all()
    return {
        (r.symbol, r.action_type, r.date.date()): r.ratio_or_amount
        for r in rows
    }


async def repair_corporate_actions_once(
    session_factory: sessionmaker[Session],
    market_data: MarketDataService,
    clock: Clock,
    config: AppConfig,
) -> dict[str, int] | None:
    """Run the one-time repair; ``None`` when it already ran.

    Returns the change counts when this call did the work: rows added,
    removed and amount-updated among dividend/stock-dividend actions.
    """
    from app.engine.state import record_event

    with session_factory() as session:
        flag = session.execute(
            select(Event.id)
            .where(Event.event_type == REPAIR_EVENT)
            .limit(1)
        ).first()
        if flag is not None:
            return None
        # The repair must cover every day a stored position could have
        # earned a dividend: from the oldest run's start (positions
        # predate runs only in theory — fall back to the oldest
        # position's open).
        oldest_run: datetime | None = session.execute(
            select(func.min(Run.started_at))
        ).scalar_one()
        if oldest_run is not None:
            start = _ny_date(oldest_run)
        else:
            oldest_pos: datetime | None = session.execute(
                select(func.min(Position.opened_at))
            ).scalar_one()
            start = (
                _ny_date(oldest_pos)
                if oldest_pos is not None
                else clock.now().astimezone(NY_TZ).date()
            )
    end = clock.now().astimezone(NY_TZ).date() + timedelta(days=90)

    before = _snapshot_with_factory(session_factory)
    result = await market_data.refresh_corporate_actions(start, end)
    if result.error is not None:
        # No flag: the next start tries again rather than leaving the
        # mislabelled rows in place silently.
        logger.warning(
            "corporate-actions repair fetch failed: %s", result.error
        )
        return {"error": 1}
    after = _snapshot_with_factory(session_factory)

    added = removed = updated = 0
    for key, amount in after.items():
        if key not in before:
            added += 1
        elif before[key] != amount:
            updated += 1
    for key in before:
        if key not in after:
            removed += 1

    with session_factory() as session:
        record_event(
            session,
            clock,
            level="info",
            event_type=REPAIR_EVENT,
            details={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "added": added,
                "removed": removed,
                "updated": updated,
            },
        )
        session.commit()
    logger.info(
        "corporate-actions repair: %d added, %d removed, %d updated "
        "(%s → %s)",
        added,
        removed,
        updated,
        start.isoformat(),
        end.isoformat(),
    )
    return {"added": added, "removed": removed, "updated": updated}


def _snapshot_with_factory(
    session_factory: sessionmaker[Session],
) -> dict[tuple[str, str, date], Decimal | None]:
    with session_factory() as session:
        return _snapshot(session)
