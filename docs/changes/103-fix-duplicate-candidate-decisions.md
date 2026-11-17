---
item: 103
type: fix
title: Duplicate candidate no_call rows in expected_profit selection
status: merged
issue: 308
kickoff: —
spec_prs: []
build_prs: [309]
---

# 103 · fix · Duplicate candidate no_call rows in expected_profit selection

## 2026-11-17 · build PR #309

Found analysing the live data of 16 November 2026. In `expected_profit` selection mode a candidate could get two decision rows in one cycle: the price-blocked backfill re-checked quote staleness after the model wave, so a trade that aged past the 60 s IEX limit mid-cycle got a second `no_call`/`price_stale` row on top of its real decision. The backfill now covers only the candidates the pre-score skipped, so every candidate stores exactly one decision per cycle. No spec, config or prompt change.
