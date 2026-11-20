---
item: 108
type: fix
title: Dividends counted like for like
status: merged
issue: 319
kickoff: 108-fix-dividends-like-for-like.md
spec_prs: [320]
build_prs: [PR]
---

# 108 · fix · Dividends counted like for like

## 2026-11-20 · spec PR

Marc asked whether dividends are taken into account properly. The agent's own dividends are; the comparisons with SPY and two edge cases are not.

- **Paper realism → Dividends, like for like** (new): the entitlement rule written out; stock dividends valued at the close before the ex-date ÷ (1 + rate), stored as their own type; SPY's return counts SPY's dividends, from one function, computed at read time (stored snapshots stay price-only); every comparison with SPY sets adjusted against SPY with dividends, raw against price-only on hover; a corporate-action fetch is complete or failed, never short.
- **Overview and the goals table:** the benchmark is SPY "with its dividends".
- **Performance screen → Equity curve:** the lines are adjusted equity and SPY with dividends; raw equity on hover.
- **Insights → Edge over SPY:** SPY's return with its dividends.
- Kickoff `108-fix-dividends-like-for-like.md`, issue #319. It also fixes the review glossary, which said dividends are "taken out".

## 2026-11-21 · build PR #PR

Behaviour change, spec already written by the spec PR. `app/engine/trades.py` `_dividends` credits cash dividends as before and values stock dividends at `qty × rate × prior-session close ÷ (1 + rate)` through a `close_before` callback over stored `bars_1d`; a missing close adds zero and logs one warning naming symbol and ex-date. `app/data/alpaca.py` parses `stock_dividends` as `stock_dividend` (cash stays `dividend`) and fetches corporate actions with `limit=None` so the SDK pages inside the one limiter call; a response still carrying `next_page_token` returns a failed `DataResult`, so refresh never prunes on a short list. New `app/engine/benchmark.py` holds the one SPY total-return function (`spy_return`) and `spy_benchmark_with_dividends`, which grows the stored price-only `spy_benchmark_value` by SPY's dividends since the run start — snapshots stay price-only. `app/engine/performance.py` marks each curve point's adjusted equity (raw − slippage to date + dividends to date), draws SPY with dividends, keeps the raw equity on the point, and reports `vs_spy_pct` as adjusted vs SPY total return with `vs_spy_raw_pct` for the price-only comparison. Callers moved to the shared function and the extended `_dividends`: `app/shadows/portfolio.py`, `app/shadows/compare.py`, `app/reviews/facts.py` (weekly and yesterday facts), `app/backtests/results.py` (+ `worker.py` passes the run's slippage), `app/insights/service.py`/`compute.py` (edge over SPY with dividends, all action types). `app/data/corporate_repair.py` runs once at startup (event-flagged, retried on fetch failure): refetches corporate actions from the oldest run's start through today + 90 days so mislabelled rows are rewritten; `app/api/main.py` calls it after the daily refresh outside simulated mode. Glossary now reads "the assumed slippage is taken out and dividends are added." Frontend: the curve draws adjusted equity and SPY with dividends, hover shows the raw equity, and the vs-SPY tile shows the price-only figure on its small line. Tests in `backend/tests/test_dividends_108.py`.
