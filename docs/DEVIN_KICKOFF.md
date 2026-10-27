# Devin kickoff messages

## First message (PR 1)

```
We're building Stock Trading Agent, a paper-only trading agent for US stocks on Alpaca,
from scratch in the empty repo stock-agent.

Read these first, in this order:
1. AGENTS.md (hard rules, commands, conventions)
2. docs/README.md (how to use the spec, PR map, PR template)
3. docs/SPEC.md (what the app does)
4. design/DESIGN.md (how it looks)

Build PR 1 (Skeleton) only:
- Repo layout as in SPEC.md → Architecture and stack.
- FastAPI app with a health endpoint; config loading from .env and
  backend/config.yaml (copy config.example.yaml as the defaults).
- SQLite with SQLAlchemy 2.x and Alembic; first migration with all tables
  from SPEC.md → Data model.
- A clock object injected everywhere; a fake clock for tests.
- React + TypeScript + Vite shell: sidebar, header (PAPER badge, session
  state from the fake clock, equity, today's P&L, regime, kill switch
  button) and empty screens, using
  design/tokens.css. Shared components on a hidden /dev/components page.
- Makefile with install, run, lint, test. CI runs lint and tests with
  sockets blocked.
- A test that fails if the live Alpaca base URL appears anywhere in the repo.

Done when the app starts, the header shows PAPER and the fake clock, and
CI is green. Use the PR template in docs/README.md. Ask in the PR if
anything in the spec is unclear.
```

## v0.2 kickoff: larger universe (PR 11)

```
v0.1 is done; we're starting v0.2 with a larger universe. The 4-week paper run
may still be going on main, so nothing may change its behaviour: everything
new sits behind universe.mode, and the default stays "fixed".

Pull the updated spec and design packages, then read, in this order:
1. docs/CHANGES.md, entry 2026-10-18 "Larger universe"
2. docs/SPEC.md → "Larger universe (v0.2)", Data model (universe_pool,
   universe_screens), Build plan items 11 and 12
3. backend/config.example.yaml → universe and schedule blocks
4. backend/universe/pool.csv (72 default symbols with sectors)
5. design/DESIGN.md and design/screens/Universe.dc.html, Settings.dc.html
   (Trading → Universe and Liquidity screen), Main.dc.html (watchlist toolbar)

Build PR 11 (Pool and liquidity screen) only:
- Alembic migration for universe_pool and universe_screens (new revision).
- Load pool.csv into universe_pool the first time screened mode starts;
  add (symbol + sector, same checks as today), CSV import, remove, limit 150.
- Warm-up: load 260 daily bars and 60 sessions of 5-min bars for new symbols,
  within the shared 150 calls/minute limiter.
- Daily screen at 08:10 New York on trading days (scheduler job, injected
  clock): history, tradable, min price, one share fits max position,
  20-session average dollar volume. Statuses eligible / excluded (with the
  failed check) / warming_up; an eligible symbol drops out only after 3 failed
  screens in a row; rank = 20-session return minus SPY's, ties by dollar
  volume. If the screen fails, keep the previous list and show a banner.
  The list is fixed for the session; held symbols are always handled.
- universe.mode and the screen thresholds in Settings → Trading, as in the
  design (a switch is a new settings version and applies at the next screen).
- Universe screen in the Control group, as in the design; in fixed mode it
  shows the fixed list and points to Settings.
- Don't change cycles yet (that's PR 12): in screened mode PR 11 only
  produces and shows the eligible list.

Tests (fakes, no network): every screen check passing and failing;
hysteresis (fail, fail, pass stays eligible; three fails excludes); a
warming-up symbol becoming eligible; rank order and tie-break; a failed
screen keeps the old list; the one-share check uses equity at screen time;
CSV import with an unknown sector is rejected; fixed mode behaves exactly
as v0.1.

Done when the screen over the 72-symbol pool in simulated mode gives the
expected statuses and fixed mode is unchanged. Use the PR template, save
screenshots in docs/screenshots/pr-11/ next to the design PNGs, and ask in
the PR before guessing.
```

## Dashboard additions and v0.2 plan (19 Oct)

```
Two additions to PR 10 (dashboard), then the v0.2 plan grows by two PRs.
Pull the updated spec and design packages first.

1) PR 10 additions — read docs/SPEC.md → GUI, "Daily briefing card" and
   "Model call counter"; design/screens/Main.dc.html and Main.png.
- Daily briefing card next to the risk budget card: today's briefing text
  (from the PR 9 reviews table), chips for the facts it used, number-check
  status or the plain-template reason, model, prompt version, cost, next
  briefing time. Before 15:00 Amsterdam on a trading day show the previous
  briefing with its date; briefings off → "Briefings are off" + Settings link.
- Model call counter tile: decision calls today vs max (max_calls_per_cycle ×
  cycles today), cost today and this month; hover shows review cost vs its
  cap (and later shadow/backtest cost). Links to the LLM inspector.
- Both read stored data from the backend; no numbers computed in the frontend.
- Tests: briefing before/after 15:00 and with briefings off; counter max on
  an early-close day; costs match the model-call log.

2) After PR 12, v0.2 continues with:
- PR 13 Strategies and shadows — docs/SPEC.md → "Strategies and shadows
  (v0.2)", design/screens/Strategies.dc.html.
- PR 14 Backtesting — docs/SPEC.md → "Backtesting (v0.2)",
  design/screens/Backtest.dc.html.
Build them in that order, one PR each, only after PR 12 is merged. Use the
kickoff below when you start PR 13.
```

## v0.2 kickoff: strategies and shadows (PR 13)

