from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.api.backtests import router as backtests_router
from app.api.inspector import router as inspector_router
from app.api.routes import router as api_router
from app.api.strategies import router as strategies_router
from app.api.websocket import UpdateBus
from app.api.websocket import router as ws_router
from app.backtests.manager import BacktestManager
from app.broker.base import Broker
from app.config import ROOT, AppConfig, get_config
from app.data.factory import DataSources, make_data_sources
from app.data.service import MarketDataService
from app.db.backup import create_backup
from app.db.base import check_schema_matches_models, get_session_maker
from app.db.models import Strategy
from app.decide.client import (
    DecisionClient,
    FakeDecisionClient,
    OpenAIDecisionClient,
    UnpricedModelError,
    make_decision_client,
)
from app.decide.llm_cache import LLMCacheStore
from app.decide.service import DecisionService
from app.engine.cycle import CycleRunner
from app.engine.executor import Executor
from app.engine.outcomes import OutcomeWorker
from app.engine.position_check import PositionChecker
from app.engine.state import (
    finalize_interrupted_cycles,
    load_execution_state,
    record_event,
    recover_interrupted_cycles,
    set_event_sink,
)
from app.logging_config import setup_logging
from app.market.calendar import CalendarProvider, CalendarStore
from app.market.clock import NY_TZ, Clock, make_clock
from app.notifications.service import NotificationService, Notifier
from app.reviews.client import make_review_client
from app.reviews.service import ReviewService
from app.secrets_service import (
    MISSING_KEY,
    KeyTesters,
    SecretsStore,
    install_secret_scrub_filter,
    make_key_testers,
    masked_account,
    read_env_keys,
    scrub,
)
from app.shadows.portfolio import LiveGateState
from app.shadows.runner import ShadowRunner
from app.shadows.strategy import check_model_prices
from app.signals.service import SignalService
from app.universe.service import UniverseService

logger = logging.getLogger(__name__)

FRONTEND_DIST = ROOT / "frontend" / "dist"


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
    # Independent sources fetched together — the calendar must load first
    # (the daily refresh resolves sessions through it).
    _, _, _, fx = await asyncio.gather(
        market_data.refresh_earnings(
            today - timedelta(days=30), today + timedelta(days=90)
        ),
        market_data.refresh_corporate_actions(
            today - timedelta(days=30), today + timedelta(days=90)
        ),
        market_data.refresh_daily(today),
        sources.fx.fetch_fx(today),
    )
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


def run_readiness_tick(
    config: AppConfig,
    calendar: CalendarProvider,
    clock: Clock,
    notifier: Notifier,
    publish: Callable[[set[str]], None],
) -> None:
    """Store the session-close readiness snapshot(s).

    Runs every minute like the cycle audit: after each session's close it
    evaluates the 15 criteria once and stores them; sessions missed while
    the app was down are backfilled. A failure is logged, never fatal.
    """
    try:
        from app.readiness.service import tick

        tick(
            config=config,
            calendar=calendar,
            clock=clock,
            notifier=notifier,
            publish=publish,
        )
    except Exception:  # noqa: BLE001
        logger.exception("readiness tick failed")


