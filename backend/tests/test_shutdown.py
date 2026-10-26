"""PR 20: graceful shutdown + run.lock (docs/SPEC.md, Clean shutdown)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import cast

import pytest
from app.shutdown import (
    RunLock,
    ShutdownManager,
    _boot_id,
    quick_check,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_lock_free(tmp_path: Path) -> None:
    assert RunLock(tmp_path / "run.lock").check().status == "free"


def test_lock_live_pid_refuses(tmp_path: Path) -> None:
    """A live pid with a fresh run.lock refuses startup (spec: never two
    writers). pid 1 is always alive and is never this process."""
    path = tmp_path / "run.lock"
    path.write_text(json.dumps({"pid": 1, "started_at": time.time()}))
    lock = RunLock(path)
    assert lock.check().status == "live"
    with pytest.raises(SystemExit):
        lock.acquire()


def test_lock_fresh_heartbeat_stays_live(tmp_path: Path) -> None:
    """started_at ages past the grace window; heartbeat_at keeps it live."""
    path = tmp_path / "run.lock"
    path.write_text(
        json.dumps(
            {
                "pid": 1,
                "started_at": time.time() - 3600,
                "heartbeat_at": time.time(),
                "heartbeat_seconds": 30,
            }
        )
    )
    assert RunLock(path).check().status == "live"


def test_lock_recycled_pid_is_stale(tmp_path: Path) -> None:
    """A live pid whose heartbeat went quiet is pid reuse, not the
    holder — a cache-restored lock must not refuse startup."""
    path = tmp_path / "run.lock"
    path.write_text(
        json.dumps(
            {
                "pid": 1,
                "started_at": time.time() - 3600,
                "heartbeat_seconds": 30,
            }
        )
    )
    lock = RunLock(path)
    assert lock.check().status == "stale"
    lock.acquire()
    assert lock.check().status == "live"
    lock.release()


def test_lock_stale_detected_and_replaced(tmp_path: Path) -> None:
    """A dead pid → stale → acquire() succeeds and marks the run unclean
    upstream (the lifespan logs app.unclean_previous_run + quick_check)."""
    path = tmp_path / "run.lock"
    # A pid that cannot exist.
    path.write_text(json.dumps({"pid": 2**30, "started_at": 1}))
    lock = RunLock(path)
    assert lock.check().status == "stale"
    lock.acquire()
    assert lock.check().status == "live"
    lock.release()
    assert not path.exists()


def test_lock_corrupt_file_is_stale(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    path.write_text("not json")
    assert RunLock(path).check().status == "stale"


def test_lock_other_boot_is_stale(tmp_path: Path) -> None:
    """A lock written under a different boot or VM is stale even when its
    pid is locally alive — pids recycle across boots (the e2e cache
    restore that refused CI startup carried exactly this)."""
    path = tmp_path / "run.lock"
    path.write_text(
        json.dumps({"pid": os.getpid(), "boot_id": "other-boot"})
    )
    lock = RunLock(path)
    assert lock.check().status == "stale"
    lock.acquire()  # must not refuse


def test_lock_same_boot_live_pid_stays_live(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    path.write_text(
        json.dumps({"pid": os.getpid(), "boot_id": _boot_id()})
    )
    lock = RunLock(path)
    assert lock.check().status == "live"
    with pytest.raises(SystemExit):
        lock.acquire()


def test_acquire_writes_boot_id(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    RunLock(path).acquire()
    assert json.loads(path.read_text())["boot_id"] == _boot_id()


def test_quick_check_ok_and_missing(tmp_path: Path) -> None:
    db = tmp_path / "ok.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO t VALUES (1)")
    assert quick_check(db) == "ok"
    missing = quick_check(tmp_path / "absent.db")
    assert missing != "ok"


def test_wait_for_work_finishes() -> None:
    async def run() -> bool:
        m = ShutdownManager(grace_seconds=30)
        done = {"n": 0}

        def mark_done() -> bool:
            done["n"] = 1
            return True

        return await m.wait_for_work(mark_done)

    assert asyncio.run(run()) is True


def test_wait_for_work_grace_expires() -> None:
    async def run() -> bool:
        m = ShutdownManager(grace_seconds=0)
        return await m.wait_for_work(lambda: False, poll=0.01)

    assert asyncio.run(run()) is False


def test_second_signal_skips_wait() -> None:
    """A second Ctrl+C (skip_wait) ends the grace wait immediately."""
    async def run() -> float:
        import time

        m = ShutdownManager(grace_seconds=60)
        started = time.monotonic()

        async def second() -> None:
            await asyncio.sleep(0.05)
            m.force()

        asyncio.get_running_loop().create_task(second())
        await m.wait_for_work(lambda: False, poll=0.01)
        return time.monotonic() - started

    assert asyncio.run(run()) < 5


def test_order_slot_blocks_until_stored() -> None:
    """wait_for_work holds while an order submit is between send and store."""
    async def run() -> None:
        m = ShutdownManager(grace_seconds=30)
        slot = m.order_slot()
        slot.__enter__()
        try:
            assert m.in_flight_orders == 1
            assert not m._in_flight.is_set()
        finally:
            slot.__exit__(None, None, None)
        assert m.in_flight_orders == 0
        assert m._in_flight.is_set()
        assert await m.wait_for_work(lambda: True) is True

    asyncio.run(run())


def test_writes_refused_while_shutting_down(client: TestClient) -> None:
    """503 for POST/PUT while shutting down; GETs keep working (spec)."""
    manager = cast(FastAPI, client.app).state.shutdown
    manager.begin()
    try:
        assert client.get("/api/health").status_code == 200
        resp = client.post("/api/keys/openai", json={"value": "sk-x"})
        assert resp.status_code == 503
        assert resp.json()["detail"] == "shutting down"
    finally:
        manager.shutting_down.clear()


def test_unclean_run_banner(client: TestClient) -> None:
    app = cast(FastAPI, client.app)
    app.state.unclean_previous_run = True
    app.state.unclean_quick_check = "ok"
    try:
        banners = client.get("/api/banners").json()["items"]
        assert any(b["kind"] == "unclean_run" for b in banners)
    finally:
        app.state.unclean_previous_run = False
