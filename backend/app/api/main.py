from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app.api.backtests import router as backtests_router
from app.api.insights import router as insights_router
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
from app.db.models import Event, Strategy
from app.decide.client import (
    DecisionClient,
    FakeDecisionClient,
    OpenAIDecisionClient,
    UnpricedModelError,
    make_decision_client,
)
from app.decide.llm_cache import LLMCacheStore
from app.decide.service import DecisionService
from app.engine.after_exit import AfterExitWorker
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
from app.market.clock import NY_TZ, UTC, Clock, make_clock
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


_migration_heads: tuple[str, ...] | None = None


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    global _migration_heads
    if _migration_heads is None:
        _migration_heads = tuple(
            sorted(ScriptDirectory.from_config(cfg).get_heads())
        )
    # A database already at head skips the full upgrade machinery: the
    # version check is one sqlite read instead of a script walk.
    import sqlite3 as _sqlite3

    db_path = Path(get_config().env.sa_db_path)
    if db_path.exists():
        try:
            with _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                version = conn.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()
            if version is not None and (version[0],) == _migration_heads:
                return
        except _sqlite3.Error:
            pass
    command.upgrade(cfg, "head")


def calendar_load_range(config: AppConfig, today: date) -> tuple[date, date]:
    history_sessions = int(config.get("schedule", "history_sessions", default=60))
    daily_sessions = int(
        config.get("schedule", "daily_history_sessions", default=260)
    )
    lookback = max(history_sessions, daily_sessions) * 2
    # The forward range must cover the earnings lookahead (today + 90
    # days): sessions beyond the loaded horizon read as non-trading days,
    # so trading_days_until would saturate instead of giving the real
    # distance to a stored report.
    return today - timedelta(days=lookback), today + timedelta(days=120)


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


