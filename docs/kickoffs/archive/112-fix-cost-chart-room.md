# 112 · fix · Room for the cost-per-session chart

Kickoff for Devin. Start the session with: "Build docs/kickoffs/112-fix-cost-chart-room.md."

The Cost per session chart in Insights → What do the models cost? (item 109) runs out of its card. This item gives it its own full-width card, caps it at 31 bars and makes the amounts readable on a phone. Spec: `docs/SPEC.md` → Insights → Model costs → **Cost per session** (changed by 112). Design: `design/screens/Insights.dc.html` (section 6), `design/screenshots/Insights.png` and `Insights-390.png`, `design/changes/112-fix-cost-chart-room.md`. Item file: `docs/changes/112-fix-cost-chart-room.md`. Issue: #331.

```text
[112] fix: Room for the cost-per-session chart. Goal: the chart has its
own full-width card, never more than 31 bars, never leaves its card from
390 to 1920 px, and shows a bar's amounts on click or tap.
Start from the latest main. One PR: "[112] fix: Room for the
cost-per-session chart", "Closes #331". Move this kickoff to
docs/kickoffs/archive/ with git mv in that PR. Set status: merged in
docs/changes/112-fix-cost-chart-room.md with a dated "build PR #n"
section.

Read first:
1. AGENTS.md (Commands: CI runs the tests) and docs/README.md.
2. docs/SPEC.md → Insights → Model costs → Cost per session; issue #331;
   design/changes/112-fix-cost-chart-room.md; design/DESIGN.md → Spacing
   (109).
3. backend/app/insights/model_costs.py (session_days, weekly_buckets,
   _iso_week, per_session); its tests;
   frontend/src/pages/modelCosts.tsx (SessionChart, ModelCostsSection,
   monthName); frontend/e2e/109-spacing.e2e.ts.

Backend (model_costs.py):
1. Buckets: unit = "session" when the period has ≤ 31 New York
   sessions, "week" (ISO week, Monday start) when it has ≤ 31 weeks,
   otherwise "month" (New York calendar month). If months would pass 31,
   use "quarter". One rule in one function, bucket_unit(sessions).
2. Every bucket in the period gets a row, $0 included (today sessions
   without calls have no bar). Each row: key, unit label ("14", "W43",
   "Nov", "Q4"), first and last date, sessions (count), live, shadows,
   reviews, ask (Decimal strings).
3. The response gets per_session_unit and per_session_summary: average
   per session (total ÷ sessions in the period, live + shadows + reviews
   + ask), the highest bucket's key and total, and its largest part.
   Money stays Decimal.

Frontend (modelCosts.tsx):
4. Cost per session is its own Card under By model, full width at every
   width. Remove the shared flex row.
5. Subtitle from the period and the unit: "This month · per session ·
   backtests not included", "All time · per month · …". Drop monthName
   for it.
6. Answer sentence above the chart, as in the spec: "Average $0.51 a
   session; the highest was $0.75 on Mon 14 Dec, with Ask the largest
   part." For weeks "the highest week was W43 (19–23 Oct)", for months
   "the highest month was October".
7. Chart as in the design: 160 px plot, axis at a round number above
   the highest bar with gridlines at the top and the half, a dotted
   average line, bars in equal slots each at most 36 px wide, 6 px gap
   (2 px below 600 px), never wider than the card (no minWidth that can
   overflow; no sideways scroll). Labels: show a label only when it is
   at least 36 px from the last shown one; always the first and the
   last. Measure the slot width with a ResizeObserver.
8. Bars are buttons (aria-pressed, aria-label with date and total). A
   click, tap, Enter or Space selects a bar; the arrow keys move the
   selection; the highest bar is selected on load. The line under the
   chart shows the selected bucket's dates, total and four amounts,
   with aria-live="polite". Keep the title attribute for mouse users.
9. Legend adds "average"; the note under the chart explains the bucket
   rule, as in the design.

Tests (no network, each its own database, FakeClock):
- bucket_unit at 31 and 32 sessions, 31 and 32 weeks, and past 31
  months; every bucket present with $0 rows; first and last dates;
  sessions per bucket; the average per session; the highest bucket and
  its largest part.
- Frontend unit test: the label thinning (first and last always shown,
  none closer than 36 px) at 390, 1024 and 1440 px slot widths; the
  sentence for each unit.
- e2e: Insights section 6 at 390, 1024, 1440 and 1920 px, This month and
  All time: the chart's right edge is inside its card; clicking a bar
  changes the line under it; the arrow keys move the selection. The
  109 spacing check still passes.
Screenshots in docs/screenshots/112/: section 6 at 1440 and 390 px for
This month and All time, each next to design/screenshots/Insights.png
and Insights-390.png in the PR.

Rules: Decimal for money; no datetime.now() (the clock); the 31-bar cap
and 36 px limits are constants in one place each (backend bucket_unit,
frontend chart constants); nothing written into design/; keep 107's card
alignment and 109's spacing rules. Tests are proven in CI: open the PR
as a draft early, run locally only the tests for the changed files, and
ruff, mypy and the frontend type check on them; never the full suites,
e2e or image builds.

Done when: all CI checks are green and the PR merges cleanly; the PR
lists the six points of #331 under "Issues closed" with the file or
test for each.
```

## Why

Marc, 21 November: "The cost per session section in insights has a graph that is running out of its container. Can we give that section more room?" Cause and fix are in `docs/changes/112-fix-cost-chart-room.md`.

## Assumptions

- Quarters as a fourth bucket only matter after about 2.5 years of data; the rule is in now so the cap always holds.
- Selecting the highest bar on load is more useful than an empty line under the chart.

## Open questions

None.
