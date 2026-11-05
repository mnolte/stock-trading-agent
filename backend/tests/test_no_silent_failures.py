"""PR 20: no silent failures (docs/SPEC.md, Error handling).

- Every ruff rule catches a sample (E722, BLE001, S110, TRY400, TRY203).
- No bare ``asyncio.create_task`` outside ``app.spawn`` (and the
  websocket fan-out, which manages its own wait pair).
- An exception in a task, a thread and a scheduler job lands in the log
  (the scheduler job end to end: an indexed ERROR line with an error_id).
- Covered elsewhere: an API route's 500 carries an error_id in the log
  (test_api_errors.py) and the frontend endpoint lands at ERROR in the
  index (test_log_index.py::test_client_log_endpoint).
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from app.spawn import install_global_hooks, spawn
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]

# Files allowed to touch the loop's task API directly.
_ALLOWED_CREATE_TASK = {
    "app/spawn.py",  # the helper itself
    "app/api/websocket.py",  # manages its own wait pair via ensure_future
    "app/api/main.py",  # loop internals
}


def test_no_bare_create_task() -> None:
    offenders: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND.parent).as_posix()
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        if rel.replace("backend/", "") in _ALLOWED_CREATE_TASK:
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            if re.search(r"(create_task|ensure_future)\(", line) and "noqa" not in line:
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "bare task creation outside app.spawn:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    ("sample", "rule"),
    [
        ("try:\n    pass\nexcept:\n    pass", "E722"),
        ("def f():\n    try:\n        pass\n    except Exception:\n        pass", "S110"),
        (
            "import logging\ndef f():\n    try:\n        pass\n"
            "    except Exception as e:\n        logging.error('x')",
            "TRY400",
        ),
        ("def f():\n    try:\n        pass\n    except Exception:\n        raise", "TRY203"),
        ("def f():\n    try:\n        pass\n    except Exception:\n        print(1)", "BLE001"),
    ],
)
def test_ruff_rules_catch_samples(sample: str, rule: str, tmp_path: Path) -> None:
    """Each enabled ruff rule fires on a sample; make lint can't regress."""
    path = tmp_path / "sample.py"
    path.write_text(sample)
    result = subprocess.run(
        [
            str(BACKEND.parent / ".venv/bin/ruff"),
            "check",
            "--select",
            rule,
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, f"ruff {rule} did not fire:\n{result.stdout}"


def test_ble001_catches_uncommented(tmp_path: Path) -> None:
    """A blind `except Exception` that neither logs nor re-raises fails."""
    path = tmp_path / "sample2.py"
    path.write_text(
        "def f():\n    try:\n        pass\n    except Exception:\n        print(1)\n"
    )
    result = subprocess.run(
        [
            str(BACKEND.parent / ".venv/bin/ruff"),
            "check",
            "--select",
            "BLE001",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


def _capture() -> tuple[list[logging.LogRecord], logging.Logger, logging.Handler]:
    """Attach a real handler to the `app` logger for hook/spawn tests."""
    records: list[logging.LogRecord] = []
    handler = logging.StreamHandler()
    handler.emit = records.append  # type: ignore[method-assign, assignment]
    target = logging.getLogger("app")
    target.addHandler(handler)
    return records, target, handler


def test_spawn_logs_failure() -> None:
    records, target, handler = _capture()
    try:
        async def boom() -> None:
            raise RuntimeError("task exploded")

        async def run() -> None:
            spawn(boom(), "test-boom")
            await asyncio.sleep(0.05)

        asyncio.run(run())
        assert any(
            r.levelno == logging.ERROR and "test-boom" in r.getMessage()
            for r in records
        )
    finally:
        target.removeHandler(handler)


def test_thread_hook_logs() -> None:
    loop = asyncio.new_event_loop()
    install_global_hooks(loop)
    records, target, handler = _capture()
    old_sys_hook, old_thread_hook = sys.excepthook, threading.excepthook
    try:
        def die() -> None:
            raise ValueError("thread died")

        t = threading.Thread(target=die, name="test-dead-thread")
        t.start()
        t.join()
        assert any(
            r.levelno == logging.ERROR and "test-dead-thread" in r.getMessage()
            for r in records
        )
    finally:
        loop.close()
        target.removeHandler(handler)
        sys.excepthook, threading.excepthook = old_sys_hook, old_thread_hook


def test_scheduler_job_failure_is_indexed_with_error_id(client: TestClient) -> None:
    """A job that raises reaches the EVENT_JOB_ERROR listener in the app
    lifespan and lands in the log index at ERROR with an error_id —
    never only on APScheduler's own stderr."""
    import queue

    from app.logging_config import get_pipeline

    app = cast(FastAPI, client.app)
    scheduler: Any = app.state.scheduler
    handler = app.state.log_index_handler
    assert handler is not None

    def boom() -> None:
        raise RuntimeError("scheduled job exploded")

    # Test-side wall clock (not engine code): run the job right away.
    scheduler.add_job(
        boom,
        "date",
        run_date=datetime.now(scheduler.timezone) + timedelta(milliseconds=50),
        id="test-boom-job",
        misfire_grace_time=60,
    )
    pipeline = get_pipeline()
    deadline = time.monotonic() + 10
    rows: list[dict[str, Any]] = []
    while not rows and time.monotonic() < deadline:
        # Drain the queue ourselves: the listener thread can starve under
        # parallel load; either path writes to the same handler.
        if pipeline is not None:
            while True:
                try:
                    record = pipeline.queue.get_nowait()
                except queue.Empty:
                    break
                pipeline.listener.handle(record)
        handler.flush()
        lines = client.get("/api/logs?levels=ERROR").json()["lines"]
        rows = [
            line for line in lines
            if "scheduler job test-boom-job failed" in line["message"]
        ]
        if not rows:
            time.sleep(0.05)
    assert rows, "a failed scheduler job must reach the log index"
    assert rows[0]["level"] == "ERROR"
    assert str(rows[0]["error_id"]).startswith("e-")
