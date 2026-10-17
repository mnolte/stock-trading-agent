from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.api.routes import router as api_router
from app.api.websocket import UpdateBus
from app.api.websocket import router as ws_router
from app.broker.base import Broker
from app.config import AppConfig, get_config
from app.data.factory import DataSources, make_data_sources
from app.data.service import MarketDataService
from app.db.backup import create_backup
from app.db.base import check_schema_matches_models
from app.decide.client import make_decision_client
from app.decide.service import DecisionService
from app.engine.cycle import CycleRunner
from app.engine.executor import Executor
from app.engine.outcomes import OutcomeWorker
from app.engine.position_check import PositionChecker
from app.engine.state import (
    finalize_interrupted_cycles,
    load_execution_state,
    recover_interrupted_cycles,
)
from app.logging_config import setup_logging
from app.market.calendar import CalendarStore
from app.market.clock import NY_TZ, Clock, make_clock
from app.signals.service import SignalService

logger = logging.getLogger(__name__)


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
    bus: UpdateBus | None = None,
) -> None:
    """The scheduled daily refresh, followed by a stored-snapshot refresh."""
    await refresh_calendar_and_daily_data(
        calendar_store, market_data, sources, config, clock
    )
    await signals.refresh()
    if bus is not None:
        bus.publish("watchlist", "header", "banners", "performance")


def run_daily_backup(clock: Clock) -> None:
    """Daily job (app timezone): copy the database and prune old copies.

    A failed copy is logged and recorded as an event so a missed backup is
    not silent; if the event write also fails, the job exception is left
    for the scheduler log.
    """
    try:
        create_backup()
    except Exception as exc:  # noqa: BLE001
        logger.exception("daily database copy failed")
        from app.db.base import get_session_maker
        from app.engine.state import record_event

        with get_session_maker()() as session:
            record_event(
                session,
                clock,
                level="warning",
                event_type="backup_failed",
                details={"error": str(exc)},
            )
            session.commit()


async def run_scheduled_cycle(
    runner: CycleRunner, clock: Clock, cycle_time: str
) -> None:
    """Scheduler entry point: run the decision cycle due at ``cycle_time`` today."""
    today = clock.now().astimezone(NY_TZ).date()
    hour, minute = (int(p) for p in cycle_time.split(":")[:2])
    scheduled = datetime.combine(today, dtime(hour, minute), tzinfo=NY_TZ)
    await runner.run(scheduled)


async def run_scheduled_check(
    checker: PositionChecker, bus: UpdateBus | None = None
) -> None:
    """Scheduler entry point for the 15-minute position check."""
    await checker.run()
    if bus is not None:
        bus.publish("positions", "orders", "banners", "header", "performance")


def make_broker(config: AppConfig, sources: DataSources, clock: Clock) -> Broker:
    """The trading broker for the mode: fake in simulated, Alpaca paper else.

    The Alpaca client is created with ``paper=True`` and shares the read
    limiter with the data client so all calls stay under the cap.
    """
    if config.env.mode == "simulated":
        from app.broker.fake import FakeBroker

        return FakeBroker(clock)
    if sources.alpaca is None:  # pragma: no cover - guarded by factory
        raise RuntimeError("paper mode needs the Alpaca data client")
    from app.broker.alpaca import AlpacaPaperBroker

    return AlpacaPaperBroker(
        config.env.alpaca_api_key,
        config.env.alpaca_secret_key,
        rate_limiter=sources.alpaca.limiter,
    )


def check_execution_mode(config: AppConfig) -> None:
    """Only automatic and supervised exist; refuse to start on anything else."""
    mode = str(config.get("execution", "mode", default="automatic"))
    if mode not in ("automatic", "supervised"):
        raise RuntimeError(f"Refusing to start: unknown execution.mode {mode!r}")


def schedule_cycle_jobs(
    scheduler: AsyncIOScheduler,
    runner: CycleRunner,
    config: AppConfig,
    clock: Clock,
) -> None:
    """(Re)build the cron jobs for the configured cycle times.

    Called at startup and again when promoted settings change the cycle
    times — the new schedule applies to the cycles after that.
    """
    for job in scheduler.get_jobs():
        if job.id.startswith("cycle-"):
            scheduler.remove_job(job.id)
    misfire_grace = int(
        config.get("schedule", "misfire_grace_seconds", default=120)
    )
    for cycle_at in config.get("schedule", "cycles", default=[]) or []:
        hour, minute = (int(p) for p in str(cycle_at).split(":")[:2])
        scheduler.add_job(
            run_scheduled_cycle,
            CronTrigger(hour=hour, minute=minute, timezone=NY_TZ),
            args=[runner, clock, str(cycle_at)],
            id=f"cycle-{cycle_at}",
            misfire_grace_time=misfire_grace,
            coalesce=True,
        )


