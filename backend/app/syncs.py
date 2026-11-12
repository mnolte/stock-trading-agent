"""Sync-run records for the Universe → Data sync card (item 077).

Every sync job writes its own ``sync_runs`` row: at start, throttled to
at most one update a second while it works, and at the end with
``ok``, ``partial`` or ``failed`` plus the per-symbol/source failures
in plain words. The GUI only reads these rows — progress is written by
the job, never guessed (docs/SPEC.md, Universe screen → Data sync).

``SyncRuns.begin`` returns a ``SyncRun`` handle the job updates through
``progress`` / ``fail`` / ``finish``. Rows older than ``KEEP_DAYS`` are
pruned when a new run starts.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.db.models import Cycle, SyncRunRow
from app.logging_config import _all_secrets, _scrub_text
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, Clock

logger = logging.getLogger(__name__)

SYNC_JOBS = (
    "daily_bars",
    "screen",
    "bars_5m",
    "history",
    "earnings",
    "corporate_actions",
    "fred",
    "ecb",
)

# sync_runs rows are kept this long (docs/SPEC.md, Universe screen).
KEEP_DAYS = 30

# A running job updates its row at most this often (docs/SPEC.md).
_PROGRESS_INTERVAL_SECONDS = 1.0

# The history progress bar needs a pace before it shows a time left
# (docs/SPEC.md, Universe screen → Data sync).
ETA_MIN_DONE = 5

# A scheduled job counts as late once its time has passed by this much
# and no run started after that time today (app boot and slow fetches
# get this grace; docs/SPEC.md, Universe screen → Data sync).
LATE_GRACE = timedelta(minutes=10)


def _job_label(job: str) -> str:
    return {
        "daily_bars": "Daily bars",
        "screen": "Daily screen",
        "bars_5m": "5-min bars",
        "history": "History for new symbols",
        "earnings": "Earnings calendar",
        "corporate_actions": "Corporate actions",
        "fred": "FRED (VIX and releases)",
        "ecb": "ECB rates",
    }.get(job, job)


def _job_schedule(job: str, config: AppConfig) -> tuple[int, int]:
    """The New York wall-clock time a scheduled job next runs — used for
    a failure's ``next_retry``. ``bars_5m`` and ``history`` have no fixed
    time (every cycle / on demand), so they get none."""
    if job == "screen":
        hhmm = str(
            (config.pending() or config.effective())
            .get("universe", {})
            .get("screen", {})
            .get("time_ny", "08:10")
        )
    else:
        hhmm = str(
            config.get("schedule", "calendar_refresh", default="08:00")
        )
    try:
        hour, minute = (int(p) for p in hhmm.split(":")[:2])
        return hour, minute
    except (ValueError, TypeError):
        return 8, 0


class SyncRun:
    """Handle for one in-flight ``sync_runs`` row.

    All methods are synchronous and cheap: progress writes are throttled
    to one per ``_PROGRESS_INTERVAL_SECONDS`` (a dirty flag guarantees the
    last state still lands), failures accumulate in memory and land on
    the next write, and ``finish`` always writes the final row.
    """

    def __init__(self, syncs: SyncRuns, run_id: int, job: str) -> None:
        self._syncs = syncs
        self.id = run_id
        self.job = job
        self._done = 0
        self._total: int | None = None
        self._failures: list[dict[str, Any]] = []
        self._failed_whats: set[str] = set()
        self._aborted = False
        self._finished = False
        self._last_write = 0.0
        self._dirty = False

    # -- job-facing API -------------------------------------------------

    def progress(self, done: int, total: int | None = None) -> None:
        """Record ``done`` of ``total`` units finished; writes at most
        once a second, the rest is flushed by the next write."""
        self._done = done
        if total is not None:
            self._total = total
        self._write_if_due(force=False)

    def set_total(self, total: int) -> None:
        self._total = total
        self._write_if_due(force=False)

    def fail(
        self,
        what: str,
        error: str,
        next_retry: datetime | None = None,
    ) -> None:
        """One symbol or source failed; ``error`` in plain words (already
        scrubbed of secrets by the caller when it can carry one)."""
        key = str(what)
        if key in self._failed_whats:
            return
        self._failed_whats.add(key)
        self._failures.append(
            {
                "what": key,
                "error": str(error)[:500],
                "next_retry": (
                    _naive_utc(next_retry).isoformat()
                    if next_retry is not None
                    else None
                ),
            }
        )
        self._write_if_due(force=False)

    def abort(self, error: str) -> None:
        """The job itself failed — status ``failed`` at finish."""
        self._aborted = True
        self.fail(self.job, error)

    def finish(self) -> None:
        """Write the final row: ``failed`` on abort or when nothing
        succeeded, ``partial`` with failures, else ``ok``."""
        if self._finished:
            return
        self._finished = True
        if self._aborted:
            status = "failed"
        elif not self._failures:
            status = "ok"
        elif self._done == 0 or len(self._failures) >= max(self._done, 1):
            status = "failed"
        else:
            status = "partial"
        self._syncs._finish(
            self.id,
            status=status,
            done=self._done,
            total=self._total,
            failures=self._failures,
        )

    # -- internals --------------------------------------------------------

    def _write_if_due(self, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < _PROGRESS_INTERVAL_SECONDS:
            self._dirty = True
            return
        self._syncs._write(
            self.id,
            done=self._done,
            total=self._total,
            failures=self._failures,
        )
        self._last_write = now
        self._dirty = False

    def flush(self) -> None:
        """Write pending progress now (e.g. before a long await)."""
        if self._dirty and not self._finished:
            self._write_if_due(force=True)


class SyncRuns:
    """The ``sync_runs`` table: begin/progress/finish plus the reads the
    API and the retry endpoint need."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Clock,
        config: AppConfig,
        calendar: CalendarProvider | None = None,
    ) -> None:
        self._sf = session_factory
        self._clock = clock
        self._config = config
        self._calendar = calendar

    # -- writes ---------------------------------------------------------

    def begin(self, job: str, total: int | None = None) -> SyncRun:
        """Open a new run row for ``job`` and return its handle."""
        if job not in SYNC_JOBS:
            raise ValueError(f"unknown sync job {job!r}")
        self._prune()
        with self._sf() as session:
            row = SyncRunRow(
                job=job,
                started_at=_naive_utc(self._clock.now()),
                status="running",
                done=0,
                total=total,
                failures=None,
            )
            session.add(row)
            session.commit()
            run_id = row.id
        return SyncRun(self, run_id, job)

    def _prune(self) -> None:
        """Keep 30 days of runs; one DELETE per begin is cheap."""
        cutoff = _naive_utc(self._clock.now() - timedelta(days=KEEP_DAYS))
        with self._sf() as session:
            session.execute(
                delete(SyncRunRow).where(SyncRunRow.started_at < cutoff)
            )
            session.commit()

    def _write(
        self,
        run_id: int,
        *,
        done: int,
        total: int | None,
        failures: list[dict[str, Any]],
    ) -> None:
        with self._sf() as session:
            row = session.get(SyncRunRow, run_id)
            if row is None:
                return
            row.done = done
            row.total = total
            row.failures = list(failures) or None
            session.commit()

    def _finish(
        self,
        run_id: int,
        *,
        status: str,
        done: int,
        total: int | None,
        failures: list[dict[str, Any]],
    ) -> None:
        with self._sf() as session:
            row = session.get(SyncRunRow, run_id)
            if row is None:
                return
            job = row.job
            retry_at = self._next_retry_at(job)
            fixed = [
                {
                    **f,
                    "next_retry": f.get("next_retry")
                    or (
                        _naive_utc(retry_at).isoformat()
                        if retry_at is not None
                        else None
                    ),
                }
                for f in failures
            ]
            row.done = done
            row.total = total
            row.failures = fixed or None
            row.status = status
            row.finished_at = _naive_utc(self._clock.now())
            session.commit()
        ev_logger = logger
        ev_logger.info(
            "sync run %s finished: %s (%s, %s/%s, %d failure(s))",
            run_id,
            job,
            status,
            done,
            total if total is not None else "?",
            len(failures),
            extra={"event": "sync.run_finished", "job": job, "status": status},
        )

    def _next_retry_at(self, job: str) -> datetime | None:
        """Next scheduled New York time for ``job``, strictly after now —
        the failure row's ``next_retry``. None for on-demand jobs and
        when the calendar has no trading day ahead."""
        if job in ("bars_5m", "history"):
            return None
        hour, minute = _job_schedule(job, self._config)
        now = self._clock.now().astimezone(NY_TZ)
        if self._calendar is not None:
            day = now.date()
            for _ in range(15):
                if self._calendar.session_for(day) is not None:
                    candidate = datetime.combine(
                        day, dtime(hour, minute), tzinfo=NY_TZ
                    )
                    if candidate > now:
                        return candidate
                nxt = self._calendar.next_trading_day(day)
                if nxt is None:
                    return None
                day = nxt.date
            return None
        candidate = now.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # -- reads ----------------------------------------------------------

    def _latest_rows(self) -> dict[str, SyncRunRow]:
        with self._sf() as session:
            rows = session.execute(
                select(SyncRunRow).order_by(SyncRunRow.id.desc())
            ).scalars().all()
        latest: dict[str, SyncRunRow] = {}
        for row in rows:
            latest.setdefault(row.job, row)
            if len(latest) == len(SYNC_JOBS):
                break
        return latest

    def latest_run(self, job: str) -> SyncRunRow | None:
        with self._sf() as session:
            return session.execute(
                select(SyncRunRow)
                .where(SyncRunRow.job == job)
                .order_by(SyncRunRow.id.desc())
                .limit(1)
            ).scalar_one_or_none()

    def running(self, job: str) -> bool:
        row = self.latest_run(job)
        return row is not None and row.status == "running"

    def cycle_active(self) -> bool:
        """A decision cycle or position check is in flight — crash and
        shutdown leftovers are marked ``error``/``interrupted`` at
        startup, so ``running`` always means live work (item 077's
        retry refuses to race it)."""
        with self._sf() as session:
            return (
                session.execute(
                    select(Cycle.id).where(Cycle.status == "running").limit(1)
                ).first()
                is not None
            )

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() + "Z" if value is not None else None

    def _row_payload(self, row: SyncRunRow) -> dict[str, Any]:
        started = row.started_at
        finished = row.finished_at
        duration = (
            (finished - started).total_seconds()
            if finished is not None and started is not None
            else None
        )
        return {
            "job": row.job,
            "label": _job_label(row.job),
            "status": row.status,
            "started_at": self._iso(started),
            "finished_at": self._iso(finished),
            "duration_seconds": duration,
            "done": row.done,
            "total": row.total,
            "failures": row.failures or [],
        }

    def _schedule_label(self, job: str) -> str:
        """When the job normally runs, for the Data sync row."""
        if job == "bars_5m":
            return "each cycle"
        if job == "history":
            return "when a symbol is added"
        hour, minute = _job_schedule(job, self._config)
        label = f"{hour:02d}:{minute:02d} NY"
        if job == "screen":
            return f"{label} · trading days"
        if job == "fred":
            return f"{label} & 17:00 NY · daily"
        return f"{label} · daily"

    def _is_late(self, job: str, row: SyncRunRow | None) -> bool:
        """A scheduled job is late when its New York time passed
        ``LATE_GRACE`` ago and its latest run started before today's
        scheduled time. On-demand jobs (``bars_5m``, ``history``) have
        no due time and are never late; the screen is not due on a day
        the market is closed."""
        if job in ("bars_5m", "history"):
            return False
        now_ny = _aware(self._clock.now()).astimezone(NY_TZ)
        hour, minute = _job_schedule(job, self._config)
        due = datetime.combine(now_ny.date(), dtime(hour, minute), NY_TZ)
        if now_ny < due + LATE_GRACE:
            return False
        if (
            job == "screen"
            and self._calendar is not None
            and self._calendar.session_for(now_ny.date()) is None
        ):
            return False
        if row is None or row.started_at is None:
            return True
        return _aware(row.started_at).astimezone(NY_TZ) < due

    def describe(self) -> dict[str, Any]:
        """GET /api/universe/sync: the latest run per job plus the
        history job's live progress and time left."""
        latest = self._latest_rows()
        jobs = []
        for job in SYNC_JOBS:
            row = latest.get(job)
            if row is None:
                continue
            payload = self._row_payload(row)
            payload["schedule"] = self._schedule_label(job)
            payload["late"] = self._is_late(job, row)
            jobs.append(payload)
        history = self.history_progress()
        return {
            "jobs": jobs,
            "history": history,
            "max_requests": self._config.get(
                "concurrency", "max_requests", default=None
            ),
        }

    def history_progress(self) -> dict[str, Any] | None:
        """While a history run is in flight: symbols ready of total and
        the time left from the job's own pace — null until
        ``ETA_MIN_DONE`` symbols are done (docs/SPEC.md)."""
        row = self.latest_run("history")
        if row is None or row.status != "running":
            return None
        total = row.total
        done = row.done
        eta: float | None = None
        if (
            total
            and done >= ETA_MIN_DONE
            and row.started_at is not None
        ):
            elapsed = (
                self._clock.now() - _aware(row.started_at)
            ).total_seconds()
            if elapsed > 0 and done < total:
                eta = (total - done) / (done / elapsed)
        return {"ready": done, "total": total, "eta_seconds": eta}


