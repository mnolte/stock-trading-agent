# Spec changes

Newest first.

## 2026-10-17 — Adopted positions keep an existing broker stop

- Reconciliation: a position adopted at Alpaca that is already covered by an open sell stop there (e.g. one placed before the database was rebuilt) keeps that stop — no second stop order is placed for the same shares. The position records the existing stop's price as its stop.
- Order ids: `client_order_id` gains a per-process nonce (`sa-<kind>-<id>-<symbol>-<nonce>-<seq>`). Alpaca enforces uniqueness account-wide forever, so ids must not collide after a restart or a database rebuild.

## 2026-10-17 — Answers from the PR 8 review

- Data model: the daily backup at 03:00 uses SQLite's online backup API and `PRAGMA integrity_check`, never a plain file copy. Migrations are immutable once merged; startup refuses to run if the schema doesn't match the models.
- Errors: on a normal shutdown, wait up to 60 s for a running cycle or check; if it's still running it is marked interrupted and does not count as a failure. Only rows still running at the next startup (a real crash) count. Startup reconciles with Alpaca before anything else.
- AGENTS.md: new hard rule 11, migrations are immutable once merged.
- `config.example.yaml`: `storage.backup_time`, `storage.shutdown_grace_seconds`.

## 2026-10-16 — Screenshot location made explicit

- AGENTS.md, docs/DEVIN_KICKOFF.md and design/DESIGN.md: PR screenshots are saved in `docs/screenshots/pr-<n>/` and never in `design/`; the PR shows them next to the reference PNG. (The old wording "next to the matching file in design/screenshots/" read as "save them there".)

## 2026-10-16 — Answers to PR 7 questions

- Outcome tracking: "1 session" = same New York time on the next trading session; "5 sessions" = same time on the 5th trading session after the decision; clamped to the close on early-close days. Price = last closed 5-minute bar at or before the target time, stored with the bar's timestamp.
- GUI: Performance shows results by symbol (new "By symbol" card in the design); the day-trade counter belongs on Positions & orders, not Performance.

## 2026-10-16 — Answers to PR 6 questions

- Reset paper account: two steps. The button turns the kill switch on, closes positions, cancels orders and records `paper_reset`; Marc resets the balance in the Alpaca dashboard; "Start new run" checks no positions/orders and equity = `performance.starting_equity` ±1%, then starts a new run (`runs` table) that restarts the SPY benchmark, drawdown peak, losing streak and daily loss baseline. Resume via the normal kill-switch resume.
- Supervised approvals: the fresh-quote limit must pass the original code checks; one pending approval per symbol (newer replaces older); Approve disabled while the kill switch is on; `approvals` table with status and reason.
- WebSocket: `{"type":"update","topics":[...]}`, GUI refetches those endpoints (300 ms debounce), full refetch after reconnect, 60 s polling with a "live updates paused" note while the socket is down.
- Adding a symbol: sector required from a fixed list; must be an active, tradable US stock or ETF at Alpaca; "Warming up" with no entries until history is loaded.
- `config.example.yaml`: `performance.starting_equity`, `universe.allowed_sectors`.

## 2026-10-15 — Answers from the PR 5 review

- Risk management, kill switch: Stop & flatten never refuses because of the day-trade rule. It warns; if Alpaca rejects a sale, the position keeps its stop and a critical banner shows the rejection. (Closes the open question in design/DESIGN.md.)

## 2026-10-15 — Answers to PR 5 questions

- Day-trade rule: a same-day exit is recorded as deferred, not carried out. The next session's first cycle reviews the position again with the deferred exit in its input snapshot; the dashboard shows "Exit deferred (day-trade rule)".
- Execution mode: automatic only in PR 5; the app refuses to start with mode supervised until PR 6. Supervised mode: entries only; an approval expires at the next full cycle; approving re-checks gates, size and spread and sets a new limit from the current price.
- Reconciliation: a position found at Alpaca that the agent never recorded gets a protective stop at current price − 3 × 1h ATR (3% if ATR is unavailable), no target; no model calls or exits until acknowledged; marked as adopted, P&L shown apart. Data model: `positions.origin`.
- PR 5 done-when: read-only checks against the real paper account; Marc runs the first real paper session after review.
- `config.example.yaml`: `risk.adopted_stop_atr: 3`, `risk.protective_stop_fallback_pct: 3`.

