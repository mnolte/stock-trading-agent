# Design changes

Newest first. Each entry names the screens it touches.

## 2026-11-11 — [069] Settings UX

`Settings.dc.html` only. `Settings.png`, `Settings-1024.png`, `Settings-390.png`.

- **Section list** in five groups with small headings: Trading (Trading, Risk limits, Entry rules, Entry selection, Decision gates, Earnings), Model (Model and reviews, · Ask the agent), Alerts and display (Notifications, Display), Connections (API keys and data, Access, Server), Records (Readiness, Schedule, History, Paper account). Previous/next follow this order. Phone: optgroups in the Section select; Find a setting shown above it.
- **Rows:** default inline ("· default 2.5%") instead of a line under the control; fixed values as "Always on" with "fixed by the spec" on the same line (Cash only, One share fits) instead of a dimmed switch.
- **Risk limits:** "What these limits mean today" box (at equity $10,184.30 with the two unsaved changes: $40.74 per stopped trade, $20.37 after 3 losses; 4 × 0.4% = 1.6% of the 2.5% budget; day stops at −$152.76; kill switch at $9,395.41, 8% below the $10,212.40 peak); "= $…" beside each % of equity; shock brake fields labelled "1h bar" and "since the open".
- **Trading:** "Advanced · prices and timeouts" folded (5 settings, all at default).
- **Entry rules:** RSI row notes "Shadow A uses 45–65" (the Strategies sample).
- **Decision gates:** "Regimes that pass the trend gate" (Up, Sideways; 058) and Decision v6 → "Sideways, high volatility: target at most 2.0 × ATR"; the trend gate's explanation updated.
- **Save bar:** each change tagged tighter (green) or looser (amber).
- **Notifications → Email:** "Test and save" primary, "Send test" secondary.

## 2026-11-10 — [060] VIX data

- **Header, every screen:** the VIX close sits on the Regime stat's label line ("REGIME · VIX 17.4"), so the header keeps its width (a separate VIX column pushed Pause and Kill switch to a third row at 1440). Hover: the close's date, 1-year percentile, 5-session change and VIXY since that close and today. Below 820 px the phone line has no room: the regime chip's tooltip carries it.
- **`Main.dc.html` → Latest decision:** a "VIX 17.4 · 42nd pct" chip next to the market regime chip, from the decision's cycle.
- **`MainLoading.dc.html`:** the header's Regime · VIX bar.
- **`Insights.dc.html` → Slices:** two dimensions, VIX level and VIX 5-session change.
- **`Settings.dc.html` → API keys and data → Data sources:** "VIX (FRED)" with the close, its date and the last fetch; FRED added to the data-source-failing notification's list.
- **`States.dc.html`:** VIX stale (amber, "25.6 from Thu 5 Nov") and unavailable ("—").
- Sample dates follow FRED's timing: on Thu 15 Oct at 18:52 Amsterdam the newest close is Tue 13 Oct's, fetched Wed 23:00 Amsterdam (17:00 New York).
- Screenshots re-rendered for every screen with the header: Main (1440, 1024, 390), MainLoading, Positions (1440, 1024, 390), Settings (1440, 1024, 390), Backtest, Insights, Inspector, Loading, Logs, Performance, Readiness, Reviews, Strategies, Universe, AskDrawer (1440, 390), States.

## 2026-11-09 — [056] UX pass on the running app

From the review in `docs/analysis/056-ux-review.md` (the app at 1440 and 390 px, seeded).

- **`Settings.dc.html`: one section at a time.** The section list picks the section; its fragment is its address (`#risk`; the file opens on Risk limits, which has the two unsaved changes). On phones a **Section** select with change counts replaces the scrolling row, whose current item was off-screen. Previous/next links end each section. Search and Changed from default still cover every section (described in `DESIGN.md`). `Settings.png`, `Settings-1024.png`, `Settings-390.png`.
- **`Backtest.dc.html`:** the eight result tiles with two unlabelled numbers each become one table: Metric · Live settings v3 · Rules only · SPY buy and hold. The form's dates read "2 Jan 2024" / "30 Sep 2026". `Backtest.png`, `Backtest-390.png`.
- **`Performance.dc.html`, `Insights.dc.html`:** the six summary tiles are one grid row (6 / 3 / 2 per row) instead of five plus one stretched. `Performance.png`, `Performance-390.png`, `Insights.png`, `Insights-390.png`.
- **`States.dc.html` → Card states:** new *Failed to draw* state (Weekly review, error id, Reload card, Open in Logs). `States.png`.
- **Every screen:** the shared CSS gains `.sa-tiles` and `.sa-tiles-6`; no other change.
- `DESIGN.md`: rules for dates, words instead of codes, tile rows, error text, failed to draw, card height on phones, and toasts never covering the header; the Settings and Backtest rows.

## 2026-11-08 — [055] Loading states