```
PR 13 (Strategies and shadows) is next. Pull the latest spec and design
packages, then read, in this order:
1. docs/CHANGES.md, entry 2026-10-19
2. docs/SPEC.md → "Strategies and shadows (v0.2)", Data model (strategies,
   shadow_decisions, shadow_positions, shadow_equity), Build plan item 13
3. backend/config.example.yaml → shadows block
4. design/screens/Strategies.dc.html and screenshots/Strategies.png

Build PR 13 only:
- Alembic revision for the four new tables.
- A Strategy config (kind llm | rules_only, prompt version, model,
  temperature, overrides of entry rules and code checks). Live = current
  settings version. Risk gates, sizing, PDT and earnings handling are never
  overridable — share the live functions, don't copy them.
- Shadow runner: after each live cycle finishes, run every active shadow
  (max 3) on that cycle's stored input snapshots, with the position part
  rebuilt from the shadow's virtual portfolio; held first, then rules-passing
  candidates in rank order, same 10-call cap. Skip and record "missed" if the
  next live cycle or check is due. Never delay live.
- Virtual portfolio and fills on 5-min bars: entry limit + timeout, stops and
  targets checked at every check/cycle, stop first when both touched in one
  bar, slippage per side, day-trade rule, earnings exit.
- rules_only baseline: stop entry − 2 × ATR(1h), target 2R.
- Shadow cost cap ($5/month, separate); LLM shadows pause at the cap,
  rules-only continue; warning notification.
- The shadow package must not import or receive the broker client; add a test
  that fails if it can.
- Comparison: return, vs SPY, closed trades, average R, max drawdown, model
  cost, "same as live"; disagreements with 5-session outcome or trade result.
  Weekly review facts include the comparison.
- Promote to live: enabled at ≥ 20 closed trades; opens Settings with the
  differences pre-filled as a new version (reason required); archives the shadow.
- Strategies screen in the Learn group, as in the design, with the "New
  shadow" dialog.

Tests (fakes, injected clock, no network): a full simulated session with two
shadows and rules-only alongside live with no delay to any live cycle;
virtual fills (limit touched/not touched, stop vs target same bar); shadows
can't override gates; cost cap pauses LLM shadows only; "same as live"
calculation; promote disabled under 20 trades.

Done when the build-plan "done when" for PR 13 holds. Use the PR template,
save screenshots in docs/screenshots/pr-13/ next to the design PNGs, and ask
in the PR before guessing.
```

PR 14 (Backtesting) uses the "Each later PR" message below.

## Readiness (PR 16)

```
PR 16 (Readiness) is next — start it only after PR 15 is merged. Pull the
latest spec and design packages, then read, in this order:
1. docs/CHANGES.md, entry 2026-10-24 "Readiness (PR 16)"
2. docs/SPEC.md → "Readiness", Notifications (new "Readiness changed" event),
   Data model (readiness_snapshots, readiness_manual, events columns),
   Build plan item 16
3. backend/config.example.yaml → readiness block
4. design/screens/Readiness.dc.html and Settings.dc.html (Readiness section),
   screenshots Readiness.png, Readiness-390.png

Build PR 16 only:
- New Alembic revision: readiness_snapshots, readiness_manual, and on events:
  affects (decision | order | fill | none), resolved_at, resolution
  (auto | dismissed), note. Backfill affects where the event already links
  to a decision, order or fill; otherwise none.
- Readiness evaluator: one function that computes all 15 criteria for the
  current run from stored data (adjusted P&L via the Performance function,
  rules-only shadow via the Strategies comparison, cycles and their
  missed reasons, events, price_stale/price_suspect decisions). Each result:
  value, target, status (pass | not_yet | waiting), sample size, and an
  estimate for counts (pace = closed trades per session; sessions via the
  trading calendar). Run it after each session's close and store a snapshot
  (hard rule 13: no write lock across I/O). The screen reads the latest
  snapshot only.
- Minimum samples exactly as in the spec table: below the minimum the status
  is waiting, never pass or fail.
- Errors: auto-resolve after readiness.error_auto_resolve_minutes without
  recurrence; dismiss with a required note from the banner and the events
  list. Only affects != none counts.
- Manual items (five, fixed by the spec): tick/untick with a required note,
  stored with date, shown in History.
- History: every status change per criterion and every manual change, dated.
- Settings → Readiness: the configurable thresholds as versioned settings;
  zero-tolerance criteria and manual items are not editable.
- Notification "Readiness changed" (Info, browser on, email off) through the
  existing notification service.
- Readiness screen in the Control group, as in the design: verdict banner,
  summary tiles, three groups with value / target / bar / status and hover
  text, manual items, History.
- Nothing may enable live trading: no live switch, no live base URL (the
  existing test must still pass).

Tests (fixtures, injected clock, no network): every criterion pass / not_yet
/ waiting at its boundaries (e.g. 29 vs 30 closed trades, 97.9% vs 98%
cycles); calibration with groups under 10 skipped; beats-rules-only waiting
when the rules-only shadow started after the run; estimates from pace and
calendar; error auto-resolve after 60 min and dismiss-with-note; a new run
resets evidence; snapshot written after the close and survives a restart;
the screen makes no evaluation per request.

Use the PR template, save screenshots in docs/screenshots/pr-16/ next to the
design PNGs, and ask in the PR before guessing.
```

## Dashboard and interaction polish (PR 17)

