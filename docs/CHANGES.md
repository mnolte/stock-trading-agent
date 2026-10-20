# Spec changes

Newest first.

## 2026-10-20 — Token prices in config

- Decision layer, Prompt and Reviews, Model and cost (added by Devin): USD cost is computed from configured prices per million tokens, `decision.input_cost_per_mtok` / `output_cost_per_mtok` and the same keys under `reviews`. Added to `config.example.yaml` (gpt-4o-mini prices; check OpenAI's current prices).
- Two stale sentences fixed: the design handoff no longer says "7 screens", and Reviews no longer says the Dashboard is unchanged (it shows the daily briefing card).
- Strategies and shadows, Cost: from PR 13 one `model_prices` table keyed by model replaces those keys, because shadows and backtests can use other models; a model without a price can't be selected.

## 2026-10-20 — Fresh quote before entries; screened mode blocked until PR 12

- Alpaca integration, Entries: new "Fresh quote before every entry". The latest IEX quote is fetched right before each entry order (automatic and Approve, one shared function) and rejected if its own timestamp is older than 60 s; spread check, limit price and code checks are redone from it; any failure means no order, recorded as a failed "Fresh quote" gate. Exits never wait for a quote. Config: `execution.quote_max_age_seconds: 60`.
- Larger universe: until PR 12, Settings and startup refuse `universe.mode: screened`; the daily screen still runs and the Universe screen shows a preview. PR 12 removes the block.

## 2026-10-19 — Briefing card, model call counter, shadows and backtesting

- GUI: "Daily briefing card" (today's briefing on the dashboard next to the risk budget, with number-check status, model, cost and next briefing; previous briefing before 15:00) and "Model call counter" (decision calls today vs the day's maximum, cost today and this month; review, shadow and backtest cost on hover). Both in PR 10.
- New section "Strategies and shadows (v0.2)", PR 13: strategy configurations (llm or rules_only, prompt, model, temperature, rule and check overrides; risk gates never overridable); up to 3 shadows run after each live cycle on the same input snapshots with virtual portfolios and fills on 5-min bars; never delay live; no broker client; $5 monthly cap; comparison, "same as live", disagreements; Promote to live through Settings after 20 closed trades.
- New section "Backtesting (v0.2)", PR 14: live cycle code over past sessions with the fake broker and a database per run; data download at 50 calls/min paused during live work; lookahead guards; fills on 5-min bars (stop first when both touched); LLM strategies only after their model's knowledge cutoff; answer cache; estimate before start; $20 monthly cap; results with "Read with care" assumptions; CSV export.
- Data model: `strategies`, `shadow_decisions`, `shadow_positions`, `shadow_equity`, `backtests`, `llm_cache`. Config: `shadows` and `backtest` blocks. GUI table: Universe, Strategies and Backtest screens.
- Later versions: shadow strategies and backtesting moved forward to PR 13 and 14.

## 2026-10-18 — Notifications

- New "Notifications" section: in-app banners always; browser notifications while the GUI is open; email over SMTP (password stored encrypted as `smtp_password`). 16 events in three severities with default channels; critical events always reach the browser and are never held back; repeat limit 30 minutes; quiet hours 23:00–08:00 Amsterdam (non-critical held and sent as one summary); sending never blocks trading; three failed emails show a banner. Subject and body format defined.
- Settings: new Notifications section (event × channel, repeat limit, quiet hours, SMTP with Send test, recently sent). Data model: `notifications` table; `secrets` gains `smtp_password`. Config: `notifications` block.
- Build plan: part of PR 10 (may be split into its own PR before the paper run).

## 2026-10-18 — Cycle counter on the dashboard

- GUI: new "Cycle counter" paragraph. The this-cycle strip ends with the next cycle (Amsterdam and New York time, live countdown), a segment per cycle of today's session, "Cycle n of 6 today", the cycle number in this run and the next position check; states for market closed, kill switch on and a running cycle. Built in PR 10 with the risk budget card.

## 2026-10-18 — Risk budget card on the dashboard

- GUI: new "Risk budget card" paragraph. Meters for open risk (positions and working entries) vs the 2.5% cap, today's loss vs the 2% daily limit and drawdown vs the 8% kill (amber above 75%, red at the limit), plus room for more trades, sector use, risk per trade with losing streak, shock brake with SPY moves, and day trades. Computed by the risk-gate functions, not in the GUI. Built in PR 10.

## 2026-10-18 — Larger universe (v0.2, PR 11 and 12)

- New "Larger universe (v0.2)" section: `universe.mode` fixed (default, v0.1 unchanged) or screened. Pool of up to 150 symbols, defaults in `backend/universe/pool.csv` (72 symbols). Daily screen at 08:10 New York: history loaded, tradable, price ≥ $10, one share fits in max position, 20-session average dollar volume ≥ $100M; eligible symbols drop out after 3 failed screens in a row; list fixed per session; held symbols always handled. Rank = 20-session return minus SPY's; model calls go to held positions, then rules-passing candidates in rank order, still capped at 10.
- Cycles at scale: multi-symbol bar requests, quotes and news only for model calls, cycle duration warning at 10 minutes, 5-min bars kept for 120 sessions.
- Data model: `universe_pool`, `universe_screens`. Config: `universe.mode`, `pool_file`, `max_pool`, `screen.*`, `rank_by`, `watchlist_rows`; `schedule.bars_5m_keep_sessions`, `schedule.cycle_duration_warn_minutes`.
- GUI: Universe screen; dashboard watchlist filters and search; universe mode and screen thresholds in Settings.
- Build plan: PR 11 (pool and liquidity screen) and PR 12 (cycles at scale).

## 2026-10-18 — Settings redesign and API keys in Settings (PR 10)

- New "API keys" section: Alpaca paper key ID and secret, OpenAI and Finnhub keys are entered in Settings instead of `.env`. Stored encrypted (Fernet, `data/secret.key`, mode 600), write-only API, only the last 4 characters shown, tested before saving, never logged. Replacing Alpaca keys needs the agent paused with no positions or orders. Missing or failing keys keep Resume disabled. Keys still in `.env` are imported once, then ignored.
- GUI: new "Settings screen" paragraph: section list and filter, one row per setting with its default, locked values, changed values highlighted with Reset, an unsaved-changes bar with a required reason, inline validation, and a warning before leaving with unsaved changes.
- Data model: new `secrets` table. `.env.example` no longer has key variables. README and AGENTS.md hard rule 9 updated.
- Build plan: ten PRs; PR 9 Reviews and PR 10 Settings and API keys listed; PR 10 is the last step before the 4-week paper run.

## 2026-10-18 — Pending entries and failed-cycle streak

- Risk gates: working entry orders (submitted, accepted or partially filled, not yet cancelled, expired or rejected) count toward total open risk, cash, max open positions, sector cap and max position size; filled quantity counts once. Entries in one cycle are gated in submission order. Supervised entries awaiting approval don't count until approved.
- Errors: only a completed cycle resets the failed-cycles count; skipped, interrupted and running rows leave it unchanged. Position checks keep their own failure count.

## 2026-10-17 — Reviews (PR 9)

- New "Reviews" section: weekly review (Saturday 10:00), daily briefing (09:00 New York, optional) and a draft verdict per enter/skip decision once its 5-session outcome is in, confirmed or changed by Marc. Code computes the facts; every number in the model's text is checked, with a plain template as fallback. Own $2 monthly cost cap; never affects trading. Missed reviews are written at the next start.
- New tables `reviews` and `verdicts`; new `reviews` block in `config.example.yaml`; prompts weekly.v1, briefing.v1, verdict.v1.
- Later versions: briefings and reviews moved forward from v0.2 to PR 9.

## 2026-10-17 — Paused at startup

- Risk management: new "Paused at startup" paragraph. The agent always starts with new entries paused; reconciliation, position checks, broker-side stops, earnings exits, outcome tracking and reviews of held positions keep running. Resume is one click in the header (no reason). Pause and the kill switch are separate states; both are stored, and a kill switch that was on stays on after a restart.
- GUI header: Pause/Resume button next to the kill switch. README: quick start says the agent starts paused.

## 2026-10-17 — Decision card and drawer like Trading Agent

- GUI, Dashboard: the latest-decision card now always shows the market regime, the earnings date and a three-line summary (failed check and proposed levels for a skip, position and levels for a held symbol, pre-filter reason when no model call was made).
- GUI, Decision detail: rebuilt in Trading Agent's drawer layout (safety gates, signal stack with 1h and daily columns, reasoning with risks and news, entry/stop/target/invalidation tiles, outcome, order status).
- Outcome tracking: for trades, also record whether the stop or the target was touched first.

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
