# 110 · fix · Log-index prune race

Kickoff for Devin. Start the session with: "Build docs/kickoffs/110-fix-log-index-prune-race.md."

A backend fix in `backend/app/log_index.py` and its tests. No spec change, no config change, no GUI change. Spec: `docs/SPEC.md` → Logging → **Index** and **Index failures**. Item file: `docs/changes/110-fix-log-index-prune-race.md`. Issue: #324.

```text
[110] fix: log-index prune race. Goal: the log index's daily prune
(item 106) never runs before the index's schema exists, never reports
its own failure through the index, and takes its time from an injected
clock, so test_new_error_notification_once_per_fingerprint stops
failing at random and a fresh logs.db never sends a false "New error"
notification.
Start from the latest main. One PR: "[110] fix: Log-index prune race",
"Closes #324". Move this kickoff to docs/kickoffs/archive/ with git mv
in that PR. Set status: merged in docs/changes/110-fix-log-index-prune-race.md
and add a dated "build PR #n" section.

Read first:
1. AGENTS.md (Commands: CI runs the tests) and docs/README.md.
2. docs/SPEC.md → Logging → Index, Index failures; issue #324.
3. backend/app/log_index.py: LogIndexHandler.__init__, _connect,
   _report_failure, emit, flush, _maybe_prune, _prune_worker,
   prune_index, make_index_handler; backend/app/market/clock.py
   (Clock, RealClock, FakeClock); backend/app/api/main.py around
   make_index_handler; backend/tests/test_log_index.py.

What happens today (CI log of PR #322, test-image job):
  flush() → _maybe_prune() starts the prune thread → prune_index()
  sees logs.db (just created by sqlite3.connect in _connect) without
  log_lines → OperationalError "no such table: log_lines" →
  _prune_worker calls logger.exception("log index prune failed") →
  that ERROR line goes through the index handler → on_warning_or_above
  → a third "new_error" notification where the test expects 2.

Steps:
1. Schema first. In flush(), call _maybe_prune() after the batch is
   written (after _connect() has run the schema), not before. A flush
   with an empty buffer on a handler that has never connected must
   not start a prune.
2. prune_index(db_path, keep_days, now): return 0 when the file is
   missing or has no log_lines table (check sqlite_master); never
   raise for that case. Other sqlite errors still raise to the worker.
3. _prune_worker: on an exception, report only through
   self._report_failure(record, exc) (file fallback and stderr at
   ERROR, as Index failures says). Remove the logger.exception call.
   The success line "log index pruned: N rows older than D days"
   stays at INFO through the normal logger (that is not a failure).
   Include the exception type in the reported line:
   "log index write failed: OperationalError: <message> (line: log
   index prune failed)".
4. Clock: LogIndexHandler takes clock: Clock = RealClock() (keyword
   argument). _maybe_prune uses clock.now() for the UTC day;
   prune_index takes now: datetime for the cutoff; _prune_worker
   passes self._clock.now(). make_index_handler passes the app's clock
   when it has one; otherwise RealClock(). No datetime.now() is left
   in log_index.py.
5. Keep everything else as it is: the 1 s batch, the immediate flush
   from WARNING, the lock retry (_lock_streak), the once-per-UTC-day
   rule, the prune on its own daemon thread.

Tests (backend/tests/test_log_index.py; no network, each test its own
tmp_path database, FakeClock for time):
- Fresh database, first flush: a handler with keep_days=30 on a new
  tmp_path, emit one INFO and one ERROR line, flush, wait for the
  prune thread → no failure reported (a fake file_fallback receives
  nothing; stderr has no "log index write failed"). Run this 50 times
  in a loop inside the one test, each with a new database: the race
  must not appear once.
- prune_index on a file without log_lines → 0, no exception; on a
  missing file → 0; on a database with an old and a new row and a
  FakeClock now → drops only the old one (the existing test, moved to
  the clock).
- A prune that fails for another reason (monkeypatch prune_index to
  raise sqlite3.OperationalError("disk I/O error")) → the fallback
  gets one ERROR line with "OperationalError: disk I/O error"; the
  index gets no line from it; with the app's notification service, no
  "new_error" notification is created.
- Once per UTC day with FakeClock: two flushes on the same day start
  one prune; advancing the clock past 00:00 UTC starts a second one;
  keep_days=None starts none.
- test_new_error_notification_once_per_fingerprint stays as it is and
  now passes without depending on timing.

Rules: a pass and a fail case for every rule; no sleeps longer than
the existing 5 s deadline loops; nothing written into design/; no
datetime.now() outside RealClock. Tests are proven in CI: open the PR
as a draft early, run locally only test_log_index.py and ruff and mypy
on the changed files, never the full suites, e2e or image builds.

Done when: all CI checks are green (test-image included) and the PR
merges cleanly; the PR lists the five points of #324 under "Issues
closed" with the test for each.
```

## Why

Found on PR #322 on 20 November: the test-image job failed 3 of 5 runs on `test_new_error_notification_once_per_fingerprint`. The cause is a race in item 106's prune, not the test. It also breaks the spec rule that index failures never go through the index, and can send Marc a false "New error" notification on a fresh `logs.db`. Marc asked for this item on 20 November.

## Assumptions

- `_report_failure` (file fallback and stderr at ERROR) is enough for a prune failure; the Logs screen won't show it, as for every other index failure.
- Pruning after the write, not before, changes nothing for retention: it still runs once per UTC day, at the first flush of that day.

## Open questions

None.