```
PR 17 (Dashboard and interaction polish) — thanks for the dashboard UX
review; it's now spec and design. It doesn't depend on PR 16, so it can go
before or after it, but not in the same PR. Pull the latest spec and design
packages, then read:
1. docs/CHANGES.md, entries 2026-10-24 "Dashboard and interaction polish"
   (spec) and "UX review: scale, states and interaction" (design)
2. docs/SPEC.md → GUI: "Dashboard at scale and off-hours", "Interaction and
   states"; Build plan item 17
3. design/DESIGN.md → Interaction rules; design/tokens.css (new interaction
   tokens and shared classes at the end)
4. design/screens/Main.dc.html and States.dc.html (Market closed, Card
   states, Toasts, Pending approval) with their screenshots

Build PR 17 only, in this order (one commit per step):
1. Scale: remove the symbol button row; held-symbol buttons + Find symbol
   in the chart header; ?symbol= in the URL (default first held position,
   else first watchlist row). Feed grouped per cycle id: changes always as
   own rows, the rest grouped by (final action, reason) with symbols (first
   8, "+n more") and a count; current cycle open, earlier collapsed to
   changes; single row → decision, grouped row → LLM inspector filtered to
   cycle + action; rows ≥ 44 px with hover.
2. States: loading / empty / error on every data card (no empty text before
   a successful load; errors show what failed + Retry, stale data kept and
   marked); page banner when ≥ 2 cards fail or the API is unreachable,
   retrying every 15 s; chart errors are not "No bars loaded".
3. Session day (backend): "today" = current or last NY session, not
   Amsterdam midnight; feed title with the session date off-hours; model-call
   tile counts that session; latest-decision fallback (why not called this
   cycle from the snapshot, else most recent decision with date); countdown
   > 24 h shows day and time only.
4. Quick wins: unrealized P&L coloured; NY time on hover for every time;
   rank column + hint only in screened mode; earnings tooltip and any copy
   naming a setting read it from settings; tiles link (Positions, Model
   calls, Since start); approval shows its expiry; backend sends numbers,
   frontend formats with U+2212.
5. Accessibility: global CSS from tokens.css (focus-visible ring, .sa-row,
   .sa-tile hover, skeleton); remove outline:none; countdown out of any live
   region; filters as aria-pressed toggles, real tabs with arrow keys,
   watchlist rows as buttons in a list; mobile sidebar overlay with Escape,
   focus trap and focus return; critical toasts persist (role alert); toasts
   deduped per notification event, max 3, below the header.
6. Polish: latest request wins for symbol/chart/dashboard fetches
   (AbortController or a request token); document.title shows the state.

Tests: Playwright with the 71-symbol screened fixture (no symbol wall, feed
fits in collapsed cycles); a Saturday fixture shows Friday's session;
keyboard-only walkthrough of Dashboard, Settings and Strategies with visible
focus; loading/empty/error per card via mocked responses; API down → banner;
rapid symbol switching shows the last symbol's chart; critical toast stays,
info toast closes after 10 s; one toast per event across channels.

Use the PR template, screenshots in docs/screenshots/pr-17/ next to the
design PNGs (Main, Main-390, States). List any remaining design differences.
```

## PR 18 — LLM call history (after PR 17)

```
PR 18 (LLM call history). Start this only after PR 17 (Dashboard and
interaction polish) is merged: it builds on PR 17's interaction rules,
card states and URL handling. Pull the latest spec and design packages,
then read:
1. docs/CHANGES.md, entry 2026-10-24 "LLM call history (PR 18)" (spec)
   and "LLM inspector: all calls with filters" (design)
2. docs/SPEC.md → "LLM call history" (the whole section), Data model
   (llm_calls), GUI table (LLM inspector row), Build plan item 18
3. backend/config.example.yaml → inspector block
4. design/screens/Inspector.dc.html with Inspector.png, Inspector-error.png
   and Inspector-390.png

Build PR 18 only, in this order (one commit per step):
1. Storage: new Alembic revision for llm_calls with the four indexes (never
   edit an old migration). Write a row for every request actually sent by
   live decisions, shadows and reviews, in the same transaction as the
   linked row and after the call returned (hard rule 13). Store latency,
   error type, HTTP status, scrubbed error message and OpenAI request id;
   never headers or keys.
2. Outcome: a single pure function, unit-tested per reason. It gives
   success, warning or error plus a list of reasons. Warning reasons:
   changed_by_code, input_incomplete, number_check_failed, slow. Error
   reasons: timeout, api_error, invalid_output, refusal. It is set once at
   write time. A size reduced by a gate is not a warning.
3. Backfill in the migration from decisions, shadow_decisions and reviews,
   using the same function. Unknown fields stay null and the row is marked
   backfilled. Calls that were never sent get no row.
4. API: GET /api/llm-calls (filters, keyset cursor on (sent at, id), limit
   ≤ 100), /summary (totals, per-session buckets, option counts),
   /{id} (prompt rebuilt from the prompt file + stored input; hash
   mismatch reported, not hidden), /export.csv (metadata only). Session
   dates are New York dates. Numbers as numbers.
5. Frontend: rebuild the LLM inspector per the design.
   - Filters synced to the URL; defaults Last session, All outcomes,
     Live decisions + Reviews.
   - Summary with bars per session (a click sets the period).
   - List grouped per session with Load more.
   - Details with the outcome block and the Error block.
   - "More filters" at phone width; Copy link.
   - PR 17 rules apply: toggles with aria-pressed, card states, latest
     request wins.
6. Links: the dashboard model-call tile opens Last session and live
   decisions. A grouped feed row opens its cycle and action. The decision
   drawer's "Open model call" opens ?call=.

Tests:
- Outcome function, every reason, including precedence (error beats
  warnings).
- Backfill on a fixture DB: the expected outcome for each call, and no rows
  for no_calls.
- A timeout and an HTTP 503 give error rows with details and no raw
  response.
- Each filter and combination, including Custom dates across a weekend and
  a Saturday weekly review.
- Paging without gaps or duplicates while new calls arrive.
- List and summary under 300 ms on a 100,000-call fixture.
- CSV has no prompt or response text, and the fake keys never appear
  anywhere.
- Playwright: filters survive reload via the URL, ?call= opens the call,
  keyboard-only walkthrough.

Use the PR template, screenshots in docs/screenshots/pr-18/ next to the
design PNGs (Inspector, Inspector-error, Inspector-390). List any
remaining design differences.
```

## PR 19 — Tail metrics (measure only)

