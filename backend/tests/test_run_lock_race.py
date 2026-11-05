"""RunLock.acquire's lost-create race (app/shutdown.py, issue #122).

Two starts can both see "free" and race to O_CREAT|O_EXCL; only one
create succeeds. The loser lands in the FileExistsError branch and must
never also win while the winner is alive — even when it catches the
winner's file empty, mid-write. The winner is simulated by writing its
lock just before the loser's create raises.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from app.shutdown import LockCheck, RunLock, _boot_id


def _live_payload() -> str:
    # pid 1 is always alive and never this process (see test_shutdown).
    return json.dumps({
        "pid": 1,
        "boot_id": _boot_id(),
        "started_at": time.time(),
        "heartbeat_seconds": 30,
    })


def _lose_create_race(lock: RunLock, winner_writes: str) -> None:
    """Make the loser's create fail the way a real race does: the winner's
    file appears between our check() and our O_EXCL open."""

    def create(heartbeat_seconds: int) -> None:
        lock.path.write_text(winner_writes)
        raise FileExistsError(str(lock.path))

    lock._create = create  # type: ignore[method-assign]


def test_lost_race_to_a_live_winner_refuses(tmp_path: Path) -> None:
    lock = RunLock(tmp_path / "run.lock")
    _lose_create_race(lock, _live_payload())
    with pytest.raises(SystemExit, match="already running"):
        lock.acquire()
    # The winner's lock is untouched — exactly one owner.
    assert json.loads(lock.path.read_text())["pid"] == 1


def test_lost_race_waits_out_a_mid_write_winner(tmp_path: Path) -> None:
    """The winner's file is still empty (unreadable → stale) on the first
    re-checks; once its payload lands the loser must refuse, not
    overwrite it."""
    lock = RunLock(tmp_path / "run.lock")
    _lose_create_race(lock, "")
    real_check = lock.check
    calls = 0

    def check() -> LockCheck:
        nonlocal calls
        calls += 1
        if calls == 4:  # 1 pre-check in acquire + 2 empty re-checks
            lock.path.write_text(_live_payload())
        return real_check()

    lock.check = check  # type: ignore[method-assign]
    with pytest.raises(SystemExit, match="already running"):
        lock.acquire()
    assert calls == 4
    assert json.loads(lock.path.read_text())["pid"] == 1


def test_lost_race_to_a_vanished_winner_takes_the_lock(tmp_path: Path) -> None:
    """The winner exited (file gone → free) before our re-check: we write
    our own lock instead of polling for the full window."""
    lock = RunLock(tmp_path / "run.lock")

    def create(heartbeat_seconds: int) -> None:
        raise FileExistsError(str(lock.path))  # the winner already left

    lock._create = create  # type: ignore[method-assign]
    started = time.monotonic()
    lock.acquire()
    assert time.monotonic() - started < 0.4  # no 50 × 10 ms polling
    assert json.loads(lock.path.read_text())["pid"] == os.getpid()


def test_lost_race_to_a_dead_winner_takes_over_after_the_window(tmp_path: Path) -> None:
    """A winner whose pid is dead stays stale through every re-check; after
    the bounded window the loser writes its own lock (an unclean leftover,
    not a live owner)."""
    lock = RunLock(tmp_path / "run.lock")
    dead = json.dumps({"pid": 999999999, "boot_id": _boot_id(), "started_at": 0})
    _lose_create_race(lock, dead)
    lock.acquire()
    assert json.loads(lock.path.read_text())["pid"] == os.getpid()
