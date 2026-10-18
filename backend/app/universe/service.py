"""Universe pool and daily liquidity screen (docs/SPEC.md, Larger universe).

Everything here sits behind ``universe.mode``: with the mode at ``fixed``
nothing in this module runs and the cycles keep using the configured fixed
symbols. In ``screened`` mode this service

- seeds ``universe_pool`` from ``backend/universe/pool.csv`` the first time
  screened mode starts, and manages add / CSV import / remove afterwards,
- warms new symbols up to 260 daily bars and 60 sessions of 5-minute bars
  through the shared data sources (and therefore the shared Alpaca limiter),
- runs the daily screen at 08:10 New York on trading days: history, tradable,
  min price, one share fits the configured max position at screen-time
  equity, and 20-session average dollar volume,
- freezes the eligible list for the session and ranks it by 20-session
  return minus SPY's, ties broken by dollar volume,
- keeps an eligible symbol in the list until it has failed the screen on 3
  consecutive trading days, and keeps the previous list untouched when the
  screen itself fails (a banner is raised instead).
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.base import Broker
from app.broker.models import OPEN_ORDER_STATUSES
from app.config import AppConfig, _resolve_path
from app.data.factory import DataSources
from app.data.models import CalendarDay, DailyBar
from app.data.service import MarketDataService
from app.db.models import Event, Order, Position, UniversePoolRow, UniverseScreenRow
from app.engine.state import record_event
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, Clock

UTC = ZoneInfo("UTC")
SYMBOL_RE = re.compile(r"^[A-Z]{1,6}(\.[A-Z])?$")
CHECK_KEYS = ("history", "tradable", "min_price", "one_share", "dollar_volume")

_log = logging.getLogger(__name__)


class PoolError(Exception):
    """A pool edit was rejected; ``messages`` lists every reason."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


class ScreenError(Exception):
    """The whole daily screen failed — the previous list stays in effect."""


@dataclass
class _ScreenMetrics:
    """One symbol's screen inputs and raw result."""

    status: str  # eligible | excluded | warming_up
    checks: dict[str, str]  # CHECK_KEYS → pass | fail | warming
    detail: str
    last_close: Decimal | None
    avg_dollar_volume: Decimal | None
    return_vs_spy: Decimal | None


