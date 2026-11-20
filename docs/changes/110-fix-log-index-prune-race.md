---
item: 110
type: fix
title: Log-index prune race
status: merged
issue: 324
kickoff: 110-fix-log-index-prune-race.md
spec_prs: [325]
build_prs: [326]
---

# 110 · fix · Log-index prune race

## 2026-11-20 · spec PR

Found on PR #322: `test_new_error_notification_once_per_fingerprint` failed 3 of 5 times in the test-image job and passed in backend-test. It's not a test-order problem. Item 106's daily prune of the log index races with the index's own setup:

1. `LogIndexHandler.flush()` calls `_maybe_prune()` before `_connect()` creates the schema. The prune thread can open the new, empty `logs.db` and fail with `no such table: log_lines`.
2. `_prune_worker` reports the failure with `logger.exception(...)`, which goes back through the index at ERROR. The spec says index failures go "to the file and stderr at ERROR, never through the index" (`docs/SPEC.md`, Logging → Index failures).
3. That ERROR becomes a "New error" notification: in the test a third row, live a false "New error: log index prune failed" on a fresh `logs.db` (new install, after `make logs-reindex`, after removing the file).

The prune also reads `datetime.now()` itself, so tests can't control when it runs.

No spec change: the fix makes the code follow Logging → Index and Index failures as written. The kickoff asks for: prune only after the schema exists, a missing table counts as nothing to prune, failures through `_report_failure` only, an injected `Clock`, and tests including a 50-run fresh-database loop.

Assumption: dropping the `logger.exception` line loses nothing, because `_report_failure` already writes the same failure to the file fallback and stderr at ERROR, with the exception text.

## 2026-11-20 · build PR #326

- `flush()` runs `_maybe_prune()` only after the batch is written, so `_connect()` has already created the schema; an empty flush on a never-connected handler starts no prune.
- `prune_index(db_path, keep_days, now)` returns 0 for a missing file or a database without `log_lines` (checked via `sqlite_master`); other sqlite errors still raise to the worker.
- `_prune_worker` reports failures only through `_report_failure` — the `logger.exception` call is gone, so an index failure can no longer re-enter the index at ERROR. `_report_failure` now includes the exception type (`OperationalError: disk I/O error`).
- `LogIndexHandler` and `make_index_handler` take an injected `Clock` (default `RealClock()`); `main.py` passes the app's clock. No `datetime.now()` remains in `log_index.py`.
- Tests: a 50-iteration fresh-database flush loop, `prune_index` on missing/schema-less databases, a forced prune failure checked against the fallback and the notification service, and a `FakeClock` once-per-UTC-day test. `test_new_error_notification_once_per_fingerprint` is unchanged.