def _aware(value: datetime) -> datetime:
    """Stored times are naive UTC; the clock is aware."""
    from datetime import UTC

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def scrub_error(exc: BaseException) -> str:
    """An exception's text with every known secret scrubbed — the only
    form an error may take into a sync_runs row (hard rule 9)."""
    return _scrub_text(str(exc), _all_secrets())


# ----------------------------------------------------------------------
# Tracked fetch wrappers — used by the scheduler jobs in app.api.main,
# the cycle's 5-minute bar refresh and the sync retry endpoint, so every
# job writes its row the same way (item 077).
# ----------------------------------------------------------------------


async def tracked_fetch(
    syncs: SyncRuns | None,
    job: str,
    fetch: Callable[[], Awaitable[Any]],
) -> Any:
    """Await one source fetch under a ``sync_runs`` row for ``job``.

    A single fetch can only fail whole, so one recorded failure makes
    the run ``failed``; ``done``/``total`` read 1/1 either way.
    """
    if syncs is None:
        return await fetch()
    run = syncs.begin(job, total=1)
    try:
        result = await fetch()
    except Exception as exc:
        run.abort(scrub_error(exc))
        raise
    error = getattr(result, "error", None)
    if error is not None:
        run.fail(str(getattr(result, "source", None) or job), str(error))
    run.progress(1, 1)
    run.finish()
    return result


