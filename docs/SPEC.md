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
- On early-close days, run only the cycles whose bar closes before the close; the last one is the exit-check. Missed cycles are never run late: if the app was down at a cycle time, it waits for the next scheduled cycle (scheduler misfire grace 2 minutes). Only simulated mode may run a cycle at startup.
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
- Entry limit = current IEX ask + 0.05%. If not filled within 10 minutes, cancel it and record "entry not filled".
- Skip the entry if the IEX spread is above 0.15% of price.
- Quantity is whole shares from the risk sizing; if that rounds to 0, skip.

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
- A `tighten_stop` only raises the stop.
- Invalid or unparseable output counts as `skip` (or `hold` for a position) and is stored with the raw text. When no model call is made or the call fails, the decision is stored with final action `no_call` and a reason: `rules`, `call_cap`, `quote_missing` or `model_error`. A model call times out after 30 seconds and is not retried; for a held position `no_call` means the position is left as it is.

**Prompt.** `prompts/decision.v1.md`, versioned; each decision stores the prompt version and hash, the model name and the token cost. The input snapshot is a JSON block with the indicators, regime, position (if any), last 24h headlines and earnings date. Data that could not be fetched is marked `"unavailable"`. The prompt tells the model to treat unavailable data as unknown and never to invent numbers or news.

