"""The 15-minute position check (docs/SPEC.md, Alpaca integration).

Between decision cycles a lightweight check runs: no model calls, no new
trades. It reconciles the database against the broker, makes sure every
position has a live stop, refreshes the session brakes (shock brake,
daily-loss limit, drawdown kill) and cancels stale entry orders — the
last two happen inside ``reconcile`` and ``update_session_brakes``.
Consecutive failures alert at the configured threshold.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import Cycle, EquitySnapshot
from app.engine.executor import Executor
from app.engine.outcomes import OutcomeWorker
from app.engine.state import ExecutionState, record_event
from app.market.clock import NY_TZ, Clock


class _ReconcileFailed(Exception):
    """Reconcile errors are already in report.errors — nothing to append."""


@dataclass
class CheckReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    mismatches: int = 0
    stops_placed: int = 0
    entries_cancelled: int = 0


class PositionChecker:
    """Runs one position check between decision cycles."""

    def __init__(
        self,
        *,
        executor: Executor,
        state: ExecutionState,
        config: AppConfig,
        clock: Clock,
        outcomes: OutcomeWorker | None = None,
        session_factory: sessionmaker[Session] | None = None,
        on_check_done: Callable[[], None] | None = None,
    ) -> None:
        self.executor = executor
        self.state = state
        self.config = config
        self.clock = clock
        self.outcomes = outcomes
        self._session_factory = session_factory or get_session_maker()
        # Shadows replay new 5-min bars for their virtual stops and targets
        # after each check — scheduled as their own task by main.
        self.on_check_done = on_check_done or (lambda: None)

    async def run(self) -> CheckReport:
        now = self.clock.now()
        report = CheckReport()
        # Outside the session (weekend, holiday, pre-open, post-close):
        # the broker-side stops keep working without 15-minute broker
        # calls — the agent stays idle (docs/SPEC.md, Session).
        ny_now = now.astimezone(NY_TZ)
        session_day = self.executor.calendar.session_for(ny_now.date())
        if session_day is None or not (
            session_day.open <= ny_now < session_day.close
        ):
            return report
        with self._session_factory() as session:
            cycle = Cycle(
                kind="position_check",
                scheduled_time=_naive_utc(now),
                started_at=_naive_utc(now),
                status="running",
            )
            session.add(cycle)
            session.commit()
            try:
                await self._run(session, report)
            except _ReconcileFailed:
                report.ok = False
            except Exception as exc:  # noqa: BLE001
                report.ok = False
                report.errors.append(str(exc))
            cycle.status = "done" if report.ok else "error"
            cycle.errors = report.errors or None
            cycle.finished_at = _naive_utc(self.clock.now())
            session.commit()

        if report.ok:
            self.state.failed_checks = 0
        else:
            self.state.failed_checks += 1
            threshold = int(
                self.config.get(
                    "schedule", "position_check_failures_alert", default=3
                )
            )
            if self.state.failed_checks >= threshold:
                with self._session_factory() as session:
                    record_event(
                        session,
                        self.clock,
                        level="critical",
                        event_type="position_check_failed",
                        details={
                            "consecutive": self.state.failed_checks,
                            "errors": report.errors,
                        },
                    )
                    session.commit()
        try:
            self.on_check_done()
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("shadow hook failed after check")
        return report

    async def _run(self, session: Session, report: CheckReport) -> None:
        rec = await self.executor.reconcile()
        report.mismatches = len(rec.mismatches)
        report.stops_placed = len(rec.protective_stops)
        report.entries_cancelled = len(rec.entries_cancelled)
        if rec.errors:
            report.errors.extend(rec.errors)
            raise _ReconcileFailed
        # Since-open shock brake, daily loss and drawdown kill from the
        # real-time quote and account; the 1h leg stays on the cycle.
        await self.executor.update_session_brakes()
        if self.state.account is not None:
            if self.outcomes is not None:
                self.outcomes.write_equity_snapshot(
                    session,
                    equity=self.state.account.equity,
                    cash=self.state.account.cash,
                )
            else:
                session.add(
                    EquitySnapshot(
                        time=_naive_utc(self.clock.now()),
                        equity=self.state.account.equity,
                        cash=self.state.account.cash,
                    )
                )
        if self.outcomes is not None:
            self.outcomes.fill(session, self.clock.now())
