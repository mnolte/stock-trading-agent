# 108 · fix · Dividends counted like for like

Kickoff for Devin. Start the session with: "Build docs/kickoffs/108-fix-dividends-like-for-like.md."

Backend, plus one line of the Performance chart and one glossary sentence. No trading behaviour changes: no rule, gate, order or model input changes. Spec: `docs/SPEC.md` → Paper realism → **Dividends, like for like** (new), Performance screen → Equity curve, Insights → the Edge over SPY row; `docs/changes/108-fix-dividends-like-for-like.md`. Issue: #319.

```text
[108] Dividends counted like for like. Goal: every comparison with SPY
sets the agent's adjusted return against SPY with its dividends, stock
dividends are valued instead of counted as cash, and no corporate-action
fetch stops at 1,000 rows.
Start from the latest main. One PR. Put "Closes #319" in it.

Read first:
1. AGENTS.md (Commands: CI runs the tests) and docs/README.md.
2. docs/SPEC.md → Paper realism (all, the new "Dividends, like for like"
   part first), Performance screen → Equity curve, Insights → Edge over
   SPY; docs/changes/108-fix-dividends-like-for-like.md; issue #319.
3. Code: app/data/alpaca.py (parse_corporate_actions,
   fetch_corporate_actions), app/data/service.py
   (refresh_corporate_actions), app/engine/trades.py (_dividends),
   app/engine/outcomes.py (write_equity_snapshot, _spy_price),
   app/engine/performance.py (vs_spy, curve points, adjusted_total),
   app/shadows/compare.py (_spy_return and its callers),
   app/readiness/service.py (spy_return_pct, Return vs SPY),
   app/reviews/facts.py (spy_return_pct, vs_spy_pts),
   app/backtests/results.py (_spy_curve, ret_pct),
   app/insights/service.py (edge over SPY), app/reviews/glossary.py.

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR, and
set status: merged in docs/changes/108-fix-dividends-like-for-like.md
with a dated "build PR #n" section.

1. Complete fetches. fetch_corporate_actions passes limit=None (alpaca-py
   0.45 then pages until next_page_token is empty; with the default
   limit=1000 it stops at 1,000 in total). If a response still ends with
   a page token, return a failed DataResult ("corporate actions
   truncated"), never a short list: refresh_corporate_actions prunes rows
   it didn't see, so a short list would delete real dividends.

2. Stock dividends.
   - parse_corporate_actions: stock_dividends → action_type
     "stock_dividend" (the rate is new shares per share); cash_dividends
     → "dividend" as now.
   - refresh_corporate_actions dedupes on (symbol, action_type, ex date)
     already; with the new type a cash and a stock dividend on one
     ex-date are two rows. Test it.
   - _dividends (one function, its callers unchanged in meaning): a
     stock dividend adds qty × rate × close ÷ (1 + rate), close = the
     daily close of the session before the ex-date, from the daily bars
     the app already stores. No close: add nothing and log one WARNING
     line naming the symbol and date (no values in the message beyond
     those). Give _dividends what it needs to read that close without
     each caller querying bars itself.
   - Rows already stored as "dividend" that came from stock_dividends
     can't be told apart: after the migration, the first start refreshes
     corporate actions once from the oldest run's start date to today
     + 90 days (a flag in app state so it runs once), which rewrites
     them. Say in the PR how many rows changed on the live install.

3. SPY with dividends. One function, app/engine/benchmark.py:
     spy_return(session, start_day, end_day, price_start, price_end)
   = (price_end + Σ SPY cash dividends with start_day < ex ≤ end_day −
   price_start) ÷ price_start × 100, Decimal; plus
     spy_benchmark_with_dividends(run, snapshot) = the snapshot's stored
   spy_benchmark_value + run.starting_equity × (SPY dividends per share
   with run start < ex ≤ snapshot day) ÷ run.spy_close.
   The stored spy_benchmark_value stays price-only (no rewrite of old
   rows). The benchmark symbol comes from performance.benchmark, as now.

4. Like for like. Replace every price-only SPY return with step 3:
   - Performance: summary sentence, the vs SPY tile and vs_spy_pct =
     adjusted return − SPY with dividends; a raw figure (raw return −
     SPY price only) in the API as vs_spy_raw_pct for the hover.
   - Performance equity curve: each point gets adjusted equity (equity −
     slippage on fills up to that time + dividends with ex-date up to
     that day, from the same functions as adjusted_total) and the SPY
     line with dividends; the raw equity stays in the point for the
     hover. Design difference: the hover's extra line "Raw paper
     equity $10,184.20" uses the wording of the tile's raw figure.
   - Strategies (compare.py): _spy_return uses step 3.
   - Readiness: Return vs SPY uses step 3.
   - Weekly review facts: vs_spy_pts = the week's adjusted return − SPY
     with dividends over the same days; check which return
     reviews/facts.py uses today and say so in the PR. The prompt is
     unchanged: only the fact's value changes.
   - Backtests: SPY with dividends from the run's market.db (SPY must be
     in its corporate-action fetch; check), and the strategy return
     adjusted the same way as live (fake-broker equity − slippage +
     dividends). Older backtest results are not recomputed.
   - Insights: edge over SPY uses SPY with dividends.

5. Glossary. app/reviews/glossary.py → "adjusted P&L": "The result after
   costs: the assumed slippage is taken out and dividends are added."

6. Tests (no network, each its own database, fake clock):
   - fetch: a fake returning two pages of 600 actions → 1,200 stored; a
     response that ends with a page token → failed result, nothing
     pruned;
   - parse: a cash and a stock dividend on one ex-date → two rows with
     their types;
   - _dividends: cash (bought the day before the ex-date: credited;
     bought on the ex-date: not; sold on the ex-date: credited); stock
     dividend with a known close (100 shares, rate 0.05, close 210.00 →
     $1,000.00); stock dividend without a close → 0 and the WARNING;
   - spy_return: no dividend in the span; one on the last day (counted);
     one on the first day (not counted);
   - spy_benchmark_with_dividends: equals stored + starting equity ×
     dividends ÷ baseline close;
   - Performance, Strategies, Readiness and backtest results: a fixture
     with one SPY dividend and one agent dividend gives the same vs SPY
     everywhere, and differs from the price-only number by exactly the
     SPY dividend's share;
   - the one-time refresh runs once.

7. Screenshot in docs/screenshots/108/: Performance at 1440 with the
   hover on a curve point.

Rules: Decimal for money and returns; no datetime.now() in engine code
(the clock); the corporate-action sync stays one request through the
shared limiter per call (paging inside it); no change to what the model
is sent apart from the vs_spy fact's value; nothing in design/.
Tests are proven in CI: open the PR as a draft early, run locally only
the test files you change, never the full suites.

Done when: all CI checks are green and the PR merges cleanly; the PR
says how many corporate-action rows the one-time refresh changed on the
live install and lists the six points of #319 under "Issues closed"
with the file or test for each.
```

