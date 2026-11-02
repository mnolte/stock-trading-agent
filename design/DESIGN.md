# Stock Trading Agent — design

How Stock Trading Agent looks and what it says. It uses the same visual language as Trading Agent: dark "instrument panel" surfaces, IBM Plex Sans for text and IBM Plex Mono for every number.

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
| `Main.dc.html` | Dashboard: header, this-cycle line with cycle counter, summary tiles (positions, model calls today and since start link on), daily briefing and risk budget cards, chart with held-symbol buttons and Find symbol (no row of every symbol), price chart, latest decision, watchlist, open positions, today's decisions | PR 1 (header, sidebar), PR 3 (watchlist rules column), PR 6 (rest), PR 10 (risk budget card, cycle counter, briefing card, model call counter) |
| `Decision.dc.html` | Decision detail drawer, 560 px, in Trading Agent's layout (gates, signal stack, reasoning, levels, outcome), opened from the dashboard, positions and inspector | PR 4 (model part), PR 6 |
| `DecisionV2.dc.html` | Decision drawer for a decision.v2 decision (here shadow C): action with option and confidence, entry/stop/target/R-R tiles, Options offered (A–E, chosen one highlighted, stop and target basis, ATR distance, levels in the way), Levels list with the price line and used ids highlighted, reasoning with levels used, explanation check, gates. A v1 decision keeps `Decision.dc.html` | PR 21 |
| `DecisionV3.dc.html` | Decision drawer for a decision.v3 decision (here shadow D on claude-haiku-5-5): Checks by code (trend 1h, trend daily, regime, room to the first level, earnings, news), Options offered with levels in the way and their ATR distance and a Break-even column, conflicts listed before the reasoning, data flags, the room and break-even gates, and how a no_options room reason and a data flag look. `DecisionV3.png` | PR 30 |
| `DecisionV3Held.dc.html` | Decision drawer for a held position under decision.v3 (SBUX, shadow D): action with confidence (target before the current stop), Position block (R now, stop and target with their ATR distance, levels to the target, invalidation filled by code, scheduled exit), the entry thesis marked as belief, Stop options with distance below the price and locked R, options not offered with their reason, conflicts, reasoning, and the checks including the lock-in claim check. `DecisionV3Held.png` | PR 30 |
| `DecisionV4.dc.html` | Decision drawer for a decision.v4 candidate (TSLA, shadow E): the model's enter turned into a skip by the flip-flop guard; Previous decision (action, best option, confidence, conflicts, what moved since); checks compared with the previous cycle; options with their stop group; each conflict with what outweighs it; the model's "what changed" and the guard's finding; news marked about the symbol or not, with the effect of each cited headline; the v4 gates (confidence sanity, flip-flop). `DecisionV4.png` | PR 34 |
| `DecisionV4Held.dc.html` | Decision drawer for a held position under decision.v4 (PG, shadow E, exit): "Does the entry thesis still hold?" with the checks at entry against now and `thesis_holds`; Position with neutral probability, time held and session time left; conflicts with not outweighed; what changed; confidence sanity against the neutral probability. `DecisionV4Held.png` | PR 34 |
| `AskDrawer.dc.html` | Ask the agent docked next to Positions (440 px): header with thread title, earlier chats, New and Close; the context chip; a question, its lookup steps, an answer with a small table, reference chips and the footer (model, lookups, cost, data as of, Copy, model calls); a follow-up streaming with its live steps, the caret and Stop. `AskDrawer.png` | PR 36 |
| `AskStates.dc.html` | Ask the agent states: first open on a decision with three suggestions; earlier chats with search; problems (a number not found in the data, a failed answer with Retry, a stopped answer, the monthly cap replacing the composer); phone at 390 px. `AskStates.png` (1680 px) | PR 36 |
| `Positions.dc.html` | Positions & orders: reconciliation status, positions with their bracket legs, today (entries, exits, same-session round trips), account, order history | PR 5 (data), PR 6 |
| `Performance.dc.html` | Performance: summary, equity vs SPY with USD/EUR switch, by regime, by symbol, winners and tails (small-sample banner, R distribution, tail tiles, after the exit with per-trade table), decisions and outcomes, closed trades | PR 7, tails in PR 19 |
| `Inspector.dc.html` | LLM inspector: every call since the start. Filters (period presets and from/to, outcome, source, more filters behind a button on phone), filtered-calls summary with a stacked bar per session, call list grouped per session (sticky headers, outcome chips with reasons, Load more), details with outcome block, metadata, Error block for failed calls, prompt and raw response. `Inspector-error.png` shows a timeout; `Inspector-390.png` phone width | PR 4, rebuilt in PR 18 |
| `Universe.dc.html` | Universe (v0.2): mode banner, counts, today's screen as a funnel, how the screen works, pool table with filters, search, rank, dollar volume, return vs SPY, status, add, CSV import and remove | PR 11 |
| `Strategies.dc.html` | Strategies (v0.2): shadow cost and slots, comparison table (live, shadows, rules only), small-sample warning, equity curves with SPY, selected strategy with Pause, Archive, Promote, disagreements list | PR 13 |
| `Backtest.dc.html` | Backtest (v0.2): new-backtest form with quick ranges, strategy picker, estimate and model-cutoff check; runs list with progress and cost; results tiles per strategy, equity curves with SPY, by regime, Read with care | PR 14 |
| `Readiness.dc.html` | Readiness: verdict banner, summary tiles (17 criteria), Evidence / Model / Operations criteria (Operations includes Errors in the log and Critical lines in the log, linked to Logs) with value, target, progress bar and status (pass, not yet, waiting for data), manual items, history | PR 16, log criteria in PR 20 |
| `Settings.dc.html` | Settings: section list with filter; API keys (masked, Test, Replace, inline "Test and save" form) and data sources; trading; risk limits in three groups; entry rules; earnings; model and reviews; notifications (event × browser/email, SMTP, repeat limit, quiet hours, recently sent); schedule; history; Access (sign-in, password, authenticator, idle sign-out, devices); Server (version, address, restarts, clock, disk, updates, backups, monitoring, instance id); paper reset. One row per setting with its default, changed rows highlighted with Reset, sticky unsaved-changes bar with reason. `Settings-390.png` shows phone width | PR 6, redesigned in PR 10 |
| `Reviews.dc.html` | Reviews: weekly review with stats, closed trades, skipped setups after 5 sessions, observations, suggestion (not applied), verdicts to confirm; Briefings tab | PR 9 |
| `Insights.dc.html` | Insights (Learn group): strategy and period toggles; headline tiles (expectancy with range bar, edge over SPY at the same exposure, beta, overnight vs in session, gaps through the stop, cost per trade); Findings (finding and early-signal cards with actions); Where trades come from (log-scale funnel with losses per step); Skipped setups; Slices with a dimension picker and a table with range bars and sample status; Calibration; Execution; Overnight and in session. Sample data is a fuller run (18 Dec, 112 trades) so every state shows | PR 22 |
| `Login.dc.html` | Sign in, three states side by side: password with "Keep me signed in", authenticator code with recovery-code link, locked out. No sidebar or data before signing in. The sidebar on every screen gains Sign out above the status | PR 23 |
| `Errors.dc.html` | Error pages: in the app (not found for a record, not found for a page, something went wrong with error id, API not answering on first load) inside a simplified shell, and static pages served by Caddy (502/504 app down, 503 updating) without sidebar or data. Texts are final | PR 25 |
| `Logs.dc.html` | Logs (Control group): level toggles with counts (warning and up on by default) and a "Show debug and info" switch, period, search, component, symbol, cycle, Download .jsonl; warnings and errors per day; recurring problems with a review state and Review button for errors; log grouped per day with level chips and context chips; an opened error line with traceback, stored line and surrounding lines; Follow new lines | PR 20 |
| `States.dc.html` | Session states, all banners, market-closed dashboard, pending approval, shutdown and restart (shutting down pill and banner, unclean previous run, terminal output), access and server (session expired, restarted resumed or paused, another instance, offsite backup failed, new sign-in toast), and 7 dialogs (including Review a recurring error and Confirm it is you) | PR 5 (kill switch), PR 6, PR 8 (error states), PR 20 (shutdown), PR 23 and 24 (access and server) |

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