- New **`Loading.dc.html`**: the loading reference. Timing (frame at once, skeleton after 200 ms, at least 400 ms once shown, data fades in over 200 ms, "Still loading" at 8 s, error state at 20 s) with a live replay; skeleton shapes for tiles, text, tables, lists, charts and chips; refreshing data already shown (rows dimmed, 2 px refresh bar); buttons while an action runs (Save, Test key, Confirm draft, Resume: idle, working, after 10 s, no answer) and the kill switch, which is never disabled, resends every 5 s and shows a critical "Kill switch not confirmed after 30 s" banner while it keeps trying (decided 8 Nov); progress bars, the unknown-length bar and Ask's live step; the new-row tint; a reduced-motion table. `Loading.png`, `Loading-390.png`.
- New **`MainLoading.dc.html`**: the dashboard about 600 ms after opening. Real titles and column headers, skeletons at final size, header stats as bars, no Needs you count yet, Pause waiting, kill switch live. `MainLoading.png`, `MainLoading-390.png`.
- **Every screen:** the skeleton animation changes from an opacity pulse (1.4 s) to a shimmer (1.6 s), and the shared CSS gains the loading classes (`sa-loading`, `sa-in`, `sa-spin`, `sa-refresh`, `sa-stale-rows`, `sa-new`, `sa-bar-fill`, `sa-sr`). Only the CSS line changes; no layout changes. Screenshots of other screens unchanged.
- `States.dc.html` → Card states: the loading example drops the visible "Loading…" (now visually hidden) and links to `Loading.dc.html`. Banners: new "Kill switch not confirmed". `States.png` re-rendered.
- `tokens.css`: `--sa-skeleton-shine`, `--sa-ease`, `--sa-fade`, `--sa-skeleton-delay`, `--sa-skeleton-min`, `--sa-refresh-delay`, `--sa-load-slow`, `--sa-load-timeout`, `--sa-new-row`, and the loading classes with their reduced-motion rules.
- `DESIGN.md`: a new **Loading** section with the rules; Card states points to it; both new screens in the screen table.

## 2026-11-07 — [045] GUI decisions

- `Inspector.dc.html`: the reason select groups Warning, changed_by_code (by change reason) and Error, and drops `no_options`, which is a no-call reason and never reaches the inspector. Screenshots unchanged (the select is closed in them).
- `DESIGN.md` → Numbers: EUR beside equity, today's P&L and card totals only; other amounts in USD with EUR in a tooltip.

## 2026-11-07 — [050] Stop invariant

- `States.dc.html`: new "Position without a stop at Alpaca · stop-missing episode" (KO, 7 shares, red chip "No stop at Alpaca since 15:46 · 1 h 30 min", legs line "stop none", the closing line); a first Needs you reference row "KO has no stop at Alpaca since 15:46" that leaves only when a check finds a stop or the position flat; the unclean-restart banner says "Last alive 18:46:35 · down 61 min · missed 3 position checks and the 19:46 cycle". `States.png` re-rendered.

## 2026-11-05 — [045] GUI catch-up

From the design review in `docs/analysis/045-gui-catch-up.md`. Every screen with the app shell is re-rendered at 1440 and 390; Main, Positions and Settings also at 1024.

- **Shell, every screen.**
  - One header layout, two fixed rows (about 110 px): title, PAPER and the subtitle; then the session pill, the Needs you chip, equity, today, regime, Pause and Kill switch. Nothing wraps at 1440; at 1024 the right-hand group drops to a third row.
  - The **Ask button moves from the header to the sidebar** (full-width accent button under the app mark, icon only when collapsed), as the app already did on 5 Nov; the phone bar keeps its Ask icon. Ctrl/⌘+J unchanged.
  - Phone (< 820 px): the title row is hidden (the top bar carries it), the stats collapse to one mono line (equity, today, regime; EUR in the tooltip), Pause and Kill switch share one row. The header is about 150 px instead of 300.
  - Sidebar mark says v0.2. Model names: `gpt-4.1-mini` for live, briefing and verdicts; `claude-sonnet-5-5` for the weekly review and Ask; `gpt-4o-mini` is gone from every screen.
- **Decision drawers, one order for every version** (`drawercommon.py` in the generators): action · tiles · **What happened** · the version's blocks · Agent reasoning · Safety gates · Levels (folded) · Outcome · footer links.
  - **What happened** (new, every drawer): final action, changes by code with their reason code, warnings, order status, the verdict (pending / draft with "Confirm draft" and "Change…" / confirmed / none, with why) and "Ask about this decision".
  - **Outcome** on every drawer (was v1 only): 1h, 1 session, 5 sessions for entries; option touches for a skip; what the position did afterwards and the next open for a hold or exit.
  - Levels fold by default; the summary gives the count and the bar time.
  - `Decision.dc.html` (v1, live): gates now follow the reasoning; the verdict chip moved into What happened.
  - `DecisionV3.dc.html` gains the Selection block the spec gives v3 drawers.
  - `DecisionV4Held.dc.html`: Position before "Does the entry thesis still hold?", as in the v5 held drawer.
  - New **`DecisionV5.dc.html`**: a v5 candidate (HD, shadow F, 4 Nov 17:46): options with target distance in ATR, per-option `min_confidence` and `base_rate · n` (dash under 30 cases), the Model column, the trend gate as the first gate row, the ev gate ("0.45 ≥ 0.42 · EV +0.35R"), the time-left row, and a previous decision in the same context with its final action. `DecisionV5.png`, `DecisionV5-390.png`.
  - Drawers shrink to the viewport (`max-width: 560px`), so the 390 renders show the phone layout: `Decision-390.png`, `DecisionV5-390.png`.
  - Corrects the 3 Nov entry: `DecisionV4.dc.html` never carried the per-option `min_confidence` and `target_atr` columns; they are in `DecisionV5.dc.html`.
