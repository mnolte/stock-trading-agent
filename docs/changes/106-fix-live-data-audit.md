---
item: 106
type: fix
title: Live data audit — log index pruning, position entry link, scheduler starvation
status: merged
issue: 314
kickoff: —
spec_prs: []
build_prs: [317]
---

# 106 · fix · Live data audit

## 2026-11-19 · build PR #317

Found analysing the live data of 19 November 2026:

- **Log index never pruned.** `prune_index()` had no production caller while SPEC says `data/logs/logs.db` "is pruned with the files". The index now prunes on the same `logging.keep_days` window.
- **Position entry link.** PG's position row (id 8) kept `entry_order_id` NULL: its `opened_at` precedes the entry `filled_at`, so the `i1b2c3d4e5f6` backfill skipped it. A new migration backfills remaining NULLs through the decision link.
- **Scheduler starvation.** `BacktestManager.tick` and other housekeeping jobs were intermittently skipped/missed by minutes while cycles ran on time. Every trade update queued a full `reconcile()`; a burst after a stream reconnect serialized into minutes of work and starved the shared scheduler/executor. Stream-triggered reconciles now coalesce into at most one trailing pass per burst — `_record_trade_update` still stores fills and closes positions immediately.
