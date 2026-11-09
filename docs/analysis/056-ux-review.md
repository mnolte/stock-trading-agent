# 056 · UX review of the running app

By Claude, 9 November 2026. Earlier reviews (1 November, 5 November) compared the designs with the spec. This one looks at the **app itself**: main at `aecbce0` (055 merged), in simulated mode with the README seed (`tests.e2e_seed`, `e2e_backtest_seed`, `readme_seed`), frozen clock 15 Oct 2026 19:00 Amsterdam. Every route was captured at 1440 px and 390 px in Chromium with the `nl-NL` locale and `Europe/Amsterdam` time zone, which is what Marc's browser most likely sends. The question for each screen: can Marc see quickly what needs him, what the agent did and why, and how it is doing, without fighting the screen?

Sample data comes from the seed, so some numbers contradict each other (Return 0.00% next to +$612.60 of closed trades). Those are seed effects and are left out below.

## Summary

| # | Finding | Severity | Where it's fixed |
| --- | --- | --- | --- |
| 1 | A broken weekly review takes down the whole Reviews screen | high | spec + design (card boundary), kickoff |
| 2 | Needs you and toasts print full stack traces; a toast covers the kill switch | high | spec + design rule, kickoff |
| 3 | Settings is one 11,125 px page (18,936 px on a phone); the section list marks the wrong section | high | spec + design |
| 4 | Four screens scroll sideways at 390 px | high | kickoff (the design already doesn't) |
| 5 | Codes instead of words: `earnings_exit`, `agent_exit`, "Verdict verdict" | medium | design rule, kickoff |
| 6 | Dates in four formats on one screen | medium | spec + design rule, kickoff |
| 7 | Backtest results: two numbers per tile, told apart by colour only | medium | design, kickoff |
| 8 | A lone tile stretched across a whole row (Performance, Insights); tile values cut off | medium | design, kickoff |
| 9 | Smaller bugs: wrong page title, unstyled button, "Replace" on a key that isn't set, missing separator, empty card space | low | kickoff |
| 10 | Text repeated three times on Universe; "No order" without a reason in the drawer | low | spec, kickoff |

## Findings

### 1. One bad record takes down a whole screen (high)

- `/reviews` shows "Something went wrong on this page" at both widths. Cause: the seeded weekly review stores `facts.trades` as a count (`12`), and `WeeklyView` iterates it (`TypeError: (facts.trades ?? []) is not iterable`, `Reviews.tsx:115`).
- The Briefings tab sits on the same screen, so it's gone too, although nothing is wrong with it.
- Each load logs the error again: 5 identical ERROR lines in 5 minutes, each a new "error not reviewed".
- **Fix:** an error boundary per card, not only per screen (spec: Frontend errors; design: States → Card states → *Failed to draw*). The same error from the same card is logged once per page load. `WeeklyView` accepts both shapes.

### 2. Errors are shown raw, and a toast covers the kill switch (high)

- Needs you showed "2 errors not reviewed." followed by about 40 lines of stack trace with Vite URLs; on a phone the text ran 41 px past the screen edge.
- The "New error" toast repeats it, sits over the header's top-right corner and hides **Resume** and **Kill switch** for 10 seconds.
- The design already shows one summary per group ("SMTP authentication failed ×2 · decision call failed ×2"); the app passes the raw message.
- **Fix:** an error's display text is its group's summary: the first line of the message, without URLs or file paths, at most 120 characters, with a count. The stack trace is shown only in Logs. Toasts sit below the header and never cover its buttons.
- (The chart's own crash came from Chromium's `en-US@posix` locale in this container; real browsers don't send it. The chart should still catch it: one card, not the dashboard.)

### 3. Settings is one very long page (high)

- 11,125 px at 1440 and 18,936 px at 390: 16 sections stacked.
- The section list highlights *Risk limits* while *API keys and data* is on screen.
- On a phone the section list is a scrolling row whose current item is off-screen.
- The usual task is "change one limit" or "check one key". Scrolling past 15 sections to do it is slow, and the sticky save bar sits over whichever section is in view.
- **Fix (design + spec):** one section at a time, picked from the list (a select on phones), each with its own address (`/settings/risk`). Previous/next links at the bottom. "Find a setting" and "Changed from default" show matching rows from every section, grouped by section. Unsaved changes survive switching sections; the save bar lists them all.

### 4. Sideways scroll at 390 px (high)

| Screen | Width | What sticks out |
| --- | --- | --- |
| Dashboard | 431 px | Needs you error text (finding 2) |
| Insights | 540 px | Slices table without its scroll wrapper |
| Backtest | 410 px | By market regime card |
| Logs | 820 px | Recurring problems table |

The designs scroll these tables inside their card. The kickoff adds a Playwright check that every route has `scrollWidth` 390 at 390 px.

### 5. Codes instead of words (medium)

- Performance → Winners and tails: a tile titled "Agent_exit exits"; the Exit column shows `earnings_exit` and `agent_exit`. The design says "agent exit".
- LLM inspector: "Verdict verdict", "Weekly weekly", "Briefing briefing": the call kind printed twice.
- **Rule:** every stored code has one label (`exit_reason`, action, source, reason codes), kept in one frontend map. Raw codes appear only in the inspector's raw response, Logs and exports, and in tooltips.

### 6. Dates in four formats (medium)

- Backtest: "2026-09-14 – 2026-09-25" (title), "14 Oct 20:04:00" (meta), "14 Sept" (chart), and native date inputs showing `mm/dd/yyyy`, with *To* set to the real date (9 Nov) instead of the agent's clock (15 Oct).
- Insights: "snapshot 2026-10-14", "MSFT on 2026-10-13". Performance: "15 Sept"; dashboard: "since 14 Sep".
- **Rule:** "14 Sep" within the current year, "14 Sep 2025" otherwise; ranges "14–25 Sep 2026"; times "19:00" (seconds only in Logs). Month names are fixed English abbreviations, not the browser's (which gives "Sept" or "sep."). ISO dates appear only in Logs, exports and URLs. Date fields default from the agent's clock.

### 7. Backtest results tell numbers apart by colour only (medium)

- Each of 8 tiles shows two numbers ("+3.1%  +0.8%"); which is Live and which is Rules only is shown only by colour and a legend below. This breaks the "never colour alone" rule, and the design had the same tiles.
- **Fix (design):** one table, *Metric · Live settings · Rules only · SPY buy and hold*, with the strategy name in each column header.

### 8. Tile rows (medium)

- Performance: 5 tiles, then *Max drawdown* alone across the full width. Insights: 5, then *Model cost per trade* alone. The designs had the same wrap.
- Insights: "β −0.31 · r −0.2" and "−$259.10 / $871." cut off at the tile edge.
- **Fix (design):** a tile row is a grid: 6 per row from 1181 px, 3 from 821 px, 2 on a phone. Values wrap, never clip.

### 9. Smaller bugs (low)

- Insights shows "Dashboard" as the page title in the header.
- Insights → Findings: "Show the trades" is an unstyled browser button; the first Slices row shows a grey empty box instead of its label.
- Settings → API keys: "Replace" on the Alpaca keys while they are "not set" (should be "Add key"); the hint reads "Current: v—" before any version exists.
- Cycle strip: "1 model call · $0.000.next cycle 19:46" (separator missing).
- Dashboard: Today's decisions and Daily briefing stretch to the chart's height, leaving 250–300 px of empty card. On a phone (one column) the briefing card still keeps that height.
- Watchlist at 390: the Day column is cut off at the card edge.
- LLM inspector at 1440: Call details stays empty until a call is clicked. The design opens the newest call.
- Backtest: "Run backtest" looks disabled without saying why (no From date). "Read with care" lists the assumed spread twice. "−0.0%" for a zero drawdown.

### 10. Repetition and missing reasons (low)

- Universe in fixed-list mode says three times that the screen is a preview (banner, note under the list, How the screen works). Once is enough: the banner.
- Decision drawer: "Order: No order — enter long", repeated in the footer. The reason is missing: here the agent was paused. The designs say "shadow D · no order · …". **Rule:** when no order was sent, What happened says why (paused, supervised and waiting, kill switch, a gate, a shadow, the market closed, outside the entry window).
- The "Started paused" banner and the header both offer **Resume**; that is fine and stays.

## What works well

- The header, the Needs you card with its health line and the risk budget answer "is it safe?" in one glance.
- The decision drawer reads in order: action, tiles, What happened, options. The R/R and option table are clear.
- Readiness: verdict, five tiles and per-criterion progress with "earliest possible ≈ 17 Jun 2027".
- Loading (055): skeletons in place, no flashes at local speed.
- No console errors other than the two crashes above, on any route at either width.

## Method

- Backend `python -m app.serve` (simulated, `SA_SIM_NOW=2026-10-15T17:00:00Z`) and the Vite dev server, as `make readme-screens` runs them.
- Playwright: each route at 1440×900 and 390×844, 5 s settle, full-height capture; `scrollWidth` and console errors recorded per route; the elements past 390 px listed per screen.
- The decision drawer was opened on decision 1 (MSFT, decision.v2).
