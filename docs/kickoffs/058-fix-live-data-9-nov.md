# 058 · fix · Live data of 9 November

Kickoff for Devin. Start the session with: "Build docs/kickoffs/058-fix-live-data-9-nov.md."

From your live-data report of Monday 9 November (paper; flat since 6 Nov, 219 candidate decisions, all `no_call`). The engine did what the spec says; four small things need fixing, two need a query first, and Marc decided to let the v5 trend gate pass a sideways regime. Spec text: `docs/CHANGES.md` entry [058].

```text
[058] Live data of 9 November. Goal: no row is retried every cycle for
months, a price_stale warning says how stale, a rebuild doesn't raise an
error, a restore test that isn't set up doesn't show an old failure, and
the v5 trend gate lets candidates through in a sideways regime.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/CHANGES.md [058] and the SPEC.md text it names: Outcome tracking →
   Option outcomes → Missing bars; Alpaca integration → Market price (the
   price_stale warning); Logging → Frontend errors → New version loaded;
   VPS deployment → Backups → Not set up; Decision v5 → Trend gate in code.

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

STEP 5 · trend gate allows sideways (Marc, 9 Nov; its own PR, trading path)

- New key decision.v5.trend_gate_regimes: [up, sideways] in
  backend/config.example.yaml next to trend_gate, read through v5_config,
  a strategy override like trend_gate. trend_gate: false still turns the
  whole gate off.
- decide/v5.py trend_gate_detail: trend_1h must be up (unchanged);
  regime_trend must be in trend_gate_regimes. Detail text names the set:
  "trend_gate: regime_trend=down (allowed up, sideways)". cycle.py uses it
  unchanged otherwise. The v4 checks_gate is not touched.
- No prompt change: decision.v5 already treats a regime that isn't up as a
  conflict ("Code usually only asks you about…"). Don't edit it.
- Settings → Decision gates: a row "Regimes that pass the trend gate" with
  up and sideways as checkboxes (down is not offered), default up +
  sideways, shown as changed from default when it differs; per-strategy on
  Strategies like trend_gate.
- Tests first (they fail before): a v5 candidate with trend_1h up and
  regime_trend sideways gets a model call; regime_trend down gets no_call ·
  checks with the new detail; trend_1h mixed with regime up still gets
  no_call; a strategy override [up] restores the old behaviour; the key
  missing from an old config.yaml falls back to the example's default
  (config layers, item 050).
- After the merge, in the PR or a comment after one full session: how many
  sideways candidates got a call, the actions, and the model cost.

Rules: no network in tests; no datetime.now() in engine code; prompts
untouched; one Alembic migration if the touches retry needs a column (never
edit a merged one); the price threshold is not changed here, and the
trend gate changes only as step 5 says.

Done when: the step 0 answers are in the PR; the backfill log line shows
"0 waiting" for rows older than the give-up time; a price_stale line names
the age; a sideways-regime candidate with trend_1h up gets a model call;
restarting the frontend build with a tab open logs one INFO and no
error; Settings → Server says "not set up" for the restore test; make test
passes.
```

## Why

The report of 9 November showed a healthy, flat day: 219 candidate decisions over 3 cycles, all `no_call` (138 rules, 67 no options, 7 trend gate, 7 price_stale), no model spend on live decisions. Four details:

- **17 rows retried every cycle.** `outcomes.py` only gives up on a row when its bars are pruned (about 120 sessions). Until then the decision's option touches stay empty and its verdict can't draft.
- **price_stale on AMT, MCD, SHW, LIN, AXP every cycle.** These are large, liquid stocks, not thin ones. The market price is the last IEX trade, which must be at most 60 s old; for high-priced names IEX often has no trade in the last minute. The warning doesn't say how old the trade was, so we can't tell a small gap from a feed problem.
- **Stale frontend chunk logged as ERROR** at 15:32 UTC after a rebuild, with a warning notification. The reload already happens; only the level is wrong.
- **Restore test "last failed Oct 1"** while it isn't set up (spec, item 050: not set up is not a failure).
- **The trend gate stopped every candidate that passed the rules** (SBUX, HD, ACN, NKE, WMT): v5 passed only an `up` regime, so a sideways market meant no entries at all. Marc decided on 9 November to let `sideways` through for live. `trend_1h` must still be up and a `down` regime never passes. Expect the model to skip most of these while the regime is also high-volatility: the v5 prompt then asks for every check to agree. Insights → "What each filter removed" keeps measuring what the gate still removes.

## Not in this item

- **Readiness, price checks.** Today 7 of 219 candidate decisions (3.2 %) were blocked by price checks; the criterion is ≤ 5 % and counts every candidate decision, including rules failures. No change.

## Open questions for Marc

None for the build. After step 2's measurement: change the 60 s limit for thin-on-IEX names, or look at another price source?
