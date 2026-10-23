"""The backtest worker — one child process per run (docs/SPEC.md).

``python -m app.backtests.worker <run-id>`` loads the run row, fills the
shared bar store and the run's market.db within its own 40-calls/minute
budget, then replays the live engine for every selected strategy in
lockstep over the historical sessions, at lower CPU priority.

The row in the live database is the only control channel:

- ``cancel_requested``: checked every session (and while paused); the run
  stops cleanly and keeps its results.
- ``paused``: set by the app while a live cycle or position check runs —
  the worker waits before every Alpaca download and every model call.
- progress, cost and status are committed after every session.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.backtests.bundle import StrategyBundle, build_bundle
from app.backtests.results import summarise
from app.backtests.store import (
    MARKET_CALENDAR,
    MARKET_COVERAGE,
    bar_store_path,
    dir_size,
    make_bar_store,
    make_market_db,
    run_dir_for,
    session_maker_for,
)
from app.config import AppConfig
from app.data.cache import BarCache, _naive_utc
from app.data.models import Bar5m as Bar5mData
from app.data.models import CalendarDay, DailyBar
from app.data.protocols import EarningsSource
from app.data.rate_limiter import RateLimitConfig, RateLimiter
from app.db.base import get_session_maker
from app.db.models import (
    Backtest,
    Bar1d,
    Bar5m,
    CorporateAction,
    Decision,
    Earning,
    Run,
    Strategy,
    UniversePoolRow,
)
from app.decide.client import FakeDecisionClient, make_decision_client
from app.decide.llm_cache import LLMCacheStore
from app.market.clock import NY_TZ, Clock, FakeClock, RealClock, SessionClock

logger = logging.getLogger("backtest.worker")


class RunCancelled(Exception):
    """The row's cancel flag was set — stop cleanly, keep the results."""


class RunGate:
    """Reads the run row's cancel/pause flags from the live database."""

    def __init__(self, sessions: sessionmaker[Session], run_id: int) -> None:
        self._sessions = sessions
        self.run_id = run_id

    def flags(self) -> tuple[bool, bool]:
        with self._sessions() as session:
            row = session.get(Backtest, self.run_id)
            if row is None:
                return True, False
            return bool(row.cancel_requested), bool(row.paused)

    async def wait_for_call(self) -> None:
        """Wait while the app has a live job running; raise on cancel."""
        while True:
            cancelled, paused = self.flags()
            if cancelled:
                raise RunCancelled()
            if not paused:
                return
            await asyncio.sleep(0.5)


@dataclass
class RunParams:
    """The run row's fields as plain values (the row lives in the live DB)."""

    id: int
    name: str
    start: date
    end: date
    universe_mode: str
    strategies: list[dict[str, Any]]
    starting_equity: Decimal
    slippage_pct: Decimal
    warnings: list[str] = field(default_factory=list)


def load_params(sessions: sessionmaker[Session], run_id: int) -> RunParams | None:
    with sessions() as session:
        row = session.get(Backtest, run_id)
        if row is None or row.status not in ("queued", "running", "downloading"):
            return None
        return RunParams(
            id=row.id,
            name=row.name,
            start=row.start_date,
            end=row.end_date,
            universe_mode=row.universe_mode,
            strategies=list(row.strategies),
            starting_equity=row.starting_equity,
            slippage_pct=row.slippage_pct,
        )


def set_status(
    sessions: sessionmaker[Session],
    run_id: int,
    status: str,
    wall: Clock,
    warnings: list[str] | None = None,
) -> None:
    with sessions() as session:
        row = session.get(Backtest, run_id)
        if row is not None:
            row.status = status
            row.finished_at = _naive_utc(wall.now())
            if warnings is not None:
                row.warnings = (row.warnings or []) + warnings
            session.commit()


# ---------------------------------------------------------------------------
# download phase


def _download_limiter(config: AppConfig) -> RateLimiter:
    """One shared limiter for every Alpaca download call in a run — at
    most ``backtest.alpaca_calls_per_minute`` (40) calls per minute in
    total, so live keeps its own 150."""
    return RateLimiter(
        RateLimitConfig(
            max_calls=int(
                config.get("backtest", "alpaca_calls_per_minute", default=40)
            )
        )
    )


