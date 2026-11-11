# 068 · feat · Breadth and sectors, collected and shown

Kickoff for Devin. Start the session with: "Build docs/kickoffs/068-feat-breadth-sectors.md."

Issue #216. Collect first, like the VIX (060): stored and shown, not sent to the model and not used by any rule. Spec: `docs/SPEC.md` → Market regime → **Breadth and sectors**, plus the lines `docs/CHANGES.md` entry [068] names. Design: `design/CHANGES.md` entry [068].

```text
[068] Breadth and sectors. Goal: every cycle stores four breadth numbers and
a table of the 11 sector ETFs' strength vs SPY; every decision stores its
candidate's sector fields outside the model's input; the watchlist,
the header hover and Insights show them. Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md: Other data (the sector ETF row), Market regime → Breadth and
   sectors, Risk → Sector cap (the sector resolver), Data model (cycles,
   decisions.market_context), the GUI header and dashboard rows, Insights →
   Slices; docs/CHANGES.md [068].
3. design/CHANGES.md [068]; design/screens/Main.dc.html (watchlist),
   Insights.dc.html (Slices, Sectors now).

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.

STEP 1 · backend (tests first)
- Config in backend/config.example.yaml (this PR):
    market:
      sector_etfs: {Technology: XLK, Financials: XLF, Communication Services: XLC,
        Industrials: XLI, Consumer Discretionary: XLY, Materials: XLB,
        Health Care: XLV, Consumer Staples: XLP, Energy: XLE,
        Utilities: XLU, Real Estate: XLRE}
      breadth: {min_symbols: 20}
  Use the sector names the sector resolver already returns; list any it
  returns that the map lacks in the PR.
- Reference symbols: the 11 ETFs, like SPY and VIXY (060): bars downloaded
  and cached every cycle, never candidates, never screened in, never
  traded, never on the watchlist. One test that the screen and the
  candidate list exclude them even if they'd pass.
- Per cycle (before decisions), on cycles (Alembic, nullable):
  breadth_above_sma50_pct, breadth_above_sma200_pct, breadth_ema_up_pct,
  breadth_up_today_pct over the cycle's universe, from the indicators the
  cycle already computed (no new downloads); null under min_symbols.
  sectors JSON: per ETF rs_vs_spy_pct (signals/indicators.py
  relative_strength_pct, indicators.rs_lookback) and rank 1–11.
- Per decision: market_context JSON with sector_etf, sector_rank,
  sector_rs_vs_spy_pct and rs_vs_sector_pct (the stock vs its ETF, same
  function). Not added to any snapshot or prompt input: check that the
  stored input snapshot and its hash are unchanged.
- Backtests: the ETFs' bars into bars.db like any symbol; the same fields.
- Tests (fake bars, no network): breadth on a fixed 25-symbol universe;
  null with 19; a stock's rs_vs_sector_pct against its ETF; ranks 1–11;
  missing bars for one ETF leave that sector null and the rest ranked;
  snapshot hash unchanged for a decision with and without the new fields.

STEP 2 · frontend
- Watchlist: a Sector column after Day ("XLK 2/11", "—" for Index), sortable;
  min width 890 px inside the card's scroll.
- Header Regime · VIX hover: "Breadth: 31% above SMA 50 · 44% up today".
- Insights → Slices: Breadth (terciles of breadth_above_sma50_pct), Sector
  rank (1–3, 4–8, 9–11), Stock vs sector (below 0, above 0).
- Insights → Sectors now: the 11 ETFs ranked at the last cycle, with breadth
  in the note.
- Labels and numbers through the 056 label map and formatter.

STEP 3 · screenshots in docs/screenshots/pr-<n>/: the watchlist at 1440 and
390, the Sectors now card, the three slices.

Rules: no network in tests; Decimal or float only where the indicators
already use it (no money here); prompts and snapshots untouched.

Done when: a live and a backtest cycle store breadth and sectors; every
decision stores market_context; the GUI shows them as designed; make test
and the e2e pass.
```

## Why

On 9 November the universe was "mostly in downtrends" while SPY held up; that was breadth, read by hand from rule failures. And "the stock beats SPY" says nothing about whether its whole sector is rallying. Decision.v7 (061) will test both against outcomes; this item collects them.
