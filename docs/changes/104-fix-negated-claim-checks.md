---
item: 104
type: fix
title: Negated claim wording flagged as unsupported_claim
status: merged
issue: 310
kickoff: —
spec_prs: []
build_prs: [311]
---

# 104 · fix · Negated claim wording flagged as unsupported_claim

## 2026-11-18 · build PR #311

Found analysing the stored `llm_calls`. The claim checks in `backend/app/decide/output.py` flagged any text containing "lock(s) in" when the locked R was 0 or less, and any path word near a path noun, without checking whether the sentence negated it — so theses saying the stop "does not lock in profit yet" were recorded as `unsupported_claim` (live calls on PG decision.v4 and COST decision.v6). Claim words are now matched per sentence and per clause and skipped when negated ("not", "n't", "never", "no", "without", "cannot" before the claim word; "locks in no/nothing/zero" after it); a negation covers only its own clause. The locks-in pattern also covers "locking in" and "locked in", and the v4/v5 path claim gets the same negation handling. Claims still never gate the decision. No spec, config or prompt change.

Not changed: held-position level claims still have no target anchor for `levels_to_target` ids — spec-sensitive, left for a separate item if wanted.