async def _fill_market_db(
    *,
    run: RunParams,
    days: list[CalendarDay],
    symbols: list[str],
    alpaca: Any,
    earnings: EarningsSource | None,
    market_sf: sessionmaker[Session],
    gate: RunGate,
    wall: Clock,
    config: AppConfig,
    limiter: RateLimiter | None = None,
) -> list[str]:
    """Write the run's read-only inputs: calendar, earnings, news, CAs."""
    warnings: list[str] = []
    limiter = limiter or _download_limiter(config)
    fetched = _naive_utc(wall.now())
    with market_sf() as session:
        for table in (MARKET_CALENDAR, MARKET_COVERAGE):
            session.execute(delete(table))
        session.commit()
        for day in days:
            session.execute(
                MARKET_CALENDAR.insert().values(
                    date=day.date,
                    open=day.open.isoformat(),
                    close=day.close.isoformat(),
                )
            )
        session.commit()

    missing: list[str] = []
    for symbol in symbols:
        await gate.wait_for_call()
        result = (
            await earnings.fetch_earnings([symbol], run.start, run.end)
            if earnings is not None
            else None
        )
        if result is None:
            result_error: str | None = "no earnings source"
            rows = []
        else:
            result_error = result.error
            rows = result.data
        with market_sf() as session:
            if result_error is not None:
                session.execute(
                    MARKET_COVERAGE.insert().values(
                        kind="earnings", symbol=symbol, status="missing"
                    )
                )
                missing.append(symbol)
            else:
                session.execute(
                    MARKET_COVERAGE.insert().values(
                        kind="earnings", symbol=symbol, status="ok"
                    )
                )
                for ev in rows:
                    exists = session.execute(
                        select(Earning.id).where(
                            Earning.symbol == ev.symbol,
                            Earning.date
                            == datetime.combine(ev.date, datetime.min.time()),
                        )
                    ).scalar_one_or_none()
                    if exists is None:
                        session.add(
                            Earning(
                                symbol=ev.symbol,
                                date=datetime.combine(
                                    ev.date, datetime.min.time()
                                ),
                                time_of_day=ev.time_of_day,
                                hour_raw=ev.hour_raw,
                                fetched_at=fetched,
                            )
                        )
            session.commit()
    if missing:
        warnings.append(
            "Earnings data missing for "
            + ", ".join(missing)
            + " — entries for these symbols were blocked"
        )

    if alpaca is not None:
        # The decision prompt reads a 24h window, so the first sessions of
        # the run need headlines from just before it.
        from app.engine.cycle import NEWS_WINDOW

        news_start = (run.start - NEWS_WINDOW) - timedelta(days=1)
        for symbol in symbols:
            await gate.wait_for_call()
            async with limiter.call_slot():
                result = await alpaca.fetch_news(
                    [symbol],
                    datetime.combine(
                        news_start, datetime.min.time(), tzinfo=NY_TZ
                    ),
                    datetime.combine(
                        run.end, datetime.max.time(), tzinfo=NY_TZ
                    ),
                )
            if result.error is not None:
                warnings.append(
                    f"news download failed for {symbol}: {result.error}"
                )
                continue
            from app.backtests.store import MARKET_NEWS

            with market_sf() as session:
                status = (result.statuses or {}).get(symbol, "complete" if result.data else "empty")
                session.execute(MARKET_COVERAGE.insert().values(
                    kind="news", symbol=symbol, status=status,
                ))
                for item in result.data:
                    exists = session.execute(
                        select(MARKET_NEWS.c.id).where(
                            MARKET_NEWS.c.news_id == item.id,
                            MARKET_NEWS.c.symbol == item.symbol,
                        )
                    ).scalar_one_or_none()
                    if exists is None:
                        session.execute(
                            MARKET_NEWS.insert().values(
                                news_id=item.id,
                                symbol=item.symbol,
                                headline=item.headline,
                                source=item.source,
                                created_at=_naive_utc(item.created_at),
                            )
                        )
                session.commit()

        await gate.wait_for_call()
        async with limiter.call_slot():
            ca = await alpaca.fetch_corporate_actions(
                symbols, run.start, run.end
            )
        if ca.error is not None:
            warnings.append(f"corporate actions download failed: {ca.error}")
        else:
            with market_sf() as session:
                for action in ca.data:
                    ex_day = datetime.combine(action.ex_date, datetime.min.time())
                    exists = session.execute(
                        select(CorporateAction.id).where(
                            CorporateAction.symbol == action.symbol,
                            CorporateAction.action_type == action.action_type,
                            CorporateAction.date == ex_day,
                        )
                    ).scalar_one_or_none()
                    if exists is None:
                        session.add(
                            CorporateAction(
                                symbol=action.symbol,
                                action_type=action.action_type,
                                date=ex_day,
                                ratio_or_amount=action.amount_or_ratio,
                            )
                        )
                session.commit()
    return warnings


