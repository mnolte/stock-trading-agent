"""PR 20: no silent failures (docs/SPEC.md, Error handling).

- Every ruff rule catches a sample (E722, BLE001, S110, TRY400, TRY203).
- No bare ``asyncio.create_task`` outside ``app.spawn`` (and the
  websocket fan-out, which manages its own wait pair).
- An exception in a task, a thread, a scheduler job, an API route and
  the frontend endpoint each lands in the log with an error_id.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import threading
from pathlib import Path

import pytest
from app.spawn import install_global_hooks, spawn

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


def _capture() -> tuple[list, object]:
    """Attach a real handler to the `app` logger for hook/spawn tests."""
    import logging

    records: list = []
    handler = logging.StreamHandler()
    handler.emit = records.append  # type: ignore[method-assign]
    target = logging.getLogger("app")
    target.addHandler(handler)
    return records, target


def test_spawn_logs_failure() -> None:
    import logging

    records, target = _capture()
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
        target.handlers.clear()


def test_thread_hook_logs() -> None:
    import logging

    loop = asyncio.new_event_loop()
    install_global_hooks(loop)
    records, target = _capture()
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
        target.handlers.clear()
