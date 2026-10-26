# Stock Agent — Spec v0.1

15 October 2026 · Marc Nolte

## Overview and goals

Stock Agent v0.1 is a standalone, paper-only agent that trades a fixed list of liquid US stocks and ETFs through Alpaca's paper account, with an OpenAI model as its decision layer. It is a new application built from scratch, not a fork of Trading Agent. It reuses Trading Agent's visual design and proven ideas (rules pre-filter, risk gates, outcome tracking, kill switch), adapted to stock markets.

**Currency.** Stock Agent trades in US dollars (USD). The Alpaca account, cash, orders, prices, P&L, risk limits and every stored money value are in USD. Euro amounts in the GUI are for display only, converted with the ECB daily rate, and never used in trading decisions or risk checks.

**Timezone.** The application timezone is Europe/Amsterdam. The GUI, logs, alerts, settings history and daily jobs (backups, the daily database copy) use Amsterdam time. Two exceptions: timestamps are stored in UTC, and the decision cycles follow the New York exchange clock (see Market, schedule and data) so they stay aligned to market hours when the US and EU change clocks on different dates. Cycle times are shown in Amsterdam time.

v0.1 is done when:

- The agent runs unattended through full US sessions for 4 consecutive weeks without crashes or unreconciled positions.
- Every decision (trade or no-trade) is stored with its inputs, prompt version, model output and outcome.
- Every open position has a broker-side stop order at Alpaca at all times.
- Performance is shown against a SPY buy-and-hold benchmark started on the same day.
- No code path can place an order against a live (real-money) Alpaca endpoint.

Working title: **Stock Agent**. Repo: `stock-agent`.

## Decisions

These defaults are set so Devin can start. Items marked **(review)** are Marc's call; change them here and the rest of the spec follows.

| Topic | Default | Why |
| --- | --- | --- |
| Broker | Alpaca paper account (paper-only account is fine) | API-first, free paper trading, available to non-US users |
| Market data | Alpaca free Basic plan: real-time IEX + 15-min delayed SIP for history | Free; hourly decisions don't need tick-level SIP |
| Universe **(review)** | SPY, QQQ, AAPL, MSFT, NVDA, AMZN, GOOGL, META, JPM, XOM | Very liquid, spread across sectors |
| Direction | Long only, no shorting, no options, no margin use | Keeps v0.1 simple and bounded |
| Timeframe | 1-hour bars, regular trading hours only | Matches Trading Agent's lesson that short timeframes don't pay |
| Cycle | Six cycles per full session at 10:46, 11:46, 12:46, 13:46, 14:46 and 15:46 New York time, plus a position check every 15 minutes in between (no trades, no model calls) | Runs on each session-aligned 1h bar once the 15-min SIP delay has passed |
| Paper balance **(review)** | $10,000, set via Alpaca paper account reset | Close to a realistic first live size |
| Account currency | USD; EUR equivalent shown everywhere using the ECB daily rate | Alpaca accounts are USD |
| Shares | Whole shares only | Bracket and stop orders need whole shares |
| Stops | Broker-side stop-loss on every position (bracket orders) | Protects positions between cycles and if the app is down |
| Model | gpt-4o-mini, temperature 0, JSON output | Same as Trading Agent during development |
| Execution mode **(review)** | Automatic. Supervised mode is built together with its screen in PR 6; until then the app refuses to start when the mode is set to supervised | Same as Trading Agent |
| Earnings data | Finnhub free earnings calendar | Alpaca has no earnings calendar |
| News | Alpaca News API | Included with the Alpaca account |
| Benchmark | SPY buy-and-hold from day one | Honest comparison against doing nothing |
| Hosting | Local during development, VPS later | Same as Trading Agent |

## Architecture and stack

One Python backend process runs the scheduler, the agent and the API; a React app talks to it over HTTP and a WebSocket.

| Layer | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, Uvicorn |
| Scheduler | APScheduler, timezone-aware (`America/New_York`) |
| Broker + data | `alpaca-py` SDK (trading, market data, news) |
| LLM | OpenAI Python SDK, structured JSON output |
| Storage | SQLite via SQLAlchemy 2.x, Alembic migrations |
| Indicators | pandas + own implementations (no TA-Lib dependency) |
| Frontend | React + TypeScript + Vite; Lightweight Charts for price charts |
| Live updates | WebSocket from backend to GUI sending `{"type": "update", "topics": [...]}` (header, positions, orders, decisions, banners, watchlist, performance, approvals); the GUI refetches those REST endpoints, debounced 300 ms, refetches everything after a reconnect, and polls every 60 s with a "live updates paused" note while the socket is down |
| Config | `.env` for mode, host, port and paths; `config.yaml` for defaults; API keys entered in Settings and stored encrypted (see API keys) |

Repo layout:

```
stock-agent/
  backend/
    app/
      api/          # FastAPI routes + websocket
      broker/       # Broker interface, AlpacaPaperBroker, FakeBroker
      data/         # bars, quotes, news, earnings, FX; each with a fake
      market/       # calendar, clock, session state
      signals/      # indicators, regime
      rules/        # deterministic pre-filter
      decide/       # prompt builder, LLM client, output validation
      risk/         # sizing, gates, kill switch
      engine/       # cycle runner, reconciliation, outcome worker
      db/           # models, migrations
    prompts/decision.v1.md
    tests/
  frontend/
  design/           # DESIGN.md, tokens.css, screens/, screenshots/
  docs/             # SPEC.md, CHANGES.md, README.md, DEVIN_KICKOFF.md
  README.md  AGENTS.md  CLAUDE.md
```

The `Broker` and every data source sit behind an interface with a fake implementation, so the whole cycle can run in tests and in a "simulated" mode without network access.

## Market, schedule and data

The agent only acts during regular US trading hours (09:30 to 16:00 New York time). Outside them it stays idle and the GUI says when the next session opens.

**Calendar and clock**

- Load the trading calendar from Alpaca (`/v2/calendar`) at startup and daily at 08:00 New York time. It covers holidays and early closes (13:00 days).
- Use Alpaca's `/v2/clock` as the single source of truth for "is the market open now".
- All scheduling uses `zoneinfo` with `America/New_York`. The GUI shows Amsterdam time. Never hard-code the 6-hour offset: the US and EU change clocks on different dates (2026: EU on 25 October, US on 1 November), so the offset is 5 hours for that week.
- Session states shown in the GUI: Closed, Open, Last cycle done, Early close. Early close shows for the whole early-close session until its last cycle has run; after that Last cycle done shows, as on a normal day.

**Bars and cycle timing**

- Build session-aligned 1h bars (09:30, 10:30, ... 14:30, plus the final 15:30 to 16:00 half bar) by aggregating 5-minute SIP bars. Do not use Alpaca's clock-hour bars: their first bar mixes pre-market and the open.
- Free SIP data is 15 minutes delayed, so each cycle runs 16 minutes after its bar closes: 10:46, 11:46, 12:46, 13:46, 14:46, 15:46. Every SIP bar request sets its end time at least 16 minutes in the past, because the free plan rejects requests for more recent SIP data.
- On early-close days, run only the cycles whose bar closes before the close; the last one is the exit-check. Every scheduled cycle that doesn't complete is stored with its reason (app down, shutdown, error, market closed). Restarts and deploys happen outside regular trading hours except in an emergency. Missed cycles are never run late: if the app was down at a cycle time, it waits for the next scheduled cycle (scheduler misfire grace 2 minutes). Only simulated mode may run a cycle at startup.
- History needed at startup: 60 sessions of 5-minute bars per symbol, cached in SQLite and topped up each cycle. Daily indicators use Alpaca daily bars instead: 260 sessions per symbol (enough for SMA 200 and a 60-session ATR percentile), loaded at startup and refreshed daily at 08:00 New York time. They are computed on completed sessions only, so they don't change during the day; the opening gap compares today's open with the previous close. All bar requests use split-adjusted prices (adjustment=split).
- Use the real-time IEX quote (latest trade, bid, ask) only for sizing and the spread check at order time.

**Position check (every 15 minutes)**

Between the hourly cycles a lighter position check runs at 09:46, 10:01, 10:16 and 10:31, then at :01, :16 and :31 past every hour until 15:31 New York time. It makes no model calls, opens no positions, sells nothing and moves no stops. It only:

- Reconciles Alpaca positions and open orders with the database, with the same rules as a full cycle.
- Confirms every position has a live stop order at Alpaca; if one is missing, it places a protective stop at the stored stop price and alerts.
- Updates the safety brakes from the real-time IEX quote and the Alpaca account: the since-open part of the shock brake (SPY down more than 2.5%), the daily loss limit and the drawdown kill. The 1h part of the shock brake stays on the hourly cycle.
- Cancels entry orders still unfilled after 10 minutes.

A check takes a few seconds. A failed check is logged; three failed checks in a row show an error banner but do not turn on the kill switch. The header shows the next check alongside the next cycle.

**Other data**

| Source | Used for | Refresh |
| --- | --- | --- |
| Alpaca SIP 5-min bars (delayed) | Indicators, regime, outcomes | Every cycle |
| Alpaca IEX latest quote | Sizing, spread check | At order time |
| Alpaca News API | Headlines for the last 24h per symbol, passed to the model | Every cycle |
| Finnhub earnings calendar | Earnings blackout | Daily at 08:00 New York time |
| ECB EUR/USD reference rate | EUR display only | Daily |
| Alpaca corporate actions | Detect splits and dividends on held symbols | Daily |

If any source fails, the cycle records it. Missing bars or quotes mean no new entries that cycle (stops at Alpaca still protect open positions). Missing news or earnings data is passed to the model as "unavailable", never as "none"; missing earnings data also blocks new entries for that symbol. If the trading calendar can't be loaded, the agent treats the market as closed (no cycles, no new entries) and retries every 5 minutes; stops at Alpaca keep working. If the EUR/USD rate is unavailable, the GUI shows USD only, marked "EUR unavailable".

## Alpaca integration

Alpaca is the source of truth for cash, positions and orders; the local database mirrors it and is corrected from it.

**Paper only**

- The trading client is always created with `paper=True`. The live base URL must not appear anywhere in the code; a test fails the build if it does.
- At startup, read the account and refuse to start unless it is a paper account. Show "PAPER" permanently in the GUI header.
- Keys are entered in Settings and stored encrypted (see API keys). They are never logged or sent to the frontend.

**Entries**

- Every entry is a bracket order: limit buy entry, stop-loss leg, take-profit leg. Time-in-force `gtc` so the stop and target survive overnight.
- **Market price.** On the free data plan the IEX quote is one small exchange's book, so it is not used as the market price. The market price is the IEX last trade, which must be at most 60 seconds old (`execution.quote_max_age_seconds`, judged on the trade's own timestamp, never the fetch time) and within 1% (`execution.quote_max_deviation_pct`) of the last closed 5-min bar's close. A missing or older trade is `price_stale`; a trade further from the bar is `price_suspect`; either means no model call and no entry. Held positions are always reviewed; for them a stale or suspect price only blocks a tighten_stop.
- **IEX bid and ask** are used only when plausible: both present, timestamped, and within 1% of the market price. A thin IEX book (a side missing or further away) is recorded as `spread_unknown` and is not a reason to skip.
- Entry limit = market price + 0.05%. If not filled within 10 minutes, cancel it and record "entry not filled".
- Skip the entry if the IEX spread is above 0.15% of price, measured only when the quote is plausible. With `spread_unknown` the entry may go ahead: the limit caps what can be paid and the universe is liquid.
- Quantity is whole shares from the risk sizing; if that rounds to 0, skip.
- Each decision stores the price provenance: last trade and its time, bid, ask and quote time, whether the quote was plausible, feed, fetch time, and the time of the last completed signal bar.
- **Sized on the final price.** Right before an entry order is sent (automatic or approved), the market price and quote are fetched again and checked as above. Size, order value and every risk gate are then worked out again from the final limit and the portfolio's current budgets; the order uses that quantity, and the final gate result is stored. The stop must be at least 1.5 × ATR(1h) below the lowest of market price, plausible ask and last closed bar close, so a fill can never land below its own stop. The last closed bar and ATR are required; if either is missing, the entry fails closed with reason `reference_missing`. Any failure means no order: it is recorded as a failed "Fresh quote" gate with its reason, and the symbol is looked at again next cycle. Automatic entries and Approve use the same function. Exits, stop moves and Stop & flatten never wait for a quote.

**Managing positions**

- Moving a stop (for example to break-even) replaces the stop leg in place. Stops only ever move in the position's favour.
- An agent exit cancels the open legs first, then sends a market sell. Confirm the legs are cancelled before selling, so the position can never be sold twice.
- Stop orders trigger only during regular hours. A gap through the stop fills at the open price; the GUI shows that slippage separately.

**Reconciliation**

