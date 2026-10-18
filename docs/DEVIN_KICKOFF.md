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

## Each later PR

```
PR <n> (<step name>) is next. Read the sections listed for it in
docs/README.md and follow AGENTS.md. Build only this step. Use the PR
template, save screenshots for any UI in docs/screenshots/pr-<n>/ (never in
design/), show them in the PR next to the matching design reference PNG, and
ask before guessing.
```
