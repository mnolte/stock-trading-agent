# 071 · feat · New entries in the last cycle

Kickoff for Devin. Start the session with: "Build docs/kickoffs/071-feat-entries-in-last-cycle.md."

Marc's decision of 11 November: drop the rule that the 15:46 cycle makes exits only. Spec: `docs/SPEC.md` → Market, schedule and data → Rules pre-filter (the paragraph after the list), and `docs/CHANGES.md` entry [071].

```text
[071] New entries in the last cycle. Goal: the 15:46 cycle can enter like
any other; no entry order is ever sent after 15:50 New York. One small PR.
Start from the latest main.

Read first: AGENTS.md; docs/SPEC.md → Rules pre-filter and the paragraph
after it; Alpaca integration (entry limit cancelled after 10 minutes);
Supervised mode; docs/CHANGES.md [071].

Move this kickoff to docs/kickoffs/archive/ with git mv in this PR.

1. Entry cutoff (tests first). New key execution.entry_cutoff: "15:50"
   (New York) in backend/config.example.yaml. The executor never sends an
   entry order at or after the cutoff on that session: the entry becomes
   final skip with change reason entry_cutoff (label "too close to the
   close"). A supervised approval that arrives after the cutoff expires
   with the same reason and the approval card says so. Exits, stop moves
   and protective stops are never affected.
2. Then flip the default: schedule.last_cycle_no_entries: false in
   backend/config.example.yaml, in the same PR (the code already reads it
   in rules/prefilter.py, settings_service.py and insights/service.py).
   Settings → Schedule: the last row reads "yes, sent by 15:50"
   (design/screens/Settings.dc.html).
3. Tests (fake clock, fake broker, no network): a 15:46 candidate passes
   the pre-filter and its order is sent at 15:46:20; an entry reached at
   15:50:00 is skipped with entry_cutoff; a supervised approval at 15:52 is
   refused; a held position's exit at 15:55 still goes out; with
   last_cycle_no_entries: true the old rule still works.
4. Insights: the entry-cycle slice's 15:46 row shows data from 11 November
   on instead of the rule row.

Rules: no network in tests; no datetime.now() in engine code; every order
still has its stop leg.

Done when: a 15:46 entry reaches Alpaca with its bracket in the simulated
run; nothing is sent after 15:50; make test passes.
```

## Why

The rule had been in the spec since before PR 12 with no written reason. Marc: 14 minutes is enough to fill, and the spread check stays. The entry limit is cancelled after 10 minutes if unfilled, so the cutoff at 15:50 keeps that window inside the session.
