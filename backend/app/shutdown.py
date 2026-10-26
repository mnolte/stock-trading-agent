"""Graceful shutdown and the run lock (docs/SPEC.md, Clean shutdown).

``ShutdownManager`` carries the shutdown state: the moment a signal
arrives the API refuses writes (503), the scheduler pauses, and the
lifespan waits up to ``storage.shutdown_grace_seconds`` for running
cycles/checks and in-flight order submissions — a second signal skips
the wait. Clients are closed, the WAL is checkpointed, the engine
disposed and the log pipeline closed last.

``RunLock`` writes ``data/run.lock`` ({pid, boot_id, heartbeat}) at
startup: a live pid from the same boot refuses startup; a stale lock
means the last run died uncleanly — a quick_check runs and the GUI shows
the unclean-run banner.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import AppConfig

logger = logging.getLogger("app")


def _pid_alive(pid: int) -> bool:
    """os.kill(pid, 0): liveness probe on Linux, macOS and Windows."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _boot_id() -> str:
    """Linux boot id; "" where /proc is absent. A lock written under a
    different boot (or a different VM, e.g. a restored e2e cache) can
    only hold a recycled pid, so it must never count as live."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


@dataclass
class LockCheck:
    status: str  # "free" | "live" | "stale"
    pid: int = 0


class RunLock:
    """data/run.lock: pid + heartbeat so an unclean exit is visible."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def check(self) -> LockCheck:
        if not self.path.exists():
            return LockCheck("free")
        try:
            data = json.loads(self.path.read_text())
            pid = int(data.get("pid", 0))
        except (OSError, ValueError, AttributeError):
            return LockCheck("stale")
        boot_id = data.get("boot_id")
        if boot_id is not None and boot_id != _boot_id():
            return LockCheck("stale", pid)
        if pid == os.getpid() or _pid_alive(pid):
            return LockCheck("live", pid)
        return LockCheck("stale", pid)

    def acquire(self, heartbeat_seconds: int = 30) -> None:
        """Write the lock; refuse startup while a live pid holds it."""
        check = self.check()
        if check.status == "live":
            logger.critical(
                "startup refused: data/run.lock held by live pid %s",
                check.pid,
                extra={"event": "app.startup_refused"},
            )
            raise SystemExit(
                f"another stock agent is already running (pid {check.pid})"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write(heartbeat_seconds)

    def _write(self, heartbeat_seconds: int) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "boot_id": _boot_id(),
                    "started_at": time.time(),
                    "heartbeat_seconds": heartbeat_seconds,
                }
            )
        )

    def heartbeat(self) -> None:
        """Bump the heartbeat timestamp (called every heartbeat_seconds)."""
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        data["heartbeat_at"] = time.time()
        self.path.write_text(json.dumps(data))

    def release(self) -> None:
        self.path.unlink(missing_ok=True)


def quick_check(db_path: Path) -> str:
    """PRAGMA quick_check on a possibly-unclean database; 'ok' or detail."""
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error as exc:
        return f"quick_check failed: {exc}"
    result = "; ".join(str(r[0]) for r in rows)
    return "ok" if result == "ok" else result


class ShutdownManager:
    """Shutdown state shared between signals, middleware and lifespan."""

    def __init__(self, grace_seconds: int) -> None:
        self.grace_seconds = grace_seconds
        self.shutting_down = asyncio.Event()
        self.skip_wait = asyncio.Event()
        # Order submissions in flight between broker send and DB store —
        # shutdown waits for this to drain inside the grace window so a
        # submit is never cut between the two (hard rule 16).
        self.in_flight_orders = 0
        self._in_flight = asyncio.Event()
        self._in_flight.set()

    def begin(self) -> None:
        self.shutting_down.set()

    def force(self) -> None:
        """A second Ctrl+C: stop waiting, finish cleanup now."""
        self.skip_wait.set()

    async def order_guard(self) -> None:
        """Wait for in-flight order submits to store their answer."""
        await self._in_flight.wait()

    class _OrderSlot:
        def __init__(self, manager: ShutdownManager) -> None:
            self._m = manager

        def __enter__(self) -> None:
            self._m.in_flight_orders += 1
            self._m._in_flight.clear()

        def __exit__(self, *_: object) -> None:
            self._m.in_flight_orders -= 1
            if self._m.in_flight_orders <= 0:
                self._m.in_flight_orders = 0
                self._m._in_flight.set()

    def order_slot(self) -> ShutdownManager._OrderSlot:
        return ShutdownManager._OrderSlot(self)

    async def wait_for_work(
        self, predicate: Callable[[], bool], poll: float = 0.25
    ) -> bool:
        """Wait until ``predicate()`` says running work is done, or the
        grace expires, or a second signal asks to skip. Returns True when
        work finished in time."""
        deadline = asyncio.get_running_loop().time() + self.grace_seconds
        while True:
            if predicate() and self.in_flight_orders == 0:
                return True
            if self.skip_wait.is_set():
                return False
            if asyncio.get_running_loop().time() >= deadline:
                return False
            try:
                await asyncio.wait_for(self.skip_wait.wait(), timeout=poll)
                return False
            except TimeoutError:
                pass


def make_shutdown(config: AppConfig) -> tuple[ShutdownManager, RunLock]:
    """Build the manager + lock from config; wired up in the lifespan."""
    grace = int(config.get("storage", "shutdown_grace_seconds", default=60))
    lock = RunLock(Path(config.env.sa_data_dir) / "run.lock")
    return ShutdownManager(grace), lock


_current: ShutdownManager | None = None


def set_current(manager: ShutdownManager | None) -> None:
    global _current
    _current = manager


def current() -> ShutdownManager | None:
    """The active manager — lets the executor guard its submit window."""
    return _current
