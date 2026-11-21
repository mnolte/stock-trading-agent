---
item: 112
type: fix
title: Room for the cost-per-session chart
status: ready
issue: 331
kickoff: 112-fix-cost-chart-room.md
spec_prs: [332]
build_prs: []
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
