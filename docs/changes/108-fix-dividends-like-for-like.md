---
item: 108
type: fix
title: Dividends counted like for like
status: ready
issue: 319
kickoff: 108-fix-dividends-like-for-like.md
spec_prs: [320]
build_prs: []
---

# 108 · fix · Dividends counted like for like

## 2026-11-20 · spec PR

Marc asked whether dividends are taken into account properly. The agent's own dividends are; the comparisons with SPY and two edge cases are not.

- **Paper realism → Dividends, like for like** (new): the entitlement rule written out; stock dividends valued at the close before the ex-date ÷ (1 + rate), stored as their own type; SPY's return counts SPY's dividends, from one function, computed at read time (stored snapshots stay price-only); every comparison with SPY sets adjusted against SPY with dividends, raw against price-only on hover; a corporate-action fetch is complete or failed, never short.
- **Overview and the goals table:** the benchmark is SPY "with its dividends".
- **Performance screen → Equity curve:** the lines are adjusted equity and SPY with dividends; raw equity on hover.
- **Insights → Edge over SPY:** SPY's return with its dividends.
- Kickoff `108-fix-dividends-like-for-like.md`, issue #319. It also fixes the review glossary, which said dividends are "taken out".
