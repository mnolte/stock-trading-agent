import asyncio
import os
import shutil
import tempfile
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ.setdefault(
    "SA_DB_PATH", str(Path(tempfile.gettempdir()) / "stock-agent-test.db")
)
# Tests always run against the fakes, whatever .env says — sockets are blocked.
os.environ["MODE"] = "simulated"
# The real .env never feeds a test: SA_ENV_FILE points at nothing and the
# key variables are scrubbed from the environment before config loads.
os.environ["SA_ENV_FILE"] = str(Path(tempfile.gettempdir()) / "no-such-env-file")
for _var in (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "OPENAI_API_KEY",
    "FINNHUB_API_KEY",
):
    os.environ.pop(_var, None)

import app.config as config_module  # noqa: E402
from app.api.main import create_app  # noqa: E402
from app.db.base import Base, _engines  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

# The simulated boot clock is frozen at this instant (make_clock's
# SA_SIM_NOW fallback); the template is seeded for the same day so the
# coverage windows a test boot computes match a real boot exactly.
TEMPLATE_NOW = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)


def _build_template(path: Path) -> None:
    """Seed one warm template database at ``path`` (see ``template_db``)."""
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from app.api.main import refresh_calendar_and_daily_data
    from app.config import AppConfig
    from app.data.factory import DataSources
    from app.data.fake import (
        FakeBarSource,
        FakeCalendarSource,
        FakeCorporateActionSource,
        FakeEarningsSource,
        FakeFXSource,
        FakeNewsSource,
        FakeQuoteSource,
    )
    from app.data.service import MarketDataService
    from app.db.base import get_engine
    from app.market.calendar import CalendarStore
    from app.market.clock import NY_TZ, FakeClock
    from sqlalchemy import text

    previous = os.environ.get("SA_DB_PATH")
    os.environ["SA_DB_PATH"] = str(path)
    config_module._config = None
    try:
        alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
        command.upgrade(AlembicConfig(str(alembic_ini)), "head")

        config = AppConfig()
        clock = FakeClock(TEMPLATE_NOW)
        today = clock.now().astimezone(NY_TZ).date()
        calendar_source = FakeCalendarSource()
        sources = DataSources(
            bars=FakeBarSource(calendar_source),
            quotes=FakeQuoteSource(),
            news=FakeNewsSource(),
            earnings=FakeEarningsSource(),
            fx=FakeFXSource(),
            corporate_actions=FakeCorporateActionSource(),
            calendar=calendar_source,
            alpaca=None,
        )
        calendar_store = CalendarStore(calendar_source)
        market_data = MarketDataService(sources, calendar_store, config)

        async def seed() -> None:
            await refresh_calendar_and_daily_data(
                calendar_store, market_data, sources, config, clock
            )
            await market_data.refresh_history(today)

        asyncio.run(seed())

        # WAL sidecars are not part of the template: checkpoint everything
        # into the main file so a plain copy carries the whole database.
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        _engines.pop(f"sqlite:///{path}", None)
        engine.dispose()
    finally:
        if previous is None:
            os.environ.pop("SA_DB_PATH", None)
        else:
            os.environ["SA_DB_PATH"] = previous
        config_module._config = None


def _template_fingerprint() -> str:
    """Everything that determines the template's content: schema, fake
    data, refresh logic, config defaults, the universe and the frozen
    clock. A change to any of them rebuilds the shared database."""
    import hashlib

    backend = Path(__file__).resolve().parents[1]
    files = (
        sorted(backend.glob("alembic/versions/*.py"))
        + [backend / "universe" / "pool.csv"]
        + [
            backend / "app" / part
            for part in (
                "api/main.py",
                "config.py",
                "data/factory.py",
                "data/fake.py",
                "data/service.py",
                "data/models.py",
                "db/models.py",
                "market/calendar.py",
                "market/clock.py",
            )
            if (backend / "app" / part).exists()
        ]
        + [Path(__file__), backend / "config.example.yaml"]
    )
    digest = hashlib.sha256(str(TEMPLATE_NOW).encode())
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _ensure_template() -> Path:
    """Build the shared warm template once, across processes and runs.

    Whoever takes the flock builds to a temp file and renames it into
    place; everyone else blocks on the same lock and reuses the result.
    The file lives under ``$TMPDIR`` keyed by ``_template_fingerprint``,
    so unchanged inputs skip the ~15 s fake-data seed entirely.
    """
    try:
        import fcntl
    except ImportError:  # Windows: lock is best-effort only
        fcntl = None  # type: ignore[assignment]
    import tempfile
    import time

    cache_dir = Path(tempfile.gettempdir()) / "stock-agent-test-template"
    cache_dir.mkdir(parents=True, exist_ok=True)
    shared = cache_dir / f"template-{_template_fingerprint()}.db"
    with open(cache_dir / "template.lock", "a+b") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if not shared.exists():
                tmp = cache_dir / f"template-{os.getpid()}.tmp"
                _build_template(tmp)
                os.replace(tmp, shared)
            # Fingerprinted templates accumulate a few MB per change;
            # keep the cache small.
            stale = time.time() - 24 * 3600
            for old in cache_dir.glob("template-*.db"):
                if old != shared and old.stat().st_mtime < stale:
                    old.unlink(missing_ok=True)
        finally:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)
    return shared


def pytest_sessionstart(session: pytest.Session) -> None:
    """Pre-build the warm template on the xdist controller so workers
    don't each repeat the fake-data seed in parallel."""
    if hasattr(session.config, "workerinput"):
        return
    _ensure_template()


@pytest.fixture(scope="session")
def template_db() -> Path:
    """A fully warmed database, shared by every test and worker.

    Seeded with the same fetches a real simulated boot runs — migrated
    schema, calendar, earnings, corporate actions, FX, daily bars and
    60 sessions of 5-minute bars for the default universe — but without
    booting the app, so no cycles, decisions, events or runs are written.
    Every test gets a copy: refresh_history sees full coverage and costs
    one small fetch instead of regenerating ~47k fake bars, and
    refresh_daily hits its own coverage check.
    """
    return _ensure_template()


@pytest.fixture(autouse=True)
def _isolated_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template_db: Path
) -> Generator[None, None, None]:
    """Every test gets its own database file so nothing can leak into
    another test or into the next app boot. The file is a copy of the
    worker's warm template — a full app boot then costs seconds instead
    of regenerating the fake market data. ``get_config()`` is reset so
    it picks the path up."""
    db_path = tmp_path / "stock-agent-test.db"
    monkeypatch.setenv("SA_DB_PATH", str(db_path))
    # secret.key and its mode-600 check need an isolated data dir per test.
    monkeypatch.setenv("SA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config_module, "_config", None)
    shutil.copyfile(template_db, db_path)
    yield
    # Engines are pooled per URL: release this test's so a worker doesn't
    # accumulate open SQLite files across hundreds of tests.
    engine = _engines.pop(f"sqlite:///{db_path}", None)
    if engine is not None:
        engine.dispose()


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