```
PR 19 (Tail metrics). This PR measures only: don't change exits, targets or
prompts. It doesn't depend on PR 16–18, so it can go before or after them,
but always as its own PR. Pull the latest spec and design packages, then
read:
1. docs/CHANGES.md, entry 2026-10-24 "Tail metrics (PR 19)" (spec) and
   "Performance: Winners and tails" (design)
2. docs/SPEC.md → "Tail metrics" (whole section), Data model (positions),
   Outcome tracking (best price while open), Build plan item 19
3. backend/config.example.yaml → performance.tail
4. design/screens/Performance.dc.html with Performance.png and
   Performance-390.png (Winners and tails card)

Build PR 19 only, in this order (one commit per step):
1. Migration: a new revision adding after_exit_best_price, after_exit_best_at,
   after_exit_close_price, after_exit_status (pending, done, unavailable)
   and after_exit_done_at to positions.
2. After-exit job, once per trading day after the close (after the readiness
   snapshot):
   - For every closed position whose 5th session after the exit has closed:
     take the highest high and the 5th session's close from 5-min bars after
     the exit fill, split-adjusted.
   - Fetch missing bars through the shared Alpaca limiter. Retry the next
     day; after 3 failures, mark the position unavailable.
   - Write once, never update.
   - Hold no write lock during a fetch (hard rule 13).
   - The first run fills existing closed positions.
3. One pure tails function in the performance module (current run, adjusted,
   1R = initial risk), unit-tested against hand calculations. It gives:
   - the R distribution (each bucket includes its lower edge);
   - largest win and loss;
   - the top ceil(20%) trades' share of gross profit;
   - net without the best trade;
   - winners kept;
   - per exit reason: median best and median close after exit, and the
     share of target exits with best after exit ≥ 1R.
   Pending trades are excluded and counted; adopted positions without an
   initial stop are excluded and counted.
4. GET /api/performance/tails?run= returning numbers (no formatted
   strings).
5. The "Winners and tails" card per the design:
   - small-sample banner below min_trades;
   - R distribution bars with an aria-label;
   - tiles with their definitions on hover;
   - After the exit with the per-trade table and "pending" rows;
   - card states and focus per the PR 17 rules (if PR 17 isn't merged yet,
     follow DESIGN.md → Interaction rules for this card).

Tests:
- Fixture run with known trades: every metric matches the hand calculation,
  including a loss below −1R, a trade still pending, a split during the
  after-exit window, and a symbol whose bars aren't cached (fetched through
  the limiter, mocked).
- A fetch failing 3 times ends as unavailable.
- A run with no winners hides the top-20% tile.
- Under 20 trades the banner shows; at 20 it doesn't.
- Values never change once done.
- No network in tests.

Use the PR template, screenshots in docs/screenshots/pr-19/ next to the
design PNGs (Performance, Performance-390). List any remaining design
differences.
```

## PR 20 — Logging, error handling and shutdown (after PR 17)

```
PR 20 (Logging, error handling and shutdown). Start after PR 17 is merged.
If PR 18 or 19 are already merged, include their code in the audit. Pull
the latest spec and design packages, then read:
1. docs/CHANGES.md, entry 2026-10-24 "Logging, error handling and shutdown
   (PR 20)" (spec) and "Logs screen and shutdown states" (design)
2. docs/SPEC.md → "Logging, error handling and shutdown" (whole section),
   GUI table (Logs, States), Data model intro (log index) and log_reviews,
   Readiness (the two log criteria and "Errors in the log"), Build plan
   item 20
3. AGENTS.md → hard rules 13, 15 and 16; backend/config.example.yaml →
   logging and storage.shutdown_grace_seconds
4. design/screens/Logs.dc.html, States.dc.html (Shutdown and restart,
   Review a recurring error) and Readiness.dc.html (Operations) with their
   screenshots

Build PR 20 only, in this order (one commit per step):
1. Audit, no code changes. Write
   docs/analysis/<date>-error-handling-audit.md:
   - every try/except, background task, callback and thread, with
     file:line, what happens on failure today, and the fix;
   - per component, its current logging and the INFO and DEBUG lines it
     needs so a cycle can be traced from schedule to order;
   - what Ctrl+C does today during a cycle, an order submission, a
     backtest and the 03:00 backup, tested in simulated mode.
   Anything that would change trading behaviour beyond logging and
   shutdown goes in a "Questions" list, not into code. Push this commit
   first and post a summary in the PR.
2. Logging core: JSON-lines file handler behind a QueueHandler and
   QueueListener; console handler at INFO; rotation with gzip, max files
   and keep days; scrubbing filter on every handler (stored keys,
   key-like strings, Authorization headers); context variables for cycle,
   check, job and request ids added to every line; error_id on ERROR and
   CRITICAL; uvicorn and library loggers routed to the same handlers;
   captureWarnings. The backtest child writes its own file in the same
   format.
3. No silent failures:
   - enable ruff E722, BLE001, S110, TRY400 and TRY203, and fix every hit
     per the audit;
   - add the global hooks: sys.excepthook, threading.excepthook, the
     asyncio exception handler, the scheduler error listener;
   - a spawn() helper for all background tasks, plus a test that fails
     on bare asyncio.create_task outside it;
   - an API exception handler returning error_id;
   - POST /api/logs/client with a rate limit, window.onerror,
     unhandledrejection and a React error boundary showing the error_id.
4. Log lines: add DEBUG and INFO lines in every component per the level
   table (scheduler, cycle, data, rules, model, risk, orders,
   reconciliation, positions, notifications, reviews, universe, shadows,
   backtest, settings, keys, jobs, db), with stable event codes. Never
   log keys, prompts or model responses.
5. Graceful shutdown, steps 1–7 of the spec:
   - signal handling for SIGINT and SIGTERM on macOS, Linux and Windows;
     make dev and make run both shut down this way;
   - scheduler paused; 503 for writes while shutting down;
   - grace wait; order submission shielded from sending until the answer
     is stored;
   - cycles stored as interrupted; backtest child stopped by PID;
   - clients closed; sessions closed; WAL checkpoint; engine disposed;
     log closed last;
   - a second Ctrl+C skips the wait;
   - data/run.lock with a heartbeat, unclean-stop detection with
     quick_check, and startup refused while another live pid holds the
     lock;
   - a "shutting down" WebSocket state.
6. Log index and API: data/logs/logs.db written in batches by the
   listener thread (never the trading DB; failures only to file and
   stderr), FTS on the message, fingerprints, pruning with the files,
   make logs-reindex; GET /api/logs, /summary, /{id}/around,
   /export.jsonl; WebSocket topic logs.
7. Frontend:
   - the Logs screen per the design: level toggles with warning and up
     by default and the "Show debug and info" switch, the URL and per-
     browser memory, period and filters, per-day bars, recurring
     problems, grouped log with an expandable line (traceback, stored
     line, surrounding lines), follow new lines, phone layout;
   - the Logs sidebar item;
   - the shutting-down pill and banner, and the unclean-run banner;
   - links from events and notifications to Logs.
8. Readiness from the log:
   - a log_reviews table (new Alembic revision, in the trading DB);
   - a Review action on ERROR and CRITICAL recurring problems (Expected or
     Fixed, with a required note) and its dialog;
   - two Operations criteria in the readiness snapshot, counted from the
     log index: "Errors in the log" (0 unreviewed ERROR problems in the
     last 10 sessions; a Fixed problem that recurs is unreviewed again)
     and "Critical lines in the log" (0 in the last 20 sessions, no review
     possible). An unavailable or incomplete index means not yet, never
     pass. Reviews appear in Readiness history.
   If PR 16 isn't merged yet, stop and ask instead of building the
   readiness part.

Tests:
- Each ruff rule catches a sample.
- An exception in a task, thread, scheduler job, API route and the
  frontend each ends up in the log file with a traceback and an error_id.
- Keys, including fake ones, never appear in the file or the index.
- One simulated cycle can be reconstructed from its id at DEBUG.
- SIGINT during a cycle, during an order submission (the fake broker
  delays its answer), during a backtest and during the backup: in each
  case integrity_check is ok, there are no half-written rows, the cycle
  is completed or interrupted, and the order's state is recorded or
  flagged for reconciliation.
- A second SIGINT skips the wait; a stale run.lock triggers the unclean
  banner; a live pid in run.lock refuses startup.
- An index failure keeps the file logging.
- Logs API filters and paging.
- Readiness: an unreviewed error in the window keeps "Errors in the
  log" at not yet; Expected keeps it passing when the error recurs, Fixed
  doesn't; one CRITICAL line fails its criterion; a missing index never
  passes.
- Playwright: Logs opens on warning and up, the switch shows debug and
  info, ?levels= survives a reload, opening a line shows the traceback
  and surrounding lines.
- No network in tests.

Use the PR template. Screenshots go in docs/screenshots/pr-20/ next to the
design PNGs (Logs, Logs-390, States, Readiness). List remaining design differences and
the audit's open questions.
```

