# Error-handling audit — 2026-10-24 (PR 20)

Audit of the code on `main` after PRs 16–19 merged (PRs 18 and 19 included).
Scope per docs/SPEC.md → "Logging, error handling and shutdown → Audit first":
every `try`/`except`, background task, callback and thread; the logging each
component has and needs; what Ctrl+C does today. Fixes follow this report;
anything that would change trading behaviour beyond logging and shutdown is in
"Questions" at the end, not in code.

## 1. `try`/`except` inventory

Grouped by what the catch does today. "Fix" says what changes in PR 20; catches
already matching the spec's rule (re-raise, defined outcome + log at the right
level, or expected condition + WARNING/DEBUG with reason) only get the log line
they are missing.

### 1a. Boundary catches → `DataResult`/`ModelResponse`/`KeyTestResult` error (defined outcome)

These are the network/parse boundaries. The error string is returned to the
caller, but nothing is logged with a traceback — a failed fetch is invisible in
the log unless the caller happens to log it.

| Where | Today | Fix |
| --- | --- | --- |
| `backend/app/broker/alpaca.py:192,202,232,239,250,281,306,332,346,355,363,375,397` | Broker read/write call raised → `DataResult(error=str(exc))`; no log line. | Keep the outcome; log the call result at DEBUG, failures at ERROR (`broker.call_failed`, no keys in args). |
| `backend/app/data/alpaca.py:259,272,287,317,344,365,418` | Same pattern → `DataResult.error`. | Keep; log fetch at DEBUG, failure at ERROR (`data.fetch_failed`). |
| `backend/app/data/alpaca.py:400` | Per-symbol parse failure inside a batch → skipped symbol, batch continues; no log. | Log at WARNING with the symbol (`data.bar_parse_failed`). |
| `backend/app/data/finnhub.py:64`, `backend/app/data/ecb.py:66` | Source error → `DataResult.error`. | Keep; log at ERROR with source. |
| `backend/app/decide/client.py:179`, `backend/app/reviews/client.py:128` | Model call raised → `ModelResponse.error` + `error_type`/`http_status`. | Keep; log the sent call at DEBUG (model, tokens, latency — never prompt/response), failures at ERROR (`model.call_failed`). |
| `backend/app/secrets_service.py:169,196,214` | Key-test call raised → `KeyTestResult.error` (scrubbed). | Keep; log key-test result at INFO, never the key (`keys.test`). |
| `backend/app/secrets_service.py:265` | `_ScrubSecretsFilter` catches `Exception` so a broken record can never break logging. | Keep, deliberate `# noqa` with reason. |

### 1b. Expected-condition catches (narrow exception, no logging needed or a DEBUG line)

| Where | Today | Fix |
| --- | --- | --- |
| `backend/app/db/backup.py:55` (`ValueError`) | Non-dated filename skipped while pruning backups. | Keep; no change. |
| `backend/app/settings_service.py:204,221,238` (`TypeError`/`ValueError`), `351,459` (`SettingsError`), `395` (`OSError`) | Settings validation and prompt-existence checks → field errors. | Keep; no change. |
| `backend/app/decide/output.py:121` (`InvalidOperation`/`ValueError`), `163` (`JSONDecodeError`), `168` (`ValidationError`) | Model output parse → invalid output, defined outcome per hard rule 7. | Keep; log invalid output at WARNING (`model.invalid_output`). |
| `backend/app/decide/llm_cache.py:102` (`IntegrityError`) | Cache write dedup → another writer won. | Keep; DEBUG (`llm_cache.conflict`). |
| `backend/app/reviews/service.py:395` (`JSONDecodeError`), `587,667,751` (`IntegrityError`) | Malformed stored text / dedup. | Keep; DEBUG where silent today. |
| `backend/app/reviews/templates.py:18` (`ValueError`), `backend/app/shadows/strategy.py:180` (`OSError`), `191,218` (`ArithmeticError`/`ValueError`), `backend/app/shadows/runner.py:88` (`ArithmeticError`), `1003,1097` (`ValueError`), `backend/app/shadows/compare.py:490` (`ValueError`) | Validation/parse fallbacks and control flow. | Keep; DEBUG where a swallowed detail helps tracing. |
| `backend/app/api/routes.py:400` (`ValueError`→422), `575,2073` (`OSError`), `799` (`ValueError`), `835,876` (`ReadinessError`), `2264` (`SettingsError`), `2357,3872` (`ValueError`), `4121,4141,4158` (`PoolError`), `backend/app/api/inspector.py:76,87,191` (`ValueError`→422), `454` (`FileNotFoundError`/`KeyError`/`OSError`→None), `backend/app/api/backtests.py:526` (`OSError`→prompt_text None), `backend/app/api/strategies.py:97` (`OSError`→skip prompt) | API argument/validation handling. | Keep; the API exception handler covers only *unhandled* errors. |
| `backend/app/api/websocket.py:70` (`WebSocketDisconnect`), `backend/app/api/main.py:1020` (`StarletteHTTPException` 404→SPA fallback, else re-raise), `backend/app/api/main.py:424` (`UnpricedModelError` re-raise) | Expected protocol control flow. | Keep. |
| `backend/app/backtests/estimate.py:91` (`Exception`→None for unknown prompt version) | Broad catch but a defined fallback; deliberate `# noqa` with reason present. | Keep; narrow or document. |
| `backend/app/smoke_decision.py:236` (`ValueError`) | CLI argument error. | Keep. |