- **Dashboard** (`Main.dc.html`).
  - Watchlist in **screened mode**: a Rank column with its hover, counts over the 69 eligible symbols, 15 rows, "Sorted by rank · 15 of 69 eligible shown · held symbols always listed" and "Show all 69". Five more sample symbols (AVGO, HD, COST, LLY, V); HD and LLY show the call cap.
  - Cycle strip and feed follow the pool: "59 of 67 symbols not held failed the pre-filter; 8 passed, 6 reached the model (call cap 10, held first)"; a "NO CALL · call cap" row; "+51 more" on the pre-filter row; "Next · cycle 4 of 6 today".
  - Needs you: an "Ask" link on every item; the health line reads "Email failing since 15:00".
  - Model calls tile hover names the Ask cap ($1.84 of $15).
  - Briefing card: the text, "Numbers checked ✓" (hover explains), the next briefing, and the facts, model and cost behind a "Facts used" disclosure. The explanation paragraph is gone.
  - Last action times carry the New York time in a tooltip.
- **Positions** (`Positions.dc.html`): order history gets "All · Problems" and a period filter, a rejected order row (red) and "Load 20 more"; the card header says the period. `Positions-390.png` and `Positions-1024.png` are new.
- **Reviews** (`Reviews.dc.html`): each verdict has one primary **"Confirm good call / bad call / unclear"** button (the draft), "Change…" and a "Decision →" link; "Confirm all 3 drafts" at the top. The hyphen in "−1.0R" is a minus sign. `Reviews-390.png` is new.
- **Insights** (`Insights.dc.html`): new **"What each filter removed"** card between the funnel and the slices (PR 37): per filter the count removed, target first, stop first, neither, average R if taken, and a sample status. The first finding is now about high volatility; the 10:46 finding that was once read as a real result is gone, and the card says "sample data".
- **LLM inspector** (`Inspector.dc.html`): Source gains **Ask** and Backtests; the prompt list runs to `decision.v5`, `weekly.v2`, `verdict.v2`, `ask.v1`; the reason list has every warning and error reason of the spec; an Ask call in the list and its details ("Question 1 of 4 · context Positions & orders", effort instead of temperature for Claude); the user message is labelled "input snapshot as sent to the model" with a **Stored input** link.
- **Settings** (`Settings.dc.html`).
  - Notifications: all **27 events** of the spec table, folded behind "Event table · 27 events · browser on for 23 · email on for 19".
  - New sections **Entry selection** (mode, confidence weight, last-slot minimum), **Decision gates** (v3: room, edge, near level; v5: entry gate, min EV, max R/R, trend gate, base-rate window, price vs signal) and **Display** (near-stop distance, slow-call threshold); Trading gains "Prices and timeouts" (quote age, deviation, tighten quote age, fill settle, Alpaca read timeout).
  - Decision prompt lists v1–v5 (shadow versions marked); model lists are `gpt-4.1-mini`, `gpt-6.1-sol`, `claude-haiku-5-5`, `claude-sonnet-5-5`; the weekly review defaults to `claude-sonnet-5-5`.
  - Section list: "Changed from default" filter under Find, "· Ask the agent" as a jump link, the new sections.
- **Universe** (`Universe.dc.html`): the company name under each symbol (what `about_symbol` matches; `universe.names` overrides), with a line in "How the screen works".
- **States** (`States.dc.html`): banners for live decisions failing, trade-updates stream degraded, a missing or failing key, keys imported from `.env`, EUR unavailable; a **Needs you reference** with every kind of item (11 rows: wording, button, when it leaves) and the empty card; the "Alpaca not answering · as of" card state; a position with a held-back exit; feed rows for every code reason; the latest-decision card for code-built options.
- **Ask** (`AskStates.dc.html`): the intro names the sidebar button; the sample note and the phone answer follow the new Insights finding.
- `DESIGN.md`: the screens table has 25 rows, the screenshot list is current, the header component lists Needs you, Pause and the kill switch, Ask is a sidebar component, and two rules are added (design notes under cards are help text; the 1024 renders).

## 2026-11-03 — Decision v5 held drawer

- New `DecisionV5Held.dc.html` (PR 37), built on the KO call of 3 Nov at 15:46 NY. It shows:
  - the **Hold or exit** block: the exit bar, the overnight bar, the model's number;
  - position rows: price since the signal bar, session left (last cycle), overnight gap p90 against the stop distance;
  - the fifth thesis check, price against EMA 20;
  - the Exit consistency gate.
- A note on the drawer compares it with the live v4 answer on the same snapshot.
- v5 candidates reuse `DecisionV4.dc.html`, with per-option `min_confidence` and `target_atr` in the options table.

## 2026-11-01 — Ask the agent: notes and settings

- `AskDrawer.dc.html`: "Save as note" in the answer footer, before Copy.
- `AskStates.dc.html` (now 2000 px wide, five panels):
  - Earlier chats has Chats | Notes tabs;
  - a new **Notes** panel ("+ Write a note", three notes with date, source and references, Edit and Delete);
  - the phone panel shows an answer that cites "Your note · 1 Nov" and checks it against today's numbers.
