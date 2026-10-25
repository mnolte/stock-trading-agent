"""The app side of a backtest run: create, spawn, cancel, clean up.

A run is a separate child process (``python -m app.backtests.worker
<id>``), one at a time, at lower CPU priority — it never competes with
live jobs for the interpreter (docs/SPEC.md, Backtesting, Process). The
``backtests`` row is the control channel: cancel sets a flag the child
checks every session; if it has not stopped within
``cancel_grace_seconds``, the app stops the child by its PID. While a
live cycle or position check runs the app sets the pause flag; the child
waits before every Alpaca download and every model call. On app shutdown
the child is cancelled first; a run still marked running at startup is
interrupted and keeps its results.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.backtests.store import dir_size, run_dir_for
from app.config import AppConfig
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import Backtest
from app.market.clock import Clock

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "downloading", "running")
TERMINAL_STATUSES = (
    "done",
    "cancelled",
    "stopped_at_cost_cap",
    "interrupted",
    "refused",
)


class BacktestManager:
    """Owns the one-at-a-time child process for backtest runs."""

    def __init__(
        self,
        config: AppConfig,
        clock: Clock,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self._sessions = session_factory or get_session_maker()
        self._process: subprocess.Popen[bytes] | None = None
        self._run_id: int | None = None
        self._cancel_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # run lifecycle (called from the API)

    def create(
        self,
        *,
        name: str,
        start: date,
        end: date,
        universe_mode: str,
        strategies: list[dict[str, Any]],
        starting_equity: Decimal,
        slippage_pct: Decimal,
        estimate: Any = None,
    ) -> Backtest:
        """Insert the run row; refused rows never spawn a child."""
        with self._sessions() as session:
            row = Backtest(
                name=name,
                start_date=start,
                end_date=end,
                universe_mode=universe_mode,
                strategies=strategies,
                starting_equity=starting_equity,
                slippage_pct=slippage_pct,
                status="queued",
                run_dir="",
                created_at=_naive_utc(self.clock.now()),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            row.run_dir = str(run_dir_for(self.config, row.id))
            session.commit()
            session.refresh(row)
        return row

    def refuse(
        self,
        *,
        name: str,
        start: date,
        end: date,
        universe_mode: str,
        strategies: list[dict[str, Any]],
        starting_equity: Decimal,
        slippage_pct: Decimal,
        reason: str,
    ) -> Backtest:
        """A refused run is stored, never started (docs/SPEC.md, Runs)."""
        with self._sessions() as session:
            row = Backtest(
                name=name,
                start_date=start,
                end_date=end,
                universe_mode=universe_mode,
                strategies=strategies,
                starting_equity=starting_equity,
                slippage_pct=slippage_pct,
                status="refused",
                refuse_reason=reason,
                run_dir="",
                created_at=_naive_utc(self.clock.now()),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def cancel(self, run_id: int) -> bool:
        """Set the flag the child checks every session; after the grace
        period the app stops the child by its PID — never by name."""
        with self._sessions() as session:
            row = session.get(Backtest, run_id)
            if row is None or row.status not in ACTIVE_STATUSES:
                return False
            row.cancel_requested = True
            session.commit()
            pid = row.process_id
        grace = float(
            self.config.get("backtest", "cancel_grace_seconds", default=60)
        )
        if pid:
            from app.spawn import spawn

            self._cancel_task = spawn(
                self._stop_after_grace(run_id, pid, grace),
                f"backtest-cancel-{run_id}",
            )
        return True

    async def _stop_after_grace(self, run_id: int, pid: int, grace: float) -> None:
        await asyncio.sleep(grace)
        with self._sessions() as session:
            row = session.get(Backtest, run_id)
            if row is None or row.status not in ACTIVE_STATUSES:
                # The child already wrote a terminal status itself.
                return
            requested = bool(row.cancel_requested)
        if _pid_alive(pid):
            # Stop only this process, by the PID the app recorded — never
            # by name or pattern (hard rule 12).
            with contextlib.suppress(OSError):
                os.kill(pid, 15)
            deadline = time.monotonic() + float(
                self.config.get(
                    "backtest", "cancel_kill_wait_seconds", default=5
                )
            )
            while _pid_alive(pid) and time.monotonic() < deadline:
                self._reap(run_id)
                await asyncio.sleep(0.1)
            self._reap(run_id)
            if _pid_alive(pid):
                with contextlib.suppress(OSError):
                    os.kill(pid, 9)
                await asyncio.sleep(0.05)
                self._reap(run_id)
        with self._sessions() as session:
            row = session.get(Backtest, run_id)
            if row is not None and row.status in ACTIVE_STATUSES:
                row.status = "cancelled" if requested else "interrupted"
                row.finished_at = _naive_utc(self.clock.now())
                session.commit()

    def _reap(self, run_id: int) -> None:
        """Reap our own child if it exited — a zombie still answers
        ``kill(pid, 0)`` until it's waited on."""
        if self._run_id == run_id and self._process is not None:
            self._process.poll()

    def delete(self, run_id: int) -> bool:
        """Remove the run's folder and its summary row; never while active."""
        with self._sessions() as session:
            row = session.get(Backtest, run_id)
            if row is None or row.status in ACTIVE_STATUSES:
                return False
            folder = row.run_dir or str(run_dir_for(self.config, run_id))
            session.delete(row)
            session.commit()
        path = Path(folder)
        if path.exists() and path.name == f"run-{run_id}":
            shutil.rmtree(path, ignore_errors=True)
        return True

    def clear_bar_store(self) -> bool:
        """Only when no run is active (docs/SPEC.md, Data)."""
        if self.active_run() is not None:
            return False
        from app.backtests.store import clear_bar_store

        clear_bar_store(self.config)
        return True

    # ------------------------------------------------------------------
    # child process management

    def active_run(self) -> Backtest | None:
        with self._sessions() as session:
            return session.execute(
                select(Backtest).where(Backtest.status.in_(ACTIVE_STATUSES))
            ).scalars().first()

    def tick(self) -> None:
        """Reap a finished child; spawn the next queued run (one at a time)."""
        with self._sessions() as session:
            rows = session.execute(
                select(Backtest)
                .where(Backtest.status.in_(ACTIVE_STATUSES))
                .order_by(Backtest.id)
            ).scalars().all()
            for row in rows:
                if row.status == "queued":
                    continue
                if row.process_id and _pid_alive(row.process_id):
                    continue
                # A child that disappeared or never spawned — mark it and
                # keep its partial results (docs/SPEC.md, Process).
                row.status = "interrupted"
                row.finished_at = _naive_utc(self.clock.now())
                session.commit()
            queued = session.execute(
                select(Backtest)
                .where(Backtest.status == "queued")
                .order_by(Backtest.id)
            ).scalars().first()
            running = session.execute(
                select(Backtest.id).where(
                    Backtest.status.in_(("downloading", "running"))
                )
            ).scalar_one_or_none()
            if queued is not None and running is None:
                self._spawn(session, queued)
                session.commit()

    def _spawn(self, session: Session, row: Backtest) -> None:
        backend = Path(__file__).resolve().parents[2]
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "app.backtests.worker", str(row.id)],
            cwd=str(backend),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        row.process_id = proc.pid
        self._process = proc
        self._run_id = row.id
        from app.logging_config import ev

        ev(
            logger, logging.INFO, "backtest.spawned",
            f"backtest {row.id} spawned (pid {proc.pid})",
            run=row.id,
        )

    def set_paused(self, paused: bool) -> None:
        """The app calls this while a live cycle or check runs."""
        with self._sessions() as session:
            for row in session.execute(
                select(Backtest).where(
                    Backtest.status.in_(("downloading", "running"))
                )
            ).scalars():
                row.paused = paused
            session.commit()

    async def shutdown(self) -> None:
        """On app shutdown the child is cancelled first, then stopped by PID
        if it ignores the flag for the grace period."""
        with self._sessions() as session:
            rows = session.execute(
                select(Backtest).where(Backtest.status.in_(ACTIVE_STATUSES))
            ).scalars().all()
            pids = []
            for row in rows:
                row.cancel_requested = True
                if row.process_id:
                    pids.append(row.process_id)
            session.commit()
        # Cancel flag first; a child that ignores it is SIGTERMed by its
        # recorded PID after logging.backtest_kill_after_seconds.
        kill_after = float(
            self.config.get("logging", "backtest_kill_after_seconds", default=10)
        )
        if pids:
            deadline = asyncio.get_running_loop().time() + kill_after
            while (
                any(_pid_alive(pid) for pid in pids)
                and asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.05)
        for pid in pids:
            if _pid_alive(pid):
                with contextlib.suppress(OSError):
                    os.kill(pid, 15)
        with self._sessions() as session:
            for row in session.execute(
                select(Backtest).where(Backtest.status.in_(ACTIVE_STATUSES))
            ).scalars():
                # An app shutdown interrupts the run — "cancelled" stays
                # for the Cancel button (docs/SPEC.md, Clean shutdown).
                row.status = "interrupted"
                row.finished_at = _naive_utc(self.clock.now())
            session.commit()

    def interrupt_orphans(self) -> int:
        """Startup: a run still marked running is interrupted, results kept."""
        count = 0
        with self._sessions() as session:
            for row in session.execute(
                select(Backtest).where(Backtest.status.in_(ACTIVE_STATUSES))
            ).scalars():
                if row.process_id is not None and _pid_alive(row.process_id):
                    continue
                row.status = "interrupted"
                row.finished_at = _naive_utc(self.clock.now())
                count += 1
            session.commit()
        return count

    def refresh_sizes(self) -> None:
        """Update the stored size-on-disk for finished runs' folders."""
        with self._sessions() as session:
            for row in session.execute(
                select(Backtest).where(Backtest.run_dir != "")
            ).scalars():
                row.size_bytes = dir_size(Path(row.run_dir))
            session.commit()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def store_usage(config: AppConfig, sessions: sessionmaker[Session]) -> dict[str, Any]:
    """Runs card footer: bar store size and range + this month's spend."""
    from app.backtests.estimate import spent_this_month
    from app.backtests.store import store_info
    from app.market.clock import RealClock

    spent = spent_this_month(sessions, RealClock().now().date())
    info = store_info(config)
    info["month_spent_usd"] = float(spent)
    return info
