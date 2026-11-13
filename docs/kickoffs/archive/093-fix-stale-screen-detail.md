# 093 · fix · Universe screen: stale detail and no re-run

Kickoff for Devin. Start the session with: "Build docs/kickoffs/093-fix-stale-screen-detail.md."

Spec: `docs/SPEC.md` → Larger universe (the daily screen, "the eligible
list is fixed for the whole session"), Universe screen → GUI (statuses,
progress detail). Code: `backend/app/universe/service.py`
(`describe`, `_run_screen`, `_warming_details`),
`backend/app/api/routes.py` (`POST /api/universe/screen` — simulated
mode only).

Move this kickoff to `docs/kickoffs/archive/` with `git mv` in the build
PR.

## The bug (seen live, 13 Nov 2026)

After the 092 data fix backfilled the missing 2026-11-12 daily bars,
the watchlist was clean (`warming_up: false` for all 73 symbols) but
the Universe page still showed every row as "eligible · 1 fail" with
the detail "258 of 259 daily bars · 59 of 59 sessions" — the recorded
result of the 08:10 screen, hours out of date. There is no way to
refresh it: `POST /api/universe/screen` answers 403 in paper mode, and
the next scheduled screen is the next trading day.

Two separate gaps:

1. **Stale detail.** `UniverseService.describe` recomputes history
   progress live only for symbols with no screen row or status
   `warming_up`; `eligible`/`eligible_fails` rows show the stored
   `check_results.detail` verbatim — a record of that morning's data,
   presented as current status. After any data fix (a backfill, a
   retried sync, a source gap that resolves) the page keeps reporting
   the old numbers until tomorrow.
2. **No re-run.** The screen itself is idempotent — `_run_screen`
   deletes and rewrites `universe_screens` rows for the session date —
   but nothing in paper mode can trigger it. Whether a mid-session
   re-run is allowed at all is a spec question: the eligible list is
   fixed for the session, and a re-run on moved prices could change
   it.

## The rule (to be decided in the spec PR — ask, don't guess)

Pick one, or combine:

- **A. Re-run on demand in paper mode.** A "Run screen now" action
  (GUI + endpoint) that re-runs `run_screen` for today. Needs a spec
  line on what a mid-session re-run may change: same-day rows are
  rewritten, so rank, `failed_in_a_row` and eligibility could move —
  either forbid list changes after the first screen (re-run refreshes
  details only) or accept them explicitly.
- **B. Live detail for screened rows.** `describe` recomputes the
  history check for `eligible_fails` rows whose `failed_check` is
  `history` (and maybe `warming`-flavoured details generally), so the
  page shows whether the history is loaded *now* while status, rank
  and fails stay the screen's record. Display-only; no behaviour
  change to what cycles trade.
- **C. Do nothing.** The detail is a screen record; it refreshes at the
  next screen. Rejected unless Marc prefers it — the 13 Nov incident
  showed the confusion is real.

Note for B: `_warming_details` is already batched for the whole pool;
reusing it for these rows is cheap. For A: `run_screen` already guards
non-trading days and writes its own `sync_runs` row; the endpoint's
`simulated_only` gate and any cycle-running guard need spec cover.

## Build

1. Whatever the spec PR decides, plus:
   - tests for the pass and fail case of the chosen behaviour;
   - `docs/CHANGES.md` entry;
   - if A: the endpoint stays behind the request gate and sign-in like
     every route; the GUI action follows `design/` (ask for the screen
     text rather than inventing it).

## Not in scope

- Item 092 (the premature source gap itself) — this item is only about
  what the Universe page shows afterwards.
- Changing the once-daily screen schedule or the hysteresis rules.
