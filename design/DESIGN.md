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
| `screens/` | One HTML file per screen (25): the exact source for styles, layout and wording. |
| `screenshots/` | One PNG per screen at 1440 px (drawers at 560 px, `AskStates` at 2000 px, `States` is a reference sheet), a `-390.png` at phone width for every app screen and for `Decision` and `DecisionV5`, and a `-1024.png` for Main, Positions and Settings. |
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
| `DecisionV5.dc.html` | Decision drawer for a decision.v5 candidate (HD, shadow F, 4 Nov 17:46): the trend gate as the first gate row, options with target distance in ATR, per-option `min_confidence` and `base_rate · n`, the ev gate row, the time-left row, a previous decision in the same context. `DecisionV5.png`, `DecisionV5-390.png` | 045 |
| `DecisionV5Held.dc.html` | Decision drawer for a held position under decision.v5, from the KO case of 3 Nov (last cycle, −0.62R): a Hold or exit block (exit below neutral − 0.03, hold overnight from neutral + 0.10, the model's number) with the expected value in a note; position rows for price since the signal bar, session left and last cycle, overnight gap p90; the fifth thesis check (price vs EMA 20); conflicts not outweighed; exit consistency in the checks. `DecisionV5Held.png` | PR 37 |
| `AskDrawer.dc.html` | Ask the agent docked next to Positions (440 px): header with thread title, earlier chats, New and Close; the context chip; a question, its lookup steps, an answer with a small table, reference chips and the footer (model, lookups, cost, data as of, Copy, model calls); a follow-up streaming with its live steps, the caret and Stop. `AskDrawer.png` | PR 36 |
| `AskStates.dc.html` | Ask the agent states: first open on a decision with three suggestions; earlier chats with search; problems (a number not found in the data, a failed answer with Retry, a stopped answer, the monthly cap replacing the composer); notes; phone at 390 px with an answer that uses a note. `AskStates.png` (2000 px) | PR 36 |
| `Positions.dc.html` | Positions & orders: reconciliation status, positions with their bracket legs, today (entries, exits, same-session round trips), account, order history | PR 5 (data), PR 6 |
| `Performance.dc.html` | Performance: summary, equity vs SPY with USD/EUR switch, by regime, by symbol, winners and tails (small-sample banner, R distribution, tail tiles, after the exit with per-trade table), decisions and outcomes, closed trades | PR 7, tails in PR 19 |
| `Inspector.dc.html` | LLM inspector: every call since the start. Filters (period presets and from/to, outcome, source, more filters behind a button on phone), filtered-calls summary with a stacked bar per session, call list grouped per session (sticky headers, outcome chips with reasons, Load more), details with outcome block, metadata, Error block for failed calls, prompt and raw response. `Inspector-error.png` shows a timeout; `Inspector-390.png` phone width | PR 4, rebuilt in PR 18 |
| `Universe.dc.html` | Universe (v0.2): mode banner, counts, today's screen as a funnel, how the screen works, pool table with filters, search, rank, dollar volume, return vs SPY, status, add, CSV import and remove | PR 11 |
| `Strategies.dc.html` | Strategies (v0.2): shadow cost and slots, comparison table (live, shadows, rules only), small-sample warning, equity curves with SPY, selected strategy with Pause, Archive, Promote, disagreements list | PR 13 |
| `Backtest.dc.html` | Backtest (v0.2): new-backtest form with quick ranges, strategy picker, estimate and model-cutoff check; runs list with progress and cost; results as one table (Metric · each strategy · SPY buy and hold, 056), equity curves with SPY, by regime, Read with care | PR 14 |
| `Readiness.dc.html` | Readiness: verdict banner, summary tiles (17 criteria), Evidence / Model / Operations criteria (Operations includes Errors in the log and Critical lines in the log, linked to Logs) with value, target, progress bar and status (pass, not yet, waiting for data), manual items, history | PR 16, log criteria in PR 20 |
| `Settings.dc.html` | Settings, **one section at a time** (056): the section list (a Section select on phones) picks it, its address is the fragment (`#risk`, `#notify` …; opened without one, the file shows Risk limits), Previous/next links end each section; search and Changed from default show matches from every section. 069: the section list in five groups (Trading, Model, Alerts and display, Connections, Records); defaults inline at the end of each explanation; fixed values as "Always on" text with a lock; "= $40.74" beside every % of equity; Risk limits opens with "What these limits mean today"; Advanced fold for prices and timeouts; override notes ("Shadow A uses 45–65"); tighter/looser tags in the save bar. Sections: section list with filter; API keys (masked, Test, Replace, inline "Test and save" form) and data sources; trading; risk limits in three groups; entry rules; earnings; model and reviews; notifications (event × browser/email, SMTP, repeat limit, quiet hours, recently sent); schedule; history; Access (sign-in, password, authenticator, idle sign-out, devices); Server (version, address, restarts, clock, disk, updates, backups, monitoring, instance id); paper reset. One row per setting with its default, changed rows highlighted with Reset, sticky unsaved-changes bar with reason. `Settings-390.png` shows phone width | PR 6, redesigned in PR 10 |
| `Reviews.dc.html` | Reviews: weekly review with stats, closed trades, skipped setups after 5 sessions, observations, suggestion (not applied), verdicts to confirm; Briefings tab | PR 9 |
| `Insights.dc.html` | Insights (Learn group): strategy and period toggles; headline tiles (expectancy with range bar, edge over SPY at the same exposure, beta, overnight vs in session, gaps through the stop, cost per trade); Findings (finding and early-signal cards with actions); Where trades come from (log-scale funnel with losses per step); Skipped setups; Slices with a dimension picker and a table with range bars and sample status; Calibration; Execution; Overnight and in session. Sample data is a fuller run (18 Dec, 112 trades) so every state shows | PR 22 |
| `Login.dc.html` | Sign in, three states side by side: password with "Keep me signed in", authenticator code with recovery-code link, locked out. No sidebar or data before signing in. The sidebar on every screen gains Sign out above the status | PR 23 |
| `Errors.dc.html` | Error pages: in the app (not found for a record, not found for a page, something went wrong with error id, API not answering on first load) inside a simplified shell, and static pages served by Caddy (502/504 app down, 503 updating) without sidebar or data. Texts are final | PR 25 |
| `Logs.dc.html` | Logs (Control group): level toggles with counts (warning and up on by default) and a "Show debug and info" switch, period, search, component, symbol, cycle, Download .jsonl; warnings and errors per day; recurring problems with a review state and Review button for errors; log grouped per day with level chips and context chips; an opened error line with traceback, stored line and surrounding lines; Follow new lines | PR 20 |
| `Loading.dc.html` | Loading reference: the timing every card follows (frame at once, skeleton after 200 ms, data fades in, "Still loading" at 8 s, error at 20 s) with a live replay; skeleton shapes for tiles, text, tables, lists, charts and chips; refreshing data already shown; buttons while an action runs (idle, working, after 10 s, no answer), the kill switch exception; progress bars, the unknown-length bar and Ask's live step; the new-row tint; what reduced motion changes. `Loading.png`, `Loading-390.png` | 055 |
| `MainLoading.dc.html` | The dashboard in its first load, about 600 ms after opening: real titles, links and column headers, skeletons at final size, Pause waiting for the agent's state, the kill switch live. `MainLoading.png`, `MainLoading-390.png` | 055 |
| `States.dc.html` | Session states, all banners, market-closed dashboard, pending approval, shutdown and restart (shutting down pill and banner, unclean previous run, terminal output), access and server (session expired, restarted resumed or paused, another instance, offsite backup failed, new sign-in toast), and 7 dialogs (including Review a recurring error and Confirm it is you) | PR 5 (kill switch), PR 6, PR 8 (error states), PR 20 (shutdown), PR 23 and 24 (access and server) |

## Shared components

Build these first, in PR 1, on a hidden `/dev/components` page:

1. Sidebar with groups, active item and collapse (232 px / 72 px)
2. Header, two fixed rows: title, PAPER badge and subtitle; session pill, Needs you chip, equity, today, regime chip with the VIX close on its label line ("Regime · VIX 17.4", details on hover; 060), Pause/Resume, Kill switch. Below 820 px the title row is hidden and the stats are one mono line (see `Main-390.png`)
2a. Sidebar Ask button (accent, under the app mark; icon only when collapsed) and the phone bar's Ask icon; both open the Ask drawer, as does Ctrl/⌘+J
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

- **Numbers:** IBM Plex Mono, right-aligned in tables. Money in USD. The EUR figure (ECB daily rate) sits beside equity, today's P&L and card totals in muted text; every other amount (rows, tables, drawers) is USD alone with EUR in its tooltip (spec, Currency; decided 7 Nov).
- **Times:** Amsterdam time, with New York time in a tooltip. Never compute the offset by hand; the US and EU change clocks on different dates.
- **Dates** (056): "14 Sep" within the current year, "14 Sep 2025" otherwise; ranges "14–25 Sep 2026" or "28 Sep – 2 Oct"; times "19:00", with seconds only in Logs. Month names are fixed English three-letter abbreviations, never the browser's locale ("Sept", "sep."). ISO dates (`2026-10-14`) only in Logs, exports and addresses. Date fields show the same format and default from the agent's clock, not the computer's.
- **Words, not codes** (056): every stored code shown on a screen has one label, kept in one map: exit reasons (`agent_exit` → "agent exit"), actions, sources, call kinds, change and warning reasons. A code appears raw only in the inspector's raw response, Logs, exports and tooltips. A label is never printed twice ("Verdict verdict").
- **Tile rows** (056): a grid, never a wrapping row: 6 tiles per row from 1181 px, 3 from 821 px, 2 on a phone (`.sa-tiles`, `.sa-tiles-6`); a row of four uses `.sa-tiles-4` (4 from 821 px, 2 on a phone; 073). A lone tile never stretches across a row. A value too long for its tile wraps; it is never cut off.
- **Colour means something:** green is a gain or a pass, red a loss, a stop or a failure, amber a warning or a skip, blue a stop raised or an info note. Never use colour alone; there is always a word or icon.
- **Controls:** buttons, nav items and clickable rows are at least 44 px high.
- **PAPER** shows in the header on every screen, always.
- **Sample data:** every number in the designs is sample data, including every "finding" on Insights and every note in Ask. Labels that say "Sample data" in the designs are for the designs only. Never quote a design number as a result of the run.
- **Design notes:** a muted paragraph under a table or card that explains how to read it (for example the bar legend under Open positions) is help text. Build it as the design shows it the first time, but it may move behind an info icon or a disclosure; it is never required copy.
- **Decision drawers** share one order, whatever the prompt version: action · tiles · What happened · the version's own blocks · Agent reasoning · Safety gates · Levels (folded) · Outcome · footer links. What happened holds the final action, every change by code with its reason, warnings, the order status, the verdict (with Confirm draft and Change… when a draft exists) and "Ask about this decision".

- **Cards take their own height** (073). A row of cards aligns them at the top; no card is stretched to its neighbour's height or ends in more than 24 px of empty space.
- **Inside a card, groups have labels** (073). Small uppercase labels (11 px, muted, `group_label` in the generator) name each group of chips or rows (Why, Context, Signals, Key facts). 16 px between groups, 8 px inside one; chips of two groups never share a row, and no sentence sits between chips.
- **Plain words next to numbers** (075, 076, 079). A headline number gets a second line saying what it means for someone new to trading ("On average a trade earned 0.21 times what it risked"), next to its definition on hover. A screen that answers a question starts with a sentence written by code, not a model.
- **Glossary terms** (079). In model-written stories, a term from the fixed glossary is a button with a dotted underline; its definition shows on hover and on tap. First occurrence per paragraph only.
- **Explain warnings** (074). A warning or error says what it means, why it happened here, and what to do, with links; the text comes from one table in code.
- **The answer comes second** (073–077). In a table the first column names the row and the second holds the number it is read for, so both show at 390 px; the rest scrolls inside the card.
- **Label–value grids stack on a phone** (`.sa-kv2`): below 760 px the label sits above its text. Buttons wrap their label (`.sa-wrap-phone`); nothing sticks out of its card.
- **On this page** (`on_this_page` in the generator): a page taller than about 4 screens gets a row of chips that jump to its sections; on the dashboard and Performance only on a phone (`.sa-phone-only`).
- **Does it need me?** (074). Next to every explained warning or error: one chip, "Nothing to do" (green), "Keep an eye on it" (amber) or "Needs you" (red), decided by code.
- **Quiet when fine** (077). A status card that only reports health (Data sync) is one line while everything is fine and opens by itself when something failed or is running.

## Interaction rules

These apply to every screen. Inline styles can't express hover or focus, so the shared classes live once in the app's global CSS (see the end of `tokens.css`).

- **Focus:** every interactive element shows `:focus-visible` (2 px accent ring, 2 px offset). Never set `outline: none` without this replacement.
- **Hover:** clickable rows use `.sa-row`, linked tiles and cards `.sa-tile`, buttons brighten slightly. Anything clickable gives hover feedback; nothing that isn't clickable does.
- **Size:** buttons, nav items, clickable rows and feed rows are at least 44 px high (`--sa-control-height`), at phone width too.
- **Card states:** every card that fetches data has three states besides showing data, plus *failed to draw* when the card itself crashes: *loading* (skeleton, `aria-busy`, never the empty text; see Loading below), *empty* (only after a successful load; says why and what comes next) and *error* (what failed and when, a Retry, and the last data kept visible and marked stale). When two or more cards fail or the API doesn't answer, a page banner says so and retries every 15 s. See `States.dc.html` → Card states.
- **Live regions:** only changes of state are announced. Countdowns and clocks are never inside `role="status"` or `aria-live`. Critical banners and toasts use `role="alert"`.
- **Toasts:** one per notification event (not per channel), stacked below the header, newest on top, at most 3. Info and warning close after 10 s (hover pauses); critical toasts stay until dismissed. A toast never covers the header: Pause, Resume and the kill switch stay clickable while toasts show (056).
- **Error text** (056): wherever an error is shown outside Logs (Needs you, toasts, banners, card errors), it is its group's summary: the first line of the message without URLs or file paths, at most 120 characters, with a count ("Reviews screen failed to draw ×5 · last 07:30"). Stack traces only in Logs, behind the opened line.
- **Failed to draw** (056): every card has its own error boundary. A card that crashes shows "This card couldn't be drawn. The agent isn't affected." with the error id, Reload card and Open in Logs; the rest of the screen keeps working (States → Card states). The screen-level "Something went wrong" is for a crash outside any card.
- **Card height** (056): cards in a row share the row's height only from 821 px. Below that every card is as tall as its content.
- **ARIA patterns:** use a pattern completely or not at all. Filters are toggle buttons (`aria-pressed`), not tabs. Real tabs (that switch panels, like Reviews) support arrow keys. Lists of clickable rows are buttons in a list, not `role="row"` on a button.
- **Dialogs and drawers:** focus moves in, stays in, Escape closes, and focus returns to what opened it. The phone sidebar overlay follows the same rule.
- **Times:** every time shown, including feed rows and chips, has the New York time in a tooltip.
- **Numbers:** the frontend formats numbers from numeric API values, so every negative uses the minus sign (−, U+2212). The backend never sends formatted strings.
- **Copy that names a setting** (for example "3 trading days") reads the value from settings; it is never hard-coded.
- **Latest request wins:** when a selection changes quickly (symbol, filter, run), only the newest response is shown; older ones are cancelled or ignored.
- **Page title:** the browser tab shows the state, for example "● Market open · Stock Trading Agent", "⏸ Paused · Stock Trading Agent", "⛔ Kill switch · Stock Trading Agent".

## Loading

`Loading.dc.html` shows every pattern; `MainLoading.dc.html` the dashboard in its first load. The numbers are tokens in `tokens.css`.

- **No loading screen.** The shell (sidebar, phone bar, header frame, card frames and titles) renders at once; each card loads on its own. There is no full-page spinner, and no spinner on top of a card.
- **Timing, every card:** nothing for the first 200 ms; then the skeleton fades in. Once shown, a skeleton stays at least 400 ms, so it never flickers. Data then fades in over 200 ms in the skeleton's place. After 8 s a muted "Still loading · 8 s" line appears under the skeleton; at 20 s the request is cancelled and the card shows its error state with Retry.
- **Skeletons are the layout of the data:** the same height as the filled card, so nothing below moves when data arrives. Titles, links, column headers and labels are real text; only values are bars. Tiles: label, value and sub-line bars. Tables: 4–8 rows of 44 px with bar widths that vary per row. Lists and feeds: a badge-sized bar and two lines per row. Charts: real grid lines, one faint plot block, axis bars, never fake candles. A skeleton never shows a number, a 0 or a dash.
- **Shimmer:** one style for every skeleton, a light band sweeping left to right every 1.6 s.
- **Header on first load:** the session pill and equity, today and regime are bars; the Needs you chip and the phone-bar count stay hidden until the list is known; Pause is `aria-disabled` until the agent's state is known (it could be Resume). The kill switch works from the first frame.
- **Refreshing data already shown** (filter, period, symbol changed): never a skeleton. Rows stay, dimmed to 55 %, and after 400 ms a 2 px accent bar runs along the card's top edge. Controls stay usable; the newest request wins. Values pushed over the WebSocket just change, with no bar or fade. A failed refresh restores the rows and shows the card's error line, marked stale.
- **Buttons while an action runs:** a 14 px spinner before a present-participle label ("Saving…", "Testing…", "Confirming…", "Resuming…"), the idle width kept, `aria-busy="true"` and `aria-disabled="true"` (not `disabled`, so focus stays and a second press does nothing). The rest of the screen stays usable. After 10 s a muted line says what it waits for ("Alpaca is slow · 12 s"). At 20 s it says "no answer", never "failed" unless the backend said so, and the screen updates if the action lands later. Unsaved input is kept.
- **Kill switch:** never `aria-disabled`, dimmed or greyed. Pressed, it shows "Turning on…" with the spinner in full red; with no answer it is sent again every 5 s until the backend confirms (turning it on twice is harmless; Stop & flatten resends carry one request id, so the backend acts once). At 30 s without an answer a critical banner (`role="alert"`) says it is not confirmed, that the agent may still place entries, to keep the tab open, and how to stop it another way (stop the app on the server, or cancel open orders in Alpaca's paper dashboard). Resending continues under the banner; it clears when the backend confirms, with the toast "Kill switch on since 23:41". Decided 8 Nov.
- **Measurable work** (backtest runs, history downloads): a filled 6 px bar with the count and percentage in text, `role="progressbar"` with `aria-valuenow`; the width eases over 200 ms. Unknown length (the liquidity screen): the sliding bar. Ask's live lookup step gets the spinner, finished steps a tick.
- **Arriving live:** a row that is new since the screen loaded (feed, order history, Logs with Follow) gets the accent background, fading out over 1.6 s. Never on the first load.
- **Screen readers:** a loading card has `aria-busy="true"` and one visually hidden "Loading <card title>"; skeleton bars are `aria-hidden`. Loading is never announced in a live region; results are, as the Live regions rule says.
- **Reduced motion** (`prefers-reduced-motion: reduce`): skeletons are flat, nothing fades, the spinner is hidden (the label carries the state), the refresh bar is a static 35 % line, progress widths jump, the new-row tint goes after 1.6 s without a fade. Every delay and timeout stays the same.

## Phone width

The `-390.png` screenshots show each screen at 390 px; `Main-1024.png`, `Positions-1024.png` and `Settings-1024.png` show the layout a laptop with the Ask drawer docked, or a tablet, gets. Decision drawers shrink to the viewport (`max-width: 560px`) and their four tiles become two columns. Below 820 px the sidebar is replaced by a sticky top bar: the app mark, the screen title, the Needs you count and a menu button that opens the sidebar as an overlay (focus rules as for dialogs). The header's Needs you chip is hidden there, since the bar carries the count. Cards stack, wide tables scroll inside their card, and no screen scrolls sideways.

## Answered questions

Both questions from the first handoff are now settled in `docs/SPEC.md` (supervised approvals; Stop & flatten never refuses, it warns). Kept for reference:

1. **Stop & flatten on a position bought today** counted as a day trade. No longer relevant: the pattern day trader rule was retired on 4 June 2026 and the warning was removed (PR 26).

## Screenshot check in every UI PR

1. Add a Playwright test that opens each screen the PR touched with the sample data and takes screenshots at 1440 × 900 and 390 × 844 (and 1024 × 768 for Main, Positions and Settings).
2. Save them in `docs/screenshots/pr-<n>/`, never in `design/`, and show each one in the PR description next to the matching reference PNG from `design/screenshots/`.
3. List deliberate differences under "Design differences".

## When the design changes

The files in `screens/` and `screenshots/` are updated together and `CHANGES.md` gets an entry. Treat each entry as a small task and mention it in the PR.
