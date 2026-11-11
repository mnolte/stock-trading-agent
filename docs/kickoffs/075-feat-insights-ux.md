# 075 · feat · Insights UX

Kickoff for Devin. Start the session with: "Build docs/kickoffs/075-feat-insights-ux.md."

Backend (answer sentences and sample states in the snapshot) and frontend. No metric definition, threshold or snapshot timing changes. Spec: `docs/SPEC.md` → Insights → **GUI** (reworked in item 075); `docs/CHANGES.md` entry [075]. Design: `design/screens/Insights.dc.html`, `design/CHANGES.md` entry [075]. Issue: #232.

```text
[075] Insights UX. Goal: Insights reads as five questions with a plain
answer each, tiles say what they mean, the slice picker is one grouped
select, and a small sample says what it needs instead of showing dashes.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md → Insights (all of it; the GUI part is new);
   docs/CHANGES.md [075].
3. design/CHANGES.md [075]; design/screens/Insights.dc.html with
   Insights.png and Insights-390.png.

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR.
Put "Closes #232" in the PR description.

1. Answers by code. app/insights/answers.py: one function per question
   that returns one or two sentences from a snapshot, from fixed templates
   (no model). Store them in the snapshot (`answers`), computed with it.
   - 1 Is there an edge?: from expectancy and its range ("Probably, but
     not proven yet" when the range's low end is above 0 but under 0.1R;
     "Not shown yet" when it includes 0; "Yes" when the low end is 0.1R or
     more), plus beta in words.
   - 2 What stands out?: counts of findings and early signals and their
     titles.
   - 3 Where are trades lost?: the share stopped by the rules, the two
     biggest losses after the model, missed vs avoided skips.
   - 4 Which conditions work?: for the default dimension, the best slices
     with their sample status.
   - 5 Confidence, fills and nights: calibration gap, gaps through the
     stop, overnight vs in session.
   Unit tests with fixture snapshots for each branch, including under 10
   trades.

2. Sample states. Every number in the snapshot that needs more trades
   carries `needs` (trades needed) and `have`, so the GUI shows "needs 10
   closed trades · 4 so far". A card whose numbers all need more trades
   shows that line and its definition.

3. Page. Top to bottom as the design: filters, "How to read this page"
   fold, then the five questions, each an h2 with its number and answer
   sentence, then its cards. Four headline tiles (Expectancy, Edge over
   SPY, Beta, Model cost per trade), each with its plain line from the
   label map. Overnight vs in session and Gaps through the stop are no
   longer tiles.

4. Slices. One <select> with <optgroup>s Timing, Market, Stock, Model
   (the spec's lists), in the URL as ?dim=; the 21 chips go. The 15:46
   row: "Entries from 11 Nov, when the last-cycle rule was removed · n
   trades so far, shown from 10" until it reaches 10 trades. The filter
   row "Rules · last cycle (15:46)" keeps its history and says "until
   10 Nov".

4b. Reading order inside cards.
   - "On this page" chips under the filters jump to the five questions
     (anchors, at every width).
   - Funnel: each step shows "n% kept" (of the step before) before what
     was lost; below 760 px the name and count share a line and the bar
     and note sit under them.
   - Columns: Slices = slice, Avg R, range, trades, win rate, total R,
     skips, sample. What each filter removed = filter, R if taken,
     removed, target first, stop first, neither, status ("needs 100" in
     the R column when too few touches are decided). Sectors now = rank,
     sector, vs SPY, ETF.
   - Buttons in Findings wrap their label on a phone.

5. Text. No item numbers or code names in UI text ("(item 068)",
   rs_vs_sector_pct); snapshot dates through the 056 formatter.

6. Screenshots in docs/screenshots/075/: 1440 and 390, a strategy with
   under 10 trades, and the select open.

Rules: no network in tests; Decimal or exact fractions where the snapshot
already uses them; no metric changes; nothing in design/.

Done when: the page matches Insights.dc.html at 1440 and 390 with no
sideways scroll; no "—" where a sample is too small; no item number in the
rendered text (a Playwright check for /item \d{3}/); make test and the e2e
pass.
```

## Why

Review of the running app on 11 November:

- Slices offered both a select and 21 chips for the same choice.
- Headline tiles showed dashes, or repeated the Overnight and Execution cards.
- Skipped setups was mostly dashes; "(item 068)" and "snapshot 2026-10-14" leaked into the text.
- Nothing explained R, expectancy or the 90% range to someone new.

## Assumptions

- Answer sentences are templates over the snapshot; they don't need a model and never change between page loads.
- The "Probably / Not shown yet / Yes" thresholds are wording only, not trading rules; they can change in a later spec.

## Open questions for Marc

None. Decided without asking: the five-question order; Model cost per trade stays a headline tile because it answers "does the model pay for itself".
