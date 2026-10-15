from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.api.websocket import router as ws_router
from app.config import AppConfig, get_config
from app.data.factory import DataSources, make_data_sources
from app.data.service import MarketDataService
from app.decide.client import make_decision_client
from app.decide.service import DecisionService
from app.engine.cycle import CycleRunner
from app.market.calendar import CalendarStore
from app.market.clock import NY_TZ, Clock, make_clock
from app.signals.service import SignalService


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(cfg, "head")


def calendar_load_range(config: AppConfig, today: date) -> tuple[date, date]:
    history_sessions = int(config.get("schedule", "history_sessions", default=60))
    daily_sessions = int(
        config.get("schedule", "daily_history_sessions", default=260)
    )
    lookback = max(history_sessions, daily_sessions) * 2
    return today - timedelta(days=lookback), today + timedelta(days=30)


async def refresh_calendar_and_daily_data(
    calendar_store: CalendarStore,
    market_data: MarketDataService,
    sources: DataSources,
    config: AppConfig,
    clock: Clock,
) -> None:
    """Daily refresh: calendar, earnings, corporate actions, FX, daily bars."""
    today = clock.now().astimezone(NY_TZ).date()
    start, end = calendar_load_range(config, today)
    await calendar_store.load(start, end)
    await market_data.refresh_earnings(today - timedelta(days=30), today + timedelta(days=90))
    await market_data.refresh_corporate_actions(
        today - timedelta(days=30), today + timedelta(days=90)
    )
    await market_data.refresh_daily(today)
    fx = await sources.fx.fetch_fx(today)
    market_data.latest_fx = fx


async def refresh_daily_data_and_signals(
    calendar_store: CalendarStore,
    market_data: MarketDataService,
    sources: DataSources,
    config: AppConfig,
    clock: Clock,
    signals: SignalService,
) -> None:
    """The scheduled daily refresh, followed by a stored-snapshot refresh."""
    await refresh_calendar_and_daily_data(
        calendar_store, market_data, sources, config, clock
    )
    await signals.refresh()


async def run_scheduled_cycle(
    runner: CycleRunner, clock: Clock, cycle_time: str
) -> None:
    """Scheduler entry point: run the decision cycle due at ``cycle_time`` today."""
    today = clock.now().astimezone(NY_TZ).date()
    hour, minute = (int(p) for p in cycle_time.split(":")[:2])
    scheduled = datetime.combine(today, dtime(hour, minute), tzinfo=NY_TZ)
    await runner.run(scheduled)


def startup_catch_up_allowed(config: AppConfig) -> bool:
    """Missed cycles are never run late in paper mode; only the frozen
    simulated clock may run one catch-up cycle at startup."""
    return config.env.mode == "simulated"


async def retry_calendar_load(
    calendar_store: CalendarStore,
    config: AppConfig,
    clock: Clock,
) -> None:
    """Retry calendar loading every 5 minutes until it succeeds.

    While the calendar is unavailable every session reads as closed, so no
    cycles run and no entries are possible.
    """
    if calendar_store.is_loaded:
        return
    today = clock.now().astimezone(NY_TZ).date()
    start, end = calendar_load_range(config, today)
    await calendar_store.load(start, end)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    config = get_config()
    Path(config.env.sa_db_path).parent.mkdir(parents=True, exist_ok=True)
    run_migrations()

    sources = make_data_sources(config)
    if sources.alpaca is not None:
        paper = await sources.alpaca.check_paper_account()
        if paper.error is not None:
            raise RuntimeError(f"Refusing to start: {paper.error}")

    clock = make_clock(config)
    calendar_store = CalendarStore(sources.calendar)
    market_data = MarketDataService(sources, calendar_store, config)
    signal_service = SignalService(market_data, calendar_store, config, clock)
    decision_service = DecisionService(make_decision_client(config), config)
    cycle_runner = CycleRunner(
        market_data=market_data,
        signals=signal_service,
        decide=decision_service,
        sources=sources,
        calendar=calendar_store,
        config=config,
        clock=clock,
    )
    await refresh_calendar_and_daily_data(
        calendar_store, market_data, sources, config, clock
    )
    today = clock.now().astimezone(NY_TZ).date()
    await market_data.refresh_history(today)
    if startup_catch_up_allowed(config):
        # Frozen clock: run today's latest due cycle once so a simulated
        # session produces stored decisions.
        await cycle_runner.run_due()
    await signal_service.refresh()

    app.state.clock = clock
    app.state.calendar_store = calendar_store
    app.state.data_sources = sources
    app.state.market_data = market_data
    app.state.signal_service = signal_service
    app.state.cycle_runner = cycle_runner

    scheduler = AsyncIOScheduler(timezone=NY_TZ)
    refresh_at = str(config.get("schedule", "calendar_refresh", default="08:00"))
    refresh_hour, refresh_minute = (int(p) for p in refresh_at.split(":")[:2])
    scheduler.add_job(
        refresh_daily_data_and_signals,
        CronTrigger(hour=refresh_hour, minute=refresh_minute, timezone=NY_TZ),
        args=[calendar_store, market_data, sources, config, clock, signal_service],
        id="daily-data-refresh",
    )
    scheduler.add_job(
        retry_calendar_load,
        IntervalTrigger(minutes=5),
        args=[calendar_store, config, clock],
        id="calendar-retry",
    )
    # Run the decision cycle at every configured cycle time; the runner
    # refreshes the stored signal snapshot as part of the cycle. Missed
    # cycles may run late by at most misfire_grace_seconds, and several
    # misses coalesce into one run — never into a queue of catch-ups.
    misfire_grace = int(
        config.get("schedule", "misfire_grace_seconds", default=120)
    )
    for cycle_at in config.get("schedule", "cycles", default=[]) or []:
        hour, minute = (int(p) for p in str(cycle_at).split(":")[:2])
        scheduler.add_job(
            run_scheduled_cycle,
            CronTrigger(hour=hour, minute=minute, timezone=NY_TZ),
            args=[cycle_runner, clock, str(cycle_at)],
            id=f"cycle-{cycle_at}",
            misfire_grace_time=misfire_grace,
            coalesce=True,
        )
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="Stock Agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api")
    app.include_router(ws_router, prefix="/ws")
    return app


app = create_app()
