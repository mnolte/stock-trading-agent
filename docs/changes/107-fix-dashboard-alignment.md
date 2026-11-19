---
item: 107
type: fix
title: Dashboard cards line up
status: ready
issue: 315
kickoff: 107-fix-dashboard-alignment.md
spec_prs: [PRNUM]
build_prs: []
---

# 107 · fix · Dashboard cards line up

## 2026-11-19 · spec PR

Marc, 19 Nov: the dashboard's cards don't line up; some rows are wider than others. No spec change: the fix is layout only, in the design and the kickoff.

- The "On this page" jump targets (item 073) were empty elements inside rows of cards; each took a 20 px gap. They go: the `id` sits on the card. Rule added to `design/DESIGN.md`.
- The three two-card rows share one 3 : 2 split from 1200 px; below that each card takes the full width.
- The app has the same spans: kickoff `107-fix-dashboard-alignment.md`, issue #315.
