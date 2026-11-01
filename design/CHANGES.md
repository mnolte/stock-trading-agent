# Design changes

Newest first. Each entry names the screens it touches.

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
