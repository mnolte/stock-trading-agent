---
item: 107
type: fix
title: Dashboard cards line up
status: merged
issue: 315
kickoff: 107-fix-dashboard-alignment.md
spec_prs: [316]
build_prs: [318]
---

# 107 · fix · Dashboard cards line up

## 2026-11-19 · spec PR

Marc, 19 Nov: the dashboard's cards don't line up; some rows are wider than others. No spec change: the fix is layout only, in the design and the kickoff.

- The "On this page" jump targets (item 073) were empty elements inside rows of cards; each took a 20 px gap. They go: the `id` sits on the card. Rule added to `design/DESIGN.md`.
- The three two-card rows share one 3 : 2 split from 1200 px; below that each card takes the full width.
- The app has the same spans: kickoff `107-fix-dashboard-alignment.md`, issue #315.

## 2026-11-20 · build PR #318

Layout only, no spec change. In the app the empty jump-target spans are gone from `Dashboard.tsx` (six), `Insights.tsx` (the `Question` header) and `Performance.tsx` (five): each `id` and `scrollMarginTop: 16` sits on the card or heading block that followed it, and the "On this page" chips keep their hrefs. The three two-card dashboard rows share `.sa-dash-pair` (`frontend/src/styles/global.css`): a `minmax(0,3fr) minmax(0,2fr)` grid with a 20 px gap and top-aligned cards from 1200 px, each card full width below. `Card`, `BriefingCard`, `Watchlist` and `TailsCard` take an optional `id`. `frontend/e2e/107-alignment.e2e.ts` checks the edges, gaps and shared split at 1440/1024/390 px and the Insights heading gaps; screenshots in `docs/screenshots/107/`.
