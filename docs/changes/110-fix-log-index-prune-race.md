---
item: 110
type: fix
title: Log-index prune race
status: ready
issue: 324
kickoff: 110-fix-log-index-prune-race.md
spec_prs: [999]
build_prs: []
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
