# How entries are chosen when more pass than there are slots

Read-only analysis by Devin, 31 October 2026 (led to PR 33). Kept as reported; findings may since have been fixed (see docs/CHANGES.md).

Short answer
There is no "pick the best 4" step anywhere. The agent processes candidates one at a time, in a fixed order, giving each an independent model call and then gating and submitting each entry sequentially. The first candidates whose entries survive every gate consume the position slots; once open positions + working entry orders reach 4, the max_open_positions gate blocks every later entry. Selection is therefore first-come-first-served in rank order — the rank is the selection mechanism.

The pipeline (each stage narrows the candidate set)
1. Candidate order is fixed before the cycle starts
The review order comes from MarketDataService.universe() (service.py:54-72):

Screened mode: eligible symbols in UniverseScreenRow.rank order. Rank is assigned at the daily 08:10 screen: 20-session return minus SPY's return, ties broken by dollar volume (SPEC.md line 582).
Fixed mode: the universe.symbols order from config — rank is None.
Held symbols and SPY are appended, but held symbols get pulled to the front anyway in the next step.
2. Rules pre-filter builds the review list
evaluate_entry_rules marks each symbol held, pass, or fail (SPEC.md lines 227–237: above SMA 50, 1h EMA20 > EMA50, RSI 40–70, outside earnings blackout, regime not Down+High, not the 15:46 cycle). The cycle then builds reviews = held positions first, then passing candidates (cycle.py:547-563), matching the spec: "held positions are called first" (line 212).

3. Call cap trims the list before the model
At most max_calls_per_cycle (10) real model calls per cycle (cycle.py:573-588). Two subtleties:

A candidate with a stale/missing/implausible quote gets no_call with a price_reason without consuming a call slot — the next candidate moves up (cycle.py:789-804).
Candidates past the cap are recorded as no_call / call_cap (cycle.py:856-886). So with >10 review items, lower-ranked candidates never reach the model at all.
4. Each candidate gets an independent enter/skip decision
The model sees one symbol at a time and answers enter or skip. It never compares candidates. Code then validates: stop 1.5–4× ATR(1h) below price, reward-to-risk ≥ 1.5, confidence ≥ 0.55 (SPEC.md lines 255–262). Under decision.v2/v3 it picks among code-built options; no valid option → no_call/no_options (cycle.py:769-788).

5. Entries are gated and submitted immediately, in walk order
executor.apply_decision runs right after each model call — before moving to the next candidate (cycle.py:826-854). For an enter, evaluate_entry_gates runs in spec order (gates.py:109-335):

kill_switch → paused → reconciliation → already_held/entry_pending →max_open_positions→ sector_cap → daily_loss_limit → shock_brake → earnings_blackout → reentry → drawdown_kill → losing_streak (halves risk, never blocks) → sizing → order_sanity, then a spread check and a full re-gate on a fresh quote just before submit (executor.py:2745-2798).

The critical mechanics:

open_count = open positions + working entry orders; ≥ 4 blocks (gates.py:151-158). A "working entry" is any entry order submitted/accepted/partially filled and not yet cancelled, expired or rejected (executor.py:2464-2499). So the 4th submitted entry blocks the 5th candidate even if nothing has filled yet — an unfilled limit order holds its slot.
Working entries also spend budget: unfilled qty × (limit − stop) counts toward the 2.5% open-risk cap, unfilled qty × limit toward cash (SPEC.md line 298). Later candidates are sized against what earlier entries left.
max_orders_per_cycle (6) caps total submissions per cycle via order_sanity.
A blocked entry gets final_action = "blocked" with gates_result.blocked_by (e.g. "max_open_positions") stored on the decision (executor.py:2643-2664).
What this means in the >4-candidates scenario
Rank decides priority. Highest-ranked candidates (strongest 20-session relative strength vs SPY, or config order in fixed mode) get their model call and gate evaluation first. There is no post-model re-ranking or portfolio optimization.
A blocked high-ranked candidate doesn't save its slot — a later candidate in a different sector, or after a sizing reduction, can still enter. E.g. rank-1 blocked by sector_cap frees the way for rank-2.
Exits earlier in the same cycle free slots. Held positions are reviewed before candidates and their exits execute immediately, so a position sold in the held phase opens a max_open_positions slot for a candidate later in the same walk.
Candidates beyond the 10-call cap are never considered — they're no_call/call_cap regardless of how good the setup was. The call cap can bind before the position cap does.
In supervised mode, an entry that passes all gates becomes a pending approval instead of an order, and pending approvals don't count toward the gates (executor.py:2697-2728). Gates re-run on approval — so effectively Marc chooses which of the queued entries actually fill.
Shadows apply the same rule: candidates sorted by (rank, original order) via _rank_key, then capped and gated the same way (runner.py:165-167, runner.py:565-565).
Spec basis
Order: "Model calls go to held positions first, then to candidates that pass the rules in rank order" (SPEC.md line 584).
Cap: "Max open positions | 4 | Skip" (line 286); "Entries in one cycle are gated one by one in submission order, each against the budget left after the earlier ones" (line 298).
Rank definition: line 582.
One nuance worth flagging: in live, candidates are not explicitly re-sorted in cycle.py — the code relies on universe() already returning rank order in screened mode. Shadows do re-sort explicitly. The behaviors coincide today, but if universe() ordering ever changed, live's candidate priority would silently change with it.
