# 061 · feat · Decision v7: the market-context analysis (step A)

Kickoff for Devin. Start the session with: "Build docs/kickoffs/061-feat-decision-v7.md."

Issue #206. **Don't start before the start condition below is met.** This kickoff covers step A only: the numbers. Step B (the prompt, snapshot v7, checks, eval) gets its own spec PR by Claude after Marc has chosen from step A. Spec: `docs/SPEC.md` → **Decision v7: market context in the model call (planned)**.

```text
[061] Decision v7, step A: the market-context analysis. Goal: one report,
docs/analysis/061-market-context.md, that shows for each new market field
(VIX, economic releases, breadth, sectors) whether it separates good
entries from bad ones, with the numbers, and a list of candidate rules.
No code in the app changes; scripts go in backend/scripts/analysis/.

Start condition: 060, 067 and 068 are merged and live, and at least 20
trading sessions have passed since the last of them went live. Check it
first; if not met, say so in the PR and stop.

Read first:
1. AGENTS.md; docs/SPEC.md → Decision v7 (planned), Market regime → VIX,
   Breadth and sectors, Risk → Economic releases; Insights → Slices.
2. Earlier analyses for the format: docs/analysis/2026-11-03-decision-v4-review.md,
   docs/analysis/045-gui-catch-up.md.

Move this kickoff to docs/kickoffs/archive/ with git mv in this PR.

1. Data: live and shadow decisions, closed trades and option touches since
   the start condition's first session, from the live database (read-only
   copy, never the running file); plus one backtest run over at least 12
   months with all strategies (the fields come from history there).
2. Tables, each split live / shadows / backtest, with n per cell and the
   90% range of average R (bootstrap, 2,000 resamples; fixed seed):
   a. trades and option touches (target first, stop first, neither) by VIX
      percentile tercile, VIX 5-session change (< −10%, flat, > +10%),
      breadth tercile, sector rank (1–3, 4–8, 9–11), stock vs sector (<0,
      >0), sessions to the next release (0, 1, 2+), release next morning;
   b. overnight gaps of held positions (open vs previous close, in R and in
      ATR) by VIX level, VIX change and release next morning;
   c. what each adds: the VIX within ATR-percentile terciles; sector rank
      within rs_vs_spy_pct terciles; breadth within regime trend;
   d. fomc_window: candidates removed and their touches; 15:46 entries since
      item 071 against the other cycles.
3. Candidate rules: for each field, at most two rules with the numbers that
   support them, written as the prompt or code would state them ("no new
   entry in the last two cycles when the VIX rose more than 15% in 5
   sessions: 23 trades, average −0.41R [−0.72, −0.08] vs +0.12R"). List
   the fields with no support plainly.
4. A cell with fewer than 20 trades is shown but marked "too few"; no rule
   rests on one.

Rules: read-only on the live data; no network in tests; scripts runnable
again later (make analysis-061) so the report can be refreshed.

Done when: the report is in a PR titled "[061] analysis: Market context for
decision v7", with every table, the candidate rules and the fields to
drop; Marc comments with his choice.
```

## Why

The VIX, releases, breadth and sectors cost a prompt version, an eval and a shadow to bring into decisions. They're worth it only where the data shows they change outcomes. Step A answers that once for all four.
