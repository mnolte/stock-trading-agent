---
item: 113
type: feat
title: Decision v7: multi-day holds
status: ready
issue: 339
kickoff: 113-feat-decision-v7-multi-day-holds.md
spec_prs: [340]
build_prs: []
---

# 113 · feat · Decision v7: multi-day holds

## 2026-11-25 · spec PR

Marc, 25 Nov: in the model calls the model keeps saying there is very little time left or that an overnight hold is unfavorable, while the agent is built to hold a symbol for up to 5 days.

What causes it (decision.v5 and v6):
- **"Time left":** with under 120 minutes left, a target more than 2 ATR away is "very likely an overnight hold; weigh that as a conflict".
  - The 14:46 and 15:46 cycles have 74 and 14 minutes left.
  - Option targets are 2.25 ATR or more away.
  - So almost every candidate in 2 of the 6 cycles gets a conflict, and an unoutweighed conflict is a skip.
- **"Overnight":** in the last cycle, a hold with its stop inside the usual overnight gap needs confidence ≥ neutral + 0.10, or the model exits. Code then warns `hold_below_breakeven`.
- **No holding period:** neither prompt says a trade is meant to last days.
- **Dead setting:** `decision.v5.long_target_minutes` is editable but nothing reads it.
- **No block in code:** `execution.entry_cutoff` is 15:50 and `schedule.last_cycle_no_entries` is false.

Marc's decisions, 25 Nov:
- The fix and the changes waiting for the next prompt version go into decision.v7.
- 5 sessions is a guide for the model, not a time exit.
- v7 ships now; the market context (item 061) moves to decision.v8.

Spec (`docs/SPEC.md`):
- **New section Decision v7: multi-day holds:**
  - the causes;
  - the prompt changes (holding period, no time-left conflict, overnight gap risk only with a reason, no overnight confidence line, the stop at Alpaca);
  - snapshot v7 (`constraints.hold_sessions`, `position.sessions_left`, `position.stop` from Alpaca with `stop_from` and `stop_stored`);
  - the contract;
  - code (`hold_below_breakeven` without the overnight line for v7, `long_target_minutes` removed, no time exit);
  - eval cases;
  - a shadow "I · v7" for at least 10 sessions before Marc decides.
- **Decision v7: market context** is renamed **Decision v8: market context** (item 061). Snapshot v8 builds on v7. The last-cycle entry rule (item 071) goes with v8, since it needs step A's data. The VIX, breadth and release notes and the build plan line point to v8.

Design (`design/changes/113-feat-decision-v7-multi-day-holds.md`): Settings → Decision gates gets a Decision v7 group with "Holding period".

Assumptions:
- `sessions_held` keeps its meaning from v4. `sessions_left` is derived from it.
- When Alpaca's stop is unknown in a cycle, the stored stop is the best fact there is, so it is used and marked `stop_from: stored`.