- Subscribe to Alpaca's trade-updates stream for fills, cancels and rejects.
- At startup and at the start of every cycle, compare Alpaca positions and open orders with the database.
- On any mismatch: record it, show a banner, adopt Alpaca's state, and block new entries until Marc acknowledges it. A position found at Alpaca that the agent never recorded is adopted: it immediately gets a standalone gtc stop at the current price minus 3 × the 1h ATR (3% below the current price if ATR is unavailable), with no target. The agent makes no model calls or exits for it until Marc acknowledges; after that it is a normal position with Alpaca's average price as its entry. It is marked as adopted, and its P&L is shown apart from the agent's own trades.
- A position without a live stop order is a critical error: place a protective stop at the stored stop price immediately, then alert.

**Pattern day trading (PDT)**

The $10,000 paper account is below the $25,000 PDT threshold, and Alpaca checks the PDT rule on every order. To stay safe:

- The agent never closes a position on the day it opened it. Only the broker-side stop or target can do that. A same-day exit decision is not carried out: it is recorded as deferred, and at the next session's first cycle the position is reviewed again as usual. That cycle's input snapshot includes the deferred exit (time and reason), and the dashboard shows "Exit deferred (day-trade rule)" on the position.
- Count day trades over the last 5 trading days. At 3, no new entries until the count drops.
- Check Alpaca's current PDT documentation before PR 5; the rule has been under revision.

**Paper realism**

Alpaca paper fills ignore slippage, market impact, regulatory fees and dividends. The performance screen shows both the raw paper P&L and an adjusted P&L with 0.05% slippage per side (configurable). Dividends on held positions are recorded from corporate actions and added to the adjusted figure.

**Rate limits**

One shared client-side limiter keeps total Alpaca calls under 150 per minute. Retries use exponential backoff and only cover reads; an order is never resent automatically after a timeout. Reconciliation decides what happened.

## Decision layer

Code decides what is allowed; the model only chooses among allowed actions and explains why. The model can never override a rule or a risk gate.

**One cycle**

1. Pre-checks: market open per Alpaca clock, kill switch off, reconciliation clean.
2. Update data: 5-min bars, quotes, news, positions.
3. Compute indicators per symbol and the market regime, and store the snapshot. The GUI reads this stored snapshot; it never recomputes signals per request.
4. Rules pre-filter: pick entry candidates; every held position is always reviewed.
5. One model call per candidate or held symbol (at most 10 per cycle; held positions are called first).
6. Validate the output, apply risk gates, size and place orders.
7. Store the full decision record; the outcome worker fills in results later.

**Indicators (on session-aligned 1h bars unless noted)**

- EMA 20 and EMA 50; RSI 14; ATR 14.
- Session VWAP, anchored at 09:30 (stocks have a real session start, unlike crypto).
- OBV trend over 20 bars.
- Relative strength vs SPY: 20-bar return of the symbol minus SPY's.
- Daily: price vs SMA 50 and SMA 200; ATR percentile over 60 sessions; today's opening gap %.
- Days until next earnings.

**Market regime** comes from SPY and is shown in the GUI. Trend: Up (daily close above SMA 50 and SMA 50 above SMA 200), Down (both below), else Sideways. Volatility: Low, Normal or High by SPY daily ATR percentile (below 30, 30 to 70, above 70). Down + High means no new entries.

**Rules pre-filter.** A symbol is an entry candidate only if all of these hold:

- Not held, and no open entry order for it.
- Daily close above its SMA 50.
- 1h EMA 20 above EMA 50.
- RSI between 40 and 70, inclusive.
- Outside the earnings blackout (see Risk).
- Regime is not Down + High.
- Not the 15:46 cycle (no new entries in the last cycle of a session).

All thresholds live in `config.yaml`.

**Model output.** Strict JSON through OpenAI structured outputs (a strict JSON schema), validated again with Pydantic. Allowed actions depend on context: for a candidate `enter` or `skip`; for a held position `hold`, `tighten_stop` or `exit`.

```json
{
  "symbol": "MSFT",
  "action": "enter",
  "confidence": 0.62,
  "stop_price": 412.80,
  "target_price": 431.50,
  "thesis": "Trend continuation above session VWAP after pullback to EMA 20.",
  "invalidation": "Hourly close below 412.80.",
  "risks": ["Broad market weak today", "Earnings in 9 sessions"],
  "news_used": ["<alpaca news id>"]
}
```

Code checks after the model answers:

- The stop is below the current price, 1.5 to 4 times the 1h ATR away. The same rule applies to every symbol, so expensive stocks are not locked out.
- Reward to risk, computed by code from entry, stop and target, is at least 1.5.
- `confidence` is at least 0.55 for `enter`.
- A `tighten_stop` only raises the stop, and the new stop must be at least 0.25 × ATR(1h) below the lowest of market price, plausible bid and last closed bar close (`decision.tighten_min_atr_below_price`); this is checked again right before the stop is replaced, and if the price has moved too far the stop stays where it is and the reason is stored. The last closed bar and ATR are required, and the market price must not be stale or suspect; otherwise the existing stop stays and the reason is stored.
- Invalid or unparseable output counts as `skip` (or `hold` for a position) and is stored with the raw text. When no model call is made or the call fails, the decision is stored with final action `no_call` and a reason: `rules`, `call_cap`, `quote_missing`, `model_error` or, under decision.v2, `no_options` (see Decision v2). A model call times out after 30 seconds and is not retried; for a held position `no_call` means the position is left as it is.

**Prompt.** `prompts/decision.v1.md` (decision.v2 with code-built options is specified in Decision v2), versioned; each decision stores the prompt version and hash, the model name and the token cost. The USD cost is computed in code from `model_prices` for the call's model and stored with the call, with its source (`priced`, `cached` or `legacy` for older calls stored with tokens but no cost; legacy costs are never rewritten). The input snapshot is a JSON block with the indicators, regime, position (if any), last 24h headlines and earnings date. It also states the time of the quote and of the last completed signal bar, so the model can tell a current quote from a delayed bar. For a held position it includes the entry, the initial stop, the current stop, the original thesis and invalidation, and progress in R measured against the initial risk (entry − initial stop), with the remaining downside to the current stop shown separately. Headlines come per symbol with a status: `complete`, `empty` (complete, no headlines), `truncated` or `failed`; only `empty` may be read as "no news". News is fetched until every symbol's result is complete or the request budget runs out, never under one shared cap. Data that could not be fetched is marked `"unavailable"`. The prompt tells the model to treat unavailable data as unknown and never to invent numbers or news.

**Outcome tracking.** For every decision, including skips, record the price after 1 hour, 1 session and 5 sessions. 1 session means the same New York time on the next trading session; 5 sessions means the same time on the 5th trading session after the decision (weekends and holidays don't count). If that time falls after the session's close (early-close days), the close is used. The price is the last closed 5-minute bar at or before the target time, stored with that bar's timestamp. For trades, also record the best and worst price reached while open, and whether the stop or the target was touched first, judging each bar against the stop that was active at that bar's time (from the stop-leg order history), never against today's stop. This feeds the Performance screen and later prompt reviews.

## Risk management

Risk gates run in code after the model answers, in the order below; the first gate that fails blocks the action and is recorded by name. All values live in `config.yaml`.

| Gate | Default | Effect |
| --- | --- | --- |
| Risk per trade | 0.5% of equity | Shares = risk budget / (entry − stop), rounded down |
| Max position size | 20% of equity | Shares reduced to fit |
| Total open risk | 2.5% of equity (sum of entry-to-stop risk on all positions and working entry orders) | Size down, or skip if under 1 share |
| Max open positions | 4 | Skip |
| Sector cap | Max 2 positions per sector; QQQ counts as Technology, SPY as Index. One sector resolver serves gates, shadows and the dashboard: the pool's sector in screened mode, the configured sector in fixed mode. An unknown sector fails the gate (`sector_unknown`) | Skip |
| Cash only | Order value ≤ cash; margin never used | Skip |
| Daily loss limit | −2% of equity in the current session | No new entries for the rest of the New York session, even if equity recovers; the latch is stored and survives a restart |
| Losing streak | 3 losing trades in a row | Risk per trade halved until the next winning trade |
| Shock brake | SPY down more than 1.5% in the last 1h bar, or more than 2.5% since the open | No new entries this session |
| Earnings blackout | Earnings within the next 3 trading days | No new entry |
| Earnings exit | Held symbol reports before the next session's close (an unknown time of day counts as before the open) | Exit in the 15:46 cycle the day before (setting "Hold through earnings": off) |
| PDT guard | 3 day trades in the last 5 trading days | No new entries |
| Drawdown kill | Equity 8% below its peak | Kill switch turns on automatically |
| Order sanity | No single order above 25% of equity; at most 6 orders per cycle | Block and alert |

**Working entry orders.** An entry order that is submitted, accepted or partially filled, and not yet cancelled, expired or rejected, counts in the gates as if it were already a position: its unfilled quantity × (limit − stop) counts toward total open risk, its unfilled quantity × limit toward cash, and it counts toward max open positions, the sector cap and max position size. Filled quantity counts as a position only, never twice. Entries in one cycle are gated one by one in submission order, each against the budget left after the earlier ones. A cancel, expiry or rejection frees the budget. In supervised mode an entry waiting for approval does not count; the gates run again when Marc approves.

**Kill switch.** Manual (header button) or automatic (drawdown kill, repeated errors). It stops all new entries and leaves existing stops in place. "Stop & flatten" also cancels all open orders and sells every position at market; outside regular hours the sells are queued as market-on-open orders. Stop & flatten never refuses because of the day-trade rule: it warns when a sale would count as a day trade, and if Alpaca rejects a sale (for example under its own day-trade protection), that position keeps its stop and the rejection shows as a critical banner. Resuming requires an explicit acknowledgment with a typed reason, which is stored.

**Paused at startup.** The agent always starts with new entries paused. Startup, reconciliation with Alpaca, position checks, the stops and targets at Alpaca, earnings exits and outcome tracking all run as normal; cycles still run and review held positions, so the model can still decide to hold, tighten a stop or exit. No new entries are placed until Marc presses Resume in the header: one click, no reason needed. Pause is the same button in reverse and can be used at any time. Pause and the kill switch are separate: the kill switch needs a typed reason to resume and can also flatten. Both states are stored and survive a restart: a kill switch that was on stays on, and startup always comes up paused even if trading was running before.

**Errors.** Three failed cycles in a row (any unhandled error) turn the kill switch on. Only a completed cycle resets this count; skipped (market closed, holiday), interrupted and running rows leave it unchanged, so error, skipped, error, error still turns the kill switch on. Position checks have their own failure count (alert after 3) and do not affect this one. A failed model call is never retried with a different model silently. On a normal shutdown the app waits up to 60 seconds for a running cycle or position check to finish; if it doesn't, it is marked interrupted, which does not count as a failure. Only a cycle or check still marked running at the next startup (a real crash) counts as failed. Startup always reconciles with Alpaca before anything else runs.

**Supervised mode** (built in PR 6). Every entry waits for Marc's approval on the Dashboard. A pending approval expires at the next full cycle. Approving re-checks the risk gates, the size and the spread, and sets a new entry limit from the current price; the new limit must also pass the original code checks (stop 1.5–4 × ATR away, reward to risk at least 1.5, using the model's stop and target). If any check fails, the approval is rejected with the reason shown. There is at most one pending approval per symbol; a newer one replaces it. Approving is disabled while the kill switch is on. Each approval is stored in an `approvals` table (decision, status pending, approved, rejected or expired, reason, created and resolved times) and its outcome shows on the decision. Agent exits, stops and targets at Alpaca, earnings exits and the kill switch never wait for approval.

**Reset paper account.** Alpaca's API can't restore the balance, so a reset has two steps. The Settings button turns the kill switch on, closes all positions and cancels all orders at Alpaca, records a `paper_reset` event, and tells Marc to reset the balance in the Alpaca dashboard. "Start new run" then checks that there are no positions or open orders and that equity equals the starting balance (`performance.starting_equity`, ±1%); if not, it says which check failed. A new run is stored in a `runs` table and restarts the SPY benchmark, the peak equity for the drawdown kill, the losing streak and the daily loss baseline. Earlier runs stay viewable in Performance and past decisions stay in the log. Trading resumes only through the normal kill-switch resume.

**Adding a symbol.** Settings requires a sector from a fixed list (the 11 GICS sectors plus Index) when adding a symbol. The symbol must be an active, tradable US stock or ETF at Alpaca. A new symbol shows "Warming up" and gets no entries until its 260 daily bars and 60 sessions of 5-minute bars are loaded.

## GUI

The GUI reuses Trading Agent's visual language: the dark "instrument panel" style, IBM Plex type and the design tokens from its DESIGN.md. Copy the tokens file into this repo; do not import from the other project. Money is shown in USD with the EUR equivalent beside it; times are in Amsterdam time with New York time on hover.

**Header (every screen):** PAPER badge, market session state with a countdown to the next open, close or cycle, equity in USD and EUR, today's P&L, regime label, Pause/Resume button, kill switch button.

