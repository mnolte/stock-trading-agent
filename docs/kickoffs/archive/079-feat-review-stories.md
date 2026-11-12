# 079 · feat · Review stories

Kickoff for Devin. Start the session with: "Build docs/kickoffs/079-feat-review-stories.md."

Backend (prompt contracts, facts, validation, template) and frontend (story, The numbers, terms). The prompts are written: `backend/prompts/briefing.v3.md` and `weekly.v3.md`, with their manifest hashes, came with the spec PR; don't edit them. Spec: `docs/SPEC.md` → Reviews → **Stories** and the Reviews table; `docs/CHANGES.md` entry [079]. Design: `design/screens/Main.dc.html` (Daily briefing card) and `Reviews.dc.html` (Weekly), `design/CHANGES.md` entry [079]. Issue: #236.

```text
[079] Review stories. Goal: the daily briefing and the weekly review read
as a short story of one to three paragraphs that explains what its numbers
mean, with every number still checked and every fact still on screen.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md → Reviews (all; Stories is new); docs/CHANGES.md [079].
3. backend/prompts/briefing.v3.md and weekly.v3.md (read only).
4. design/CHANGES.md [079]; Main.dc.html (Daily briefing card) and
   Reviews.dc.html with their screenshots.

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR.
Put "Closes #236" in the PR description.

1. Contracts. Register briefing.v3 and weekly.v3 in
   ReviewService._REVIEW_CONTRACTS (and wherever Settings lists known
   versions). Output: briefing.v3 {"text"}; weekly.v3 {"story",
   "suggestion"} (suggestion as v2). Pydantic models per version.

2. Facts.
   - `glossary`: app/reviews/glossary.py, one dict term → one plain
     sentence (stop, target, R, regime, VIX, VIX percentile, drawdown,
     adjusted P&L, win rate, expectancy, sample, release, earnings
     blackout). Use the wording of the design's tooltips. The facts carry
     only the terms whose facts are present. A definition that needs a
     threshold reads it from settings and the threshold goes into the
     facts too (for example risk.drawdown_kill_pct for drawdown).
   - limits: briefing_words from reviews.max_words.briefing; add
     weekly_story_words from reviews.max_words.weekly_story.
   - Config (backend/config.example.yaml, reviews.max_words):
     briefing: 220 (was 80) and weekly_story: 350; keep weekly_summary
     (120) for v1 and v2. Add the new key to the README's config table.
     v1 and v2 briefings keep validating at 80 words (a fixed per-version
     limit in code is fine): a v2 answer of 150 words must still fail.

3. Checks. Number check unchanged (it covers the story). New format check
   for v3: 1–3 paragraphs split on blank lines; no line starting with
   "-", "*", "•" or a number and "."; no "#"; within the word cap. A
   failure shows the template with "Plain review: format check failed".

4. Prose template for v3: one fixed sentence per fact group, written by
   code from the same facts, in the same paragraph order as the prompt
   (account, market, what to watch / week, drivers, sample). Unit tests
   per group, empty lists included.

5. GUI.
   - Dashboard briefing card and Reviews: the status line (briefing),
     the paragraphs with 12 px between them, a "The numbers" fold listing
     the facts as label–value rows (labels from the label map), then the
     footer (number check, next briefing, model, prompt, cost).
   - Terms: every glossary term in the story is a button with dotted
     underline; its definition from the stored facts' glossary shows on
     hover and on tap (a popover on phones). Match whole words,
     case-insensitive, first occurrence per paragraph only.
   - Weekly: the story replaces the summary and the Observations card;
     the tiles move under "The numbers".
   - Older reviews (v1, v2) show as before.
   - Email: the status line and the paragraphs, no markup.

6. Defaults. reviews.prompts → {weekly: weekly.v3, briefing: briefing.v3,
   verdict: verdict.v2} in the example config and the code default.

7. Eval. Add the v3 prompts to the review eval: on the fixture facts, the
   answer passes the number check and the format check, uses at least one
   glossary term with its meaning, and stays within the cap. Record the
   pass rate on the default models in the PR.

8. Screenshots in docs/screenshots/079/: the dashboard card (1440, 390),
   Reviews weekly (1440), a term's popover on a phone, and the template
   after a failed format check.

Rules: no network in tests (fake model); never edit a used prompt; the
model never sees a number code didn't compute; nothing in design/.

Done when: both prompts pass the eval on the default models at 90% or
more; a list-shaped fake answer falls back to the prose template; v2
reviews still validate at their own limits; make test and the e2e pass.
```

## Why

Marc, 11 November: "Make the daily briefing and weekly review a longer story. Prevent it from just summing up numbers because I want it to be a readable story of one to three paragraphs. Possibly explain what the numbers, that are being mentioned, mean for novice users."

- briefing.v2 asks for "short and scannable" in 80 words and lists every fact in a fixed order, so the result is a row of numbers.
- weekly.v2's summary covers return, SPY, trades, R and drawdown in 120 words, again mostly numbers.

## Assumptions

- Explanations add words, not numbers, so the existing number check needs no change.
- At about 1,500 tokens in and up to 450 out, a briefing costs under $0.002 on gpt-4.1-mini; the $2 review cap is unaffected.

## Open questions for Marc

None. Decided without asking: 220 words for the briefing and 350 for the weekly story (one to three paragraphs of normal length); "The numbers" fold keeps every fact one click away.
