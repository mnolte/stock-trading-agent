# Decision v4 — ranked candidates

Analysis for the decision.v4 spec, to be written after PR 33 (entry
selection). Read-only: no code, prompt, config or database changes were
made. All numbers come from a copy of the live database taken 31 October
(decisions and llm_calls, 19–30 October, live + shadow) and from the
5-minute bar store.

## Data, method and limits

| Sample | Calls | Window | Model |
| --- | --- | --- | --- |
| live decision.v1 | 229 | 19–23 Oct | gpt-4o-mini |
| live decision.v2 | 344 | 26–29 Oct | gpt-4.1-mini |
| live decision.v2 | 29 | 26 Oct | gpt-4o-mini |
| live decision.v3 | 27 (15 usable) | 30 Oct | gpt-4.1-mini |
| shadow "C · Options" decision.v2 | 453 | 26–30 Oct | gpt-4o-mini |

- decision.v3 went live 30 Oct 14:46. 12 of its 27 calls failed with 401
  (the dropped-keys bug fixed in #64), leaving **15 usable v3 calls**.
  Every v3 number below is a *hypothesis*, not a finding (<30 cases).
  Per the brief, the decision.v3 replay was **not** run because v3 calls
  exist; no other model calls were made. Cost of this analysis: $0.
- `outcomes.option_touches` is NULL on every row in the copy, so option
  outcomes were recomputed from `bars_5m` under the spec rules (fill in
  the 10-minute window, first stop/target touch, both-in-one-bar = stop,
  5-session end). **Bars stop at 30 Oct 19:55 UTC**, so every v2/v3
  window is right-censored: a `stop`/`target` touch is real, `neither`
  means "nothing yet". Rates below split out decided outcomes.
- Results cover one rally week on ~47 symbols. Decisions on the same
  symbol across cycles are highly correlated; treat differences under
  ~10 points and every n<30 cell as a hypothesis.
- `shadow_decisions.input_snapshot` is stored on only 11 of 4,735 rows;
  shadow inputs were joined from the linked live decision for trend/room
  features and from the shadow's own stored `options`, `levels` and
  `position` for claim checks (see B2).

## The six findings of 30 October, re-measured

| Finding | Then | Now | Did v3 fix it? |
| --- | --- | --- | --- |
| "locks in profit" on break-even stops | 4 live tightens 28–29 Oct | 8 scripted cases: calls 1448 (SBUX), 1602 (RTX), 1630 (ACN), 1668 (HD), 1727 (NKE) live; 1618, 1649, 1728 shadow. All 28–29 Oct | **Mostly, by code.** `locks_profit` now means above-entry, `locked_r` is shown, and the claim check fired once ("locks-in claim", call 1973). The break-even guard still protects v2/v1. In the 15 v3 calls: no case |
| Loose daily-trend claims | "daily uptrend" while mixed | 7 v2 calls claim a daily uptrend with `trend_daily` mixed: 969, 984, 1036, 1068, 1073, 1108, 1125 | **Yes (in-sample).** v3 gives `checks.trend_daily`; the 15 v3 calls cite "daily trend mixed" correctly as a conflict |
| Inconsistent resistance reasoning | "clear path" into levels | 7 calls say "clear path"/"manageable" with 3–5 levels in the way: 1398, 1499, 1555, 1558, 1608, 1611 (v2) and **1732 (v3 held: "target still has a clear path", 3 levels L4–L6)** | **No.** Still occurs under v3 |
| Confidence used as an action label | unanchored numbers | Skips: 4.1-mini answers 0.0, 4o-mini 0.4 — two different semantics. Enters quantized to {0.6, 0.65, 0.7} (4.1) and {0.65} (4o). Realized target-first of chosen options: 9/18 decided; stated mean 0.65 | **No.** v3 enter confidences 0.58–0.65 same cluster; held confidences run ~0.3 above `neutral_probability` (see §2) |
| Entry thesis anchoring held calls | thesis treated as fact | Still present: 3 calls with >60% word overlap (1490, 1601, 1918); ACN held "original thesis remains valid" repeatedly; PG held at −0.61R with conf 0.85 (call 1975) | **Partially.** `entry_thesis` is now labelled belief, but holds still lean on it |
| Irrelevant headlines, empty citations | name collisions, citation without effect | ~61–66% of offered headlines are about the symbol (title heuristic); ~12% of cited ones are not (generic market items, whale alerts, other companies). Enter rate 6% with citations vs 4% without (v2) | **Output side fixed; feed unchanged.** v3 cites almost nothing (1 id in 15 calls). The feed is still noisy; same stale headline re-cited across cycles (XOM "Jury Verdict in Mazza" 4 cycles running; id 62664176 on three HD calls) |

## 1. Choices against outcomes

777 candidate decisions have code-built options; computed touches:

- 226 of 777 (29%) had at least one offered option touch its target
  first. The model entered 12 of those 226.
- 41 model enters: chosen option touched **target 9, stop 9,
  neither (censored) 20, unknown 3**. Decided rate 9/18 = 50% (small n).
- The chosen option was **strictly worse than an alternative in only
  3/41**: SBUX 1424 (picked 4.11R D; only A hit target), LOW 1519,
  MCD 1573 — each time the model reached for the bigger target and only
  the nearer one would have paid. Option *picking* is already good;
  the problem is *when to enter at all*.
- Skips: 214/728 (29%) had a fillable option reach its target first —
  a high missed-opportunity rate in a rally week (baseline: always
  entering option A would have caught 221 targets but also 353 stops).

Feature ↔ target-first, per filled option (n=2,268; censored cells
excluded from rates where noted):

| Feature | Target-first rate |
| --- | --- |
| `trend_1h` up vs mixed | 14% vs 24% |
| `trend_daily` up vs mixed | 15% vs 22% |
| `momentum` strong / mixed / weak | 12% / 20% / 33% |
| `regime_volatility` high vs normal | 21% vs 9% |
| RSI <50 / 50–60 / 60–70 / 70+ | 24% / 23% / 7% / 0% (n=14) |
| rr 1.5–2.0 / 2.1–3.0 / >3.0 | ~24% / ~15% / ~5% |
| `room_atr` <0.5 / 0.5–1 / 1–1.5 / >1.5 | 18% / 19% / 16% / 21% |
| hour 10 / 11 / 12 / 13 / 14 | 18% / 11% / 15% / 22% / 24% |
| earnings ≤7d / 8–20d / 21–60d / unknown | 21% / 36% / 15% / 15% |
| news complete / empty / failed | 20% / 9% / 0% (n=4) |

The features the prompt tells the model to require — up trends, strong
momentum, RSI towards 70 — **anti-correlate** with target-first in this
window (hypothesis: one week, correlated rows, but the signs are large
and consistent). `room_atr` is flat: the v3 room filter's value is
unproven in-sample and needs the replay data before it is tightened
further.

Code-only baselines on the same 777 snapshots (always option A):

| Rule | Enters | Target | Stop | Neither | Decided win rate |
| --- | --- | --- | --- | --- | --- |
| Always enter | 777 | 221 | 353 | 197 | 38% |
| All checks agree (t1 up, regime up, td≠down, mom≠weak) | 205 | 34 | 55 | 111 | 38% |
| Strict (td up + momentum strong) | 81 | 9 | 26 | 45 | 26% |
| Model (its own choice) | 41 | 9 | 9 | 20 | 50% |

The model's decided rate (50%, n=18 — hypothesis) beats the baselines',
but the mechanical "all checks agree" filter adds nothing over always
entering on decided rate, at 4× fewer trades. In this sample the model's
edge is selectivity, not the checklist it is told to apply.

## 2. Calibration

- Enter confidence vs target-first (v2, n=37): 0.6 bucket → 3T/2S (n=9);
  0.7 bucket → 6T/7S (n=28). **Not monotone**; stated ~0.65 vs realized
  ~50% decided / 22% of all enters. v3 (n=4): 0.58–0.65, all censored.
- Skip confidence is **anti-informative**: stated 0.0 → 34% of those
  setups had a target-first option; 0.4 → 27%; 0.5 → 13% (n=728). The
  two models use different semantics (4.1-mini: 0.0 on 290/302 skips;
  4o-mini: 0.4 on 312/397 skips, i.e. "below the 0.55 gate").
- vs the v3 edge gate: of 26 v2+ v3 enters with a computed break-even,
  all sit 0.15–0.30 above `breakeven_confidence`; none would have failed
  `be + 0.10`. The gate has never fired on a real enter.
- Held confidence vs `neutral_probability` (n≈70 held calls): the model
  sits ~0.2–0.4 above neutral almost everywhere — 0.7 vs np 0.14
  (RTX 1669/1687), 0.7 vs np 0.0 (2464), **0.85 vs np 0.15 on a −0.61R
  position (PG, call 1975, v3)**, 0.65 vs np 0.36 (NKE 1733, v3).
  "Start from neutral_probability" is not followed.
- PR 33 consequence: `p = be + 0.5(conf − be)` on these enters gives
  expected R of +0.28…+2.15 (mean ≈ +0.6). Realized decided win rate
  50% ≈ stated confidence, but the base rate it applies to (enter
  frequency 5%) means a miscalibrated 0.7 directly inflates the
  expected-profit ranking against other candidates.

## 3. Faithfulness

Scripted checks over all 1,055 called decisions plus ~70 manual reads
(≈30 enters, ≈25 skips, ≈15 held):

| Error type | Count | Versions/models |
| --- | --- | --- |
| "locks in profit" on a ≤0R stop | 8 | v2 4.1 (5), v2 4o (3); 0 in v3 (n=15) |
| "daily uptrend" while mixed | 7 | v2 4.1; 0 v3 |
| "clear path"/"manageable" with levels in the way | 7 | v2 4.1 (6), v3 (1) |
| strong/supportive momentum while `weak` | ~8 (verified ≥3) | v2 4o (7), v2 4.1 (1); incl. the ACN case 1490 |
| stale/wrong numbers | ~8, mostly shadow-side artifacts; real: "40.14538" (5 decimals) call 1576, "259.08" | v2 both models; the unrounded-input problem v3 fixed |
| "no news" with headlines present | 3 borderline | v2 4.1: 1402, 1509, 1616 ("no news directly supports…" — a relevance claim, not a falsehood) |
| invalid `option_id` | 4 | LOW held picked "E" (call 1689 → hold); 3 shadow calls picked unoffered T-ids → hold |
| stored checks | 1 explanation_check fail / 826; 2 claim-check fails / 15 | one is a false positive, see B1 |

Manual-read notes: held theses repeat `entry_thesis` phrasing even after
v3's "belief, not fact" framing; v2 theses recite a fixed litany
("1-hour and daily trends are up, and the market regime is X") that
often contradicts the listed conflicts in the same answer (COP 1604
entered with its own listed conflicts `obv down`, `rs negative`).
`news_used` under v2 is decorative — ids cited, no stated effect.

## 4. Consistency

- 318 same-snapshot pairs across live(4.1-mini) and shadow(4o-mini):
  **22 action disagreements** (94% agreement), incl. 9 live-enters the
  shadow skipped and 8 shadow-enters live skipped; 253 confidence-only
  diffs (the 0.0/0.4 semantic split).
- Consecutive live cycles, same symbol, action flip within one hour:
  **11**, incl. COP skip→enter→skip (calls ~1573, 1604, ~1630s) and TSLA
  enter→skip→enter (1497, ~1550s, 1637).
- Same-session skip↔enter with no material change: **~28 pairs**, median
  price drift ~0.3–0.5% — PG skipped at 10:46/11:46/12:46/13:46 then
  entered 14:46 (drift 0.34–0.59%); TSLA entered on 4 of 5 cycles on
  29 Oct (calls 1497, 1550, 1637, 1673), every chosen option stopped.

## 5. Held positions

233 held calls: **hold ~190, tighten_stop ~30, exit 0 — the model has
never exited.** Verdict on "is it right not to": probably — every
losing position (WMT −13.86, HD −21.96, SBUX −1.96, RTX −2.07) went
from enter to stop within hours; no held call sat above a clear
invalidation while the trend stayed broken. ACN held through weak
momentum for three calls and paid +88 — holding was right. Still, a
decision layer that *cannot* exit is untested on the trade that needs
it; and the inputs currently don't tell the model whether the entry
thesis still holds.

- Tightens turned into holds: v1 WMT proposed its own current stop 3×
  (no-op, code→hold: 508, 575, 646); v3 HD tighten→hold twice on
  `price_stale` (calls 1866, 1917), then executed the third (1973,
  stop 325.81, trail from 330.15).
- Shadow LOW ratcheted T1 six cycles in a row (1728→1977, stop
  203.61→206.60) — mechanically fine, behaviourally it spends a call
  every hour to trail pennies.
- Earnings exits: none have fired yet (HD Mon 16 Nov, NKE Wed 16 Dec,
  PG Tue 19 Jan) — cannot be measured; `scheduled_exit` is correctly
  handled in the theses read (HD 1732 mentions it without making it a
  conflict — as specified).
- One stand-out: SHEL held decision 1974 correctly listed conflict
  "target not set" — the position row lost its target (see B4). Good
  data-awareness; also shows `conflicts` can carry data problems.

## 6. News

- Headline relevance (title-vs-symbol heuristic): ~61% (v2) of offered
  headlines name the company; the rest are sector/generic feeds. ~88%
  of cited headlines are about the symbol.
- Citations do not move the action: enter rate 6% citing vs 4% not (v2);
  under v1 they were 15%/15%. v3's "only what changed the judgement"
  rule works (1 citation in 15 calls) — but then `news_used` is almost
  always empty, so the field no longer tells anyone what the model saw.
- Repeated stale citations: same headline re-cited cycle after cycle
  (XOM jury verdict ×4; 62664176 on 3 HD calls) — the model treats
  headlines as decor, not as new information.
- `failed`/`truncated` statuses: 13 called decisions had `failed`;
  handled correctly (LOW held with `price: unavailable` + news failed →
  hold, call 1620). The ~4.8k `failed` statuses sit on no_call rows
  where news is not fetched.
- News is fetched per called symbol; the earlier `news_status:failed`
  mass on no_call rows is storage noise, not a fetch problem.

## 7. Inputs

- Never/rarely used: `rank` (0 mentions — correctly ignored),
  `quote.plausible`/`spread_unknown` (~6 mentions of 826), `gap_pct`,
  `signal_bar`, `stop_reference_price`, `quote.*` provenance fields
  (timestamps, feed, fetched_at — ~300 chars never referenced),
  `days_to_earnings` (43 mentions in v1's era, 6 under v2 — the blackout
  rule is internalised).
- Heavyweights: `levels` ~1.6k chars, `headlines` ~0.86k (v2), `options`
  ~0.8k, `quote` ~0.36k — ≈80% of a v2 input (tokens_in mean: v1 1,744,
  v2 3,587, v3 4,318).
- Fields the model demonstrably needs but doesn't get: cycle context
  (free slots, other candidates — 6 enters blocked `max_open_positions`,
  1 `sector_cap` it could never see); position age and hours-Left
  (relevant to the never-used exit and the tighten churn); previous
  same-symbol decision (needed to explain any flip); for shadows, the
  input snapshot itself is not stored (B2).
- Misread/confused: v2 `original_invalidation` was `"unavailable"` for
  every v2 entry until the 29 Oct fix; `entry_thesis` id-rewrite garbles
  one phrase (B3); v1 `levels` format (bare price lists) — model
  invented L-ids anyway.

## 8. Outputs

- Invalid answers: 4 of ~1,000 (option E on a held LOW, call 1689;
  3 unoffered T-ids, shadow) — all correctly converted to hold.
- Always-empty: `data_flags` set 0 times in 15 v3 calls; `conflicts`
  empty on 10/15 — **including every hold, even with `trend_daily`
  mixed** — the conflict discipline is applied to candidates only.
- `news_used` correctly near-empty under v3.
- Did conflicts-first change anything? Can't isolate with n=15, but v3
  enters now carry the right conflict text ("daily trend mixed") and
  the theses read shorter; the action/confidence distribution is
  unchanged (still 0.58–0.65 enters, holds at ~np+0.3).

## 9. The model ↔ code line

Gates that blocked model enters (v2/v3 era): `spread` 7+5, `fresh_quote`
4 (`price_stale`/room at order time — unknowable for the model, rightly
code-side), `max_open_positions` 6, `sector_cap` 1, `edge_too_small` 0,
`data_flag` 0; v1 geometry failures 32. **All 86 v3 `no_options` calls
are the room filter** (one day!) — the builder now removes what the
model used to have to judge.

- Move to code: the mechanical trend/momentum checklist
  (t1 up ∧ regime up ∧ td≠down ∧ mom≠weak) — code already computes the
  checks; the model applies them inconsistently *and* they don't predict
  in-sample (§1). Either enforce or delete — see open question OQ1.
- Keep with the model: headline relevance, "is this level about to be
  broken", stop-choice among equals, thesis-vs-facts judgement, data
  flagging (it caught "target not set").
- New code work suggested by the data: `entry_thesis_status`
  (contradicted/still-valid, computed like `checks`), previous-decision
  field, confidence-sanity warning (see C7/C8/C18 below).

## 10. Models (same snapshots only, v2, n=318 pairs)

| | gpt-4.1-mini (live) | gpt-4o-mini (shadow) |
| --- | --- | --- |
| enters / 304 candidates | 20 (6T/7S) | 9 (2T/2S) |
| confidence values | {0.6, 0.65, 0.7} | {0.65} only — unusable for selection |
| action agreement | 94% candidates, 13/14 held | — |
| option preference | nearer targets (A/2R) | higher rr (B/3R, D/7.1R) |
| cost (304 calls) | $0.539 | $0.192 |
| latency median | ~2.2 s | ~2.2 s |

claude-haiku-5-5: no calls exist (key not set) — untested.

---

# Candidates for decision.v4

Ranked by expected impact. Ids C1…C23; each lists problem, evidence,
root cause, proposed change, expected effect, measurement, risk.
Sample sizes: *F* = finding (n≥30), *H* = hypothesis (n<30).

## Prompt

### C1 — give skip-confidence a defined meaning *(top 5)*
- **Problem:** skip confidence is an action label, not a probability;
  the two models use different semantics and it anti-predicts. Entry
  selection (PR 33) and the calibration record both need a usable number
  on the calls that dominate (728 skips vs 41 enters).
- **Evidence:** skip conf dist {0.0: 339, 0.4: 351, 0.5: 38}; target-
  first rates 34%/27%/13% by bucket (n=728). *(F)*
- **Root cause:** prompt defines confidence only for the taken action.
- **Proposed change:** define it as "your estimate that the best offered
  option reaches its target before its stop". Add a `best_option_id`
  field so the number has an anchor: `"option_id": chosen or null,
  "best_option_id": the option you'd take if forced`.
- **Expected effect:** a calibration sample on every call instead of
  only enters; lets skip-side probabilities feed `confidence_weight`
  fitting; also surfaces near-miss skips (0.5+ skips) for review.
- **Measure:** on the eval set, stated best-option probability vs that
  option's realized target-first rate, per 0.1 bucket.
- **Risk:** the number may stay quantized; mitigate by also logging
  `confidence` unchanged (action semantics) — two numbers, one anchored.

### C2 — anchor held confidence to neutral_probability *(top 5)*
- **Problem:** held confidence is systematically ~0.3 above neutral,
  up to 0.85 at np 0.15. The prompt's "start from np" rule is ignored.
- **Evidence:** ~20 calls at conf ≥ np + 0.3 incl. v3 calls 1975
  (0.85 / np 0.15 / −0.61R), 1733, 1917, 1973. *(F for v2, H for v3)*
- **Root cause:** prompt wording only; no checkable anchor.
- **Proposed change:** prompt: "conf above neutral_probability + 0.15
  requires a named input fact that changed the odds; conf above np + 0.3
  is almost never right." Plus a code-side warning `conf_above_neutral`
  (like `unsupported_claim`) so it is measured, not just advised.
- **Expected effect:** held confidence becomes a signal worth storing;
  catches the PG 0.85 case.
- **Measure:** fraction of held calls flagged; conf-vs-np spread.
- **Risk:** none — warning only, never gates.

### C3 — every conflict must be resolved, not just listed
- **Problem:** the model lists conflicts then ignores them (enters with
  `momentum weak` listed, holds with none). v3 holds never list
  conflicts even when the same condition would be one for a candidate.
- **Evidence:** COP 1604 entered with own-listed `obv down`/`rs
  negative`; IBM 1734/1868 listed "daily trend mixed" then entered
  (legal but unexplained); conflicts `[]` on 10/15 v3 calls incl. all
  holds. *(F for "unresolved conflicts exist", H for v3 rate)*
- **Root cause:** prompt asks for conflicts but not their resolution;
  held-side rules never demand any.
- **Proposed change:** "For each conflict, say in the thesis what
  outweighs it — 'manageable' is not an answer; if nothing outweighs it,
  skip/hold. List conflicts for held positions too when a check is
  adverse."
- **Expected effect:** fewer self-contradicting answers; conflicts
  become a machine-checkable record of what was waived.
- **Measure:** enters with listed-but-unresolved conflicts (manual read
  of eval set); conflicts-empty rate on adverse checks.
- **Risk:** longer theses; more boilerplate "outweighed by trend" text.

### C4 — ban unsupported superlatives about the path
- **Problem:** "clear path", "manageable levels", "strong support"
  claimed into 3–5 levels in the way; survived into v3 (call 1732).
- **Evidence:** 7 calls: 1398, 1499, 1555, 1558, 1608, 1611, 1732. *(H:
  rate per version ~2–7%)*
- **Root cause:** prompt doesn't bind path-claims to `levels_in_way`
  distances; claim check covers level-attribution only.
- **Proposed change:** prompt: "Never call a path clear, easy or
  manageable when a level in the way is closer than 1 ATR; name its
  distance instead." Extend the claim check: "clear/open path" +
  non-empty `levels_in_way` (first level <1 ATR) → `unsupported_claim`.
- **Expected effect:** kills the remaining resistance-reasoning errors.
- **Measure:** scripted phrase-check pass rate on eval set.
- **Risk:** phrasing whack-a-mole; the claim-check half is cheap to
  verify on stored calls first.

### C5 — ask "what changed" when a previous call exists
- **Problem:** same-setup flips within an hour at ~0.3% price drift;
  TSLA entered 4× on 29 Oct, all stopped.
- **Evidence:** 11 consecutive-cycle flips; ~28 same-day reversals;
  call pairs 1497/1637 (TSLA), 1604 (COP), 1678 (PG). *(F)*
- **Root cause:** the model can't see its own previous answer, so every
  call is an independent coin-flip on a marginal input.
- **Proposed change:** add `previous_decision` to the snapshot (symbol's
  last action, option, confidence, reason words) and require: "when your
  answer differs from `previous_decision`, the thesis must name what
  changed in the input."
- **Expected effect:** hysteresis; fewer marginal flips; the "name what
  changed" text becomes a checkable field.
- **Measure:** flip rate on eval set; fraction of changed answers
  carrying a changed-fact claim.
- **Risk:** anchors the model on its own error (a bad skip persists);
  mitigate by the same field on the other side — see C6.

### C6 — `news_used` needs an effect per headline, or silence
- **Problem:** citations are decor (no stated effect, don't move
  actions, ~12% off-symbol); v3's empty-by-default swings too far the
  other way — the record loses what the model weighed.
- **Evidence:** enter rate 6% vs 4% with/without citations; re-cited
  stale headlines (62664176 ×3 HD; XOM jury verdict ×4). *(F)*
- **Root cause:** the schema lets ids be listed with no stated effect.
- **Proposed change:** `news_used`: `[{id, effect}]` where effect is ≤8
  words ("downgrade at open; smaller position thesis") — or keep ids
  but require the thesis to name what each changed. Optionally: a
  `news_relevant` boolean so "all headlines were noise" is explicit.
- **Expected effect:** auditable news reasoning; cuts reflexive citing.
- **Measure:** cited-headlines-about-symbol rate; effect-field fill
  rate; correlation of citations with action on eval set.
- **Risk:** slightly bigger outputs; still small.

## Snapshot

### C7 — tell the model whether the entry thesis still holds
- **Problem:** the never-used exit + anchored holds trace to one gap:
  the model must re-derive "does the original reason still hold" from
  raw fields, and usually just re-asserts it.
- **Evidence:** 0 exits / 233 held calls; anchored theses (1490, 1601,
  1918); PG −0.61R hold at conf 0.85 (1975). *(F for hold-bias; H for
  the fix)*
- **Root cause:** `checks` exist for setups but not for the entry's own
  claims; `entry_thesis` is free text the model can't verify against.
- **Proposed change:** compute `position.entry_thesis_status` like the
  other checks: store the entry-time check values at the entry decision
  (trend_1h, trend_daily, momentum, regime at entry) and in each held
  snapshot emit `checks_at_entry` + `thesis_holds`: false when ≥2 of
  those checks flipped adverse. The model then judges "invalidation has
  as good as happened" against facts, not memory.
- **Expected effect:** exits become possible; holds with a broken
  thesis become visible and measurable.
- **Measure:** on eval set: held calls where `thesis_holds` is false —
  action distribution; post-decision path vs hold.
- **Risk:** `thesis_holds` is a blunt code proxy; keep it advisory
  (shown fact), never a forced exit.

### C8 — position context: age, time left, distances already move
- **Problem:** the model tightens hourly (LOW ×6, HD ×3 same day) and
  never weighs session time or position age; it can't see "entered 2h
  ago" or "45 min of session left".
- **Evidence:** tighten churn above; 0 exits. *(H — mechanism plausible,
  effect unmeasured)*
- **Root cause:** fields missing.
- **Proposed change:** `position.age_hours`, `position.sessions_held`,
  `session.minutes_left` (from `time_ny`); all cheap, all explainable in
  one line each.
- **Expected effect:** exit judgement gains a clock; tighten frequency
  becomes mentionable ("trail only when ≥1 ATR of new progress").
- **Measure:** tighten-per-position rate on eval set.
- **Risk:** none significant.

### C9 — shrink the input around what is used
- **Problem:** ~80% of tokens go to levels+headlines+options+quote;
  several sections are never referenced.
- **Evidence:** section sizes §7; tokens_in v3 4,318 vs v1 1,744;
  `quote.*` provenance ~300 chars unreferenced; `signal_bar_time`
  duplicated across 3 fields; one snapshot had 55 headlines. *(F)*
- **Root cause:** provenance kept for storage leaks into the sent copy.
- **Proposed change:** send `quote` as {last, bid, ask, plausible,
  spread_unknown} only; drop `last_bar_time/close` and
  `stop_reference_price` from the sent snapshot (already v3-styled);
  cap headlines at ~12 most recent; cap levels at the nearest 8+8
  (already) *and* drop levels >4 ATR away (never reachable this cycle).
- **Expected effect:** ~15–25% input-token cut at $0.002/call-scale
  costs; less noise for the model to quote back.
- **Measure:** tokens_in before/after on the same eval snapshots;
  claim-error rate unchanged.
- **Risk:** cutting a field the model silently uses — check usage on
  eval set before each drop; keep everything in stored provenance.

## Options

### C10 — collapse same-stop options into stop + target choices
- **Problem:** option sets are dominated by one stop with 2–4 R-targets;
  the model spends its real choice on stop placement but burns letters
  and tokens on target arithmetic; the 3 "worse choice" cases were all
  higher-rr picks where only the nearer target paid.
- **Evidence:** e.g. calls 1398 (A 2R vs B–E same stop), 1499, 3216
  (D = 7.1R, be 0.12). *(F for structure; H for harm size: 3/41)*
- **Root cause:** the option list flattens two decisions (which stop,
  how much rr) into one letter grid.
- **Proposed change:** options as `{stop_id, stops: [...], targets:
  [{rr, target, breakeven_confidence, levels_in_way}]}` — pick a stop
  and a target separately, or keep letters but present them grouped so
  the "same stop" structure is visible.
- **Expected effect:** simpler choice surface; makes "nearer vs
  farther target" an explicit trade-off the prompt can direct
  ("prefer the nearer target when room is thin").
- **Measure:** chosen-option regret (chosen vs best offered touch) on
  eval set; tokens_out.
- **Risk:** schema+GUI change; moderate. Alternative: keep letters,
  add `same_stop_group` to each option (smaller change, same info).

### C11 — don't tighten the room filter on this evidence
- **Problem:** room filter removed 86 candidates in one day (every v3
  `no_options`), but room didn't predict target-first in-sample.
- **Evidence:** room buckets 16–21% target rates (§1). *(H — censored,
  one week)*
- **Root cause:** 1.0 ATR threshold was set from one bad SBUX case, not
  fitted.
- **Proposed change:** keep 1.0 (it did block the SBUX-type cases) but
  measure its blocked-set outcome on the replay/eval set before any
  raise; consider logging room continuously rather than gating harder.
- **Expected effect:** avoids a false-precision threshold.
- **Measure:** touches of room-filter-blocked options.
- **Risk:** it's already live — keep measuring, don't tighten.

## Code checks

### C12 — the trend/momentum checklist: enforce in code or drop it *(top 5)*
- **Problem:** the prompt's mechanical rules ("enter only when t1 up ∧
  regime up; td down → never; mom weak → conflict") are applied
  inconsistently by the model *and* don't predict target-first
  in-sample. Two failure modes at once.
- **Evidence:** enters with mom weak (1604, hit target) and t1 mixed;
  per-feature rates §1 (up-trends 14–15% vs mixed 22–24%; weak mom 33%
  vs strong 12%). *(F for inconsistency; H for the anti-correlation)*
- **Root cause:** judgement rules written as deterministic checks but
  left to the model.
- **Proposed change:** either (a) code-enforce "enter only when t1 up ∧
  regime_trend up ∧ td≠down" as a `no_call`/`edge` gate and let the
  model judge only inside that box — removes a whole error class and
  saves calls; or (b) delete the checklist from the prompt and let the
  model weigh the indicators as input, since the mechanical version
  doesn't predict anyway. Decide with the eval set (a vs b on the same
  snapshots) — do not pick by gut.