**Outcome tracking.** For every decision, including skips, record the price after 1 hour, 1 session and 5 sessions. 1 session means the same New York time on the next trading session; 5 sessions means the same time on the 5th trading session after the decision (weekends and holidays don't count). If that time falls after the session's close (early-close days), the close is used. The price is the last closed 5-minute bar at or before the target time, stored with that bar's timestamp. For trades, also record the best and worst price reached while open, and whether the stop or the target was touched first. This feeds the Performance screen and later prompt reviews.

## Risk management

Risk gates run in code after the model answers, in the order below; the first gate that fails blocks the action and is recorded by name. All values live in `config.yaml`.

| Gate | Default | Effect |
| --- | --- | --- |
| Risk per trade | 0.5% of equity | Shares = risk budget / (entry − stop), rounded down |
| Max position size | 20% of equity | Shares reduced to fit |
| Total open risk | 2.5% of equity (sum of entry-to-stop risk on all positions and working entry orders) | Size down, or skip if under 1 share |
| Max open positions | 4 | Skip |
| Sector cap | Max 2 positions per sector; QQQ counts as Technology, SPY as Index | Skip |
| Cash only | Order value ≤ cash; margin never used | Skip |
| Daily loss limit | −2% of equity in the current session | No new entries for the rest of the session |
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
| Performance | Equity curve vs SPY buy-and-hold (USD and EUR), raw vs adjusted P&L, win rate, average R, max drawdown, results by symbol and by regime |
| LLM inspector | Every model call: prompt version, full prompt, raw response, tokens, cost, latency, validation result |
| Settings | API keys and data-source status, execution mode, universe, risk values, entry rules, earnings handling, model and reviews, notifications, cycle times (read-only), settings history, paper account reset; changes are versioned and take effect next cycle (see Settings screen) |
| States and dialogs | Market closed, early close, kill switch on, reconciliation mismatch, data source down, pending approval (supervised mode); confirm dialogs for Stop & flatten, reset paper account and settings changes |

The layout must also work at phone width, so the VPS version can be checked from a phone.

**Cycle counter.** The this-cycle strip at the top of the dashboard ends with a cycle counter: the next cycle's time in Amsterdam and New York with a live countdown (mm:ss under an hour, otherwise h:mm), one segment per cycle of today's session (done, next, later; an early-close day has fewer), "Cycle n of 6 today", the cycle's number in this run, and the next position check. Market closed: the next session's first cycle with its day. Kill switch on: "Kill switch on · no cycles". A cycle that is running shows "running" instead of the countdown. The count in this run comes from the `cycles` table (kind cycle) since the run started.

**Risk budget card.** The dashboard shows how much of each limit is in use, computed by the same functions as the risk gates (never recomputed in the GUI): open risk of positions and working entries against the 2.5% cap; today's loss against the 2% daily limit; drawdown from the run's peak against the 8% kill. Each is a meter that turns amber above 75% of its limit and red at the limit. Underneath: room for more trades (the smallest of positions left, open risk left ÷ current risk per trade, and cash ÷ max position size, rounded down, with "limited by cash" or "limited by open risk" when that is the smallest), positions per sector against the sector cap, current risk per trade with the losing-streak count, the shock brake state with SPY's move over the last 1h bar and since the open, and day trades in the last 5 trading days. Every label explains its rule on hover. It refreshes with the positions, orders and header updates.

**Settings screen.** Sections with a section list on the left (a scrolling row on a phone) and a "Find a setting" filter: API keys and data, Trading (execution mode, universe, slippage), Risk limits (per trade, portfolio, brakes), Entry rules and checks, Earnings, Model and reviews, Notifications, Schedule (read-only), History and Paper account. Each setting is one row: name, one-line explanation, the control with its unit, and its default underneath. Values fixed by the spec (cash only, cycle times) are shown locked. A changed value is highlighted with "Changed from …" and Reset, and the section list shows how many changes each section has. A bar at the bottom lists the unsaved changes (old → new), asks for a reason (required, shown in History) and has Discard and "Save as v<n>". Values are checked as they are typed against the allowed ranges (for example RSI from must be below RSI to); Save is disabled while one is invalid. Leaving the page with unsaved changes asks first. Keys are not part of this flow: they are saved per key, straight away (see API keys).

**Design handoff.** The repo has a `design/` folder: `DESIGN.md` (rules, shared components, which PR builds which screen), `tokens.css`, `screens/` (the 7 screens as HTML, the source of exact styles and wording) and `screenshots/`. The spec wins on behaviour, the design on look and wording; differences go in the PR description.

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
| Model failing or review cost cap reached | Warning | on | off |
| Entry waiting for approval (supervised mode) | Info | on | off |
| Exit deferred (day-trade rule) or earnings exit tomorrow | Info | on | off |
| Trade opened or closed | Info | off | off |
| Daily briefing | Info | off | on |
| Weekly review ready | Info | off | on |
| Verdicts waiting to confirm (once a day, with the briefing) | Info | off | off |

**Rules.** Critical events always go to the browser and are never held back. Any other event with the same subject is sent at most once per 30 minutes (repeat limit). During quiet hours (23:00–08:00 Amsterdam) only critical events are sent; the rest are held and sent as one summary when quiet hours end. Sending runs in the background: a slow or failing channel never delays a cycle, a check or an order. Three failed emails in a row show a banner.

**Content.** Subject `[Stock Agent · PAPER] <event>: <one line with the key number>`, for example "Kill switch on: drawdown −8.1% from peak". The body has the figures, the time in Amsterdam and New York, what the agent did and what Marc needs to do, and a link to the right GUI page. Briefings and reviews carry their text. Notifications never contain keys or passwords.

**Settings.** The event × channel choices, the repeat limit and quiet hours are settings (versioned, in Settings → Notifications). SMTP details are saved on their own, like the keys.

## Data model

SQLite, one file, migrations with Alembic. Times are stored in UTC; money as decimals in USD.

| Table | Holds |
| --- | --- |
| `bars_5m` | symbol, start time, OHLCV, feed; unique on (symbol, start). Daily bars live in `bars_1d` (symbol, session date, OHLCV), unique on (symbol, date) |
| `cycles` | kind (cycle or position check), scheduled time, started, finished, status, errors, data-source health |
| `decisions` | cycle, symbol, context (candidate or held), input snapshot JSON, rules result, model output JSON, validation result, gates result, final action, prompt version and hash, model, tokens, cost |
| `orders` | Alpaca order id, parent id, decision, side, type, leg (entry, stop, target, exit), qty, prices, status, timestamps |
| `fills` | order, qty, price, time, slippage vs intended price |
| `positions` | symbol, origin (agent or adopted), entry decision, qty, avg entry, current stop, target, opened, closed, realized P&L raw and adjusted, exit reason |
| `outcomes` | decision, price after 1h, 1 session and 5 sessions; best and worst price while open |
| `equity_snapshots` | time, equity, cash, SPY benchmark value, EUR/USD rate |
| `day_trades` | date, symbol, order ids |
| `earnings` | symbol, date, time of day (before_open, after_close, during_market or unknown, mapped from Finnhub's bmo, amc and dmh), raw Finnhub value, fetched at |
| `corporate_actions` | symbol, type, date, ratio or amount |
| `settings_versions` | version, settings JSON, changed at, reason |
| `universe_pool` | symbol, sector, source (default, added or import), added at, removed at (v0.2) |
| `universe_screens` | session date, symbol, status (eligible, excluded or warming_up), check results JSON, last close, average dollar volume, return vs SPY, rank, failed screens in a row (v0.2) |
| `notifications` | time, event, severity, subject, channels, status per channel (sent, failed, held back, quiet hours), error |
| `secrets` | name (alpaca_key_id, alpaca_secret, openai, finnhub, smtp_password), encrypted value, last 4 characters, saved at, tested at, test result |
| `events` | time, level, type (reconciliation, kill switch, error, acknowledgment), details JSON |

A daily copy of the database is made at 03:00 Amsterdam time with SQLite's online backup API (never a plain file copy, which can catch a write half-done), checked with `PRAGMA integrity_check`, and kept for 14 days. Migrations are never edited once merged; a schema change is always a new revision. At startup the app compares the schema with the models and refuses to start with a clear message if they differ.

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

Ten PRs for v0.1, each reviewable on its own; v0.2 continues with PR 11 and 12. Paper orders are first sent in PR 5; everything before runs on fakes and read-only data.

1. **Skeleton.** Repo layout, FastAPI app, React shell with header and empty screens, config loading, SQLite and migrations, `AGENTS.md`, CI running tests with sockets blocked. *Done when:* app starts, header shows PAPER and a fake clock.
2. **Market and data.** Calendar and clock, 5-min bar download and cache, session-aligned 1h bars, IEX quotes, news, Finnhub earnings, ECB rate; fakes for each. *Done when:* aggregation, DST and early-close tests pass, and a read-only run fills the cache for all 10 symbols.
3. **Signals, regime and rules.** Indicators, regime, rules pre-filter, shared risk-math function. *Done when:* indicator tests match hand-checked values and the dashboard shows rules status per symbol.
4. **Decision layer.** Prompt v1, input snapshot builder, OpenAI client, output validation, decision storage, LLM inspector screen. *Done when:* simulated cycles produce stored decisions; invalid outputs are handled per spec.
5. **Execution.** Alpaca paper broker, bracket entries, stop moves, exits, trade-updates stream, reconciliation, 15-minute position check, PDT guard, risk gates, kill switch. *Done when:* a simulated session runs with every position protected by a stop, and startup, the paper-account check and reconciliation are verified read-only against the real paper account. Marc runs the first real paper session himself after review, with the paper account reset to $10,000.
6. **GUI.** Dashboard, decision detail, positions and orders, settings, states and dialogs, supervised-mode approvals, WebSocket updates, phone-width layout.
7. **Performance and outcomes.** Outcome worker, equity snapshots, SPY benchmark, raw vs adjusted P&L, performance screen.
8. **Hardening.** Logging and rotation, daily database copy, error kill switch, startup recovery after a crash mid-cycle, README for running locally. *Done when:* a crash mid-cycle recovers cleanly.
9. **Reviews.** Weekly review, daily briefing and verdicts (see Reviews).
10. **Settings and API keys.** Settings screen redesign (see Settings screen) and keys entered in Settings (see API keys), including the one-time import from `.env`, plus the dashboard risk budget card and cycle counter (see Risk budget card, Cycle counter) and notifications (see Notifications). If this makes PR 10 too large, notifications may follow as their own PR, still before the paper run. *Done when:* the app runs with an `.env` that has no keys, and the 4-week paper run can start.
11. **Pool and liquidity screen (v0.2).** `universe_pool` and `universe_screens`, pool loading from `pool.csv`, add/import/remove, warm-up, daily screen with hysteresis, rank, universe mode in Settings, Universe screen. Default mode stays `fixed`. *Done when:* in simulated mode the screen over the 72-symbol pool gives the expected statuses, and fixed mode is unchanged.
12. **Cycles at scale (v0.2).** Cycles over eligible plus held symbols, multi-symbol bar requests, model calls in rank order, cycle duration and warning, 5-min bar pruning, dashboard watchlist filters and search. *Done when:* a simulated session with the 72-symbol pool keeps every cycle under 10 minutes and under 150 Alpaca calls per minute, and every held position is handled.

## Reviews

The model writes a weekly review, a daily briefing and a draft verdict on each decision, so the 4-week paper run can be read without digging through the log. Code computes every fact; the model only writes sentences about them. Reviews never change settings, prompts or trading. Built as PR 9, before the 4-week paper run.

| Feature | When | Content |
| --- | --- | --- |
| Weekly review | Saturday 10:00 Amsterdam, covering Monday to Friday | Return, difference with SPY, trades won and lost, average R, max drawdown, model cost, best and worst trades, what skipped setups did after 5 sessions, confirmed verdicts, results by regime and symbol. Model text: a summary of at most 120 words, up to 3 observations and at most 1 suggestion, marked "not applied" |
| Daily briefing | Trading days, 09:00 New York (15:00 Amsterdam most of the year); can be turned off | Open positions with stop distance, earnings today or tomorrow for held and watched symbols, market regime, yesterday's result, active banners. At most 80 words |
| Decision verdict | Once a decision's 5-session outcome is in | For every enter and model skip (not rules no_calls): a draft verdict of good call, bad call or unclear, with a one-line reason. Marc confirms or changes it; only confirmed verdicts count in reviews |

**Number check.** Every number in the model's text must match a fact it was given (after rounding). If one doesn't, or there is no OpenAI key, the call fails or the review cost cap is reached, the app shows a plain template built from the facts instead, labelled with the reason (for example "Plain review: number check failed"). The failure is logged.

**Model and cost.** Same model as decisions (gpt-4o-mini), temperature 0.2, with versioned prompts (`weekly.v1`, `briefing.v1`, `verdict.v1`) that are never edited once used. Review calls have their own monthly cap of $2 and can never use budget or rate limits needed by trading decisions.

**Missed runs.** Unlike cycles, a review or briefing missed while the app was down is written at the next start, labelled with its original date. A briefing older than its trading day is skipped.

**Storage.** A `reviews` table (kind, period, facts JSON, text, source model or template, reason, model, prompt version, tokens, cost, created at) and a `verdicts` table (decision, verdict, reason, source draft or Marc, created and updated at).

**GUI.** A Reviews screen in the Learn group with Weekly and Briefings tabs and a "Verdicts to confirm" list. The decision drawer shows the verdict with Confirm and Change. The Dashboard is unchanged.

## Larger universe (v0.2)

Watch more of the market without taking more risk: risk limits, gates and the 10-call cap stay the same; only the set of symbols the agent looks at grows. Built as PR 11 and PR 12.

**Two modes.** `universe.mode` is `fixed` (the 10-symbol list from v0.1, the default) or `screened` (a daily liquidity screen over a pool). Settings → Trading → Universe switches between them. A switch is a new settings version and applies at the next daily screen, never mid-session. Switch at the start of a new run so the v0.1 paper results stay comparable. In `fixed` mode everything behaves exactly as in v0.1.

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

## Out of scope and later versions

Not in v0.1: live trading, shorting, options, margin, extended-hours trading, fractional shares, EU exchanges, backtesting, VPS deployment and login.

Likely v0.2+, in rough order:

- Ask the agent (as in Trading Agent). Briefings and reviews moved forward to PR 9 (see Reviews).
- Shadow strategies: run a second prompt or model on the same inputs without trading, and compare outcomes.
- Backtest mode on the cached 5-min bars.
- Larger universe with a liquidity screen: specified for PR 11 and 12 (see Larger universe).
- VPS deployment with HTTPS and login.
- Live-readiness checklist, and a separate spec for live trading (including Dutch tax treatment, which needs checking with an advisor before going live).

## Sources

- [Alpaca Market Data API: plans and feeds](https://docs.alpaca.markets/docs/about-market-data-api)
- [Alpaca: paper-only accounts and PDT checks](https://alpaca.markets/deprecated/docs/trading-on-alpaca)
- [Alpaca paper trading: what it does not simulate](https://blog.traderspost.io/article/alpaca-paper-trading)
