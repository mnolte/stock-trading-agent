# 091 · fix · Number check: numbers inside fact strings

Kickoff for Devin. Start the session with: "Build docs/kickoffs/091-fix-number-check-fact-strings.md."

Spec: `docs/SPEC.md` → Reviews → **Number check** ("Every number in the
model's text must match a fact it was given (after rounding)"). Code:
`backend/app/reviews/numbercheck.py`, `backend/app/reviews/service.py`
(`_screen_content`), `backend/app/reviews/facts.py` (`verdict_facts`).
Prior art: the 2026-11-04 identifier fix (`bc81993`, CHANGES.md) — same
class of false positive.

Move this kickoff to `docs/kickoffs/archive/` with `git mv` in the build
PR.

## The bug

On 12 Nov, seven draft verdicts fell back to the plain template with
"number check failed" (detail `1.5`/`1.50`): verdicts 590–593, 601, 603,
605, all `shock_brake` blocks whose `change_text` is "SPY fell past
1.50% last hour". The verdict model quotes the threshold as "1.5%" — a
number it was given — but `bad_numbers` only accepts numeric JSON leaves
of `facts`; a number inside a string is not one, so the literal fails.
The two shock-brake verdicts that passed (597, 600) did so only because
a numeric fact coincidentally matched (room_atr 1.47, pct −1.5).

The same trap waits in every code-written check detail that reaches
`change_text`: spread ("0.234% > 0.15%"), max_open_positions ("4 ≥ 4"),
sector_cap, daily_loss_limit, earnings_blackout ("2 ≤ 3") and the
decision code checks ("Reward to risk ≥ 1.5 (116 − 113.02) / 3.76").

## The rule

A number written inside a **code-produced** fact string counts as a fact
the model was given. For verdicts that is `change_text` (built by code
from the first failed gate or check). The model's own earlier text
(`thesis`) stays excluded: its numbers belong to the decision snapshot's
fact set, not the verdict's, and were checked at decision time.
Dates and other string fields stay excluded too — scanning them would
admit junk literals ("18" from a timestamp).

## Build

1. In `ReviewService._screen_content`, when `facts["change_text"]` is a
   non-empty string, run `bad_numbers` against `facts` plus the numbers
   `extract_numbers` finds in that string (as a numeric list fact).
   Applies to every kind; today only verdicts carry `change_text`.
2. Tests, pass and fail: a blocked model-enter whose failed check detail
   says "SPY fell past 1.50% last hour" and whose verdict reason quotes
   "1.5%" drafts from the model (`draft_source == "model"`); the same
   setup with an invented figure ("9.9%") still falls back with
   "number check failed".
3. `docs/CHANGES.md` entry.

## Not in scope

- `thesis` and other model-written fact strings (see The rule).
- Weekly and briefing facts: they carry no code-written prose numbers
  today; if one appears later the same `change_text` mechanism does not
  apply — raise it then.
- Rewriting the seven template verdicts already stored: drafts are
  rewritten only when Marc re-asks or the row is regenerated; leave them.