def load_saved_settings(config: AppConfig) -> None:
    """Apply the newest settings_versions row over the YAML defaults."""
    from app.db.base import get_session_maker
    from app.settings_service import latest_version

    with get_session_maker()() as session:
        row = latest_version(session)
        if row is not None:
            config.apply_saved(dict(row.settings), row.version)


def ensure_run_row(broker: Broker, clock: Clock, config: AppConfig) -> None:
    """The first run anchors the benchmark and the "since start" numbers."""
    from app.data.cache import _naive_utc
    from app.db.base import get_session_maker
    from app.db.models import Run

    with get_session_maker()() as session:
        exists = session.execute(
            select(func.count(Run.id))
        ).scalar_one()
        if exists:
            return
        starting = Decimal(
            str(config.get("performance", "starting_equity", default=10000))
        )
        session.add(
            Run(
                started_at=_naive_utc(clock.now()),
                starting_equity=starting,
                spy_close=None,
            )
        )
        session.commit()


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
    setup_logging()
    config = get_config()
    Path(config.env.sa_db_path).parent.mkdir(parents=True, exist_ok=True)
    run_migrations()
    # Migrations are immutable; a schema that drifted outside them fails
    # here with the differing table/column instead of a raw SQL error.
    check_schema_matches_models()
    # Versioned GUI settings sit on top of the YAML before anything reads
    # them; the execution-mode check sees the effective mode.
    load_saved_settings(config)
    check_execution_mode(config)

    clock = make_clock(config)
    bus = UpdateBus()
    sources = make_data_sources(config)
    if sources.alpaca is not None:
        paper = await sources.alpaca.check_paper_account()
        if paper.error is not None:
            raise RuntimeError(f"Refusing to start: {paper.error}")

    broker = make_broker(config, sources, clock)
    calendar_store = CalendarStore(sources.calendar)
    market_data = MarketDataService(sources, calendar_store, config)
    signal_service = SignalService(market_data, calendar_store, config, clock)
    decision_service = DecisionService(make_decision_client(config), config)
    exec_state = load_execution_state(clock, config)
    executor = Executor(
        broker=broker,
        market_data=market_data,
        signals=signal_service,
        calendar=calendar_store,
        config=config,
        clock=clock,
        state=exec_state,
        notify=lambda topics: bus.publish(*topics),
    )
    outcome_worker = OutcomeWorker(
        market_data=market_data,
        calendar=calendar_store,
        config=config,
        clock=clock,
    )
    position_checker = PositionChecker(
        executor=executor,
        state=exec_state,
        config=config,
        clock=clock,
        outcomes=outcome_worker,
    )
    cycle_runner = CycleRunner(
        market_data=market_data,
        signals=signal_service,
        decide=decision_service,
        sources=sources,
        calendar=calendar_store,
        config=config,
        clock=clock,
        executor=executor,
        outcomes=outcome_worker,
        notify=lambda topics: bus.publish(*topics),
    )
    await refresh_calendar_and_daily_data(
        calendar_store, market_data, sources, config, clock
    )
    today = clock.now().astimezone(NY_TZ).date()
    await market_data.refresh_history(today)

    # Trade updates → ledger; then reconcile once so the database starts
    # as a mirror of the broker (adopted positions get their stop here).
    broker.subscribe_trade_updates(executor.on_trade_update)
    await broker.start_stream()
    await executor.reconcile()
    if exec_state.account is None:
        await executor.refresh_account()
    ensure_run_row(broker, clock, config)

    if startup_catch_up_allowed(config):
        # Frozen clock: run today's latest due cycle once so a simulated
        # session produces stored decisions.
        await cycle_runner.run_due()
    await signal_service.refresh()

    # Fill any outcome targets that were due while the app was down, and
    # record the startup equity point.
    from app.db.base import get_session_maker
    from app.db.models import Cycle

    with get_session_maker()() as session:
        recovered = recover_interrupted_cycles(session, exec_state, clock)
        if recovered:
            session.commit()
        # Repeated failures reach the same kill-switch threshold at startup
        # as they do at runtime (docs/SPEC.md, Risk management, Errors). The
        # reason names the newest failure — an interruption or a real error.
        kill_after = int(config.get("risk", "failed_cycles_kill", default=3))
        if (
            exec_state.failed_cycles >= kill_after
            and not exec_state.kill_switch_on
        ):
            last_failed = session.execute(
                select(Cycle)
                .where(Cycle.kind == "cycle", Cycle.status == "error")
                .order_by(Cycle.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            detail = (
                (last_failed.errors or ["unknown"])[0]
                if last_failed is not None
                else "unknown"
            )
            await executor.activate_kill_switch(
                reason=(
                    f"{exec_state.failed_cycles} cycles failed in a row: "
                    f"{detail}"
                ),
                automatic=True,
            )

        outcome_worker.fill(session, clock.now())
        if exec_state.account is not None:
            outcome_worker.write_equity_snapshot(
                session,
                equity=exec_state.account.equity,
                cash=exec_state.account.cash,
            )
        session.commit()

    app.state.clock = clock
    app.state.calendar_store = calendar_store
    app.state.data_sources = sources
    app.state.market_data = market_data
    app.state.signal_service = signal_service
    app.state.cycle_runner = cycle_runner
    app.state.broker = broker
    app.state.executor = executor
    app.state.exec_state = exec_state
    app.state.position_checker = position_checker
    app.state.update_bus = bus

    scheduler = AsyncIOScheduler(timezone=NY_TZ)
    # When promoted settings move cycle times the cron jobs are rebuilt.
    cycle_runner._on_settings_promoted = (
        lambda: schedule_cycle_jobs(scheduler, cycle_runner, config, clock)
    )
    refresh_at = str(config.get("schedule", "calendar_refresh", default="08:00"))
    refresh_hour, refresh_minute = (int(p) for p in refresh_at.split(":")[:2])
    scheduler.add_job(
        refresh_daily_data_and_signals,
        CronTrigger(hour=refresh_hour, minute=refresh_minute, timezone=NY_TZ),
        args=[
            calendar_store,
            market_data,
            sources,
            config,
            clock,
            signal_service,
            bus,
        ],
        id="daily-data-refresh",
    )
    scheduler.add_job(
        retry_calendar_load,
        IntervalTrigger(minutes=5),
        args=[calendar_store, config, clock],
        id="calendar-retry",
    )
    # Daily database copy: an app-timezone job (Amsterdam per spec).
    app_tz = ZoneInfo(str(config.get("app", "timezone", default="Europe/Amsterdam")))
    backup_at = str(config.get("storage", "backup_time", default="03:00"))
    backup_hour, backup_minute = (int(p) for p in backup_at.split(":")[:2])
    scheduler.add_job(
        run_daily_backup,
        CronTrigger(hour=backup_hour, minute=backup_minute, timezone=app_tz),
        args=[clock],
        id="daily-db-backup",
    )
    # Run the decision cycle at every configured cycle time; the runner
    # refreshes the stored signal snapshot as part of the cycle. Missed
    # cycles may run late by at most misfire_grace_seconds, and several
    # misses coalesce into one run — never into a queue of catch-ups.
    schedule_cycle_jobs(scheduler, cycle_runner, config, clock)
    misfire_grace = int(
        config.get("schedule", "misfire_grace_seconds", default=120)
    )
    for check_at in config.get("schedule", "position_checks", default=[]) or []:
        hour, minute = (int(p) for p in str(check_at).split(":")[:2])
        scheduler.add_job(
            run_scheduled_check,
            CronTrigger(hour=hour, minute=minute, timezone=NY_TZ),
            args=[position_checker, bus],
            id=f"position-check-{check_at}",
            misfire_grace_time=misfire_grace,
            coalesce=True,
        )
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        # Normal shutdown: give a running cycle or check the grace window
        # to finish; leftovers become "interrupted" and never count as
        # failures — only rows still "running" at the next startup (a real
        # crash) do (docs/SPEC.md, Risk management, Errors).
        grace = float(
            config.get("storage", "shutdown_grace_seconds", default=60)
        )
        interrupted = await finalize_interrupted_cycles(
            get_session_maker(), clock, grace
        )
        if interrupted:
            logger.warning(
                "Shutdown with %d cycle(s) still running; marked interrupted",
                interrupted,
            )
        await broker.stop_stream()


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
