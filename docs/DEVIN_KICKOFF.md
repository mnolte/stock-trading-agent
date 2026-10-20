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

## Each later PR

```
PR <n> (<step name>) is next. Read the sections listed for it in
docs/README.md and follow AGENTS.md. Build only this step. Use the PR
template, save screenshots for any UI in docs/screenshots/pr-<n>/ (never in
design/), show them in the PR next to the matching design reference PNG, and
ask before guessing.
```
