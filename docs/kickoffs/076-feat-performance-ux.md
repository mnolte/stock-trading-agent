# 076 · feat · Performance UX

Kickoff for Devin. Start the session with: "Build docs/kickoffs/076-feat-performance-ux.md."

Backend (summary sentence, exits and drawdown span in the performance response) and frontend. No metric changes. Spec: `docs/SPEC.md` → **Performance screen** (item 076) and Tail metrics → GUI; `docs/CHANGES.md` entry [076]. Design: `design/screens/Performance.dc.html`, `design/CHANGES.md` entry [076]. Issue: #233.

```text
[076] Performance UX. Goal: Performance opens with one plain sentence,
tiles say what they mean, the curve shows exits and the deepest drawdown,
codes become labels, and empty or pending tables fold into one line.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md → Performance screen (item 076), Tail metrics;
   docs/CHANGES.md [076].
3. design/CHANGES.md [076]; design/screens/Performance.dc.html with
   Performance.png and Performance-390.png.

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR.
Put "Closes #233" in the PR description.

1. Summary sentence. The performance endpoint returns `summary`: one or
   two sentences from a fixed template over the same numbers as the tiles
   (sessions, closed trades and won, adjusted P&L in $ and %, return vs
   SPY, open positions). No model. Unit test per branch (no trades yet,
   losing, winning, ahead of or behind SPY).

2. Small sample. One banner under the summary below
   performance.tail.min_trades (20) closed trades, "n of 20 trades".

3. Tiles. Six tiles with a plain line under the value from the label map
   (the design's wording, numbers filled in) and the definition on hover.

4. Equity curve. The response gains `exits` (date, symbol, R, won) and
   `drawdown` (peak date, low date, %). Exits are rings on the curve
   (green won, red lost) with "symbol · result" on hover; the deepest
   drawdown is shaded from peak to low. Both in the legend.

5. Labels. Exit reasons and actions through the label map everywhere on
   the screen (Winners and tails, Closed trades): never earnings_exit or
   agent_exit.

6. Folding.
   - By regime: only regimes with closed trades; then "No closed trades
     yet in: …", and the down · high vol note.
   - After the exit: all pending → one line with the date of the first
     result (the earliest exit + 5 sessions, from the trading calendar);
     some done → pending rows grouped at the bottom.
   - Decisions and outcomes: a price not in yet says "waiting · first
     prices <date>".

7. Closed trades. Newest first, 20 rows then "Show all n"; filters for
   symbol and exit reason (in the URL); an R bar from −1R to the trade's R
   (capped at 2R in width).

8. Screenshots in docs/screenshots/076/: 1440 and 390, under 20 trades,
   and After the exit all pending.

Rules: no network in tests; the summary and exits come from the same
functions as the tiles (no second calculation); Decimal for money; nothing
in design/.

Done when: the screen matches Performance.dc.html at 1440 and 390 with no
sideways scroll; no raw exit or action code on the screen (Playwright
check for /_exit|agent_exit/); make test and the e2e pass.
```

## Why

Review of the running app on 11 November:

- No sentence said how the run was going; six tiles meant nothing to a newcomer without hovering.
- `earnings_exit` and `agent_exit` showed as codes in Winners and tails (the 056 label rule wasn't applied there).
- By regime was mostly rows of zeros; After the exit was 12 rows of "pending".

## Assumptions

- Exits and the drawdown span come from data the endpoint already reads (closed positions, the daily equity series).

## Open questions for Marc

None.