- **Expected effect:** (a) guaranteed consistency, ~fewer model calls;
  (b) removes a false rule. Either kills the contradiction.
- **Measure:** replay both variants on the eval set; decided target
  rate + enter rate + cost.
- **Risk:** (a) blocks winners the model would have caught in
  off-template tape; (b) more discretion where the model is weakest.

### C13 — flip-flop guard
- **Problem:** §4 — action changes on unchanged inputs.
- **Evidence:** 11 hourly flips, ~28 same-day reversals, TSLA 4 enters
  all stopped. *(F)*
- **Root cause:** no memory + no hysteresis (see C5 for the input half).
- **Proposed change:** code-side soft guard: if the same symbol flips
  skip↔enter within the session with price moved <0.5 ATR and checks
  unchanged, require `what_changed` non-empty in the answer or revert
  to the previous action with reason `flip_flop`; shown as changed by
  code. Pairs with C5's `previous_decision` field.
- **Expected effect:** kills thrash entries (TSLA×4 pattern).
- **Measure:** flips on eval set with/without the guard.
- **Risk:** a genuinely improved setup gets held to the old answer for a
  cycle — bounded, one-cycle cost.

### C14 — confidence-sanity warning
- **Problem:** no tripwire exists for "0.7 where no-edge is 0.14" or
  "0.85 at np 0.15"; these pass silently today.
