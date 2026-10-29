# Analysis — IEX quote plausibility

Kickoff for Devin. Start the session with: "Build docs/kickoffs/quote-plausibility.md."

```text
Analysis, read-only: how often is the IEX quote implausible, and is the
spread gate effectively switched off?

Background: SPEC → Alpaca integration says bid and ask are used only when
plausible (both present, timestamped, within 1% of the market price);
otherwise the snapshot says spread_unknown and the entry may go ahead,
so the 0.15% spread gate doesn't run. On 28 Oct, ACN (rank 1, the most
liquid symbol) had bid 229.90 and ask 247.00 against a last trade of
243.745 at 15:46 NY, and SBUX had bid 99.98 / ask 104.18 against
103.84. If that's the normal case, the spread gate rarely runs without
anyone having decided that.

Do not change any code, config or data. Use the stored price
provenance on decisions (and shadow decisions) of the current run.

Report in docs/analysis/2026-10-29-quote-plausibility.md:
1. Share of snapshots with plausible = false, overall and per cycle
   time (10:46 … 15:46), per symbol (top 20 by count) and per liquidity
   rank band (1–10, 11–30, 31+).
2. For implausible quotes, which condition failed: bid missing, ask
   missing, timestamp missing, bid more than 1% away, ask more than 1%
   away. Show the distribution of distances.
3. For plausible quotes: the spread distribution in % (median, p90,
   p99) and how many entries the 0.15% gate blocked.
4. How many entries went ahead with spread_unknown, and their entry
   slippage in bps against the last trade, compared with entries that
   had a plausible quote.
5. price_stale: how many candidate checks were blocked as price_stale
   or price_suspect per day and per symbol (29 Oct: 16 across 10
   symbols, 4.9% against the 5% Readiness target), and how old the last
   trade was when blocked. Is the 60-second freshness rule the main
   cause on the free IEX feed?
6. Optional, only if it needs no new data source: what the 1-minute bar
   range (high − low) at the decision time looked like, as a possible
   replacement measure.
7. A short conclusion with numbers: is the spread gate effectively off,
   and which of these would you recommend: keep as is, drop the gate
   officially, or replace it with another measure. Don't implement any
   of them.

Use a script under scripts/analysis/ (read-only database access,
mode=ro), so the numbers can be produced again. Open a PR with the
report and the script only. It touches docs/analysis/, so add the
"spec change" label and a docs/CHANGES.md entry. No restart needed.
```