### 1c. Catch-log-continue (correct shape; needs the event code and level review)

All of these already log; PR 20 gives them stable event codes, the right level
per the table and context fields.

| Where | Today | Fix |
| --- | --- | --- |
| `backend/app/api/main.py:171` (readiness tick), `186` (backup), `207` (bar prune), `224` (after-exit), `282` (weekly review), `290` (briefing), `299` (verdict scan), `779` (review catch-up) | `logger.exception` → job failure logged. | Same + event codes (`jobs.backup_failed`, …), ERROR. |
| `backend/app/engine/cycle.py:312` (cycle body), `341` (outcomes), `400` (shadow hook), `473` (shadow symbols), `504` (shadow snapshots) | Cycle marked `error`, logged. | `cycle.failed`, `cycle.outcomes_failed`, `shadows.*`; ERROR for cycle body/outcomes, WARNING for shadow hooks (degraded, live unaffected). |
| `backend/app/engine/executor.py:522` (shadow flatten), `backend/app/engine/position_check.py:123` (shadow hook) | Logged. | WARNING `shadows.hook_failed`. |
| `backend/app/engine/position_check.py:89` (`_ReconcileFailed`), `91` (`Exception`) | Check marked `error`; reconcile errors come from the report. | Keep; log `check.failed` at ERROR with traceback for the generic branch. |
| `backend/app/engine/state.py:157` (notification sink) | `logger.exception`. | ERROR `notify.sink_failed`. |
| `backend/app/notifications/service.py:171` (queue), `184` (sink), `401` (row write), `450` (quiet-hours tick), `551` (row update), `653` (smtp test record), `784` (hydrate) | `logger.exception`. | ERROR `notify.*`; WARNING for the optional-write cases. |
| `backend/app/notifications/service.py:536` (SMTP send raised → error string) and `553` (`pass` on "email is not configured") | Send failure becomes `channel_status failed` + a WARNING line after 3 in a row; "not configured" is deliberately skipped with a bare `pass` — hits ruff S110 as written. | Rework the flow so no bare `pass` remains; log a failed send at ERROR (`notify.email_failed`); "not configured" stays a defined outcome, not a send failure (DEBUG). |
| `backend/app/universe/service.py:625` (screen failed → warning event + banner) | `logger.exception` + `universe_screen_failed` event. | ERROR `universe.screen_failed` + keep event. |
| `backend/app/backtests/worker.py:540` (per-bundle screen), `802` (run failed) | `logger.exception` to a child whose stderr goes to `/dev/null`. | ERROR `backtest.failed` → the child's own log file (§4). |
| `backend/app/api/routes.py:4101` (warm-up background task) | `logger.exception`. | ERROR `universe.warmup_failed`. |
| `backend/app/api/routes.py:130` (`_regime_label` → `"unavailable"`) | Any exception silently degrades the label; **no log at all** — a silent failure. | Expected condition: log at DEBUG with the reason (`api.regime_unavailable`). |
| `backend/app/reviews/facts.py:588` (regime → None) | Same silent fallback. | DEBUG `reviews.regime_unavailable`. |
| `backend/app/data/rate_limiter.py:70` (read retry) | Retry happens silently; the only trace is the eventual error. | DEBUG `data.retry` per attempt; WARNING when a limiter wait exceeds 5 s (`data.limiter_wait`). |
| `backend/app/engine/cycle.py:312` also stores the error on the cycle row | Already logged. | Keep. |

### 1d. Places a failure can pass silently today

1. `backend/app/notifications/service.py:349` — `asyncio.create_task(self._run_worker())`
   with no done-callback. If `drain()` itself raises (a DB error outside the
   per-item try), the worker task dies and its exception is never retrieved:
   notifications silently stop. Fix: route through `spawn()` (logs the failure
   at ERROR) and keep per-item isolation.