| Screen | What it shows |
| --- | --- |
| Dashboard | Risk budget card and cycle counter (see below); price chart for the selected symbol (1h bars, VWAP, EMAs, entry, stop and target lines); watchlist with price, day change, rules status and earnings date; open positions with unrealized P&L and stop distance; latest decisions feed; next-cycle timer; latest-decision card for the selected symbol (action, confidence, market regime, earnings date, reasoning, signal chips, and a three-line summary: the failed check and proposed stop and target for a skip, the position and its stop and target for a held symbol, or the pre-filter reason when no model call was made) |
| Decision detail | Same layout as Trading Agent's decision drawer: action with size and a confidence bar; safety gates (code checks, then risk gates, each pass, fail or reduced); signal stack with 1h and daily columns; agent reasoning with named risks and news used; entry, stop, target and invalidation tiles; outcome after 1h, 1 session and 5 sessions with best and worst R and which of stop or target was touched first; order status; links to the position and the model call |
| Positions and orders | Open positions, their bracket legs at Alpaca, order history with fills and slippage, reconciliation status, day-trade counter (last 5 trading days) |
| Performance | Equity curve vs SPY buy-and-hold (USD and EUR), raw vs adjusted P&L, win rate, average R, max drawdown, results by symbol and by regime, winners and tails (see Tail metrics) |
| LLM inspector | Every model call since the start (live, shadows, reviews), filtered by period, outcome (success, warning, error), source and more; summary per session; prompt, raw response or error, tokens, cost, latency, outcome and reasons (see LLM call history) |
| Settings | API keys and data-source status, execution mode, universe, risk values, entry rules, earnings handling, model and reviews, notifications, cycle times (read-only), settings history, paper account reset; changes are versioned and take effect next cycle (see Settings screen) |
| Universe (v0.2) | Pool, daily screen and eligible symbols (see Larger universe) |
| Strategies (v0.2) | Live, shadows and baselines compared; equity curves; disagreements; Promote to live (see Strategies and shadows) |
| Backtest (v0.2) | New backtest with estimate, runs, results (see Backtesting) |
| Readiness | Whether the paper evidence would justify live trading (see Readiness) |
| Logs | Every log line, warnings and up by default with a switch for debug and info; level toggles, period, search, component, symbol and cycle filters; warnings and errors per day; recurring problems; traceback, stored line and surrounding lines (see Logging, error handling and shutdown) |
| States and dialogs | Market closed, early close, kill switch on, reconciliation mismatch, data source down, pending approval (supervised mode), shutting down, previous run not shut down cleanly; confirm dialogs for Stop & flatten, reset paper account and settings changes |

The layout must also work at phone width, so the VPS version can be checked from a phone.

**Daily briefing card.** Next to the risk budget card the dashboard shows today's briefing (see Reviews): the text, chips for the facts it used, whether every number was checked or the plain template was used (and why), the model, prompt version and cost, and when the next briefing is written. Before 15:00 Amsterdam on a trading day it shows the previous briefing with its date; with briefings turned off it says so and links to Settings. A briefing never changes after it is written; what changed since is in the cards around it.

**Model call counter.** A summary tile shows today's decision calls against the day's maximum (max calls per cycle × cycles today, 60 by default), with today's and this month's decision cost. On hover it shows review cost against its own cap, and from v0.2 shadow and backtest cost against theirs. It links to the LLM inspector.

**Dashboard at scale and off-hours.**
- **Session day.** "Today" on the dashboard is the current New York session or, outside a session, the last completed one, never the Amsterdam calendar day. Outside a session the feed is titled with that session's date ("Friday 23 Oct · last session"), the model-call tile counts that session, and the cycle strip summarises it. A countdown longer than 24 hours shows only the day and time.
- **Latest decision.** The card shows the selected symbol's latest decision in that session. If the symbol wasn't decided this cycle, it says why from the stored snapshot (pre-filter reason, call cap, price check). If it has no decision in the session, it shows its most recent decision with the date.
- **Decisions feed.** Grouped per cycle (by cycle, not by minute), with the cycle's Amsterdam time (New York on hover) and a header that can collapse. Changes (entries, exits, stop moves, deferred exits, approvals) always show as their own rows. Other decisions are grouped by final action and reason into one row: the symbols (the first 8, then "+n more") and a count. The current cycle is open; earlier cycles start collapsed and show only their changes. A single row opens the decision; a grouped row opens the LLM inspector filtered to that cycle and action.
- **Picking a symbol.** By clicking a watchlist row, a button for each held symbol in the chart header, or "Find symbol" (search over the universe). There is never a button for every symbol. The selected symbol is in the URL (`?symbol=`); without one, the dashboard opens on the first held position, otherwise the first watchlist row.
- **Watchlist.** In screened mode it shows a rank column and "sorted by rank, 15 rows, then Show all"; in fixed mode neither.
- **Tiles.** Positions links to Positions and orders, Model calls to the LLM inspector, Since start to Performance.
- **Approvals** show when they expire ("expires at the 17:46 cycle").

**Interaction and states.** The "Interaction rules" in `design/DESIGN.md` are binding for every screen. In short:
- **Accessibility:** a visible focus ring everywhere, hover feedback on everything clickable, 44 px targets.
- **Loading and errors:**
  - Every data card has loading, empty and error states. Empty text only after a successful load; an error shows what failed and a Retry, with stale data kept and marked.
  - A page banner shows when the backend can't be reached, retrying every 15 seconds.
- **Announcements:** no countdown inside a live region.
- **Toasts:** one per notification event, whatever the channels. Critical toasts stay until dismissed.
- **Data in the browser:**
  - Only the newest response counts when a selection changes quickly.
  - Numbers arrive as numbers and are formatted in the browser (minus sign −).
  - Copy that names a setting reads it from settings.
- **Browser tab:** the title shows the agent's state (open, paused, kill switch).

**Cycle counter.** The this-cycle strip at the top of the dashboard ends with a cycle counter: the next cycle's time in Amsterdam and New York with a live countdown (mm:ss under an hour, otherwise h:mm), one segment per cycle of today's session (done, next, later; an early-close day has fewer), "Cycle n of 6 today", the cycle's number in this run, and the next position check. Market closed: the next session's first cycle with its day. Kill switch on: "Kill switch on · no cycles". A cycle that is running shows "running" instead of the countdown. The count in this run comes from the `cycles` table (kind cycle) since the run started.

**Risk budget card.** The dashboard shows how much of each limit is in use, computed by the same functions as the risk gates (never recomputed in the GUI): open risk of positions and working entries against the 2.5% cap; today's loss against the 2% daily limit; drawdown from the run's peak against the 8% kill. Each is a meter that turns amber above 75% of its limit and red at the limit. Underneath: room for more trades (the smallest of positions left, open risk left ÷ current risk per trade, and cash ÷ max position size, rounded down, with "limited by cash" or "limited by open risk" when that is the smallest), positions per sector against the sector cap, current risk per trade with the losing-streak count, the shock brake state with SPY's move over the last 1h bar and since the open, and day trades in the last 5 trading days. Every label explains its rule on hover. It refreshes with the positions, orders and header updates.

**Settings screen.** Sections with a section list on the left (a scrolling row on a phone) and a "Find a setting" filter: API keys and data, Trading (execution mode, universe, slippage), Risk limits (per trade, portfolio, brakes), Entry rules and checks, Earnings, Model and reviews, Notifications, Readiness, Schedule (read-only), History and Paper account. Each setting is one row: name, one-line explanation, the control with its unit, and its default underneath. Values fixed by the spec (cash only, cycle times) are shown locked. A changed value is highlighted with "Changed from …" and Reset, and the section list shows how many changes each section has. A bar at the bottom lists the unsaved changes (old → new), asks for a reason (required, shown in History) and has Discard and "Save as v<n>". Values are checked as they are typed against the allowed ranges (for example RSI from must be below RSI to); Save is disabled while one is invalid. Leaving the page with unsaved changes asks first. Keys are not part of this flow: they are saved per key, straight away (see API keys).

**Design handoff.** The repo has a `design/` folder: `DESIGN.md` (rules, shared components, which PR builds which screen), `tokens.css`, `screens/` (one HTML file per screen, the source of exact styles and wording) and `screenshots/`. The spec wins on behaviour, the design on look and wording; differences go in the PR description.

## API keys

API keys are entered in Settings, not in `.env`. There are four: Alpaca key ID and secret (paper), OpenAI, and Finnhub. `.env` keeps only `MODE`, `SA_HOST`, `SA_PORT`, `SA_DATA_DIR` and `SA_DB_PATH`.

- **Storage.** Keys are encrypted with Fernet (`cryptography`) and stored in the `secrets` table. The encryption key is `SA_DATA_DIR/secret.key`, created at first start with file mode 600; it is never stored in the database, so a database backup alone does not reveal the keys. If `secret.key` is lost, the keys must be entered again.
- **Never shown again.** The API only accepts keys (write-only). Reads return the name, the last 4 characters, saved at, last tested at and the test result; never the key. Keys are never logged, never in error messages, the LLM inspector, events or settings versions; log output is scrubbed for every stored key as a second line of defence.
- **Test before save.** A new key is saved only after one test call passes: Alpaca `GET account` on the paper endpoint (must report a paper account), OpenAI list models, Finnhub one earnings-calendar request. The old key keeps working until the new one passes. "Test" re-runs the check without changing anything. Every save and test is an event (key name, last 4 characters, result), never the key.
- **Replacing Alpaca keys** is only allowed while the agent is paused or the kill switch is on, with no open positions or orders. If the new keys belong to a different paper account, the change requires "Start new run".
- **Missing or failing keys.** The app always starts (paused, as usual) and shows a banner naming the missing or failing key with a link to Settings. Resume stays disabled until the Alpaca, OpenAI and Finnhub keys are all set and passing. Simulated mode needs no keys.
- **Moving from `.env`.** At startup, a key found in the environment with nothing stored under that name is imported once, tested, and stored; a banner then says "Keys imported from .env — remove them from .env". After that, keys in the environment are ignored with a warning in the log.
- Settings, keys and the kill switch have no login, so the backend stays bound to `127.0.0.1` (hard rule 10). On a VPS later the GUI is reached over an SSH tunnel until login exists.

## Notifications

The agent tells Marc about what matters while he is away from the screen. Every "alert" in this spec is a notification event below.

**Channels.** In the app every event shows as a banner or toast, always. Browser notifications (Web Notifications API) work while the GUI is open in a browser tab; Settings asks for permission. Email goes through SMTP: host, port with STARTTLS, username, from and to in Settings; the password is stored encrypted in the `secrets` table as `smtp_password`, write-only like the API keys. Saving the SMTP details sends a test first; "Send test" sends one to every enabled channel.

| Event | Severity | Browser | Email |
| --- | --- | --- | --- |
| Kill switch turned on (manual, drawdown kill, failed cycles) | Critical | always | on |
| Position without a stop at Alpaca (protective stop placed) | Critical | always | on |
| Reconciliation mismatch or adopted position | Critical | always | on |
| Order rejected by Alpaca (including a Stop & flatten sale) | Critical | always | on |
| Daily loss limit reached | Warning | on | on |
| Shock brake on | Warning | on | off |
| Day-trade guard at 3 of 3 | Warning | on | off |
| Data source or API key failing | Warning | on | on |
| Position checks failing (3 in a row) | Warning | on | on |
| Model failing, or review or shadow cost cap reached | Warning | on | off |
| Entry waiting for approval (supervised mode) | Info | on | off |
| Exit deferred (day-trade rule) or earnings exit tomorrow | Info | on | off |
| Trade opened or closed | Info | off | off |
| Daily briefing | Info | off | on |
| Weekly review ready | Info | off | on |
| Verdicts waiting to confirm (once a day, with the briefing) | Info | off | off |
| Readiness changed (a criterion passes or stops passing) | Info | on | off |

**Rules.** Critical events always go to the browser and are never held back. Any other event with the same subject is sent at most once per 30 minutes (repeat limit). During quiet hours (23:00–08:00 Amsterdam) only critical events are sent; the rest are held and sent as one summary when quiet hours end. Sending runs in the background: a slow or failing channel never delays a cycle, a check or an order. Three failed emails in a row show a banner.

**Content.** Subject `[Stock Agent · PAPER] <event>: <one line with the key number>`, for example "Kill switch on: drawdown −8.1% from peak". The body has the figures, the time in Amsterdam and New York, what the agent did and what Marc needs to do, and a link to the right GUI page. Briefings and reviews carry their text. Notifications never contain keys or passwords.

**Settings.** The event × channel choices, the repeat limit and quiet hours are settings (versioned, in Settings → Notifications). SMTP details are saved on their own, like the keys.

## Data model

SQLite, one file, migrations with Alembic. Times are stored in UTC; money as decimals in USD. The log index (`data/logs/logs.db`, see Logging, error handling and shutdown) is a separate file outside this schema and its migrations.

