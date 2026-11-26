---
item: 112
type: fix
title: Room for the cost-per-session chart
status: merged
issue: 331
kickoff: 112-fix-cost-chart-room.md
spec_prs: [332]
build_prs: [333]
---

# 112 · fix · Room for the cost-per-session chart

## 2026-11-21 · spec PR

Marc, 21 Nov: the Cost per session chart in Insights → What do the models cost? runs out of its card.

Why it happens (item 109's design and build):
- **A narrow card.** It shared a row with By model (`flex: 1 1 340px` against `999 1 640px`), so it was about 400 px wide at 1440 px.
- **Too many bars.** Each bar is at least 14 px plus a 6 px gap. More than about 17 bars overflow: This month after 17 sessions, and All time, where 260 sessions become 52 weekly bars, about 1,040 px.
- **Labels and subtitle.** Week labels don't fit under a 14 px bar. The subtitle always shows the month of the period's end, also for 3 months and All time.
- **Hover only.** A bar's amounts show only on hover, which a phone doesn't have.

Changes:
- **`docs/SPEC.md` → Insights → Model costs → Cost per session.** Its own full-width card. At most 31 bars: per session up to 31 sessions, per week up to 31 weeks, per month beyond. Every bucket gets a bar, $0 included. Bars at most 36 px wide, never scroll or overflow, labels thinned to one per 36 px. Gridlines and an average line. A subtitle with period and unit. An answer sentence. Click or tap a bar for its amounts, keyboard steps through the bars.
- **Design** (`design/changes/112-fix-cost-chart-room.md`): `Insights.dc.html` and its two screenshots.

Assumption: months are the right bucket above 31 weeks. All time stays readable for 2.5 years, at 31 months, before even months would overflow the 31-bar cap. If it ever does, the kickoff groups by quarter.

## 2026-11-21 · build PR #333

- **`backend/app/insights/model_costs.py`:** `bucket_unit(sessions)` — session ≤ 31 sessions, ISO week ≤ 31 weeks, month ≤ 31 months, quarter beyond (`MAX_SESSION_BUCKETS = 31`). Every bucket in the span gets a row, $0 included, with key, label, first/last session dates (calendar bounds when a bucket has no sessions), session count and the four Decimal amounts; a call's `session_date` always lands in a bucket, so no cost is dropped. The response adds `per_session_unit` and `per_session_summary` (average per session = total ÷ sessions; highest bucket's key, total, largest part; `highest` is null when the period spent nothing).
- **`frontend/src/pages/modelCosts.tsx` + `modelCostsChart.tsx` + `modelCostsFormat.ts`:** Cost per session is its own full-width card under By model; the subtitle reads "{period} · per {unit} · backtests not included"; the fixed-template sentence sits above the chart. The chart has a 160 px plot, a round-number axis, gridlines at top and half, a dotted average line, equal-slot bars ≤ 36 px (6 px gaps, 2 px below 600 px), labels thinned to ≥ 36 px via ResizeObserver (first and last always shown), and no min-width overflow. Bars are buttons with `aria-pressed` and roving `tabIndex`: click/tap/Enter/Space selects, arrows/Home/End move, the highest bar is selected on load, and an `aria-live="polite"` line shows the selected bucket's dates, total and four amounts. Legend adds "average"; the note explains the bucket rule.
- **Tests:** `backend/tests/test_model_costs_112.py` (bucket_unit at 31/32 sessions, 31/32 weeks, >31 months; $0 rows; dates; sessions; average; highest/largest part), `frontend/src/pages/modelCostsChart.test.ts` (label thinning at 390/1024/1440 px; the sentence for each unit), `frontend/e2e/112-cost-chart.e2e.ts` (fit at 390/1024/1440/1920 px for This month and All time; click and arrow keys). `test_model_costs_109.py` now expects 3 `per_session` rows — the fixture's Ask calls sit on a session with no SPY bar.
- **Deviations:** bucket dates are first/last *session* dates (the spec's "W43 (19–23 Oct)"); month/quarter sentences carry the year ("October 2026", "Q4 2026") since All time spans years; the note mentions the quarter rule the design's shorter wording predates.
