"""Backtest storage: the shared bar store and the per-run market database.

``data/backtests/bars.db`` keeps ``bars_5m`` and ``bars_1d`` (same schema
as the live cache, split-adjusted, never pruned automatically) so a run
never reads the live cache, which keeps its 120-session limit.

``run-<id>/market.db`` is a small run-scoped database — earnings, news,
corporate actions and the trading calendar — written during the download
phase and read-only while the run replays (docs/SPEC.md, Backtesting,
Engine and Data).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import BarCache
from app.db.base import configure_sqlite
from app.db.models import Bar1d, Bar5m, CorporateAction, Earning

# market.db tables that share the live schema get their columns from the
# live models; news and the trading calendar have no live table.
_MARKET_META = MetaData()

MARKET_NEWS = Table(
    "market_news",
    _MARKET_META,
    Column("id", Integer, primary_key=True),
    Column("news_id", String(64), nullable=False),
    Column("symbol", String(16), nullable=False, index=True),
    Column("headline", String(512), nullable=False),
    Column("source", String(64), nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("news_id", "symbol", name="uq_market_news"),
)

MARKET_CALENDAR = Table(
    "market_calendar",
    _MARKET_META,
    Column("id", Integer, primary_key=True),
    Column("date", Date, nullable=False, unique=True),
    Column("open", String(32), nullable=False),  # ISO, America/New_York
    Column("close", String(32), nullable=False),
)

# Per-symbol coverage for the run window: "ok" or "missing" (the download
# failed — those days block entries for the symbol, docs/SPEC.md, Data).
MARKET_COVERAGE = Table(
    "market_coverage",
    _MARKET_META,
    Column("id", Integer, primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    UniqueConstraint("kind", "symbol", name="uq_market_coverage"),
)

# Extra run metadata the estimate and results read (sessions count etc.).
MARKET_META_ROWS = Table(
    "market_meta",
    _MARKET_META,
    Column("key", String(64), primary_key=True),
    Column("value", JSON, nullable=False),
)


def engine_for(path: Path, config: AppConfig) -> Any:
    """A WAL/busy-timeout engine for one backtest database file."""
    timeout = float(
        config.get("storage", "sqlite_busy_timeout_seconds", default=30)
    )
    return configure_sqlite(
        create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": timeout},
        ),
        timeout,
    )


def session_maker_for(path: Path, config: AppConfig) -> sessionmaker[Session]:
    return sessionmaker(bind=engine_for(path, config))


def read_session_maker_for(path: Path, config: AppConfig) -> sessionmaker[Session]:
    """A read-only session factory for run databases opened only for
    reading (docs/SPEC.md → Files on disk) — mode=ro, no WAL pragma."""
    timeout = float(
        config.get("storage", "sqlite_busy_timeout_seconds", default=30)
    )
    engine = create_engine(
        f"sqlite:///file:{path}?mode=ro&uri=true",
        connect_args={"check_same_thread": False, "timeout": timeout},
    )
    return sessionmaker(bind=engine)


def make_bar_store(path: Path, config: AppConfig) -> BarCache:
    """The shared bar store: the live ``bars_5m``/``bars_1d`` schema on its
    own file, so reads reuse ``BarCache`` unchanged."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = engine_for(path, config)
    cast(Table, Bar5m.__table__).create(engine, checkfirst=True)
    cast(Table, Bar1d.__table__).create(engine, checkfirst=True)
    return BarCache(session_factory=sessionmaker(bind=engine))


def make_market_db(path: Path, config: AppConfig) -> sessionmaker[Session]:
    """Create (or open) the run's read-only market database."""
    engine = engine_for(path, config)
    cast(Table, Earning.__table__).create(engine, checkfirst=True)
    cast(Table, CorporateAction.__table__).create(engine, checkfirst=True)
    _MARKET_META.create_all(engine)
    return sessionmaker(bind=engine)


def _resolve(config: AppConfig, value: str) -> Path:
    """Relative backtest paths live under the data dir (isolated in tests).

    A leading component equal to the data dir's own name is dropped, so a
    config value written as ``data/backtests`` means ``<data>/backtests``,
    not ``<data>/data/backtests``.
    """
    path = Path(value)
    if not path.is_absolute():
        data_dir = Path(config.env.sa_data_dir)
        if path.parts and path.parts[0] == data_dir.name:
            path = Path(*path.parts[1:])
        path = data_dir / path
    return path


def run_dir_for(config: AppConfig, run_id: int) -> Path:
    base = _resolve(
        config, str(config.get("backtest", "data_dir", default="backtests"))
    )
    return base / f"run-{run_id}"


def bar_store_path(config: AppConfig) -> Path:
    return _resolve(
        config,
        str(
            config.get("backtest", "bar_store", default="backtests/bars.db")
        ),
    )


def dir_size(path: Path) -> int:
    """Size on disk of a run folder (all files), for the runs list."""
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def store_info(config: AppConfig) -> dict[str, Any]:
    """The "Bar store" row on the Runs card: size and covered date range."""
    path = bar_store_path(config)
    info: dict[str, Any] = {"size_bytes": 0, "earliest": None, "latest": None}
    if not path.exists():
        return info
    info["size_bytes"] = path.stat().st_size
    sessions = session_maker_for(path, config)
    from sqlalchemy import func, select

    with sessions() as session:
        earliest, latest = session.execute(
            select(func.min(Bar5m.start), func.max(Bar5m.start))
        ).one()
        dmin, dmax = session.execute(
            select(func.min(Bar1d.date), func.max(Bar1d.date))
        ).one()
    dates = [d for d in (dmin, dmax) if d is not None]
    if earliest is not None:
        dates.append(earliest.date() if isinstance(earliest, datetime) else earliest)
    if latest is not None:
        dates.append(latest.date() if isinstance(latest, datetime) else latest)
    if dates:
        info["earliest"] = min(dates).isoformat()
        info["latest"] = max(dates).isoformat()
    return info


def clear_bar_store(config: AppConfig) -> None:
    """Drop every stored bar; only allowed while no run is active."""
    path = bar_store_path(config)
    if not path.exists():
        return
    sessions = session_maker_for(path, config)
    from sqlalchemy import delete

    with sessions() as session:
        session.execute(delete(Bar5m))
        session.execute(delete(Bar1d))
        session.commit()