- **Evidence:** calls 1669, 1687, 2464, 1975, 1733. *(F)*
- **Root cause:** the edge gate only checks entries ≥ threshold, not
  plausibility; held conf is ungated (correctly) but unmeasured.
- **Proposed change:** warning reason `confidence_outlier` when conf −
  breakeven (or − neutral_probability) > 0.30; stored like
  `unsupported_claim`, never gates.
- **Expected effect:** calibration problems become countable per call
  instead of discovered in analyses.
- **Measure:** flag rate on eval set (~5–15% expected initially).
- **Risk:** none.

### C15 — store the numbers the calibration needs
- **Problem:** PR 33 fits `confidence_weight` later; today the record
  lacks the skip-side probability and the option's breakeven together.
- **Evidence:** §2; spec already schedules fitting once ≥50 entries.
- **Proposed change:** on every stored decision record `chosen_be`
  (breakeven/neutral of chosen or best option) next to confidence —
  cheap plumbing, no prompt change; combined with C1 makes every call
  calibration-usable.
- **Expected effect:** the eval/fitting data exists when needed.
- **Measure:** columns populated; join coverage on eval set.
- **Risk:** none.

## Model choice

### C16 — keep gpt-4.1-mini; don't ship 4o-mini for selection
- **Problem:** entry selection consumes confidence; gpt-4o-mini's enter
  confidence is a constant 0.65 — zero information — and it entered
  less than half as often on identical inputs.