def run_daily_backup(clock: Clock, market_data: MarketDataService) -> None:
    """Daily job (app timezone): copy the database and prune old data.

    After the copy succeeds, cached 5-minute bars older than
    ``bars_5m_keep_sessions`` sessions are pruned (docs/SPEC.md, Cycles at
    scale). A failed copy is logged and recorded as an event so a missed
    backup is not silent; if the event write also fails, the job exception
    is left for the scheduler log.
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
        # No copy on disk — nothing older than the keep window is pruned
        # until a backup has safely run.
        return
    try:
        pruned = market_data.prune_5m_bars(clock.now())
        if pruned:
            logger.info("pruned %d cached 5m bars past the keep window", pruned)
    except Exception:  # noqa: BLE001
        logger.exception("5m bar pruning failed")


async def run_scheduled_cycle(
    runner: CycleRunner,
    clock: Clock,
    cycle_time: str,
    backtests: Any = None,
) -> None:
    """Scheduler entry point: run the decision cycle due at ``cycle_time`` today."""
    today = clock.now().astimezone(NY_TZ).date()
    hour, minute = (int(p) for p in cycle_time.split(":")[:2])
    scheduled = datetime.combine(today, dtime(hour, minute), tzinfo=NY_TZ)
    if backtests is not None:
        backtests.set_paused(True)
    try:
        await runner.run(scheduled)
    finally:
        if backtests is not None:
            backtests.set_paused(False)


async def run_universe_screen(
    service: UniverseService, bus: UpdateBus | None = None
) -> None:
    """Scheduler entry point: the 08:10 New York liquidity screen.

    The screen runs in either mode — in ``screened`` mode its eligible
    list is what cycles trade from that session on. A data failure keeps
    the previous list and shows a banner (docs/SPEC.md, Larger universe).
    """
    await service.run_screen()
    if bus is not None:
        bus.publish("universe", "banners", "header")


async def run_scheduled_check(
    checker: PositionChecker,
    bus: UpdateBus | None = None,
    backtests: Any = None,
) -> None:
    """Scheduler entry point for the 15-minute position check."""
    if backtests is not None:
        backtests.set_paused(True)
    try:
        await checker.run()
    finally:
        if backtests is not None:
            backtests.set_paused(False)
    if bus is not None:
        bus.publish("positions", "orders", "banners", "header", "performance")


async def run_weekly_review(service: ReviewService) -> None:
    """Scheduler entry point for the Saturday weekly review."""
    try:
        await service.write_weekly_due()
    except Exception:  # noqa: BLE001
        logger.exception("weekly review job failed")


async def run_daily_briefing(service: ReviewService) -> None:
    """Scheduler entry point for the pre-open daily briefing."""
    try:
        await service.write_briefing_due()
    except Exception:  # noqa: BLE001
        logger.exception("daily briefing job failed")


async def run_due_verdicts(service: ReviewService) -> None:
    """Interval scan: draft verdicts for decisions whose 5-session
    outcome has landed since the last pass."""
    try:
        await service.write_due_verdicts()
    except Exception:  # noqa: BLE001
        logger.exception("verdict scan failed")


_WEEKDAY_ABBREVS = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}


def make_broker(
    config: AppConfig,
    sources: DataSources,
    clock: Clock,
    secrets: SecretsStore | None = None,
) -> Broker:
    """The trading broker for the mode: fake in simulated, Alpaca paper else.

    The Alpaca client is created with ``paper=True`` and shares the read
    limiter with the data client so all calls stay under the cap. Keys
    come from the encrypted secrets table; a missing pair keeps the
    stream off until keys are saved in Settings.
    """
    if config.env.mode == "simulated":
        from app.broker.fake import FakeBroker

        return FakeBroker(clock)
    if sources.alpaca is None:  # pragma: no cover - guarded by factory
        raise RuntimeError("paper mode needs the Alpaca data client")
    from app.broker.alpaca import AlpacaPaperBroker

    key_id, secret = secrets.alpaca_pair() if secrets is not None else (None, None)
    return AlpacaPaperBroker(
        key_id or MISSING_KEY,
        secret or MISSING_KEY,
        rate_limiter=sources.alpaca.limiter,
        keyed=bool(key_id and secret),
    )


async def startup_key_checks(
    secrets: SecretsStore, testers: KeyTesters, sources: DataSources
) -> None:
    """Paper mode: refresh each stored key's test state once at startup.

    The Alpaca check doubles as the paper-account check — a non-paper
    account refuses startup (hard rule). Anything else only marks the
    key failing; the app still comes up, paused, with the banner.
    """
    alpaca = sources.alpaca
    if alpaca is not None:
        result = await alpaca.check_paper_account()
        if result.error == "account is not a paper account":
            raise RuntimeError(f"Refusing to start: {result.error}")
        key_id, secret = secrets.alpaca_pair()
        if key_id and secret:
            if result.error is None:
                number = (
                    str(result.data.get("account_number", ""))
                    if isinstance(result.data, dict)
                    else ""
                )
                detail = (
                    f"Account {masked_account(number)}" if number else None
                )
                for name in ("alpaca_key_id", "alpaca_secret"):
                    secrets.record_test(name, True, detail)
            else:
                error = scrub(result.error, secrets.scrub_values())
                for name in ("alpaca_key_id", "alpaca_secret"):
                    secrets.record_test(name, False, error)
    for group in ("openai", "finnhub"):
        await secrets.retest_stored(group, testers, event=False)


def make_shadow_client_factory(
    config: AppConfig, secrets: SecretsStore
) -> Callable[[Strategy], DecisionClient]:
    """Each shadow gets its own model client — never live's — with its own
    model and temperature; simulated mode uses the fake."""

    def factory(strategy: Strategy) -> DecisionClient:
        if config.env.mode == "simulated":
            return FakeDecisionClient()
        view = config.overlay(
            {
                "decision": {
                    "model": strategy.model,
                    "temperature": strategy.temperature or 0,
                }
            }
        )
        return OpenAIDecisionClient(view, api_key=secrets.get("openai") or MISSING_KEY)

    return factory


def spawn_background(
    tasks: set[asyncio.Task[object]], coro: Coroutine[Any, Any, object], name: str
) -> None:
    """Start shadow work as its own task so the live job returns at once."""
    task: asyncio.Task[object] = asyncio.get_running_loop().create_task(coro, name=name)
    tasks.add(task)

    def _done(t: asyncio.Task[object]) -> None:
        tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.error("%s failed", name, exc_info=t.exception())

    task.add_done_callback(_done)


def check_model_prices_at_startup(config: AppConfig) -> None:
    """Refuse to start when the live, reviews or an active shadow's model
    has no entry in model_prices (docs/SPEC.md, Strategies and shadows)."""
    from app.db.base import get_session_maker

    with get_session_maker()() as session:
        try:
            check_model_prices(config, session)
        except UnpricedModelError as exc:
            raise RuntimeError(f"Refusing to start: {exc}") from exc


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
    backtests: Any = None,
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
            args=[runner, clock, str(cycle_at), backtests],
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
    check_model_prices_at_startup(config)

    clock = make_clock(config)
    bus = UpdateBus()
    # Encrypted keys come first: a one-time .env import runs before any
    # client is built, and the scrub filter is in place before anything
    # can log a value that passed through the environment.
    secrets_store = SecretsStore(config, clock)
    # One notification service behind every alert (docs/SPEC.md,
    # Notifications). The record_event sink is registered before any
    # client can write an event row.
    notification_service = NotificationService(
        config=config,
        clock=clock,
        secrets=secrets_store,
        publish=lambda topics: bus.publish(*topics),
    )
    notification_service.start()
    set_event_sink(notification_service.sink)
    key_testers = make_key_testers(config, clock)
    await secrets_store.import_env_keys(read_env_keys(), key_testers)
    install_secret_scrub_filter(secrets_store.scrub_values)

    sources = make_data_sources(config, secrets_store)
    if config.env.mode != "simulated":
        await startup_key_checks(secrets_store, key_testers, sources)
        missing = secrets_store.missing_groups()
        if missing:
            notification_service.notify(
                "source_failing",
                f"{', '.join(missing)} key(s) failing — "
                "the agent stays paused",
                link="/settings",
            )

    broker = make_broker(config, sources, clock, secrets_store)
    calendar_store = CalendarStore(sources.calendar)
    market_data = MarketDataService(sources, calendar_store, config)
    signal_service = SignalService(market_data, calendar_store, config, clock)
    # The llm_cache is shared: live and shadows write through to it (never
    # read it), backtests read it — a rerun on identical inputs is free.
    llm_cache = LLMCacheStore(get_session_maker(), clock)
    decision_service = DecisionService(
        make_decision_client(config, api_key=secrets_store.get("openai")),
        config,
        api_key=secrets_store.get("openai"),
        notifier=notification_service,
        llm_cache=llm_cache,
        clock=clock,
        secrets=secrets_store.scrub_values,
    )
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
        notifier=notification_service,
    )
    outcome_worker = OutcomeWorker(
        market_data=market_data,
        calendar=calendar_store,
        config=config,
        clock=clock,
    )
    # Reviews have their own client and cost cap — they can never use the
    # budget or rate limits reserved for trading decisions.
    review_service = ReviewService(
        client=make_review_client(config, api_key=secrets_store.get("openai")),
        config=config,
        clock=clock,
        calendar=calendar_store,
        market_data=market_data,
        signals=signal_service,
        state=exec_state,
        notify=lambda topics: bus.publish(*topics),
        notifier=notification_service,
        secrets=secrets_store.scrub_values,
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
        notifier=notification_service,
    )
    def live_gate_state() -> LiveGateState:
        from app.db.base import get_session_maker

        with get_session_maker()() as s:
            pending_ack = exec_state.pending_ack(s)
        return LiveGateState(
            kill_switch_on=exec_state.kill_switch_on,
            paused=exec_state.paused,
            pending_ack=pending_ack,
            shock_brake_1h=exec_state.shock_brake_1h,
            shock_brake_session=exec_state.shock_brake_session,
        )

    # Shadow strategies (PR 13): their own clients and cost cap, no broker.
    shadow_runner = ShadowRunner(
        config=config,
        clock=clock,
        calendar=calendar_store,
        client_factory=make_shadow_client_factory(config, secrets_store),
        live_state=live_gate_state,
        notifier=notification_service,
        notify=lambda topics: bus.publish(*topics),
        llm_cache=llm_cache,
        secrets=secrets_store.scrub_values,
    )
    shadow_tasks: set[asyncio.Task[object]] = set()
    cycle_runner.on_cycle_done = lambda report: spawn_background(
        shadow_tasks,
        shadow_runner.after_cycle(report.cycle_id or 0, report.shadow_snapshots),
        f"shadows-cycle-{report.cycle_id}",
    )
    position_checker.on_check_done = lambda: spawn_background(
        shadow_tasks, shadow_runner.after_check(), "shadows-check"
    )
    executor.on_flatten = shadow_runner.flatten
    cycle_runner.shadow_symbols = shadow_runner.held_symbols
    universe_service = UniverseService(
        market_data=market_data,
        sources=sources,
        calendar=calendar_store,
        broker=broker,
        config=config,
        clock=clock,
        notify=lambda: bus.publish("universe", "banners", "header"),
    )
    await refresh_calendar_and_daily_data(
        calendar_store, market_data, sources, config, clock
    )
    today = clock.now().astimezone(NY_TZ).date()
    await market_data.refresh_history(today)

    # The pool seeds once at startup; the daily screen runs from its
    # 08:10 New York job — in screened mode its eligible list is what
    # cycles trade from that session on (docs/SPEC.md, Larger universe).
    # The simulated demo triggers it through POST /api/universe/screen.
    universe_service.ensure_pool_loaded()

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
    from app.db.models import Cycle

    with get_session_maker()() as session:
        recovered = recover_interrupted_cycles(session, exec_state, clock)
        if recovered:
            session.commit()
        # Paused at startup (docs/SPEC.md, Risk management): the state was
        # set by load_execution_state; log it like every other state change.
        record_event(
            session,
            clock,
            level="info",
            event_type="pause",
            details={"on": True, "reason": "startup"},
        )
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

    # A review missed while the app was down is written now with its
    # original date; a briefing older than its trading day is skipped.
    try:
        caught_up = await review_service.catch_up()
        if any(caught_up.values()):
            logger.info("review catch-up wrote %s", caught_up)
    except Exception:  # noqa: BLE001
        logger.exception("review catch-up failed")

    app.state.clock = clock
    app.state.calendar_store = calendar_store
    app.state.data_sources = sources
    app.state.secrets_store = secrets_store
    app.state.key_testers = key_testers
    app.state.market_data = market_data
    app.state.signal_service = signal_service
    if config.env.mode != "simulated":
        cycle_runner.record_missing_cycles("app down")
    app.state.cycle_runner = cycle_runner
    app.state.broker = broker
    app.state.executor = executor
    app.state.exec_state = exec_state
    app.state.position_checker = position_checker
    app.state.review_service = review_service
    app.state.universe = universe_service
    app.state.update_bus = bus
    app.state.notification_service = notification_service
    app.state.shadow_runner = shadow_runner

    # Backtesting (PR 14): one child process at a time; rows left "queued"
    # or "running" by a crash are marked interrupted at startup.
    backtest_manager = BacktestManager(config, clock)
    backtest_manager.interrupt_orphans()
    app.state.backtests = backtest_manager

    scheduler = AsyncIOScheduler(timezone=NY_TZ)
    scheduler.add_job(
        cycle_runner.record_missing_cycles, IntervalTrigger(minutes=1),
        args=["error: scheduled cycle not started"], id="scheduled-cycle-audit",
    )
    # When promoted settings move cycle times the cron jobs are rebuilt.
    cycle_runner._on_settings_promoted = (
        lambda: schedule_cycle_jobs(
            scheduler, cycle_runner, config, clock, backtest_manager
        )
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
    # Liquidity screen (v0.2): 08:10 New York on trading days, after the
    # daily bars refresh. Reads the configured time each startup; no-ops
    # in fixed mode and outside trading days.
    screen_at = str(
        (config.pending() or config.effective())
        .get("universe", {})
        .get("screen", {})
        .get("time_ny", "08:10")
    )
    screen_hour, screen_minute = (int(p) for p in screen_at.split(":")[:2])
    scheduler.add_job(
        run_universe_screen,
        CronTrigger(hour=screen_hour, minute=screen_minute, timezone=NY_TZ),
        args=[universe_service, bus],
        id="universe-screen",
        misfire_grace_time=int(
            config.get("schedule", "misfire_grace_seconds", default=120)
        ),
        coalesce=True,
    )
    # Daily database copy: an app-timezone job (Amsterdam per spec).
    app_tz = ZoneInfo(str(config.get("app", "timezone", default="Europe/Amsterdam")))
    backup_at = str(config.get("storage", "backup_time", default="03:00"))
    backup_hour, backup_minute = (int(p) for p in backup_at.split(":")[:2])
    scheduler.add_job(
        run_daily_backup,
        CronTrigger(hour=backup_hour, minute=backup_minute, timezone=app_tz),
        args=[clock, market_data],
        id="daily-db-backup",
    )
    # Run the decision cycle at every configured cycle time; the runner
    # refreshes the stored signal snapshot as part of the cycle. Missed
    # cycles may run late by at most misfire_grace_seconds, and several
    # misses coalesce into one run — never into a queue of catch-ups.
    schedule_cycle_jobs(
        scheduler, cycle_runner, config, clock, backtest_manager
    )
    misfire_grace = int(
        config.get("schedule", "misfire_grace_seconds", default=120)
    )
    for check_at in config.get("schedule", "position_checks", default=[]) or []:
        hour, minute = (int(p) for p in str(check_at).split(":")[:2])
        scheduler.add_job(
            run_scheduled_check,
            CronTrigger(hour=hour, minute=minute, timezone=NY_TZ),
            args=[position_checker, bus, backtest_manager],
            id=f"position-check-{check_at}",
            misfire_grace_time=misfire_grace,
            coalesce=True,
        )
    # Reviews: weekly (Saturday, app timezone), daily briefing (09:00 New
    # York on trading days) and a light verdict scan. A miss while the app
    # was down is covered by the startup catch-up above, never by misfires.
    weekly_day = _WEEKDAY_ABBREVS.get(
        str(config.get("reviews", "weekly_day", default="saturday"))
        .strip()
        .lower(),
        "sat",
    )
    weekly_at = str(config.get("reviews", "weekly_time", default="10:00"))
    weekly_hour, weekly_minute = (int(p) for p in weekly_at.split(":")[:2])
    scheduler.add_job(
        run_weekly_review,
        CronTrigger(
            day_of_week=weekly_day,
            hour=weekly_hour,
            minute=weekly_minute,
            timezone=app_tz,
        ),
        args=[review_service],
        id="weekly-review",
        coalesce=True,
    )
    briefing_at = str(config.get("reviews", "briefing_time_ny", default="09:00"))
    briefing_hour, briefing_minute = (int(p) for p in briefing_at.split(":")[:2])
    scheduler.add_job(
        run_daily_briefing,
        CronTrigger(hour=briefing_hour, minute=briefing_minute, timezone=NY_TZ),
        args=[review_service],
        id="daily-briefing",
        coalesce=True,
    )
    scheduler.add_job(
        run_due_verdicts,
        IntervalTrigger(minutes=15),
        args=[review_service],
        id="verdict-scan",
        coalesce=True,
    )
    # Quiet-hours heartbeat: sends the held-notifications summary when
    # the window ends, and catches up after a restart.
    scheduler.add_job(
        notification_service.tick,
        IntervalTrigger(minutes=1),
        id="notifications-tick",
        coalesce=True,
    )
    # Readiness: after each session's close evaluate the 15 criteria once
    # and store the snapshot; backfills sessions missed while down.
    scheduler.add_job(
        run_readiness_tick,
        IntervalTrigger(minutes=1),
        args=[
            config,
            calendar_store,
            clock,
            notification_service,
            lambda topics: bus.publish(*topics),
        ],
        id="readiness-tick",
        coalesce=True,
    )
    # Backtest lifecycle: spawn the oldest queued run, sync progress from a
    # running child, keep cost totals fresh.
    scheduler.add_job(
        backtest_manager.tick,
        IntervalTrigger(seconds=10),
        id="backtests-tick",
        misfire_grace_time=10,
        coalesce=True,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        await backtest_manager.shutdown()
        scheduler.shutdown(wait=False)
        # Shadow work is never waited for: it is cut, never live.
        for task in list(shadow_tasks):
            task.cancel()
        set_event_sink(None)
        await notification_service.stop()
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
    app = FastAPI(title="Stock Agent", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api")
    app.include_router(strategies_router, prefix="/api")
    app.include_router(backtests_router, prefix="/api")
    app.include_router(inspector_router, prefix="/api")
    app.include_router(ws_router, prefix="/ws")
    if FRONTEND_DIST.is_dir():
        app.mount("/", SpaStaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
    return app


class SpaStaticFiles(StaticFiles):
    """Serve the built Vite app; unknown paths fall back to index.html."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


app = create_app()