| Table | Holds |
| --- | --- |
| `bars_5m` | symbol, start time, OHLCV, feed; unique on (symbol, start). Daily bars live in `bars_1d` (symbol, session date, OHLCV), unique on (symbol, date) |
| `cycles` | kind (cycle or position check), scheduled time, started, finished, status, errors, data-source health |
| `decisions` | cycle, symbol, context (candidate or held), input snapshot JSON, rules result, model output JSON, validation result, gates result, final action, prompt version and hash, model, tokens, cost, cached (backtests only; always false live); under decision.v2 also levels JSON, options JSON, chosen option, explanation check, options version |
| `orders` | Alpaca order id, parent id, decision, side, type, leg (entry, stop, target, exit), qty, prices, status, timestamps |
| `fills` | order, qty, price, time, slippage vs intended price |
| `positions` | symbol, origin (agent or adopted), entry decision, qty, avg entry, initial stop, current stop, target, opened, closed, realized P&L raw and adjusted, exit reason, after-exit best price and time, after-exit close price, after-exit status and done at (see Tail metrics) |
| `outcomes` | decision, price after 1h, 1 session and 5 sessions; best and worst price while open |
| `equity_snapshots` | time, equity, cash, SPY benchmark value, EUR/USD rate |
| `day_trades` | date, symbol, order ids |
| `earnings` | symbol, date, time of day (before_open, after_close, during_market or unknown, mapped from Finnhub's bmo, amc and dmh), raw Finnhub value, fetched at |
| `corporate_actions` | symbol, type, date, ratio or amount |
| `settings_versions` | version, settings JSON, changed at, reason |
| `strategies` | name, kind (llm or rules_only), prompt version, model, temperature, rule and check overrides JSON, status (active, paused, archived), created, archived at (v0.2) |
| `shadow_decisions` | strategy, cycle, symbol, context, live decision (shares its input snapshot), own input snapshot (only for a symbol only a shadow holds), model output JSON, final action, gates result, tokens, cost, missed and its reason (time or cost_cap); under decision.v2 also levels, options, chosen option and explanation check (v0.2) |
| `shadow_positions` | strategy, symbol, qty, entry, stop, target, opened, closed, exit reason, R (v0.2) |
| `shadow_equity` | strategy, time, equity (v0.2) |
| `backtests` | name, period, universe mode, strategies JSON, starting equity, slippage, status (queued, downloading, running, done, cancelled, stopped at cost cap, interrupted, refused), progress, cost, run folder path, process id, cancel and pause flags, size on disk, warnings JSON, summary JSON, created, finished (v0.2) |
| `llm_cache` | key hash (model, temperature, prompt version and hash, input snapshot hash), model, temperature, prompt hash, cycle time, response JSON, tokens, created (v0.2) |
| `universe_pool` | symbol, sector, source (default, added or import), added at, removed at (v0.2) |
| `universe_screens` | session date, symbol, status (eligible, excluded or warming_up), check results JSON, last close, average dollar volume, return vs SPY, rank, failed screens in a row (v0.2) |
| `notifications` | time, event, severity, subject, channels, status per channel (sent, failed, held back, quiet hours), error |
| `secrets` | name (alpaca_key_id, alpaca_secret, openai, finnhub, smtp_password), encrypted value, last 4 characters, saved at, tested at, test result |
| `events` | time, level, type (reconciliation, kill switch, error, acknowledgment), details JSON, affects (decision, order, fill or none), resolved at, resolution (auto or dismissed), note |
| `readiness_snapshots` | session date, run, criteria JSON (value, target, status, sample, estimate) |
| `readiness_manual` | item, done, note, changed at |
| `log_reviews` | fingerprint, level, sample message, review (expected or fixed), note, reviewed at, app version (PR 20; in the trading database, so it is backed up) |
| `llm_calls` | sent at, session date, source (live, shadow, review), strategy, kind, linked decision, shadow decision or review, cycle, symbol, context, model, temperature, prompt version and hash, tokens, cost and source, latency, final action, outcome (success, warning, error), reasons JSON, error type, HTTP status, error message, request id, raw response, backfilled (see LLM call history) |

Every connection uses WAL mode and a busy timeout (`storage.sqlite_busy_timeout_seconds`, 30). No job keeps a write transaction open while it waits for a model call or any other network call. A daily copy of the database is made at 03:00 Amsterdam time with SQLite's online backup API (never a plain file copy, which can catch a write half-done), checked with `PRAGMA integrity_check`, and kept for 14 days. Migrations are never edited once merged; a schema change is always a new revision. At startup the app compares the schema with the models and refuses to start with a clear message if they differ.

## Testing and engineering rules

These go into `AGENTS.md` at the repo root so Devin follows them on every PR.

- **No network in tests.** Every external call goes through an interface with a fake. A test that touches the network fails: block sockets in the test setup (for example `pytest-socket`).
- **Simulated mode.** `MODE=simulated` runs the full cycle against recorded bars and the fake broker, at accelerated time. It is used for demos and end-to-end tests.
- **Time is injected.** No direct `datetime.now()` in engine code; use a clock object so tests can run any date, including DST-change weeks and early closes.
- **Required tests:** every indicator against hand-checked values; every risk gate pass and fail; bar aggregation into session-aligned 1h bars; PDT counting; reconciliation mismatches; a position-without-stop recovery; DST weeks (25 Oct to 1 Nov 2026); an early-close day; invalid model output.
- **Risk math consistency.** One function computes stop distance, risk and reward to risk; rules, gates and the GUI all call it. A test checks that each default symbol can produce a valid trade at typical ATR values.
- **Processes.** Never kill processes by name or with broad patterns; stop only the process the task started, by its PID.
- **Secrets.** Never print, log or commit keys, `secret.key` or the database; `.env` and `data/` stay in `.gitignore`. Tests use fake keys only.
- **Spec is binding.** If the spec is unclear or contradicts itself, stop and ask in the PR rather than guessing. Every behaviour change updates `SPEC.md` and adds a line to `CHANGES.md`.
- **PR log.** Each PR ends with what was built, what was tested, what was not, and any deviation from the spec.

## Build plan

Ten PRs for v0.1, each reviewable on its own; v0.2 continues with PR 11 to 14. Paper orders are first sent in PR 5; everything before runs on fakes and read-only data.

1. **Skeleton.** Repo layout, FastAPI app, React shell with header and empty screens, config loading, SQLite and migrations, `AGENTS.md`, CI running tests with sockets blocked. *Done when:* app starts, header shows PAPER and a fake clock.
2. **Market and data.** Calendar and clock, 5-min bar download and cache, session-aligned 1h bars, IEX quotes, news, Finnhub earnings, ECB rate; fakes for each. *Done when:* aggregation, DST and early-close tests pass, and a read-only run fills the cache for all 10 symbols.
3. **Signals, regime and rules.** Indicators, regime, rules pre-filter, shared risk-math function. *Done when:* indicator tests match hand-checked values and the dashboard shows rules status per symbol.
4. **Decision layer.** Prompt v1, input snapshot builder, OpenAI client, output validation, decision storage, LLM inspector screen. *Done when:* simulated cycles produce stored decisions; invalid outputs are handled per spec.
5. **Execution.** Alpaca paper broker, bracket entries, stop moves, exits, trade-updates stream, reconciliation, 15-minute position check, PDT guard, risk gates, kill switch. *Done when:* a simulated session runs with every position protected by a stop, and startup, the paper-account check and reconciliation are verified read-only against the real paper account. Marc runs the first real paper session himself after review, with the paper account reset to $10,000.
6. **GUI.** Dashboard, decision detail, positions and orders, settings, states and dialogs, supervised-mode approvals, WebSocket updates, phone-width layout.
7. **Performance and outcomes.** Outcome worker, equity snapshots, SPY benchmark, raw vs adjusted P&L, performance screen.
8. **Hardening.** Logging and rotation, daily database copy, error kill switch, startup recovery after a crash mid-cycle, README for running locally. *Done when:* a crash mid-cycle recovers cleanly.
9. **Reviews.** Weekly review, daily briefing and verdicts (see Reviews).
10. **Settings and API keys.** Settings screen redesign (see Settings screen) and keys entered in Settings (see API keys), including the one-time import from `.env`, plus the dashboard risk budget card, cycle counter, daily briefing card and model call counter (see Risk budget card, Cycle counter, Daily briefing card, Model call counter) and notifications (see Notifications). If this makes PR 10 too large, notifications may follow as their own PR, still before the paper run. *Done when:* the app runs with an `.env` that has no keys, and the 4-week paper run can start.
11. **Pool and liquidity screen (v0.2).** `universe_pool` and `universe_screens`, pool loading from `pool.csv`, add/import/remove, warm-up, daily screen with hysteresis, rank, universe mode in Settings, Universe screen. Default mode stays `fixed`. *Done when:* in simulated mode the screen over the 72-symbol pool gives the expected statuses, and fixed mode is unchanged.
12. **Cycles at scale (v0.2).** Cycles over eligible plus held symbols, multi-symbol bar requests, model calls in rank order, cycle duration and warning, 5-min bar pruning, dashboard watchlist filters and search. *Done when:* a simulated session with the 72-symbol pool keeps every cycle under 10 minutes and under 150 Alpaca calls per minute, and every held position is handled.
13. **Strategies and shadows (v0.2).** `strategies`, shadow runner after each live cycle, virtual portfolios and fills, rules-only baseline, shadow cost cap, comparison and disagreements, Promote to live via Settings, Strategies screen. *Done when:* in simulated mode two shadows and the rules-only baseline run alongside live for a full session without delaying any live cycle, and a test proves the shadow code cannot reach the broker.
14. **Backtesting (v0.2).** Backtest worker on the live cycle code with the fake broker, per-run database, historical data download within its own rate limit, lookahead guards, simulated fills, model-cutoff check, answer cache, estimate and cost cap, results and Backtest screen. *Done when:* a rules-only backtest over 3 months of cached bars gives the same trades twice, a live cycle starts on time while a backtest runs, an LLM backtest before the model's cutoff is refused, and a lookahead test passes.
15. **Integrity fixes.** Usable quotes and quote provenance; sizing on the final price; sector resolver; daily-loss latch; tighten-stop check; R from the initial stop and the original thesis for held positions; stop history for first-touch outcomes; news completeness; briefing status facts; missed-cycle reasons; cost source; backtest database errors. *Done when:* each defect from the 22 October analysis has a test that fails before the fix and passes after it, and none of the risk limits changed.
16. **Readiness.** Daily readiness snapshot after the close, the 15 criteria (two log criteria follow in PR 20) with minimum samples and estimates, error resolution (auto after 1 hour, dismiss with a note), manual items with notes, history, Settings → Readiness thresholds, "Readiness changed" notification, Readiness screen. *Done when:* the snapshot for a fixture run gives the expected status for every criterion, including "waiting for data" below each minimum sample, and nothing in the app can enable live trading.
17. **Dashboard and interaction polish.** Dashboard at scale and off-hours (session day, latest-decision fallback, feed grouped per cycle, symbol picking and `?symbol=`, rank only in screened mode, linked tiles, approval expiry) and the interaction rules on every screen (focus, hover, card states, backend banner, live regions, toasts, ARIA patterns, latest request wins, numeric API values, settings-driven copy, page title). Independent of PR 16. *Done when:* with the 71-symbol screened pool the dashboard has no symbol button wall and the feed for a full session fits in collapsed cycles, the dashboard on a Saturday shows Friday's session, and every screen passes a keyboard-only walkthrough with visible focus.
18. **LLM call history.** `llm_calls` table with backfill, outcome and reasons at write time, list, summary, detail and CSV endpoints with keyset paging, filters in the URL, LLM inspector rebuilt with filters, summary, per-session list and error details; dashboard, feed and decision links updated. After PR 17. *Done when:* every model call since the start can be found by period, outcome and source; the backfill gives each existing call the outcome the spec rules give; a timeout and an HTTP 503 show as errors with their details; list and summary answer within 300 ms on a 100,000-call fixture.
19. **Tail metrics.** After-exit job and `positions` columns, the tails function (R distribution, largest win and loss, top 20% share, net without the best trade, winners kept, after-exit medians per exit reason), `/api/performance/tails`, "Winners and tails" card on Performance. Measures only; exits and prompts don't change. Independent of PR 16–18. *Done when:* on a fixture run every metric matches a hand calculation, a trade inside its 5-session window shows pending, a symbol outside the bar cache is fetched, and the card shows the small-sample banner below 20 closed trades.
20. **Logging, error handling and shutdown.** Audit report first; JSON log file with rotation and scrubbing, levels per the table, DEBUG and INFO lines in every component, tracing ids; no swallowed exceptions (ruff rules, global hooks, `spawn()`, API `error_id`, frontend error reporting and error boundary); graceful shutdown on Ctrl+C and SIGTERM with unclean-stop detection; log index, logs API and Logs screen with error reviews (`log_reviews`); the two log criteria on Readiness; shutdown banners. After PR 17. *Done when:* the audit's findings are fixed or listed as questions; one cycle can be followed from schedule to order by its id at DEBUG; `make lint` fails on a swallowed exception; Ctrl+C during a cycle, an order submission, a backtest and a backup leaves `PRAGMA integrity_check` ok with no half-written rows; the Logs screen opens on warnings and up, and an unreviewed error in the last 10 sessions keeps Readiness at "not yet".
21. **Decision v2: code-built options.** Level finder (1h and daily swings, session levels), entry option builder and held stop options, `constraints` in the snapshot, `prompts/decision.v2.md` with its strict schema, option and explanation checks, order-time recheck of the chosen option, `no_options`, storage fields, v2 in strategies, shadows and backtests, `make replay` with its report, v2 decision drawer. Live stays on v1. Needs PR 13 and PR 15; independent of PR 16–20. *Done when:* the builder gives the expected options on fixture bars (including no options, merged stops and targets, and levels in the way), no offered option ever breaks a rule, an option id that wasn't offered becomes skip, the replay report runs on the current run's snapshots, and a v2 shadow runs next to live.

## Reviews

The model writes a weekly review, a daily briefing and a draft verdict on each decision, so the 4-week paper run can be read without digging through the log. Code computes every fact; the model only writes sentences about them. Reviews never change settings, prompts or trading. Built as PR 9, before the 4-week paper run.

| Feature | When | Content |
| --- | --- | --- |
| Weekly review | Saturday 10:00 Amsterdam, covering Monday to Friday | Return, difference with SPY, trades won and lost, average R, max drawdown, model cost, best and worst trades, what skipped setups did after 5 sessions, confirmed verdicts, results by regime and symbol. Model text: a summary of at most 120 words, up to 3 observations and at most 1 suggestion, marked "not applied" |
| Daily briefing | Trading days, 09:00 New York (15:00 Amsterdam most of the year); can be turned off | Open positions with stop distance, earnings today or tomorrow for held and watched symbols, market regime, yesterday's result, active banners. At most 80 words |
| Decision verdict | Once a decision's 5-session outcome is in | For every enter and model skip (not rules no_calls): a draft verdict of good call, bad call or unclear, with a one-line reason. Marc confirms or changes it; only confirmed verdicts count in reviews |

**Number check.** Every number in the model's text must match a fact it was given (after rounding). Status facts (open positions, earnings today and tomorrow, active banners) are written by code as a fixed line, not by the model, and the model's text may not call a known-empty fact "unavailable". If one doesn't, or there is no OpenAI key, the call fails or the review cost cap is reached, the app shows a plain template built from the facts instead, labelled with the reason (for example "Plain review: number check failed"). The failure is logged.

**Model and cost.** Same model as decisions (gpt-4o-mini), temperature 0.2, with versioned prompts (`weekly.v1`, `briefing.v1`, `verdict.v1`) that are never edited once used. Review calls have their own monthly cap of $2 and can never use budget or rate limits needed by trading decisions. Their cost also comes from `model_prices` and counts only toward the review cap.

**Missed runs.** Unlike cycles, a review or briefing missed while the app was down is written at the next start, labelled with its original date. A briefing older than its trading day is skipped.

**Storage.** A `reviews` table (kind, period, facts JSON, text, source model or template, reason, model, prompt version, tokens, cost, created at) and a `verdicts` table (decision, verdict, reason, source draft or Marc, created and updated at).

**GUI.** A Reviews screen in the Learn group with Weekly and Briefings tabs and a "Verdicts to confirm" list. The decision drawer shows the verdict with Confirm and Change. The Dashboard shows today's briefing (see Daily briefing card).

## Larger universe (v0.2)

Watch more of the market without taking more risk: risk limits, gates and the 10-call cap stay the same; only the set of symbols the agent looks at grows. Built as PR 11 and PR 12.

**Two modes.** `universe.mode` is `fixed` (the 10-symbol list from v0.1, the default) or `screened` (a daily liquidity screen over a pool). Settings → Trading → Universe switches between them. A switch is a new settings version and applies at the next daily screen, never mid-session. Switch at the start of a new run so the v0.1 paper results stay comparable. In `fixed` mode everything behaves exactly as in v0.1. Until PR 12 wires cycles to the eligible list, Settings and startup refuse `screened` ("Screened pool arrives with PR 12"); the daily screen still runs and the Universe screen shows its result as a preview that cycles don't use yet.

**Pool.** `backend/universe/pool.csv` (symbol, sector) holds a default pool of 72 large US stocks and ETFs with sectors from the fixed list. It is loaded into `universe_pool` the first time screened mode starts; after that the pool is edited on the Universe screen (add one symbol with its sector, import a CSV of symbol and sector, remove) and the file is only the defaults. At most 150 symbols. Adding checks what it checks today (active, tradable US stock or ETF at Alpaca, sector required) and the symbol warms up until its history is loaded.

**Daily screen.** On trading days at 08:10 New York, after the daily bars refresh, every pool symbol is checked on completed sessions:

| Check | Default | Fails as |
| --- | --- | --- |
| History loaded | 260 daily bars and 60 sessions of 5-min bars | Warming up |
| Active and tradable at Alpaca | yes | Excluded |
| Last close | ≥ $10 | Excluded |
| One share fits in max position | price ≤ max position size × equity at screen time (fixed by the spec) | Excluded |
| Average dollar volume (close × volume) | ≥ $100M over the last 20 sessions | Excluded |

A symbol that passes every check is eligible. An eligible symbol stays eligible until it fails 3 screens in a row; a new or warming-up symbol becomes eligible on its first pass. The eligible list is fixed for the whole session. If the screen itself fails (data error), the previous list is kept and a banner says so. Held symbols are always handled by cycles, position checks, exits and earnings exits, whatever their screen status; a held symbol removed from the pool leaves it when the position is closed.

**Rank.** At the screen, each eligible symbol gets a rank: its return over the last 20 completed sessions minus SPY's over the same sessions (ties broken by dollar volume). The rank is shown on the Universe screen and the dashboard and is part of the input snapshot.

**Cycles at scale.** Cycles run over the eligible symbols plus held symbols. 5-min bars are fetched with multi-symbol requests; quotes and news only for symbols that get a model call. Model calls go to held positions first, then to candidates that pass the rules in rank order, up to `max_calls_per_cycle` (10); the rest are stored as `no_call` with reason `call_cap`. The shared limit of 150 Alpaca calls per minute still applies. Each cycle's duration is stored; a cycle longer than 10 minutes shows a warning banner. 5-min bars older than 120 sessions are deleted after the 03:00 backup.

**GUI.** A Universe screen in the Control group: mode, counts (pool, eligible, held, excluded, warming up), today's screen as a funnel, and the pool table (rank, symbol, sector, price, 20-session average dollar volume, return vs SPY, status with reason) with filters, search, add, CSV import and remove. Statuses: eligible, eligible with 1 or 2 fails, held, excluded with the failed check, warming up. The dashboard watchlist gets filters (All, Pass rules, Held, Earnings within 3 days) and search; in screened mode it is sorted by rank and shows 15 rows, then "Show all". Settings → Trading gets the universe mode and the screen thresholds. In fixed mode the Universe screen shows the fixed list and points to Settings.

## Strategies and shadows (v0.2)

A shadow strategy decides on exactly the same inputs as live, every cycle, but never trades. It answers questions like "would another prompt, a bigger model, or no model at all have done better?" with evidence from the same market, instead of opinions. Built as PR 13.

**Strategies.** A strategy is a named configuration: kind (`llm` or `rules_only`), prompt version, model, temperature, and overrides of the entry rules and code checks (RSI range, minimum confidence, stop distance range, minimum reward to risk). Risk gates, sizing, the day-trade rule and earnings handling can never be overridden; every strategy runs under the live limits. Live is the strategy defined by the current settings version. `rules_only` enters whenever the rules pass, with the stop at entry − 2 × ATR(1h) and the target at 2R, and exits only through its stop, target or the earnings exit.

**Running shadows.** At most 3 shadows are active or paused; archiving frees a slot. A paused shadow makes no model calls and no entries but keeps managing its virtual positions (stops, targets, earnings exits). After each live cycle has finished and its orders are sent, every active shadow runs on that cycle's stored input snapshots: the market part is reused as it is, and the position part is rebuilt from the shadow's own virtual portfolio. Calls go to the shadow's held positions first, then to candidates that pass its rules in rank order, up to the same 10-call cap. Shadow work never delays live: if the next live cycle or position check is due, unfinished shadow work for that cycle is skipped and recorded as missed, with the reason (`time` or `cost_cap`). Live's cycle also builds snapshots for symbols held by any active or paused shadow, so a shadow can decide on them; live makes no rules check, model call or decision for a symbol only a shadow holds.

**Virtual trading.** Each shadow has a virtual portfolio that starts with the live run's starting equity when the shadow starts. Shadow orders are timed at the live cycle's scheduled time. Entries use the same limit price and timeout as live and fill when a later 5-min bar trades at or through the limit, at the limit or at a better bar open. The stop is already checked on the fill bar (if its low reaches the stop, the stop counts); the target counts from the next bar. Stops and targets are checked on 5-min bars at every position check and cycle; when both are touched in the same bar, the stop counts; a gap through the stop fills at the open. Market exits (model exit, earnings exit, Stop & flatten) fill at the next 5-min bar's open plus slippage. When live fetched a market price and quote for that symbol and cycle, the shadow uses them under the same rules as live (a price or quote that blocks live blocks the shadow). When live fetched none, the shadow uses the last closed 5-min bar's close as its market price, marked `synthetic`: entry limit = close + 0.05%, stop at least 1.5 × ATR(1h) below it; the bar and ATR are required (`reference_missing` otherwise). Shadows place no real orders and skip the second, order-time fetch; this slightly favours shadows, and the Strategies screen shows how many shadow entries used a synthetic price. Slippage per side, dividends from corporate actions, the day-trade rule and earnings exits apply as for live. While live can't place new entries (paused, kill switch on, unacknowledged reconciliation mismatch), shadows can't either, recorded under the same gate name; they keep managing their virtual positions. Stop & flatten also closes every shadow's virtual positions at the next 5-min bar's open plus slippage. All other gates (risk per trade, open risk, positions, sectors, daily loss, losing streak, drawdown, day trades) are computed on the shadow's own virtual portfolio; the shock brake is market-wide. The shadow code has no broker client at all; a test fails the build if it can reach one.

**Cost.** Shadow model calls have their own monthly cap ($5). When it is reached, LLM shadows pause until the next month (rules-only shadows keep running) and a warning notification is sent. Shadows never use live call budget or rate limits needed by live. Shadows and backtests can use other models than live, so PR 13 makes the `model_prices` block in `config.yaml` (input and output USD per million tokens, keyed by model) the only source of prices and removes the `decision` and `reviews` `*_cost_per_mtok` keys; every cost (decisions, reviews, shadows, backtests) is computed from it. Startup refuses to start if the live, reviews or an active shadow's model has no price; Settings refuses such a model change; New shadow and the backtest strategy picker only offer priced models. Each model call stores its own USD cost when it is made, so a later price change never rewrites past costs.

**Comparing.** For the current run, per strategy: adjusted return, difference with SPY, closed trades won and lost, average R, max drawdown, model cost, and "same as live". Live's return here is Performance's adjusted P&L (slippage per side and dividends) ÷ starting equity, from the same function, so it is like-for-like with shadow fills; hovering shows live's raw paper return. "Same as live" is the share of counted symbol-cycles where the final action matched live, with all `no_call` reasons treated as one action. A symbol-cycle counts only if at least one side did more than a rules `no_call` (a model call, a rules-only entry, a held-position decision, or a call-cap or quote `no_call`); missed shadow work is left out. It is shown with its count, for example "41% of 212". The disagreements list shows each symbol-cycle where a shadow and live chose differently, with what happened after (the 5-session outcome, or the trade's result). The weekly review's facts include this comparison. A new run restarts every shadow.

