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
Start after PR 26 (day-trade rule retired) is merged, because the
execution numbers assume same-day exits are allowed. It uses PR 19
(after-exit data) and PR 21 (stop and target basis) when they are
merged; leave those parts empty otherwise and say so in the PR. Pull the latest spec and design packages, then read:
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
   - execution (fill rate, slippage in bps, agent exits on the entry day
     with their average R, earnings exits, dividends); exit reasons
     include stop_breached (PR 27) as its own group;
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

The app is now called Stock Trading Agent in the GUI (see CHANGES
2026-10-27); use that name on the new screen.

Use the PR template, screenshots in docs/screenshots/pr-22/ next to the
design PNGs (Insights, Insights-390). List remaining design differences.
Don't restart the running app between 14:30 and 21:00 Amsterdam this week
(15:30–22:00 from 2 November); say in the PR when you merged.
```

## PR 23 — Sign-in and access (after PR 28)

```
PR 23 (Sign-in and access). Built and tested locally; nothing is deployed
in this PR (that is PR 24). Merge after PR 28 (security hardening):
rebase onto it, and onto PR 22 if that merged first, so there is a single
Alembic head (point down_revision at the latest merged revision). Pull the
latest spec and design packages, then read:
1. docs/CHANGES.md, entries 2026-10-26 "VPS deployment (PR 23 and 24)"
   and "Error pages and API errors (PR 25)", and 2026-10-27 "Security
   hardening (PR 28)" (spec), "Sign-in and server"
   (design), and 2026-10-27 "Name: Stock Trading Agent"
2. docs/SPEC.md → "VPS deployment": Sign-in, Secrets on the server,
   Moving from local to the VPS (instance id), Restarts; "Error pages
   and API errors" (the error shape and the 401/403/429 rows only); Data
   model (users, sessions, sign_in_attempts); Notifications (new rows);
   API keys; Build plan item 23
3. AGENTS.md → hard rule 10 (rewritten); backend/config.example.yaml →
   auth; .env.example (SA_AUTH, SA_PUBLIC_URL, SA_TRUSTED_PROXY,
   SA_INSTANCE_ID)
4. design/screens/Login.dc.html, Settings.dc.html (Access section),
   States.dc.html (Access and server; Confirm it is you) and the sidebar
   Sign out, with their screenshots

Before step 1, check what PR 27 already built and say it in the PR:
app/broker/client_id.py already puts SA_INSTANCE_ID into every client
order id (sa-<instance>-<kind>-<rest>). Reuse it; don't add a second
format. Step 5 below is only the parts that don't exist yet.

Build PR 23 only, in this order (one commit per step):
1. Startup guard: SA_AUTH (off | required), default off, parsed once in
   EnvSettings as an enum (strip, lower case) and used everywhere. With
   required, walk the route table at startup and refuse to start if any
   route except /healthz and sign-in lacks the session dependency; guard
   and sessions merge together. Refuse to start
   with SA_AUTH=off when SA_HOST isn't 127.0.0.1 or ::1, with a clear
   message. Trust X-Forwarded-For only from SA_TRUSTED_PROXY. With the
   default local setup (loopback, SA_AUTH unset) the app must start and
   behave exactly as today.
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
   Promote strategy, replacing a key, changing the password,
   authenticator or recovery codes, a settings save that loosens a risk
   limit, and changing the SMTP server, To address or heartbeat URL
   (tightening never asks). PR 28's request gate stays in front of all
   of this.
   Errors use the PR 25 JSON shape already, so PR 25 only generalises it:
   401 not_signed_in / session_expired, 403 confirm_required / forbidden,
   429 rate_limited / locked_out with Retry-After. No HTML, no traceback.
5. Instance id: reconciliation treats an open order or fill whose client
   id carries another instance id as a critical mismatch (kill switch on,
   banner, notification). Orders without an sa- id (manual or from before
   the format) are left as they are, as are this instance's own ids with
   any kind (entry, stop, target, exit, breach).
6. Notifications: New sign-in, Failed sign-ins (lockout), Another instance
   (critical).
7. Frontend: the sign-in screen (three states, "Stock Trading Agent"), a
   session-expired redirect that returns to the same page, the
   Confirm-it-is-you dialog wired to those actions (it repeats the
   request once after confirming), Settings → Access (devices, password
   change, authenticator and recovery codes, idle setting), and Sign out
   in the sidebar. With SA_AUTH=off, Access shows "Sign-in off (local
   only)" and none of the sign-in UI appears.

Tests:
- The route-table walk: every route except /healthz and sign-in returns
  401 with the JSON error shape without a session; the WebSocket closes
  without a session or with a foreign Origin.
- Wrong password and wrong code give the same message; lockout after 5
  failures; the global limit; Retry-After is set.
- Idle and absolute expiry with the injected clock.
- A recovery code works once only.
- A risky action without a fresh confirmation returns 403
  confirm_required; with one it works, and stops working after 5
  minutes.
- CSRF: a write without the token is refused.
- Loosening a risk limit needs a fresh confirmation; tightening doesn't.
- " Required " behaves as required; a route without the session
  dependency stops startup with SA_AUTH=required.
- Startup refusal with SA_AUTH=off on 0.0.0.0; normal start with
  SA_AUTH unset on 127.0.0.1.
- An order with another instance id turns the kill switch on; this
  instance's breach and exit orders don't; an order without an sa- id
  doesn't.
- Scheduled jobs (cycles, daily screen, readiness and insights snapshots)
  run without any session.
- Passwords, TOTP secrets, codes and session tokens never appear in logs
  (add their patterns to the PR 20 scrubber and test it).
- No network in tests.

Use the PR template, screenshots in docs/screenshots/pr-23/ next to the
design PNGs (Login, Login-390, Settings, States). List remaining design
differences.

Merging and restarting: the migration applies on the next start. Restart
only outside 14:30–21:00 Amsterdam this week (15:30–22:00 from 2
November). Leave SA_AUTH unset locally after merging; switching it on is
part of PR 24. Say in the PR when you restarted.
```

## PR 24 — VPS deployment (after PR 23)

```
PR 24 (VPS deployment). Start after PR 23 (sign-in) is merged; it also
uses PR 20 (graceful shutdown) and PR 27 (broker guard); check both are
in main and say so in the PR if not. It does not wait for PR 28 (security
hardening): a few pieces are shared with PR 28, and whichever of the two
is merged first builds them (see below). Don't provision, deploy to or
touch any real server in this PR: everything is scripts, files and tests,
run locally or in CI. I run the scripts on the server myself. Pull the
latest spec and design packages, then read:
1. docs/CHANGES.md, entries 2026-10-26 "VPS deployment (PR 23 and 24)",
   2026-10-27 "Sign-in details (PR 23 review)", "Security hardening
   (PR 28)", "Protective stops hardened (PR 27)" and "Name: Stock Trading
   Agent"
2. docs/SPEC.md → "VPS deployment" (whole section); "Security hardening"
   (request gate, headers, one bind source, files on disk, stored
   credentials); Logging, error handling and shutdown (grace period,
   run.lock); Alpaca integration → Protective stops (broker guard at
   startup); Build plan item 24