async def tracked_fetches(
    syncs: SyncRuns | None,
    job: str,
    fetches: list[tuple[str, Callable[[], Awaitable[Any]]]],
) -> list[Any]:
    """Several source fetches that make up one job (FRED's VIX close and
    release calendar) under a single ``sync_runs`` row — one failure of
    several reads ``partial``, all of them ``failed``."""
    import asyncio

    if syncs is None:
        return list(await asyncio.gather(*(f() for _, f in fetches)))
    run = syncs.begin(job, total=len(fetches))
    try:
        results = list(await asyncio.gather(*(f() for _, f in fetches)))
    except Exception as exc:
        run.abort(scrub_error(exc))
        raise
    for (label, _), result in zip(fetches, results, strict=True):
        error = getattr(result, "error", None)
        if error is not None:
            run.fail(label, str(error))
    run.progress(len(results), len(fetches))
    run.finish()
    return results


async def tracked_symbols(
    syncs: SyncRuns | None,
    job: str,
    symbols: list[str],
    fetch: Callable[..., Awaitable[dict[str, Any]]],
    error_prefix: str,
) -> dict[str, Any]:
    """A per-symbol refresh (daily bars, 5-minute history) under one
    run row. ``fetch(symbols, on_symbol)`` returns the symbol → result
    map; ``on_symbol`` reports each symbol as its fetch lands so the
    row's ``done`` is the job's own pace, never a guess."""
    if syncs is None or not symbols:
        return await fetch(symbols, None)
    run = syncs.begin(job, total=len(symbols))
    done = 0

    def on_done(sym: str, res: Any) -> None:
        nonlocal done
        done += 1
        if res.error is not None:
            run.fail(sym, str(res.error))
        run.progress(done, len(symbols))

    try:
        result = await fetch(symbols, on_done)
    except Exception as exc:
        run.abort(scrub_error(exc))
        raise
    # Fetches that early-return (no calendar days, empty group) never
    # fire on_symbol — every wanted symbol was still attempted, so count
    # it and record its error.
    for sym, res in result.items():
        if res.error is not None:
            run.fail(sym, f"{error_prefix}: {res.error}")
    run.progress(len(result), len(symbols))
    run.finish()
    return result