## PR 21 — Decision v2: code-built options

```
PR 21 (Decision v2: code-built options). Live stays on decision.v1 in this
PR: v2 runs only in replay, shadows and backtests until Marc promotes it at
a new run. It needs PR 13 (shadows) and PR 15 (integrity fixes) merged, and
is independent of PR 16–20. Pull the latest spec and design packages, then
read:
1. docs/CHANGES.md, entry 2026-10-25 "Decision v2: code-built options
   (PR 21)" (spec) and "Decision drawer for decision.v2" (design)
2. docs/SPEC.md → "Decision v2: code-built options" (whole section),
   Decision layer (code checks, no_call reasons), Strategies and shadows,
   Backtesting → Model calls, Data model (decisions, shadow_decisions),
   Readiness (Model group), Build plan item 21
3. docs/analysis/2026-10-22-live-data-and-llm.md §3 and
   2026-10-22-bugs-and-faulty-logic.md §8–9 (why)
4. backend/prompts/decision.v2.md (new; copy it into the repo unchanged)
   and backend/config.example.yaml → decision.options, decision.replay
5. AGENTS.md hard rule 8; design/screens/DecisionV2.dc.html with
   DecisionV2.png

Before you start: if backend/prompts/decision.v2.md already exists in the
repo (for example made for a shadow), stop and ask; don't overwrite it.

Build PR 21 only, in this order (one commit per step):
1. Level finder, a pure function: confirmed 1h and daily swing highs and
   lows (swing_strength bars each side, lookbacks from config), prior
   session high, low and close, session high and low so far, session
   VWAP; at most 8 levels each side of the price, ids L1… from the highest
   price down. Unit tests on fixture bars, including unconfirmed newest
   bars and early-close sessions.
2. Option builder, a pure function:
   - entry options from the stop reference (lowest of market price,
     plausible ask, bar close), the limit (+0.05%) and ATR(1h); level
     stops with the buffer plus ATR stops; kept only within the stop band
     (the strategy's overrides apply); merged within dedupe_atr, level
     first; max 3 stops;
   - targets from levels above the limit and R multiples, kept at R/R ≥
     the minimum, merged the same way, with levels_in_way; max 6 options
     lettered A–F;
   - held stop options T1–T3 (swing low above the current stop,
     break-even, trail) under the existing tighten rule, with
     locks_profit and r_at_stop.
   No options means no model call: no_call with reason no_options and the
   reason text. Property test: no offered option ever breaks the stop band
   or minimum R/R.
3. Snapshot and schema: levels, constraints, options or stop_options
   added for v2 strategies; the strict v2 JSON schema (option_id, no price
   fields, levels_used); prompt registry by version, so a strategy's
   prompt version picks the schema and the builder. v1 stays byte-for-byte
   unchanged (test with a stored v1 snapshot).
4. Checks:
   - an unknown or missing option_id is invalid_output (skip or hold);
   - the chosen option goes through the same code checks and risk gates;
   - at order time (live and approvals) the stop band and R/R are
     rechecked from the final limit with the stop and target fixed; a
     failure fails the Fresh quote gate with its reason; never pick
     another option or move a level;
   - explanation check: level ids exist; decimals match the snapshot or
     the chosen option after rounding; a failure is stored with its
     tokens, the decision still stands, and llm_calls gets the warning
     unsupported_claim (if PR 18 is merged).
   Readiness proposal criterion counted per the spec for v2 decisions (if
   PR 16 is merged).
5. Storage: a new Alembic revision adding levels, options, chosen_option,
   explanation_check and options_version to decisions and
   shadow_decisions; the no_options reason everywhere no_call reasons
   are listed or labelled.
6. Shadows and backtests: decision.v2 selectable in New shadow and in the
   backtest strategy picker; the same builder in backtests, cut at the
   decision time.
7. Replay: make replay PROMPT= FROM= TO= [MODEL=] [--yes]
   - rebuild levels and options from the cached bars at each stored
     snapshot's cycle time; call the model;
   - score the chosen option's first touch and R over the next 5 sessions
     from bars; run rules-only on the same snapshots;
   - write data/replays/<id>/results.jsonl and report.md (enter rate,
     invalid choice rate, explanation failures, choices by basis,
     agreement with v1, first touch and R);
   - estimate first and refuse above decision.replay.confirm_above_usd
     without --yes; count the cost toward the backtest cap; never write to
     live tables.
8. GUI: the decision drawer for v2 per DecisionV2.dc.html (options table,
   levels list, explanation check, option gates); v1 decisions unchanged;
   the dashboard latest-decision card shows the chosen option for v2.

Then, without changing live:
- Run make replay with decision.v2 on every candidate snapshot of the
  current run and attach report.md to the PR.
- Tell me which shadow to archive to make room for "C · Options
  (decision.v2)" (I'll confirm), then start it.

Tests:
- Level finder and builder fixtures, including: no options, merged stops
  and targets, a level exactly on the band edge, levels in the way, and
  held options with and without locks_profit.
- Property test over random bars: every offered option meets the rules.
- Invalid option id → skip or hold.
- Explanation check pass and fail cases (unknown id, invented decimal,
  whole numbers ignored).
- Order-time recheck failure → Fresh quote gate with reason, and no other
  option is picked.
- A v1 snapshot and prompt are unchanged.
- A v2 shadow runs a full simulated session.
- The replay estimate and refusal, and no writes to live tables.
- No network in tests.

Use the PR template, screenshots in docs/screenshots/pr-21/ next to the
design PNGs (DecisionV2). List remaining design differences.
```

