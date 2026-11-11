# 077 · feat · Universe UX

Kickoff for Devin. Start the session with: "Build docs/kickoffs/077-feat-universe-ux.md."

Backend (a sync-run record and two endpoints) and frontend. No change to the screen rules, the pool or warm-up. Spec: `docs/SPEC.md` → **Universe screen** (item 077); `docs/CHANGES.md` entry [077]. Design: `design/screens/Universe.dc.html`, `design/CHANGES.md` entry [077]. Issue: #234.

```text
[077] Universe UX. Goal: Universe says its mode once, shows what the app is
loading and what failed (Data sync), gives each symbol one consistent
status with progress, and stops colouring zeros.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md → Larger universe (all), Universe screen (item 077),
   Concurrent requests (078) → Data syncs; docs/CHANGES.md [077].
3. design/CHANGES.md [077]; design/screens/Universe.dc.html with
   Universe.png and Universe-390.png.

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.
Two PRs if it grows past ~800 lines: steps 1–2, then 3–6. Put
"Closes #234" in the last one ("Part of #234" in the first).

1. Sync runs. A new table `sync_runs` (new migration): job (daily_bars,
   screen, bars_5m, history, earnings, corporate_actions, fred, ecb), started
   at, finished at, status (running, ok, partial, failed), done, total,
   failures JSON (symbol or source, error in plain words, next retry). Each
   job writes its row at start and updates it as it goes (at most once per
   second) and at the end. Keep 30 days. Unit tests with the fake sources:
   a partial run stores its failures; a running job's row shows progress.

2. API. GET /api/universe/sync returns the latest run per job and, while
   history loads, symbols ready of total, and the time left from the job's
   pace so far (null until 5 symbols are done). POST
   /api/universe/sync/{job}/retry reruns a failed job for its failed
   symbols (not during a cycle; 409 then).

3. Status per symbol. The pool endpoint returns per symbol: status
   (warming_up, ready, eligible, held, excluded, eligible_fails) and, for
   warming_up, daily bars and 5-min sessions loaded of needed; for ready,
   the next screen time; for excluded, the failed check with its numbers.
   "ready" = history loaded, not yet screened. One chip and one line from
   these; warming_up gets two small progress bars.

4. Page, top to bottom as the design: mode banner (once; the old note
   under the list and the duplicate in How the screen works go), tiles
   (Pool, Eligible, Held, Excluded, Warming up, Ready; colour only above
   0), Data sync next to Today's screen (How the screen works as a fold
   inside it), Pool by sector (pool, eligible, sector rank from 068;
   clicking filters the pool, ?sector= in the URL), Pool.

4b. Data sync, quiet when fine: while every job's last run is ok and on
   time and none is running, the card is one line ("All 5 jobs ran on
   time · last 18:46 · Show jobs"); it opens by itself when a job failed,
   is late or is running, with the reason in its header ("open: 1 failed ·
   1 running"). Below 760 px a job row stacks: name and time, the result on
   its own line, then Retry.

5. Pool table. Status is the third column (after Symbol), so it shows at
   390 px. The remove question stays in the visible part of the table
   (position: sticky) when the table scrolls. The fold row names its
   filter and count; "name pending"
   for an unknown name; columns without values before the first screen get
   the hint "after the first screen" instead of dashes; the × opens an
   inline confirm row (Remove, Cancel; focus on Cancel); the add field's
   placeholder in normal case, typed text uppercased.

6. Screenshots in docs/screenshots/077/: 1440 and 390 in screened mode
   with a symbol warming up, fixed mode, a failed job with Retry, and the
   remove confirm.

Rules: no network in tests; job progress written by the job, never
guessed by the GUI; the retry respects rate limits and the live-first rule
(078); nothing in design/.

Done when: Data sync shows every job's last run from sync_runs; a job that
fails for one symbol shows it with its error and a working Retry; the
screen matches Universe.dc.html at 1440 and 390 with no sideways scroll;
make test and the e2e pass.
```

## Why

Review of the running app on 11 November (fixed mode, 72-symbol pool warming up):

- Excluded 0 in red and Eligible 0 in amber; "Sorted by rank" twice.
- "60 more eligible symbols" while Eligible was 0; "warming up" next to "ready — enters at the next screen".
- A column of dashes under every name, price and volume.
- Nothing showed what the app was loading, how far it was, or what failed.

## Assumptions

- Writing a progress row at most once per second costs nothing noticeable next to the requests themselves.
- "Time left" is a plain extrapolation of the job's own pace; it is labelled "about".

## Open questions for Marc

None. Decided without asking: `sync_runs` keeps 30 days; Retry is refused during a cycle instead of queued.