class UniverseService:
    def __init__(
        self,
        market_data: MarketDataService,
        sources: DataSources,
        calendar: CalendarProvider,
        broker: Broker,
        config: AppConfig,
        clock: Clock,
        session_factory: sessionmaker[Session] | None = None,
        notify: Callable[[], None] | None = None,
    ) -> None:
        self._market_data = market_data
        self._sources = sources
        self._calendar = calendar
        self._broker = broker
        self._config = config
        self._clock = clock
        self._sf = session_factory or market_data._session_factory
        self._notify = notify

    # ------------------------------------------------------------------
    # Configuration. The pending tree wins over the active one, so a saved
    # mode/threshold change applies at the next daily screen — never
    # mid-session, whether or not a cycle has promoted it yet.
    # ------------------------------------------------------------------

    def _tree(self) -> dict[str, Any]:
        return self._config.pending() or self._config.effective()

    def _cfg(self) -> dict[str, Any]:
        return self._tree().get("universe") or {}

    def _schedule_cfg(self) -> dict[str, Any]:
        return self._tree().get("schedule") or {}

    def _risk_cfg(self) -> dict[str, Any]:
        return self._tree().get("risk") or {}

    def mode(self) -> str:
        return str(self._cfg().get("mode") or "fixed")

    def allowed_sectors(self) -> list[str]:
        return list(self._cfg().get("allowed_sectors") or [])

    def max_pool(self) -> int:
        return int(self._cfg().get("max_pool") or 150)

    def _screen_cfg(self) -> dict[str, Any]:
        return self._cfg().get("screen") or {}

    def screen_time_ny(self) -> str:
        return str(self._screen_cfg().get("time_ny") or "08:10")

    def _daily_history_sessions(self) -> int:
        return int(self._schedule_cfg().get("daily_history_sessions") or 260)

    def _history_sessions(self) -> int:
        return int(self._schedule_cfg().get("history_sessions") or 60)

    def _lookback(self) -> int:
        return int(self._screen_cfg().get("lookback_sessions") or 20)

    def _drop_after(self) -> int:
        return int(self._screen_cfg().get("drop_after_failed_screens") or 3)

    def pool_path(self) -> str:
        return _resolve_path(
            str(self._cfg().get("pool_file") or "backend/universe/pool.csv")
        )

    # ------------------------------------------------------------------
    # Pool management
    # ------------------------------------------------------------------

    def ensure_pool_loaded(self) -> int:
        """Seed the default pool from pool.csv — once, ever.

        Runs the first time screened mode is used. Rows added before the
        seed keep theirs; a pool that already saw the defaults (even if a
        row was removed since) is never re-seeded.
        """
        if self.mode() != "screened":
            return 0
        now = self._clock.now()
        with self._sf() as session:
            seeded = session.execute(
                select(func.count())
                .select_from(UniversePoolRow)
                .where(UniversePoolRow.source == "default")
            ).scalar_one()
            if seeded:
                return 0
            rows = self._read_pool_file()
            existing = {
                r.symbol
                for r in session.execute(select(UniversePoolRow)).scalars()
            }
            added = 0
            for symbol, sector in rows:
                if symbol in existing:
                    continue
                session.add(
                    UniversePoolRow(
                        symbol=symbol,
                        sector=sector,
                        source="default",
                        added_at=now,
                    )
                )
                added += 1
            session.commit()
        if added:
            self._ping()
        return added

    def _read_pool_file(self) -> list[tuple[str, str]]:
        allowed = set(self.allowed_sectors())
        rows: list[tuple[str, str]] = []
        with open(self.pool_path(), encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line or line.lower() == "symbol,sector":
                    continue
                symbol, _, sector = line.partition(",")
                symbol = symbol.strip().upper()
                sector = sector.strip()
                if not SYMBOL_RE.match(symbol):
                    raise PoolError(
                        [f"pool.csv line {line_no}: invalid symbol '{symbol}'"]
                    )
                if sector not in allowed:
                    raise PoolError(
                        [
                            f"pool.csv line {line_no}: unknown sector "
                            f"'{sector}' for {symbol}"
                        ]
                    )
                rows.append((symbol, sector))
        return rows

    def _active_pool(self, session: Session) -> list[UniversePoolRow]:
        """Pool members: live rows plus held symbols marked for removal.

        Per spec a held symbol removed from the pool leaves it when the
        position closes — until then it is still screened and shown.
        """
        held = set(self._held_symbols(session))
        return [
            row
            for row in session.execute(
                select(UniversePoolRow).order_by(UniversePoolRow.symbol)
            ).scalars()
            if row.removed_at is None or row.symbol in held
        ]

    def active_pool(self) -> list[UniversePoolRow]:
        with self._sf() as session:
            return self._active_pool(session)

    async def _check_symbol_add(
        self, session: Session, symbol: str, sector: str
    ) -> list[str]:
        """The same checks as the fixed-universe symbol add, plus the cap."""
        errors: list[str] = []
        if not SYMBOL_RE.match(symbol):
            errors.append(f"'{symbol}' is not a valid US stock or ETF symbol.")
            return errors
        existing = session.execute(
            select(UniversePoolRow).where(
                UniversePoolRow.symbol == symbol,
                UniversePoolRow.removed_at.is_(None),
            )
        ).scalar_one_or_none()
        if existing is not None:
            errors.append(f"{symbol} is already in the pool.")
        if len(self._active_pool(session)) >= self.max_pool():
            errors.append(f"the pool is full ({self.max_pool()} symbols).")
        if sector not in self.allowed_sectors():
            errors.append(f"{sector} is not an allowed sector.")
        asset_result = await self._broker.get_asset(symbol)
        asset = asset_result.data if asset_result.error is None else None
        if asset is None or not asset.tradable or asset.status != "active":
            errors.append(
                f"{symbol} is not an active, tradable US stock or ETF at Alpaca."
            )
        elif asset.asset_class != "us_equity":
            errors.append(f"{symbol}: {asset.asset_class} is not a US stock or ETF")
        return errors

    async def add_symbol(self, symbol: str, sector: str) -> UniversePoolRow:
        symbol = symbol.strip().upper()
        sector = sector.strip()
        now = self._clock.now()
        with self._sf() as session:
            errors = await self._check_symbol_add(session, symbol, sector)
            if errors:
                raise PoolError(errors)
            row = session.execute(
                select(UniversePoolRow).where(UniversePoolRow.symbol == symbol)
            ).scalar_one_or_none()
            if row is None:
                row = UniversePoolRow(
                    symbol=symbol,
                    sector=sector,
                    source="added",
                    added_at=now,
                )
                session.add(row)
            else:  # re-adding a removed row
                row.sector = sector
                row.removed_at = None
            session.commit()
            session.refresh(row)
            out = UniversePoolRow(
                symbol=row.symbol,
                sector=row.sector,
                source=row.source,
                added_at=row.added_at,
                removed_at=row.removed_at,
            )
            out.id = row.id
        self._ping()
        return out

    async def import_csv(self, text: str) -> tuple[list[str], int]:
        """Import ``symbol,sector`` rows atomically — all or nothing.

        Returns (added symbols, skipped) where skipped rows were already
        in the pool. Any invalid row rejects the whole import.
        """
        rows: list[tuple[int, str, str]] = []
        errors: list[str] = []
        reader = csv.reader(io.StringIO(text))
        for line_no, fields in enumerate(reader, start=1):
            if not fields or all(not f.strip() for f in fields):
                continue
            if line_no == 1 and [f.strip().lower() for f in fields[:2]] == [
                "symbol",
                "sector",
            ]:
                continue
            symbol = fields[0].strip().upper()
            sector = fields[1].strip() if len(fields) > 1 else ""
            if not SYMBOL_RE.match(symbol):
                errors.append(f"line {line_no}: '{symbol}' is not a valid symbol")
                continue
            if sector not in self.allowed_sectors():
                errors.append(
                    f"line {line_no}: {symbol} — unknown sector '{sector}'"
                )
                continue
            rows.append((line_no, symbol, sector))
        seen: set[str] = set()
        for line_no, symbol, _ in rows:
            if symbol in seen:
                errors.append(f"line {line_no}: {symbol} appears twice in the file")
            seen.add(symbol)
        if errors:
            raise PoolError(errors)

        added: list[str] = []
        skipped = 0
        now = self._clock.now()
        with self._sf() as session:
            active = {r.symbol for r in self._active_pool(session)}
            for line_no, symbol, sector in rows:
                if symbol in active:
                    skipped += 1
                    continue
                if len(active) + len(added) >= self.max_pool():
                    raise PoolError(
                        [
                            f"the pool is full ({self.max_pool()} symbols) — "
                            "import rejected"
                        ]
                    )
                line_errors = await self._check_symbol_add(session, symbol, sector)
                if line_errors:
                    errors.extend(f"line {line_no}: {e}" for e in line_errors)
                    continue
                session.add(
                    UniversePoolRow(
                        symbol=symbol,
                        sector=sector,
                        source="import",
                        added_at=now,
                    )
                )
                added.append(symbol)
            if errors:
                raise PoolError(errors)
            session.commit()
        if added:
            self._ping()
        return added, skipped

    def remove_symbol(self, symbol: str) -> None:
        """Mark a pool row removed (the row stays for history)."""
        symbol = symbol.strip().upper()
        if symbol == "SPY":
            raise PoolError(["SPY is the market baseline and stays in the pool."])
        with self._sf() as session:
            row = session.execute(
                select(UniversePoolRow).where(
                    UniversePoolRow.symbol == symbol,
                    UniversePoolRow.removed_at.is_(None),
                )
            ).scalar_one_or_none()
            if row is None:
                raise PoolError([f"{symbol} is not in the pool."])
            row.removed_at = self._clock.now()
            session.commit()
        self._ping()

    # ------------------------------------------------------------------
    # Held symbols — always handled, inside or outside the pool
    # ------------------------------------------------------------------

    def _held_symbols(self, session: Session) -> dict[str, int]:
        """Open positions plus symbols with a live entry order."""
        held = {
            r.symbol: r.qty
            for r in session.execute(
                select(Position).where(Position.closed_at.is_(None))
            ).scalars()
        }
        for symbol in session.execute(
            select(Order.symbol).where(
                Order.leg == "entry",
                Order.status.in_(OPEN_ORDER_STATUSES),
            )
        ).scalars():
            held.setdefault(symbol, 0)
        return held

    def held_symbols(self) -> dict[str, int]:
        with self._sf() as session:
            return self._held_symbols(session)

    # ------------------------------------------------------------------
    # History / warm-up
    # ------------------------------------------------------------------

    def _history_detail(
        self, symbol: str, now: datetime
    ) -> tuple[bool, str, list[CalendarDay], list[CalendarDay]]:
        """The same warming semantics as the signal snapshot: completed
        daily bars and covered 5m sessions against the configured needs
        (260 daily / 60 sessions), capped by what the calendar can supply.
        Returns (loaded, detail, completed daily sessions, started sessions).
        """
        ny_today = now.astimezone(NY_TZ).date()
        daily_sessions = self._market_data.recent_sessions(
            ny_today, self._daily_history_sessions()
        )
        completed = [s for s in daily_sessions if s.close <= now]
        sessions = self._market_data.recent_sessions(
            ny_today, self._history_sessions()
        )
        started = [s for s in sessions if s.open <= now]
        daily_bars = self._market_data.get_daily_bars(symbol, completed)
        covered = self._market_data.bars_cache.covered_session_dates(
            symbol, started
        )
        want_daily = min(self._daily_history_sessions(), len(completed))
        want_5m = min(self._history_sessions(), len(started))
        loaded = len(daily_bars) >= want_daily and len(covered) >= want_5m
        detail = (
            f"{len(daily_bars)} of {want_daily} daily bars · "
            f"{len(covered)} of {want_5m} sessions"
        )
        return loaded, detail, completed, started

    async def _fetch_daily(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> str | None:
        """Top up cached daily bars; returns an error string or None."""
        if not sessions:
            return None
        res = await self._sources.bars.fetch_daily_bars(
            symbol, sessions[0].date, sessions[-1].date
        )
        if res.error is not None:
            return res.error
        if res.data is not None:
            self._market_data.bars_cache.store_daily(res.data)
        return None

    async def _fetch_5m(
        self, symbol: str, sessions: list[CalendarDay]
    ) -> str | None:
        """Top up cached 5m bars over the given sessions; error or None."""
        if not sessions:
            return None
        res = await self._sources.bars.fetch_5m_bars(
            symbol, sessions[0].open, sessions[-1].close
        )
        if res.error is not None:
            return res.error
        if res.data is not None:
            self._market_data.bars_cache.store(res.data)
        return None

    async def warm_up(
        self, symbols: list[str] | None = None
    ) -> dict[str, str]:
        """Load the 260 daily + 60-session 5m history for the symbols.

        Every fetch goes through the shared data sources, so it shares the
        150 calls/minute Alpaca limiter with the cycles. Returns a
        ``symbol → error`` map; a symbol whose history could not be loaded
        stays ``warming_up`` at the next screen.
        """
        now = self._clock.now()
        ny_today = now.astimezone(NY_TZ).date()
        daily_sessions = self._market_data.recent_sessions(
            ny_today, self._daily_history_sessions()
        )
        sessions = self._market_data.recent_sessions(
            ny_today, self._history_sessions()
        )
        started = [s for s in sessions if s.open <= now]
        wanted = (
            symbols
            if symbols is not None
            else [r.symbol for r in self.active_pool()]
        )
        errors: dict[str, str] = {}
        for symbol in wanted:
            err = await self._fetch_daily(symbol, daily_sessions)
            if err:
                errors[symbol] = err
                continue
            covered = self._market_data.bars_cache.covered_session_dates(
                symbol, started
            )
            if len(covered) < min(self._history_sessions(), len(started)):
                err = await self._fetch_5m(symbol, started)
                if err:
                    errors[symbol] = err
        return errors

    # ------------------------------------------------------------------
    # The daily screen
    # ------------------------------------------------------------------

    def latest_screen_rows(self) -> dict[str, UniverseScreenRow]:
        """The most recent screen row per symbol (the list in effect)."""
        with self._sf() as session:
            latest_date: date | None = session.execute(
                select(func.max(UniverseScreenRow.session_date))
            ).scalar()
            if latest_date is None:
                return {}
            rows = session.execute(
                select(UniverseScreenRow).where(
                    UniverseScreenRow.session_date == latest_date
                )
            ).scalars()
            return {r.symbol: r for r in rows}

    def last_screen_failed(self) -> bool:
        """Whether the newest screen attempt event was a failure."""
        with self._sf() as session:
            event = session.execute(
                select(Event)
                .where(
                    Event.event_type.in_(
                        ["universe_screen", "universe_screen_failed"]
                    )
                )
                .order_by(Event.id.desc())
                .limit(1)
            ).scalar_one_or_none()
        return event is not None and event.event_type == "universe_screen_failed"

    async def run_screen(self, now: datetime | None = None) -> dict[str, Any]:
        """Run the daily liquidity screen for the session at ``now``.

        No-ops in fixed mode and on non-trading days. On a data error the
        previous list is kept untouched and a ``universe_screen_failed``
        event raises the banner.
        """
        now = now or self._clock.now()
        if self.mode() != "screened":
            return {"skipped": "fixed mode"}
        today = now.astimezone(NY_TZ).date()
        day = self._calendar.session_for(today)
        if day is None:
            return {"skipped": "not a trading day"}
        self.ensure_pool_loaded()
        try:
            return await self._run_screen(now, today)
        except Exception as exc:  # keep the previous list, raise the banner
            _log.exception("universe screen failed")
            with self._sf() as session:
                record_event(
                    session,
                    self._clock,
                    level="warning",
                    event_type="universe_screen_failed",
                    details={
                        "session_date": today.isoformat(),
                        "error": str(exc)[:500],
                    },
                )
                session.commit()
            self._ping()
            return {"failed": str(exc)}

    async def _run_screen(self, now: datetime, today: date) -> dict[str, Any]:
        cfg = self._screen_cfg()
        min_price = Decimal(str(cfg.get("min_price") or 10))
        min_dv = Decimal(str(cfg.get("min_avg_dollar_volume") or 100_000_000))
        lookback = self._lookback()
        drop_after = self._drop_after()

        account = await self._broker.get_account()
        if account.error is not None or account.data is None:
            raise ScreenError(f"account lookup failed: {account.error}")
        equity = account.data.equity
        max_position_pct = Decimal(
            str(self._risk_cfg().get("max_position_pct") or 0)
        )
        max_position_usd = (equity * max_position_pct / Decimal(100)).quantize(
            Decimal("0.01")
        )

        with self._sf() as session:
            pool = self._active_pool(session)
        if not pool:
            raise ScreenError("the universe pool is empty")

        # 1. Refresh history — every pool symbol gets its daily top-up; new
        #    symbols also get their 5m warm-up. A fetch failure fails the
        #    whole screen (data error → keep the previous list).
        daily_sessions = self._market_data.recent_sessions(
            today, self._daily_history_sessions()
        )
        sessions = self._market_data.recent_sessions(
            today, self._history_sessions()
        )
        started = [s for s in sessions if s.open <= now]
        want_5m = min(self._history_sessions(), len(started))
        for row in pool:
            err = await self._fetch_daily(row.symbol, daily_sessions)
            if err:
                raise ScreenError(f"{row.symbol} daily bars: {err}")
            covered = self._market_data.bars_cache.covered_session_dates(
                row.symbol, started
            )
            if len(covered) < want_5m:
                err = await self._fetch_5m(row.symbol, started)
                if err:
                    raise ScreenError(f"{row.symbol} 5m bars: {err}")

        # 2. Check every symbol.
        completed = [s for s in daily_sessions if s.close <= now]
        spy_bars = self._market_data.get_daily_bars("SPY", completed)
        spy_return = _period_return(spy_bars, lookback)
        metrics: dict[str, _ScreenMetrics] = {}
        for row in pool:
            metrics[row.symbol] = await self._screen_symbol(
                row.symbol,
                now,
                completed,
                min_price,
                min_dv,
                max_position_usd,
                lookback,
                spy_return,
            )

        # 3. Hysteresis + rank, then persist.
        with self._sf() as session:
            previous = session.execute(
                select(func.max(UniverseScreenRow.session_date)).where(
                    UniverseScreenRow.session_date < today
                )
            ).scalar_one()
            prev_rows: dict[str, UniverseScreenRow] = {}
            if previous is not None:
                for r in session.execute(
                    select(UniverseScreenRow).where(
                        UniverseScreenRow.session_date == previous
                    )
                ).scalars():
                    prev_rows[r.symbol] = r

            session.execute(
                delete(UniverseScreenRow).where(
                    UniverseScreenRow.session_date == today
                )
            )
            out_rows: list[UniverseScreenRow] = []
            for row in pool:
                m = metrics[row.symbol]
                prev = prev_rows.get(row.symbol)
                streak = (
                    (prev.failed_in_a_row if prev else 0) + 1
                    if m.status != "eligible"
                    else 0
                )
                status = m.status
                if (
                    m.status != "eligible"
                    and prev is not None
                    and prev.status == "eligible"
                    and streak < drop_after
                ):
                    status = "eligible"  # stays in, streak keeps counting
                out_rows.append(
                    UniverseScreenRow(
                        session_date=today,
                        symbol=row.symbol,
                        status=status,
                        check_results={
                            **m.checks,
                            "failed_check": _failed_check(m.checks),
                            "detail": m.detail,
                            "equity": str(equity),
                            "max_position_usd": str(max_position_usd),
                        },
                        last_close=m.last_close,
                        avg_dollar_volume=m.avg_dollar_volume,
                        return_vs_spy=m.return_vs_spy,
                        failed_in_a_row=streak,
                    )
                )

            eligible = [r for r in out_rows if r.status == "eligible"]
            eligible.sort(
                key=lambda r: (
                    r.return_vs_spy is None,
                    -(r.return_vs_spy or Decimal(0)),
                    r.avg_dollar_volume is None,
                    -(r.avg_dollar_volume or Decimal(0)),
                    r.symbol,
                )
            )
            for rank, r in enumerate(eligible, start=1):
                r.rank = rank
            session.add_all(out_rows)

            funnel = _funnel_counts(out_rows)
            record_event(
                session,
                self._clock,
                level="info",
                event_type="universe_screen",
                details={
                    "session_date": today.isoformat(),
                    "equity": str(equity),
                    "max_position_usd": str(max_position_usd),
                    "funnel": funnel,
                    "eligible": len(eligible),
                },
            )
            session.commit()
        self._ping()
        return {
            "session_date": today.isoformat(),
            "pool": len(out_rows),
            "eligible": len(eligible),
            "funnel": funnel,
        }

    async def _screen_symbol(
        self,
        symbol: str,
        now: datetime,
        completed: list[CalendarDay],
        min_price: Decimal,
        min_dv: Decimal,
        max_position_usd: Decimal,
        lookback: int,
        spy_return: Decimal | None,
    ) -> _ScreenMetrics:
        checks = dict.fromkeys(CHECK_KEYS, "pass")
        loaded, hist_detail, _, _ = self._history_detail(symbol, now)
        if not loaded:
            checks["history"] = "warming"
            return _ScreenMetrics(
                status="warming_up",
                checks=checks,
                detail=hist_detail,
                last_close=None,
                avg_dollar_volume=None,
                return_vs_spy=None,
            )

        daily = self._market_data.get_daily_bars(symbol, completed)
        last_close = daily[-1].close if daily else None
        dv = _avg_dollar_volume(daily, lookback)
        ret = _period_return(daily, lookback)
        vs_spy = (
            (ret - spy_return).quantize(Decimal("0.0001"))
            if ret is not None and spy_return is not None
            else None
        )

        asset_result = await self._broker.get_asset(symbol)
        asset = asset_result.data if asset_result.error is None else None
        if asset is None or not asset.tradable or asset.status != "active":
            checks["tradable"] = "fail"
            detail = (
                str(asset_result.error)
                if asset is None and asset_result.error
                else "not an active, tradable US stock or ETF at Alpaca"
            )
            if asset is not None and asset.asset_class != "us_equity":
                detail = f"{asset.asset_class} is not a US stock or ETF"
        elif last_close is None or last_close < min_price:
            checks["min_price"] = "fail"
            detail = f"last close ${last_close} below ${min_price}"
        elif last_close > max_position_usd:
            checks["one_share"] = "fail"
            detail = (
                f"1 share ${last_close:,.2f} > max position "
                f"${max_position_usd:,.0f}"
            )
        elif dv is None or dv < min_dv:
            checks["dollar_volume"] = "fail"
            dv_text = (
                "n/a"
                if dv is None
                else f"${dv / Decimal(1_000_000):,.0f}M"
            )
            detail = (
                f"20-session avg dollar volume {dv_text} < "
                f"${min_dv / Decimal(1_000_000):,.0f}M"
            )
        else:
            return _ScreenMetrics(
                status="eligible",
                checks=checks,
                detail="",
                last_close=last_close,
                avg_dollar_volume=dv,
                return_vs_spy=vs_spy,
            )
        return _ScreenMetrics(
            status="excluded",
            checks=checks,
            detail=detail,
            last_close=last_close,
            avg_dollar_volume=dv,
            return_vs_spy=vs_spy,
        )

    # ------------------------------------------------------------------
    # API payload
    # ------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """The GET /api/universe payload for the Universe screen."""
        mode = self.mode()
        held = self.held_symbols()
        cfg = self._cfg()
        screen_cfg = self._screen_cfg()
        now = self._clock.now()
        fixed = [
            {"symbol": s, "sector": (cfg.get("sectors") or {}).get(s, "")}
            for s in (cfg.get("symbols") or [])
        ]
        payload: dict[str, Any] = {
            "mode": mode,
            "allowed_sectors": self.allowed_sectors(),
            "fixed_symbols": fixed,
            "held_symbols": sorted(held),
            "screen_time_ny": self.screen_time_ny(),
            "history": {
                "daily_sessions": self._daily_history_sessions(),
                "sessions_5m": self._history_sessions(),
            },
            "thresholds": {
                "min_price": screen_cfg.get("min_price") or 10,
                "min_avg_dollar_volume": screen_cfg.get("min_avg_dollar_volume")
                or 100_000_000,
                "lookback_sessions": self._lookback(),
                "drop_after_failed_screens": self._drop_after(),
                "max_position_pct": self._risk_cfg().get("max_position_pct")
                or 20,
            },
        }
        if mode != "screened":
            return payload

        self.ensure_pool_loaded()
        with self._sf() as session:
            pool = self._active_pool(session)
            latest_rows = self.latest_screen_rows()

        rows: list[dict[str, Any]] = []
        for entry in pool:
            row = latest_rows.get(entry.symbol)
            check: dict[str, Any] = (row.check_results or {}) if row is not None else {}
            if row is None:
                loaded, detail, _, _ = self._history_detail(entry.symbol, now)
                # Not screened yet — never "eligible" off a screen row.
                status = "warming_up"
                check = {
                    "failed_check": None if loaded else "history",
                    "detail": "ready — enters at the next screen"
                    if loaded
                    else detail,
                }
            else:
                status = row.status
            detail = str(check.get("detail") or "")
            if entry.removed_at is not None:  # held removal, pending close
                detail = (
                    "leaves the pool when the position closes"
                    + (f" · {detail}" if detail else "")
                )
            rows.append(
                {
                    "symbol": entry.symbol,
                    "sector": entry.sector,
                    "added_at": entry.added_at.isoformat(),
                    "status": status,
                    "rank": row.rank if row is not None else None,
                    "last_close": str(row.last_close)
                    if row is not None and row.last_close is not None
                    else None,
                    "avg_dollar_volume": str(row.avg_dollar_volume)
                    if row is not None and row.avg_dollar_volume is not None
                    else None,
                    "return_vs_spy": str(row.return_vs_spy)
                    if row is not None and row.return_vs_spy is not None
                    else None,
                    "fails": row.failed_in_a_row if row is not None else 0,
                    "failed_check": check.get("failed_check"),
                    "detail": detail,
                    "held": entry.symbol in held,
                    "qty": held.get(entry.symbol, 0),
                    "screened": row is not None,
                    "in_pool": True,
                }
            )
        # Held symbols outside the pool still show — they are always handled.
        in_pool = {r["symbol"] for r in rows}
        for symbol in sorted(set(held) - in_pool):
            rows.append(
                {
                    "symbol": symbol,
                    "sector": "",
                    "added_at": None,
                    "status": "held",
                    "rank": None,
                    "last_close": None,
                    "avg_dollar_volume": None,
                    "return_vs_spy": None,
                    "fails": 0,
                    "failed_check": None,
                    "detail": "",
                    "held": True,
                    "qty": held.get(symbol, 0),
                    "screened": False,
                    "in_pool": False,
                }
            )

        order = {"eligible": 0, "warming_up": 1, "excluded": 2, "held": 3}
        rows.sort(
            key=lambda r: (
                order.get(str(r["status"]), 4),
                r["rank"] if r["rank"] is not None else 10**6,
                r["symbol"],
            )
        )

        counts = {
            "pool": len(pool),
            "eligible": sum(1 for r in rows if r["status"] == "eligible"),
            "excluded": sum(1 for r in rows if r["status"] == "excluded"),
            "warming_up": sum(1 for r in rows if r["status"] == "warming_up"),
            "held": len(held),
        }
        payload.update(
            {
                "pool": {"size": len(pool), "max": self.max_pool()},
                "counts": counts,
                "screen": self._screen_summary(latest_rows, counts),
                "rows": rows,
            }
        )
        return payload

    def _screen_summary(
        self,
        latest_rows: dict[str, UniverseScreenRow],
        counts: dict[str, int],
    ) -> dict[str, Any] | None:
        if not latest_rows:
            return {
                "session_date": None,
                "failed": self.last_screen_failed(),
                "funnel": [],
                "eligible_total": 0,
            }
        rows = list(latest_rows.values())
        session_date = rows[0].session_date
        funnel = [{"key": "pool", "count": len(rows), "out": []}]
        for key in CHECK_KEYS:
            kept = [
                r
                for r in rows
                if all(
                    (r.check_results or {}).get(k) == "pass"
                    for k in CHECK_KEYS[: CHECK_KEYS.index(key) + 1]
                )
            ]
            dropped_here = [
                r.symbol
                for r in rows
                if (r.check_results or {}).get(key) != "pass"
                and all(
                    (r.check_results or {}).get(k) == "pass"
                    for k in CHECK_KEYS[: CHECK_KEYS.index(key)]
                )
            ]
            funnel.append(
                {"key": key, "count": len(kept), "out": sorted(dropped_here)}
            )
        first = rows[0].check_results or {}
        return {
            "session_date": session_date.isoformat(),
            "failed": self.last_screen_failed(),
            "equity": first.get("equity"),
            "max_position_usd": first.get("max_position_usd"),
            "funnel": funnel,
            "eligible_total": counts["eligible"],
        }

    def _ping(self) -> None:
        if self._notify:
            self._notify()


def _failed_check(checks: dict[str, str]) -> str | None:
    for key in CHECK_KEYS:
        if checks.get(key) != "pass":
            return key
    return None


def _period_return(bars: list[DailyBar], lookback: int) -> Decimal | None:
    """Return over the last ``lookback`` closes, in percent."""
    if len(bars) < 2:
        return None
    window = bars[-(lookback + 1) :] if len(bars) > lookback else bars
    first, last = window[0].close, window[-1].close
    if not first:
        return None
    return ((last - first) / first * Decimal(100)).quantize(Decimal("0.0001"))


def _avg_dollar_volume(bars: list[DailyBar], lookback: int) -> Decimal | None:
    if not bars:
        return None
    window = bars[-lookback:] if len(bars) > lookback else bars
    total = Decimal(0)
    for bar in window:
        total += bar.close * bar.volume
    return (total / len(window)).quantize(Decimal("0.01"))


def _funnel_counts(rows: list[UniverseScreenRow]) -> dict[str, int]:
    """Sequential funnel: count rows passing checks 1..k for each stage."""
    counts: dict[str, int] = {"pool": len(rows)}
    for index, key in enumerate(CHECK_KEYS):
        counts[key] = sum(
            1
            for r in rows
            if all(
                (r.check_results or {}).get(k) == "pass"
                for k in CHECK_KEYS[: index + 1]
            )
        )
    return counts
