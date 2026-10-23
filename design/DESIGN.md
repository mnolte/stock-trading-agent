# Stock Agent — design

How Stock Agent looks and what it says. It uses the same visual language as Trading Agent: dark "instrument panel" surfaces, IBM Plex Sans for text and IBM Plex Mono for every number.

## What decides what

- **`docs/SPEC.md` decides behaviour.** If a screen shows behaviour the spec doesn't describe, the spec wins; ask in the PR.
- **This folder decides look and wording.** Copy text as written: labels, button names, banner sentences, empty states.
- Deliberate differences go under "Design differences" in the PR description.

## What's here

| Path | Contents |
| --- | --- |
| `tokens.css` | Every colour, font, size, spacing and radius. Copy it into `frontend/src/styles/`; use only these variables. |
| `screens/` | The 7 screens as HTML: the exact source for styles, layout and wording. |
| `screenshots/` | One PNG per screen at 1440 px wide (Decision at 560 px), plus `Main-390.png` at phone width. |
| `CHANGES.md` | Design change log, newest first. |

## How to read the screen files

- Each `.dc.html` file is plain HTML with inline styles, plus a small `renderVals()` script at the bottom.
- `{{name}}` marks a value filled in by that script; `<sc-for>` repeats an element over a list; `<sc-if>` shows it conditionally. In React these become props, `.map()` and conditional rendering.
- The script holds **sample data only** (prices, trades, times). The real numbers come from the backend. The sample data is consistent across screens, so use it for fixtures and Playwright tests.
- The files load a design-tool runtime (`support.js`) that is not included; read them as source and compare with the screenshots. Don't copy the HTML into the app: rebuild it in React with the tokens.

## Screens

| File | Screen | Built in |
| --- | --- | --- |
| `Main.dc.html` | Dashboard: header, this-cycle line with cycle counter, summary tiles (positions, model calls today, since start), daily briefing and risk budget cards, price chart, latest decision, watchlist, open positions, today's decisions | PR 1 (header, sidebar), PR 3 (watchlist rules column), PR 6 (rest), PR 10 (risk budget card, cycle counter, briefing card, model call counter) |
| `Decision.dc.html` | Decision detail drawer, 560 px, in Trading Agent's layout (gates, signal stack, reasoning, levels, outcome), opened from the dashboard, positions and inspector | PR 4 (model part), PR 6 |
| `Positions.dc.html` | Positions & orders: reconciliation status, positions with their bracket legs, day trades, account, order history | PR 5 (data), PR 6 |
| `Performance.dc.html` | Performance: summary, equity vs SPY with USD/EUR switch, by regime, decisions and outcomes, closed trades | PR 7 |
| `Inspector.dc.html` | LLM inspector: calls today, metadata, validation result, prompt, raw response | PR 4 |
| `Universe.dc.html` | Universe (v0.2): mode banner, counts, today's screen as a funnel, how the screen works, pool table with filters, search, rank, dollar volume, return vs SPY, status, add, CSV import and remove | PR 11 |
| `Strategies.dc.html` | Strategies (v0.2): shadow cost and slots, comparison table (live, shadows, rules only), small-sample warning, equity curves with SPY, selected strategy with Pause, Archive, Promote, disagreements list | PR 13 |
| `Backtest.dc.html` | Backtest (v0.2): new-backtest form with quick ranges, strategy picker, estimate and model-cutoff check; runs list with progress and cost; results tiles per strategy, equity curves with SPY, by regime, Read with care | PR 14 |
| `Readiness.dc.html` | Readiness: verdict banner, summary tiles, Evidence / Model / Operations criteria with value, target, progress bar and status (pass, not yet, waiting for data), manual items, history | PR 16 |
| `Settings.dc.html` | Settings: section list with filter; API keys (masked, Test, Replace, inline "Test and save" form) and data sources; trading; risk limits in three groups; entry rules; earnings; model and reviews; notifications (event × browser/email, SMTP, repeat limit, quiet hours, recently sent); schedule; history; paper reset. One row per setting with its default, changed rows highlighted with Reset, sticky unsaved-changes bar with reason. `Settings-390.png` shows phone width | PR 6, redesigned in PR 10 |
| `Reviews.dc.html` | Reviews: weekly review with stats, closed trades, skipped setups after 5 sessions, observations, suggestion (not applied), verdicts to confirm; Briefings tab | PR 9 |
| `States.dc.html` | Session states, all banners, market-closed dashboard, pending approval, and 5 dialogs | PR 5 (kill switch), PR 6, PR 8 (error states) |

## Shared components

Build these first, in PR 1, on a hidden `/dev/components` page:

1. Sidebar with groups, active item and collapse (232 px / 72 px)
2. Header: title, PAPER badge, session pill, equity, today, regime chip, Kill switch button
3. Card with uppercase label and optional right-side link
4. Stat card (label, value, sub line, optional bar)
5. Table with mono numeric columns and horizontal scroll on narrow screens
6. Chip (neutral, positive, negative, warning, info, accent)
7. Action badge, small and large: ENTER, SKIP, HOLD, TIGHTEN STOP, EXIT, NO CALL
8. Check row (pass, fail, adjusted) used for rules, code checks and risk gates
9. Banner (negative, warning, info) with actions
10. Dialog with confirm and cancel
11. Buttons: default, primary, danger, ghost; segmented control; toggle switch
12. Number field with unit and help text
13. Price chart: 1h candles, EMA 20, EMA 50, session VWAP, entry, stop and target lines, session separators (Lightweight Charts)
14. Equity line chart with benchmark line

## Rules

- **Numbers:** IBM Plex Mono, right-aligned in tables. Money in USD with the EUR figure beside it in muted text, using the ECB daily rate.
- **Times:** Amsterdam time, with New York time in a tooltip. Never compute the offset by hand; the US and EU change clocks on different dates.
- **Colour means something:** green is a gain or a pass, red a loss, a stop or a failure, amber a warning or a skip, blue a stop raised or an info note. Never use colour alone; there is always a word or icon.
- **Controls:** buttons, nav items and clickable rows are at least 44 px high.
- **PAPER** shows in the header on every screen, always.
- **Sample data:** every number in the designs is sample data. Labels that say "Sample data" in the designs are for the designs only.

## Phone width

`Main-390.png` shows the dashboard at 390 px. Cards stack and wide tables scroll inside their card. The sidebar stacks above the content, as in Trading Agent; turning it into a menu button on phones is an acceptable improvement in PR 6.

## Answered questions

Both questions from the first handoff are now settled in `docs/SPEC.md` (supervised approvals; Stop & flatten never refuses, it warns). Kept for reference:

1. **Stop & flatten on a position bought today** counts as a day trade. The dialog warns about it. Should it also refuse when that would be the 4th day trade?

## Screenshot check in every UI PR

1. Add a Playwright test that opens each screen the PR touched with the sample data and takes screenshots at 1440 × 900 and 390 × 844.
2. Save them in `docs/screenshots/pr-<n>/`, never in `design/`, and show each one in the PR description next to the matching reference PNG from `design/screenshots/`.
3. List deliberate differences under "Design differences".

## When the design changes

The files in `screens/` and `screenshots/` are updated together and `CHANGES.md` gets an entry. Treat each entry as a small task and mention it in the PR.
