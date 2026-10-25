import logging
from typing import Any

from sqlalchemy import Engine, Numeric, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_config


class Base(DeclarativeBase):
    pass


Money = Numeric(19, 8)


def get_db_url() -> str:
    cfg = get_config()
    path = cfg.env.sa_db_path
    return f"sqlite:///{path}"


# One engine (one connection pool) per database URL — creating a fresh
# engine per session maker leaked pools that were never disposed.
_engines: dict[str, Engine] = {}


def configure_sqlite(engine: Engine, busy_timeout_seconds: float) -> Engine:
    """WAL journal and a busy timeout on every connection.

    Shadow strategies, API requests and live jobs all write to the one
    SQLite file. WAL lets readers run beside a writer, and the busy
    timeout makes a writer wait for the lock instead of failing — a live
    write must never fail because a shadow write is in progress.
    """
    busy_ms = int(busy_timeout_seconds * 1000)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={busy_ms}")
        # Read side: map the file and keep index pages warm — the
        # inspector's 100k-row summary scans are memory-bound then.
        cursor.execute("PRAGMA mmap_size=268435456")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.close()

    return engine


def get_engine() -> Engine:
    url = get_db_url()
    engine = _engines.get(url)
    if engine is None:
        timeout = float(
            get_config().get("storage", "sqlite_busy_timeout_seconds", default=30)
        )
        engine = configure_sqlite(
            create_engine(
                url, connect_args={"check_same_thread": False, "timeout": timeout}
            ),
            timeout,
        )
        _engines[url] = engine
    return engine


def get_session_maker() -> sessionmaker[Session]:
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def shutdown_engine() -> None:
    """Clean shutdown tail: WAL checkpoint (TRUNCATE) then dispose.

    Called last before the log pipeline closes; every session is closed
    by then, so the checkpoint folds the -wal back into the main file.
    """
    for url, engine in list(_engines.items()):
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # noqa: BLE001 - checkpoint failure must not stop disposal
            logging.getLogger("db").exception(
                "wal_checkpoint(TRUNCATE) failed for %s", url
            )
        engine.dispose()
        _engines.pop(url, None)


def check_schema_matches_models() -> None:
    """Refuse to start when the database schema doesn't match the models.

    Migrations are immutable once merged (AGENTS.md rule 11): a drifted
    database is moved aside and rebuilt, never patched by hand.
    """
    inspector = inspect(get_engine())
    present = set(inspector.get_table_names())
    problems: list[str] = []
    for name, table in Base.metadata.tables.items():
        if name not in present:
            problems.append(f"missing table {name}")
            continue
        columns = {c["name"] for c in inspector.get_columns(name)}
        expected = {c.name for c in table.columns}
        problems += sorted(f"{name}.{c} missing" for c in expected - columns)
        problems += sorted(f"{name}.{c} unexpected" for c in columns - expected)
    if problems:
        raise RuntimeError(
            "Database schema does not match the models: "
            + "; ".join(problems)
            + ". The database may need rebuilding — move "
            + get_config().env.sa_db_path
            + " aside and restart so migrations recreate it."
        )
