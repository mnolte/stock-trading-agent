# Design changes

Newest first. Each entry names the screens it touches.

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