2. `backend/app/backtests/manager.py:151` — `self._cancel_task = asyncio.create_task(self._stop_after_grace(...))`
   never awaited; a failure in `_stop_after_grace` is dropped. Fix: `spawn()`.
3. `backend/app/api/routes.py:4123,4144` — bare `asyncio.get_running_loop().create_task(_warm_up_in_background(...))`;
   the wrapper catches, but the call is outside any helper. Fix: `spawn()` and a
   lint/test rule that fails on bare `create_task`/`ensure_future` outside
   `app.spawn` and `app.api.websocket` internals.
4. `backend/app/broker/alpaca.py:425` — `future.add_done_callback(self._log_handler_result)`
   already logs handler failures; keep, switch to the structured logger.
5. `backend/app/broker/alpaca.py:450` — daemon thread `alpaca-trade-stream` runs
   `TradingStream.run()`; an exception escaping the SDK run is invisible
   (daemon thread, nothing logs it). Fix: `threading.excepthook` + a wrapper
   that logs stream start/stop/failure (`broker.stream_stopped` at WARNING —
   it silently stops delivering fills).
6. `backend/app/backtests/manager.py:262` — child spawned with
   `stdout/stderr=subprocess.DEVNULL`: every log line and traceback the child
   writes is discarded. Fix: the child configures the same JSON file handler
   writing `data/logs/backtest-<id>.log` (spec); stderr still to DEVNULL.
7. `backend/app/api/websocket.py:54-55` — `ensure_future` on the two wait
   branches; a non-disconnect error re-raises into uvicorn's handler (logged
   there today). Keep the pattern; the loop exception handler covers leaks.
8. Scheduler jobs: APScheduler logs a job exception to its own logger at ERROR,
   but several entry points (`run_scheduled_cycle`, `run_universe_screen`,
   `run_scheduled_check`, `refresh_daily_data_and_signals`,
   `retry_calendar_load`, `backtest_manager.tick`) do **not** catch
   exceptions themselves, relying on APScheduler's log. Fix: a scheduler
   `EVENT_JOB_ERROR` listener that logs `scheduler.job_failed` at ERROR with
   traceback + job id (and keeps APScheduler's own logger routed to our
   handlers), plus event codes on the job entry points.
9. `sys.excepthook`, `threading.excepthook`, the asyncio exception handler and
   `logging.captureWarnings` are all unset today → anything escaping a
   callback/thread/warning goes to stderr only, or nowhere in a child.
10. `backend/app/notifications/service.py:784` hydrate failure is logged; the
    `_held` deque then starts empty — held notifications from before the
    restart are dropped silently. Fix: keep the log; add a WARNING event so a
    lost quiet-hours summary is visible (behaviour-neutral: only reporting).

## 2. Logging per component — today vs needed

Today one handler exists: `TimedRotatingFileHandler` (midnight) on the root
logger at INFO, plain text (`logging_config.py`); uvicorn's own console.
No DEBUG anywhere, no structure, no tracing ids, no per-cycle ids.

Logger names below are the components the Logs screen filters on (design
`Logs.dc.html`). `event` codes are stable; the message carries the values.