**Promote to live.** Only possible once the shadow has at least 20 closed trades. It opens Settings with the shadow's differences filled in as a new settings version, which Marc saves with a reason like any other change; the shadow is then archived. Nothing is promoted automatically.

**GUI.** A Strategies screen in the Learn group: the comparison table (live, shadows and baselines), a warning while samples are small, equity curves with SPY, the selected strategy's configuration (what differs from live) with Pause, Archive and Promote, and the disagreements list. "New shadow" opens a dialog to name it, pick the kind, prompt version, model and temperature, and set rule overrides.

## Backtesting (v0.2)

A backtest replays strategies over past sessions with the same code that trades live, so results are comparable with the paper run. Built as PR 14.

**Engine.** The cycle, rules, risk gates, sizing, position checks and outcome tracking are the live code, driven by an injected clock over historical sessions and the fake broker from simulated mode. Each run has a folder `data/backtests/run-<id>/` with one full live-schema database per strategy (`<strategy-slug>.db`, created from the same migrations, each driven by its own engine instance) and a read-only `market.db` with the run's earnings, news and corporate actions; the live schema gets no strategy column. Strategies advance in lockstep, session by session on one clock, so progress, cancel and the cost cap stop them all at the same point. The live database only gets the `backtests` summary row; deleting a run removes its folder and that row, and the runs list shows each run's size on disk. Backtests never touch the Alpaca account.

**Process.** A run is a separate child process started by the app, one at a time, at lower CPU priority, so it never competes with live jobs for the Python interpreter. It reports progress, cost and status on the `backtests` row (committing after each update). Cancel sets a flag the child checks every session; if it hasn't stopped within 60 seconds, the app stops it by its PID. While a live cycle or position check runs, the app sets a pause flag that the child checks before every Alpaca download and every model call. On app shutdown the child is cancelled first; a run still marked running at startup becomes "interrupted" and keeps its results so far.