## PR 22 — Insights

```
PR 22 (Insights). Measures only: nothing on this screen changes trading.
It doesn't depend on PR 16–21, but uses PR 19 (after-exit data) and PR 21
(stop and target basis) when they are merged; leave those parts empty
otherwise. Pull the latest spec and design packages, then read:
1. docs/CHANGES.md, entry 2026-10-26 "Insights (PR 22)" (spec) and
   "Insights" (design)
2. docs/SPEC.md → "Insights" (whole section), Strategies and shadows
   (entry_cycles override), Data model (insights_snapshots), GUI table,
   Build plan item 22
3. backend/config.example.yaml → insights
4. design/screens/Insights.dc.html with Insights.png and Insights-390.png

Build PR 22 only, in this order (one commit per step):
1. One pure insights function in the performance module, per strategy and
   period (this run, last 20 sessions, all runs), adjusted P&L, 1R =
   initial risk:
   - headline numbers: expectancy with a 90% range; edge over SPY at the
     same exposure; beta and correlation from 20 sessions;
     overnight vs in-session P&L from daily bars and fills; gaps through
     the stop; model cost per trade;
   - the funnel with losses per step and per reason;
   - slices for all 16 dimensions with the sample statuses (finding at
     30+ and 2 sigma vs the rest; early signal at 10–29; hidden under
     10 and counted), and rule rows (15:46);
   - calibration per 0.1 bucket (target before stop);
   - execution (fill rate, slippage in bps, deferred-exit cost, earnings
     exits, dividends);
   - skips scored with the rules-only geometry (missed, avoided, neither,
     baseline R).
   Unit-test every number against hand calculations on a fixture run.
2. A daily job after the readiness snapshot writes insights_snapshots (new
   Alembic revision), one per strategy and period. No write lock across
   any fetch (hard rule 13).
3. API: GET /api/insights?strategy=&period= and
   /api/insights/trades?strategy=&period=&dimension=&slice=, numbers
   only.
4. Strategy override entry_cycles (new shadow dialog, strategies
   comparison, and the engine honours it); "Test in a shadow" on a
   finding opens New shadow with the override filled in, only for
   dimensions that map to an override.
5. Weekly review facts: new findings and findings that stopped holding.
6. Frontend per the design: strategy and period toggles; headline tiles
   with definitions on hover; findings; funnel; skipped setups; slices
   with the dimension picker and range bars; calibration; execution;
   overnight; before-data state under 10 trades; Insights in the Learn
   sidebar group; card states and focus per the interaction rules.

Tests:
- Fixture run with known trades and bars: every headline number,
  funnel count, slice and calibration bucket matches the hand
  calculation.
- Sample statuses switch at 10 and 30.
- A planted difference becomes a finding; random noise across 16
  dimensions produces at most the expected chance findings with a fixed
  seed.
- Overnight split with a gap day.
- A gap through the stop is counted with its extra R.
- A skip is scored missed, avoided or neither.
- entry_cycles blocks entries in excluded cycles in a shadow.
- The rules-only strategy shows no calibration or skips.
- No network in tests.

Use the PR template, screenshots in docs/screenshots/pr-22/ next to the
design PNGs (Insights, Insights-390). List remaining design differences.
```

## PR 23 — Sign-in and access

```
PR 23 (Sign-in and access). Built and tested locally; nothing is deployed
in this PR. It doesn't depend on PR 16–22. Pull the latest spec and design
packages, then read:
1. docs/CHANGES.md, entry 2026-10-26 "VPS deployment (PR 23 and 24)"
   (spec) and "Sign-in and server" (design)
2. docs/SPEC.md → "VPS deployment": Sign-in, Secrets on the server,
   Moving from local to the VPS (instance id), Restarts; Data model
   (users, sessions, sign_in_attempts); Notifications (new rows); API
   keys; Build plan item 23
3. AGENTS.md → hard rule 10 (rewritten); backend/config.example.yaml →
   auth; .env.example (SA_AUTH, SA_PUBLIC_URL, SA_TRUSTED_PROXY,
   SA_INSTANCE_ID)
4. design/screens/Login.dc.html, Settings.dc.html (Access section),
   States.dc.html (Access and server; Confirm it is you) and the sidebar
   Sign out, with their screenshots

Build PR 23 only, in this order (one commit per step):
1. Startup guard: SA_AUTH (off | required). Refuse to start with
   SA_AUTH=off when SA_HOST isn't 127.0.0.1 or ::1, with a clear
   message. Trust X-Forwarded-For only from SA_TRUSTED_PROXY.
2. Tables and CLI: users, sessions, sign_in_attempts (new Alembic
   revision; attempts pruned after 90 days); make create-user creates or
   resets the single account (password at least 14 characters, Argon2id
   via argon2-cffi), sets up TOTP (shows the otpauth URI and a QR code in
   the terminal) and prints 10 recovery codes once. TOTP secrets are
   encrypted with the same Fernet key as the API keys.
3. Sign-in flow: password step, then TOTP or recovery code; session token
   in an HttpOnly, Secure (except on loopback), SameSite=Strict cookie,
   hash stored; idle expiry (auth.idle_hours) and absolute expiry
   (remember_days); generic error messages; per-address lockout and the
   global hourly limit; sign-out, sign out others, sign out everywhere.
4. Protection: one dependency on every API route and the WebSocket (only
   /healthz and the sign-in endpoints are open); a test walks the FastAPI
   route table and fails on any unprotected route. CSRF double-submit
   token on writes; WebSocket Origin check. "Confirm it is you"
   (password or code, valid auth.confirm_minutes) on: Stop & flatten,
   Resume after the kill switch, Reset paper account, Start new run,
   Promote strategy, replacing a key, and changing the password,
   authenticator or recovery codes.
5. Instance id: SA_INSTANCE_ID in every client order id; reconciliation
   treats an open order or fill with another instance id as a critical
   mismatch (kill switch on, banner, notification). Existing orders
   without an id are left as they are.
6. Notifications: New sign-in, Failed sign-ins (lockout), Another instance
   (critical).
7. Frontend: the sign-in screen (three states), a session-expired redirect
   that returns to the same page, the Confirm-it-is-you dialog wired to
   those actions, Settings → Access (devices, password change,
   authenticator and recovery codes, idle setting), and Sign out in the
   sidebar. With SA_AUTH=off, Access shows "Sign-in off (local only)" and
   none of the sign-in UI appears.

Tests:
- The route-table walk: every route except /healthz and sign-in returns
  401 without a session; the WebSocket closes without a session or with a
  foreign Origin.
- Wrong password and wrong code give the same message; lockout after 5
  failures; the global limit.
- Idle and absolute expiry with the injected clock.
- A recovery code works once only.
- A risky action without a fresh confirmation returns 403; with one it
  works, and stops working after 5 minutes.
- CSRF: a write without the token is refused.
- Startup refusal with SA_AUTH=off on 0.0.0.0.
- An order with another instance id turns the kill switch on.
- Passwords, TOTP secrets, codes and session tokens never appear in logs
  (the PR 20 scrubber, or a test if PR 20 isn't merged).
- No network in tests.

Use the PR template, screenshots in docs/screenshots/pr-23/ next to the
design PNGs (Login, Login-390, Settings, States). List remaining design
differences.
```

