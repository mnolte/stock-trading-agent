"""The economic-release calendar (docs/SPEC.md, Economic releases — item 067).

FOMC decision dates live in config and are copied into ``economic_events``
at startup, on the daily 08:00 refresh and when settings promote. CPI,
the jobs report and PCE dates come from FRED's release calendar, fetched
daily at 08:00 New York ``events.lookahead_days`` ahead — the FRED key is
required for them; a missing key or a failed fetch leaves only the FOMC
rows, reports the source as failing and never blocks a cycle.

``next_release`` is computed per cycle from the stored rows: the first
release within ``events.next_release_sessions`` sessions. The 08:30
releases (``release_time``) drive the ``release_next_morning`` tag.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.data.models import DataResult
from app.data.protocols import ReleaseSource
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import EconomicEvent
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, Clock
from app.risk.fomc import fomc_dates

logger = logging.getLogger(__name__)

FOMC_TYPE = "fomc"


def _parse_hhmm(raw: object, default: time) -> time:
    if isinstance(raw, str):
        try:
            return datetime.strptime(raw, "%H:%M").time()  # noqa: DTZ007
        except ValueError:
            pass
    return default


def release_time(config: AppConfig) -> time:
    """``events.release_time`` — the New York time the FRED releases land."""
    return _parse_hhmm(config.get("events", "release_time", default="08:30"), time(8, 30))


def release_kinds(config: AppConfig) -> dict[str, int]:
    """``events.fred_releases`` — tag -> FRED release id."""
    raw = config.get("events", "fred_releases", default={}) or {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            logger.warning("events.fred_releases.%s = %r is not an id", key, value)
    return out


def event_label(kind: str) -> str:
    """Display name for a release type (the 056 label map reads these)."""
    return {"fomc": "FOMC", "cpi": "CPI", "jobs": "Jobs", "pce": "PCE"}.get(
        kind, kind.upper()
    )


class ReleaseCalendarService:
    """``economic_events`` rows; per-cycle ``next_release``; release tags."""

    def __init__(
        self,
        source: ReleaseSource | None,
        market_data: MarketDataService,
        config: AppConfig,
        clock: Clock,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.source = source
        self.market_data = market_data
        self.config = config
        self.clock = clock
        self._sf = session_factory or get_session_maker()
        # The last scheduled/attempted FRED fetch — feeds data-source
        # health and the Settings tile; None until the first refresh.
        self.latest: DataResult[int] | None = None

    @property
    def calendar(self) -> CalendarProvider:
        return self.market_data.calendar

    # ------------------------------------------------------------------
    # refresh (startup and the daily 08:00 New York job)
    # ------------------------------------------------------------------

    async def refresh(self, today: date | None = None) -> DataResult[int]:
        """Sync FOMC rows from config, then fetch FRED release dates.

        A FRED failure is stored on ``latest`` for health — it never
        raises into the scheduler and never blocks a cycle.
        """
        today = today or self.clock.now().astimezone(NY_TZ).date()
        self.sync_fomc()
        if self.source is None:
            self.latest = DataResult(
                data=0, error="no release source", source="none"
            )
            return self.latest
        lookahead = int(
            self.config.get("events", "lookahead_days", default=60)
        )
        start, end = today, today + timedelta(days=lookahead)
        stored = 0
        errors: list[str] = []
        fetched_at = _naive_utc(self.clock.now())
        rt = release_time(self.config).strftime("%H:%M")
        for kind, release_id in release_kinds(self.config).items():
            result = await self.source.fetch_release_dates(release_id, start, end)
            if result.error is not None:
                errors.append(f"{kind}: {result.error}")
                continue
            stored += self._upsert_fred(kind, result.data, rt, fetched_at)
        if errors:
            logger.warning("release calendar fetch failed: %s", "; ".join(errors))
            self.latest = DataResult(
                data=stored, error="; ".join(errors), source="fred"
            )
        else:
            self.latest = DataResult(data=stored, source="fred")
        return self.latest

    def sync_fomc(self) -> int:
        """Copy ``events.fomc.dates`` into ``economic_events``.

        Runs at startup, on the daily refresh and when settings promote —
        a saved FOMC list replaces the stored rows wholesale.
        """
        days = fomc_dates(self.config)
        hhmm = _parse_hhmm(
            self.config.get("events", "fomc", "time", default="14:00"),
            time(14, 0),
        ).strftime("%H:%M")
        now = _naive_utc(self.clock.now())
        wanted = set(days)
        with self._sf() as session:
            existing = {
                row.date: row
                for row in session.execute(
                    select(EconomicEvent).where(
                        EconomicEvent.release_type == FOMC_TYPE
                    )
                ).scalars()
            }
            for day in sorted(wanted):
                row = existing.pop(day, None)
                if row is None:
                    session.add(
                        EconomicEvent(
                            release_type=FOMC_TYPE,
                            date=day,
                            time=hhmm,
                            source="config",
                            fetched_at=now,
                        )
                    )
                else:
                    row.time = hhmm
                    row.fetched_at = now
            for stale in existing.values():
                session.delete(stale)
            session.commit()
        return len(wanted)

    def _upsert_fred(
        self,
        kind: str,
        days: list[date],
        hhmm: str,
        fetched_at: datetime,
    ) -> int:
        """Insert/update the fetched dates; prune stale rows of this kind
        outside the fetched window (a moved release must not linger)."""
        if not days:
            return 0
        wanted = set(days)
        window_start, window_end = min(days), max(days)
        with self._sf() as session:
            existing = {
                row.date: row
                for row in session.execute(
                    select(EconomicEvent).where(
                        EconomicEvent.release_type == kind,
                        EconomicEvent.source == "fred",
                    )
                ).scalars()
            }
            for day in sorted(wanted):
                row = existing.pop(day, None)
                if row is None:
                    session.add(
                        EconomicEvent(
                            release_type=kind,
                            date=day,
                            time=hhmm,
                            source="fred",
                            fetched_at=fetched_at,
                        )
                    )
                else:
                    row.time = hhmm
                    row.fetched_at = fetched_at
            # Rows the fetch no longer returns are stale — FRED revised
            # the calendar. Rows outside [start, end] of this fetch are
            # left alone: tomorrow's window covers them.
            lookahead_end = window_end
            for stale in existing.values():
                if window_start <= stale.date <= lookahead_end:
                    session.delete(stale)
            session.commit()
        return len(wanted)

    def health(self) -> str | None:
        """Data-source health for the cycle report; None before the first
        refresh (backtests never refresh — they read the filled table)."""
        if self.latest is None:
            return None
        if self.latest.error is None:
            return "ok"
        return f"failed: {self.latest.error}"

    # ------------------------------------------------------------------
    # per-cycle reads — stored rows only, never the network
    # ------------------------------------------------------------------

    def _events(self) -> list[EconomicEvent]:
        with self._sf() as session:
            return list(
                session.execute(
                    select(EconomicEvent).order_by(
                        EconomicEvent.date, EconomicEvent.time
                    )
                ).scalars()
            )

    def _sessions_away(self, today: date, day: date) -> int | None:
        """Trading sessions from ``today`` (exclusive) to ``day`` —
        ``trading_days_until`` semantics: today is 0, tomorrow 1."""
        if day < today:
            return None
        count = 0
        cursor = today
        while cursor < day:
            nxt = self.calendar.next_trading_day(cursor)
            if nxt is None or nxt.date > day:
                break
            count += 1
            cursor = nxt.date
        if cursor < day:
            return None
        return count

    def next_release(self, now: datetime) -> dict[str, Any] | None:
        """The first release within ``events.next_release_sessions``
        sessions, as {type, date, time, sessions_away}; None when the
        calendar has nothing coming (or is missing entirely)."""
        horizon = int(
            self.config.get("events", "next_release_sessions", default=2)
        )
        ny = now.astimezone(NY_TZ)
        today = ny.date()
        best: tuple[datetime, EconomicEvent, int] | None = None
        for ev in self._events():
            hhmm = _parse_hhmm(ev.time, time(0, 0))
            at = datetime.combine(ev.date, hhmm, tzinfo=NY_TZ)
            if at <= ny:
                continue
            away = self._sessions_away(today, ev.date)
            if away is None or away > horizon:
                continue
            if best is None or at < best[0]:
                best = (at, ev, away)
        if best is None:
            return None
        at, ev, away = best
        return {
            "type": ev.release_type,
            "label": event_label(ev.release_type),
            "date": ev.date.isoformat(),
            "time": ev.time,
            "at": at.astimezone(UTC).isoformat(),
            "sessions_away": away,
        }

    def morning_releases(self, day: date) -> list[dict[str, str]]:
        """The 08:30 releases dated ``day`` — the ``release_next_morning``
        tag payload: a list of {type, date} dicts."""
        rt = release_time(self.config).strftime("%H:%M")
        out: list[dict[str, str]] = []
        for ev in self._events():
            if ev.date != day or ev.time != rt:
                continue
            hhmm = _parse_hhmm(ev.time, time(0, 0))
            at = datetime.combine(ev.date, hhmm, tzinfo=NY_TZ)
            out.append(
                {
                    "type": ev.release_type,
                    "label": event_label(ev.release_type),
                    "date": ev.date.isoformat(),
                    "time": ev.time,
                    "at": at.astimezone(UTC).isoformat(),
                }
            )
        return out

    def next_session_morning_releases(self, now: datetime) -> list[dict[str, str]]:
        """The 08:30 releases on the session after ``now``'s — what an
        entry or a position still open at the last cycle is held into."""
        today = now.astimezone(NY_TZ).date()
        nxt = self.calendar.next_trading_day(today)
        if nxt is None:
            return []
        return self.morning_releases(nxt.date)

    def summary(self, now: datetime | None = None) -> dict[str, Any]:
        """The Settings → Entry rules display (item 067): FOMC dates from
        config, the tracked 08:30 releases' next dates, this cycle's
        ``next_release`` and the FRED fetch state."""
        now = now or self.clock.now()
        today = now.astimezone(NY_TZ).date()
        days = fomc_dates(self.config)
        upcoming: dict[str, date] = {}
        for ev in self._events():
            if ev.release_type == FOMC_TYPE or ev.date < today:
                continue
            cur = upcoming.get(ev.release_type)
            if cur is None or ev.date < cur:
                upcoming[ev.release_type] = ev.date
        latest = self.latest
        return {
            "fomc": {
                "count": len(days),
                "next": days[0].isoformat() if days else None,
                "last": days[-1].isoformat() if days else None,
            },
            "releases": [
                {
                    "type": kind,
                    "label": event_label(kind),
                    "next_date": day.isoformat(),
                }
                for kind, day in sorted(upcoming.items())
            ],
            "next_release": self.next_release(now),
            "fred": (
                "not fetched yet"
                if latest is None
                else ("ok" if latest.error is None else f"failed: {latest.error}")
            ),
        }