**Data.** Historical bars live in a separate store, `data/backtests/bars.db` (the `bars_5m` and `bars_1d` schema, split-adjusted, never pruned automatically), so the live cache keeps its 120-session limit. Before a run, the worker fills the period plus the indicator lookback (`schedule.history_sessions` and `daily_history_sessions` before the first session) into the store, first copying what the live cache has, then downloading the rest from Alpaca at most 40 calls per minute (live keeps its 150; Alpaca allows 200 per account), pausing during live cycles and checks. During a run, bars are read only from the store, read-only, never from the live database. The Backtest screen shows the store's size; "Clear bar store" is only possible when no run is active. The 03:00 backup covers the live database only. News comes from Alpaca's historical news, earnings dates from Finnhub's calendar history. Every input is cut at the decision time with the same delays as live (bars closed at least 16 minutes earlier, news published before the decision); tests check that no later data leaks in. Days with missing earnings data block entries for that symbol, and the results say so. There are no historical quotes on the free data plan, so the spread is assumed (0.05%) and the limit price uses the last 5-min close.

**Fills.** Bracket orders are simulated on 5-min bars: an entry fills when a bar trades at or through the limit before the entry timeout; stops and targets are checked on every bar; when both are touched in the same bar the stop counts. Slippage per side applies (0.05% by default, editable per run). Commission is zero, as at Alpaca.

