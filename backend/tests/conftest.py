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


@pytest.fixture(scope="session")
def template_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A fully warmed database, built once per xdist worker.

    Seeded with the same fetches a real simulated boot runs — migrated
    schema, calendar, earnings, corporate actions, FX, daily bars and
    60 sessions of 5-minute bars for the default universe — but without
    booting the app, so no cycles, decisions, events or runs are written.
    Every test gets a copy: refresh_history sees full coverage and costs
    one small fetch instead of regenerating ~47k fake bars, and
    refresh_daily hits its own coverage check.
    """
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

    path = tmp_path_factory.mktemp("template") / "template.db"
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
    return path


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