def _quarters(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into ~90-day chunks (the download call unit)."""
    out = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=90))
        out.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return out


async def _fill_bar_store(
    *,
    config: AppConfig,
    run: RunParams,
    days: list[CalendarDay],
    run_days: list[CalendarDay],
    symbols: list[str],
    alpaca: Any,
    store: BarCache,
    gate: RunGate,
    limiter: RateLimiter | None = None,
) -> list[str]:
    """Copy what the live cache has, then download the rest — at most
    ``backtest.alpaca_calls_per_minute`` calls, paused during live jobs."""
    limiter = limiter or _download_limiter(config)
    warnings: list[str] = []
    live_sf = get_session_maker()
    start_dt, end_dt = days[0].open, days[-1].close
    with live_sf() as session:
        rows5 = session.execute(
            select(Bar5m).where(
                Bar5m.symbol.in_(symbols),
                Bar5m.start >= _naive_utc(start_dt),
                Bar5m.start <= _naive_utc(end_dt),
            )
        ).scalars().all()
        rows1 = session.execute(
            select(Bar1d).where(
                Bar1d.symbol.in_(symbols),
                Bar1d.date >= days[0].date,
                Bar1d.date <= run.end,
            )
        ).scalars().all()
    store.store(
        [
            Bar5mData(
                symbol=r.symbol,
                start=_aware(r.start),
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
                feed=r.feed,
            )
            for r in rows5
        ]
    )
    store.store_daily(
        [
            DailyBar(
                symbol=r.symbol,
                date=r.date,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
            )
            for r in rows1
        ]
    )

    if alpaca is None:
        return warnings

    day_set = {s.date for s in days}
    for symbol in symbols:
        missing = [
            s
            for s in days
            if s.date not in store.covered_session_dates(symbol, [s])
        ]
        if not missing:
            continue
        first, last = missing[0].date, missing[-1].date
        for chunk_start, chunk_end in _quarters(first, last):
            await gate.wait_for_call()
            async with limiter.call_slot():
                result = await alpaca.fetch_5m_bars(
                    [symbol],
                    datetime.combine(
                        chunk_start, datetime.min.time(), tzinfo=NY_TZ
                    ),
                    datetime.combine(
                        chunk_end, datetime.max.time(), tzinfo=NY_TZ
                    ),
                )
            if result.error is not None:
                warnings.append(
                    f"5m bars download failed for {symbol} "
                    f"{chunk_start}…{chunk_end}: {result.error}"
                )
                continue
            store.store([b for b in result.data if b.start.date() in day_set])
        await gate.wait_for_call()
        async with limiter.call_slot():
            result = await alpaca.fetch_daily_bars(
                symbol, days[0].date, run.end
            )
        if result.error is None:
            store.store_daily(result.data)
        else:
            warnings.append(
                f"daily bars download failed for {symbol}: {result.error}"
            )
    missing_days = sum(
        1
        for s in run_days
        for symbol in symbols
        if s.date not in store.covered_session_dates(symbol, [s])
    )
    if missing_days:
        warnings.append(
            f"{missing_days} symbol-sessions have no bars — those days are skipped"
        )
    return warnings


def _aware(dt: datetime) -> datetime:
    from datetime import UTC

    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# the replay


def _seed_universe_pool(
    live_sf: sessionmaker[Session], bundle: StrategyBundle
) -> None:
    """Screened mode replays today's pool — flagged as survivorship bias."""
    with live_sf() as session:
        rows = session.execute(select(UniversePoolRow)).scalars().all()
        data = [
            {
                c.name: getattr(r, c.name)
                for c in UniversePoolRow.__table__.columns
                if c.name != "id"
            }
            for r in rows
        ]
    if not data:
        return
    with bundle.session_factory() as session:
        for values in data:
            session.add(UniversePoolRow(**values))
        session.commit()


def _seed_run_row(bundle: StrategyBundle, first: CalendarDay, equity: Decimal) -> None:
    with bundle.session_factory() as session:
        session.add(
            Run(
                started_at=_naive_utc(first.open),
                starting_equity=equity,
                spy_close=None,
            )
        )
        session.commit()


async def _run_sessions(
    *,
    config: AppConfig,
    run: RunParams,
    days: list[CalendarDay],
    bundles: list[StrategyBundle],
    clock: FakeClock,
    gate: RunGate,
    live_sf: sessionmaker[Session],
    wall: Clock,
) -> str | None:
    """Lockstep replay; returns a final status or None when it finished."""
    session_clock = SessionClock(clock, config, bundles[0].calendar)
    screener_time = str(config.get("universe", "screen_time", default="08:10"))
    sh, sm = (int(p) for p in screener_time.split(":")[:2])
    cap = Decimal(str(config.get("backtest", "monthly_cost_cap_usd", default=20)))
    total = len(days)

    for index, session_day in enumerate(days):
        if run.universe_mode == "screened":
            clock.set(
                datetime.combine(
                    session_day.date, datetime.min.time(), tzinfo=NY_TZ
                ).replace(hour=sh, minute=sm)
            )
            for bundle in bundles:
                try:
                    await bundle.universe_service.run_screen(clock.now())
                except Exception:  # noqa: BLE001
                    logger.exception("screen failed for %s", bundle.slug)

        events = sorted(
            [
                (t, "cycle")
                for t in session_clock.active_cycles(session_day)
            ]
            + [
                (t, "check")
                for t in session_clock.active_checks(session_day)
            ]
        )
        for at, kind in events:
            clock.set(at)
            now = clock.now()
            for bundle in bundles:
                await bundle.broker.replay(now)
                if kind == "cycle":
                    await bundle.cycle.run(at)
                else:
                    await bundle.checker.run()

        spent = _run_spend(bundles)
        with live_sf() as session:
            row = session.get(Backtest, run.id)
            if row is not None:
                row.progress = (
                    f"{session_day.date:%b %Y} · session {index + 1} of {total}"
                )
                row.cost_usd = spent
                row.size_bytes = dir_size(run_dir_for(config, run.id))
                session.commit()
        if _month_spend(live_sf, wall.now(), run.id) + spent >= cap:
            return "stopped_at_cost_cap"
        cancelled, _paused = gate.flags()
        if cancelled:
            return "cancelled"
    return None


def _run_spend(bundles: list[StrategyBundle]) -> Decimal:
    """Uncached model-call cost across the run's strategy databases."""
    total = Decimal(0)
    for bundle in bundles:
        with bundle.session_factory() as session:
            total += Decimal(
                str(
                    session.execute(
                        select(func.coalesce(func.sum(Decision.cost), 0)).where(
                            Decision.cached.is_(False)
                        )
                    ).scalar_one()
                )
            )
    return total


def _month_spend(
    sessions: sessionmaker[Session], now: datetime, run_id: int
) -> Decimal:
    first = now.date().replace(day=1)
    with sessions() as session:
        rows = session.execute(
            select(Backtest.cost_usd).where(
                Backtest.id != run_id,
                Backtest.created_at
                >= _naive_utc(datetime.combine(first, datetime.min.time())),
            )
        ).scalars().all()
    return sum((r for r in rows), Decimal(0))


# ---------------------------------------------------------------------------
# entry point


def _run_symbols(
    config: AppConfig, live_sf: sessionmaker[Session]
) -> list[str]:
    """The symbols a run needs data for: the configured list plus the pool."""
    symbols = list(config.get("universe", "symbols", default=[]) or [])
    with live_sf() as session:
        pool = session.execute(select(UniversePoolRow.symbol)).scalars().all()
    return sorted(set(symbols) | set(pool) | {"SPY"})


def _bundle_config(
    config: AppConfig, spec: dict[str, Any], live_sf: sessionmaker[Session]
) -> AppConfig:
    """A shadow runs with its overlay; live and rules-only run live config."""
    if spec["ref"] == "live" or spec["kind"] == "rules_only":
        return config
    from app.shadows.strategy import strategy_config

    with live_sf() as session:
        strategy = session.get(Strategy, int(str(spec["ref"]).split(":")[-1]))
    if strategy is None:
        return config
    return strategy_config(config, strategy)


def _bundle_client(config: AppConfig, spec: dict[str, Any], secrets: Any) -> Any:
    if spec["kind"] == "rules_only":
        return None
    if config.env.mode == "simulated":
        return FakeDecisionClient()
    key = secrets.get("openai") if secrets is not None else None
    if spec["ref"] == "live":
        return make_decision_client(config, api_key=key)
    from app.decide.client import OpenAIDecisionClient
    from app.secrets_service import MISSING_KEY

    view = config.overlay(
        {
            "decision": {
                "model": spec.get("model"),
                "temperature": spec.get("temperature") or 0,
            }
        }
    )
    return OpenAIDecisionClient(view, api_key=key or MISSING_KEY)


async def run_backtest(
    run_id: int,
    *,
    config: AppConfig,
    live_sf: sessionmaker[Session] | None = None,
    alpaca: Any = None,
    earnings: EarningsSource | None = None,
    secrets: Any = None,
    wall: Clock | None = None,
) -> None:
    """Run the whole backtest; raises nothing — status lands on the row."""
    with contextlib.suppress(AttributeError, OSError):
        os.nice(int(config.get("backtest", "process_nice", default=10)))
    wall = wall or RealClock()
    live_sf = live_sf or get_session_maker()
    clock = FakeClock(datetime(2020, 1, 1, tzinfo=NY_TZ))
    gate = RunGate(live_sf, run_id)

    run = load_params(live_sf, run_id)
    if run is None:
        return
    set_status(live_sf, run_id, "downloading", wall)
    # finished_at marks a terminal state; clear it while the run works.
    with live_sf() as session:
        row = session.get(Backtest, run_id)
        if row is not None:
            row.finished_at = None
            session.commit()

    warnings: list[str] = []
    status = "done"
    try:
        run_dir = run_dir_for(config, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        market_sf = make_market_db(run_dir / "market.db", config)
        store = make_bar_store(bar_store_path(config), config)

        # Signals at the start of the period need the same lookback the
        # live cache keeps, so the fill covers the period plus it.
        lookback = max(
            int(config.get("schedule", "history_sessions", default=60)),
            int(config.get("schedule", "daily_history_sessions", default=260)),
        ) * 2
        fill_start = run.start - timedelta(days=lookback)
        limiter = _download_limiter(config)
        if alpaca is not None:
            await gate.wait_for_call()
            async with limiter.call_slot():
                cal = await alpaca.fetch_calendar(fill_start, run.end)
            if cal.error is not None:
                raise RuntimeError(f"calendar download failed: {cal.error}")
            all_days = cal.data
        else:
            all_days = _weekday_sessions(fill_start, run.end)
        days = [d for d in all_days if d.date >= run.start]

        symbols = _run_symbols(config, live_sf)
        warnings += await _fill_market_db(
            run=run,
            days=all_days,
            symbols=symbols,
            alpaca=alpaca,
            earnings=earnings,
            market_sf=market_sf,
            gate=gate,
            wall=wall,
            config=config,
            limiter=limiter,
        )
        warnings += await _fill_bar_store(
            config=config,
            run=run,
            days=all_days,
            run_days=days,
            symbols=symbols,
            alpaca=alpaca,
            store=store,
            gate=gate,
            limiter=limiter,
        )
        set_status(live_sf, run_id, "running", wall)
        with live_sf() as session:
            row = session.get(Backtest, run_id)
            if row is not None:
                row.finished_at = None
                session.commit()

        from app.backtests.sources import (
            BacktestEarningsSource,
            make_backtest_sources,
        )

        bundles: list[StrategyBundle] = []
        cache = LLMCacheStore(live_sf, clock)
        earnings_src = BacktestEarningsSource(market_sf)
        for spec in run.strategies:
            bundle_config = _bundle_config(config, spec, live_sf)
            sources = make_backtest_sources(
                store, market_sf, clock, bundle_config
            )
            bundle = build_bundle(
                config=bundle_config,
                clock=clock,
                run_id=run_id,
                slug=str(spec["slug"]),
                name=str(spec["name"]),
                kind=str(spec["kind"]),
                sources=sources,
                store=store,
                decision_client=_bundle_client(config, spec, secrets),
                llm_cache=cache,
                before_call=gate.wait_for_call,
                starting_equity=run.starting_equity,
                slippage_pct=run.slippage_pct,
            )
            await bundle.calendar.load(all_days[0].date, run.end)
            if run.universe_mode == "screened":
                _seed_universe_pool(live_sf, bundle)
            _seed_run_row(bundle, days[0], run.starting_equity)
            bundle.signals.earnings_missing = earnings_src.missing_symbols(symbols)
            _prime_bars(bundle, store, all_days, symbols)
            await bundle.market_data.refresh_earnings(run.start, run.end)
            bundles.append(bundle)

        final = await _run_sessions(
            config=config,
            run=run,
            days=days,
            bundles=bundles,
            clock=clock,
            gate=gate,
            live_sf=live_sf,
            wall=wall,
        )
        if final is not None:
            status = final
    except RunCancelled:
        status = "cancelled"
    except Exception as exc:  # noqa: BLE001
        logger.exception("backtest run %s failed", run_id)
        warnings.append(str(exc)[:500])
        status = "interrupted"

    with live_sf() as session:
        _finish(
            session, config, run_id, warnings, status, bundles_hint=run, wall=wall
        )
        session.commit()


def _prime_bars(
    bundle: StrategyBundle,
    store: BarCache,
    days: list[CalendarDay],
    symbols: list[str],
) -> None:
    """Copy the store's bars into the strategy DB's cache — the same path
    live's startup ``refresh_history`` takes, read-only on the store."""
    start_dt, end_dt = days[0].open, days[-1].close
    cache = bundle.market_data.bars_cache
    for symbol in symbols:
        cache.store(store.get(symbol, start_dt, end_dt))
        cache.store_daily(store.get_daily(symbol, days[0].date, days[-1].date))


def _weekday_sessions(start: date, end: date) -> list[CalendarDay]:
    """Calendar fallback when no Alpaca client exists (tests/simulated)."""
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(
                CalendarDay(
                    date=cursor,
                    open=datetime.combine(
                        cursor, datetime.min.time(), tzinfo=NY_TZ
                    ).replace(hour=9, minute=30),
                    close=datetime.combine(
                        cursor, datetime.min.time(), tzinfo=NY_TZ
                    ).replace(hour=16),
                )
            )
        cursor += timedelta(days=1)
    return days


def _finish(
    session: Session,
    config: AppConfig,
    run_id: int,
    warnings: list[str],
    status: str,
    *,
    bundles_hint: RunParams,
    wall: Clock,
) -> None:
    """Write the per-strategy summaries and the final status."""
    row = session.get(Backtest, run_id)
    if row is None:
        return
    run_dir = run_dir_for(config, run_id)
    summary = {}
    for spec in bundles_hint.strategies:
        db = run_dir / f"{spec['slug']}.db"
        if db.exists():
            sf = session_maker_for(db, config)
            summary[spec["slug"]] = summarise(
                sf, config=config, equity=bundles_hint.starting_equity
            )
    row.summary = summary
    row.warnings = (row.warnings or []) + warnings + _read_with_care(
        bundles_hint
    )
    row.size_bytes = dir_size(run_dir)
    row.status = status
    row.finished_at = _naive_utc(wall.now())


def _read_with_care(run: RunParams) -> list[str]:
    """Every assumption the run relied on (docs/SPEC.md, Results)."""
    notes = [
        "Spreads are assumed, not measured — the free data plan has no "
        "historical quotes.",
        "A bar that touches both stop and target counts the stop first.",
    ]
    if run.universe_mode == "screened":
        notes.append(
            "Survivorship: the screened pool is today's list; delisted "
            "symbols never appear."
        )
    notes.append("A backtest is evidence about the past, never a forecast.")
    return notes


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_id = int(sys.argv[1])
    config = AppConfig()

    from app.api.main import load_saved_settings
    from app.secrets_service import SecretsStore

    live_sf = get_session_maker()
    load_saved_settings(config)
    secrets = SecretsStore(config, RealClock(), session_factory=live_sf)
    alpaca = None
    earnings_src: EarningsSource | None = None
    if config.env.mode != "simulated":
        from app.data.alpaca import AlpacaDataClient
        from app.data.finnhub import FinnhubEarningsSource
        from app.secrets_service import MISSING_KEY

        rpm = int(config.get("backtest", "alpaca_calls_per_minute", default=40))
        limiter = RateLimiter(RateLimitConfig(max_calls=rpm))
        key_id, secret = secrets.alpaca_pair()
        alpaca = AlpacaDataClient(
            key_id or MISSING_KEY,
            secret or MISSING_KEY,
            rate_limiter=limiter,
        )
        earnings_src = FinnhubEarningsSource(
            secrets.get("finnhub") or MISSING_KEY
        )
    else:
        from app.data.fake import FakeEarningsSource

        earnings_src = FakeEarningsSource()

    asyncio.run(
        run_backtest(
            run_id,
            config=config,
            live_sf=live_sf,
            alpaca=alpaca,
            earnings=earnings_src,
            secrets=secrets,
        )
    )


if __name__ == "__main__":
    main()
