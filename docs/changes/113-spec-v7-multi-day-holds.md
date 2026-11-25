---
item: 113
type: spec
title: Decision v7 also covers multi-day holds
status: merged
issue: 339
kickoff: —
spec_prs: [999]
build_prs: []
---

# 113 · spec · Decision v7 also covers multi-day holds

## 2026-11-25 · spec PR

Marc, 25 Nov: in the model calls the model keeps saying there is very little time left or that an overnight hold is unfavorable, while the agent is built to hold a symbol for up to 5 days. He asked for the fix to go into decision.v7 (item 061, #206) together with everything already planned for it.

What causes it (decision.v5 and v6):
- **"Time left":** with under 120 minutes left, a target more than 2 ATR away is "very likely an overnight hold; weigh that as a conflict".
  - The 14:46 and 15:46 cycles have 74 and 14 minutes left.
  - Option targets are 2.25 ATR or more away.
  - So almost every candidate in 2 of the 6 cycles gets a conflict, and "a candidate with a conflict that nothing outweighs is a skip".
- **"Overnight":** in the last cycle, a hold with its stop inside the usual overnight gap needs confidence ≥ neutral + 0.10, or the model exits. Code then warns `hold_below_breakeven`.
- **No holding period:** neither prompt says a trade is meant to last days.
- **Dead setting:** `decision.v5.long_target_minutes` has a Settings control that changes nothing; the prompts say "120" in words.
- **No block in code:** nothing stops entries in the last cycle. `execution.entry_cutoff` is 15:50 and `schedule.last_cycle_no_entries` is false.

Spec changes (`docs/SPEC.md` → Decision v7):
- **Section "Also in v7: multi-day holds"** with the causes. v7 is made with these changes even if step A keeps no market field.
- **Step A** also measures holding overnight:
  - entries and skips per cycle, and how many skips name the time left;
  - option touches of candidates skipped late;
  - last-cycle exits against what holding would have made, and gaps of the holds, split by stop inside or outside the usual gap and by an event the next morning.
- **Step B, prompt:**
  - a holding period (`decision.v7.hold_sessions`, 5) with `position.sessions_left`;
  - the time-left conflict goes;
  - overnight gap risk is a conflict only with a reason (earnings, a release, a kept market field);
  - the overnight confidence line goes, the exit line stays;
  - the snapshot carries the stop Alpaca holds (analysis 050, point 7);
  - a last-cycle entry rule only if step A asks for one (item 071).
- **Step B, code:**
  - `hold_below_breakeven` fires on the exit line only for v7;
  - `decision.v5.long_target_minutes` is removed;
  - `decision.v7.hold_sessions` gets a Settings control.
- **Eval:** five new cases. The shadow comparison adds entries per cycle and overnight results.

Spec-only: the prompt, snapshot, code and eval are built under item 061 step B. The 061 kickoff is merged and unchanged; Marc gets a paste-ready prompt for Devin with the step A additions.

Assumption: 5 sessions is a guide for the model, not a time exit in code. Nothing in the app sells a position after 5 sessions today. Open question 1.

Open questions for Marc:
1. Should a position that reaches `hold_sessions` be sold by code (a time exit), or is it only a guide for the model, as written now?
2. v7 waits for step A, which can't start before the 10 December session, plus the analysis and your choice: about 3–4 weeks of the current behaviour. Alternative: ship v7 now with only these changes and the stop field, and put the market context in v8.