## Why

Marc, 20 November: "Are earnings (dividend) currently taken into account properly?" Reading the spec and the code on `main` found:

- Alpaca paper pays no dividends ([Alpaca docs](https://docs.alpaca.markets/docs/paper-trading): "Paper trading account does NOT simulate dividends"), and the adjusted P&L adds them back with the right entitlement rule, in one shared function. That part works.
- SPY's benchmark is price-only, but Strategies and Readiness compare it with the agent's adjusted return, which includes dividends: the agent gains SPY's yield, about 0.3% at each quarterly ex-date. Performance compares raw with price-only and so leaves the agent's own dividends out.
- `stock_dividends.rate` is shares per share, stored as dollars per share.
- alpaca-py 0.45's `CorporateActionsRequest` stops at 1,000 actions in total by default.
- The review glossary says dividends are "taken out".

## Assumptions

- Dividends are not reinvested, for the agent and for SPY: cash, like Alpaca would pay it.
- A stock dividend is valued at the close before its ex-date ÷ (1 + rate): the price after the ex-date, times the new shares.
- Alpaca paper doesn't add stock-dividend shares to the position either (its paper docs say it doesn't simulate dividends). If it does, reconciliation will see a quantity change: say so in the PR and stop.

## Open questions for Marc

None blocking. Decided without asking: the curve switches to adjusted equity, so it matches the headline and the comparison; raw equity moves to the hover. The ex-date price drop near a stop stays as it is: with stops at least 1.5 ATR away and quarterly dividends of about 0.2–1%, it should be rare.