## 2026-10-15 — Answers from the PR 4 review

- Decision layer: held positions are called first when the 10-call cap applies.
- Model output uses OpenAI structured outputs (strict JSON schema), still validated with Pydantic.
- `no_call` is an official final action, with a reason: `rules`, `call_cap`, `quote_missing` or `model_error`. Model calls time out after 30 s and are not retried; for a held position `no_call` leaves it as it is.
- Schedule: missed cycles are never run late (misfire grace 2 minutes); only simulated mode may run a cycle at startup.
- `config.example.yaml`: `decision.call_timeout_seconds: 30`, `schedule.misfire_grace_seconds: 120`.

## 2026-10-15 — Answers from the PR 3 review

- Market, schedule and data: daily indicators use Alpaca daily bars, 260 sessions per symbol, refreshed daily at 08:00 New York; computed on completed sessions only. All bar requests are split-adjusted. (60 sessions of 5-minute bars could not give SMA 200 or the 60-session ATR percentile.)
- Data model: new `bars_1d` table.
- Rules pre-filter: RSI 40–70 is inclusive.
- One cycle, step 3: the signal snapshot is stored; the GUI reads it and never recomputes per request.
- `config.example.yaml`: `schedule.daily_history_sessions: 260`, `schedule.bar_adjustment: split`.

## 2026-10-15 — Answers to PR 2 questions

- Market, schedule and data: Early close shows for the whole early-close session until its last cycle has run, then Last cycle done.
- SIP bar requests always end at least 16 minutes in the past (free-plan limit).
- Trading calendar unavailable: market treated as closed, retry every 5 minutes. EUR/USD unavailable: GUI shows USD only, marked "EUR unavailable".
- Data model `earnings`: time of day normalised to before_open, after_close, during_market or unknown (from Finnhub bmo, amc, dmh), raw value kept. Risk: an unknown time of day counts as before the open for the earnings exit.

## 2026-10-15 — 15-minute position check

- Market, schedule and data: new "Position check" between the hourly cycles, at 09:46, 10:01, 10:16, 10:31, then :01, :16 and :31 past each hour until 15:31 New York time. No model calls and no trades; it reconciles with Alpaca, makes sure every position has a stop, updates the since-open shock brake, daily loss limit and drawdown kill, and cancels stale entries.
- Decisions: the Cycle row mentions the check. Data model: `cycles.kind` (cycle or position check). Build plan: part of PR 5.
- `config.example.yaml`: `schedule.position_checks`, `schedule.position_check_failures_alert`.

## 2026-10-15 — Application timezone

- Overview: new "Timezone" paragraph. The application timezone is Europe/Amsterdam (GUI, logs, alerts, daily jobs); timestamps stay stored in UTC and decision cycles stay on the New York exchange clock.
- `config.example.yaml`: `app.timezone: Europe/Amsterdam`; `schedule.timezone` renamed to `schedule.market_timezone`.

## 2026-10-15 — Trading currency

- Overview: new "Currency" paragraph. All trading, risk limits and stored money values are in US dollars; euro amounts are display only and never used in decisions or risk checks.

## 2026-10-15 — First handoff

SPEC.md v0.1 as exported on 15 October 2026. It includes:

- Alpaca paper account only; free data plan (IEX real-time quotes, 15-minute delayed SIP bars).
- 10-symbol universe, long only, whole shares, 1-hour session-aligned bars, six cycles per session at :46.
- Bracket orders with broker-side stops on every position; reconciliation against Alpaca each cycle; PDT guard.
- gpt-4o-mini decision layer with strict JSON output, code checks, and outcome tracking for every decision.
- 14 risk gates, kill switch and Stop & flatten.
- 8-step build plan.

Details settled in this package that SPEC.md does not spell out:

- Environment variables: `MODE`, `SA_HOST`, `SA_PORT`, `SA_DATA_DIR`, `SA_DB_PATH`, plus the four API keys (`.env.example`).
- Every config key and default in `backend/config.example.yaml`.
- Command names: `make install`, `make run`, `make lint`, `make test`; frontend `pnpm lint` and `pnpm typecheck`.
- The full system prompt in `backend/prompts/decision.v1.md`.
