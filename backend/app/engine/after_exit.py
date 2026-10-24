"""After-exit metrics job (docs/SPEC.md, Tail metrics).

Once per trading day after the close — after the readiness snapshot —
this fills ``positions.after_exit_*`` for every closed position whose
``performance.tail.after_exit_sessions`` (5) sessions after the exit have
closed: the highest high and the last close of that window, split-adjusted
with ``corporate_actions`` (dividends ignored).

Bars come from the local cache first; a symbol that left the universe is
fetched through the shared bar source (the Alpaca limiter in paper mode).
A failed fetch is retried the next session; after three failures the row
is stored ``unavailable``. Values are written once and never updated.
The write is one short transaction per position — no fetch ever runs
inside it (AGENTS.md hard rule 13).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.aggregate import session_bars
from app.data.cache import _naive_utc
from app.data.factory import DataSources
from app.data.models import Bar5m, CalendarDay
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import CorporateAction, Position
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, Clock

logger = logging.getLogger(__name__)


@dataclass
class AfterExitReport:
    """One daily pass over the pending closed positions."""

    filled: int = 0
    pending: int = 0  # window not over yet, or no calendar coverage yet
    failed: int = 0  # fetch failed; retried next session
    unavailable: int = 0  # third failure — stored as unavailable


@dataclass
class _PendingRow:
    """What one position needs, read before any fetch (no lock held)."""

    position_id: int
    symbol: str
    exit_day: date
    sessions: list[CalendarDay]
    start: datetime
    end: datetime


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def split_factor_after(
    actions: Sequence[CorporateAction], symbol: str, day: date
) -> Decimal:
    """Cumulative split ratio for ``symbol`` after ``day`` (ex-date > day).

    A 2:1 forward split halves every earlier price, so a price from before
    the ex-date divides by this factor to compare with later prices.
    """
    factor = Decimal(1)
    for action in actions:
        if (
            action.symbol == symbol
            and action.action_type == "split"
            and action.ratio_or_amount is not None
            and action.date.date() > day
            and action.ratio_or_amount > 0
        ):
            factor *= action.ratio_or_amount
    return factor


class AfterExitWorker:
    """Fills the after-exit columns once per closed trading session."""

    def __init__(
        self,
        *,
        market_data: MarketDataService,
        sources: DataSources | None,
        calendar: CalendarProvider,
        config: AppConfig,
        clock: Clock,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.market_data = market_data
        self.sources = sources
        self.calendar = calendar
        self.config = config
        self.clock = clock
        self._session_factory = session_factory or get_session_maker()
        # The session date the last pass ran for — one attempt per session.
        self._ran_for: date | None = None
        self._sessions_n = int(
            config.get("performance", "tail", "after_exit_sessions", default=5)
        )
        # Fetch failures that make a row "unavailable"; the counter itself
        # lives on the position row so a restart keeps the budget.
        self._max_failures = int(
            config.get(
                "performance", "tail", "after_exit_max_failures", default=3
            )
        )
        # Delay before trusting a closed session's bars (the SIP delay the
        # paper data client applies).
        self._data_delay = timedelta(
            minutes=int(
                config.get(
                    "performance",
                    "tail",
                    "after_exit_bar_delay_minutes",
                    default=16,
                )
            )
        )

    # ------------------------------------------------------------------
    # scheduling

    def _latest_closed_session(self, now: datetime) -> CalendarDay | None:
        """Newest session whose close has passed — today's, else the
        previous trading day (so a weekend start still runs once)."""
        today = now.astimezone(NY_TZ).date()
        session = self.calendar.session_for(today)
        if session is not None and session.close.astimezone(UTC) < now:
            return session
        return self.calendar.previous_trading_day(today)

    # ------------------------------------------------------------------
    # due work

    def _due_rows(
        self, session: Session, now: datetime
    ) -> list[_PendingRow]:
        """Pending closed positions whose after-exit window has closed.

        The window's bars must also be old enough to trust: the last
        session's close plus the SIP delay the data client applies.
        """
        rows = session.execute(
            select(Position).where(
                Position.closed_at.is_not(None),
                Position.after_exit_status == "pending",
            )
        ).scalars().all()
        due: list[_PendingRow] = []
        for position in rows:
            exit_day = _aware(position.closed_at).astimezone(NY_TZ).date()  # type: ignore[arg-type]
            sessions: list[CalendarDay] = []
            cursor = exit_day
            for _ in range(self._sessions_n):
                nxt = self.calendar.next_trading_day(cursor)
                if nxt is None:
                    break
                sessions.append(nxt)
                cursor = nxt.date
            if len(sessions) < self._sessions_n:
                continue  # calendar doesn't reach far enough — stays pending
            last_close = sessions[-1].close.astimezone(UTC)
            if last_close + self._data_delay > now:
                continue  # still inside the window — shows "pending"
            due.append(
                _PendingRow(
                    position_id=position.id,
                    symbol=position.symbol,
                    exit_day=exit_day,
                    sessions=sessions,
                    # The window is the sessions AFTER the exit: bars from
                    # the first post-exit session's open, never the
                    # remainder of the exit session.
                    start=sessions[0].open.astimezone(UTC),
                    end=last_close,
                )
            )
        return due

    # ------------------------------------------------------------------
    # bars

    async def _bars(
        self, row: _PendingRow
    ) -> tuple[list[tuple[Bar5m, bool]] | None, str | None]:
        """Bars for the window, ``(bar, split_adjusted)`` pairs.

        ``split_adjusted`` marks bars that came back from a fresh fetch:
        the bar source asks Alpaca for split-adjusted data, so those are
        already in post-split terms while cached bars predate any later
        split. ``None`` + error string on a failed fetch.
        """
        cached = self.market_data.get_5m_bars(row.symbol, row.start, row.end)
        covered = self.market_data.bars_cache.covered_session_dates(
            row.symbol, row.sessions
        )
        wanted = {s.date for s in row.sessions}
        if covered >= wanted:
            return [(b, False) for b in cached], None
        if self.sources is None:
            return None, "no bar source"
        fetched = await self.sources.bars.fetch_5m_bars(
            [row.symbol], row.start, row.end
        )
        if fetched.error is not None:
            return None, fetched.error
        fresh = session_bars(fetched.data or [], row.sessions)
        if not fresh and not cached:
            return None, "no bars returned"
        self.market_data.bars_cache.store(fresh)
        # Union by bar start: fetched bars win (they are split-adjusted;
        # a cached bar at the same start would be the stale unadjusted one).
        by_start: dict[datetime, tuple[Bar5m, bool]] = {
            b.start: (b, False) for b in cached
        }
        for bar in fresh:
            by_start[bar.start] = (bar, True)
        return [by_start[k] for k in sorted(by_start)], None

    # ------------------------------------------------------------------
    # one position

    def _record_failure(self, position_id: int) -> bool:
        """Count a fetch failure on the position row; returns True when the
        row was stored ``unavailable``."""
        with self._session_factory() as session:
            position = session.get(Position, position_id)
            if position is None or position.after_exit_status != "pending":
                return False
            position.after_exit_failures += 1
            if position.after_exit_failures < self._max_failures:
                session.commit()
                return False
            position.after_exit_status = "unavailable"
            position.after_exit_done_at = _naive_utc(self.clock.now())
            session.commit()
            return True

    async def _fill_one(
        self,
        row: _PendingRow,
        actions: list[CorporateAction],
    ) -> str:
        """Compute and store one position; returns done | pending | failed | unavailable."""
        bars, error = await self._bars(row)
        if bars is None:
            if self._record_failure(row.position_id):
                logger.warning(
                    "after-exit %s: fetch failed %d times (%s) — unavailable",
                    row.symbol,
                    self._max_failures,
                    error,
                )
                return "unavailable"
            logger.warning(
                "after-exit %s: fetch failed (%s) — retry next session",
                row.symbol,
                error,
            )
            return "failed"
        if not bars:
            return "pending"

        def adjusted(bar: Bar5m, fetched: bool) -> Bar5m:
            if fetched:
                return bar  # the bar source already applied the split
            factor = split_factor_after(
                actions, row.symbol, bar.start.astimezone(NY_TZ).date()
            )
            if factor == 1:
                return bar
            return Bar5m(
                symbol=bar.symbol,
                start=bar.start,
                open=bar.open / factor,
                high=bar.high / factor,
                low=bar.low / factor,
                close=bar.close / factor,
                volume=bar.volume,
                feed=bar.feed,
            )

        window = [adjusted(b, f) for b, f in bars]
        best = max(window, key=lambda b: b.high)
        close = max(window, key=lambda b: b.start)
        with self._session_factory() as session:
            position = session.get(Position, row.position_id)
            if position is None or position.after_exit_status != "pending":
                return "pending"
            position.after_exit_status = "done"
            position.after_exit_done_at = _naive_utc(self.clock.now())
            position.after_exit_failures = 0
            position.after_exit_best_price = best.high
            position.after_exit_best_at = _naive_utc(best.start)
            position.after_exit_close_price = close.close
            session.commit()
        return "done"

    # ------------------------------------------------------------------
    # the daily pass

    async def fill(self, now: datetime | None = None) -> AfterExitReport:
        """Run once per closed session; a second call the same day no-ops."""
        now = now or self.clock.now()
        report = AfterExitReport()
        session_day = self._latest_closed_session(now)
        if session_day is None or self._ran_for == session_day.date:
            return report
        factory = self._session_factory
        with factory() as session:
            due = self._due_rows(session, now)
            actions = list(
                session.execute(
                    select(CorporateAction).where(
                        CorporateAction.action_type == "split"
                    )
                ).scalars()
            )
        report.pending = 0
        for row in due:
            # Fetch and compute outside any write transaction; each row is
            # stored in its own short one (hard rule 13).
            outcome = await self._fill_one(row, actions)
            if outcome == "done":
                report.filled += 1
            elif outcome == "failed":
                report.failed += 1
            elif outcome == "unavailable":
                report.unavailable += 1
            else:
                report.pending += 1
        self._ran_for = session_day.date
        logger.info(
            "after-exit job: %d filled, %d failed (retry), %d unavailable",
            report.filled,
            report.failed,
            report.unavailable,
        )
        return report
