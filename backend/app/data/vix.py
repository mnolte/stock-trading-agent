"""VIX collection and the per-cycle VIX fields (docs/SPEC.md, VIX).

``refresh`` fetches FRED ``VIXCLS`` closes into ``vix_closes`` (startup,
08:00 and 17:00 New York) and refreshes the reference ETF's daily bars.
``cycle_fields`` computes the seven fields a cycle stores — it reads
only stored closes and cached bars, so a cycle never waits on FRED and
missing data yields nulls, never an error.

A close dated session ``d`` counts as usable from 17:00 New York on the
next trading session — FRED posts the close the next afternoon, so the
newest usable close is normally two sessions old during the day.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.data.models import CalendarDay, DataResult
from app.data.protocols import VixSource
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import VixClose
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, Clock

logger = logging.getLogger(__name__)

VIX_FIELD_NAMES = (
    "vix_close",
    "vix_date",
    "vix_percentile_1y",
    "vix_change_5d_pct",
    "vixy_change_today_pct",
    "vixy_change_since_vix_pct",
    "vix_stale",
)

# FRED posts a session's close around 16:00 New York the next trading
# day; the fields only use a close once that posting time has passed.
_POSTING_TIME = time(17, 0)

# percentile_sessions needs ~1y of closes; fetch a little beyond it.
_FETCH_DAYS = 400


class VixService:
    """FRED closes → ``vix_closes``; per-cycle fields → ``cycles``."""

    def __init__(
        self,
        source: VixSource | None,
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
        # The last scheduled/attempted fetch — feeds data-source health
        # and the Settings tile; None until the first refresh runs.
        self.latest: DataResult[int] | None = None
        self.last_fetch_at: datetime | None = None

    @property
    def calendar(self) -> CalendarProvider:
        return self.market_data.calendar

    @property
    def etf(self) -> str:
        """``market.vix.reference_etf`` — the VIX-direction ETF (VIXY)."""
        return str(
            self.config.get("market", "vix", "reference_etf", default="VIXY")
        ).upper()

    def reference_symbols(self) -> list[str]:
        """Symbols cycles fetch bars for that are never candidates."""
        return [self.etf]

    # ------------------------------------------------------------------
    # refresh (startup, 08:00 and 17:00 New York)
    # ------------------------------------------------------------------

    async def refresh(self, today: date | None = None) -> DataResult[int]:
        """Fetch closes into ``vix_closes`` and the ETF's daily bars.

        A fetch failure is stored on ``latest`` for health and the
        Settings tile — it never raises into the scheduler.
        """
        today = today or self.clock.now().astimezone(NY_TZ).date()
        self.last_fetch_at = self.clock.now()
        if self.source is None:
            self.latest = DataResult(data=0, error="no vix source", source="none")
            return self.latest
        result = await self.source.fetch_vix_closes(
            today - timedelta(days=_FETCH_DAYS), today + timedelta(days=1)
        )
        if result.error is not None:
            logger.warning("vix fetch failed: %s", result.error)
            self.latest = DataResult(
                data=0, error=result.error, source=result.source
            )
            return self.latest
        stored = self._store(result.data)
        try:
            await self.market_data.refresh_daily(
                today, self.clock.now(), symbols=[self.etf]
            )
        except Exception:  # noqa: BLE001 - ETF bars are best-effort here
            logger.exception("vixy daily refresh failed")
        self.latest = DataResult(data=stored, source=result.source)
        return self.latest

    def _store(self, observations: list[Any]) -> int:
        now = _naive_utc(self.clock.now())
        with self._sf() as session:
            existing = {
                row.date: row
                for row in session.execute(select(VixClose)).scalars()
            }
            for obs in observations:
                row = existing.get(obs.date)
                if row is None:
                    session.add(
                        VixClose(date=obs.date, close=obs.close, fetched_at=now)
                    )
                else:
                    row.close = obs.close
                    row.fetched_at = now
            session.commit()
        return len(observations)

    def health(self) -> str | None:
        """Data-source health for the cycle report; None before the first
        refresh (backtests never refresh — they read the filled table)."""
        if self.latest is None:
            return None
        if self.latest.error is None:
            return "ok"
        return f"failed: {self.latest.error}"

    def latest_close(self) -> VixClose | None:
        """The newest stored close — the Settings tile's close/date."""
        with self._sf() as session:
            return session.execute(
                select(VixClose).order_by(VixClose.date.desc()).limit(1)
            ).scalar_one_or_none()

    # ------------------------------------------------------------------
    # per-cycle fields
    # ------------------------------------------------------------------

    def cycle_fields(self, now: datetime) -> dict[str, Any]:
        """The seven ``cycles`` VIX columns for a cycle at ``now``.

        Never raises and never waits on the network: stored closes plus
        the cached VIXY bars only. Missing data leaves the fields null.
        """
        fields: dict[str, Any] = dict.fromkeys(VIX_FIELD_NAMES)
        try:
            self._fill(fields, now)
        except Exception:  # noqa: BLE001 - VIX never blocks a cycle
            logger.exception("vix cycle fields failed")
        return fields

    def _fill(self, fields: dict[str, Any], now: datetime) -> None:
        closes = self._usable_closes(now)
        if not closes:
            return
        vix_date, vix_close = closes[-1]
        fields["vix_date"] = vix_date
        fields["vix_close"] = vix_close

        window_n = int(
            self.config.get("market", "vix", "percentile_sessions", default=252)
        )
        window = [close for _, close in closes[-window_n:]] if window_n > 0 else []
        if window:
            at_or_below = sum(1 for c in window if c <= vix_close)
            fields["vix_percentile_1y"] = (
                Decimal(at_or_below) / Decimal(len(window)) * 100
            ).quantize(Decimal("0.01"))
        if len(closes) >= 6:
            base = closes[-6][1]
            if base > 0:
                fields["vix_change_5d_pct"] = (
                    (vix_close - base) / base * 100
                ).quantize(Decimal("0.01"))

        last_completed = self._last_completed_session(now)
        if last_completed is not None:
            stale_after = int(
                self.config.get("market", "vix", "stale_sessions", default=1)
            )
            behind = self._sessions_between(vix_date, last_completed.date)
            fields["vix_stale"] = behind > stale_after

        self._vixy_fields(fields, now, vix_date)

    def _vixy_fields(
        self, fields: dict[str, Any], now: datetime, vix_date: date
    ) -> None:
        """VIXY's move since the previous session close and since the VIX
        close shown — the intraday direction FRED's lag hides."""
        etf = self.etf
        # The last closed 5-minute bar — same convention as the quote
        # staleness check (a bar counts once it could be complete).
        last_bar = self.market_data.bars_cache.latest(etf, now - timedelta(minutes=5))
        if last_bar is None:
            return
        today = now.astimezone(NY_TZ).date()
        prev = self.calendar.previous_trading_day(today)
        if prev is not None:
            prev_bars = self.market_data.bars_cache.get_daily(
                etf, prev.date, prev.date
            )
            if prev_bars and prev_bars[0].close > 0:
                fields["vixy_change_today_pct"] = (
                    (last_bar.close - prev_bars[0].close)
                    / prev_bars[0].close
                    * 100
                ).quantize(Decimal("0.01"))
        vix_bars = self.market_data.bars_cache.get_daily(etf, vix_date, vix_date)
        if vix_bars and vix_bars[0].close > 0:
            fields["vixy_change_since_vix_pct"] = (
                (last_bar.close - vix_bars[0].close)
                / vix_bars[0].close
                * 100
            ).quantize(Decimal("0.01"))

    def _usable_closes(self, now: datetime) -> list[tuple[date, Decimal]]:
        """Stored closes FRED could have posted by ``now``, oldest first.

        A close dated ``d`` is usable from 17:00 New York on the next
        trading session — during a session the newest usable close is the
        one from two sessions back (docs/SPEC.md, VIX).
        """
        with self._sf() as session:
            rows = session.execute(
                select(VixClose).order_by(VixClose.date)
            ).scalars().all()
        out: list[tuple[date, Decimal]] = []
        for row in rows:
            nxt = self.calendar.next_trading_day(row.date)
            if nxt is None:
                continue
            usable_at = datetime.combine(nxt.date, _POSTING_TIME, tzinfo=NY_TZ)
            if usable_at <= now:
                out.append((row.date, row.close))
        return out

    def _last_completed_session(self, now: datetime) -> CalendarDay | None:
        """The latest session whose close has passed at ``now``."""
        cursor = now.astimezone(NY_TZ).date()
        for _ in range(10):
            session = self.calendar.session_for(cursor)
            if session is not None and session.close <= now:
                return session
            prev = self.calendar.previous_trading_day(cursor)
            if prev is None:
                return None
            cursor = prev.date
        return None

    def _sessions_between(self, after: date, through: date) -> int:
        """Trading sessions ``s`` with ``after < s <= through``."""
        count = 0
        cursor = after
        while count < 40:
            nxt = self.calendar.next_trading_day(cursor)
            if nxt is None or nxt.date > through:
                break
            count += 1
            cursor = nxt.date
        return count
