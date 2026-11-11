# 067 · feat · Economic releases

Kickoff for Devin. Start the session with: "Build docs/kickoffs/067-feat-economic-releases.md."

Issue #215. Spec: `docs/SPEC.md` → Risk management → **Economic releases**, plus the lines `docs/CHANGES.md` entry [067] names. Design: `design/CHANGES.md` entry [067]. One rule (the FOMC window); everything about the 08:30 releases is collected, not acted on.

```text
[067] Economic releases. Goal: the agent knows the FOMC decisions and the
CPI, jobs and PCE release dates; it makes no new entries in the FOMC window
(the 13:46 cycle on decision days); every cycle stores the next release;
entries and positions held into an 08:30 release are tagged; the GUI shows
it all. Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md: Other data (the new row), Rules pre-filter, the risk gate
   table (FOMC window row), Economic releases, Data model (economic_events,
   cycles.next_release), Insights → Slices, the dashboard "Next release"
   paragraph, Settings screen; docs/CHANGES.md [067].
3. design/CHANGES.md [067]; design/screens/Main.dc.html (cycle counter),
   Settings.dc.html → Entry rules → Economic releases, Insights.dc.html
   (What each filter removed, Slices).

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.

STEP 0 · sources (results in the PR before code)
- FRED release ids for CPI ("Consumer Price Index"), the jobs report
  ("Employment Situation") and PCE ("Personal Income and Outlays"), from
  fred/releases with our key. Fetch fred/releases/dates for each, 60 days
  ahead, and paste the next dates.
- Whether Finnhub's economic calendar is on our plan (one call). If it is,
  say so in the PR; FRED stays the source.

STEP 1 · backend (tests first)
- Config, added to backend/config.example.yaml in this PR:
    events:
      fomc:
        enabled: true
        time: "14:00"                 # New York
        block_minutes_before: 30
        block_minutes_after: 45
        dates: [2026-12-09, 2027-01-27, 2027-03-17, 2027-04-28, 2027-06-09,
                2027-07-28, 2027-09-15, 2027-10-27, 2027-12-08, 2028-01-26]
      fred_releases: {cpi: <id>, jobs: <id>, pce: <id>}   # from step 0
      release_time: "08:30"           # New York, for the FRED releases
      lookahead_days: 60
      next_release_sessions: 2
  (decision dates = the second day of each meeting, from the Fed's
  calendar of 9 Nov 2026.)
- data/fred.py (from 060) gains release dates; a daily job at 08:00 New
  York with the earnings calendar fills economic_events (Alembic migration):
  type, date, time New York, source, fetched_at. FOMC rows come from config
  at startup and on settings save.
- Rules pre-filter: check fomc_window, after the earnings blackout: a
  cycle whose start time is within [decision − before, decision + after] on
  an FOMC date → the candidate fails with check fomc_window. Options still
  built and tracked like any rules failure. Strategy override
  events.fomc.enabled. Risk gate at order time with the same test (an entry
  approved in supervised mode inside the window is refused).
- Per cycle: next_release on cycles (JSON: type, time New York, sessions
  away; the first release within next_release_sessions sessions, or null).
- Tag release_next_morning: on an entry decision and on each open position
  at the session's last cycle, when the next session opens after an 08:30
  release dated that next session. Stored on the decision (entries) and on
  the position (a list of the release dates it was held into).
- Needs you: from 60 days before the last FOMC date in config, a warning
  item "FOMC dates in config end on <date>: add the next year from the
  Fed's calendar", with "Open Settings".
- Missing FRED (no key, down): FOMC rule still works, next_release falls
  back to FOMC only, data-source warning as usual; never blocks a cycle.
- Tests (fake clock, fake FRED, no network): the 13:46 cycle on 9 Dec 2026
  fails fomc_window and 14:46 passes; a held position is reviewed in the
  13:46 cycle; next_release on 15 Oct is CPI 16 Oct 08:30 with a fake
  calendar; a position open at 15:46 the day before a CPI date gets the
  tag; FRED down → FOMC still blocks, next_release null for CPI; the Needs
  you item appears on 27 Nov 2027 (60 days before 26 Jan 2028).

STEP 2 · frontend
- Dashboard: the next release under the cycle counter ("CPI tomorrow 14:30
  · 08:30 NY", "FOMC today 20:00 · no entries in the 13:46 cycle"); the same
  as a chip on a tagged open position card.
- Settings → Entry rules → Economic releases: FOMC window switch, the two
  minute fields (labelled before and after), the FOMC dates (count, next,
  last), the 08:30 releases tracked with the next one.
- Insights: "Rules · FOMC window" in What each filter removed; Slices gains
  "Release next morning".
- Labels and dates through the 056 label map and formatter.

STEP 3 · screenshots in docs/screenshots/pr-<n>/: the dashboard strip
with a release and an FOMC day, Settings → Entry rules, the Insights rows,
a tagged position card.

Rules: no network in tests; no datetime.now() in engine code; prompts
untouched. Builds on 071 (entries allowed at 15:46, cutoff 15:50).

Done when: the FOMC window blocks only the 13:46 cycle on decision days;
next_release and the tag are stored; the GUI shows them as designed; make
test and the e2e pass.
```

## Why

The earnings blackout covers one stock's report; nothing covers the releases that move every stock at a known time. For FOMC (14:00, inside the session) the risk is concrete: an entry in the 13:46 cycle sits through the statement 14 minutes later, so it gets a rule. For the 08:30 releases a rule is less clear: any entry made the session before holds through the release overnight, so the only real rule would block a whole session. Marc chose (11 November) to collect first and decide in item 061 with data.

## Open questions for Marc

None. Decided: no rule for 08:30 releases (collect first); FOMC window 30 minutes before to 45 after.