- `Settings.dc.html`: an **Ask the agent** group under Reviews with:
  - on/off;
  - the model: Claude Sonnet 5.5 and GPT-6.1 Sol marked recommended, Haiku 5.5 and GPT-4.1 mini marked budget, with the price and cost per question in the help;
  - the monthly cap ($15, with this month's spend, number of questions and average);
  - max cost per answer ($0.25);
  - keep chats;
  - the Standing instructions textarea.
- `AskDrawer` and `AskStates`: example costs at Sonnet 5.5 prices, and the cap notice at $15.

## 2026-11-01 — Ask the agent drawer

- New `AskDrawer.dc.html` (PR 36). The drawer docked to the right of Positions mid-conversation. It shows:
  - the context chip ("Positions & orders · 2 open", removable);
  - "Looked up 2 things" steps;
  - an answer that opens with the answer, a small table, reference chips (MSFT position, JPM position, Risk budget) and the footer;
  - a follow-up being answered: live steps (● for the running lookup), the streaming caret, and Stop in place of Send.
- New `AskStates.dc.html`:
  - first open on a decision with three suggestions;
  - earlier chats (search, today and earlier, kept 90 days);
  - problems: a number not found in the data (dotted amber underline and a footer line), a failed answer with Retry, a stopped answer, the monthly cap notice in place of the composer;
  - the phone layout.
- Every screen with the shell:
  - an **Ask** button in the header before Pause (accent outline; Ctrl/⌘+J);
  - an Ask button in the phone top bar next to the menu.
- Screenshots re-rendered for those screens at 1440 and 390.

## 2026-11-01 — Needs you, positions at a glance, phone top bar

From the UX audit (`docs/analysis/2026-11-01-ux-audit.md`).

- `Main.dc.html`:
  - **Needs you** card first, with 3 sample items (email alerts failing, 2 errors not reviewed, 1 verdict to confirm), each with one action, and the health line;
  - new order: cycle strip, two tiles (model calls, since start), open positions next to the risk budget, chart and latest decision, today's decisions next to the briefing, watchlist last;
  - open positions as rows with a stop-to-target bar and the distance to the stop in % and ATR (amber under 1.5 ATR);
  - the "Positions" tile is removed; the card title carries "2 of 4".
- `Positions.dc.html`:
  - a totals line;
  - per position: the bar, four facts (if the stop hits, to the stop, to the target, exit planned), the latest decision link, and the Alpaca legs folded into one summary line.
- Every screen with the app shell:
  - a "Needs you · 3" chip in the header after the session pill;
  - below 820 px, the sidebar is replaced by a sticky top bar (mark, title, count, menu button); the header chip is hidden there.
- `Settings.dc.html`: the recovery-codes row wraps at phone width, so the page no longer scrolls sideways at 390 px.
- Screenshots re-rendered at 1440 and 390 for every screen with the shell. `DESIGN.md` → Phone width updated.

## 2026-11-01 — Decision v4 drawers

- New `DecisionV4.dc.html` (PR 34), the decision.v4 candidate drawer. The example is TSLA in shadow E: the model's enter became a skip through the flip-flop guard. The drawer shows:
  - **Previous decision**: action, best option, confidence, conflicts, and what moved since;
  - **Checks by code** compared with the previous cycle;
  - **Options** with their stop group;
  - **Conflicts** each with what outweighs it;
  - the model's **What changed** and the guard's finding (amber);
  - **News** marked about the symbol or not, with the effect of each cited headline;
  - the v4 gate rows: confidence sanity and flip-flop;
  - a note on how a skip shows its best option.
- New `DecisionV4Held.dc.html`, the held drawer. The example is PG at −0.61R, exiting. It shows:
  - **Does the entry thesis still hold?**: checks at entry against now, with `thesis_holds`;
  - time held and minutes left in the session;
  - conflicts marked "not outweighed";
  - confidence sanity against the neutral probability.
- `Settings.dc.html`, Model and reviews → Reviews: a prompt select under each review model: Weekly review prompt, Daily briefing prompt and Verdict prompt. They use the same pattern as the Decision prompt row (`version · short hash`), list only that task's prompts, and show the default under each. The Verdict prompt notes that a change applies to new drafts only. Screenshots `Settings.png` and `Settings-390.png` re-rendered.
- `DESIGN.md`: both screens listed. `DecisionV3*.dc.html` are unchanged and still used for v3 decisions.

## 2026-10-31 — Entry selection

- `DecisionV2.dc.html` (PR 33): a "Selection · expected profit" gate row, and a "Selection in this cycle" block.
  - The block lists each scored entry with its rank, R/R, p used, dollars at risk, expected R and expected profit, and shows it as selected (green) or not selected (amber, with the reason). This decision is marked with the accent bar.
  - A note gives the chosen set's total and what rank order would have taken: XOM + MSFT $41.08 against AMZN + MSFT $27.42.
- The v3 drawers get the same block. No other screen changes; the feed shows `not_selected` like other code blocks.

## 2026-10-30 — Review attribution

- `Reviews.dc.html`, Verdicts to confirm (PR 32): each row shows the model's call and, when code changed it, the final action in amber with the reason ("ENTER B → SKIP by code · reward to risk 1.4, min 1.5"); an Outcome line (option touches, or the fill and R); the draft chip marked "label by code"; and a Thesis check line ("consistent with the facts", or an amber "contradicts the facts" chip with the claim). Confirm buttons use `aria-pressed`.
- `Reviews.dc.html`, Suggestion: until the run has 10 closed trades the box is neutral and says "No suggestion yet: 3 closed trades in this run, 10 needed". An allowed suggestion keeps the accent box, "not applied", plus an evidence line.
- `Reviews.dc.html`, Observations: confirmed verdicts are counted per group (executed model entries, entries blocked by code, model skips) with thesis contradictions.

## 2026-10-29 — Decision v3: momentum, neutral probability, stop basis by code

- `DecisionV3.dc.html`: a Momentum row in Checks by code; the stop and target basis shown as a line "Written by code" under the reasoning (the model's text no longer describes them); a Claim checks gate.
- `DecisionV3Held.dc.html`: a Neutral probability row in the position block (the starting point for held confidence); the claim checks gate now covers named stop or target levels too.

## 2026-10-28 — Decision v3 drawer

- New `DecisionV3.dc.html` (PR 30), the decision.v3 version of the drawer:
  - **Checks by code** block: trend 1h, trend daily, market regime, room to the first level in the way, earnings, news; each a value with the rule behind it;
  - **Options offered** adds each level in the way with its distance above the limit in ATR, and a Break-even column (1 ÷ (1 + R/R));
  - the model's **conflicts** are shown before the reasoning, and **data flags** after it (none, or a red chip such as "Data flag: earnings date conflict");
  - two new gates: room to the first level ≥ 1.0 ATR, and confidence ≥ max(0.55, break-even + 0.10);
  - a note shows how a `no_options` room reason looks when code removes every option.
- New `DecisionV3Held.dc.html`: the v3 drawer for a held position (position facts with distances and the scheduled exit, stop options with locked R and distance below the price, options not offered with their reason, the lock-in claim check).
- `DecisionV2.dc.html` is unchanged and still used for v2 decisions.

## 2026-10-28 — Model providers (OpenAI and Anthropic)

- `Settings.dc.html`, API keys: a "Model keys" line (at least one of OpenAI and Anthropic is required), then "OpenAI (optional)" and the new "Anthropic (optional)" rows, each saying what uses it, with Test, Replace and Remove.
- `Settings.dc.html`, Decisions: the model picker lists models as "id · provider" (models whose key is missing can't be chosen); the temperature row says "OpenAI models only". Reviews: three pickers, "Weekly review model", "Daily briefing model" and "Verdict model".
- `Settings.dc.html`, Notifications: "Data source or API key failing" names Anthropic too.
- `Inspector.dc.html`: the model filter lists every model with its provider.
- `Logs.dc.html`: component filter adds `model.anthropic`.
- The new-shadow dialog and the backtest form use the same "id · provider" model list (no new screen).

## 2026-10-27 — Signed-in devices

- `Settings.dc.html`, Access → Signed-in devices: the masked address only, without a location; the app does no location lookup.

## 2026-10-27 — Security hardening

- `Settings.dc.html`, Notifications → Email (SMTP): the note says the stored password is only sent to the server it was saved for, and that changing the host, port or username asks for the password again (the field is then required).
- `Settings.dc.html`, Access → "Confirm before risky actions": adds loosening a risk limit and changing the SMTP server, the To address or the heartbeat URL; tightening a limit never asks.
- No new screens. The request gate's refusals (`bad_host`, `origin_refused`) never show in normal use and use the existing error toast.

## 2026-10-27 — Name: Stock Trading Agent

- All screens: the sidebar brand, the page titles, Sign in and the Caddy error page say "Stock Trading Agent". The sidebar brand is 15 px and may wrap to two lines; the "SA" mark is unchanged.
- DESIGN.md and tokens.css headers renamed; the page-title examples now read "● Market open · Stock Trading Agent".

## 2026-10-27 — Day-trade rule retired

- `Positions.dc.html`: the "Day trades 0 of 3" card is now "Today" (entries, exits, bought and sold the same session, symbols not to be bought again today, and a note on the retired rule). MSFT no longer shows "agent exit waits until tomorrow".
- `Main.dc.html`: the risk card fact "Day trades" is now "Re-entry"; the positions note no longer mentions the day-trade guard.
- `Decision.dc.html`: the gate "Day-trade guard" is now "Not traded today".
- `Settings.dc.html`: the notifications drop "Day-trade guard at 3 of 3"; "Exit deferred or earnings exit tomorrow" is now "Earnings exit tomorrow".
- `States.dc.html`: the day-trade guard banner and the day-trade warning in the Stop & flatten dialog are removed.
- `Insights.dc.html`: "Exits deferred by the day-trade rule" is now "Agent exits on the entry day".

## 2026-10-26 — Error pages

- New `Errors.dc.html` with six pages shown in browser frames:
  - **In the app:** "Decision 98213 doesn't exist" (404 for a record, with a matching search), "There is no page here" (404 for a route), "Something went wrong on this page" (error id, Reload, Copy error id, Open in Logs), and "The app isn't answering" (first load, retrying every 15 s, with a note that stops at Alpaca still protect positions).
  - **Served by Caddy:** "Stock Trading Agent isn't responding" (502/504) and "Updating" (503). Brand mark only; no version, host or data.
- The in-app pages use the normal shell (simplified here); the static ones stand alone with system fonts.

## 2026-10-26 — Sign-in and server

- New `Login.dc.html`: one card per step (password, authenticator code, locked out) with the PAPER badge and host name. There is no sidebar before signing in.
- Sidebar on every screen: "Sign out" above the status line (new `signout` icon; `lock` icon moved to the shared set).
- `Settings.dc.html`: new sections Access (sign-in mode, password, authenticator and recovery codes, idle sign-out, risky actions, signed-in devices, failed sign-ins) and Server (version, address and certificate, running since, clock, disk with warning, OS updates, local and offsite backups, restore test, heartbeat, single instance, Back up now and Download).
- `States.dc.html`:
  - new group "Access and server": session expired, restarted and resumed, restarted paused, another instance (critical), offsite backup failed, and the new sign-in toast;
  - new dialog "Confirm it is you".
- All screens re-rendered.

## 2026-10-26 — Insights

- New `Insights.dc.html` in the Learn group (sidebar: Reviews, Insights, Strategies, Backtest, LLM inspector; new `bulb` icon), with:
  - headline tiles with definitions on hover and a 90% range bar on Expectancy;
  - finding cards (accent chip for findings, greyed early signals) with "Test in a shadow" and "Show the trades";
  - the trade funnel on a log scale with what was lost per step, and Skipped setups (missed and avoided);
  - Slices: 16 dimension chips, a table by entry cycle with an average-R range bar, missed and avoided skips and sample status, and the 15:46 rule row;
  - Calibration bars against dashed stated-confidence lines (early greyed, hidden counted); Execution key-value list; Overnight vs in-session bars.
- The sample is deliberately a fuller run (Fri 18 Dec, 58 sessions, 112 closed trades) so findings, early signals and hidden slices all appear; the subtitle says so.
- All screens re-rendered for the new sidebar item.

## 2026-10-25 — Decision drawer for decision.v2

- New `DecisionV2.dc.html` (560 px drawer, same structure as `Decision.dc.html`):
  - header with strategy and prompt version, and a "shadow · no real order" chip;
  - ENTER with the option letter and confidence ("chance of reaching the target before the stop" on hover);
  - tiles for entry limit, stop/invalidation, target and R/R with risk per share;
  - "Options offered": a table A–E with stop (basis, ATR distance), target (basis, levels in the way) and R/R, with the chosen row highlighted, and a note on how they were built and what was merged;
  - "Levels" L1–L9 with the price and limit line between them, the used ids in accent, and why unused levels were not stops;
  - reasoning with a "Levels used" chip and an explanation check banner;
  - gates starting with "Option D offered" and "Explanation check";
  - a note comparing with live v1 and explaining the order-time recheck.
- Sample data: shadow A ("Stricter trend") now reads decision.v1 with RSI 45–65 on Strategies and Backtest, because decision.v2 is now the options prompt.

## 2026-10-24 — Logs in Readiness

- `Readiness.dc.html`: Operations gains "Errors in the log" (2 unreviewed · 4 lines, not yet, with a link to review on Logs) and "Critical lines in the log" (0, pass); the group links to Logs; tiles now count 17 criteria (8 pass, 5 not yet); History line added.
- `Logs.dc.html`: Recurring problems gains a Review column. Error problems show "unreviewed" and a Review button; warnings show —.
- `States.dc.html`: new dialog "Review a recurring error": Expected or Fixed, with a required note.
- `Settings.dc.html`: the Readiness zero-tolerance note names the log criteria.

## 2026-10-24 — Logs screen and shutdown states

- New `Logs.dc.html` in the Control group (sidebar: Readiness, Universe, Logs, Settings; new `logs` icon), with:
  - level toggles with counts (Debug and Info off, dashed, by default) and a "Show debug and info" switch;
  - period presets, then search, component, symbol and cycle filters (behind "More filters" on phone);
  - "Warnings and errors per day" stacked bars;
  - "Recurring problems" table;
  - the log grouped per day, each line with time, level chip, message, component and context chips. The opened ERROR line shows the error id, Copy line, the related link, the traceback, the stored JSON line, and surrounding lines at all levels with the line highlighted;
  - Follow new lines switch; footer with file size and retention.
- `States.dc.html`: new "Shutdown and restart" group with the Shutting down pill, the shutting-down banner, the unclean previous run banner (Open logs, Dismiss) and the terminal output after Ctrl+C.
- All screens re-rendered for the new sidebar item; screenshots updated, plus `Logs.png` and `Logs-390.png`.

## 2026-10-24 — Performance: Winners and tails

- `Performance.dc.html`: new "Winners and tails" card below By regime and By symbol:
  - Amber small-sample banner ("6 of 20 trades").
  - R distribution in 0.5R buckets (losses red, wins green, empty buckets as a thin line, counts above the bars).
  - Tiles: Largest win, Largest loss, Top 20% of trades, Net without the best trade, Winners kept. Each tile explains its definition on hover.
  - "After the exit": medians per exit reason (best after and 5-session close), then a per-trade table (result, best while open, best after exit, close 5 sessions after) and a "How to read it" line.
- Sample values match the 6 closed trades on the screen.

## 2026-10-24 — LLM inspector: all calls with filters

- `Inspector.dc.html`, rebuilt:
  - Filters card: Period (Last session, 5 sessions, 20 sessions, All, Custom) with From and To; Outcome (All, Success, Warning, Error) and Source (Live decisions, Shadows, Reviews) as `aria-pressed` toggles with counts; Symbol, Action, Reason, Model, Prompt; Clear filters and Export CSV. At phone width the last row folds behind "More filters".
  - Filtered calls: calls, success, warning, error with shares, cost by source, latency median and p95, a stacked bar per session (success slate, warning amber, error red); a bar click sets the period.
  - Calls list: scrolls inside its card, grouped per session with sticky headers (calls, warnings, errors); rows show time, symbol or review kind, final action, context and model, and an outcome chip naming the reasons; "50 of 65 loaded · Load 15 more".
  - Details: title with session date; outcome block coloured by outcome with the reason codes and explanation; Source, Model, Prompt, Tokens, Cost, Latency; Copy link; an Error block replaces Raw response for failed calls; review calls show their facts as the user part.
- New screenshots `Inspector-error.png` (timeout selected) and `Inspector-390.png`.

## 2026-10-24 — UX review: scale, states and interaction

- `Main.dc.html`:
  - Today's decisions is grouped by cycle (Amsterdam time, New York on hover) with a collapsible header per cycle. Changes (enter, exit, stop moves) always show; the rest is grouped by action and reason with symbols listed and a count. Rows are 44 px links with hover. Pre-filter rows read "NO CALL", as on the decision card.
  - Chart header: buttons for held symbols plus Find symbol; there is no row of buttons for every symbol.
  - Model calls and Since start tiles are links (Open →).
  - The cycle strip is no longer a live region, so the countdown isn't announced every second.
  - Watchlist: filters are toggle buttons, rows are buttons in a list; the screened-mode hint was removed from the fixed-mode design.
- `States.dc.html`: new "Market closed · Dashboard shows the last session", "Card states" (loading, empty, error, backend unreachable) and "Toasts" (critical persistent, info auto-close) groups; the pending approval shows when it expires.
- `Universe.dc.html`: pool filters are toggle buttons.
- All screens: shared focus ring, hover and skeleton styles (`tokens.css` interaction tokens and classes). `DESIGN.md`: new "Interaction rules" section.
- All screenshots re-rendered.

## 2026-10-24 — Readiness

- New screen `Readiness.dc.html` in the Control group (every sidebar updated, shield icon): red "Not ready for live trading" banner, five summary tiles, Evidence (7), Model (2) and Operations (6) criteria with current value, target, progress bar for counts and a status chip (pass, not yet, waiting for data), five manual items with checkboxes, and History.
- `Settings.dc.html`: new Readiness section with the configurable thresholds; zero-tolerance criteria noted as fixed.
- All screenshots re-rendered.

## 2026-10-21 — Backtest: process, bar store, estimate

- `Backtest.dc.html`: the estimate shows "≈" and the cost at the 10-call cap; the note under Run says separate process, one database per strategy, 40 Alpaca calls per minute and pausing during live cycles and checks; the Runs card shows the bar store size with "Clear bar store" (disabled while a run is active).

## 2026-10-19 — Briefing card, model call counter, Strategies, Backtest

- `Main.dc.html`: summary tiles row (Positions, Model calls today with a bar against the 60-call maximum and cost, Since start); new "Daily briefing · 15:00" card next to the risk budget card, with fact chips and a footer (numbers checked, model and prompt, cost, next briefing).
- New screen `Strategies.dc.html` (Learn group): shadow cost and slots with New shadow, comparison table with colour keys, small-sample warning, equity curves with SPY, selected strategy (what differs from live, Pause, Archive, Promote disabled under 20 trades), disagreements list.
- New screen `Backtest.dc.html` (Learn group): new-backtest form (dates, quick ranges, universe, strategies with cutoff note, equity, slippage, estimate with model-cutoff check), runs list (running with progress and cancel, done, refused), results for run #4 (tiles live vs rules, equity curves with SPY, by regime, Read with care, trades/decisions/CSV).
- Sidebar: Learn group now Reviews, Strategies, Backtest, LLM inspector (every screen). All screenshots re-rendered.

## 2026-10-18 — Notifications in Settings

- `Settings.dc.html`: new "Notifications" section (and section-list entry) after Model and reviews: event table grouped Critical / Warnings / Trading / Reviews with Browser and Email checkboxes (browser locked on for critical events); Browser channel status; Email (SMTP) fields with encrypted password, Send test and Test and save; repeat limit; quiet hours; recently sent list. `Settings.png` and `Settings-390.png` re-rendered.

## 2026-10-18 — Cycle counter

- `Main.dc.html`: the this-cycle strip ends with a cycle counter instead of "Cycle 3 of 6 today": next cycle 19:46 (13:46 NY) with countdown, six segments for today's cycles (done, next outlined in amber, later), "Cycle 4 of 6 today · #52 this run" and the next position check. `Main.png` and `Main-390.png` re-rendered.

## 2026-10-18 — Risk budget card

- `Main.dc.html`: the four summary tiles are replaced by a "Risk budget" card (meters for open risk vs 2.5% cap, today's loss vs −2% limit, drawdown vs −8% kill; facts for room for more trades, sectors, risk per trade and losing streak, shock brake, day trades; every label has a hover explanation) next to two tiles (Positions, Since start). The open-risk and day-trade tiles moved into the card. Screenshots `Main.png` and `Main-390.png` re-rendered.

## 2026-10-18 — Larger universe (v0.2)

- New screen `Universe.dc.html` in the Control group (every sidebar updated): mode banner, five count tiles, today's screen funnel (72 → 69), how the screen works, and the pool table with filters (All, Eligible, Held, Excluded, Warming up), search, rank, 20-session dollar volume, return vs SPY, status with reason, Add to pool, Import CSV and remove.
- `Settings.dc.html`: Trading → Universe is now a Fixed list / Screened pool switch with a link to Universe, plus a "Liquidity screen" group (min price, min dollar volume, sessions, drop after, one share fits (locked), model-call order). The symbol chips moved to the Universe screen.
- `Main.dc.html`: watchlist toolbar with filters (All, Pass rules, Held, Earnings ≤ 3 days) and search, and a link to Universe.

## 2026-10-18 — Settings redesign and API keys

- `Settings.dc.html` rebuilt: section list on the left with "Find a setting" and unsaved-change counts (a scrolling row at phone width); larger section titles with a one-line explanation; one row per setting (name, explanation, control with unit, default underneath) instead of four-column field grids; ranges as one row ("40 to 70"); locked values (cash only) marked "fixed by the spec"; held symbols in the universe locked; sector picker when adding a symbol.
- New "API keys and data" section first: Alpaca paper keys, OpenAI and Finnhub with status, masked key, last test, Test and Replace; Replace for Alpaca disabled while positions are open; inline "Test and save" form with show/hide; data sources as compact tiles. Replaces the Connections table ("keys from .env").
- Changed values highlighted with "Changed from … · Reset"; sticky bar lists the changes (old → new) with a required reason, Discard and "Save as v4". Reset paper account moved to the last section.
- New screenshot `Settings-390.png` (phone width).

## 2026-10-17 — Reviews

- New screen `Reviews.dc.html` in the Learn group of the sidebar (every screen's sidebar updated): Weekly and Briefings tabs, Week 41 review with six stat tiles, closed trades, skipped setups after 5 sessions, observations, a "Suggestion · not applied" box and "Verdicts to confirm" with Good call / Bad call / Unclear.
- Decision: verdict chip under the outcome ("Verdict: draft after 22 Oct") with a link to Reviews.
- All screenshots re-rendered.

## 2026-10-17 — Paused at startup

- Header (every screen): Pause button left of Kill switch; it becomes an amber Resume button while paused.
- States: new "Paused" session state with the Resume button, and a blue "Started paused" banner shown after every start until Resume.
- All screenshots re-rendered.

## 2026-10-17 — Decision card and drawer like Trading Agent

- Dashboard: the Latest decision card adds a market-regime chip and an earnings chip, and always shows a three-line summary box (skip: failed check and proposed stop/target; held: position, stop/target, when an agent exit is allowed; no model call: the pre-filter reason). Button renamed "Open full decision".
- Decision: rebuilt in Trading Agent's drawer layout: header with regime chip; ENTER badge with size and confidence bar; Safety gates as one bordered list (code checks, then risk gates; amber for "reduced"); Signal stack table with 1h and Daily columns plus chips; Agent reasoning box with risk chips and news used; Entry/Stop/Target/Invalidation tiles; Outcome with best/worst R and first touch; footer with order status and links to Positions & orders and the model call. The raw input snapshot moved to the LLM inspector only.

## 2026-10-16 — Screenshot instructions

DESIGN.md: PR screenshots are saved in `docs/screenshots/pr-<n>/`, never in `design/`. No screen changes.

## 2026-10-16 — By symbol card

Performance: new "By symbol" card next to "By regime" (closed trades per symbol, best first: trades, won, P&L, average R). Decisions and outcomes now spans the full width. Screenshot updated.

## 2026-10-15 — Flatten question answered

DESIGN.md: Stop & flatten never refuses because of the day-trade rule; the existing warning in the dialog stays. No screen changes.

## 2026-10-15 — Supervised mode wording

Settings: the supervised-mode text now matches the spec — entries only wait for approval, an approval expires at the next cycle and is checked again on approval. DESIGN.md: the approval-expiry question is answered.

## 2026-10-15 — First handoff

All 7 screens: Dashboard, Decision detail, Positions & orders, Performance, LLM inspector, Settings, States and dialogs.

Adapted from the Trading Agent design for stocks:

- Header shows PAPER, the US session state with next-cycle countdown, equity in USD and EUR, today's P&L, the SPY regime and the Kill switch.
- Dashboard: 1h candles with session separators, session VWAP, entry/stop/target lines; watchlist with rules status and earnings dates.
- New Positions & orders screen: bracket legs at Alpaca, reconciliation status, day-trade counter, order history with slippage.
- Performance compares with SPY buy-and-hold, shows raw and adjusted P&L, and scores skips as well as entries.
- States cover session states, broker-side stop and reconciliation banners, PDT guard, earnings data down, and the kill switch, flatten, resume, reset and save dialogs.
- Left out for v0.1: Ask the agent, briefings, reviews, insights, what-if and readiness.