3. AGENTS.md → hard rules 10, 12, 14 and 16 (and 18 and 20 for the
   shared pieces); backend/config.example.yaml → ops, storage
4. design/screens/Settings.dc.html (Server section) and States.dc.html
   (Access and server), with their screenshots

From PR 23, reuse: sign-in, SA_AUTH (with its startup route check),
SA_TRUSTED_PROXY, the __Host-sa_session cookie over HTTPS and
SA_INSTANCE_ID in client order ids (PR 27's client_id.py). /healthz is
the only open health endpoint; /api/health needs a session since the
PR 23 review, so nothing outside the app may use it.

Shared with PR 28. Check main first: if PR 28 is merged, reuse its
version; if not, build it here as the spec describes, so PR 28 only has
to reuse it:
- IBM Plex bundled with the frontend, no Google Fonts;
- python -m app.serve as the only way to start, reading SA_HOST and
  SA_PORT;
- umask 077 at startup, SA_DATA_DIR 0700 and its files 0600.
Not shared: the request gate, the app's own security headers and the
other PR 28 items stay in PR 28. Don't build them here.

Build PR 24 only, in this order (one commit per step):
1. Container:
   - a multi-stage Dockerfile; the entrypoint is python -m app.serve,
     never uvicorn with --host/--port;
   - a non-root user, everything writable under /data owned by that user,
     TZ=Europe/Amsterdam;
   - one Uvicorn worker; GET /healthz returns the version only.
2. Compose:
   - app: internal network only, no published port, read_only: true with
     a small tmpfs for /tmp, security_opt no-new-privileges, cap_drop
     ALL, restart unless-stopped, stop_grace_period 90s, a memory limit;
   - app environment: SA_HOST=0.0.0.0 inside the container,
     SA_AUTH=required, SA_PUBLIC_URL, SA_TRUSTED_PROXY set to Caddy's
     address on the internal network, SA_INSTANCE_ID=sa-vps-1. .env holds
     no secrets;
   - caddy: 80 and 443, automatic HTTPS for SA_PUBLIC_URL's host,
     reverse proxy to app with the original Host, X-Forwarded-For and
     X-Forwarded-Proto;
   - HTTPS behind the proxy: the app talks plain HTTP to Caddy, so it
     takes "is this HTTPS" from X-Forwarded-Proto, trusted only from
     SA_TRUSTED_PROXY (or from an https SA_PUBLIC_URL). Cookies must then
     be Secure and named __Host-sa_session; check that sign-in works
     through Caddy and that no cookie is ever sent without Secure on the
     VPS;
   - caddy headers: HSTS and Permissions-Policy always; CSP,
     X-Frame-Options, X-Content-Type-Options, Referrer-Policy and COOP as
     defaults only (Caddy's "header ?Name value" form), with the values
     from SPEC → Security hardening. That way the headers are there now,
     and PR 28's own app headers win once it's merged, with no
     duplicates;
   - the healthcheck calls /healthz with a Host that PR 28's gate will
     accept (localhost with SA_PORT);
   - add docker-compose.local.yml so the same stack runs on a laptop for
     testing (a local hostname, Caddy's internal CA).
3. Restarts: ops.resume_after_clean_restart (auto = on when
   SA_AUTH=required). Resume only when trading was running before, the
   previous shutdown was clean, the kill switch is off, reconciliation
   finds no differences and the broker guard (no_shorting, 1× margin) is
   confirmed. Banner and notification either way.
4. Scripts in deploy/ (bash, set -euo pipefail, idempotent, with --help):
   - provision.sh: user and SSH keys, no password or root login, ufw
     22/80/443, fail2ban, unattended-upgrades with the Sunday 05:00
     Amsterdam reboot window, chrony, Docker;
   - deploy.sh <tag>:
     - market-hours guard: refuse 09:30–16:00 New York, worked out in
       America/New_York, never a fixed offset; --emergency overrides;
     - backup; pull; migrations in a one-off container; restart;
     - wait for /healthz and a clean startup reconciliation;
     - on failure within 2 min: restore the pre-deploy backup if a
       migration ran, start the previous image, report;
     - leave one clearly marked hook before stop and after the health
       check for PR 25's maintenance flag;
   - restore.sh <snapshot>: from the local or offsite copy, with
     integrity_check, file modes 0600 afterwards.
   Stop only containers by name from our compose project; never kill
   processes by name (hard rule 12).
5. CI:
   - on every tag, build and push a versioned image to a private
     registry;
   - run the tests in the image (and make security once PR 28 adds it);
   - pin the new Actions by commit SHA with permissions: contents: read
     plus packages: write only for the push job; the registry token is
     a CI secret, never in the repo.
6. Backups:
   - offsite with restic to S3-compatible storage, after the 03:00 local
     copy; keep 30 daily and 12 weekly;
   - the backup password and storage credentials come from the secrets
     table; validate the endpoint as https and not loopback or private
     (SPEC → Security hardening → Stored credentials);
   - a monthly restore test into a temp folder with integrity_check;
   - Back up now, and Download latest backup (behind "Confirm it is
     you").
7. Monitoring:
   - a heartbeat ping to ops.heartbeat_url after every cycle and position
     check; validate the URL on save as https and not loopback or
     private;
   - checks for clock offset, disk use, certificate expiry, backup age
     and the restore test;
   - the notifications from the spec, with subjects "[Stock Trading Agent
     · PAPER] …".
8. Settings → Server per the design (read-only plus the two backup
   buttons), shown when SA_AUTH=required.
9. docs/DEPLOY.md, a runbook I can follow alone:
   - choosing a VPS and DNS;
   - provision; first deploy; make create-user;
   - entering keys;
   - running make broker-guard against the paper account;
   - setting up offsite storage, the heartbeat service and the outside
     uptime check (on /healthz);
   - moving the local database and secret.key: stop locally first with
     Ctrl+C after the close, set SA_INSTANCE_ID per copy, and never start
     the local copy against the same account again;
   - deploy and rollback; restore; rotating the backup password;
   - a go-live checklist at the top: PR 23 and PR 28 merged before the
     first real deploy, sign-in tested from a phone, and the restore
     test passed once.
   Use "Stock Trading Agent" for the name; technical names stay
   stock-agent.

Tests:
- The container runs as non-root with a read-only root and no
  capabilities.
- /healthz says nothing beyond the version; through Caddy without a
  session, /api/health and every other /api route get 401.
- Signing in through Caddy over HTTPS sets __Host-sa_session with Secure,
  HttpOnly, SameSite=Strict and Path=/; X-Forwarded-Proto from anything
  but SA_TRUSTED_PROXY is ignored.
- Through Caddy with the public Host, the GUI loads and a write works;
  with another Host, Caddy doesn't serve the app.
- Through Caddy every header from the spec is present exactly once;
  when the app sends its own CSP (simulate it), Caddy keeps the app's.
- No --host/--port in the Dockerfile, compose files or scripts (a test
  greps for them; PR 28 extends it to the Makefile).
- docker stop during a simulated cycle waits for the graceful shutdown
  and leaves integrity_check ok.
- deploy.sh refuses during market hours on both sides of the 1 November
  US clock change (injected clock), and rolls back on a failing health
  check (fake image).
- restore.sh round trip, with file modes checked afterwards.
- The resume rules: clean and running resumes; an unclean stop, the kill
  switch, a mismatch or an unconfirmed broker guard stays paused.
- The heartbeat is called once per cycle and check (mocked), and an http
  or private heartbeat URL is refused.
- Each Server check and its notification thresholds.
- No network in unit tests; the compose test runs in CI only.

Use the PR template, screenshots in docs/screenshots/pr-24/ (Settings
Server section, States server banners). List anything in docs/DEPLOY.md
you couldn't verify without a real server.

Merging changes nothing on my machine: locally the app still runs with
make run and SA_AUTH unset. If a local restart is needed, do it outside
14:30–21:00 Amsterdam this week (15:30–22:00 from 2 November), and say in
the PR when.
```

## PR 25 — Error pages and API errors (after PR 23)

```
PR 25 (Error pages and API errors). Start after PR 23 (sign-in) is
merged. The Caddy part needs PR 24: if PR 24 isn't merged yet, commit the
static pages in deploy/errors/ and leave the wiring to PR 24 (say so in
the PR). Doesn't wait for PR 28. Pull the latest spec and design
packages, then read:
1. docs/CHANGES.md, entries 2026-10-26 "Error pages and API errors
   (PR 25)" (spec) and "Error pages" (design); 2026-10-27 "Sign-in
   details (PR 23 review)", "Security hardening (PR 28)" (the new error
   codes only) and "Name: Stock Trading Agent"
2. docs/SPEC.md → "Error pages and API errors" (whole section, including
   the new rows), Interaction and states (backend banner), Logging, error
   handling and shutdown (error_id, POST /api/logs/client, shutting
   down), VPS deployment → Sign-in, Build plan item 25
3. design/screens/Errors.dc.html with Errors.png and Errors-390.png;
   copy the texts as written

What already exists; reuse, don't rebuild:
- PR 23 already answers 401 not_signed_in / session_expired, 403
  confirm_required / forbidden and 429 rate_limited / locked_out in the
  JSON shape, and the frontend already handles 401 and confirm_required;
- PR 20 already returns error_id on 500 and has POST /api/logs/client;
- PR 24 left a marked hook in deploy.sh for the maintenance flag (if
  merged).
Move these onto the shared handler and registry below without changing
how they behave.

Build PR 25 only, in this order (one commit per step):
1. Backend:
   - one exception handler set producing {"error": {code, message,
     error_id?, fields?}} for every /api error: HTTPException,
     validation, and unhandled (500 with error_id, logged with the
     traceback);
   - the message is always a fixed sentence from the registry, never
     the exception's own text (exceptions can carry keys or paths;
     hard rule 9);
   - a code registry (an enum) so codes are stable; include PR 28's
     bad_host, origin_refused, too_large, unsupported_media_type and
     password_required now, so PR 28 only raises them;
   - every non-/api GET path serves the frontend; unknown /api paths
     return 404 JSON;
   - 503 shutting_down while the graceful shutdown runs, and
     starting_up before startup reconciliation finishes.
2. Frontend API client: one place that maps status to behaviour per the
   spec's table:
   - 401: sign-in, then back to the same page;
   - 403 confirm_required: the dialog, then retry once;
   - 409: a reload offer;
   - 413/415: the message in the card or dialog that sent it;
   - 422: field messages;
   - 429: Retry-After;
   - 500: a toast with error_id and Open in Logs;
   - 503: the backend banner.
3. Pages:
   - not found for routes and for records: each record screen handles
     404 with its own title and search link. When not signed in, an
     unknown route goes to sign-in instead, so nothing is revealed;
   - an error boundary around every screen: it reports to
     /api/logs/client and shows the returned error_id. If the report
     fails (not signed in, app down), show "Copy details" without an id;
   - the first-load "The app isn't answering" page with a 15 s retry;
   - the offline banner.
4. Static pages:
   - deploy/errors/502.html and 503.html, self-contained: inline CSS and
     SVG, system fonts, no external requests, a small reload script;
   - they say "Stock Trading Agent" and show no version, host, commands
     or data;
   - JSON bodies for /api during an outage (app_unavailable, updating).
5. Caddy and scripts (if PR 24 is merged):
   - handle_errors for 502/503/504 by path: HTML for pages, JSON for
     /api;
   - the maintenance flag file: deploy.sh and restore.sh set it before
     stopping the app and clear it after the health check (also cleared
     on rollback), using PR 24's hook; Retry-After: 15;
   - /healthz stays outside the maintenance answer, so the scripts can
     still check the app.

Tests:
- A route walk: force each error path (404, 422, 500 via a test-only
  failing route) and assert the JSON shape with no traceback, path or
  HTML. Make the failing route raise an exception whose text holds a
  fake key, and assert the key appears in neither the response nor the
  log.
- PR 23's existing auth error tests still pass unchanged.
- Frontend unit tests for every status mapping.
- Playwright:
  - an unknown route shows the 404 page when signed in, and sign-in when
    not;
  - a missing decision id shows the record page;
  - a forced render error on one screen leaves the sidebar and other
    screens working and shows an error id;
  - the first load with the API down shows the retry page;
  - going offline shows the banner.
- Static pages pass a check for no external URLs and contain no version
  string.
- With the compose stack and the app stopped (CI), / returns the 502 page
  and /api/x returns JSON; with the flag set, 503 with Retry-After.
- No network in unit tests.

Use the PR template, screenshots in docs/screenshots/pr-25/ next to the
design PNGs (Errors, Errors-390). List remaining design differences.

Merging and restarting: no migration. Restart only outside 14:30–21:00
Amsterdam this week (15:30–22:00 from 2 November), and say in the PR
when you restarted.
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

## PR 28 — Security hardening (after PR 25)

```
PR 28 (Security hardening). From your audit of 27 October. Guards only:
no trading change, no risk-limit change, no migration. Start after PR 25
(error pages) is merged; PR 23 and PR 24 are already in main.

A lot of the audit is already done by later PRs; reuse it, don't rebuild
it, and list in the PR which audit items you found already closed:
- PR 23: SA_AUTH parsed once and failing closed, CSRF on writes, the
  WebSocket Origin check behind sign-in, "Confirm it is you" for loosening
  a risk limit and for SMTP/To/heartbeat changes;
- PR 24: bundled fonts, python -m app.serve as the only start path (with
  the --host/--port grep test), umask 077 and file modes, https/public
  checks for the heartbeat URL and backup endpoint, write-only backup
  credentials;
- PR 25: the error registry with bad_host, origin_refused, too_large,
  unsupported_media_type and password_required; exception messages and
  tracebacks scrubbed in the file handler, log index and console;
  malformed JSON as 400.

Pull the latest spec and design packages, then read:
1. docs/CHANGES.md, entry 2026-10-27 "Security hardening (PR 28)" (spec)
   and "Security hardening" (design)
2. docs/SPEC.md → "Security hardening" (whole section); "Error pages and
   API errors" (the new rows); Build plan item 28
3. AGENTS.md → hard rule 9 and rules 18–20; backend/config.example.yaml
   → security
4. design/screens/Settings.dc.html (SMTP note)

Build PR 28 only, in this order (one commit per step):
1. Request gate (audit 1, 8, 9): one middleware for every /api route and
   the WebSocket upgrade, in front of sign-in, also with SA_AUTH=off:
   - Host allowlist: loopback with SA_PORT, the SA_PUBLIC_URL host,
     security.allowed_hosts, the Vite dev address in make dev only;
     anything else → 400 bad_host;
   - on POST/PUT/PATCH/DELETE: a foreign Origin, or Sec-Fetch-Site other
     than same-origin/none → 403 origin_refused; no headers at all passes;
   - write bodies must be application/json (415), except the CSV import
     (multipart);
   - body cap while streaming (413 too_large): max_body_kb, and
     max_upload_kb for the import;
   - /healthz passes the gate with Host localhost:SA_PORT, so the compose
     healthcheck and deploy.sh keep working;
   - check the Vite proxy settings so make dev still works.
2. The app's own headers: CSP, nosniff, X-Frame-Options, Referrer-Policy,
   COOP, and no-store on /api. The CSP is relaxed only under make dev.
   Caddy already sets these only as defaults, so the app's values win;
   extend deploy/test-compose.sh to prove each header still appears
   exactly once and carries the app's value.
3. Scrubber (audit 3, rest): add the URL and header token patterns from
   the spec (token=, apikey=, api_key=, key=, secret=, Authorization).
   Test with a fake key in an exception's args, in a URL inside an
   exception message, and in an Authorization header string, across the
   file, console and index output.
4. Prompts (audit 4):
   - version regex and a resolve-inside check for backend/prompts/;
   - backend/prompts/manifest.json with the SHA-256 of every prompt file;
     load_prompt refuses a mismatch;
   - settings and strategy validation use the same function and answer
     422;
   - the same name check for run, replay and backtest folder names;
   - a test fails when a listed prompt's hash changes.
5. Bounded inputs (audit 5, 12):
   - a shared helper for limit (1–200), window (1–100) and typed ids, 422
     on non-numeric values; GET /api/notifications clamped too;
   - exports streamed and capped at security.export_max_rows, with a
     closing line saying so;
   - the FTS search quoted as a phrase; an FTS syntax error gets 422;
   - strategy:abc gets 422;
   - a test that walks every GET route and sends -1, 0, 10^12 and "abc"
     to each query parameter: no 500 anywhere.
6. /api/logs/client (audit 6):
   - rate-limited per client_ip(), with a bounded table of senders
     (security.client_error_senders, oldest dropped);
   - message at most 2 KB and stack at most 8 KB;
   - control characters and CR/LF stripped from single-line fields, and
     source=frontend on the line;
   - fix the precedence quirk on the tab line.
7. Stored credentials (audit 7):
   - SMTP: a request that changes the host, port or username without a
     password gets 422 password_required, and no connection is made;
   - SMTP: port must be 465, 587 or 2525; connect timeout 10 s; a failed
     test gives a category only, never the server's text;
   - the frontend makes the password field required once the host, port
     or username changes, as in the design note;
   - check that replacing Alpaca keys with a different paper account's
     keys requires Start new run; add a test, and implement it if
     missing.
8. Files (audit 12): mode=ro for run databases opened only for reading
   (_alembic_version and friends); run.lock created with
   O_CREAT|O_EXCL, keeping today's stale-lock handling.
9. Codebase and CI:
   - ruff S rules on, every ignore with a comment;
   - pip-audit on uv.lock and pnpm audit --prod in CI on every PR and
     weekly, failing on high/critical unless listed in
     security/audit-allowlist.yml with a reason and expiry;
   - gitleaks in CI and as a pre-commit hook;
   - pnpm install --frozen-lockfile;
   - pin every Action by commit SHA (PR 24 did only the new ones), with
     permissions: contents: read by default; Dependabot for
     github-actions;
   - make security runs tests/security/ and both audits; add make security
     to the image test stage in CI;
   - docs/SECURITY.md: one page with the threat model, the layers and
     what isn't covered.

Tests (tests/security/, all without network):
- With SA_AUTH=off: a text/plain cross-origin POST to /api/kill-switch
  → 403 and nothing happens; a bodiless cross-origin POST to /api/pause
  → 403; the same requests from the app's own origin work.
- Host: evil.example → 400 (DNS rebinding); the WebSocket with a foreign
  Origin or Host is refused, also with SA_AUTH=off.
- An oversized body → 413; a form-encoded write → 415.
- With SA_AUTH=required, the gate runs before sign-in: a foreign Origin
  gets 403 origin_refused even with a valid session.
- The headers are present on HTML and /api responses; compose-test
  confirms they appear once each through Caddy.
- The scrubber cases from step 3.
- ../../README in a prompt version → 422; a tampered prompt file is
  refused at load.
- The GET-route parameter walk from step 5.
- /api/logs/client: rotating tab ids doesn't bypass the limit; the
  senders table stays at its cap; newlines don't create extra log lines.
- SMTP with a fake SMTP server: a changed host without a password →
  422, and the fake server sees no connection; with a password it
  connects and logs in with that password.
- Two simultaneous RunLock.acquire calls → exactly one wins.
- The GUI e2e suites, with sign-in on and off, still pass through the
  gate.

Use the PR template. Screenshots: Settings (SMTP card with the password
field required after a host change). List remaining design differences.
Answer audit items you chose not to change as questions in the PR, and
post the compose-test CI result.

Merging and restarting: no migration, but the local app needs a restart
to pick up the gate. Restart only outside 14:30–21:00 Amsterdam this week
(15:30–22:00 from 2 November). Afterwards check the GUI loads and a
harmless write works from it (Pause and Resume while the market is
closed). Never test with Stop & flatten. Say in the PR when you
restarted. Once this is merged, the go-live checklist in docs/DEPLOY.md
is unblocked.
```

## Docs — README rewrite (any time)

```
Docs PR: rewrite the root README.md. Documentation only: no change to
code behaviour, config defaults, the spec or the design. The current
README is from v0.1 (10 fixed symbols, decision.v1 only, no sign-in) and
is out of date.

Sources of truth, in this order:
- docs/SPEC.md and docs/CHANGES.md for behaviour;
- design/DESIGN.md and design/screens for names and labels;
- AGENTS.md for the rules contributors follow;
- .env.example, backend/config.example.yaml, the Makefile, docs/DEPLOY.md
  and the code for exact names, defaults and commands.

Every statement in the README must be traceable to one of these. Don't
invent features, numbers, commands or claims, and don't add marketing
language. Use the GUI labels exactly as the design writes them
(Settings → API keys, Stop & flatten, Confirm it is you, …). Where the
code in main and the spec disagree, don't pick one: leave the line out
and list it as a question in the PR. Features that are specced but not
yet in main go only in the Roadmap section, worded as in the spec's
build plan.

Structure (in this order):
1. Title "Stock Trading Agent", a one-line description, a short "Paper
   only" notice (from the spec: Alpaca paper account only, no real-money
   orders, a personal experiment and not financial advice), and one hero
   screenshot (the dashboard). Add a CI status badge for the existing
   workflow; no other badges.
2. Table of contents.
3. Description:
   - what it does: the universe and daily liquidity screen, the decision
     cycles (times in New York, with Amsterdam alongside), rules
     pre-filter, the model choosing between code-built options, risk
     gates, bracket orders with broker-side stops, position checks,
     outcome tracking, the SPY benchmark;
   - strategies and shadows, backtests and replays, reviews, insights,
     readiness;
   - a "Safety model" subsection: paper only, every position has a stop
     at Alpaca, the model never overrides code, no shorting and 1×
     margin set at the broker, the kill switch, sign-in when not on
     127.0.0.1;
   - one Mermaid diagram of a single cycle (data → rules → model → risk
     gates → order → stop at Alpaca), using the spec's step names.
4. Screenshots: a gallery (see "Screenshots" below), each with a
   one-line caption saying what the screen is for.
5. Requirements: Python, Node and pnpm versions as pinned in the repo
   (pyproject, .nvmrc/package.json engines, uv); an Alpaca paper account
   with API keys; an OpenAI key; a Finnhub key (free plan); Docker and
   Docker Compose only for the VPS. Name versions exactly as the repo
   pins them.
6. Installation:
   - local: clone, copy .env.example and backend/config.example.yaml,
     make install, make run; the app starts paused; enter the keys in
     Settings → API keys (each is tested before saving); check the
     PAPER badge; reset the paper account to $10,000 in the Alpaca
     dashboard before the first run; press Resume;
   - simulated mode without keys (MODE=simulated);
   - development with make dev;
   - production on a VPS: a short summary and a link to docs/DEPLOY.md
     (don't copy the runbook).
7. Configuration:
   - Environment variables: one table with variable, default, allowed
     values, what it does, and when to change it. Cover every variable
     the app or the deploy files read (.env.example,
     docker-compose*.yml, deploy/*.sh, the settings class in the code).
     Build the list from the code, not from memory; a variable in the
     code but missing from .env.example (or the reverse) is a question
     in the PR.
   - backend/config.yaml: every key in config.example.yaml, grouped by
     top-level block, one collapsible <details> section per block. Each
     row gives the key (full dotted path), default, unit, what it does,
     and whether it can be changed in Settings or only in the file.
     Take the meaning from the spec section that defines it, and link
     that section.
   - Settings and API keys: what is stored where (encrypted keys and
     data/secret.key, settings versions), and that keys never go in
     .env.
8. Usage basics: the daily rhythm in Amsterdam time, explaining the
   weeks when Europe and the US change clocks on different dates; where
   to look (Dashboard, Positions, LLM inspector, Logs, Readiness);
   Pause, Resume and Stop & flatten in one or two sentences each.
9. Commands: the make targets that exist (install, run, dev, test, lint,
   security if present, logs-reindex, create-user, broker-guard,
   replay, …), one line each, generated from the Makefile.
10. Project layout: the top-level folders and what's in each.
11. Contributing:
    - how work is done: spec first (docs/SPEC.md, docs/CHANGES.md), one
      build step per PR, the PR template, screenshots next to the design
      PNGs, design differences listed;
    - a short summary of the hard rules in AGENTS.md, linking it as the
      full list. Include at least: paper only, no network in tests,
      migrations and used prompts immutable, never kill processes by
      name, no restarts during the session, no silent failures, secrets
      never logged;
    - before opening a PR: make lint, make test (and make security if
      present) must pass;
    - reporting a security problem: link docs/SECURITY.md if it exists;
      otherwise say to contact the maintainer privately and not to open
      a public issue.
12. Roadmap and status: merged build-plan steps versus open ones,
    taken from the spec's build plan and git history; no dates or
    promises.
13. License: only if a LICENSE file exists. If not, write no license
    section and ask in the PR.
14. Disclaimer: the paper-only, not-financial-advice text from the
    spec.

Screenshots:
- Capture them from the real app, not from the design files, in
  MODE=simulated with seeded fixture data, so no real account number,
  key, email address, IP address or position appears. Check every image
  for these before committing.
- Add a repeatable script (make readme-screens, Playwright) that starts
  the app in simulated mode and writes the images to
  docs/images/readme/.
- Screens: Dashboard (hero), Decision drawer (decision.v2 options),
  Positions, Performance, Insights, Readiness, LLM inspector, Settings,
  Sign in, and the dashboard at phone width (390 px).
- Desktop at 1440 px wide, cropped to the useful part. Each PNG under
  400 KB (optimise it), with alt text naming the screen.
- Where a screen in main differs visibly from the design, mention it in
  the PR.

Quality bar:
- Plain English, short sentences, consistent terms with the spec.
- Every relative link and image path must resolve: add a test or script
  that checks the links in README.md.
- Add tests/test_readme.py: it fails when a key in
  backend/config.example.yaml or a variable in the settings class is
  missing from README.md, so the README can't fall behind the config.
- Renders cleanly on GitHub (check the Mermaid block and the <details>
  sections in the PR preview).

PR: use the PR template. Put the README preview screenshots and the list
of questions (code vs spec differences, missing variables, license) in
the description. Nothing needs a restart.
```

## PR 29 — Model providers: OpenAI and Anthropic (after PR 25)

```
PR 29 (Model providers: OpenAI and Anthropic). OpenAI and Anthropic
(Claude) models can each be chosen for live decisions, the weekly review,
the daily briefing, verdicts, shadows, replays and backtests. The OpenAI
key and the Anthropic key are each optional; at least one is required.
The default live model stays gpt-4o-mini, and nothing in this PR changes
prompts, the output schema, the option builder or any risk gate. Start from the
latest main after PR 25 is merged; it doesn't depend on PR 28. Pull the
latest spec and design packages, then read:
1. docs/CHANGES.md, entry 2026-10-28 "Model providers: OpenAI and
   Anthropic (PR 29)" (spec) and "Model providers (OpenAI and
   Anthropic)" (design)
2. docs/SPEC.md → "Model providers: OpenAI and Anthropic" (whole
   section); Decision layer (model output); API keys; LLM call history
   (outcomes, stored fields); Backtesting (model calls); Reviews (model
   and cost); Build plan item 29
3. AGENTS.md → hard rules 6, 7, 8, 9 and the new rule 21;
   backend/config.example.yaml → models, decision, reviews
4. design/screens/Settings.dc.html (API keys, Decisions, Reviews),
   Inspector.dc.html (model filter), Logs.dc.html (component filter),
   with their screenshots
5. The Anthropic structured outputs documentation:
   https://platform.claude.com/docs/build-with-claude/structured-outputs
   (output_config.format, the schema features that aren't supported,
   stop reasons)

PR 28 already added part of step 1 to fix a boot failure on main:
prices_for() and the one-release fallback for model_prices and
reviews.model. Reuse and extend them; don't build a second version.

Build PR 29 only, in this order (one commit per step):
1. Model list:
   - load the models block (provider, input, output, cutoff, effort,
     timeout_seconds) and validate it at startup;
   - read model_prices and backtest.model_cutoffs for one release with
     a WARNING when models is missing;
   - reviews.model becomes reviews.models.weekly / .briefing /
     .verdict; read the old reviews.model for one release (as all
     three) with a WARNING;
   - every place that names a model (decision.model, reviews.models.*,
     strategies, backtests, replays) must refer to an entry, otherwise
     startup or the save fails with a clear message;
   - add decision.max_output_tokens.
2. One model client interface in app/llm/ (request: prompt, snapshot,
   JSON schema, model; result: parsed JSON, input and output tokens,
   latency, provider request id, or a typed error: timeout, api_error,
   invalid_output, refusal):
   - move today's OpenAI code behind it unchanged, as the OpenAI
     adapter in app/llm/providers/;
   - keep a fake adapter for tests;
   - decision, review, shadow, replay and backtest code call only the
     interface. A test fails if openai or anthropic is imported
     anywhere outside app/llm/providers/ (hard rule 21).
3. Anthropic adapter (official anthropic SDK, Messages API):
   - the prompt file as system, the snapshot as the user message;
   - output_config.format with type json_schema;
   - a schema transform that removes the keywords Anthropic doesn't
     accept from the copy that is sent (numeric minimum/maximum,
     string and array lengths, additionalProperties other than false);
     Pydantic still validates the answer against the full schema;
   - no temperature/top_p/top_k; send the model's effort;
     max_tokens from decision.max_output_tokens; the per-model timeout;
   - map stop reasons: refusal → refusal; max_tokens → invalid_output
     with reason truncated; HTTP 529/5xx and connection errors →
     api_error with status and error type; one try, no retry;
   - store only the final text, never thinking.
4. Keys (SPEC → Model providers → Keys):
   - OpenAI and Anthropic keys in Settings → API keys, each optional,
     at least one required: write-only, encrypted, tested with one
     list-models call before saving, one-time import from .env
     (OPENAI_API_KEY, ANTHROPIC_API_KEY); add sk-ant- to the log
     scrubber patterns;
   - Resume needs at least one model key and a passing key for every
     provider used by live decisions and the three review tasks; the
     banner names what's missing ("Live decisions use gpt-4o-mini: add
     the OpenAI key or choose a Claude model");
   - saving a setting that picks a model whose provider has no passing
     key is refused with a message;
   - a new Remove action for each model key, behind "Confirm it is you":
     refused while live decisions or a review task use that provider;
     shadows on it pause and say why;
   - a shadow whose provider key is missing or failing is skipped for
     that cycle with a WARNING and never delays live;
   - a backtest with a strategy whose provider has no working key is
     refused before it starts.
   Check every place that assumes an OpenAI key exists today (startup
   checks, Resume, the key banner, reviews, cost estimates) and list
   them in the PR.
5. Recording (new Alembic revision on the current head):
   - add llm_calls.provider and provider_request_id, and copy the
     existing OpenAI request ids into the new column; the old column
     stays;
   - the answer cache key gets provider and effort;
   - replays and backtests with a Claude model show "not repeatable:
     the model has no temperature 0".
6. Backtests: the cutoff check reads the model's cutoff from models.
   For the current Claude 5.5 models (cutoff June 2026), the earliest
   start is 1 July 2026; the form says so.
7. Frontend per the design:
   - Settings → API keys: the "Model keys" line, then the OpenAI and
     Anthropic rows, each optional, each saying what uses it, each with
     Test, Replace and Remove;
   - Settings → Decisions: the model picker shows "id · provider";
     models whose provider has no passing key are disabled with "needs
     the … key"; temperature marked "OpenAI models only";
   - Settings → Reviews: three pickers (weekly review, daily briefing,
     verdicts);
   - the new-shadow dialog, the backtest form and make replay --model
     use the same list;
   - LLM inspector: the model filter and the provider in the call
     detail and error block;
   - Logs: the model.anthropic component.

Tests (no network; fakes only):
- The schema transform: the decision.v1 and decision.v2 schemas, once
  sent, contain no unsupported keyword; an answer with confidence 1.5 or
  an option id that wasn't offered is still rejected by Pydantic and
  becomes skip.
- Anthropic adapter with recorded fixture responses: success, refusal,
  max_tokens, 529, timeout. Each gives the documented outcome in
  llm_calls, and no temperature is ever sent.
- A shadow on claude-haiku-5-5 runs next to live in a simulated session
  with the fake adapter; live's cycle timing is unchanged; without the
  key the shadow is skipped with a warning.
- Key combinations: only an OpenAI key (everything on OpenAI models)
  and only an Anthropic key (live and all three review tasks on Claude
  models) both run a simulated session and the review jobs; no model key
  blocks Resume; a review task on a provider without a key blocks
  Resume; a shadow without its provider's key doesn't.
- Saving a model whose provider has no passing key is refused; removing
  a key in use is refused; removing an unused key pauses the shadows on
  it.
- Each review task (weekly, briefing, verdict) calls the model chosen
  for it.
- A backtest with a Claude model starting before 1 July 2026 is
  refused.
- Config: an unknown model in decision.model fails at startup; the old
  model_prices/model_cutoffs keys still load with a warning.
- The migration fills provider and provider_request_id for existing
  rows.
- Costs use the prices from models for both providers.
- A fake sk-ant- key never reaches any log.

Use the PR template, screenshots in docs/screenshots/pr-29/ next to the
design PNGs (Settings, Inspector, Logs). List remaining design
differences.

Don't add a real Anthropic key or make real calls; I'll add the key and
choose the models myself after merging. Merging and restarting: the
migration applies on the next start. Restart only outside 14:30–21:00
Amsterdam this week (15:30–22:00 from 2 November), and say in the PR
when you restarted.
```

## PR 30 — Decision v3: checks by code, room and break-even

```
PR 30 (Decision v3: checks by code, room and break-even). A new prompt
version and snapshot for v3 strategies only. Live's prompt, decision.v1
and decision.v2 (prompts, snapshots, options) and every risk gate stay
exactly as they are. Start from the latest main; it builds on PR 21
(decision.v2) and works with or without PR 29 (Anthropic). Pull the
latest spec and design packages, then read:
1. docs/CHANGES.md, entry 2026-10-28 "Decision v3 (PR 30)" (spec) and
   "Decision v3 drawer" (design)
2. docs/SPEC.md → "Decision v3: checks by code, room and break-even"
   (whole section, including "Held positions in v3"); "Decision v2:
   code-built options" (levels, options, stop options,
   code checks, replay, storage); Build plan item 30
3. backend/prompts/decision.v3.md (new; copy it into the repo as is, and
   add its SHA-256 to prompts/manifest.json if PR 28 is merged)
4. backend/config.example.yaml → decision.v3
5. design/screens/DecisionV3.dc.html and DecisionV3Held.dc.html with
   their PNGs

Build PR 30 only, in this order (one commit per step):
1. Snapshot v3 builder, separate from v2's, selected by the strategy's
   prompt version:
   - rounding (prices and levels to cents, ATR to 4 decimals,
     distances, percentages and ratios to 2);
   - no duplicates; bid and ask only when plausible, otherwise null;
   - checks: trend_1h, trend_daily, regime_trend, regime_volatility,
     room_atr, room_level, earnings, news_status, with the exact rules
     from the spec;
   - distance_atr on levels and on each level in the way;
   - breakeven_confidence per option;
   - constraints with min_room_atr, min_confidence and min_edge.
   The provenance the v3 snapshot doesn't send (stop_reference_price,
   quote timestamps, feed, fetch time) is still stored on the decision.
2. Option builder version 2 (v3 strategies only): the room filter with
   decision.v3.min_room_atr and room_level_kinds, overridable per
   strategy. When nothing is left: no_call, no_options with a room
   reason naming the level, its kind and its distance.
3. The v3 output schema (fields in the spec's order; data_flags as an
   enum list). Store conflicts where v2 stores risks.
4. Code checks: the confidence gate (edge_too_small); data flags (enter
   with a flag becomes skip with reason data_flag; every flag is logged
   at WARNING and recorded as an event); room checked again at order
   time from the final limit (Fresh quote gate, reason room); the
   explanation check against the rounded snapshot.
5. Held positions in v3 (option builder version 2 and snapshot v3):
   - stop options: locks_profit only above the entry; locked_r
     (replacing r_at_stop) and distance_atr; drop moves under
     decision.v3.min_stop_move_atr; break-even only from
     decision.v3.breakeven_min_r; trail and swing-low options at least
     decision.v3.held_stop_min_atr below the price; all three
     strategy-overridable; store dropped options with their reasons;
   - position: r_now, stop_distance_atr, target_distance_atr,
     levels_to_target, invalidation filled by code, entry_thesis,
     scheduled_exit when the earnings exit will sell it;
   - held confidence stored per action, not gated, and kept out of the
     entry calibration on Readiness and Insights;
   - the lock-in claim check (unsupported_claim when the thesis says the
     stop locks in profit and the chosen stop's locked_r ≤ 0).
6. v3 in strategies, the new-shadow dialog, backtests and make replay
   (PROMPT=decision.v3; v3 snapshots derived from stored v2 inputs, no
   refetch), with the report additions from the spec (including the
   held section).
7. Frontend: the v3 drawers per the design. DecisionV3 (entries): the
   checks block, options with distances and break-even, conflicts before
   the reasoning, data flags, the two new gates and the room reason for
   no_options. DecisionV3Held: the position facts, scheduled exit, stop
   options with locked R and distance, and the options not offered with
   their reason. no_options · room in the dashboard card and the feed.

Tests:
- The SBUX snapshot of 28 Oct 2026 10:46 (fixture: the stored v2 input)
  gives no_options under v3 with reason "room: L7 1h swing high 0.31 ATR
  above the limit (min 1.0)"; no model call is made.
- A fixture with room gets options carrying distance_atr and
  breakeven_confidence; trend labels match the rules for up, down and
  mixed cases (including SBUX's daily: close > SMA 50 < SMA 200 is
  mixed).
- VWAP and the prior close above the price don't count for room.
- A strategy override of min_room_atr 0.5 keeps an option that 1.0
  removes.
- Confidence 0.40 on a 2R option (break-even 0.33 + 0.10 = 0.43, min
  0.55) becomes skip with edge_too_small; 0.60 passes.
- An enter with data_flags ["earnings_date_conflict"] becomes skip with
  reason data_flag, logs a WARNING and records an event; a held
  decision with a flag keeps its action but still logs and records.
- Order-time room recheck: a fresh limit that leaves 0.9 ATR fails the
  Fresh quote gate with reason room.
- v1 and v2 snapshots, options and replay output are byte-for-byte
  unchanged on the existing fixtures.
- The explanation check passes on rounded numbers and fails on an
  unrounded one that isn't in the snapshot.
- The SBUX held snapshot of 28 Oct 14:46 (fixture: the stored v2 input)
  under v3: T1 dropped (moves 0.05 ATR), T2 dropped (+0.49R < 1.0R), T3
  offered with locked_r −0.07 and distance_atr 1.21; scheduled_exit is
  the earnings exit on 29 Oct 15:46 NY; invalidation is filled.
- locks_profit is false for a stop exactly at the entry and true just
  above it.
- A thesis "locks in the current 0.49 R profit" with a chosen stop at
  locked_r 0 gets unsupported_claim.
- Held confidence never blocks an action and doesn't appear in the
  entry calibration.
- A v3 shadow runs next to live in a simulated session; live's timing is
  unchanged.
- No network in tests.

After merging, run make replay PROMPT=decision.v3 on the current run's
snapshots with gpt-4o-mini (and claude-haiku-5-5 if PR 29 is merged and
I've added the key), stay under the replay cost cap, and post the report
in the PR. Don't start the v3 shadow yourself; I'll do that.

Use the PR template, screenshots in docs/screenshots/pr-30/ next to the
design PNGs (DecisionV3, DecisionV3Held). List remaining design differences. No
migration, but a restart is needed to load the new prompt: only outside
14:30–21:00 Amsterdam this week (15:30–22:00 from 2 November); say in
the PR when you restarted.
```

## Fix — original_invalidation filled for held positions (before PR 30)

```
Small bug fix, before PR 30: held decisions get "original_invalidation":
"unavailable".

Example: SBUX held, decision at 28 Oct 2026 14:46 NY (decision.v2,
gpt-4.1-mini). The snapshot's position block has original_invalidation
"unavailable", while SPEC → Decision v2 → Model output says the
invalidation is the chosen stop, written by code. Without it the model
can't judge an exit.

1. Find where the position block gets original_invalidation and why it
   is "unavailable" for v2 entries (and check v1 entries too).
2. Fill it from what was stored at entry: the initial stop and its basis
   (for v2 the chosen option's stop_basis), for example "below 101.74:
   2 × ATR stop". Don't change the v2 prompt and don't add other fields
   to the v2 snapshot; only the value of this existing field changes.
3. For positions already open, fill it from the stored entry decision; if
   that really isn't available, keep "unavailable" and say which
   positions in the PR.
4. Tests: a v2 entry with an ATR stop and one with a level stop both give
   a filled invalidation in the next held snapshot; a v1 entry still
   works.

No migration expected. Restart only outside 14:30–21:00 Amsterdam this
week; say in the PR when you restarted. Note that SBUX should be sold by
the earnings exit at 15:46 NY on Thu 29 Oct; don't restart around that
cycle.
```

## CI — spec change label

````
CI: require the "spec change" label on pull requests that change spec
files. Small, CI only: no app code changes. Pull the latest spec package
and read docs/README.md rule 8, AGENTS.md → How to work, and
docs/CHANGES.md entry 2026-10-29 "Process: spec changes are labelled".

Add these three files (adjust only the checkout pin):

1. .github/spec-paths.txt

# Files owned by the spec and design. A PR that changes any of them
# needs the "spec change" label. Bash matching: * also crosses "/".
docs/SPEC.md
docs/README.md
docs/DEVIN_KICKOFF.md
docs/analysis/*
AGENTS.md
CLAUDE.md
backend/prompts/*.md
backend/config.example.yaml
.env.example
design/*
.github/spec-paths.txt
.github/scripts/check-spec-label.sh
.github/workflows/spec-label.yml

2. .github/workflows/spec-label.yml

name: spec-label
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled, ready_for_review]
permissions:
  contents: read
concurrency:
  group: spec-label-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  spec-label:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@<same SHA pin as our other workflows>  # v4
        with:
          fetch-depth: 0
      - name: Check spec label and changelogs
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          HAS_LABEL: ${{ contains(github.event.pull_request.labels.*.name, 'spec change') }}
        run: .github/scripts/check-spec-label.sh

3. .github/scripts/check-spec-label.sh (executable)

#!/usr/bin/env bash
# Fails when a PR changes spec files without the "spec change" label,
# or changes spec or design files without their changelog entry.
set -euo pipefail

mapfile -t patterns < <(grep -vE '^[[:space:]]*(#|$)' .github/spec-paths.txt)
mapfile -t changed < <(git diff --name-only --no-renames "$BASE_SHA...$HEAD_SHA")

matches() {  # file, then patterns; [[ == ]] lets * cross "/"
  local f=$1; shift
  for p in "$@"; do [[ $f == $p ]] && return 0; done
  return 1
}
changed_has() { printf '%s\n' "${changed[@]}" | grep -qxF "$1"; }

spec_files=()
for f in "${changed[@]}"; do
  if matches "$f" "${patterns[@]}"; then spec_files+=("$f"); fi
done

{
  echo "## Spec label check"
  if ((${#spec_files[@]})); then
    echo "Spec files changed:"
    printf -- '- `%s`\n' "${spec_files[@]}"
  else
    echo "No spec files changed."
  fi
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

errors=0
if ((${#spec_files[@]})) && [[ $HAS_LABEL != true ]]; then
  echo "::error::This PR changes spec files but has no 'spec change' label: ${spec_files[*]}"
  errors=1
fi
if ((${#spec_files[@]} == 0)) && [[ $HAS_LABEL == true ]]; then
  echo "::warning::The 'spec change' label is set but no spec file changed. Remove the label."
fi

needs_spec_log=(docs/SPEC.md AGENTS.md 'backend/prompts/*.md' backend/config.example.yaml .env.example)
needs_design_log=('design/screens/*' design/DESIGN.md design/tokens.css)
for f in "${spec_files[@]}"; do
  if matches "$f" "${needs_spec_log[@]}" && ! changed_has docs/CHANGES.md; then
    echo "::error file=$f::Spec change without an entry in docs/CHANGES.md"
    errors=1
  fi
  if matches "$f" "${needs_design_log[@]}" && ! changed_has design/CHANGES.md; then
    echo "::error file=$f::Design change without an entry in design/CHANGES.md"
    errors=1
  fi
done
exit "$errors"

Notes:
- The label values come in through env, never interpolated into the
  script, so a PR title or branch name can't inject anything.
- labeled/unlabeled are triggers, so adding the label re-runs the check.
- docs/CHANGES.md itself is not in the list: code PRs add entries for
  behaviour changes (AGENTS.md), and that alone shouldn't need the label.

Tests: tests/ci/test_spec_label.py (pytest, no network). Build a
temporary git repo per case, copy the script and spec-paths.txt in, and
run the script with BASE_SHA/HEAD_SHA/HAS_LABEL set:
- only backend code changed, no label → pass;
- docs/SPEC.md changed, no label → fail;
- docs/SPEC.md and docs/CHANGES.md changed, label → pass;
- docs/SPEC.md changed with label but no docs/CHANGES.md → fail;
- design/screens/Main.dc.html with label but no design/CHANGES.md →
  fail;
- backend/prompts/decision.v3.md added with label and docs/CHANGES.md
  → pass;
- backend/config.example.yaml changed, no label → fail;
- label set, no spec file changed → pass with the warning;
- a file renamed out of design/ counts (the old path matches);
- design/screens/sub/x.html matches design/* (star crosses "/").
Run it in the existing backend test job.

Then:
- Add a "Spec files" line to the PR template: "Spec files changed: none
  / list, and why".
- In the PR, tell me the exact check name to mark as required.

I'll create the label and make the check required myself. Use the PR
template; no restart needed.
````

## PR 31 — Exit fills and reconciliation

```
PR 31 (Exit fills and reconciliation). Thanks for the SBUX/HD
investigation. Your observation is right, and the root cause is one
step earlier: a routine stop or target fill should never be a
reconciliation mismatch. Start from the latest main; independent of PR
29 and 30. Pull the latest spec, then read:
1. docs/CHANGES.md, entry 2026-10-29 "Exit fills and reconciliation
   (PR 31)"
2. docs/SPEC.md → Alpaca integration → Reconciliation (the new "Exits
   close from the stream", "Fills the stream missed" and "Mismatches"
   bullets) and Protective stops; Build plan item 31
3. AGENTS.md → hard rule 5 (timezone-aware datetimes)

Build PR 31 only, in this order (one commit per step):
1. Stream closes exits: when a trade update fills an order of this
   instance (stop, target, OCO, exit, earnings exit, breach, Stop &
   flatten) and the position's filled sell quantity reaches the
   position quantity, close the position row in the same transaction as
   the fill: exit reason from the order kind, exit price from the fill,
   INFO log, the normal "Position closed" notification, no event. A
   partial fill only updates the quantity.
2. Reconciliation explains before it accuses: for a recorded position
   that is flat at Alpaca, fetch the symbol's filled orders since the
   position opened. If this instance's exit orders cover the quantity,
   close the row with that reason and fill, closed_while_offline = true,
   WARNING log, no banner. Otherwise it's a mismatch (step 3).
3. One mismatch path: every mismatch kind (flat without an explaining
   fill, quantity mismatch, unknown order, another instance, adopted
   position) goes through one function that records the event, logs
   CRITICAL, sets the banner and pending acknowledgment, and blocks new
   entries until Marc acknowledges. Route _close_position's "closed at
   broker" case through it and remove the silent path.
4. Datetimes: turn on ruff's DTZ rules, fix every violation (aware UTC
   or the injected clock), and add a regression test for the
   build_stop_options naive/aware TypeError of 28 Oct (cycles 200, 204
   and 208). Tell me in the PR whether that fix went through a PR with a
   test, or was patched on the running app.

Tests (no network; fake broker and stream):
- A stop leg fill closes the position from the stream: exit reason
  stop, fill price stored, notification sent, no reconciliation event,
  and the next reconcile is clean.
- A target fill and an OCO exit do the same with their own reasons.
- A partial stop fill leaves the position open with the right
  quantity.
- The app is down while the stop fills; at start the reconcile finds
  the fill in Alpaca's history and closes the row as
  closed_while_offline, WARNING, no banner, entries not blocked.
- A flat position with no explaining fill → CRITICAL, event recorded,
  banner, entries blocked until acknowledged.
- Every mismatch kind goes through the one mismatch function (one test
  per kind).
- The SBUX case of 28 Oct as a fixture: break-even stop at 103.15
  filled → closed from the stream with reason stop, raw P&L 0.00.
- make lint fails on a naive datetime.now() in engine code.

No migration expected (if closed_while_offline needs a column, add a
new Alembic revision and say so). Use the PR template. Restart only
outside 14:30–21:00 Amsterdam this week (15:30–22:00 from 2 November),
and not while a position is open if you can avoid it; say in the PR
when you restarted.
```