- **Evidence:** §10 pairs. *(F for spread+rate; H for which is better
  on profit — decided n's are tiny: 13 vs 4)*
- **Proposed change:** live model stays gpt-4.1-mini; when the Anthropic
  key is set, run a claude-haiku shadow for comparison — there is no
  Claude data at all yet.
- **Measure:** same-snapshot agreement and per-model decided rates.
- **Risk:** none — status quo for live; shadow for Claude.

---

## Keep — what works and must not be lost in v4

1. **Code-built options and the model's option-picking.** Chosen option
   worse than an alternative only 3/41. The A–F choice surface works.
2. **`checks` by code (trend, regime, momentum, room, earnings).**
   Killed the loose daily-trend claims (7 → 0 in-sample).
3. **Break-even machinery:** `locked_r`, `locks_profit` semantics, the
   break-even guard, the locks-in claim check — the 28–29 Oct class is
   closed.
4. **`conflicts` first** — v3 answers carry real conflicts; keep the
   order and the field.
5. **Explanation + claim checks** — near-clean (1/826), and they catch
   real drift without touching money.
6. **Invalid → hold/skip conversion** — worked on all 4 invalid ids.
7. **`data_flags`** — unused so far but the right mechanism; SHEL's
   "target not set" conflict shows the model flags real data problems.
8. **`scheduled_exit` + invalidation written by code** — used correctly
   in-sample; keep out of conflicts as specified.
9. **Sparse `news_used`** under v3 — correct; the fix needed is richer
   content per citation, not more citations (C6).
10. **Held confidence kept out of entry calibration** — right decision;
    extend it to the new skip-side probability (C1) until it is fitted.

## Eval set

≥60 stored calls covering every failure type plus good/normal cases.
All ids are `llm_calls.id`; decisions referenced live under them. Each
call lists the properties a good v4 answer must have — not one exact
answer.

**False "locks in profit" (8):** 1448, 1602, 1630, 1668, 1727, 1618,
1649, 1728.
Required: no locks-in claim while `locked_r` ≤ 0; if tighten, chosen
option's `locks_profit` must be true for any lock-in wording.

**Wrong daily-trend claim (7):** 969, 984, 1036, 1068, 1073, 1108, 1125.
Required: no claim that the daily trend is up when `trend_daily` ≠ up.

**"Clear path" into levels (7):** 1398, 1499, 1555, 1558, 1608, 1611,
1732.
Required: no clear/open-path wording while a level in the way sits
<1 ATR; distance must be named instead.

**Momentum contradiction (4):** 1490, 1630/1631 (HD/RTX family), 1620 —
plus 1975 (0.85 at np 0.15, −0.61R) for held-confidence anchoring.
Required: no "supporting"/"strong momentum" claim with `momentum` weak;
held conf ≤ np + 0.3 unless a named new fact is given.

**Conflicts listed then ignored / empty on adverse checks (6):** 1604
(COP enter with mom weak listed), 1734, 1868 (IBM, legal-but-unresolved),
1733, 1918, 2991-equivalent holds.
Required: every listed conflict resolved or the action is skip/hold;
adverse `checks` produce at least one conflict entry.

**Flip-flops (8 calls, 4 pairs + pattern cases):** TSLA 1497, 1637,
1673, 2469; COP 1604 vs its 11:46 skip (~1555s); PG 1678 after 4 skips;
UNP 1639/1678-pair; LOW 1519/1694.
Required: a changed answer names the changed input fact; identical
checks+price band prefers the previous action.

**Invalid/edge outputs (4):** 1689 (option E on held), plus the 3 shadow
unoffered-T-id calls (ids in stored `validation_result`).
Required: never emit an unoffered option id; held calls never emit A–F.

**Over-confidence vs break-even/neutral (6):** 1669, 1687, 2464, 1975,
1733, 1973.
Required: conf − anchor ≤ 0.30 or the thesis names the fact that lifts
it; held conf anchored at `neutral_probability`.

**News discipline (6):** 1402, 1509, 1616 (no-news phrasing with
headlines present); re-citation cases: 1398-era HD calls citing
62664176, XOM run (19–21 Oct ids 110/122/137/152/163); SHEL 1974 as the
positive "flags data problem" case.
Required: no "no news" claim with headlines present; citations carry
an effect; stale headline re-citation doesn't repeat.

**Held-side normal cases (8):** 1732, 1866, 1917, 1973 (HD trail:
hold/tighten/blocked-tighten/executed), 1974 (SHEL), 1976, 2992-proxy
(held PG −0.61R), 2555/1727-family tighten→hold paths.
Required: tighten only offered ids; trail proposals don't churn below
the min move; scheduled_exit is never a conflict.

**Normal good enters (8):** 1869 (PG v3, filled), 1920 (SHEL v3,
filled), 1398 (SBUX target), 1449 (ACN target +88), 1555/1611 (MCD
targets), 1552/1604 (code-blocked enters — good model, good gate).
Required: enter only with a valid offered id, resolved conflicts, and
conf anchored at breakeven + real edge.

**Blocked-by-code keeps (4):** 1497, 1550 (TSLA spread blocks), 1499,
1552 (HD spread), 1734/1868 (IBM fresh_quote).
Required: model answer stays sound; block reason recorded.

That's ~65 calls. **Scoring script outline:**

```
for each stored snapshot in eval_ids:
    rebuild v3 input (as today) and the v4 input (new fields)
    call model with decision.v3 and decision.v4 (same model, temp 0)
    for each answer: run production validation + property predicates:
      per category, the required properties above are boolean checks
      over (model_output, snapshot, options) — e.g. no /locks? in/ when
      locked_r<=0; conf-anchor<=0.3; conflicts non-empty on adverse checks
    report: pass rate per category per prompt version; regression list
      (calls v3 passed and v4 failed); confidence calibration vs the
      option's stored/computed touch; cost+latency deltas.
```

## Open questions for Marc

1. **OQ1 — enforce the trend/momentum checklist in code or drop it?**
   It's applied inconsistently *and* doesn't predict in-sample. Option
   (a) hard gate; (b) demote to soft guidance. Recommend deciding on the
   eval set, not by preference. This is the biggest v4 fork.
2. **OQ2 — does the model need cycle context?** Free slots, other
   candidates, sector exposure are invisible to it (cost: 6+1+7 blocked
   enters). With PR 33's selection, telling it "you compete for N slots"
   may either sharpen confidence honesty or just waste tokens. I lean
   to *not* adding it — selection is code's job — but it's your call.
3. **OQ3 — is 0 exits ever a problem to fix or a feature?** Positions
   have broker stops; maybe the model never needs `exit` and C7 buys a
   capability never used. Alternative: make exit *only* available when
   `thesis_holds` is false.
4. **OQ4 — same-stop option collapse (C10):** worth a schema/GUI change
   for a 3/41 measured harm, or keep letters + add `same_stop_group`?
5. **OQ5 — `confidence_weight` for PR 33:** with stated conf ≈ realized
   decided rate (n=18, hypothesis) but inflated EV vs base rate, is 0.5
   the right starting weight, or start lower until the eval set fits it?
6. **OQ6 — news feed quality:** ~35–40% of headlines aren't about the
   symbol. Pre-filter by title match in code (lossy), keep as-is, or
   mark each headline `about_symbol` computed by code so the model can
   see the flag?
7. **OQ7 — store shadow input snapshots?** Needed for any honest
   shadow-vs-live reasoning audit; costs DB size on every shadow call.

## Code bugs found during the analysis (not v4 items; not fixed here)

- **B1 — claim check flags correct "levels in the way" conflicts.**
  `backend/app/decide/output.py:1076-1079`: a sentence containing level
  ids but no "stop"/"target" word falls into `bad.extend(ids - allowed)`
  with `allowed` empty. IBM call 1734: conflict "levels L2 and L1 in the
  way" flagged `L1; L2` as unsupported claims — false positive.
- **B2 — `shadow_decisions.input_snapshot` is almost never stored**
  (11/4,735 rows). Shadow inputs can't be audited or exactly replayed;
  spec says shadows decide on identical inputs. See shadows/runner.
- **B3 — `entry_thesis` id-rewrite garbles text.**
  `backend/app/decide/v3.py:792` `rewrite_thesis_ids` replaces an id
  after words the thesis already carries: HD's stored thesis reads
  "the 1h swing low the 23 Oct 10:30 1h swing low (317.61)".
- **B4 — SHEL duplicate position rows** (positions 9 and 10: same
  qty/price opened 0.7s apart on 30 Oct 18:46; 9 closed `broker_close`,
  10 still open with `target NULL`). Its held call (1974) then reported
  "target not set" as a conflict.
- **B5 — all 224 verdict calls are verdict.v1; `thesis_check` is NULL on
  every verdict row** although `reviews.prompts` defaults to verdict.v2
  post-PR-32. Either the task still runs v1 or no v2-eligible verdict has
  been produced — worth checking which.
- **B6 — `outcomes.option_touches` NULL on all rows** in the copy,
  including decisions mature for backfill — verify the worker/backfill
  actually ran (legitimate if every 5-session window is still open).
- **B7 — informational:** v1 WMT proposed its own current stop as a
  tighten 3 cycles running (calls 508, 575, 646) — code handled it; a
  sign the held side needed offered-options even then.