def run_daily_backup(clock: Clock, market_data: MarketDataService, ops: Any = None) -> None:
    """Daily job (app timezone): copy the database, prune old data, then
    chain the offsite upload.

    After the copy succeeds, cached 5-minute bars older than
    ``bars_5m_keep_sessions`` sessions are pruned (docs/SPEC.md, Cycles at
    scale). A failed copy is logged and recorded as an event so a missed
    backup is not silent; if the event write also fails, the job exception
    is left for the scheduler log.

    The offsite restic upload runs only after a successful local copy —
    a failed local backup skips it (and warns), never the other way
    around (docs/SPEC.md → VPS deployment → Backups).
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
        # until a backup has safely run, and the offsite upload stays
        # chained to a successful copy.
        return
    try:
        pruned = market_data.prune_5m_bars(clock.now())
        if pruned:
            logger.info("pruned %d cached 5m bars past the keep window", pruned)
    except Exception:  # noqa: BLE001
        logger.exception("5m bar pruning failed")
    if ops is not None and bool(
        get_config().get("ops", "offsite_backup", "enabled", default=False)
    ):
        try:
            ops.run_offsite_backup_sync()
        except Exception as exc:  # noqa: BLE001
            logger.exception("offsite backup failed")
            from app.db.base import get_session_maker
            from app.engine.state import record_event

            with get_session_maker()() as session:
                record_event(
                    session,
                    clock,
                    level="warning",
                    event_type="backup_failed",
                    details={"error": f"offsite: {exc}"},
                )
                session.commit()


async def run_after_close_jobs(
    config: AppConfig,
    calendar: CalendarProvider,
    clock: Clock,
    notifier: Notifier,
    publish: Callable[[set[str]], None],
    after_exit: AfterExitWorker,
) -> None:
    """After each session's close: readiness snapshot, then after-exit
    metrics for the Winners and tails card (docs/SPEC.md, Tail metrics)."""
    loop = asyncio.get_running_loop()

    def publish_from_thread(topics: set[str]) -> None:
        loop.call_soon_threadsafe(publish, topics)

    await run_in_threadpool(
        run_readiness_tick, config, calendar, clock, notifier, publish_from_thread
    )
    try:
        from app.insights.service import tick as insights_tick

        await run_in_threadpool(
            insights_tick, config=config, calendar=calendar, clock=clock,
            publish=publish_from_thread,
        )
    except Exception:  # noqa: BLE001
        logger.exception("insights job failed")
    try:
        await after_exit.fill()
    except Exception:  # noqa: BLE001
        logger.exception("after-exit job failed")


async def run_scheduled_cycle(
    runner: CycleRunner,
    clock: Clock,
    cycle_time: str,
    backtests: Any = None,
    ops: Any = None,
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
        if ops is not None:
            await ping_heartbeat(ops)


async def ensure_broker_guard(broker: Any, clock: Clock, session_factory: Any) -> bool:
    """Set and verify the broker-side safety guardrails (docs/SPEC.md,
    Protective stops): `no_shorting` and a 1× margin multiplier. A failed
    or unverifiable check raises a warning banner — never a refused
    startup; cash-only sizing and the sell cap still protect. A confirm
    clears the banner left by an earlier unconfirmed check."""
    from app.data.cache import _naive_utc
    from app.db.models import Event
    from app.engine.state import record_event

    details: dict[str, object] = {}
    try:
        written = await broker.set_account_config(
            no_shorting=True, max_margin_multiplier="1"
        )
        if written.error is not None or written.data is None:
            details = {"error": f"set failed: {written.error}"}
        else:
            read = await broker.get_account_config()
            cfg = read.data or {}
            ok = (
                read.error is None
                and cfg.get("no_shorting") is True
                and str(cfg.get("max_margin_multiplier")) in ("1", "1.0")
            )
            if not ok:
                details = {"read_back": cfg, "error": read.error}
    except Exception as exc:  # noqa: BLE001
        details = {"error": str(exc)}
    if details:
        with session_factory() as session:
            record_event(
                session,
                clock,
                level="warning",
                event_type="broker_guard_unconfirmed",
                details=details,
            )
            session.commit()
            logger.warning("broker guard unconfirmed: %s", details)
        return False
    with session_factory() as session:
        stale = session.execute(
            select(Event).where(
                Event.event_type == "broker_guard_unconfirmed",
                Event.acknowledged_at.is_(None),
            )
        ).scalars().all()
        for event in stale:
            event.acknowledged_at = _naive_utc(clock.now())
        session.commit()
    return True


async def _catch_up_missed_screen(service: Any, event_id: int, session_factory: Any) -> None:
    from zoneinfo import ZoneInfo

    from app.data.cache import _naive_utc
    from app.db.models import Event

    await service.run_screen()
    with session_factory() as session:
        event = session.get(Event, event_id)
        if event is not None:
            event.acknowledged_at = _naive_utc(
                datetime.now(ZoneInfo("UTC"))
            )
            session.commit()


async def run_missed_screen_at_startup(
    service: Any, clock: Clock, calendar: Any, session_factory: Any, bus: Any
) -> None:
    """The 08:10 screen missed while the app was down runs now (docs/
    SPEC.md, Daily screen): a banner shows the stale list's date until
    the screen finishes; the first cycles use the previous list."""
    from sqlalchemy import func, select

    from app.db.models import UniverseScreenRow
    from app.engine.state import record_event

    now = clock.now()
    today = now.astimezone(NY_TZ).date()
    if calendar.session_for(today) is None:
        return
    screen_hour, screen_minute = (
        int(p) for p in service.screen_time_ny().split(":")[:2]
    )
    due = now.astimezone(NY_TZ).replace(
        hour=screen_hour, minute=screen_minute, second=0, microsecond=0
    )
    if now.astimezone(NY_TZ) < due:
        return
    with session_factory() as session:
        latest = session.execute(
            select(func.max(UniverseScreenRow.session_date))
        ).scalar_one()
        if latest == today:
            return
        event = record_event(
            session,
            clock,
            level="warning",
            event_type="universe_screen_pending",
            details={
                "session_date": today.isoformat(),
                "list_date": latest.isoformat() if latest else None,
            },
        )
        session.commit()
        event_id = event.id
    logger.warning(
        "missed universe screen for %s — running at startup; first "
        "cycles use the %s list",
        today,
        latest or "no",
    )
    from app.spawn import spawn

    spawn(
        _catch_up_missed_screen(service, event_id, session_factory),
        "missed-universe-screen",
    )


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
    ops: Any = None,
) -> None:
    """Scheduler entry point for the 15-minute position check."""
    if backtests is not None:
        backtests.set_paused(True)
    try:
        await checker.run()
    finally:
        if backtests is not None:
            backtests.set_paused(False)
        if ops is not None:
            await ping_heartbeat(ops)
    if bus is not None:
        bus.publish("positions", "orders", "banners", "header", "performance")


async def ping_heartbeat(ops: Any) -> None:
    """The dead-man's-switch ping after a cycle or position check."""
    try:
        await ops.ping_heartbeat()
    except Exception:  # noqa: BLE001
        logger.exception("heartbeat ping failed")


