# 058 · fix · Live data of 9 November

Kickoff for Devin. Start the session with: "Build docs/kickoffs/058-fix-live-data-9-nov.md."

From your live-data report of Monday 9 November (paper; flat since 6 Nov, 219 candidate decisions, all `no_call`). The engine did what the spec says; four small things need fixing, and two need a query first. Spec text: `docs/CHANGES.md` entry [058].

```text
[058] Live data of 9 November. Goal: no row is retried every cycle for
months, a price_stale warning says how stale, a rebuild doesn't raise an
error, and a restore test that isn't set up doesn't show an old failure.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/CHANGES.md [058] and the SPEC.md text it names: Outcome tracking →
   Option outcomes → Missing bars; Alpaca integration → Market price (the
   price_stale warning); Logging → Frontend errors → New version loaded;
   VPS deployment → offsite backup → Not set up.

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.

STEP 0 · two queries on the live database, results in the PR (no code)

a. The 6 closed positions with after_exit_status = 'pending': symbol, exit
   fill time, and the session that ends their 5-session window
   (performance.tail.after_exit_sessions). Expected: all exited on or after
   2 Nov, so all windows end 10–12 Nov. Any window that already ended is a
   bug: say which, and why the after-exit job skipped it.
b. The 17 rows behind "option-touches backfill: … 17 missing bars":
   decision id, symbol, cycle time, whether the symbol is in today's
   universe, and how many 5-minute bars the cache holds for the window
   versus how many the window needs. Expected: symbols that left the
   universe after a screen, so their bars stopped downloading.

STEP 1 · option touches: fetch once, then give up (one PR)

- backend/app/engine/outcomes.py, the backfill loop for decisions and the
  one for shadow decisions. For a row whose window isn't covered by stored
  bars: fetch that symbol's 5-minute bars for the window once with
  data/alpaca.py fetch_5m_bars (historical, within the data limiter), store
  them, and retry the touches.
- Still not covered and the 5-session time is more than
  outcomes.touches_give_up_sessions (2) trading sessions ago: every option
  without a result gets "unavailable", the row is done, and the verdict can
  draft (facts show "option touches unavailable").
- A row is tried at most once per session (store when it was last tried),
  not every cycle. The INFO line becomes "option-touches backfill: n filled,
  n fetched, n waiting, n unavailable".
- Config: add outcomes.touches_give_up_sessions: 2 to
  backend/config.example.yaml in this PR (the code reads it).
- Verdict facts and the decision drawer's Outcome block accept the new
  value "unavailable" (shown as "unavailable", not as null or a dash).
- Tests (fake clock, fake broker, no network): a row with bars missing is
  fetched once, fills when the fetch returns the bars; a row whose fetch
  returns nothing is retried at most once per session and gets
  "unavailable" on the 3rd session after its 5-session time; its verdict
  drafts afterwards. Same for a shadow decision.

STEP 2 · price_stale says how stale (same PR or its own)

- engine/executor.py (the market-price check) and engine/cycle.py: the
  price_stale WARNING names the symbol, the last IEX trade's age in seconds
  and its price ("price_stale MCD: last IEX trade 143 s old at $291.40,
  limit 60 s"). No threshold change.
- Test: the log line carries the age and price.
- After one full session with the new line, add to the PR (or a comment):
  for AMT, MCD, SHW, LIN, AXP the median and 90th percentile age of the
  last trade at each cycle. That number decides the follow-up (Marc):
  a small threshold change for thin-on-IEX names, or another price source.
  Don't change the threshold in this item.

STEP 3 · rebuild while a tab is open (frontend)

- src/errors.ts already reloads once on vite:preloadError. Its report to
  /api/logs/client must arrive as INFO ("frontend reloaded after a new
  build", with the failed file name), not ERROR, so it creates no error
  group, no Needs you line and no warning notification. A second failure
  within the guard window stays an ERROR as today.
- Tests: errors.test.ts asserts the level of the first report; a backend
  test that an INFO client report creates no error group.

STEP 4 · restore test "not set up" (backend + Settings → Server)

- ops/service.py: when the backup password or storage credentials are
  missing, the status is "not set up" and the stored result of an earlier
  run (the 1 Oct failure: "no backup password stored") isn't shown as the
  current state. Keep it in the history if there is one.
- Test: with credentials cleared after a failed run, the Server card shows
  "not set up" and no failure date.

Rules: no network in tests; no datetime.now() in engine code; prompts
untouched; one Alembic migration if the touches retry needs a column (never
edit a merged one); the trend gate and the price threshold are not changed
here.

Done when: the step 0 answers are in the PR; the backfill log line shows
"0 waiting" for rows older than the give-up time; a price_stale line names
the age; restarting the frontend build with a tab open logs one INFO and no
error; Settings → Server says "not set up" for the restore test; make test
passes.
```

## Why

The report of 9 November showed a healthy, flat day: 219 candidate decisions over 3 cycles, all `no_call` (138 rules, 67 no options, 7 trend gate, 7 price_stale), no model spend on live decisions. Four details:

- **17 rows retried every cycle.** `outcomes.py` only gives up on a row when its bars are pruned (about 120 sessions). Until then the decision's option touches stay empty and its verdict can't draft.
- **price_stale on AMT, MCD, SHW, LIN, AXP every cycle.** These are large, liquid stocks, not thin ones. The market price is the last IEX trade, which must be at most 60 s old; for high-priced names IEX often has no trade in the last minute. The warning doesn't say how old the trade was, so we can't tell a small gap from a feed problem.
- **Stale frontend chunk logged as ERROR** at 15:32 UTC after a rebuild, with a warning notification. The reload already happens; only the level is wrong.
- **Restore test "last failed Oct 1"** while it isn't set up (spec, item 050: not set up is not a failure).

## Not in this item

- **The trend gate.** v5 allows entries only when `regime_trend` is up, so a sideways regime means no entries at all. That is the intended rule since KO. Judge it after about 20 sessions with Insights → "What each filter removed" (options are built and tracked for gate-blocked candidates), not on one rebound day.
- **Readiness, price checks.** Today 7 of 219 candidate decisions (3.2 %) were blocked by price checks; the criterion is ≤ 5 % and counts every candidate decision, including rules failures. No change.

## Open questions for Marc

None for the build. After step 2's measurement: change the 60 s limit for thin-on-IEX names, or look at another price source?
