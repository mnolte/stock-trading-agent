from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.models import OPEN_ORDER_STATUSES
from app.concurrency import run_batch
from app.config import AppConfig
from app.data.aggregate import aggregate_1h, session_bars
from app.data.cache import BarCache, RawBar5m, _naive_utc
from app.data.factory import DataSources
from app.data.models import (
    Bar1h,
    Bar5m,
    CalendarDay,
    CorporateActionEvent,
    DailyBar,
    DataResult,
    EarningsEvent,
    FXRate,
)
from app.db.base import get_session_maker
from app.db.models import CorporateAction as CorporateActionRow
from app.db.models import Earning as EarningRow
from app.db.models import Event, Order, Position, UniverseScreenRow
from app.market.calendar import CalendarProvider

UTC = ZoneInfo("UTC")
NY_TZ = ZoneInfo("America/New_York")


class MarketDataService:
    """Orchestrates data fetches, the bar cache and stored calendar data."""

    def __init__(
        self,
        sources: DataSources,
        calendar: CalendarProvider,
        config: AppConfig,
        session_factory: sessionmaker[Session] | None = None,
        bar_cache: BarCache | None = None,
    ) -> None:
        self.sources = sources
        self.calendar = calendar
        self.config = config
        self._session_factory = session_factory or get_session_maker()
        self.bars_cache = bar_cache or BarCache(self._session_factory)
        self.latest_fx: DataResult[FXRate | None] | None = None
        self.latest_earnings: DataResult[int] | None = None

    def _max_requests(self) -> int:
        """``concurrency.max_requests`` — one batch's in-flight cap
        (item 078); read per call so tests can change it."""
        return max(
            1,
            int(self.config.get("concurrency", "max_requests", default=20)),
        )

    def universe(self) -> list[str]:
        """The cycle universe — the list every data refresh and cycle runs.

        In ``screened`` mode the newest successful screen hands its
        eligible rows to cycles in rank order; in ``fixed`` mode (or
        before the first screened screen has run) the configured symbols
        are used. Held symbols and SPY are always added on top, so a
        position is still managed outside the eligible list and the
        regime / shock-brake baseline never disappears (docs/SPEC.md,
        Larger universe).
        """
        screened = self.screened_rows()
        base = (
            [row.symbol for row in screened]
            if screened is not None
            else list(self.config.get("universe", "symbols", default=[]))
        )
        extras = self._held_symbols() | {"SPY"}
        symbols = base + sorted(extras - set(base))
        # Reference symbols get bars every cycle but are never candidates,
        # screened in or traded (docs/SPEC.md, VIX and Breadth and sectors).
        excluded = set(self.reference_symbols())
        return [s for s in symbols if s not in excluded]

    def reference_symbols(self) -> list[str]:
        """Symbols that get bars but are never candidates: the VIX ETF and
        the sector ETFs of ``market.sector_etfs`` (item 068)."""
        out = [
            str(
                self.config.get(
                    "market", "vix", "reference_etf", default="VIXY"
                )
            ).upper()
        ]
        sector_etfs = self.config.get("market", "sector_etfs", default={}) or {}
        out += [str(etf).upper() for etf in sector_etfs.values()]
        return list(dict.fromkeys(out))

    def screened_rows(self) -> list[UniverseScreenRow] | None:
        """Eligible rows of the screen currently in effect, in rank order.

        Returns ``None`` while cycles run the fixed list: no screen has
        run yet, or the newest successful screen ran in ``fixed`` mode.
        A failed screen writes no success event, so the previous list
        stays in effect (docs/SPEC.md, Larger universe — a switch applies
        at the next daily screen, never mid-session).
        """
        with self._session_factory() as session:
            event = session.execute(
                select(Event)
                .where(Event.event_type == "universe_screen")
                .order_by(Event.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            details = dict(event.details or {}) if event is not None else {}
            if event is None or details.get("mode") != "screened":
                return None
            try:
                session_date = date.fromisoformat(str(details["session_date"]))
            except (KeyError, ValueError):
                return None
            rows = session.execute(
                select(UniverseScreenRow)
                .where(
                    UniverseScreenRow.session_date == session_date,
                    UniverseScreenRow.status == "eligible",
                )
                .order_by(UniverseScreenRow.rank)
            ).scalars()
            return list(rows)

    def universe_mode(self) -> str:
        """Which list cycles run right now — ``screened`` or ``fixed``."""
        return "screened" if self.screened_rows() is not None else "fixed"

    def _held_symbols(self) -> set[str]:
        """Open-position symbols plus symbols with a live entry order."""
        with self._session_factory() as session:
            held = {
                row[0]
                for row in session.execute(
                    select(Position.symbol).where(Position.closed_at.is_(None))
                ).all()
            }
            held.update(
                row[0]
                for row in session.execute(
                    select(Order.symbol).where(
                        Order.leg == "entry",
                        Order.status.in_(OPEN_ORDER_STATUSES),
                    )
                ).all()
            )
        return held

    def _sessions_spanning(
        self, start: datetime, end: datetime
    ) -> list[CalendarDay]:
        """Every trading session whose New York date falls in [start, end]."""
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        days: list[CalendarDay] = []
        day = start.astimezone(NY_TZ).date()
        last = end.astimezone(NY_TZ).date()
        while day <= last:
            found = self.calendar.session_for(day)
            if found is not None:
                days.append(found)
            day += timedelta(days=1)
        return days

    def recent_sessions(self, today: date, count: int) -> list[CalendarDay]:
        """The ``count`` most recent trading sessions ending at ``today``."""
        days: list[CalendarDay] = []
        cursor = today
        while len(days) < count:
            session = self.calendar.session_for(cursor)
            if session is not None:
                days.append(session)
            prev = self.calendar.previous_trading_day(cursor)
            if prev is None:
                break
            cursor = prev.date
        return sorted(days, key=lambda d: d.date)

    async def refresh_bars(
        self,
        start: datetime,
        end: datetime,
        symbols: list[str] | None = None,
        on_symbol: Callable[[str, DataResult[int]], None] | None = None,
    ) -> dict[str, DataResult[int]]:
        """Fetch and cache 5-minute bars — one multi-symbol request.

        The eligible list at scale is too long for per-symbol calls; the
        bar source takes the whole universe in one request (docs/SPEC.md,
        Cycles at scale). ``symbols`` defaults to ``universe()``.
        ``on_symbol`` fires once per symbol with its stored result — the
        sync-run progress hook (item 077).
        """
        import logging

        from app.logging_config import ev

        log = logging.getLogger("app.data.prices")
        wanted = symbols if symbols is not None else self.universe()
        if not wanted:
            return {}
        fetched = await self.sources.bars.fetch_5m_bars(wanted, start, end)
        if fetched.error is not None:
            ev(
                log, logging.ERROR, "data.fetch_failed",
                f"bars fetch failed: {fetched.error}",
                source=fetched.source,
            )
            results = {
                symbol: DataResult(
                    data=0, error=fetched.error, source=fetched.source
                )
                for symbol in wanted
            }
            if on_symbol is not None:
                for symbol, result in results.items():
                    on_symbol(symbol, result)
            return results
        # SIP bars cover the full extended session (04:00-20:00 NY); the
        # cache is regular-hours only — the coverage counters, outcome
        # reads and shadow replay all assume it (docs/SPEC.md, Market
        # data).
        in_session = session_bars(
            fetched.data or [], self._sessions_spanning(start, end)
        )
        grouped: dict[str, list[Bar5m]] = {symbol: [] for symbol in wanted}
        for bar in in_session:
            grouped.setdefault(bar.symbol, []).append(bar)
        ev(
            log, logging.DEBUG, "data.fetch",
            f"bars: {len(wanted)} symbols, "
            f"{sum(len(v) for v in grouped.values())} bars",
            source=fetched.source, symbols=len(wanted),
        )
        results = {
            symbol: DataResult(
                data=self.bars_cache.store(grouped[symbol]),
                source=fetched.source,
            )
            for symbol in wanted
        }
        if on_symbol is not None:
            for symbol, result in results.items():
                on_symbol(symbol, result)
        return results

    async def refresh_history(
        self,
        today: date,
        symbols: list[str] | None = None,
        session_count: int | None = None,
        on_symbol: Callable[[str, DataResult[int]], None] | None = None,
    ) -> dict[str, DataResult[int]]:
        """Fill the cache with ``history_sessions`` sessions of 5-minute bars.

        Only the uncovered tail is fetched: each symbol's request starts at
        its earliest missing session (every later missing session sits
        inside the range, so mid-range holes are re-downloaded too). The
        newest session is always refetched — a live session's cached bars
        are still forming. A warm cache costs one small request instead of
        re-downloading the whole window (docs/SPEC.md, Cycles at scale).

        ``symbols`` defaults to ``universe()``; ``session_count`` defaults
        to ``schedule.history_sessions`` — callers that read the pending
        settings tree pass their own count so fetch and check agree.
        """
        count = session_count or int(
            self.config.get("schedule", "history_sessions", default=60)
        )
        # Walk back through the trading calendar until we have enough sessions.
        days = self.recent_sessions(today, count)
        wanted = symbols if symbols is not None else self.universe()
        if not days:
            return {
                symbol: DataResult(data=0, error="no calendar days loaded", source="calendar")
                for symbol in wanted
            }
        end = max(days, key=lambda d: d.close).close.astimezone(UTC)
        # Batched coverage first: only symbols short of the full window
        # get the per-symbol date query.
        covered_counts = self.bars_cache.covered_session_counts(wanted, days)
        results: dict[str, DataResult[int]] = {}
        groups: dict[datetime, list[str]] = {}
        for symbol in wanted:
            if covered_counts.get(symbol, 0) == len(days):
                missing: list[CalendarDay] = []
            else:
                covered = self.bars_cache.covered_session_dates(symbol, days)
                missing = [d for d in days if d.date not in covered]
            # The newest session's bars are still forming — refetch it
            # even when the cache has rows for it.
            if days[-1] not in missing:
                missing.append(days[-1])
            start = min(missing, key=lambda d: d.open).open.astimezone(UTC)
            groups.setdefault(start, []).append(symbol)
        # Item 078: one request per symbol group (same start date), sent
        # as a batch; pages of one request stay in sequence inside
        # ``refresh_bars``.
        async def fetch_group(
            item: tuple[datetime, list[str]],
        ) -> tuple[datetime, dict[str, DataResult[int]]]:
            start, group = item
            try:
                return start, await self.refresh_bars(
                    start, end, symbols=group, on_symbol=on_symbol
                )
            except Exception as exc:  # noqa: BLE001 - per-symbol error rows
                # Exception text can carry request details — the class
                # name is what the sync UI shows (hard rule 9).
                group_results = {
                    symbol: DataResult(
                        data=0,
                        error=f"bars: {type(exc).__name__}",
                        source="batch",
                    )
                    for symbol in group
                }
                if on_symbol is not None:
                    for symbol, res in group_results.items():
                        on_symbol(symbol, res)
                return start, group_results

        for item in await run_batch(
            list(groups.items()),
            send=fetch_group,
            limit=self._max_requests(),
            kind="sync.history",
        ):
            if isinstance(item, BaseException):
                continue  # fetch_group already maps errors to rows
            _start, group_results = item
            results.update(group_results)
        return results

    def prune_5m_bars(self, now: datetime) -> int:
        """Drop cached 5m bars older than ``bars_5m_keep_sessions`` sessions.

        Called after the daily backup: bars older than the kept window
        (default 120 sessions) are removed from the cache — daily bars
        are untouched (docs/SPEC.md, Cycles at scale).
        """
        keep = int(
            self.config.get("schedule", "bars_5m_keep_sessions", default=120)
        )
        sessions = self.recent_sessions(now.astimezone(NY_TZ).date(), keep)
        if not sessions:
            return 0
        return self.bars_cache.prune_5m(min(s.open for s in sessions))

    def get_5m_bars(self, symbol: str, start: datetime, end: datetime) -> list[Bar5m]:
        return self.bars_cache.get(symbol, start, end)

    def get_1h_bars(self, symbol: str, session: CalendarDay) -> list[Bar1h]:
        return self.get_1h_bars_range(symbol, [session])

    def get_5m_bars_sessions(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> list[Bar5m]:
        """One cache read covering the whole span of the given sessions."""
        if not sessions:
            return []
        start = min(s.open for s in sessions)
        end = max(s.close for s in sessions)
        return self.get_5m_bars(symbol, start, end)

    def get_5m_rows_sessions(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> list[RawBar5m]:
        """The same read as ``get_5m_bars_sessions``, as raw rows —
        aggregation callers skip the per-bar Decimal construction."""
        if not sessions:
            return []
        start = min(s.open for s in sessions)
        end = max(s.close for s in sessions)
        return self.bars_cache.get_rows(symbol, start, end)

    def get_1h_bars_range(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> list[Bar1h]:
        """Session-aligned 1h bars across several sessions, in order.

        Aggregates are stored per session in ``bars_1h`` together with
        the 5m fingerprint (row count, newest start) they were computed
        from. A session whose fingerprint still matches is served from
        the store; a stale or missing one is recomputed — so writes that
        bypass ``BarCache.store`` can never serve stale bars.
        """
        if not sessions:
            return []
        live = self.bars_cache.session_fingerprints(symbol, sessions)
        states = self.bars_cache.get_1h_states(symbol, sessions)
        hits = {
            s.date
            for s in sessions
            if states.get(s.date) is not None
            and states.get(s.date) == live.get(s.date, "0:")
        }
        stored = self.bars_cache.get_1h(
            symbol, [s for s in sessions if s.date in hits]
        )
        out: list[Bar1h] = []
        for session in sessions:
            cached = stored.get(session.date)
            if session.date in hits and (
                cached is not None or live.get(session.date, "0:") == "0:"
            ):
                out.extend(cached or [])
                continue
            bars = aggregate_1h(
                symbol,
                self.bars_cache.get_rows(symbol, session.open, session.close),
                session,
            )
            self.bars_cache.replace_1h(
                symbol, session.date, bars, live.get(session.date, "0:")
            )
            out.extend(bars)
        return out

    async def refresh_daily(
        self,
        today: date,
        now: datetime,
        symbols: list[str] | None = None,
        session_count: int | None = None,
        on_symbol: Callable[[str, DataResult[Any]], None] | None = None,
    ) -> dict[str, DataResult[int]]:
        """Fetch and cache daily bars for ``daily_history_sessions`` sessions.

        Fetches run a few at a time: the per-symbol calls keep per-symbol
        error reporting, and the shared rate limiter releases its lock
        after acquiring a slot, so the SDK calls overlap. Coverage is
        checked like ``refresh_history``: each symbol's request starts at
        its earliest missing session and the newest session is always
        refetched — a warm cache costs one small request per symbol.

        ``now`` is the fetch time: a session's absence counts as a source
        gap only once its New York date is safely past, so the still-open
        newest session stays missing and is asked for again (item 092).

        ``symbols`` defaults to ``universe()``; ``session_count`` defaults
        to ``schedule.daily_history_sessions`` — callers that read the
        pending settings tree pass their own count so fetch and check
        agree.
        """
        count = session_count or int(
            self.config.get("schedule", "daily_history_sessions", default=260)
        )
        sessions = self.recent_sessions(today, count)
        wanted = symbols if symbols is not None else self.universe()
        if not sessions:
            return {
                symbol: DataResult(data=0, error="no calendar days loaded", source="calendar")
                for symbol in wanted
            }
        end = sessions[-1].date
        gap_cutoff = now.astimezone(NY_TZ).date()
        # Batched coverage first: only symbols short of the full window
        # get the per-symbol date query. Sessions the source already
        # confirmed it has no bar for are known, not missing — without
        # this a recent IPO re-downloads the whole window every refresh.
        covered_counts = self.bars_cache.daily_bar_counts(wanted, sessions)
        known_gaps = self.bars_cache.daily_gap_dates_many(
            wanted, {s.date for s in sessions}
        )
        fetch_start: dict[str, date] = {}
        for symbol in wanted:
            gaps = known_gaps.get(symbol, set())
            if covered_counts.get(symbol, 0) + len(gaps) == len(sessions):
                missing: list[date] = []
            else:
                covered = self.bars_cache.covered_daily_dates(symbol, sessions)
                missing = [
                    s.date
                    for s in sessions
                    if s.date not in covered and s.date not in gaps
                ]
            # The newest session's daily bar is still forming — refetch
            # it even when the cache has a row for it.
            if end not in missing:
                missing.append(end)
            fetch_start[symbol] = min(missing)

        # Item 078: one request per symbol, sent as a batch — at most
        # concurrency.max_requests in flight, results still per symbol.
        async def fetch(
            symbol: str,
        ) -> tuple[str, DataResult[list[DailyBar]]]:
            try:
                result = await self.sources.bars.fetch_daily_bars(
                    symbol, fetch_start[symbol], end
                )
            except Exception as exc:  # noqa: BLE001 - per-symbol error row
                # Exception text can carry request details — the class
                # name is what the sync UI shows (hard rule 9).
                result = DataResult(
                    data=[],
                    error=f"daily bars: {type(exc).__name__}",
                    source="batch",
                )
            if on_symbol is not None:
                on_symbol(symbol, result)
            return symbol, result

        fetched = await run_batch(
            wanted,
            send=fetch,
            limit=self._max_requests(),
            kind="sync.daily_bars",
        )
        results: dict[str, DataResult[int]] = {}
        for item in fetched:
            if isinstance(item, BaseException):
                continue  # fetch already maps errors to DataResults
            symbol, result = item
            if result.error is not None:
                results[symbol] = DataResult(
                    data=0, error=result.error, source=result.source
                )
                continue
            inserted = self.bars_cache.store_daily(result.data)
            # A fetch that succeeded but returned no bar for a session in
            # its range confirms a gap at the source (SPEC, Adding a
            # symbol → "Gaps at the source"); a failed fetch writes none.
            returned = {b.date for b in result.data}
            self.bars_cache.clear_daily_gaps(symbol, returned)
            # A session is a source gap only once its New York date is
            # safely past: a session still open — or just closed, while
            # the vendor may still be publishing its daily bar — stays
            # missing, so the next refresh asks for it again (item 092).
            self.bars_cache.record_daily_gaps(
                symbol,
                [
                    s.date
                    for s in sessions
                    if s.date >= fetch_start[symbol]
                    and s.date not in returned
                    and s.date < gap_cutoff
                ],
            )
            results[symbol] = DataResult(data=inserted, source=result.source)
        return results

    def get_daily_bars(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> list[DailyBar]:
        """Daily bars from the bars_1d cache, one per given session."""
        if not sessions:
            return []
        wanted = {s.date for s in sessions}
        return [
            bar
            for bar in self.bars_cache.get_daily(
                symbol, sessions[0].date, sessions[-1].date
            )
            if bar.date in wanted
        ]

    async def refresh_earnings(self, start: date, end: date) -> DataResult[int]:
        result = await self.sources.earnings.fetch_earnings(self.universe(), start, end)
        if result.error is not None:
            self.latest_earnings = DataResult(
                data=0, error=result.error, source=result.source
            )
            return self.latest_earnings
        stored = 0
        with self._session_factory() as session:
            # Upsert: a Finnhub revision changes time_of_day in place, and a
            # report cancelled or moved out of the window is pruned — a stale
            # row would still trigger blackouts and earnings exits.
            seen: set[tuple[str, datetime]] = set()
            # One report can appear twice in a single fetch (a revised
            # hour); the later entry wins. Sessions never autoflush, so a
            # pending insert is invisible to the exists check — without
            # this dedupe a duplicated event commits two rows and every
            # later refresh crashes on scalar_one_or_none.
            latest: dict[tuple[str, datetime], EarningsEvent] = {}
            for event in result.data:
                key = (event.symbol, datetime.combine(event.date, time.min))
                latest[key] = event
            for (symbol, day), event in latest.items():
                seen.add((symbol, day))
                rows = session.execute(
                    select(EarningRow).where(
                        EarningRow.symbol == symbol,
                        EarningRow.date == day,
                    )
                ).scalars().all()
                if not rows:
                    session.add(
                        EarningRow(
                            symbol=symbol,
                            date=day,
                            time_of_day=event.time_of_day,
                            hour_raw=event.hour_raw,
                            fetched_at=_naive_utc(event.fetched_at),
                        )
                    )
                    stored += 1
                else:
                    exists = rows[0]
                    exists.time_of_day = event.time_of_day
                    exists.hour_raw = event.hour_raw
                    exists.fetched_at = _naive_utc(event.fetched_at)
                    # A database that already committed a duplicate keeps
                    # the first row; the extras are dropped here.
                    for extra in rows[1:]:
                        session.delete(extra)
            stale = session.execute(
                select(EarningRow).where(
                    EarningRow.symbol.in_(self.universe()),
                    EarningRow.date >= datetime.combine(start, time.min),
                    EarningRow.date <= datetime.combine(end, time.max),
                )
            ).scalars().all()
            for row in stale:
                if (row.symbol, row.date) not in seen:
                    session.delete(row)
            session.commit()
        self.latest_earnings = DataResult(data=stored, source=result.source)
        return self.latest_earnings

    async def refresh_corporate_actions(self, start: date, end: date) -> DataResult[int]:
        result = await self.sources.corporate_actions.fetch_corporate_actions(
            self.universe(), start, end
        )
        if result.error is not None:
            return DataResult(data=0, error=result.error, source=result.source)
        stored = 0
        with self._session_factory() as session:
            # Same upsert and prune as refresh_earnings: a revised ratio or
            # amount updates the row in place, and an action cancelled or
            # moved out of the window is deleted — a stale dividend would
            # still be credited in trade and shadow P&L.
            seen: set[tuple[str, str, datetime]] = set()
            # Same dedupe as refresh_earnings: sessions never autoflush, so
            # a duplicated action in one fetch would otherwise commit two
            # rows and crash every later refresh on scalar_one_or_none.
            latest: dict[tuple[str, str, datetime], CorporateActionEvent] = {}
            for action in result.data:
                key = (
                    action.symbol,
                    action.action_type,
                    datetime.combine(action.ex_date, time.min),
                )
                latest[key] = action
            for (symbol, action_type, day), action in latest.items():
                seen.add((symbol, action_type, day))
                rows = session.execute(
                    select(CorporateActionRow).where(
                        CorporateActionRow.symbol == symbol,
                        CorporateActionRow.action_type == action_type,
                        CorporateActionRow.date == day,
                    )
                ).scalars().all()
                if not rows:
                    session.add(
                        CorporateActionRow(
                            symbol=symbol,
                            action_type=action_type,
                            date=day,
                            ratio_or_amount=action.amount_or_ratio,
                        )
                    )
                    stored += 1
                else:
                    rows[0].ratio_or_amount = action.amount_or_ratio
                    for extra in rows[1:]:
                        session.delete(extra)
            stale = session.execute(
                select(CorporateActionRow).where(
                    CorporateActionRow.symbol.in_(self.universe()),
                    CorporateActionRow.date >= datetime.combine(start, time.min),
                    CorporateActionRow.date <= datetime.combine(end, time.max),
                )
            ).scalars().all()
            for row in stale:
                if (row.symbol, row.action_type, row.date) not in seen:
                    session.delete(row)
            session.commit()
        return DataResult(data=stored, source=result.source)