**Model calls.** An `llm` strategy can only be backtested from the day after its model's knowledge cutoff (from `backtest.model_cutoffs`); an earlier start is refused before the run begins, because the model may already know what happened. Answers are cached by model, temperature, prompt version and hash, and input snapshot hash, so a rerun on the same inputs costs nothing and gives the same result. Live and shadows only write to the cache and never read from it, so a live decision is always a fresh model call; a backtest decision answered from the cache is marked `cached`. Before a run starts the screen shows the number of sessions, bars to download, estimated model calls, estimated cache hits and estimated cost, plus the cost at the 10-call cap. Calls per LLM strategy = sessions × cycles × that strategy's average real calls per cycle in the current run (a shadow uses its own average once it has at least 20 cycles, otherwise live's; with no history the 10-call cap); rules-only is 0. Cache hits = `llm_cache` rows with the same model, temperature and prompt hash whose cycle time falls in the period, labelled "≈". Cost = (calls − cache hits) × the strategy's average tokens per call × `model_prices`. Run is disabled when the expected cost is above what is left of the monthly cap; when only the cost at the cap is above it, a warning says the run may stop early. Backtests have their own monthly cap ($20); a run that reaches it stops cleanly and keeps its results so far.

**Runs.** A run has a period (at most 3 years), the universe (fixed list, or the screened pool applied day by day with only the data available that day, flagged for survivorship bias because the pool is today's list), the strategies to compare (live settings, any shadow, rules only), starting equity and slippage.

**Results.** Per strategy: return, difference with SPY buy and hold over the same sessions, max drawdown, closed trades, win rate, average R, profit factor and time in market; equity curves with SPY; results by market regime and by symbol; the trade list; and every decision, opened in the same decision drawer as live. A "Read with care" list names every assumption the run relied on (assumed spreads, missing earnings days, same-bar stop and target, cache hits, survivorship). CSV export of trades and decisions. A backtest is evidence about the past, never a forecast; the screen says so and the weekly review never cites a backtest as a reason to change anything.

**GUI.** A Backtest screen in the Learn group: the new-backtest form with quick ranges, strategy picker, the estimate and the cutoff check; the runs list with status, progress, cost and cancel; and the selected run's results.

## Readiness

A Readiness screen in the Control group shows whether the paper evidence would justify live trading. Live trading isn't built and nothing unlocks automatically: every criterion passing and every manual item done is the condition for starting the separate live-trading spec, not permission to trade. Built as PR 16.

**Scope.** All evidence comes from the current run (since "Start new run"), using adjusted P&L. The criteria are evaluated after each session's close and stored as a daily snapshot; the screen reads the latest snapshot and never recomputes per request. Each criterion has a status: pass, not yet, waiting for data (its minimum sample isn't reached, so it can't pass or fail yet) or to do (manual items).

| Group | Criterion | Default | Minimum sample |
| --- | --- | --- | --- |
| Evidence | Closed trades | ≥ 100 | — |
| Evidence | Paper sessions in this run | ≥ 60 | — |
| Evidence | Adjusted return minus SPY buy and hold, same sessions | > 0 | — |
| Evidence | Max drawdown | under 6% | — |
| Evidence | Average R (adjusted) | ≥ +0.2R | 30 closed trades |
| Evidence | Profit factor (adjusted) | ≥ 1.3 | 30 closed trades |
| Evidence | Live average R above the rules-only shadow's, same sessions | live > rules-only | 30 closed trades each; the rules-only shadow must have been active since the run started |
| Model | Share of the last 100 model entry proposals that pass the code checks (under decision.v2: valid option, explanation check and order-time recheck; see Decision v2) | ≥ 75% | 30 proposals |
| Model | Confidence calibration: per 0.1 confidence group with at least 10 filled entries, the gap between stated confidence and actual win rate, averaged weighted by group size | within 10 pts | 50 filled entries |
| Operations | Scheduled cycles completed, last 20 sessions | ≥ 98% | — |
| Operations | Times a position had no live stop at Alpaca for longer than one position check, last 30 sessions | 0 | — |
| Operations | Kill switch events, last 30 sessions | 0 | — |
| Operations | Unresolved errors that affected a decision, order or fill, last 10 sessions | 0 | — |
| Operations | Errors in the log: ERROR problems (grouped by fingerprint) in the last 10 sessions that are not reviewed as expected or fixed (from PR 20) | 0 unreviewed | — |
| Operations | CRITICAL lines in the log, last 20 sessions (from PR 20) | 0 | — |
| Operations | Candidate checks blocked as `price_stale` or `price_suspect`, last 20 sessions | ≤ 5% | — |
| Operations | Unacknowledged reconciliation mismatches, now | 0 | — |

**Counts and dates.** For counts that aren't reached yet, the screen shows an estimate at the current pace (closed trades per session) and, for sessions, the date from the trading calendar. "Earliest possible" is the latest of those estimates.

**Errors.** An error resolves itself after 1 hour without recurring, or Marc dismisses it with a note from its banner or the events list. Only errors linked to a decision, order or fill count for this criterion.

**Errors in the log** (from PR 20).
- **Why a review and not "zero ERROR lines".** Some errors come from outside and are handled as specified (an OpenAI outage, a mail server refusing a login). Zero ERROR lines would never pass, so every recurring error problem needs a review instead.
- **How.** On the Logs screen, under Recurring problems, Marc marks a problem **Expected** (an outside failure handled as specified; it stays reviewed if it recurs) or **Fixed** (the cause is solved; if it happens again after the review, it is unreviewed again). Either way he adds a note.
- **Criterion.** It passes when no ERROR problem with a line in the last `readiness.log_error_sessions` (10) sessions is unreviewed.
- **Critical lines.** They can't be reviewed away: any CRITICAL line in the last `readiness.log_critical_sessions` (20) sessions fails its criterion.
- **Source and safety.** Both are counted from the log index when the daily snapshot runs. If the index is unavailable or incomplete for the window, both criteria show "not yet" with that reason, never pass.
- **History.** Reviews show in Readiness History with their note.

**Manual items.** Real-time SIP market data subscribed; Dutch tax treatment checked with an advisor; live-trading spec written and reviewed; hosting with login (VPS); first live size and loss limit decided. Ticking or unticking an item asks for a short note, stored with the date.

**History.** Every change of a criterion's status and every manual item change is listed with its date. Thresholds live in Settings → Readiness; changing one is a settings change with a reason and shows in History too. The zero-tolerance criteria and the manual items are fixed by the spec. A new run resets the evidence.

**Notifications.** A new Info event "Readiness changed" (browser on, email off) when a criterion passes or stops passing.

**GUI.** A verdict banner ("Not ready for live trading" until everything passes), summary tiles (pass, not yet, waiting, manual items, earliest possible), the Evidence, Model and Operations groups with current value, target, a progress bar for counts and the status, the manual items, and History. Every criterion explains how it is measured on hover and links to the screen with the details.

## LLM call history

The LLM inspector shows every model call ever made, not just today's, and can filter them by date, outcome (success, warning, error), source and a few more fields. Built as PR 18, after PR 17.

**What counts as a call.** One request actually sent to OpenAI by live decisions, shadows or reviews. Nothing that wasn't sent appears: rules no_calls, the call cap, `price_stale` or `price_suspect`, a missing key, a reached cost cap, missed shadow calls. Those stay visible as decisions or review labels. Backtest calls stay in their run's database and are shown on the Backtest screen.

**Outcome.** Set once, when the call and its linked row are stored, and never recomputed. Later events (an approval expiring, an order not filling) don't change it. An error outranks warnings; a call can have several warning reasons.

| Outcome | When | Reasons |
| --- | --- | --- |
| Success | A valid response used as the model chose; for reviews, the number check passed | — |
| Warning | A valid response, but something needs a look | `changed_by_code` (the final action differs from the model's: a code check, risk gate or deferred exit), `input_incomplete` (an input field was `"unavailable"` or the symbol's headlines were `truncated` or `failed`), `number_check_failed` (review text failed the number check; the plain template was used), `slow` (latency above `inspector.slow_call_seconds`, 15), `unsupported_claim` (decision.v2: the explanation check failed) |
| Error | No usable response | `timeout` (30 s, not retried), `api_error` (HTTP error or connection failure; status and OpenAI error code stored), `invalid_output` (unparseable, fails the schema or Pydantic, or picks an option that wasn't offered; counts as skip or hold), `refusal` (the structured-output refusal field is set) |

A size reduced by a gate is not a warning; the model's action was kept.

**Storage.** A new `llm_calls` table, one row per call:
- What and where: sent at, New York session date, source (live, shadow, review), strategy (shadows), kind (decision, briefing, weekly, verdict), the linked decision, shadow decision or review, cycle, symbol, context.
- How: model, temperature, prompt version and hash, tokens in and out, cost and its source, latency in ms, final action.
- Result: outcome, reasons JSON, error type, HTTP status, error message, OpenAI request id, raw response text.
- The row is written in the same transaction as the linked row, after the call has returned (hard rule 13). Error messages go through the same key scrubbing as logs; request headers are never stored.
- The prompt is not duplicated. The system part is rebuilt from the versioned prompt file; if its hash differs from the stored hash, the screen says so instead of showing the text. The user part comes from the linked row's input snapshot (decisions, shadows) or facts JSON (reviews).
- Indexes: (session date, sent at), (outcome, sent at), (source, sent at), (symbol, sent at).
- Never pruned. At roughly 1 KB per call, a year of live, shadow and review calls stays in the tens of MB.

**Backfill.** The migration fills `llm_calls` from existing `decisions` (rows with a model output or reason `model_error`), `shadow_decisions` (rows with a model output) and `reviews` (source model, or template after a failed call). Outcome and reasons come from the stored validation result, final action, headline status and number check. Latency, HTTP status and request id are null and shown as "not recorded"; such rows are marked `backfilled`.

**Filters.** Every filter is in the URL (`?from=&to=&outcome=&source=&symbol=&action=&reason=&model=&prompt=&strategy=&cycle=&call=`), so links land on the right calls:
- The dashboard's model-call tile opens Last session with live decisions.
- A grouped feed row opens its cycle and action.
- The decision drawer's "Open model call" opens `?call=`, as does Copy link.
- The navigation opens the defaults.

| Filter | Options | Default |
| --- | --- | --- |
| Period | Last session, 5 sessions, 20 sessions, All, Custom (from and to) | Last session |
| Outcome | All, Success, Warning, Error (one) | All |
| Source | Live decisions, Shadows, Reviews (one or more) | Live decisions and Reviews; shadows are off because they triple the volume |
| More | Symbol, action, reason, model, prompt version, strategy (with Shadows on), cycle (only from links) | none |

- **Dates.** Periods use New York session dates. A call made outside a session (the Saturday weekly review, a verdict after the close) belongs to the New York date it was sent.
- **Counts.** Every option shows its count under the other filters.
- **Clear filters** returns to the defaults.
- **Phone.** At phone width the "More" filters sit behind a "More filters" button that says how many are set.

**Summary.** For the filtered set:
- Calls, success, warning and error with their share.
- Cost, split by source.
- Latency median and p95, counting only calls that returned.
- A stacked bar per session (per week above 20 sessions). Clicking a bar sets the period to that session.

**List.** Newest first, with a switch to oldest first. Calls are grouped per session under a sticky header (calls, warnings, errors). A row shows:
- the time (New York on hover);
- the symbol, or the review kind;
- the final action;
- the context or review prompt, and the model;
- an outcome chip naming the reasons.

Pages of `inspector.page_size` (50) use a keyset cursor on (sent at, id), with "Load more" and "n of total loaded". Selecting a row shows its details; on phone the details open below the list and "Back to list" returns to the same place.

**Details.**
- **Outcome:** a block with the chip, a one-line result, the reason codes and an explanation (for example "reward to risk computed by code is 1.3, below 1.5").
- **Call data:** source, model and temperature, prompt version, tokens, cost and latency.
- **Links and copy:** a link to the decision, shadow strategy or review, and Copy link.
- **Prompt and response:** the prompt (system, then user), then the raw response.
- **Errors:** instead of the raw response, an Error block with the type, HTTP status, OpenAI's message and the request id.

**Export CSV.** The metadata of the filtered calls: time, source, strategy, symbol, action, outcome, reasons, model, prompt, tokens, cost and latency. No prompts or responses.

**API.**
- `GET /api/llm-calls` (filters, cursor, limit up to 100) returns the page and the next cursor.
- `GET /api/llm-calls/summary` (same filters) returns totals, per-session buckets and the option counts.
- `GET /api/llm-calls/{id}` returns the full call with the rebuilt prompt.
- `GET /api/llm-calls/export.csv` returns the export.
- Each list and summary request must answer within 300 ms on a 100,000-call fixture.

**GUI.** The LLM inspector screen keeps its place in the Learn group. It has filters, the summary, the list and the details, and follows the PR 17 interaction rules (toggle buttons with `aria-pressed`, card states, latest request wins).

## Tail metrics

Trend-following results usually depend on a few large winners. Fixed targets can cut those winners off, which is why the Performance screen gets a "Winners and tails" card. It shows whether the run's results lean on a few trades and whether fixed targets cut winners short. It measures and changes nothing: exits, prompts and targets stay as they are. Whether to test another exit style is decided later, from these numbers. Built as PR 19.

**Scope.** Closed trades of the current run, using adjusted P&L. 1R is the trade's initial risk: (average entry − initial stop) × quantity. Adopted positions without an initial stop are left out, and the card says how many. The numbers come from one function in the performance module, so Strategies and backtest results can reuse it later; PR 19 only shows them for live.

| Metric | Definition |
| --- | --- |
| R distribution | Closed trades counted in buckets of `performance.tail.bucket_r` (0.5R), from below −1.5R to 3R or more; each bucket includes its lower edge |
| Largest win, largest loss | Best and worst closed trade in R, with symbol, exit reason and dates. A loss below −1R means the exit filled worse than the stop |
| Top 20% of trades | The best `ceil(20% × trades)` closed trades by adjusted P&L, as a share of the summed P&L of all winning trades. Hidden while there are no winners |
| Net without the best trade | Adjusted net P&L of closed trades with the single best trade removed |
| Winners kept | Per winning trade, realized R ÷ the best R reached while open (from `outcomes`), averaged |
| Best after exit | Per closed trade: (highest high in the `performance.tail.after_exit_sessions` (5) sessions after the exit fill − exit price) ÷ initial risk per share |
| Close after exit | Per closed trade: (close of the 5th session after the exit − exit price) ÷ initial risk per share |

**After the exit.** Medians of best after exit and close after exit per exit reason (target, stop, agent exit, earnings exit, Stop & flatten), plus for target exits the share whose best after exit reached 1R or more. A table lists each closed trade with its result, best R while open, best after exit and close after exit. A trade still inside its 5-session window shows "pending" and isn't counted yet. Best after exit is an upper bound, because nobody sells at the top; the close is the realistic view, and the screen labels both.

**Computing the after-exit values.** A job runs once per trading day after the close (after the readiness snapshot). It fills the values for every closed position whose 5th session after the exit has closed. It uses 5-min bars from the exit fill time to that session's close, split-adjusted with `corporate_actions`; dividends are ignored. If the bars aren't in the cache (for example the symbol left the universe), the job fetches them through the shared Alpaca limiter. A failed fetch is retried the next day; after 3 failures the values are stored as unavailable and shown as "—". Once filled, the values never change. The job doesn't hold a write lock during a fetch (hard rule 13).

**Small samples.** Under `performance.tail.min_trades` (20) closed trades, the card shows "n of 20 trades" with the note that one trade can swing every number. The values are still shown. The R distribution and the per-trade table are always shown.

**Storage.** `positions` gains `after_exit_best_price`, `after_exit_best_at`, `after_exit_close_price`, `after_exit_status` (pending, done, unavailable) and `after_exit_done_at`, in a new migration. Existing closed positions are filled by the first job run, as far as the bars allow.

**API.** `GET /api/performance/tails?run=` returns the distribution, the summary values with their sample counts, the after-exit medians per exit reason and the per-trade rows, all as numbers.

**GUI.** The "Winners and tails" card on Performance, below By regime and By symbol. It has:
- a small-sample banner;
- the R distribution as bars (losses red, wins green);
- tiles for largest win, largest loss, top 20% share, net without the best trade and winners kept;
- "After the exit" with medians per exit reason;
- the per-trade table;
- a one-line "How to read it".

Every tile explains its definition on hover.

## Logging, error handling and shutdown

Every part of the app logs what it does, so a cycle, an order or a failure can be traced afterwards. No error or warning is swallowed. A Logs screen shows the log with warnings and errors first. Ctrl+C stops the app cleanly without damaging the database. Built as PR 20, which starts with an audit of the existing code.

**Log file.**
- **Format.** One JSON object per line in `data/logs/stock-agent.log`. Fields:
  - `ts` (UTC, milliseconds), `level`, `logger` (the component, for example `data.prices`), `event` (a stable code such as `prices.stale`), `message`;
  - `context` (cycle id, symbol, decision, order, notification, run or HTTP request id, whichever apply);
  - `process` (`app` or `backtest-<id>`), `pid`;
  - for errors: `error_id` and `exception` (type, message, traceback).
- **Levels.** All five levels go to the file (`logging.file_level`: DEBUG). The console shows INFO and up (`logging.console_level`) in a short readable format.
- **Writing.** Through a queue: the event loop puts a line on a queue and one listener thread writes it, so logging never blocks a cycle.
- **Rotation.** At `logging.max_file_mb` (50 MB) the file rotates and the old one is gzipped. Files older than `logging.keep_days` (30) or beyond `logging.max_files` (20) are deleted.
- **Process logs.** A backtest child process writes its own file, `data/logs/backtest-<id>.log`, in the same format.
- **Scrubbing.** A filter on every handler replaces the value of every stored key and anything that looks like a key or an Authorization header with `[redacted]`. Model prompts and responses are not logged; they live in `llm_calls`.

**What each level means.** The audit and every later PR follow this table. Every component logs its steps at DEBUG and its milestones at INFO, so a cycle can be followed from schedule to order.

| Level | Meaning | Examples |
| --- | --- | --- |
| DEBUG | The steps and values behind a decision | Bars fetched per symbol and their last time, each rule check with its value, snapshot hash, gate inputs and results, sizing math, model call sent and returned (tokens, latency), API calls with status and duration (no bodies), limiter waits, WebSocket topics sent |
| INFO | The workflow, one line per milestone | App start and stop with version and config hash, cycle start and end with a summary, position check summary, each decision's final action, order submitted, filled, cancelled or replaced, stop moved, settings saved, keys tested (never the key), jobs run (backup, screen, readiness, reviews, after-exit), reconciliation result, notification sent |
| WARNING | Something degraded but handled | `price_stale`, `price_suspect`, `spread_unknown`, news truncated or failed, slow or invalid model output, a fetch retried, a cycle over 10 minutes, a limiter wait over 5 s, an exit deferred, an unclean previous shutdown, a missing optional data source |
| ERROR | An operation failed and its unit of work was given up | An unhandled exception in a cycle step, an order call failing, a model error, a job failing, an email failing, a database error that was rolled back |
| CRITICAL | Safety at risk or the app can't continue | The kill switch turned on automatically, a position without a broker-side stop, a reconciliation mismatch, the database can't be written, startup refused, shutdown with an order whose result is unknown |

**Tracing.**
- Every cycle, position check and job gets an id (`c-0412`), set in a context variable. Every line written while it runs carries that id.
- Lines about one symbol carry the symbol, and the decision or order id once known.
- API requests get a request id, returned in the `X-Request-Id` header.
- One cycle can be followed end to end by filtering on its id.

**Error handling.**
- **Never swallowed.** No bare `except:`, no `except Exception: pass`, no catch that neither logs nor re-raises. A caught exception is one of three things:
  - re-raised;
  - turned into a defined outcome (for example `no_call` with `model_error`, a failed gate, a cycle stored as error) and logged with its traceback at the matching level;
  - an expected condition, logged at WARNING with its reason.
- **Linted.** Ruff enforces this with rules E722, BLE001, S110, TRY400 and TRY203, and `make lint` fails on them. A narrow exception that is deliberately allowed needs a `# noqa` with a reason.
- **Global hooks**, so nothing escapes the log:
  - `sys.excepthook` and `threading.excepthook`;
  - the asyncio loop's exception handler;
  - a single `spawn()` helper for every background task, which logs a failed task at ERROR. Bare `asyncio.create_task` outside it fails a test.
  - the scheduler's job-error listener;
  - `logging.captureWarnings`;
  - uvicorn's and the libraries' loggers routed to the same handlers.
- **API errors.** An unhandled API error returns 500 with an `error_id`; the same id is on the log line with the traceback.
- **Frontend errors.** `window.onerror`, `unhandledrejection` and a React error boundary send the error to `POST /api/logs/client`, at most 10 per minute per tab. They are logged at ERROR with logger `frontend`. The error boundary shows "Something went wrong" with the `error_id` and a Reload button instead of a blank page.
- **Logs and events.** The log is for diagnosis. Errors that need action still create `events` and notifications as specified; a log line never sends a notification by itself.

**Graceful shutdown.**
- **Steps.** Ctrl+C (SIGINT) or SIGTERM, on macOS, Linux and Windows, with `make run` and `make dev`, starts a graceful shutdown:
  1. Log "Shutdown requested", show the "Shutting down" banner in every tab, and pause the scheduler: no new cycles, position checks or jobs. API writes return 503 "shutting down"; reads keep working.
  2. Wait up to `storage.shutdown_grace_seconds` (60) for running work to finish. A cycle finishes the symbol it is on. Order submission, from sending to storing Alpaca's answer, is shielded and is never cut off halfway.
  3. Work still running after the grace period is cancelled. Its cycle is stored as `interrupted` (reason shutdown), and any order whose answer didn't arrive is left to reconciliation at the next start (hard rule 3), with a CRITICAL line naming it.
  4. A running backtest child gets its cancel flag. After 10 s it is stopped by its PID and marked `interrupted`.
  5. Pending emails get up to 5 s. WebSocket clients and the Alpaca, OpenAI and data clients are closed.
  6. Database: every session is committed or rolled back, then a WAL checkpoint (TRUNCATE) runs and the engine is disposed.
  7. The log listener is flushed and closed last. The final line is "Shutdown complete in n s".
- **Second Ctrl+C.** A second Ctrl+C logs a WARNING and skips the wait in step 2. Steps 6 and 7 always run.
- **Data stays whole.** Every unit of work writes in one short transaction: a decision with its `llm_calls` row, gates and order rows; a fill with its position update. Shutdown can't leave half of it behind. Stops at Alpaca protect positions while the app is down.
- **Unclean stop detection.** At start the app writes `data/run.lock` (pid, start time) and a heartbeat every 30 s; a clean shutdown deletes the file. If the file is still there at the next start, the app logs a WARNING and runs `PRAGMA quick_check`. It marks running cycles as `interrupted`, reconciles with Alpaca as usual, and shows "The previous run did not shut down cleanly" with the results. If the pid in the file is still running, startup is refused (CRITICAL), so two instances never share the database.

**Log index and API.**
- **Index.** The listener thread also writes each line, in batches every second, to a separate SQLite file, `data/logs/logs.db`. It has a `log_lines` table with the fields above plus a fingerprint, full-text search on the message, and indexes on (ts), (level, ts), (logger, ts), (cycle, ts) and (symbol, ts). It is never the trading database: it isn't in the 03:00 backup and is pruned with the files.
- **Index failures.** If the index can't be written, the file keeps working. The failure goes to the file and stderr at ERROR, never through the index. A rebuild from the files is possible (`make logs-reindex`).
- **Fingerprint.** Logger + event + the message with numbers, symbols and ids replaced by placeholders. Recurring problems are grouped by it.
- **Endpoints.**
  - `GET /api/logs` takes levels, from and to, search, logger, symbol, cycle, cursor and limit (up to 200), and returns newest first with a keyset cursor.
  - `GET /api/logs/summary` returns counts per level, warnings and up per day or hour, and recurring problems.
  - `GET /api/logs/{id}/around?n=50` returns the surrounding lines of the same process at all levels.
  - `GET /api/logs/export.jsonl` exports the filtered lines.
  - A WebSocket topic `logs` announces new lines at warning and up, debounced 1 s.

**Logs screen.** A Logs item in the Control group. The screen has:
- **Levels.** A toggle per level (Debug, Info, Warning, Error, Critical) with its count. The default shows Warning, Error and Critical. A "Show debug and info" switch turns those two on or off together. The choice is in the URL (`?levels=`) and remembered per browser.
- **Period.** Last hour, Today, 7 days (default), 30 days, Custom.
- **More filters.** Search on the message, component, symbol, cycle. On a phone these sit behind "More filters".
- **Clear filters** and **Download .jsonl**.
- **Warnings and errors per day.** Stacked bars; clicking one shows that day.
- **Recurring problems.** Grouped by fingerprint with level, count, first and last seen and examples. Clicking one filters the log on it. ERROR and CRITICAL problems show their review state: unreviewed, expected or fixed, with a Review button. Review opens a dialog: Expected or Fixed, plus a required note. These reviews feed Readiness (see Readiness → Errors in the log).
- **Log.** Grouped per day under sticky headers, newest first. Each line shows its time (Amsterdam, New York on hover), a level chip, the message, the component and context chips (cycle, symbol). Context chips link to the cycle filter, the decision or the order.
- **Opening a line.** It shows:
  - the exception with its traceback;
  - the stored JSON;
  - "Surrounding lines" (same process, all levels, with "Show 50 more");
  - Copy line, and a link to what it concerns.
- **Follow new lines.** On by default; it pauses while scrolled away from the top.
- **Card states.** Loading, empty and error as in the Interaction rules. With no lines in the period: "No warnings or errors in this period".

`events` and `notifications` link to the Logs screen filtered on their time (±2 minutes) and cycle.

**Audit first.**
- **Report.** PR 20 starts with `docs/analysis/<date>-error-handling-audit.md`. It lists:
  - every `try`/`except`, every background task and every place a failure could pass silently, each with file and line, what happens today and the fix;
  - every component and its current logging, and the INFO and DEBUG lines it needs to be traced;
  - shutdown behaviour today under Ctrl+C during a cycle, an order submission, a backtest and a backup.
- **Order of work.** The fixes follow the report. Anything that changes trading behaviour beyond logging and shutdown is listed as a question, not fixed.

## Decision v2: code-built options

In the 22 October analysis, 32 of 33 model entry proposals failed the code checks. Most failed the stop distance, and 21 put the stop about 1 ATR away despite the prompt's explicit 1.5–4 ATR rule. Some explanations also cited levels that didn't match the input. Decision v2 moves all trade geometry into code:
- Code finds the price levels and builds every stop and target combination that meets the rules.
- The model only judges the setup and picks one of those options, or skips.

The risk rules don't change: no threshold is lowered to let more trades through. Built as PR 21, then tested in replay and as a shadow before it can become live.

**Levels.** Computed per symbol each cycle from the stored bars and added to the snapshot as `levels`:
- **Swing lows and highs on 1h bars.** A session-aligned 1h bar whose low (high) is below (above) the lows (highs) of the `decision.options.swing_strength` (2) bars on each side, within the last `decision.options.swing_lookback_bars_1h` (60) bars. Only confirmed swings count, so the newest two bars can't be one.
- **Swing lows and highs on daily bars.** The same rule over the last `decision.options.swing_lookback_sessions` (60) sessions.
- **Session levels.** The prior session's high, low and close; today's session high and low so far; the session VWAP.
- **Selection.** At most 8 levels below the market price and 8 above, nearest first. Each has an id (L1, L2, …, numbered from the highest price down), price, kind, timeframe and time.

**Entry options (candidates).** Built after the price checks, using the market price, entry limit `L` (market price + 0.05%), ATR(1h), and the stop reference used for the order-time rule (the lowest of market price, plausible ask and last closed bar close).
- **Stops.**
  - Each swing low, prior session low and session low below the price, placed `decision.options.level_buffer_atr` (0.1) × ATR below the level.
  - Plus a stop at reference − k × ATR for each k in `decision.options.atr_stop_multiples` ([2.0]).
  - A stop is kept only when it lies 1.5–4 × ATR below the reference: the same rule as today, with the strategy's override if it has one.
  - Stops within `decision.options.dedupe_atr` (0.5) × ATR of each other are merged, keeping the level-based one.
  - At most 3 stops are kept, nearest first.
- **Targets per stop.**
  - Each swing high, prior session high and session high above `L`, placed the buffer below the level.
  - Plus `L` + r × risk for each r in `decision.options.target_r_multiples` ([2.0, 3.0]).
  - A target is kept only when reward to risk is at least `decision.min_reward_risk` (1.5). Close targets are merged the same way.
  - Each target lists `levels_in_way`: the level ids strictly between `L` and the target.
- **Option list.**
  - At most `decision.options.max_options` (6), lettered A–F: by stop, nearest first, then by reward to risk.
  - Each option has id, stop, stop_basis (for example "0.1 ATR below L8, 1h swing low 13 Oct 14:30" or "2.0 × ATR"), stop_atr, target, target_basis, rr, risk per share and levels_in_way.
  - If no option exists, no model call is made: the decision is stored as `no_call` with the new reason `no_options` and the reason why (for example "nearest swing low 1.1 ATR away; daily swing low 4.7 ATR away; no target with rr ≥ 1.5").
- **Constraints block.** The snapshot also gets `constraints` (atr_1h, entry_limit, allowed stop range, min_rr), so the model sees the rules the options already meet.

**Stop options (held positions).** At most 3, named T1–T3:
- below the most recent 1h swing low that is above the current stop;
- break-even (the entry price);
- a trail at the highest 1h close since entry − 2 × ATR.

Each one is kept only when it is above the current stop and satisfies the existing tighten rule (at least 0.25 × ATR below the lowest of market price, plausible bid and bar close). Each shows `locks_profit` (the stop is at or above the entry) and `r_at_stop` (R at that stop against the initial risk). With no valid stop option, `tighten_stop` is not allowed for that call. The snapshot's `position` also carries the original thesis and invalidation (already required since PR 15).

**Model output.** `prompts/decision.v2.md` (in the spec package), with a strict JSON schema:
- symbol, action, `option_id` (A–F for enter, T1–T3 for tighten_stop, otherwise null), confidence, thesis, `levels_used`, risks, news_used.
- There are no price fields. The invalidation is the chosen stop, written by code.
- `confidence` now has a defined meaning: the model's estimate that price reaches the target before the stop.

**Code checks under v2.**
- **Invalid choice.** An `option_id` that wasn't offered (or a missing one for enter or tighten_stop) is invalid output: `skip` or `hold`, as today.
- **Rechecks and gates.** The chosen option's stop distance and reward to risk are checked again, then confidence and every risk gate as today.
- **Order time.**
  - The final limit is recomputed as today, and the chosen stop and target stay fixed prices.
  - Stop distance against the fresh reference and reward to risk from the final limit are checked again. If either now fails, the entry fails the Fresh quote gate with that reason.
  - Code never picks another option or moves a level to make a trade pass.
- **Explanation check.** Every level id in `levels_used`, thesis and risks must exist in the snapshot. Every decimal number in the text (prices, percentages, ratios) must match a number in the snapshot or the chosen option, after rounding to the text's precision. Whole numbers are not checked, because they are mostly indicator names and dates (EMA 20, 13 Oct).
- **When the explanation check fails.** The decision still stands (explanations don't move money). The failing tokens are stored, the decision drawer shows them, and the LLM call gets the warning reason `unsupported_claim` (see LLM call history).

**Strategies and backtests.**
- A strategy's prompt version decides the decision schema: v1 strategies keep free prices, v2 strategies get options. The option builder uses the strategy's own check overrides (stop distance range, minimum reward to risk).
- New shadow and the backtest strategy picker offer decision.v2.
- Backtests build levels and options from the bar store with the same function and the same time cut-offs.

- **What it is.** `make replay PROMPT=decision.v2 FROM=… TO=…` runs a prompt on stored input snapshots of the live run. For each candidate and held decision, it rebuilds the levels and options from the cached bars as they were at that cycle, calls the model, and scores the chosen option on the bars that followed: did the stop or the target come first within 5 sessions?
- **Replayable or not.** Each snapshot gets one status before anything else:
  - `replayed`: the builder ran.
    - If it produced options, the model was called.
    - If it produced none, the result is `no_options` with the builder's own reason (`EntryOptions.reason`).
  - `not_replayable`: an input the builder needs is missing. The reason is one of `missing_reference` (a snapshot from before `stop_reference_price` existed), `price_unavailable` (live had no usable price, so v1 made no model call either), `missing_atr` or `missing_bars`. No model call, no cost.
  - `no_options` is never used for missing inputs.
- **Approximate reference** (`--approx-reference`, off by default).
  - **What it does.** For snapshots that only lack `stop_reference_price`, the replay uses min(stored price, last closed 5-min bar close at the cycle time) from the rebuilt bars. The plausible ask isn't stored for those snapshots, so it's left out.
  - **Labelling.** These rows have status `replayed_approx`. The report shows them in their own section and never mixes them into the main rates, because a missing ask can only make the reference higher than it really was.
  - **Price.** A snapshot whose price is `"unavailable"` stays `not_replayable`; the price is never reconstructed.
- **Output.** `data/replays/<id>/results.jsonl` and a `report.md` with:
  - **counts per status, first:** replayed, no_options (with reasons), replayed_approx, and not_replayable per reason;
  - enter rate;
  - invalid choice rate and explanation check failure rate;
  - which options were chosen (structure or ATR stop, level or R target);
  - agreement with the live v1 decision;
  - first touch and R of the chosen options;
  - the same counts for rules-only on the same snapshots.
  - **Base of the rates:** they are computed over `replayed` rows only, and each says how many rows it is based on.
  - **Entries per session:** entries and their R are also shown per session, with the number of distinct sessions. Entries on the same day mostly ride the same market move, so 60 entries on one day count as roughly one observation, not 60.
- **Live entries.** The report lists every snapshot where live v1 entered, with what v2 offered and chose. If such a snapshot wasn't replayable, the report says why, so a missing comparison is visible instead of silent.
- **Cost.** It counts toward the backtest monthly cap. The tool shows an estimate first and refuses more than $2 without `--yes`. Replays never write to the live tables.

**Path to live. Nothing switches automatically.**
1. Replay on every replayable candidate snapshot of the current run. The first bar to clear: invalid choices under 2% and explanation check failures under 5%, without lowering any rule. Whether v2 enters more often than v1 can't be judged from a quiet week: if v2 made fewer than 20 entry proposals, the replay only shows that v2 works mechanically, and the shadow decides the rest.
2. A shadow "C · Options (decision.v2)" with gpt-4o-mini at temperature 0, for at least 10 sessions and at least 20 v2 entry proposals, next to live and rules-only. Shadows are capped at 3, so one slot has to be freed first; archive "A · Stricter trend".
3. Marc decides with the Strategies comparison and the replay report. Promotion to live happens only with "Start new run", so the readiness evidence stays under one prompt.

**Readiness.** Under v2, "Share of the last 100 model entry proposals that pass the code checks" counts a proposal as passing when:
- its option id is valid;
- the explanation check passes;
- the order-time recheck passes, or no order was attempted.

The criterion keeps meaning something even though the options meet the geometry rules by construction.

**Storage.** `decisions` and `shadow_decisions` gain:
- `levels` JSON and `options` JSON (as offered);
- `chosen_option`;
- `explanation_check` (passed or failed, with the failing tokens);
- `options_version` (the builder version, `1`).

The no_call reason list gains `no_options`.

**GUI.** The decision drawer shows, for a v2 decision:
- the header with the strategy and prompt;
- the action with confidence;
- **Options offered:** each option's stop with basis and ATR distance, target with basis and levels in the way, and reward to risk, with the chosen one marked;
- **Levels:** id, price, kind and time, with the price's position marked;
- the explanation check;
- the gates;
- the reasoning with the levels it used;
- the entry, stop, target and reward to risk tiles.

A v1 decision looks as before. On the dashboard, the latest-decision card shows the chosen option ("D · stop below L8 · target 2R") instead of free prices.

## Out of scope and later versions

Not in v0.1: live trading, shorting, options, margin, extended-hours trading, fractional shares, EU exchanges, backtesting, VPS deployment and login.

Likely v0.2+, in rough order:

- Ask the agent (as in Trading Agent). Briefings and reviews moved forward to PR 9 (see Reviews).
- Shadow strategies: specified for PR 13 (see Strategies and shadows).
- Backtesting: specified for PR 14 (see Backtesting).
- Larger universe with a liquidity screen: specified for PR 11 and 12 (see Larger universe).
- VPS deployment with HTTPS and login.
- Live-readiness checklist (including Alpaca's real-time SIP data, so entries use the consolidated market quote instead of the IEX last trade), and a separate spec for live trading (including Dutch tax treatment, which needs checking with an advisor before going live).

## Sources

- [Alpaca Market Data API: plans and feeds](https://docs.alpaca.markets/docs/about-market-data-api)
- [Alpaca: paper-only accounts and PDT checks](https://alpaca.markets/deprecated/docs/trading-on-alpaca)
- [Alpaca paper trading: what it does not simulate](https://blog.traderspost.io/article/alpaca-paper-trading)
