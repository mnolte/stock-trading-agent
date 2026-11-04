"""Startup schema check: refuse to boot on a drifted database."""

import asyncio
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from app.api.main import create_app
from app.config import get_config
from app.db.base import Base, check_schema_matches_models, get_engine
from app.logging_config import shutdown_logging
from app.shutdown import set_current
from uvicorn.lifespan.on import LifespanOn


def test_schema_check_passes_on_a_fresh_database() -> None:
    """A database built by the current models passes the check."""
    Base.metadata.create_all(get_engine())
    check_schema_matches_models()


def test_schema_check_names_differing_table_and_column() -> None:
    """A drifted DB fails with the differing table/column in the error."""
    db_path = Path(get_config().env.sa_db_path)
    with sqlite3.connect(db_path) as conn:
        # The shared template already has every table; drift cycles and
        # drop decisions so both failure kinds show in the error.
        conn.execute("DROP TABLE cycles")
        conn.execute("CREATE TABLE cycles (id INTEGER PRIMARY KEY)")
        conn.execute("DROP TABLE decisions")

    with pytest.raises(RuntimeError) as excinfo:
        check_schema_matches_models()

    message = str(excinfo.value)
    assert "cycles.kind missing" in message
    assert "missing table" in message
    assert "may need rebuilding" in message


@pytest.mark.anyio
async def test_startup_refusal_reaches_stderr_and_log_file(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A failed startup check must be visible, not just exit code 3.

    Uvicorn runs the lifespan inside ``LifespanOn``: starlette answers
    ``lifespan.startup.failed`` with the traceback and uvicorn logs it on
    ``uvicorn.error``. That logger only reaches the console and
    ``data/logs/stock-agent.log`` because ``install_queue_root`` points it
    at the queue pipeline — without it the refusal printed nowhere
    (issue #15). Drives the real handler so no socket is bound.
    """
    db_path = Path(get_config().env.sa_db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE decisions DROP COLUMN cached")

    app = create_app()
    config = uvicorn.Config(app=app, log_config=None)
    config.load()
    lifespan = LifespanOn(config)

    # The refusal exits mid-lifespan: the hooks, run lock, shutdown
    # manager and log pipeline it installed never get torn down, so the
    # test puts back what the boot replaced.
    loop = asyncio.get_running_loop()
    prior_hooks = (
        sys.excepthook,
        threading.excepthook,
        loop.get_exception_handler(),
    )
    try:
        await lifespan.startup()
    finally:
        sys.excepthook = prior_hooks[0]
        threading.excepthook = prior_hooks[1]
        loop.set_exception_handler(prior_hooks[2])
        set_current(None)
        run_lock = getattr(app.state, "run_lock", None)
        if run_lock is not None:
            run_lock.release()
        shutdown_logging()

    assert lifespan.startup_failed
    assert lifespan.should_exit

    # The queue listener drains on a thread: wait for the record to land
    # instead of asserting against a half-written capture.
    log_file = (
        Path(get_config().env.sa_data_dir) / "logs" / "stock-agent.log"
    )
    stderr_seen = ""
    log_seen = ""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        stderr_seen += capfd.readouterr().err
        log_seen = log_file.read_text() if log_file.exists() else ""
        if (
            "decisions.cached missing" in stderr_seen
            and "decisions.cached missing" in log_seen
        ):
            break
        await asyncio.sleep(0.05)

    assert "decisions.cached missing" in stderr_seen
    assert "Application startup failed. Exiting." in stderr_seen
    assert "decisions.cached missing" in log_seen