## PR 24 — VPS deployment

```
PR 24 (VPS deployment). Needs PR 20 (graceful shutdown) and PR 23
(sign-in) merged. Don't provision, deploy to or touch any real server in
this PR: everything is scripts, files and tests, run locally or in CI. I
run the scripts on the server myself. Pull the latest spec and design
packages, then read:
1. docs/CHANGES.md, entry 2026-10-26 "VPS deployment (PR 23 and 24)"
2. docs/SPEC.md → "VPS deployment" (whole section), Logging, error
   handling and shutdown (grace period, run.lock), Build plan item 24
3. AGENTS.md → hard rules 10, 12, 14 and 16; backend/config.example.yaml
   → ops, storage
4. design/screens/Settings.dc.html (Server section) and States.dc.html
   (Access and server), with their screenshots

Build PR 24 only, in this order (one commit per step):
1. Container:
   - a multi-stage Dockerfile: the frontend is built with IBM Plex fonts
     bundled, so there are no Google Fonts requests;
   - a non-root user, read-only root filesystem, everything writable
     under /data, TZ=Europe/Amsterdam;
   - one Uvicorn worker; GET /healthz returns the version only.
2. Compose: app (internal network only, no published port, restart
   unless-stopped, stop_grace_period 90s, memory limit) and caddy (80 and
   443, automatic HTTPS for SA_PUBLIC_URL's host, the spec's security
   headers, reverse proxy to app). Add docker-compose.local.yml so the
   same stack runs on a laptop for testing.
3. Restarts: ops.resume_after_clean_restart (auto = on when
   SA_AUTH=required). Resume only when trading was running before, the
   previous shutdown was clean, the kill switch is off and reconciliation
   finds no differences; banner and notification either way.
4. Scripts in deploy/ (bash, set -euo pipefail, idempotent, with --help):
   - provision.sh: user and SSH keys, no password or root login, ufw
     22/80/443, fail2ban, unattended-upgrades with the Sunday 05:00
     reboot window, chrony, Docker;
   - deploy.sh <tag>: market-hours guard (refuse 09:30–16:00 New York
     unless --emergency); backup; pull; migrations in a one-off
     container; restart; wait for /healthz and a clean startup
     reconciliation; on failure within 2 min, restore the pre-deploy
     backup if a migration ran, start the previous image, report;
   - restore.sh <snapshot>: from the local or offsite copy, with
     integrity_check.
   Stop only containers by name from our compose project; never kill
   processes by name (hard rule 12).
5. CI: build and push a versioned image to a private registry on every
   tag; run tests in the image.
6. Backups: offsite with restic to S3-compatible storage (credentials and
   password from the secrets table), after the 03:00 local copy; keep 30
   daily and 12 weekly; monthly restore test into a temp folder with
   integrity_check; Back up now and Download latest backup (a
   confirmation is needed for the download).
7. Monitoring: heartbeat ping to ops.heartbeat_url after every cycle and
   position check; checks for clock offset, disk use, certificate expiry,
   backup age and restore test; the notifications from the spec.
8. Settings → Server per the design (read-only plus the two backup
   buttons), shown when SA_AUTH=required.
9. docs/DEPLOY.md, a runbook I can follow alone:
   - choosing a VPS and DNS;
   - provision; first deploy; make create-user;
   - entering keys;
   - setting up offsite storage and the heartbeat service;
   - moving the local database and secret.key (stop locally first; set
     SA_INSTANCE_ID per copy);
   - deploy and rollback; restore; rotating the backup password.

Tests:
- The container runs as non-root with a read-only root.
- /healthz says nothing beyond the version.
- docker stop during a simulated cycle waits for the graceful shutdown
  and leaves integrity_check ok.
- deploy.sh refuses during market hours (injected clock) and rolls back
  on a failing health check (fake image).
- restore.sh round trip.
- The resume rules: clean and running resumes; unclean, kill switch or
  mismatch stays paused.
- Heartbeat is called once per cycle and check (mocked).
- Each Server check and its notification thresholds.
- Security headers present via a local compose run in CI.
- No network in unit tests.

Use the PR template, screenshots in docs/screenshots/pr-24/ (Settings
Server section, States server banners). List anything in docs/DEPLOY.md
you couldn't verify without a real server.
```

## PR 25 — Error pages and API errors

