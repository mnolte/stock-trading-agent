"""Scan fixes of 31 October — backtest child process identity.

A recorded pid alone can't prove a process is ours: the OS recycles
ids. ``process_started_at`` pins the exact process, so a signal never
lands on a reused pid and a mismatched pid marks the run interrupted.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from app.backtests.manager import BacktestManager, _pid_same, _pid_start
from app.config import AppConfig
from app.db.models import Backtest
from app.market.clock import FakeClock
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def _manager(session_factory: sessionmaker[Session]) -> BacktestManager:
    return BacktestManager(
        AppConfig(), FakeClock(datetime.now(UTC)), session_factory=session_factory
    )


def _running_row(mgr: BacktestManager) -> int:
    row = mgr.create(
        name="run",
        start=date(2026, 1, 5),
        end=date(2026, 1, 9),
        universe_mode="fixed",
        strategies=[],
        starting_equity=Decimal(10000),
        slippage_pct=Decimal(0),
    )
    with mgr._sessions() as session:
        row = session.get(Backtest, row.id)
        assert row is not None
        row.status = "running"
        row.process_id = os.getpid()  # a live pid that is not ours
        row.process_started_at = 1.0  # start time that cannot match
        session.commit()
        session.refresh(row)
        return row.id


def test_reused_pid_is_not_ours(session_factory: sessionmaker[Session]) -> None:
    """A live pid with a different start time fails the identity check —
    it is a recycled pid, not our child."""
    real = _pid_start(os.getpid())
    assert real is not None and real != 1.0
    assert _pid_same(os.getpid(), real)
    assert not _pid_same(os.getpid(), 1.0)
    assert _pid_same(os.getpid(), None)  # pre-migration rows: alive only
    assert not _pid_same(2**22, None)


def test_tick_marks_recycled_pid_interrupted_and_sends_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    mgr = _manager(session_factory)
    run_id = _running_row(mgr)
    signals: list[int] = []
    real_kill = os.kill

    def spy(pid: int, sig: int) -> None:
        if sig:
            signals.append(sig)
            raise AssertionError(f"signal {sig} sent to recycled pid {pid}")
        real_kill(pid, sig)

    with patch("app.backtests.manager.os.kill", spy):
        mgr.tick()
    assert not signals
    with mgr._sessions() as session:
        row = session.get(Backtest, run_id)
        assert row is not None and row.status == "interrupted"


def test_matching_start_time_is_alive(session_factory: sessionmaker[Session]) -> None:
    """The same pid with the recorded start time counts as our child."""
    mgr = _manager(session_factory)
    run_id = _running_row(mgr)
    with mgr._sessions() as session:
        row = session.get(Backtest, run_id)
        assert row is not None
        row.process_started_at = _pid_start(os.getpid())
        session.commit()
    mgr.tick()
    with mgr._sessions() as session:
        row = session.get(Backtest, run_id)
        assert row is not None and row.status == "running"
