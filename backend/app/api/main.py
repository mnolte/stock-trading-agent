from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.api.websocket import router as ws_router
from app.config import AppConfig, get_config
from app.data.factory import DataSources, make_data_sources
from app.data.service import MarketDataService
from app.market.calendar import CalendarStore
from app.market.clock import NY_TZ, Clock, make_clock


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(cfg, "head")


async def refresh_calendar_and_daily_data(
    calendar_store: CalendarStore,
    market_data: MarketDataService,
    sources: DataSources,
    config: AppConfig,
    clock: Clock,
) -> None:
    """Daily 08:00 NY refresh: calendar, earnings, corporate actions, FX rate."""
    today = clock.now().astimezone(NY_TZ).date()
    history_sessions = int(config.get("schedule", "history_sessions", default=60))
    await calendar_store.load(
        today - timedelta(days=history_sessions * 2), today + timedelta(days=30)
    )
    await market_data.refresh_earnings(today - timedelta(days=30), today + timedelta(days=90))
    await market_data.refresh_corporate_actions(
        today - timedelta(days=30), today + timedelta(days=90)
    )
    fx = await sources.fx.fetch_fx(today)
    market_data.latest_fx = fx


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
    await refresh_calendar_and_daily_data(
        calendar_store, market_data, sources, config, clock
    )
    today = clock.now().astimezone(NY_TZ).date()
    await market_data.refresh_history(today)

    app.state.calendar_store = calendar_store
    app.state.data_sources = sources
    app.state.market_data = market_data

    scheduler = AsyncIOScheduler(timezone=NY_TZ)
    scheduler.add_job(
        refresh_calendar_and_daily_data,
        CronTrigger(hour=8, minute=0, timezone=NY_TZ),
        args=[calendar_store, market_data, sources, config, clock],
        id="daily-data-refresh",
    )
    scheduler.start()
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
