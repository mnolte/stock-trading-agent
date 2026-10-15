# Design changes

Newest first. Each entry names the screens it touches.

## 2026-10-15 — PR 1: shared components and shell

- Copied `tokens.css` to `frontend/src/styles/tokens.css`.
- Built header (PAPER badge, session pill, equity, today, regime, kill switch), sidebar, card, stat card, chip, action badge, check row, banner, button, segmented control, toggle, number field, table and dialog on the `/dev/components` page.
- Empty screens routed: Dashboard, Positions & orders, Performance, LLM inspector, Settings, States and dialogs.

## 2026-10-15 — First handoff

All 7 screens: Dashboard, Decision detail, Positions & orders, Performance, LLM inspector, Settings, States and dialogs.

Adapted from the Trading Agent design for stocks:

- Header shows PAPER, the US session state with next-cycle countdown, equity in USD and EUR, today's P&L, the SPY regime and the Kill switch.
- Dashboard: 1h candles with session separators, session VWAP, entry/stop/target lines; watchlist with rules status and earnings dates.
- New Positions & orders screen: bracket legs at Alpaca, reconciliation status, day-trade counter, order history with slippage.
- Performance compares with SPY buy-and-hold, shows raw and adjusted P&L, and scores skips as well as entries.
- States cover session states, broker-side stop and reconciliation banners, PDT guard, earnings data down, and the kill switch, flatten, resume, reset and save dialogs.
- Left out for v0.1: Ask the agent, briefings, reviews, insights, what-if and readiness.