| Component (logger) | Current logging | Needed |
| --- | --- | --- |
| `app` | Startup/shutdown incidental only | INFO `app.started` (version, config hash, mode), `app.shutdown_requested`, `app.unclean_previous_run`, `app.shutdown_complete`; WARNING `app.unclean_lock`; CRITICAL `app.startup_refused` (live pid in run.lock, non-paper account, schema mismatch) |
| `scheduler` | APScheduler's own logger only | INFO `scheduler.started`, `scheduler.paused`; DEBUG `scheduler.job_fired` (job id); ERROR `scheduler.job_failed` (listener) |
| `cycle` | `cycle failed` + shadow-hook messages | INFO `cycle.start`, `cycle.end` (id, status, symbols, calls, orders, duration), `cycle.skipped` (reason); DEBUG per-symbol `cycle.symbol` (rules status, price reason), `cycle.data` (bars/quotes/news fetched per scope); WARNING `cycle.slow` (>10 min), price_stale/price_suspect/spread_unknown; ERROR `cycle.failed`, `cycle.outcomes_failed` |
| `check` (position check) | none | INFO `check.end` (mismatches, stops placed, entries cancelled, duration); WARNING `check.skipped` (outside session → DEBUG actually, it is every 15 min); ERROR `check.failed` |
| `data.prices`, `data.news`, `data.earnings`, `data.calendar`, `data.fx`, `data.alpaca` | none | DEBUG `data.fetch` (source, symbols, window, status, ms) + `data.retry`; WARNING `data.fetch_degraded` (price_stale, spread_unknown, news truncated); ERROR `data.fetch_failed`; INFO `data.bars_pruned` |
| `rules` | none | DEBUG `rules.eval` (symbol, checks with values, status) |
| `model.openai`, `model.reviews` | none | DEBUG `model.call_sent`/`model.call_returned` (model, prompt hash, tokens, latency, request id — never prompt or response); WARNING `model.invalid_output`, `model.slow`; ERROR `model.call_failed` |
| `risk` | none | DEBUG `gates.eval` (symbol, every gate input and pass/fail), `gates.sizing` (qty math); INFO `gates.blocked` (which gate) |
| `broker.orders` | none | INFO `orders.submitted`/`filled`/`cancelled`/`replaced`/`timeout` (symbol, legs, client id — never keys); DEBUG `broker.call` (status, ms); ERROR `orders.submit_failed` (defined outcome → critical event stays) |
| `broker.reconcile` | none | INFO `reconcile.result` (orders synced, fills, mismatches, adopted, protective stops); CRITICAL `reconcile.mismatch`, `reconcile.position_without_stop` |
| `positions` | none | INFO `position.opened`/`closed` (exit reason, P&L); DEBUG `position.brake` (shock brake/daily loss/drawdown inputs and results) |
| `notify`, `notify.email`, `notify.browser` | failures only | INFO `notify.sent` (event, channels — never bodies); DEBUG `notify.held` (quiet hours / repeat limit); WARNING `notify.email_degraded` (fail streak); ERROR `notify.email_failed`, `notify.queue_failed` |
| `reviews` | catch-up count only | INFO `reviews.written` (kind, source model/template + reason, cost); WARNING `reviews.template_fallback` (number check failed etc.); DEBUG `reviews.call` |
| `universe` | screen failure | INFO `universe.screen` (eligible n, funnel, duration); WARNING `universe.warmup` per symbol; ERROR `universe.screen_failed`, `universe.warmup_failed` |
| `shadows` | flatten/hook failures | INFO `shadows.run` (strategy, decisions, cost, missed+reason); DEBUG `shadows.call`; WARNING `shadows.missed` (time/cost cap) |
| `backtest`, `backtest.worker` | `logger.exception` to /dev/null | INFO `backtest.spawned`/`finished` (status, cost, duration); DEBUG `backtest.progress` per session; ERROR `backtest.failed`, `backtest.stopped` (SIGTERM by pid) |
| `settings` | none | INFO `settings.saved` (version, changed keys — never values of keys), `settings.promoted` |
| `keys` | event rows only | INFO `keys.saved`/`keys.tested`/`keys.imported` (name, last4 — never the key); WARNING `keys.env_ignored` |
| `jobs` (backup, readiness, after-exit) | failures only | INFO `jobs.run` (name, result, ms); ERROR `jobs.failed` |
| `db` | none | INFO `db.backup`, `db.wal_checkpoint`, `db.engine_disposed`; CRITICAL `db.write_failed`, `db.integrity_failed` |
| `api` | uvicorn access log only | DEBUG `api.request` (method, path, status, ms — no bodies); ERROR `api.unhandled` (with `error_id`); INFO `api.client_error` (frontend-reported) |
| `frontend` | none | ERROR `frontend.error` (POST /api/logs/client, rate-limited) |

Every line carries `context` fields: `cycle`/`check`/`job`/`request` ids from
context variables, plus `symbol`, `decision`, `order`, `notification` where
they apply; `process` (`app` or `backtest-<id>`) and `pid` on all lines;
`error_id` + `exception` on ERROR/CRITICAL.

## 3. Ctrl+C today

Mechanism: uvicorn owns the signal handlers; SIGINT or SIGTERM stops accepting
connections and runs the FastAPI lifespan `finally` (`backend/app/api/main.py:967-992`):

1. `backtest_manager.shutdown()` — sets `cancel_requested`, waits
   `min(cancel_grace_seconds, 10)` = 10 s, SIGTERMs the child by PID, marks
   active rows **"cancelled"** (spec step 4 wants "interrupted").
2. `scheduler.shutdown(wait=False)` — no new jobs; a running job coroutine
   keeps executing on the loop but is not awaited.
3. Shadow tasks cancelled (correct: never live).
4. `notification_service.stop()` — drains the queue **with no time limit**
   (spec: pending emails get up to 5 s).
5. `finalize_interrupted_cycles(grace=60)` — polls the DB until no `running`
   cycle/check or the grace expires, then marks leftovers `interrupted`
   (reason "interrupted by shutdown").