## Interaction rules

These apply to every screen. Inline styles can't express hover or focus, so the shared classes live once in the app's global CSS (see the end of `tokens.css`).

- **Focus:** every interactive element shows `:focus-visible` (2 px accent ring, 2 px offset). Never set `outline: none` without this replacement.
- **Hover:** clickable rows use `.sa-row`, linked tiles and cards `.sa-tile`, buttons brighten slightly. Anything clickable gives hover feedback; nothing that isn't clickable does.
- **Size:** buttons, nav items, clickable rows and feed rows are at least 44 px high (`--sa-control-height`), at phone width too.
- **Card states:** every card that fetches data has three states besides showing data: *loading* (skeleton, `aria-busy`, never the empty text), *empty* (only after a successful load; says why and what comes next) and *error* (what failed and when, a Retry, and the last data kept visible and marked stale). When two or more cards fail or the API doesn't answer, a page banner says so and retries every 15 s. See `States.dc.html` → Card states.
- **Live regions:** only changes of state are announced. Countdowns and clocks are never inside `role="status"` or `aria-live`. Critical banners and toasts use `role="alert"`.
- **Toasts:** one per notification event (not per channel), stacked below the header, newest on top, at most 3. Info and warning close after 10 s (hover pauses); critical toasts stay until dismissed.
- **ARIA patterns:** use a pattern completely or not at all. Filters are toggle buttons (`aria-pressed`), not tabs. Real tabs (that switch panels, like Reviews) support arrow keys. Lists of clickable rows are buttons in a list, not `role="row"` on a button.
- **Dialogs and drawers:** focus moves in, stays in, Escape closes, and focus returns to what opened it. The phone sidebar overlay follows the same rule.
- **Times:** every time shown, including feed rows and chips, has the New York time in a tooltip.
- **Numbers:** the frontend formats numbers from numeric API values, so every negative uses the minus sign (−, U+2212). The backend never sends formatted strings.
- **Copy that names a setting** (for example "3 trading days") reads the value from settings; it is never hard-coded.
- **Latest request wins:** when a selection changes quickly (symbol, filter, run), only the newest response is shown; older ones are cancelled or ignored.
- **Page title:** the browser tab shows the state, for example "● Market open · Stock Trading Agent", "⏸ Paused · Stock Trading Agent", "⛔ Kill switch · Stock Trading Agent".

## Phone width

The `-390.png` screenshots show each screen at 390 px. Below 820 px the sidebar is replaced by a sticky top bar: the app mark, the screen title, the Needs you count and a menu button that opens the sidebar as an overlay (focus rules as for dialogs). The header's Needs you chip is hidden there, since the bar carries the count. Cards stack, wide tables scroll inside their card, and no screen scrolls sideways.

## Answered questions

Both questions from the first handoff are now settled in `docs/SPEC.md` (supervised approvals; Stop & flatten never refuses, it warns). Kept for reference:

1. **Stop & flatten on a position bought today** counted as a day trade. No longer relevant: the pattern day trader rule was retired on 4 June 2026 and the warning was removed (PR 26).

## Screenshot check in every UI PR

1. Add a Playwright test that opens each screen the PR touched with the sample data and takes screenshots at 1440 × 900 and 390 × 844.
2. Save them in `docs/screenshots/pr-<n>/`, never in `design/`, and show each one in the PR description next to the matching reference PNG from `design/screenshots/`.
3. List deliberate differences under "Design differences".

## When the design changes

The files in `screens/` and `screenshots/` are updated together and `CHANGES.md` gets an entry. Treat each entry as a small task and mention it in the PR.
