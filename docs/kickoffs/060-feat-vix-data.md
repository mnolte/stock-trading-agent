# 060 · feat · VIX data, collected and shown

Kickoff for Devin. Start the session with: "Build docs/kickoffs/060-feat-vix-data.md."

Issue #205, step 1 of 2. Step 2 (decision.v7 with the VIX in the model call) is item 061, issue #206, and waits for 20–30 sessions of this item's data. Nothing in trading changes here: not the regime label, not a gate, not the model's input. Spec: `docs/SPEC.md` → Market regime → **VIX**, and `docs/CHANGES.md` entry [060]. Design: `design/CHANGES.md` entry [060].

```text
[060] VIX data. Goal: every cycle (live, shadow, backtest) stores the VIX
close, its 1-year percentile, its 5-session change, VIXY's move today and
since that close, and a stale flag; the header, the latest-decision card,
Insights and Settings show it as the design does. No decision uses it yet.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md: Other data (the two new rows), Market regime → VIX,
   API keys (the optional FRED key), Data model → cycles, Backtesting →
   Data, Insights → Slices, the GUI header sentence; docs/CHANGES.md [060].
3. design/CHANGES.md [060]; design/screens/Main.dc.html (header Regime ·
   VIX, Latest decision chip), States.dc.html (VIX stale, unavailable),
   Settings.dc.html → Data sources, Insights.dc.html → Slices.

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.

STEP 0 · the source (results in the PR, before code)
- From our server, fetch https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS
  without a key. Record: works or not, the header line, how missing days
  look ("." or empty), and at what New York time today's posting of the
  previous close appeared (FRED showed "updated 9 Nov 15:03 CST" for the
  6 Nov close). If the keyless CSV fails, use the FRED API
  (series/observations, file_type=json) with a key from Settings.

STEP 1 · backend (one PR, tests first)
- data/fred.py: FredVixSource like data/ecb.py (protocol in protocols.py,
  fake in fake.py, wired in factory.py and the simulated mode). Returns
  (date, close) pairs; skips missing values; timeouts and retries as the
  other sources; failures feed data-source health.
- Schedule: fetch at startup (260 sessions of history), daily at 08:00 New
  York with the earnings job, and at 17:00 New York. Store closes in a
  vix_closes table (date primary key, close Decimal as text, fetched_at).
- VIXY: a reference symbol like SPY, from market.vix.reference_etf. Its
  5-minute and daily bars are downloaded and cached every cycle; it is
  never a candidate, never screened in, never traded, never on the
  watchlist. Test that the universe screen and the candidate list exclude
  it even if it would pass the screen.
- Per cycle, before the decisions, compute and store on cycles (Alembic
  migration, nullable columns): vix_close, vix_date, vix_percentile_1y
  (over the last market.vix.percentile_sessions closes, 252),
  vix_change_5d_pct (5 closes back), vixy_change_today_pct (previous
  session close → the cycle's last 5-min bar), vixy_change_since_vix_pct
  (VIXY's close on vix_date → the same bar), vix_stale (vix_date more than
  market.vix.stale_sessions, 1, trading sessions before the last completed
  session). Position checks don't compute them.
- Missing: no closes at all → all null; a cycle never fails or waits for
  the VIX.
- Backtests: the worker fills vix_closes for the period from FRED and VIXY
  bars into bars.db like other symbols; backtest cycles compute the same
  fields with the run's clock (only closes dated before the cycle's
  session, as FRED would have had them: the close of session d is usable
  from 17:00 New York on session d+1).
- API keys: an optional FRED key (Settings → API keys and data), tested by
  fetching one VIXCLS observation; never required to start or resume.
- Config, added to backend/config.example.yaml in this PR:
  market.vix.source: fred, percentile_sessions: 252, stale_sessions: 1,
  reference_etf: VIXY.
- Tests (fake FRED, fake clock, no network): percentile and 5-session
  change on a fixed series; stale true when FRED is 2 sessions behind,
  false at the normal 1-session lag on a Monday after a Friday holiday;
  VIXY since-close across a weekend; missing FRED → nulls and the
  data-source warning; backtest fields use no close posted after the
  cycle's time.

STEP 2 · frontend (one PR)
- Header: the VIX on the Regime stat's label line ("REGIME · VIX 17.4"),
  details in its tooltip (date, percentile, 5-session change, VIXY since
  the close and today); stale in amber with its date; "—" when null.
  Below 820 px: in the regime chip's tooltip only. Same height and width
  as before: check at 1440 and 1024 that Pause and the kill switch stay on
  the stats row.
- Latest decision card: chip "VIX 17.4 · 42nd pct" from its cycle.
- Loading: the header's Regime · VIX bar (MainLoading.dc.html).
- Insights → Slices: "VIX level" (vix_percentile_1y terciles) and "VIX
  5-session change" (below −10 %, −10 to +10 %, above +10 %), with the
  usual "not enough data" until 30 trades.
- Settings → Data sources: "VIX (FRED)" with close, its date and last
  fetch; the data-source-failing notification lists FRED.
- Labels and dates through the 056 label map and date formatter.

STEP 3 · screenshots in docs/screenshots/pr-<n>/ next to the design PNGs:
the header at 1440, 1024 and 390, the latest-decision card, the stale and
unavailable states, Settings → Data sources, Insights slices.

Rules: no network in tests; no datetime.now() in engine code; money and
index values as Decimal; prompts and snapshots untouched (the model sees
nothing new); backend/config.example.yaml boots main at every commit.

Done when: a live cycle stores the seven fields; a backtest run stores
them from history; the header and the latest-decision card show the VIX
at 1440, 1024 and 390; Insights has the two slices; make test and the e2e
pass.
```

## Why

The regime's volatility label measures how much SPY has moved; the VIX is what the market expects. Friday 6 November (SPY −4.3 %, VIX 25.64) showed in the VIX that afternoon, while the ATR percentile catches up over sessions. Before the model gets the VIX (061), 20–30 sessions of it stored next to our decisions should show whether it separates good entries from bad ones.

## Assumptions

- FRED posts a close the next trading day around 16:00 New York (observed: 6 Nov's close on 9 Nov at 15:03 Central). So during a session the newest close is from two sessions back; `vixy_change_since_vix_pct` bridges that. Step 0 confirms the time.
- The keyless CSV keeps working; the optional key is the fallback.

## Open questions for Marc

None. Decided without asking (say if you want otherwise): the VIX shows in the header on every screen (on the Regime stat, so the header doesn't grow), not only on the dashboard.
