# 107 · fix · Dashboard cards line up

Kickoff for Devin. Start the session with: "Build docs/kickoffs/107-fix-dashboard-alignment.md."

Frontend only, layout only: no text, data or behaviour changes. Design: `design/screens/Main.dc.html` with `Main.png`, `Main-1024.png`, `Main-390.png`; `design/changes/107-fix-dashboard-alignment.md`; `design/DESIGN.md` → Rules → "On this page". Issue: #315.

```text
[107] Dashboard cards line up. Goal: every row of dashboard cards starts
and ends at the page column's edges with 20 px between cards, and the
three two-card rows share one split.
Start from the latest main. One PR. Put "Closes #315" in it.

Read first:
1. AGENTS.md (Commands: CI runs the tests).
2. design/changes/107-fix-dashboard-alignment.md and design/DESIGN.md →
   Rules → On this page.
3. design/screens/Main.dc.html: the three elements with class
   "dash-pair" and the second <style> block in its head.
4. frontend/src/pages/Dashboard.tsx (the <span id=… height: 0> jump
   targets near lines 1505–1711) and frontend/src/pages/Insights.tsx
   (line 529).

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR, and
set status: merged in docs/changes/107-fix-dashboard-alignment.md.

1. Jump targets. Remove every empty jump-target element
   (<span id=… style={{display:'block', height: 0, …}}/>) in
   Dashboard.tsx and Insights.tsx, and in any other page that has one
   (search for "height: 0" next to an id). Put the id and
   scrollMarginTop: 16 on the card or heading block that followed it.
   The "On this page" chips keep their hrefs.

2. Two-card rows. Open positions | Risk budget, Price chart | Latest
   decision, Today's decisions | Daily briefing: from a 1200 px viewport
   one grid, grid-template-columns: minmax(0, 3fr) minmax(0, 2fr),
   gap 20 px, cards at their own height (align-items: start); below
   1200 px each card takes the full width. One shared class or
   component for the three rows.

3. Test (Playwright, frontend/e2e/107-alignment.e2e.ts, simulated data):
   at 1440, 1024 and 390 px, for every row of top-level cards on the
   dashboard: the first card's left edge and the last card's right edge
   equal the page column's content edges (±1 px), and the gaps between
   cards are 20 px (±1). At 1440 px the three two-card rows have the
   same split (left card right edge equal ±1). Fail case: a fixture
   with one empty span put back fails the check. Insights at 1440:
   the gap above each question heading equals the gap between the
   other blocks of that column.

4. Screenshots in docs/screenshots/107/: the dashboard at 1440, 1024
   and 390.

Rules: no network in tests; nothing written into design/; no other
layout changes. Tests are proven in CI: open the PR as a draft early,
run locally only the new test file.

Done when: all CI checks are green and the PR merges cleanly; the
dashboard matches Main.png, Main-1024.png and Main-390.png in where the
cards sit; "Issues closed" lists the five points of #315 with the file
or test for each.
```

## Why

Marc, 19 November: "The dashboard elements are not properly aligned in the design. Some rows are wider than the others." Measured on the design at 1440 px: the first card of three rows started at 276 px instead of 256 px, two rows had 40 px between their cards instead of 20, and the left cards of the two-card rows were 610, 760 and 550 px wide. The empty jump targets added by item 073 were flex items, so each took a gap. The app copied them.

## Assumptions

- 3 : 2 suits all three rows: the chart gets 684 px (it was 760), the latest decision 456 px (340), Open positions 684 px (610). A card keeps its own height; making a row's cards equal height would leave 160 px empty under the chart.
- 1200 px is where the right-hand cards still get at least 360 px.

## Open questions for Marc

None. Decided without asking: the four summary tiles keep their own 4-column grid, so their edges don't line up with the 3 : 2 split.