```
PR 25 (Error pages and API errors). Independent of PR 16–22. The Caddy
part needs PR 24; if PR 24 isn't merged yet, commit the static pages in
deploy/errors/ and leave the wiring to PR 24 (say so in the PR). Pull the
latest spec and design packages, then read:
1. docs/CHANGES.md, entry 2026-10-26 "Error pages and API errors (PR 25)"
   (spec) and "Error pages" (design)
2. docs/SPEC.md → "Error pages and API errors" (whole section),
   Interaction and states (backend banner), Logging, error handling and
   shutdown (error_id, POST /api/logs/client), Build plan item 25
3. design/screens/Errors.dc.html with Errors.png and Errors-390.png;
   copy the texts as written

Build PR 25 only, in this order (one commit per step):
1. Backend:
   - one exception handler set producing {"error": {code, message,
     error_id?, fields?}} for every /api error: HTTPException,
     validation, and unhandled (500 with error_id, logged with the
     traceback);
   - a code registry (an enum) so codes are stable;
   - every non-/api GET path serves the frontend; unknown /api paths
     return 404 JSON.
2. Frontend API client: one place that maps status to behaviour per the
   spec's table:
   - 401: sign-in, then back to the same page;
   - 403 confirm_required: the dialog, then retry once;
   - 409: a reload offer;
   - 422: field messages;
   - 429: Retry-After;
   - 500: a toast with error_id and Open in Logs;
   - 503: the backend banner.
3. Pages: not found for routes and for records (each record screen
   handles 404 with its own title and search link); an error boundary
   around every screen (reporting to /api/logs/client and showing the
   returned error_id); the first-load "The app isn't answering" page with
   a 15 s retry; the offline banner.
4. Static pages: deploy/errors/502.html and 503.html, self-contained
   (inline CSS and SVG, system fonts, no external requests, a small
   reload script). They show no version, host, commands or data. Also
   JSON bodies for /api during an outage (app_unavailable, updating).
5. Caddy and scripts (if PR 24 is merged): handle_errors for 502/503/504
   by path (HTML for pages, JSON for /api); a maintenance flag file that
   deploy.sh and restore.sh set before stopping the app and clear after
   the health check (also cleared on rollback), with Retry-After: 15.

Tests:
- A route walk: force each error path (404, 422, 500 via a test-only
  failing route) and assert the JSON shape with no traceback, path or
  HTML.
- Frontend unit tests for every status mapping.
- Playwright: an unknown route shows the 404 page; a missing decision id
  shows the record page; a forced render error on one screen leaves the
  sidebar and other screens working and shows an error id; the first load
  with the API down shows the retry page; going offline shows the
  banner.
- Static pages pass a check for no external URLs.
- With the compose stack and the app stopped (CI), / returns the 502 page
  and /api/x returns JSON; with the flag set, 503 with Retry-After.
- No network in unit tests.

Use the PR template, screenshots in docs/screenshots/pr-25/ next to the
design PNGs (Errors, Errors-390). List remaining design differences.
```

## PR 26 — Day-trade rule retired (do first)

```
PR 26 (Day-trade rule retired). Do this first in the queue: live trading
currently defers exits for a rule that no longer exists. FINRA replaced the
pattern day trader rule on 4 June 2026 (Regulatory Notice 26-10); Alpaca
applied it from that date and removed pattern_day_trader, daytrade_count
and daytrading_buying_power from its API by 6 July 2026. Pull the latest
spec and design packages, then read:
1. docs/CHANGES.md, entry 2026-10-27 "Day-trade rule retired (PR 26)"
   (spec) and "Day-trade rule retired" (design)
2. docs/SPEC.md → Alpaca integration → "Day trades (rule retired)"; Risk
   table (Re-entry); Kill switch; GUI table (Positions); Risk budget card;
   Notifications; Strategies and shadows; Build plan item 26
3. AGENTS.md hard rule 17; backend/config.example.yaml →
   risk.reentry_same_session, notifications
4. design/screens/Positions, Main, Decision, Settings, States and Insights
   with their screenshots

Before you start: tell me what the code does today with the old fields
(does anything read daytrade_count or pattern_day_trader, and what
happens now that they're gone?) and whether any exit is currently
deferred. Don't change live behaviour mid-session (hard rule 14).

Build PR 26 only, in this order (one commit per step):
1. Remove: the same-day exit deferral (a model exit on the entry day is
   carried out like any other); the day-trade guard gate and its
   config, notification and banner; the deferred-exit field in the input
   snapshot; the Stop & flatten day-trade warning; every read of the
   three removed Alpaca fields. Exits deferred before this PR are not
   replayed. A test fails on any reference to those fields.
2. Add the re-entry gate: no new entry in a symbol the agent sold or that
   was stopped out earlier in the same New York session
   (risk.reentry_same_session: false), for live and shadows; shown as a
   gate in the decision drawer and as "Re-entry" on the risk card.
3. Cash only, made explicit: size against cash minus working entries,
   never buying_power. Handle Alpaca pre-trade rejections as "Order
   rejected by Alpaca".
4. Storage: stop writing day_trades (keep the table; no edits to old
   migrations).
5. GUI per the design: the Positions "Today" card (entries, exits,
   same-session round trips, symbols blocked from re-entry); the risk card
   Re-entry fact; the decision drawer gate "Not traded today"; Settings
   notifications without the day-trade rows; States without the day-trade
   banner and warning; Insights "Agent exits on the entry day".

Tests:
- A model exit on the entry day sells at once.
- A symbol stopped out at 10:46 New York is refused a new entry at 12:46
  and allowed at the next session's first cycle (live and shadow).
- Sizing ignores buying_power when it is larger than cash.
- No reference to the removed fields (static check).
- A pre-trade rejection raises the critical notification and leaves the
  stop in place.
- No network in tests.

Use the PR template, screenshots in docs/screenshots/pr-26/ next to the
design PNGs (Positions, Main, Decision, Settings, States, Insights). List
remaining design differences.
```

## Each later PR

```
PR <n> (<step name>) is next. Read the sections listed for it in
docs/README.md and follow AGENTS.md. Build only this step. Use the PR
template, save screenshots for any UI in docs/screenshots/pr-<n>/ (never in
design/), show them in the PR next to the matching design reference PNG, and
ask before guessing.
```
