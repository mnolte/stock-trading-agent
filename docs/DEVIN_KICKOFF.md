# Devin kickoff messages

## First message (PR 1)

```
We're building Stock Agent, a paper-only trading agent for US stocks on Alpaca,
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

## Each later PR

```
PR <n> (<step name>) is next. Read the sections listed for it in
docs/README.md and follow AGENTS.md. Build only this step. Use the PR
template, save screenshots for any UI in docs/screenshots/pr-<n>/ (never in
design/), show them in the PR next to the matching design reference PNG, and
ask before guessing.
```