6. `broker.stop_stream()`.

Observed in simulated mode (this audit): booting `MODE=simulated` uvicorn and
sending SIGINT during startup kills the process silently mid-migration — no
shutdown logging at all. Per case after a full start:

- **During a cycle:** the job keeps running on the loop while
  `finalize_interrupted_cycles` polls. Finishes inside 60 s → `done`;
  otherwise → `interrupted`. A second Ctrl+C does nothing special (uvicorn
  force-exits the interpreter). Writes are never refused (no 503), and nothing
  tells the GUI — no "shutting down" state, no banner.
- **During an order submission:** nothing shields the
  `submit → _store_broker_order` window. Cancelled between Alpaca's accept and
  the store → the order exists at the broker with no row; next start's
  reconciliation records it as an `unknown_order` mismatch (correct recovery,
  hard rule 3) but there is no CRITICAL log naming the in-flight order, and no
  `orders.submit_unconfirmed` flag at shutdown.
- **During a backtest:** handled by step 1 above; status becomes `cancelled`
  instead of the spec's `interrupted`; the child's logs are already lost
  (stderr → DEVNULL).
- **During the 03:00 backup:** `run_daily_backup` is a *sync* job — APScheduler
  runs it in a thread-pool executor. `scheduler.shutdown(wait=False)` does not
  wait for it, and the interpreter exits mid-copy: a torn
  `stock-agent.db.YYYY-MM-DD` file can be left behind (the copy's own
  integrity_check never runs). No tracking of running jobs exists to wait on.
- **Unclean stop:** a crash leaves `running` rows → next start's
  `recover_interrupted_cycles` marks them `error` (counted toward the
  failed-cycles kill). There is no `data/run.lock`, no heartbeat, no
  `quick_check`, no live-pid startup refusal and no unclean-run banner.
- **No WAL checkpoint (TRUNCATE), no engine dispose** on shutdown today; the
  log handler is not flushed/closed last (it is never closed).

## 4. Log/index gaps

- No JSON-lines file, no `QueueHandler`/`QueueListener`, no gzip rotation, no
  `keep_days`/`max_files` prune (today: midnight rotation, 30 files, plain text).
- Scrub filter exists (`_ScrubSecretsFilter`) but replaces with `••••` and only
  covers *stored* values — spec wants `[redacted]` plus key-shaped strings
  (`sk-…`, `PK…`, bearer/`Authorization` headers) on every handler.
- No `logs.db` index, no `/api/logs*` endpoints, no `logs` WebSocket topic,
  no `make logs-reindex`.
- No `log_reviews` table; the two Operations criteria (`Errors in the log`,
  `Critical lines in the log`) are absent from `readiness/service.py` — its
  `_META` still has 15 criteria.

## 5. Questions

Things found that would change behaviour beyond logging/shutdown — listed,
not fixed:

1. `BacktestManager.shutdown()` marks the run `cancelled`; spec step 4 says
   `interrupted`. Is renaming to `interrupted` on app-shutdown intended
   (shutdown scope) or a status change to review?
2. `scheduler.shutdown(wait=False)` waits for nothing, so sync jobs like the
   03:00 backup are abandoned mid-run. The fix I plan: wrap jobs in a
   "running work" registry the grace wait drains — a thread job can be waited
   on but not cancelled; after grace the app exits anyway. OK?
3. `NotificationService.stop()` drains with no cap; spec gives pending emails
   5 s. Draining a large queue inside 5 s may drop queued emails — acceptable
   per spec's "up to 5 s"?
4. `_run_worker` dying leaves notifications stopped silently. Beyond `spawn()`
   logging the failure, should the service also restart the worker or record
   a critical event? (Restarting a dead worker is new behaviour.)
5. API "writes" get 503 while shutting down — taken as every non-GET/HEAD
   request (plus `/ws` handshake?), reads keep working. Confirm.
6. `run.lock` live-pid check on Linux reads `/proc`; on macOS/Windows there is
   no `/proc` — is `os.kill(pid, 0)` an acceptable cross-platform liveness
   probe (it can't distinguish "our app" from any live pid)?
7. `spawn()` lint rule: I'll enforce "no bare `asyncio.create_task` outside
   `app.spawn`" via a test scanning the source (ruff has no such rule). The
   websocket module's `ensure_future` pair stays (it manages its own tasks) —
   allow-listed. OK?
8. The scrub filter currently writes `••••`; the spec text says `[redacted]`.
   Changing the marker changes the stored `test_detail` strings written by
   `scrub()` — harmless, but confirming `[redacted]` is the intended literal.