async def run_server_checks(ops: Any) -> None:
    """Hourly: disk, clock, certificate, backup freshness (SPEC → VPS
    deployment → Monitoring)."""
    try:
        ops.check_disk()
        await ops.check_clock()
        ops.check_cert()
        ops.check_backups()
    except Exception:  # noqa: BLE001
        logger.exception("server checks failed")


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
    from app.spawn import spawn

    task = spawn(coro, name)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def check_model_prices_at_startup(config: AppConfig) -> None:
    """Refuse to start when the live, reviews or an active shadow's model
    has no entry in model_prices (docs/SPEC.md, Strategies and shadows)."""
    from app.db.base import get_session_maker

    with get_session_maker()() as session:
        try:
            check_model_prices(config, session)
        except UnpricedModelError as exc:
            raise RuntimeError(f"Refusing to start: {exc}") from exc


def check_prompt_versions_at_startup(config: AppConfig) -> None:
    """Refuse to start when config or an active strategy names a prompt
    version without a registered contract (docs/SPEC.md, Known prompt
    versions only)."""
    from app.db.base import get_session_maker
    from app.db.models import Strategy
    from app.decide.prompt import UnknownPromptVersion, prompt_spec
    from app.reviews.service import ReviewService

    versions = [str(config.get("decision", "prompt", default="decision.v1"))]
    for kind, known in ReviewService._REVIEW_CONTRACTS.items():
        version = str(
            config.get("reviews", "prompts", kind, default=f"{kind}.v1")
        )
        if version not in known:
            raise RuntimeError(
                f"Refusing to start: unknown_prompt_version {version} "
                f"for reviews.prompts.{kind}"
            )
    with get_session_maker()() as session:
        versions.extend(
            s
            for s in session.execute(
                select(Strategy.prompt_version).where(
                    Strategy.status == "active", Strategy.prompt_version.is_not(None)
                )
            ).scalars()
            if s is not None
        )
    try:
        for version in versions:
            prompt_spec(version)
    except UnknownPromptVersion as exc:
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
    ops: Any = None,
) -> None:
    _ops = ops
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
            args=[runner, clock, str(cycle_at), backtests, _ops],
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
    # umask 077 before anything creates a file — app.serve sets it too,
    # but the `make dev`/`make run` uvicorn paths come straight here.
    from app.filemodes import apply_umask

    apply_umask()
    # /api answers 503 starting_up until the startup reconciliation
    # finishes (docs/SPEC.md → Error pages and API errors).
    app.state.ready = False
    pipeline = setup_logging()
    from app.spawn import install_global_hooks

    install_global_hooks(asyncio.get_running_loop())
    config = get_config()

    from app.auth.guard import check_auth_guard
    from app.shutdown import (
        make_shutdown,
        quick_check,
        set_current,
    )
    from app.spawn import spawn

    # Hard rule 10: sign-in off on a non-loopback address refuses to
    # start — checked before the run lock, database or any client.
    check_auth_guard(config)

    shutdown_manager, run_lock = make_shutdown(config)
    app.state.shutdown = shutdown_manager
    # The run lock: a live pid refuses startup, a stale lock marks the
    # last run unclean (quick_check + the banner, docs/SPEC.md, Clean
    # shutdown).
    lock_state = run_lock.check()
    if lock_state.status == "stale":
        integrity = quick_check(Path(config.env.sa_db_path))
        logger.warning(
            "previous run did not shut down cleanly (quick_check: %s)",
            integrity,
            extra={"event": "app.unclean_previous_run"},
        )
        app.state.unclean_previous_run = True
        app.state.unclean_quick_check = integrity
        app.state.unclean_stopped_at = (
            datetime.fromtimestamp(lock_state.last_seen, UTC)
            if lock_state.last_seen
            else None
        )
    run_lock.acquire(
        int(config.get("logging", "heartbeat_seconds", default=30))
    )
    set_current(shutdown_manager)
    app.state.run_lock = run_lock

    Path(config.env.sa_db_path).parent.mkdir(parents=True, exist_ok=True)
    # Files on disk (docs/SPEC.md, Security hardening): SA_DATA_DIR 0700,
    # its files 0600; wrong modes are corrected with one INFO line.
    from app.filemodes import enforce_file_modes

    corrected_modes = enforce_file_modes(Path(config.env.sa_data_dir))
    if corrected_modes:
        logger.info(
            "corrected file modes on %d path(s) under %s",
            corrected_modes,
            config.env.sa_data_dir,
        )
    run_migrations()
    # alembic's fileConfig closes every handler (env.py can't skip it —
    # CLI runs need it): re-attach the queue to the root logger.
    from app.logging_config import install_queue_root

    install_queue_root(pipeline)
    # Migrations are immutable; a schema that drifted outside them fails
    # here with the differing table/column instead of a raw SQL error.
    check_schema_matches_models()
    # Versioned GUI settings sit on top of the YAML before anything reads
    # them; the execution-mode check sees the effective mode.
    load_saved_settings(config)
    check_execution_mode(config)
    check_model_prices_at_startup(config)
    check_prompt_versions_at_startup(config)

    clock = make_clock(config)
    bus = UpdateBus()

    # Log index: the listener thread batches rows into logs.db; warning
    # and up also pings the "logs" topic (debounced 1s, spec).
    from app.log_index import make_index_handler

    loop = asyncio.get_running_loop()
    _last_logs_push = [0.0]

    def _push_logs_topic(_record: object) -> None:
        now = time.monotonic()
        if now - _last_logs_push[0] >= 1:
            _last_logs_push[0] = now
            loop.call_soon_threadsafe(bus.publish, "logs")

    # "New error" (docs/SPEC.md, Notifications): the first time an ERROR
    # fingerprint appears this session it sends the Warning notification
    # once — a repeating bug stays visible after the first one.
    seen_error_fingerprints: set[str] = set()

    def _on_warning_or_above(record: logging.LogRecord) -> None:
        _push_logs_topic(record)
        if record.levelno < logging.ERROR:
            return
        from app.log_index import fingerprint

        fp = fingerprint(
            record.name, getattr(record, "event", None), record.getMessage()
        )
        if fp in seen_error_fingerprints:
            return
        seen_error_fingerprints.add(fp)
        notifier = getattr(app.state, "notification_service", None)
        if notifier is None:
            return
        from app.logging_config import _all_secrets
        from app.secrets_service import scrub

        notifier.notify(
            "new_error",
            scrub(record.getMessage()[:300], _all_secrets()),
            link="/logs",
        )

    index_handler = make_index_handler(
        config,
        file_fallback=pipeline.file_handler,
        on_warning_or_above=_on_warning_or_above,
    )
    if index_handler is not None:
        pipeline.add_handler(index_handler)
    app.state.log_index_handler = index_handler
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
    app.state.notification_service = notification_service
    set_event_sink(notification_service.sink)
    key_testers = make_key_testers(config, clock)
    await secrets_store.import_env_keys(read_env_keys(), key_testers)
    install_secret_scrub_filter(secrets_store.scrub_values)

    # Sign-in (PR 23): the service exists in both modes; with SA_AUTH=off
    # it is dormant and the middleware is a passthrough.
    from app.auth.service import AuthService
    from app.logging_config import register_secret_provider

    auth_service = AuthService(
        config, clock, notifier=notification_service
    )
    app.state.auth = auth_service
    register_secret_provider(auth_service.scrub_values)
    if auth_service.enabled:
        # Fail closed: no route outside the session guard may exist.
        from app.auth.guard import check_route_coverage

        check_route_coverage(app)

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
    after_exit_worker = AfterExitWorker(
        market_data=market_data,
        sources=sources,
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
    if config.env.mode != "simulated":
        # A screen missed while the app was down runs before the first
        # cycle; a stale-list banner stays until it lands. (The
        # simulated demo screens on demand via POST /api/universe/screen.)
        await run_missed_screen_at_startup(
            universe_service, clock, calendar_store, get_session_maker(), bus
        )

    # Trade updates → ledger; then reconcile once so the database starts
    # as a mirror of the broker (adopted positions get their stop here).
    broker.subscribe_trade_updates(executor.on_trade_update)
    broker.subscribe_stream_failures(executor.on_stream_failure)
    await broker.start_stream()
    if config.env.mode != "simulated":
        # Broker-side guardrails (no shorts, 1× margin) — set and
        # verified; an unconfirmable value raises a warning banner,
        # never a refusal. The simulated broker has no such config.
        exec_state.broker_guard_confirmed = await ensure_broker_guard(
            broker, clock, get_session_maker()
        )
    report = await executor.reconcile()
    # /api answers starting_up until the startup reconciliation finishes
    # (docs/SPEC.md → Error pages and API errors).
    app.state.ready = True
    if getattr(app.state, "unclean_previous_run", False):
        # The unclean-run banner reports this pass (design: States).
        app.state.unclean_reconcile = report
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
        if getattr(app.state, "unclean_previous_run", False):
            app.state.unclean_interrupted = recovered
        # Was trading running before this start? The last pause event
        # decides (on=False = entries were live) — needed by the
        # resume-after-clean-restart check below.
        last_pause = session.execute(
            select(Event)
            .where(Event.event_type == "pause")
            .order_by(Event.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        was_running = bool(
            last_pause is not None
            and (last_pause.details or {}).get("on") is False
        )
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

    # Resume after a clean planned restart (docs/SPEC.md → VPS deployment
    # → Restarts). Only where sign-in is required (the VPS): the local
    # app always starts paused. A previous run existed when the events
    # log holds a pause row from before this boot.
    from app.restart import decide_restart, resume_after_clean_restart_enabled

    had_previous_run = last_pause is not None
    if resume_after_clean_restart_enabled(config) and had_previous_run:
        decision = decide_restart(
            was_running=was_running,
            clean_shutdown=not getattr(
                app.state, "unclean_previous_run", False
            ),
            kill_switch_on=exec_state.kill_switch_on,
            reconcile=report,
            broker_guard_confirmed=exec_state.broker_guard_confirmed,
        )
        with get_session_maker()() as session:
            if decision.resume:
                exec_state.paused = False
                exec_state.paused_at_startup = False
                exec_state.paused_since = None
                record_event(
                    session,
                    clock,
                    level="info",
                    event_type="restart_resumed",
                    details={},
                )
            else:
                record_event(
                    session,
                    clock,
                    level="warning",
                    event_type="restart_paused",
                    details={"reasons": decision.reasons},
                )
            session.commit()
        app.state.restart_resumed = decision.resume
        bus.publish("banners", "header")

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

    # PR 24 ops: heartbeat pings after cycles/checks, hourly server
    # checks, offsite backups + the monthly restore test.
    from app.ops.service import OpsService

    ops_service = OpsService(
        config,
        clock,
        notifier=notification_service,
        secrets=secrets_store,
        data_client=sources.alpaca,
    )
    app.state.ops = ops_service
    # Persist the startup reconciliation so deploy.sh reads it back
    # read-only (docker compose exec app python -m app.ops.reconcile_check).
    if report is not None:
        ops_service.record_startup_reconcile(
            bool(report.ok and not report.mismatches), len(report.mismatches)
        )

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
        args=[clock, market_data, ops_service],
        id="daily-db-backup",
    )
    # Run the decision cycle at every configured cycle time; the runner
    # refreshes the stored signal snapshot as part of the cycle. Missed
    # cycles may run late by at most misfire_grace_seconds, and several
    # misses coalesce into one run — never into a queue of catch-ups.
    schedule_cycle_jobs(
        scheduler, cycle_runner, config, clock, backtest_manager, ops_service
    )
    # Server checks hourly (clock offset needs it); heartbeat is pinged
    # by the cycle/check wrappers themselves.
    scheduler.add_job(
        run_server_checks,
        IntervalTrigger(hours=1),
        args=[ops_service],
        id="server-checks",
        coalesce=True,
    )
    # The offsite upload is chained to the daily local copy above — not a
    # separate cron (a failed local copy skips it). The monthly restore
    # test keeps its own schedule.
    restore_day = int(
        config.get("ops", "offsite_backup", "restore_test_day", default=1)
    )
    scheduler.add_job(
        ops_service.run_restore_test,
        CronTrigger(day=restore_day, hour=4, minute=10, timezone=app_tz),
        id="restore-test",
        coalesce=True,
    )
    misfire_grace = int(
        config.get("schedule", "misfire_grace_seconds", default=120)
    )
    for check_at in config.get("schedule", "position_checks", default=[]) or []:
        hour, minute = (int(p) for p in str(check_at).split(":")[:2])
        scheduler.add_job(
            run_scheduled_check,
            CronTrigger(hour=hour, minute=minute, timezone=NY_TZ),
            args=[position_checker, bus, backtest_manager, ops_service],
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
    # and store the snapshot; backfills sessions missed while down. The
    # after-exit job for the tails card runs right after it (docs/SPEC.md,
    # Tail metrics).
    scheduler.add_job(
        run_after_close_jobs,
        IntervalTrigger(minutes=1),
        args=[
            config,
            calendar_store,
            clock,
            notification_service,
            lambda topics: bus.publish(*topics),
            after_exit_worker,
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
    # Scheduler error listener: a failed job is logged with its id at
    # ERROR through the same pipeline — never just APScheduler's stderr.
    from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent

    def _on_job_error(event: JobExecutionEvent[object]) -> None:
        logger.error(
            "scheduler job %s failed: %s",
            event.job_id,
            event.exception,
            exc_info=event.exception,
        )

    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
    scheduler.start()
    app.state.scheduler = scheduler
    from app.logging_config import ev as _ev

    _ev(
        logger, logging.INFO, "app.started",
        f"stock agent started ({config.env.mode})",
        mode=config.env.mode,
    )

    # run.lock heartbeat: the file carries pid + heartbeat timestamps so
    # the next startup can tell "still running" from "died uncleanly".
    heartbeat_seconds = int(
        config.get("logging", "heartbeat_seconds", default=30)
    )

    async def _heartbeat() -> None:
        while not shutdown_manager.shutting_down.is_set():
            await asyncio.sleep(heartbeat_seconds)
            try:
                run_lock.heartbeat()
            except OSError:
                logger.exception("run.lock heartbeat failed")

    heartbeat_task = spawn(_heartbeat(), "run-lock-heartbeat")

    try:
        yield
    finally:
        # Clean shutdown (docs/SPEC.md). A second Ctrl+C/SIGTERM during
        # this block skips the grace wait — cleanup still runs.
        import signal as _signal

        loop = asyncio.get_running_loop()

        def _force(*_args: object) -> None:
            loop.call_soon_threadsafe(shutdown_manager.skip_wait.set)

        for _sig in (_signal.SIGINT, _signal.SIGTERM):
            # ValueError/OSError off the main thread (tests) — the test
            # calls force() directly instead of signalling.
            with contextlib.suppress(ValueError, OSError):
                _signal.signal(_sig, _force)
        shutdown_manager.begin()
        logger.info("shutdown requested", extra={"event": "app.shutdown_requested"})
        bus.publish("shutdown", "banners", "header")
        scheduler.pause()
        heartbeat_task.cancel()
        await backtest_manager.shutdown()
        scheduler.shutdown(wait=False)
        # Shadow work is never waited for: it is cut, never live.
        for task in list(shadow_tasks):
            task.cancel()
        set_event_sink(None)
        try:
            await asyncio.wait_for(notification_service.stop(), timeout=5)
        except TimeoutError:
            logger.warning("notification drain exceeded 5s on shutdown")
        # Normal shutdown: give a running cycle or check the grace window
        # to finish; leftovers become "interrupted" and never count as
        # failures — only rows still "running" at the next startup (a real
        # crash) do (docs/SPEC.md, Risk management, Errors).
        grace = float(
            config.get("storage", "shutdown_grace_seconds", default=60)
        )

        def _work_done() -> bool:
            with get_session_maker()() as session:
                running = session.execute(
                    select(func.count()).select_from(Cycle).where(
                        Cycle.status == "running"
                    )
                ).scalar_one()
            return running == 0

        await shutdown_manager.wait_for_work(_work_done)
        interrupted = await finalize_interrupted_cycles(
            get_session_maker(), clock, grace
        )
        if interrupted:
            logger.warning(
                "Shutdown with %d cycle(s) still running; marked interrupted",
                interrupted,
            )
        await broker.stop_stream()
        # Clients and sessions are closed above; fold the WAL back and
        # dispose the engine, then release the lock, and close the log
        # pipeline last (docs/SPEC.md, Clean shutdown).
        from app.db.base import shutdown_engine

        shutdown_engine()
        run_lock.release()
        set_current(None)
        from app.logging_config import shutdown_logging

        _ev(
            logger, logging.INFO, "app.shutdown_complete",
            "shutdown complete",
        )
        shutdown_logging()


def create_app() -> FastAPI:
    # With SA_AUTH=required the only open endpoints are /healthz and the
    # sign-in calls — the API docs are switched off entirely so no doc
    # route ships reachable without a session.
    docs_off = get_config().env.sa_auth == "required"
    app = FastAPI(
        title="Stock Trading Agent",
        version="0.2.0",
        lifespan=lifespan,
        openapi_url=None if docs_off else "/openapi.json",
        docs_url=None if docs_off else "/docs",
        redoc_url=None if docs_off else "/redoc",
    )
    # Apps that never run the lifespan (raw TestClient use) answer as
    # ready; the lifespan clears this while startup work runs.
    app.state.ready = True

    import secrets as _secrets_mod

    from fastapi import Request
    from fastapi.responses import JSONResponse

    from app.logging_config import log_context

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Any:
        """One request id per call: X-Request-Id back, ``request`` context
        on every log line written while it runs."""
        from app.errors import ErrorCode, error_body

        rid = f"r-{_secrets_mod.token_hex(4)}"
        manager = getattr(request.app.state, "shutdown", None)
        if (
            manager is not None
            and manager.shutting_down.is_set()
            and request.method not in ("GET", "HEAD", "OPTIONS")
        ):
            # Writes refuse while the graceful shutdown runs; reads keep
            # working (docs/SPEC.md, Graceful shutdown).
            return JSONResponse(
                status_code=503,
                content=error_body(ErrorCode.SHUTTING_DOWN),
                headers={"X-Request-Id": rid},
            )
        if (
            request.url.path.startswith("/api/")
            and not getattr(request.app.state, "ready", False)
        ):
            # Before the startup reconciliation finishes every /api
            # answer is 503 starting_up; /healthz stays open (spec).
            return JSONResponse(
                status_code=503,
                content=error_body(ErrorCode.STARTING_UP),
                headers={"X-Request-Id": rid},
            )
        import time as _time

        started = _time.monotonic()
        with log_context(request=rid):
            response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        logging.getLogger("api").debug(
            "%s %s -> %s in %dms",
            request.method,
            request.url.path,
            response.status_code,
            int((_time.monotonic() - started) * 1000),
            extra={"event": "api.request"},
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    from app.auth.middleware import AuthMiddleware

    app.add_middleware(AuthMiddleware)
    from app.api.gate import RequestGateMiddleware, SecurityHeadersMiddleware

    # The request gate is the outermost user middleware: it runs in
    # front of sign-in, so it also guards SA_AUTH=off (hard rule 18).
    # The app's own security headers wrap it, so gate refusals carry
    # them too (Caddy's ?-defaults defer to these on the VPS).
    app.add_middleware(RequestGateMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """The one open probe: Caddy's health check and the uptime monitor
        (docs/SPEC.md, VPS deployment). Version only — nothing else."""
        return {"version": app.version}

    from app.errors import install_exception_handlers

    install_exception_handlers(app)

    from app.auth.api import router as auth_router

    app.include_router(auth_router, prefix="/api")
    app.include_router(api_router, prefix="/api")
    app.include_router(strategies_router, prefix="/api")
    app.include_router(backtests_router, prefix="/api")
    app.include_router(inspector_router, prefix="/api")
    app.include_router(insights_router, prefix="/api")
    app.include_router(ws_router, prefix="/ws")
    if FRONTEND_DIST.is_dir():
        app.mount("/", SpaStaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
    return app


class SpaStaticFiles(StaticFiles):
    """Serve the built Vite app; unknown page paths fall back to index.html.

    The fallback only answers paths without a file extension — a missing
    file (like a hashed chunk a stale tab still asks for after a rebuild)
    gets a real 404, not HTML a script loader cannot use. index.html goes
    out no-cache so a rebuilt GUI is picked up on the next navigation;
    hashed assets under /assets/ are immutable.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        # Unknown /api paths get the JSON error shape, never the SPA
        # fallback (docs/SPEC.md → Error pages and API errors).
        if path == "api" or path.startswith("api/"):
            raise StarletteHTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or PurePosixPath(path).suffix:
                raise
            response = await super().get_response("index.html", scope)
        if not PurePosixPath(path).suffix or path == "index.html":
            response.headers["Cache-Control"] = "no-cache"
        elif path.startswith("assets/"):
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
            )
        return response


app = create_app()
