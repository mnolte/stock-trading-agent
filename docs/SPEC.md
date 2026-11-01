# Stock Trading Agent — Spec v0.1

15 October 2026 · Marc Nolte

## Overview and goals

Stock Trading Agent v0.1 is a standalone, paper-only agent that trades a fixed list of liquid US stocks and ETFs through Alpaca's paper account, with an OpenAI or Anthropic model as its decision layer (OpenAI `gpt-4o-mini` live by default; see Model providers). It is a new application built from scratch, not a fork of Trading Agent. It reuses Trading Agent's visual design and proven ideas (rules pre-filter, risk gates, outcome tracking, kill switch), adapted to stock markets.

**Currency.** Stock Trading Agent trades in US dollars (USD). The Alpaca account, cash, orders, prices, P&L, risk limits and every stored money value are in USD. Euro amounts in the GUI are for display only, converted with the ECB daily rate, and never used in trading decisions or risk checks.

**Timezone.** The application timezone is Europe/Amsterdam. The GUI, logs, alerts, settings history and daily jobs (backups, the daily database copy) use Amsterdam time. Two exceptions: timestamps are stored in UTC, and the decision cycles follow the New York exchange clock (see Market, schedule and data) so they stay aligned to market hours when the US and EU change clocks on different dates. Cycle times are shown in Amsterdam time.

v0.1 is done when:

- The agent runs unattended through full US sessions for 4 consecutive weeks without crashes or unreconciled positions.
- Every decision (trade or no-trade) is stored with its inputs, prompt version, model output and outcome.
- Every open position has a broker-side stop order at Alpaca at all times.
- Performance is shown against a SPY buy-and-hold benchmark started on the same day.
- No code path can place an order against a live (real-money) Alpaca endpoint.

Name: **Stock Trading Agent** (shown in the GUI, page titles, emails and error pages). Technical names stay `stock-agent`: the repo, the database file, log files and containers. Prompt files already used (`decision.v1.md`, `decision.v2.md`) keep the old name because used prompts are never edited; the next prompt version uses the new one.

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
| Hosting | Local during development; production on a VPS (Docker Compose, Caddy, sign-in with password and authenticator) | See VPS deployment |

## Architecture and stack

One Python backend process runs the scheduler, the agent and the API; a React app talks to it over HTTP and a WebSocket.

| Layer | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, Uvicorn |
| Scheduler | APScheduler, timezone-aware (`America/New_York`) |
| Broker + data | `alpaca-py` SDK (trading, market data, news) |
| LLM | OpenAI and Anthropic Python SDKs behind one model client, structured JSON output (see Model providers) |
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
- Confirms every position has a live stop order at Alpaca; if one is missing, it repairs it as in Alpaca integration → Protective stops (including the market sale when the stop level is already breached) and alerts.
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

**Broker reads from the GUI.** An API route that reads from Alpaca (positions, risk budget, account) waits at most `api.broker_timeout_seconds` (5). On a timeout or error it answers from the database's last reconciled values with `stale: true` and their time, and the GUI shows "Alpaca not answering · as of 18:46" on those cards. A slow Alpaca never holds a request for more than 5 seconds; on 30 October two reads took 87 seconds.

**Paper only**

- The trading client is always created with `paper=True`. The live base URL must not appear anywhere in the code; a test fails the build if it does.
- At startup, read the account and refuse to start unless it is a paper account. Show "PAPER" permanently in the GUI header.
- Keys are entered in Settings and stored encrypted (see API keys). They are never logged or sent to the frontend.

**Entries**

- Every entry is a bracket order: limit buy entry, stop-loss leg, take-profit leg. Time-in-force `gtc` so the stop and target survive overnight.
- **Market price.** On the free data plan the IEX quote is one small exchange's book, so it is not used as the market price. The market price is the IEX last trade, which must be at most 60 seconds old (`execution.quote_max_age_seconds`, judged on the trade's own timestamp, never the fetch time) and within 1% (`execution.quote_max_deviation_pct`) of the last closed 5-min bar's close. A missing or older trade is `price_stale`; a trade further from the bar is `price_suspect`; either means no model call and no entry. Held positions are always reviewed; for them a stale or suspect price only blocks a tighten_stop. For a tighten the trade may be up to 5 minutes old (`execution.tighten_quote_max_age_seconds`, 300), because a stop move only lowers risk and IEX trades in quieter names are often 2–7 minutes apart; the 1% bar check still applies. A tighten blocked this way becomes `hold` shown as changed by code with reason `price_stale` or `price_suspect` (drawer, feed and inspector), like the break-even guard.
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
- **Exits close from the stream.** When a fill brings a position to zero (stop, target, OCO, agent exit, earnings exit, breach sale or Stop & flatten), the trade-updates handler closes the position row right away. It records the exit reason from the order's kind and the fill price, logs INFO and sends the normal "Position closed" notification. A routine stop or target exit is not a mismatch.
- **Stream health.** "Connected" is not enough. Every trade update that can't be parsed is logged at ERROR with the raw message (keys scrubbed), counted, and marks the stream `degraded`. So does a fill found by reconciliation whose update never arrived within 2 minutes. `degraded` shows the backend banner ("Order updates delayed: positions are checked every 15 minutes"), sends the critical notification, and clears after 30 minutes without a new failure. The parser is tested on the exact wrapper shape the SDK delivers, recorded from a real paper message. Reconnects: the first failed connection of an outage is logged at ERROR; the same error again is logged at most once per 10 minutes with a count; the reconnect is logged at INFO with the outage length. An outage of more than 5 minutes during a session marks the stream `degraded` too.
- **Fills the stream missed** (the app was down or the stream dropped): when reconciliation finds a recorded position flat at Alpaca, it first looks up the symbol's filled orders at Alpaca since the position opened. If this instance's own exit orders explain the close, the row is closed with that reason and fill, marked `closed_while_offline`, and logged at WARNING. That is not a mismatch either.
- At startup and at the start of every cycle, compare Alpaca positions and open orders with the database.
- **Fills still settling.** An entry fills in several prints over a few seconds, and Alpaca's positions endpoint can lag the fills by a second or two. A quantity difference for a symbol with a working entry order, or a fill in the last `execution.fill_settle_seconds` (10), is not compared yet: it's logged at INFO and checked again at the next reconcile, or 10 seconds later when the entry order reaches a final state. Only a difference that remains after the order is final and the settle time has passed is a mismatch.
- **Mismatches** are what the stream and Alpaca's order history can't explain: a position flat with no explaining fill, a quantity that differs, an unknown order, an order of another instance, or a position the agent never recorded. On any mismatch: record it as an event, log CRITICAL, show a banner, adopt Alpaca's state, and block new entries until Marc acknowledges it. Every mismatch path does all of these; none of them may just close a row silently. A position found at Alpaca that the agent never recorded is adopted: it immediately gets a standalone gtc stop at the current price minus 3 × the 1h ATR (3% below the current price if ATR is unavailable), with no target. The agent makes no model calls or exits for it until Marc acknowledges; after that it is a normal position with Alpaca's average price as its entry. It is marked as adopted, and its P&L is shown apart from the agent's own trades.
- A position without a live stop order is a critical error, repaired as described in **Protective stops** below, then alerted.

**Protective stops** (decided 27 October after the WMT incidents)
- **Confirm first.** Before any protective order, fetch the symbol's position and open orders from Alpaca again. Do nothing when the position is flat, or when recorded sell fills already cover its quantity (it is exiting; the row closes on the next pass).
- **Stop below the market.** Place a gtc stop at the stored stop price.
- **Stop at or above the market** (the stop level was already breached). Don't place a stop one cent below the market. Sell the confirmed quantity at market during the session, or market-on-open outside it. Record the exit reason `stop_breached`. This is the only sale a position check may make.
- **Orphaned stop leg, target still holds the shares** (`held_for_orders`). A new stop would be rejected for insufficient quantity, so:
  1. cancel the target and wait for Alpaca to confirm the cancel;
  2. submit an OCO exit order (`order_class: oco`) with the stored target as its take-profit and the stored stop as its stop-loss;
  3. if the OCO is rejected, place the stop alone. Protection comes before the target. Raise a critical alert either way.

  The position is briefly without a stop between the cancel and the new order, and that gap is logged at CRITICAL with its length.
- **Never sell more than is held.** Every sell order the agent sends (exits, protective stops, Stop & flatten) is capped at the position quantity Alpaca confirms right before it is sent. The agent never goes short.
- **Broker-side guard.** At startup the app sets, and then checks, the account configuration that blocks shorting (`no_shorting`) and limits margin to 1× (`max_margin_multiplier: 1`) where Alpaca offers them. If it can't confirm them, it shows a warning banner; it doesn't refuse to start, because cash-only sizing and the sell cap still protect.

**Day trades (rule retired)**

FINRA replaced the pattern day trader rule and its $25,000 minimum with intraday margin standards on 4 June 2026, and Alpaca applies them from that date (FINRA Regulatory Notice 26-10). There is no day-trade limit any more. PR 26 removes the old guard.
- **Same-day exits.** The agent may close a position on the day it opened it. A model `exit` on the entry day is carried out like any other exit, and earlier deferred exits are not replayed.
- **No re-entry the same session.** A symbol the agent sold or that was stopped out today is not bought again until the next session. This rule is new (`risk.reentry_same_session: false`). It replaces the brake the old rule gave against churning.
- **Cash only, unchanged.** Entries are sized against Alpaca's `cash` minus working entries, never `buying_power`, so intraday margin is never used. Alpaca's own pre-trade checks can still reject an order; that is handled as "Order rejected by Alpaca".
- **Old API fields.** The code no longer reads `pattern_day_trader`, `daytrade_count` or `daytrading_buying_power` (Alpaca removed them by 6 July 2026). A test fails if they are referenced.
- **History.** The `day_trades` table is no longer written. It stays for history; migrations are never edited.
- **Count shown.** Positions shows a count of positions bought and sold in the same session, for information only.

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

**Model output.** Strict JSON through the provider's structured outputs (a strict JSON schema; see Model providers for Anthropic), validated again with Pydantic. Allowed actions depend on context: for a candidate `enter` or `skip`; for a held position `hold`, `tighten_stop` or `exit`.

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
- **Break-even guard** (every prompt version, from 29 October): a `tighten_stop` to a stop at or below the entry plus nothing (it locks in 0 R or less) is refused while the position is less than `decision.breakeven_min_r` (1.0) R in profit. The action becomes `hold` with reason `breakeven_too_early`, shown as changed by code. On 29 October four break-even tightens at +0.13R to +0.40R were called "locking in profit"; RTX turned a +0.40R open gain into −$2.07 two hours later. Decision v3 also stops offering such options; this guard protects v2 and v1 strategies until then.
- Invalid or unparseable output counts as `skip` (or `hold` for a position) and is stored with the raw text. When no model call is made or the call fails, the decision is stored with final action `no_call` and a reason: `rules`, `call_cap`, `quote_missing`, `model_error` or, under decision.v2, `no_options` (see Decision v2). **Live decisions failing:** when every live model call of a cycle fails (at least one call), or any live call gets an authentication error (401 or 403), record a critical event, show the banner "Live decisions failing: <provider> <error>. Held positions are not being reviewed; their stops at Alpaca still stand", and send the critical notification once per episode. An authentication error also marks that provider's key as failing in Settings → API keys. The episode clears at the first successful live call. On 30 October a reload dropped the keys and two cycles ran with no model answer while only a per-symbol warning was sent. A model call times out after 30 seconds and is not retried; for a held position `no_call` means the position is left as it is.

**Prompt.** `prompts/decision.v1.md` (decision.v2 with code-built options is specified in Decision v2), versioned; each decision stores the prompt version and hash, the model name and the token cost. The USD cost is computed in code from `model_prices` for the call's model and stored with the call, with its source (`priced`, `cached` or `legacy` for older calls stored with tokens but no cost; legacy costs are never rewritten). The input snapshot is a JSON block with the indicators, regime, position (if any), last 24h headlines and earnings date. It also states the time of the quote and of the last completed signal bar, so the model can tell a current quote from a delayed bar. For a held position it includes the entry, the initial stop, the current stop, the original thesis and invalidation, and progress in R measured against the initial risk (entry − initial stop), with the remaining downside to the current stop shown separately. Headlines come per symbol with a status: `complete`, `empty` (complete, no headlines), `truncated` or `failed`; only `empty` may be read as "no news". News is fetched until every symbol's result is complete or the request budget runs out, never under one shared cap. Data that could not be fetched is marked `"unavailable"`. The prompt tells the model to treat unavailable data as unknown and never to invent numbers or news.

**Known prompt versions only** (PR 32). Each prompt version has a contract in code: its output schema, snapshot builder and checks (decisions), or its facts and output (reviews). Only versions with a contract can be used: `decision.v1`–`v3`, `weekly.v1`–`v2`, `briefing.v1`–`v2`, `verdict.v1`–`v2`. A version without one (for example `decision.v4` before its code exists) never falls back to another version's contract. Startup fails with a clear message when the config names one; saving a strategy, a setting, a backtest or a replay with one is refused (422 `unknown_prompt_version`). A prompt file alone is not enough to make a new version usable.

**Outcome tracking.** For every decision, including skips, record the price after 1 hour, 1 session and 5 sessions. 1 session means the same New York time on the next trading session; 5 sessions means the same time on the 5th trading session after the decision (weekends and holidays don't count). If that time falls after the session's close (early-close days), the close is used. The price is the last closed 5-minute bar at or before the target time, stored with that bar's timestamp. For trades, also record the best and worst price reached while open, and whether the stop or the target was touched first, judging each bar against the stop that was active at that bar's time (from the stop-leg order history), never against today's stop. This feeds the Performance screen and later prompt reviews.

**Option outcomes** (PR 32). For every decision with code-built options (decision.v2 and v3, live and shadows), also record for each offered option which of its stop or target price touched first, whether or not it was traded. Recorded with the 5-session outcome, from stored 5-minute bars:

- Entry: the option fills if a bar's low reaches `entry_limit` within the entry order's 10-minute life after the decision; otherwise `not_filled`. For the option actually traded, the real fill and the trade's own first touch are used instead.
- After the fill bar, the first bar whose low reaches the stop gives `stop`, whose high reaches the target gives `target`; both in the same bar count as `stop`. Nothing by the 5-session time gives `neither`. Missing bars give `null`.
- It ignores earnings exits, stop moves and position limits: it measures the setup, not what the agent would have done.
- Stored as `outcomes.option_touches` (JSON, option id → `target`, `stop`, `neither`, `not_filled` or null).

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
| Re-entry | The symbol was sold or stopped out earlier this session | No new entry in that symbol until the next session |
| Drawdown kill | Equity 8% below its peak | Kill switch turns on automatically |
| Order sanity | No single order above 25% of equity; at most 6 orders per cycle | Block and alert |

**Working entry orders.** An entry order that is submitted, accepted or partially filled, and not yet cancelled, expired or rejected, counts in the gates as if it were already a position: its unfilled quantity × (limit − stop) counts toward total open risk, its unfilled quantity × limit toward cash, and it counts toward max open positions, the sector cap and max position size. Filled quantity counts as a position only, never twice. Entries in one cycle are gated one by one in submission order, each against the budget left after the earlier ones. Which entries are submitted, and in what order, follows `selection.mode` (see Entry selection). A cancel, expiry or rejection frees the budget. In supervised mode an entry waiting for approval does not count; the gates run again when Marc approves.

**Kill switch.** Manual (header button) or automatic (drawdown kill, repeated errors). It stops all new entries and leaves existing stops in place. "Stop & flatten" also cancels all open orders and sells every position at market; outside regular hours the sells are queued as market-on-open orders. If Alpaca rejects a Stop & flatten sale, that position keeps its stop and the rejection shows as a critical banner. Resuming requires an explicit acknowledgment with a typed reason, which is stored.

**Paused at startup.** The agent always starts with new entries paused. Startup, reconciliation with Alpaca, position checks, the stops and targets at Alpaca, earnings exits and outcome tracking all run as normal; cycles still run and review held positions, so the model can still decide to hold, tighten a stop or exit. No new entries are placed until Marc presses Resume in the header: one click, no reason needed. Pause is the same button in reverse and can be used at any time. Pause and the kill switch are separate: the kill switch needs a typed reason to resume and can also flatten. Both states are stored and survive a restart: a kill switch that was on stays on, and startup always comes up paused even if trading was running before, except after a clean planned restart on the VPS (see VPS deployment → Restarts).

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
| Positions and orders | Open positions, their bracket legs at Alpaca, order history with fills and slippage, reconciliation status, today's entries, exits and same-session round trips (information only) |
| Performance | Equity curve vs SPY buy-and-hold (USD and EUR), raw vs adjusted P&L, win rate, average R, max drawdown, results by symbol and by regime, winners and tails (see Tail metrics) |
| Insights | Where results come from: expectancy with its range, edge over SPY at the same exposure, beta, overnight vs in session, gaps, cost; findings; trade funnel; slices by time of day, sector, earnings distance, gap, VWAP, pullback, strength, regime, confidence and more; calibration; execution; skipped setups (see Insights) |
| LLM inspector | Every model call since the start (live, shadows, reviews), filtered by period, outcome (success, warning, error), source and more; summary per session; prompt, raw response or error, tokens, cost, latency, outcome and reasons (see LLM call history) |
| Settings | API keys and data-source status, execution mode, universe, risk values, entry rules, earnings handling, model and reviews, notifications, cycle times (read-only), settings history, access (sign-in, devices), server (health, backups, monitoring; on the VPS), paper account reset; changes are versioned and take effect next cycle (see Settings screen) |
| Universe (v0.2) | Pool, daily screen and eligible symbols (see Larger universe) |
| Strategies (v0.2) | Live, shadows and baselines compared; equity curves; disagreements; Promote to live (see Strategies and shadows) |
| Backtest (v0.2) | New backtest with estimate, runs, results (see Backtesting) |
| Readiness | Whether the paper evidence would justify live trading (see Readiness) |
| Logs | Every log line, warnings and up by default with a switch for debug and info; level toggles, period, search, component, symbol and cycle filters; warnings and errors per day; recurring problems; traceback, stored line and surrounding lines (see Logging, error handling and shutdown) |
| Error pages | Not found (page, record), something went wrong (with error id), app not answering, offline; static pages from Caddy when the app is down or updating (see Error pages and API errors) |
| Sign in | Password, then authenticator code; locked-out state; no data before signing in (see VPS deployment) |
| States and dialogs | Market closed, early close, kill switch on, reconciliation mismatch, data source down, pending approval (supervised mode), shutting down, previous run not shut down cleanly, session expired, restarted (resumed or paused), another instance on the account, server warnings, new sign-in; confirm dialogs (including "Confirm it is you" before risky actions) for Stop & flatten, reset paper account and settings changes |

The layout must also work at phone width, so the VPS version can be checked from a phone.

**Daily briefing card.** Next to the risk budget card the dashboard shows today's briefing (see Reviews): the text, chips for the facts it used, whether every number was checked or the plain template was used (and why), the model, prompt version and cost, and when the next briefing is written. Before 15:00 Amsterdam on a trading day it shows the previous briefing with its date; with briefings turned off it says so and links to Settings. A briefing never changes after it is written; what changed since is in the cards around it.

**Model call counter.** A summary tile shows today's decision calls against the day's maximum (max calls per cycle × cycles today, 60 by default), with today's and this month's decision cost. On hover it shows review cost against its own cap, and from v0.2 shadow and backtest cost against theirs. It links to the LLM inspector.

**Dashboard at scale and off-hours.**
- **Session day.** "Today" on the dashboard is the current New York session or, outside a session, the last completed one, never the Amsterdam calendar day. Outside a session the feed is titled with that session's date ("Friday 23 Oct · last session"), the model-call tile counts that session, and the cycle strip summarises it. A countdown longer than 24 hours shows only the day and time.
- **Latest decision.** The card shows the selected symbol's latest decision in that session. If the symbol wasn't decided this cycle, it says why from the stored snapshot (pre-filter reason, call cap, price check). If it has no decision in the session, it shows its most recent decision with the date.
- **Decisions feed.** Grouped per cycle (by cycle, not by minute), with the cycle's Amsterdam time (New York on hover) and a header that can collapse. Changes (entries, exits, stop moves, approvals) always show as their own rows. Other decisions are grouped by final action and reason into one row: the symbols (the first 8, then "+n more") and a count. The current cycle is open; earlier cycles start collapsed and show only their changes. A single row opens the decision; a grouped row opens the LLM inspector filtered to that cycle and action.
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

**Risk budget card.** The dashboard shows how much of each limit is in use, computed by the same functions as the risk gates (never recomputed in the GUI): open risk of positions and working entries against the 2.5% cap; today's loss against the 2% daily limit; drawdown from the run's peak against the 8% kill. Each is a meter that turns amber above 75% of its limit and red at the limit. Underneath: room for more trades (the smallest of positions left, open risk left ÷ current risk per trade, and cash ÷ max position size, rounded down, with "limited by cash" or "limited by open risk" when that is the smallest), positions per sector against the sector cap, current risk per trade with the losing-streak count, the shock brake state with SPY's move over the last 1h bar and since the open, and symbols blocked from re-entry this session. Every label explains its rule on hover. It refreshes with the positions, orders and header updates.

**Settings screen.** Sections with a section list on the left (a scrolling row on a phone) and a "Find a setting" filter: API keys and data, Trading (execution mode, universe, slippage), Risk limits (per trade, portfolio, brakes), Entry rules and checks, Earnings, Model and reviews, Notifications, Readiness, Schedule (read-only), History and Paper account. Each setting is one row: name, one-line explanation, the control with its unit, and its default underneath. Values fixed by the spec (cash only, cycle times) are shown locked. A changed value is highlighted with "Changed from …" and Reset, and the section list shows how many changes each section has. A bar at the bottom lists the unsaved changes (old → new), asks for a reason (required, shown in History) and has Discard and "Save as v<n>". Values are checked as they are typed against the allowed ranges (for example RSI from must be below RSI to); Save is disabled while one is invalid. Leaving the page with unsaved changes asks first. Keys are not part of this flow: they are saved per key, straight away (see API keys).

**Design handoff.** The repo has a `design/` folder: `DESIGN.md` (rules, shared components, which PR builds which screen), `tokens.css`, `screens/` (one HTML file per screen, the source of exact styles and wording) and `screenshots/`. The spec wins on behaviour, the design on look and wording; differences go in the PR description.

## API keys

API keys are entered in Settings, not in `.env`. There are Alpaca key ID and secret (paper) and Finnhub, plus at least one model key: OpenAI, Anthropic, or both (see Model providers). `.env` keeps only `MODE`, `SA_HOST`, `SA_PORT`, `SA_DATA_DIR` and `SA_DB_PATH`.

- **Storage.** Keys are encrypted with Fernet (`cryptography`) and stored in the `secrets` table. The encryption key is `SA_DATA_DIR/secret.key`, created at first start with file mode 600; it is never stored in the database, so a database backup alone does not reveal the keys. If `secret.key` is lost, the keys must be entered again.
- **Never shown again.** The API only accepts keys (write-only). Reads return the name, the last 4 characters, saved at, last tested at and the test result; never the key. A secret shorter than 8 characters is refused at save ("Too short to be a real key or password"), so the hint and the log scrubber never deal with one; a stored one shows no characters at all. Keys are never logged, never in error messages, the LLM inspector, events or settings versions; log output is scrubbed for every stored key as a second line of defence.
- **Test before save.** A new key is saved only after one test call passes: Alpaca `GET account` on the paper endpoint (must report a paper account), OpenAI list models, Anthropic list models, Finnhub one earnings-calendar request. The old key keeps working until the new one passes. "Test" re-runs the check without changing anything. Every save and test is an event (key name, last 4 characters, result), never the key.
- **Replacing Alpaca keys** is only allowed while the agent is paused or the kill switch is on, with no open positions or orders. If the new keys belong to a different paper account, the change requires "Start new run".
- **Missing or failing keys.** The app always starts (paused, as usual) and shows a banner naming the missing or failing key with a link to Settings. Resume stays disabled until the Alpaca and Finnhub keys are set and passing, at least one model key is set, and every provider used by live decisions, the weekly review, the daily briefing and verdicts has a passing key. Simulated mode needs no keys.
- **Moving from `.env`.** At startup, a key found in the environment with nothing stored under that name is imported once, tested, and stored; a banner then says "Keys imported from .env — remove them from .env". After that, keys in the environment are ignored with a warning in the log.
- Locally, settings, keys and the kill switch have no sign-in, so the backend stays bound to `127.0.0.1`. On any other address sign-in is required and the app refuses to start without it (hard rule 10; see VPS deployment).

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
| Data source or API key failing | Warning | on | on |
| Position checks failing (3 in a row) | Warning | on | on |
| Model failing, or review or shadow cost cap reached | Warning | on | off |
| Entry waiting for approval (supervised mode) | Info | on | off |
| Earnings exit tomorrow | Info | on | off |
| Trade opened or closed (opened: once the entry order is final, with the filled quantity, for example "opened PG: 12 shares @ 162.85", or "8 of 12 shares" when the rest was cancelled) | Info | off | off |
| Daily briefing | Info | off | on |
| Weekly review ready | Info | off | on |
| Verdicts waiting to confirm (once a day, with the briefing) | Info | off | off |
| Readiness changed (a criterion passes or stops passing) | Info | on | off |
| Another instance is trading this Alpaca account (VPS) | Critical | always | on |
| New sign-in (VPS) | Info | on | on |
| Failed sign-ins: lockout started (VPS) | Warning | on | on |
| Backup or restore test failed; disk at 80%; certificate expires within 14 days; clock off by more than 1 s (VPS) | Warning | on | on |
| Disk at 90% (VPS) | Critical | always | on |
| Restarted and resumed (VPS) | Info | on | off |
| Restarted paused after an unclean stop (VPS) | Warning | on | on |
| Trade-updates stream degraded (an update that couldn't be parsed, or no update within 2 minutes of a fill seen by reconciliation) | Critical | always | on |
| New error: an ERROR fingerprint seen for the first time this session | Warning | on | on |
| Clock check failing (the check itself, not the offset) | Warning | on | on |
| Live decisions failing: every live model call of a cycle failed, or any live call got an authentication error (401 or 403) | Critical | always | on |

**Rules.** Critical events always go to the browser and are never held back. Any other event with the same subject is sent at most once per 30 minutes (repeat limit). During quiet hours (23:00–08:00 Amsterdam) only critical events are sent; the rest are held and sent as one summary when quiet hours end. Sending runs in the background: a slow or failing channel never delays a cycle, a check or an order. Three failed emails in a row show a banner.

**Content.** Subject `[Stock Trading Agent · PAPER] <event>: <one line with the key number>`, for example "Kill switch on: drawdown −8.1% from peak". The body has the figures, the time in Amsterdam and New York, what the agent did and what Marc needs to do, and a link to the right GUI page. Briefings and reviews carry their text. Notifications never contain keys or passwords.

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
| `outcomes` | decision, price after 1h, 1 session and 5 sessions; best and worst price while open; first touch (trades); option touches (PR 32) |
| `equity_snapshots` | time, equity, cash, SPY benchmark value, EUR/USD rate |
| `day_trades` | date, symbol, order ids (no longer written since PR 26; kept for history) |
| `earnings` | symbol, date, time of day (before_open, after_close, during_market or unknown, mapped from Finnhub's bmo, amc and dmh), raw Finnhub value, fetched at |
| `corporate_actions` | symbol, type, date, ratio or amount |
| `settings_versions` | version, settings JSON, changed at, reason |
| `strategies` | name, kind (llm or rules_only), prompt version, model, temperature, rule and check overrides JSON (including `selection`, PR 33), status (active, paused, archived), created, archived at (v0.2) |
| `shadow_decisions` | strategy, cycle, symbol, context, live decision (shares its input snapshot), own input snapshot (only for a symbol only a shadow holds), model output JSON, final action, gates result, tokens, cost, missed and its reason (time or cost_cap); under decision.v2 also levels, options, chosen option and explanation check (v0.2) |
| `shadow_positions` | strategy, symbol, qty, entry, stop, target, opened, closed, exit reason, R (v0.2) |
| `shadow_equity` | strategy, time, equity (v0.2) |
| `backtests` | name, period, universe mode, strategies JSON, starting equity, slippage, status (queued, downloading, running, done, cancelled, stopped at cost cap, interrupted, refused), progress, cost, run folder path, process id, cancel and pause flags, size on disk, warnings JSON, summary JSON, created, finished (v0.2) |
| `llm_cache` | key hash (model, temperature, prompt version and hash, input snapshot hash), model, temperature, prompt hash, cycle time, response JSON, tokens, created (v0.2) |
| `universe_pool` | symbol, sector, source (default, added or import), added at, removed at (v0.2) |
| `universe_screens` | session date, symbol, status (eligible, excluded or warming_up), check results JSON, last close, average dollar volume, return vs SPY, rank, failed screens in a row (v0.2) |
| `notifications` | time, event, severity, subject, channels, status per channel (sent, failed, held back, quiet hours), error |
| `secrets` | name (alpaca_key_id, alpaca_secret, openai, finnhub, smtp_password, backup_password, backup_credentials), encrypted value, last 4 characters, saved at, tested at, test result |
| `events` | time, level, type (reconciliation, kill switch, error, acknowledgment), details JSON, affects (decision, order, fill or none), resolved at, resolution (auto or dismissed), note |
| `readiness_snapshots` | session date, run, criteria JSON (value, target, status, sample, estimate) |
| `readiness_manual` | item, done, note, changed at |
| `users` | username, password hash (Argon2id), TOTP secret (encrypted like the keys), recovery code hashes, password changed at (PR 23) |
| `sessions` | token hash, device name, user agent, IP, created, last seen, expires, keep signed in, confirmed until (risky actions) (PR 23) |
| `sign_in_attempts` | time, IP, result (ok, wrong, locked) (PR 23; kept 90 days) |
| `insights_snapshots` | session date, strategy, period, computed at, JSON (headline, funnel, findings, slices, calibration) (PR 22) |
| `log_reviews` | fingerprint, level, sample message, review (expected or fixed), note, reviewed at, app version (PR 20; in the trading database, so it is backed up) |
| `llm_calls` | sent at, session date, source (live, shadow, review), strategy, kind, linked decision, shadow decision or review, cycle, symbol, context, model, temperature, prompt version and hash, tokens, cost and source, latency, final action, outcome (success, warning, error), reasons JSON, error type, HTTP status, error message, request id, raw response, backfilled (see LLM call history) |

Every connection uses WAL mode and a busy timeout (`storage.sqlite_busy_timeout_seconds`, 30). No job keeps a write transaction open while it waits for a model call or any other network call. A daily copy of the database is made at 03:00 Amsterdam time with SQLite's online backup API (never a plain file copy, which can catch a write half-done), checked with `PRAGMA integrity_check`, and kept for 14 days. Migrations are never edited once merged; a schema change is always a new revision. At startup the app compares the schema with the models and refuses to start with a clear message if they differ.

## Testing and engineering rules

These go into `AGENTS.md` at the repo root so Devin follows them on every PR.

- **No network in tests.** Every external call goes through an interface with a fake. A test that touches the network fails: block sockets in the test setup (for example `pytest-socket`).
- **Simulated mode.** `MODE=simulated` runs the full cycle against recorded bars and the fake broker, at accelerated time. It is used for demos and end-to-end tests.
- **Time is injected.** No direct `datetime.now()` in engine code; use a clock object so tests can run any date, including DST-change weeks and early closes.
- **Required tests:** every indicator against hand-checked values; every risk gate pass and fail; bar aggregation into session-aligned 1h bars; the same-session re-entry rule; reconciliation mismatches; a position-without-stop recovery; DST weeks (25 Oct to 1 Nov 2026); an early-close day; invalid model output.
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
5. **Execution.** Alpaca paper broker, bracket entries, stop moves, exits, trade-updates stream, reconciliation, 15-minute position check, PDT guard (removed in PR 26), risk gates, kill switch. *Done when:* a simulated session runs with every position protected by a stop, and startup, the paper-account check and reconciliation are verified read-only against the real paper account. Marc runs the first real paper session himself after review, with the paper account reset to $10,000.
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
22. **Insights.** Daily insights snapshot per strategy and period (headline numbers, funnel, findings, slices by 16 dimensions, calibration, execution, overnight, skipped setups), `entry_cycles` strategy override and "Test in a shadow", Insights screen, findings in the weekly review facts. Uses PR 19 for after-exit data and PR 21 for the stop and target basis when present. *Done when:* on a fixture run every number matches a hand calculation, sample statuses switch at 10 and 30 trades, a planted difference becomes a finding and noise doesn't, and the page shows the before-data state under 10 trades.
23. **Sign-in and access.** Users, sessions and sign-in attempts tables; `make create-user`; password and authenticator sign-in with recovery codes; session cookie, idle and absolute expiry, CSRF and WebSocket origin checks; rate limits and lockouts; "Confirm it is you" before risky actions; `SA_AUTH` with refusal to start without sign-in off loopback; instance id in client order ids and the "another instance" check in reconciliation; Sign in screen, Settings → Access, Sign out, new notifications. Built and tested locally. *Done when:* every API route and the WebSocket refuse without a session (a test walks the route table), a lockout starts after 5 failures, a risky action without a fresh confirmation is refused, the app refuses `SA_AUTH=off` on 0.0.0.0, and an order with another instance id turns the kill switch on.
24. **VPS deployment.** Dockerfile and Compose (`app`, `caddy`), security headers, bundled fonts, `/healthz`, graceful stop within the grace period; `deploy/provision.sh` (SSH, ufw, fail2ban, updates with the Sunday window, chrony), `deploy/deploy.sh` (market-hours guard, backup, migrate, health check, rollback), `deploy/restore.sh`; CI image builds; offsite encrypted backups with monthly restore test; heartbeat and uptime check; resume after a clean planned restart; Settings → Server and its notifications; `docs/DEPLOY.md` including moving the local database. After PR 20 (shutdown) and PR 23; the bundled fonts, `python -m app.serve` and file modes are shared with PR 28, built by whichever comes first. The first real deploy waits for PR 28. *Done when:* a fresh VPS can be provisioned and deployed from `docs/DEPLOY.md` alone, a failed health check rolls back to the previous image, a restore from the offsite copy passes `integrity_check`, and stopping the app outside a session triggers the heartbeat email.
25. **Error pages and API errors.** One JSON error shape for every `/api` error with stable codes and `error_id` on 500; the frontend's handling per status (sign-in redirect and return, confirm-and-retry once, conflict reload, field messages, retry-after, error toasts); not-found pages for routes and records, a per-screen error boundary, the first-load "isn't answering" page and the offline banner; static 502/504 and 503 pages in `deploy/errors/` with Caddy wiring and the maintenance flag in `deploy.sh` and `restore.sh` (when PR 24 is merged). *Done when:* a test walks every API route's error paths and finds only the JSON shape (no traceback or HTML), each status in the table leads to its documented behaviour, a thrown render error on one screen leaves the others working, and with the app container stopped the browser gets the static 502 page and the API gets JSON.
26. **Day-trade rule retired.** Remove the same-day exit deferral, the day-trade guard and every read of `pattern_day_trader`, `daytrade_count` and `daytrading_buying_power`; add the same-session re-entry rule; stop writing `day_trades`; update Positions (Today card), the dashboard risk card, the decision drawer gate, Settings notifications, States, Insights and shadows. Do it first in the queue, since live trading currently defers exits for no reason. *Done when:* a model exit on the entry day is carried out, a symbol stopped out today is refused a new entry until the next session (live and shadows), a test fails on any reference to the removed Alpaca fields, and no screen mentions day trades as a limit.
27. **Protective stops hardened.** Confirm the position and orders at Alpaca before any protective order; a market sale (`stop_breached`) when the stored stop is at or above the market, replacing the one-cent clamp; the orphaned-stop repair (cancel the target, OCO exit order, stop alone as fallback); every sell capped at the confirmed quantity; broker-side `no_shorting` and 1× margin set and checked at startup; the missed daily screen run at startup. *Done when:* tests cover a breached stop (market sale, no stop placed), a stop leg missing while the target holds the shares (OCO placed, gap logged), a sell larger than the confirmed position (capped), a flat position (nothing sent), and a start after 08:10 without today's screen (screen runs, the first cycle waits or uses the previous list with a warning).

28. **Security hardening.** From the 27 October audit: the request gate (Host, Origin and `Sec-Fetch-Site` on writes, JSON-only writes, body size, WebSocket checks) and the app's own security headers, with bundled fonts; one source for the bind address (`python -m app.serve`); exceptions and URL tokens scrubbed in every log handler; prompt names whitelisted, contained and hash-checked against `prompts/manifest.json`; clamped list parameters, capped exports and safe log search; frontend error reports limited per address and sanitised; stored SMTP password never sent to a changed server, outbound URLs https and public; `umask 077`, file modes, read-only run databases and an exclusive `run.lock`; ruff `S` rules, dependency audits, gitleaks, pinned Actions, `make security` and `docs/SECURITY.md`. Before PR 23 is merged; no migration and no trading change. *Done when:* a cross-site `text/plain` POST, a bodiless cross-site POST, a rebinding `Host` and a foreign-origin WebSocket are all refused while the GUI works normally; a fake key inside an exception never reaches any log; `../` in a prompt version gets 422; no GET route returns 500 for bad query values; a changed SMTP host without a password gets 422 and no connection is made with the stored password; files under the data folder are `0600`; and `make security` passes in CI.
29. **Model providers.** One model client interface with an OpenAI and an Anthropic adapter (and fakes); the `models` list in config (provider, prices, cutoff, effort) replacing `model_prices` and `backtest.model_cutoffs`; Anthropic structured outputs with unsupported schema keywords removed from the sent copy only; no temperature for Claude models, effort instead; stop reasons and errors mapped to the existing outcomes; OpenAI and Anthropic keys, each optional with at least one required, and Remove; `provider` and `provider_request_id` in `llm_calls`; model pickers from the list for live decisions, the weekly review, the daily briefing and verdicts in Settings, the shadow dialog, backtests and replays, with models that need a missing key disabled; LLM inspector and Logs filters. The default live model stays `gpt-4o-mini`. *Done when:* the app runs with only an Anthropic key (live and all review tasks on Claude models) and with only an OpenAI key, Resume is blocked with no model key or when a used provider has none, a shadow on a Claude model runs next to live in simulated mode with the fake adapter, every answer goes through the same Pydantic checks, a Claude refusal or truncated answer becomes skip, no unsupported schema keyword is sent, a missing provider key skips that shadow without delaying live, and a backtest with a Claude model before its cutoff is refused.
30. **Decision v3.** Snapshot v3 for v3 strategies (rounded numbers, no duplicate fields, plausible-only bid and ask, `checks` with trend labels, room and earnings, `distance_atr` on levels and levels in the way, `breakeven_confidence` per option); the room filter in option builder version 2 (`decision.v3.min_room_atr`, strategy-overridable, `no_options` with a room reason); `prompts/decision.v3.md` with conflicts and thesis before the action and the new `data_flags`; code checks for confidence against break-even (`edge_too_small`), data flags (`data_flag`, WARNING and event) and room at order time; `make replay PROMPT=decision.v3` with its report additions; v3 in strategies, shadows, backtests and replays; held positions in v3 (stop options with `locked_r` and `distance_atr`, minimum stop move, break-even from +1R, minimum distance for trails, position context, filled invalidation, scheduled exit, held confidence per action, the lock-in claim check); the v3 decision drawers. v1 and v2 are untouched. *Done when:* the SBUX held snapshot of 28 October 14:46 offers only T3 under v3 (T1 dropped as too small a move, T2 as below +1R) and shows the earnings exit as scheduled; the SBUX snapshot of 28 October gives `no_options` with a room reason under v3; a fixture with room gets options with break-even and distances; an entry with confidence below break-even + 0.10 becomes skip; an enter with a data flag becomes skip and logs an event; v2 snapshots and replays are byte-for-byte unchanged; and a v3 shadow runs next to live in simulated mode.
31. **Exit fills and reconciliation.** The trade-updates handler closes a position row when a fill brings it to zero, with the exit reason and fill; reconciliation explains a flat position from Alpaca's filled orders before calling it a mismatch (`closed_while_offline`); every real mismatch records an event, logs CRITICAL, shows a banner and blocks entries until acknowledged; ruff `DTZ` rules for timezone-aware datetimes. *Done when:* a stop fill closes its position from the stream with no reconciliation event; a stop that filled while the app was down closes at the next start as `closed_while_offline` without a banner; a flat position with no explaining fill gives a CRITICAL mismatch with a banner and blocked entries until acknowledged; and `make lint` fails on a naive `datetime`.
32. **Review attribution.** From the model-call analysis of 30 October: verdict facts with the model's action, code's change and its reason, execution, the checks at decision time and option outcomes (`outcomes.option_touches`); draft verdict labels by code from the outcome table; `prompts/verdict.v2.md` writing the reason and a thesis check; confirmed verdicts counted per model call; `prompts/weekly.v2.md` with the sample and evidence, and suggestions only from 10 closed trades in the run; the briefing status line shown once; known prompt versions only (`unknown_prompt_version`). *Done when:* a model enter blocked for reward to risk gets a verdict on the enter with "Code blocked: reward to risk 1.4, min 1.5" and is not counted as a model skip; a skip whose option A touched its target first is drafted bad call by code; an enter whose thesis says "daily trend up" with the daily trend mixed gets `contradicts`; a weekly review with 1 closed trade in the run has no suggestion; a briefing shows its status line once; and a strategy set to `decision.v4` is refused at save and at startup.
33. **Entry selection.** `selection.mode` (`rank` default, `expected_profit`), `confidence_weight` and `last_slot_min_ev_r` as settings and strategy overrides; for `expected_profit`: pre-score call order, collect-then-choose, the exact best set within slots, sector cap, open risk, cash and orders per cycle, submit in expected-profit order with the next-best fallback, `not_selected` for the rest; selection stored per decision and cycle; the drawer block; `make replay-selection` comparing both modes on stored cycles. Gates, sizing and orders unchanged. *Done when:* with `rank` every existing cycle test and replay is byte-for-byte unchanged; the XOM, MSFT, AMZN example of the design with 2 free slots chooses XOM and MSFT ($41.08) where rank order chooses AMZN and MSFT ($27.42); a sector-cap case picks the best set that fits; a chosen entry failing the fresh quote is replaced by the next best that fits; an enter with expected R ≤ 0 is never chosen; supervised mode queues only the chosen set; a shadow with `expected_profit` runs next to live; and the replay report lists every cycle where the modes differ.
34. **Decision v4.** From the analysis of 31 October: snapshot v4 (`previous_decision` with code-computed `since`, headlines with `about_symbol` and `new`, capped headlines and levels, `stop_group` on options, `checks_at_entry`, `thesis_holds`, time held, session minutes left); `prompts/decision.v4.md` with confidence defined for every action and `best_option_id`, conflicts with `outweighed_by`, `what_changed`, news effects, path and exit rules; code checks for the flip-flop guard, confidence sanity, path claims, unresolved conflicts and the claim-check fix; the `checks_gate` strategy override; shadow input snapshots stored; `make eval-decision` with `backend/eval/decision_v4.json`; the v4 drawers. v1–v3 untouched. *Done when:* the TSLA case of the design becomes `skip` · `flip_flop` while a skip → enter after a new headline about the symbol passes; a held PG answer of 0.85 at neutral 0.15 is stored with `confidence_outlier`; IBM call 1734's conflict "levels L2 and L1 in the way" no longer fails the claim check; a skip carries a valid `best_option_id`; `thesis_holds` is false for a position whose 1h trend and momentum turned against it; the eval runs v3 and v4 on the same calls and reports per category; and a v4 shadow runs next to live in simulated mode.
## Reviews

The model writes a weekly review, a daily briefing and a draft verdict on each decision, so the 4-week paper run can be read without digging through the log. Code computes every fact; the model only writes sentences about them. Reviews never change settings, prompts or trading. Built as PR 9, before the 4-week paper run.

| Feature | When | Content |
| --- | --- | --- |
| Weekly review | Saturday 10:00 Amsterdam, covering Monday to Friday | Return, difference with SPY, trades won and lost, average R, max drawdown, model cost, best and worst trades, what skipped setups did after 5 sessions, confirmed verdicts, results by regime and symbol. Model text: a summary of at most 120 words, up to 3 observations and at most 1 suggestion, marked "not applied" |
| Daily briefing | Trading days, 09:00 New York (15:00 Amsterdam most of the year); can be turned off | Open positions with stop distance, earnings today or tomorrow for held and watched symbols, market regime, yesterday's result, active banners. At most 80 words |
| Decision verdict | Once a decision's 5-session outcome is in | For every decision where the model answered enter or skip (not rules no_calls), including enters that code blocked: a draft verdict of good call, bad call or unclear on the model's call, a one-line reason and a thesis check (from PR 32, see Verdicts). Marc confirms or changes it; only confirmed verdicts count in reviews |

**Number check.** Every number in the model's text must match a fact it was given (after rounding). Status facts (open positions, earnings today and tomorrow, active banners) are written by code as a fixed line, not by the model, and the model's text may not call a known-empty fact "unavailable". If one doesn't, or there is no OpenAI key, the call fails or the review cost cap is reached, the app shows a plain template built from the facts instead, labelled with the reason (for example "Plain review: number check failed"). The failure is logged.

**Verdicts** (PR 32, `verdict.v2`). A verdict judges the model's call, not what code did with it. When a gate or check changed the model's enter into a skip, the verdict is still about the enter; the code's block is shown next to it and never counted as a model skip.

- **Facts.** Besides today's facts: `model_action` and `option_id` (what the model answered), `final_action`, `changed_by_code` with `change_reason` (the gate or check that changed it, for example `rr_below_min`, `spread`, `max_positions`, `edge_too_small`), `execution` (`filled`, `not_filled`, `not_sent` or null), `checks_at_decision` (trend 1h, trend daily, regime trend and volatility, room in ATR; computed with the decision.v3 rules from the stored input, so v2 decisions get them too) and, for decisions with options, each offered option's `rr` and touch (Option outcomes); and `draft_verdict`, the label from the table below.
- **Draft label by code**, from the outcome:

| Model's call | Outcome | Draft |
| --- | --- | --- |
| enter | the chosen option (or the real trade) touched its target first | good call |
| enter | stop first | bad call |
| enter | neither, not filled, or unknown | unclear |
| skip, with options | at least one offered option touched its target first | bad call |
| skip, with options | every option touched its stop first or didn't fill | good call |
| skip, with options | anything else (nothing touched, or unknown) | unclear |
| skip, no options (decision.v1) | 5-session move above `reviews.skip_flat_pct` | bad call |
| skip, no options | below −`skip_flat_pct` | good call |
| skip, no options | within ±`skip_flat_pct` | unclear |

- **Model text.** The model writes the `reason` (one line: what the model called, what code did if anything, what happened) and a **thesis check**: `consistent`, `contradicts` or `unclear`, with a short `contradiction` naming the claim when the thesis says something the facts at the time didn't support (for example "daily trend up" while `checks_at_decision.trend_daily` is `mixed`). The thesis check is about the reasoning at the time and is independent of the label: a good call with a contradicting thesis is shown as such. The number check covers both fields.
- **GUI.** Each verdict to confirm shows the model's call, code's change with its reason, the outcome and the draft label, and the thesis check (a "Thesis contradicts the facts" chip with the claim). Drafts written under `verdict.v1` show the same call and code line, built from the decision; they have no thesis check. The decision drawer's verdict block shows the same.
- **Counting.** The weekly facts count confirmed verdicts in three groups: model enters that were executed, model enters blocked by code (with the reasons), and model skips; plus the number of thesis contradictions.

**Weekly suggestion** (PR 32, `weekly.v2`). The facts carry the sample: closed trades and model skips with a 5-session outcome, this week and in the current run, and confirmed verdicts. A suggestion is allowed only when the current run has at least `reviews.min_closed_trades_for_suggestion` (10) closed trades; code sets `suggestion_allowed` and drops any suggestion it didn't allow (INFO log). An allowed suggestion names its evidence and its limit in an `evidence` line, which the number check covers. Otherwise the review shows "No suggestion yet: 3 closed trades in this run, 10 needed".

**Briefing text** (PR 32). The status line is written by code and stored only in `status_line`; `text` holds only the model's sentences, and the GUI and email show the status line above the text. Briefings stored with the status line in front of the text are shown without the repeat (the leading copy is removed on read; stored rows are not rewritten).

**Model and cost.** Each review task has its own model, OpenAI or Anthropic, from the `models` list: `reviews.models.weekly`, `reviews.models.briefing` and `reviews.models.verdict` (default gpt-4o-mini each). Temperature 0.2 for OpenAI models, with versioned prompts (`weekly.v1` and `v2`, `briefing.v1` and `v2`, `verdict.v1` and `v2`; `reviews.prompts` picks one per task) that are never edited once used. All three are chosen in Settings → Reviews (model and prompt per task, PR 34); each prompt select lists only that task's known versions, and saving another task's or an unknown version is refused (422 `unknown_prompt_version`). Review calls have their own monthly cap of $2 and can never use budget or rate limits needed by trading decisions. Their cost also comes from the `models` list and counts only toward the review cap.

**Missed runs.** Unlike cycles, a review or briefing missed while the app was down is written at the next start, labelled with its original date. A briefing older than its trading day is skipped.

**Storage.** A `reviews` table (kind, period, facts JSON, text, source model or template, reason, model, prompt version, tokens, cost, created at) and a `verdicts` table (decision, verdict, reason, thesis check and contradiction (PR 32), prompt version, source draft or Marc, created and updated at).

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

A symbol that passes every check is eligible. An eligible symbol stays eligible until it fails 3 screens in a row; a new or warming-up symbol becomes eligible on its first pass. The eligible list is fixed for the whole session. If the screen itself fails (data error), the previous list is kept and a banner says so. If the app was down at 08:10 on a trading day, it runs the missed screen at startup. When that can't finish before the session's first cycle, that cycle uses the previous list and logs a WARNING; a banner shows the date of the list in use until today's screen is done. Held symbols are always handled by cycles, position checks, exits and earnings exits, whatever their screen status; a held symbol removed from the pool leaves it when the position is closed.

**Rank.** At the screen, each eligible symbol gets a rank: its return over the last 20 completed sessions minus SPY's over the same sessions (ties broken by dollar volume). The rank is shown on the Universe screen and the dashboard and is part of the input snapshot.

**Cycles at scale.** Cycles run over the eligible symbols plus held symbols. 5-min bars are fetched with multi-symbol requests; quotes and news only for symbols that get a model call. Model calls go to held positions first, then to candidates that pass the rules in rank order (with `selection.mode: expected_profit`, in pre-score order; see Entry selection), up to `max_calls_per_cycle` (10); the rest are stored as `no_call` with reason `call_cap`. The shared limit of 150 Alpaca calls per minute still applies. Each cycle's duration is stored; a cycle longer than 10 minutes shows a warning banner. 5-min bars older than 120 sessions are deleted after the 03:00 backup.

**GUI.** A Universe screen in the Control group: mode, counts (pool, eligible, held, excluded, warming up), today's screen as a funnel, and the pool table (rank, symbol, sector, price, 20-session average dollar volume, return vs SPY, status with reason) with filters, search, add, CSV import and remove. Statuses: eligible, eligible with 1 or 2 fails, held, excluded with the failed check, warming up. The dashboard watchlist gets filters (All, Pass rules, Held, Earnings within 3 days) and search; in screened mode it is sorted by rank and shows 15 rows, then "Show all". Settings → Trading gets the universe mode and the screen thresholds. In fixed mode the Universe screen shows the fixed list and points to Settings.

## Strategies and shadows (v0.2)

A shadow strategy decides on exactly the same inputs as live, every cycle, but never trades. It answers questions like "would another prompt, a bigger model, or no model at all have done better?" with evidence from the same market, instead of opinions. Built as PR 13.

**Strategies.** A strategy is a named configuration: kind (`llm` or `rules_only`), prompt version, model, temperature, and overrides of the entry rules and code checks (RSI range, minimum confidence, stop distance range, minimum reward to risk, and from PR 22 `entry_cycles`: the cycles in which it may enter). Risk gates, sizing, the re-entry rule and earnings handling can never be overridden; every strategy runs under the live limits. Live is the strategy defined by the current settings version. `rules_only` enters whenever the rules pass, with the stop at entry − 2 × ATR(1h) and the target at 2R, and exits only through its stop, target or the earnings exit.

**Running shadows.** At most 3 shadows are active or paused; archiving frees a slot. A paused shadow makes no model calls and no entries but keeps managing its virtual positions (stops, targets, earnings exits). After each live cycle has finished and its orders are sent, every active shadow runs on that cycle's stored input snapshots: the market part is reused as it is, and the position part is rebuilt from the shadow's own virtual portfolio. Calls go to the shadow's held positions first, then to candidates that pass its rules in rank order, up to the same 10-call cap. Shadow work never delays live: if the next live cycle or position check is due, unfinished shadow work for that cycle is skipped and recorded as missed, with the reason (`time` or `cost_cap`). Live's cycle also builds snapshots for symbols held by any active or paused shadow, so a shadow can decide on them; live makes no rules check, model call or decision for a symbol only a shadow holds.

**Virtual trading.** Each shadow has a virtual portfolio that starts with the live run's starting equity when the shadow starts. Shadow orders are timed at the live cycle's scheduled time. Entries use the same limit price and timeout as live and fill when a later 5-min bar trades at or through the limit, at the limit or at a better bar open. The stop is already checked on the fill bar (if its low reaches the stop, the stop counts); the target counts from the next bar. Stops and targets are checked on 5-min bars at every position check and cycle; when both are touched in the same bar, the stop counts; a gap through the stop fills at the open. Market exits (model exit, earnings exit, Stop & flatten) fill at the next 5-min bar's open plus slippage. When live fetched a market price and quote for that symbol and cycle, the shadow uses them under the same rules as live (a price or quote that blocks live blocks the shadow). When live fetched none, the shadow uses the last closed 5-min bar's close as its market price, marked `synthetic`: entry limit = close + 0.05%, stop at least 1.5 × ATR(1h) below it; the bar and ATR are required (`reference_missing` otherwise). Shadows place no real orders and skip the second, order-time fetch; this slightly favours shadows, and the Strategies screen shows how many shadow entries used a synthetic price. Slippage per side, dividends from corporate actions, the re-entry rule and earnings exits apply as for live. While live can't place new entries (paused, kill switch on, unacknowledged reconciliation mismatch), shadows can't either, recorded under the same gate name; they keep managing their virtual positions. Stop & flatten also closes every shadow's virtual positions at the next 5-min bar's open plus slippage. All other gates (risk per trade, open risk, positions, sectors, daily loss, losing streak, drawdown, re-entry) are computed on the shadow's own virtual portfolio; the shock brake is market-wide. The shadow code has no broker client at all; a test fails the build if it can reach one.

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

**Model calls.** An `llm` strategy can only be backtested from the day after its model's knowledge cutoff (the model's `cutoff` in `models`); an earlier start is refused before the run begins, because the model may already know what happened. Answers are cached by provider, model, temperature (or effort for Claude models), prompt version and hash, and input snapshot hash, so a rerun on the same inputs costs nothing and gives the same result. Live and shadows only write to the cache and never read from it, so a live decision is always a fresh model call; a backtest decision answered from the cache is marked `cached`. Before a run starts the screen shows the number of sessions, bars to download, estimated model calls, estimated cache hits and estimated cost, plus the cost at the 10-call cap. Calls per LLM strategy = sessions × cycles × that strategy's average real calls per cycle in the current run (a shadow uses its own average once it has at least 20 cycles, otherwise live's; with no history the 10-call cap); rules-only is 0. Cache hits = `llm_cache` rows with the same model, temperature and prompt hash whose cycle time falls in the period, labelled "≈". Cost = (calls − cache hits) × the strategy's average tokens per call × the model's prices in `models`. Run is disabled when the expected cost is above what is left of the monthly cap; when only the cost at the cap is above it, a warning says the run may stop early. Backtests have their own monthly cap ($20); a run that reaches it stops cleanly and keeps its results so far.

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

**What counts as a call.** One request actually sent to OpenAI or Anthropic by live decisions, shadows or reviews. Nothing that wasn't sent appears: rules no_calls, the call cap, `price_stale` or `price_suspect`, a missing key, a reached cost cap, missed shadow calls. Those stay visible as decisions or review labels. Backtest calls stay in their run's database and are shown on the Backtest screen.

**Outcome.** Set once, when the call and its linked row are stored, and never recomputed. Later events (an approval expiring, an order not filling) don't change it. An error outranks warnings; a call can have several warning reasons.

| Outcome | When | Reasons |
| --- | --- | --- |
| Success | A valid response used as the model chose; for reviews, the number check passed | — |
| Warning | A valid response, but something needs a look | `changed_by_code` (the final action differs from the model's: a code check or risk gate), `input_incomplete` (an input field was `"unavailable"` or the symbol's headlines were `truncated` or `failed`), `number_check_failed` (review text failed the number check; the plain template was used), `slow` (latency above `inspector.slow_call_seconds`, 15), `unsupported_claim` (decision.v2: the explanation check failed) |
| Error | No usable response | `timeout` (30 s, not retried), `api_error` (HTTP error or connection failure; status and the provider's error code stored), `invalid_output` (unparseable, fails the schema or Pydantic, or picks an option that wasn't offered; counts as skip or hold), `refusal` (OpenAI's refusal field is set, or Anthropic's stop reason is `refusal`) |

A size reduced by a gate is not a warning; the model's action was kept.

**Storage.** A new `llm_calls` table, one row per call:
- What and where: sent at, New York session date, source (live, shadow, review), strategy (shadows), kind (decision, briefing, weekly, verdict), the linked decision, shadow decision or review, cycle, symbol, context.
- How: model, temperature, prompt version and hash, tokens in and out, cost and its source, latency in ms, final action.
- Result: outcome, reasons JSON, error type, HTTP status, error message, provider and provider request id, raw response text.
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
- **Errors:** instead of the raw response, an Error block with the type, HTTP status, the provider's message and the request id.

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
| WARNING | Something degraded but handled | `price_stale`, `price_suspect`, `spread_unknown`, news truncated or failed, slow or invalid model output, a fetch retried, a cycle over 10 minutes, a limiter wait over 5 s, an unclean previous shutdown, a missing optional data source |
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
  - uvicorn's and the libraries' loggers routed to the same handlers, at WARNING and above for every logger outside the app's own (`app.*` and `frontend`), not a fixed list of names, unless `logging.library_debug` is on, so keep-alive and header chatter (httpcore, urllib3, h2 and any library added later) doesn't fill the file or the log index.
- **API errors.** An unhandled API error returns 500 with an `error_id`; the same id is on the log line with the traceback.
- **Frontend errors.** `window.onerror`, `unhandledrejection` and a React error boundary send the error to `POST /api/logs/client`, at most 10 per minute per tab. They are logged at ERROR with logger `frontend`. The error boundary shows "Something went wrong" with the `error_id` and a Reload button instead of a blank page.
- **Logs and events.** The log is for diagnosis. Errors that need action still create `events` and notifications as specified; a log line never sends a notification by itself.

**Graceful shutdown.**
- **Steps.** Ctrl+C (SIGINT) or SIGTERM, on macOS, Linux and Windows, with `make run` and `make dev`, starts a graceful shutdown:
  1. Log "Shutdown requested", show the "Shutting down" banner in every tab, and pause the scheduler: no new cycles, position checks or jobs. API writes return 503 "shutting down"; reads keep working.
  2. Wait up to `storage.shutdown_grace_seconds` (60) for running work to finish. A cycle finishes the symbol it is on. Order submission, from sending to storing Alpaca's answer, is shielded and is never cut off halfway.
  3. Work still running after the grace period is cancelled. Its cycle is stored as `interrupted` (reason shutdown), and any order whose answer didn't arrive is left to reconciliation at the next start (hard rule 3), with a CRITICAL line naming it.
  4. A running backtest child gets its cancel flag. After 10 s it is stopped by its PID and marked `interrupted`.
  5. Pending emails get up to 5 s. WebSocket clients and the Alpaca, model (OpenAI and Anthropic) and data clients are closed.
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

## Insights

An Insights screen in the Learn group shows where the results come from, sliced the way US stocks behave: by time of day in the session, sector, earnings distance, opening gaps and overnight holds. It also shows what the market, the fills and the calendar cost, separately from the model's choices. It never changes anything. A finding is a question to test in a shadow or a backtest, not a rule. Built as PR 22.

**Scope and timing.**
- **What is computed.** Insights are computed by one function after each session's close (after the readiness snapshot) for each active strategy (live, shadows, rules-only) and for three periods: this run, the last 20 sessions, and all runs.
- **Storage.** The result is stored as a snapshot; the screen reads the latest one and never recomputes per request.
- **P&L basis.** All P&L is adjusted (slippage and dividends). 1R is the trade's initial risk.

**Headline numbers.**

| Number | Definition | Why it matters for stocks |
| --- | --- | --- |
| Expectancy | Average R per closed trade, with a 90% range (± 1.64 standard errors) | Whether there is an edge at all, and how sure we are |
| Edge over SPY at the same exposure | Run return − SPY return over the same sessions × the average share of equity invested | The agent is mostly in cash; comparing with SPY fully invested is unfair to it, comparing with nothing flatters it |
| Beta to SPY | Slope and correlation of daily equity returns against SPY's, from 20 sessions | Whether the result is just the market going up |
| Overnight vs in session | P&L of held positions from the previous close to the open, versus from the open to the close or exit | Stops at Alpaca can't protect against overnight gaps; the edge should come from the session |
| Gaps through the stop | Count of stop exits that filled more than 0.1R below the stop, and the extra R lost beyond −1R | The real risk per trade is larger than 1R when gaps are frequent |
| Model cost per trade | Decision model cost ÷ closed trades, and as a share of gross profit | Whether the model pays for itself |

**Findings.**
- **When a slice is a finding.** It has at least `insights.min_trades` (30) closed trades, and its average R differs from all other trades by at least `insights.finding_sigma` (2) times the noise (the standard error of the difference).
- **Early signals.** Slices with 10–29 trades and the same size of difference show as early signals.
- **Ordering.** Findings are listed newest first, with how long each has held.
- **Testing a finding.** Each one links to its trades and, when the dimension maps to a strategy override, offers "Test in a shadow" with that override filled in. PR 22 adds the override `entry_cycles`, the cycles in which a strategy may enter, so time-of-day findings can be tested.
- **Wording.** The screen says that checking many slices finds some differences by chance, and that a finding is a question.
- **Weekly review.** Its facts include new findings and findings that stopped holding.

**Where trades come from.** A funnel for the period:
- symbol checks;
- passed the rules pre-filter;
- model called for a candidate;
- model proposed an entry;
- passed code checks;
- passed risk gates;
- filled;
- closed.

Next to each step is what was lost and why: rules, call cap, price checks, model skips, each code check, each risk gate, and unfilled limits. This shows whether the agent trades rarely because of the market, the rules, the model or the risk limits.

**Slices.** One dimension at a time.
- **Dimensions:**
  - entry cycle (time of day, New York with Amsterdam alongside);
  - weekday;
  - sector;
  - symbol;
  - market regime;
  - days to earnings at entry (4–10, 11–20, 21+);
  - the day's opening gap (down more than 1%, within 1%, up more than 1%);
  - entry vs session VWAP;
  - pullback depth (distance from the 1h EMA 20 in ATR: under 0.5, 0.5–1.5, over 1.5);
  - strength vs SPY (20-session, terciles);
  - SPY on the day at entry (down, flat, up, at 0.3%);
  - volatility (ATR percentile terciles);
  - confidence (0.1 buckets);
  - stop and target basis (decision.v2);
  - sessions held (1, 2–3, 4+);
  - prompt and model.
- **Columns:** trades, win rate, average R with its 90% range, total R, model skips with missed and avoided (see Skipped setups), and the sample status.
- **Sample status:**
  - finding: 30 or more trades and a large enough difference;
  - early signal: 10–29, greyed;
  - hidden: under 10, counted in one line.
- **Rules shown as rows.** A slice with no entries because of a rule (the 15:46 cycle) shows that rule instead of numbers.

**Calibration.**
- **Chart.** Per 0.1 confidence bucket, stated confidence against the share of entries that reached the target before the stop, with the same sample statuses.
- **Gap.** The weighted gap is the same number as the Readiness criterion. Under decision.v1, confidence has no defined meaning, so the chart says it is approximate.

**Execution.**
- **Fills:** entry fill rate (with the worst cycle); entry and exit slippage in basis points (exits split by stop, target and agent exit).
- **Gaps:** stops filled through the stop.
- **Rules and calendar:** agent exits on the entry day with their average R; earnings exits with their average R and the median move in the 5 sessions after (from Tail metrics, when PR 19 is merged).
- **Dividends** received.

**Overnight and in session.**
- **Totals.** In-session and overnight P&L.
- **Nights.** The number of nights positions were held, the share of nights that gapped against the position, and the largest gap in R with its symbol and date.

**Skipped setups.**
- **How skips are scored.** Every model skip is scored with the rules-only geometry: entry at the decision's limit, stop 2 × ATR below, target 2R, within 5 sessions. "Missed" means the target came first, "avoided" means the stop came first, "neither" means neither was hit.
- **Baseline.** The average R of taking every skip is shown as the rules-only baseline on the same setups.
- **Reading it.** More avoided than missed means skipping adds value.

**Shadows and rules-only.** A strategy picker shows the same page for any strategy. Rules-only has no model, so its findings and slices work, but calibration and skips don't apply.

**Storage.**
- `insights_snapshots`: session date, strategy, period, computed at, JSON with headline numbers, funnel, findings, slices per dimension and calibration.
- Each position's overnight P&L is computed from daily bars and fills; the after-exit data comes from Tail metrics when present.

**API.** `GET /api/insights?strategy=&period=` returns the latest snapshot as numbers, and `GET /api/insights/trades?strategy=&period=&dimension=&slice=` returns the trades behind a slice.

**GUI.** The page shows, top to bottom:
- strategy and period toggles, with when the snapshot was computed;
- the headline tiles, each explaining its definition on hover;
- Findings;
- "Where trades come from" next to "Skipped setups";
- Slices, with a dimension picker;
- Calibration, Execution, and Overnight and in session.

Before 10 closed trades, the page shows the funnel, skips and execution, and says when slices start to count.

## VPS deployment

Production runs on a rented Linux VPS, so the agent trades and watches positions whether or not Marc's computer is on. Development stays local. The provider isn't chosen yet; this section describes what the server needs, not who supplies it. Built in two PRs:
- **PR 23 (sign-in and access)** works and is tested locally.
- **PR 24 (deployment, backups and monitoring)** puts it on the server.

**Server.**
- **Size.** 2 vCPU, 4 GB RAM (backtests run as a separate process), at least 40 GB disk, in an EU data centre close to Marc. Hourly cycles don't need low latency to New York.
- **System.** Ubuntu 24.04 LTS on UTC; the app sets `TZ=Europe/Amsterdam` itself.
- **Domain.** A DNS name pointing at the server, for example `stock.example.nl`.

**How it runs.** Docker Compose with two containers:
- **`app`:**
  - The backend serves the built frontend, with one Uvicorn worker, because exactly one engine may run per database.
  - It runs as a non-root user with `no-new-privileges` and all capabilities dropped, with a read-only root filesystem and every writable path on the `/data` volume (database, `secret.key`, logs, log index, backups, bar store, replays).
  - It listens only on the internal Docker network; its port is never published. It starts with `python -m app.serve`, so `SA_HOST` and `SA_PORT` are the only source of the address (see Security hardening).
  - Health check: `GET /healthz`, which returns 200 and the version and nothing else.
  - `stop_grace_period: 90s`, longer than the 60-second graceful shutdown (see Logging, error handling and shutdown), so `docker stop` never cuts a cycle or an order submission.
  - Restart policy `unless-stopped`.
- **`caddy`:**
  - The only public entry point, on ports 80 and 443. It gets and renews the HTTPS certificate automatically and redirects HTTP to HTTPS.
  - Security headers: it always adds HSTS and a restrictive Permissions-Policy. The CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` and COOP (values in Security hardening) are set only when the app hasn't sent them (`header ?Name`), so the app's own headers win once PR 28 is merged. It forwards the original `Host`, so the app's Host check sees the public name.
  - It forwards the real client address; the app trusts `X-Forwarded-For` only from Caddy.

**Server hardening** (scripted in `deploy/provision.sh`, explained in `docs/DEPLOY.md`):
- SSH with keys only: no passwords, no root login. fail2ban on SSH.
- Firewall (ufw): only 22, 80 and 443 open.
- Automatic security updates, with a reboot window on Sunday at 05:00 Amsterdam. That is never during a US session or the 03:00 backup.
- Time sync (chrony), checked hourly by the app: an offset over 1 second is a warning, because cycles depend on the exact time.

**Sign-in (PR 23).**
- **The account.** There is one user, created on the server with `make create-user`. There is no sign-up page.
- **Password.** At least 14 characters, stored as an Argon2id hash.
- **Second step.** A 6-digit TOTP code from an authenticator app is required. 10 single-use recovery codes are shown once, when created.
- **When sign-in is required.**
  - It is required whenever the app listens on anything other than 127.0.0.1: `SA_AUTH=required`.
  - With `SA_AUTH=off` the app refuses to start on any other address (hard rule 10).
  - Locally, sign-in can stay off.
- **Sessions.**
  - A random token in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie, named `__Host-sa_session` when served over HTTPS. The server keeps only its hash. A new token is issued at every sign-in, and changing the password signs out every other session.
  - Signed out after `auth.idle_hours` (12) without activity, and after 30 days at most with "Keep me signed in".
  - Settings → Access lists the sessions and can sign them out one at a time or all at once.
- **Requests.**
  - Every API request and the WebSocket need a valid session, for every HTTP method. Only `/healthz` and the sign-in endpoints are open; nothing open returns more than the version or whether sign-in is required.
  - Write requests also need a CSRF header (double-submit token).
  - The WebSocket checks the `Origin`.
- **Rate limits.** After 5 failed attempts in 15 minutes, an address waits 15 minutes. After 20 failures in an hour across all addresses, sign-in pauses for an hour and a warning is sent. Error messages never say whether the password or the code was wrong.
- **Confirm and password change.** A wrong password or code in "Confirm it is you" or in Change password counts as a failed attempt for that session. After 5 in 15 minutes the session is signed out, the attempts are recorded like sign-in failures, and the "Failed sign-ins" warning is sent. A stolen session cookie must not allow unlimited guessing.
- **The code step.** A wrong code counts as a failed attempt like a wrong password. The ticket between the two steps is single-use, tied to the browser that entered the password, and dropped after 3 wrong codes. A code that was already used (same 30-second step) is refused.
- **Confirming risky actions.** These ask for the password or a code again (valid 5 minutes on that device):
  - Stop & flatten, and Resume after the kill switch;
  - Reset paper account, Start new run, Promote strategy;
  - replacing an API key;
  - changing the password, the authenticator or recovery codes;
  - a settings save that loosens a risk limit, and changing the SMTP server, "To" address or heartbeat URL (see Security hardening).
- **Fails closed.** `SA_AUTH` is parsed once as an enum; with `required`, the app refuses to start unless every route but `/healthz` and sign-in carries the session check. The guard and the session layer ship together.
- **Notifications.** "New sign-in" (info, browser and email on), and "Failed sign-ins" (warning, email on) when a lockout starts.

**Secrets on the server.**
- API keys are entered in Settings over HTTPS, as locally. `.env` holds no secrets.
- `secret.key` sits on the data volume with mode 600, owned by the app user.
- The offsite backup password and storage credentials are stored in the `secrets` table like the API keys (names `backup_password`, `backup_credentials`), never in files or logs.

**Moving from local to the VPS.**
- **Steps.**
  1. Stop the local app cleanly with Ctrl+C. It must not run again against the paper account.
  2. Copy the database and `secret.key` to the server with `scp`.
  3. Start the app on the server.
- **What carries over.** Settings, keys, runs and history move with the database, so the paper run continues.
- **Two copies on one account.** To catch two copies trading the same account:
  - every client order id carries the instance id (`SA_INSTANCE_ID`, for example `sa-local` and `sa-vps-1`);
  - reconciliation treats an open order or fill with another instance id as a critical mismatch: kill switch on, banner "Another instance is trading this Alpaca account" and a notification.

**Restarts.**
- **Default.** The agent starts paused (see Paused at startup).
- **Exception on the VPS** (`ops.resume_after_clean_restart`, on by default when `SA_AUTH=required`). After a planned restart the agent resumes by itself when all of these hold:
  - it was running before;
  - the previous shutdown was clean (no `run.lock` left);
  - the kill switch is off;
  - reconciliation finds no differences.

  Planned restarts include a deploy and the Sunday reboot. The agent then says so in a banner and a notification.
- **Otherwise it stays paused** with a warning banner, and Marc presses Resume.

**Deploying (PR 24).**
- **Builds.** CI builds a versioned image for every tag (`v0.4.2`) and pushes it to a private registry.
- **Deploy.** `deploy/deploy.sh v0.4.2` on the server:
  - refuses between 09:30 and 16:00 New York unless `--emergency` is given (hard rule 14);
  - makes a backup;
  - pulls the image;
  - runs the migrations in a one-off container;
  - restarts `app` and waits for the health check and a clean startup reconciliation.
- **Rollback.** If either fails within 2 minutes, the script restores the pre-deploy backup when a migration ran, starts the previous image, and reports what happened. Positions are safe meanwhile because their stops are at Alpaca.
- **Record.** Settings → Server shows the running version and when it was deployed.

**Backups (PR 24).**
- **Local.** The existing 03:00 local backup stays.
- **Offsite.** The backup is then encrypted (restic) and uploaded to S3-compatible storage at another provider. 30 daily and 12 weekly copies are kept. It includes `secret.key`, protected by the backup password, so a restore works on a new server.
- **Restore test.** Monthly, the latest offsite copy is restored to a temporary folder and checked with `PRAGMA integrity_check`.
- **Failures.** A failed backup or restore test is a warning notification.
- **Manual.** "Back up now" and "Download latest backup" are in Settings → Server. Restoring is done on the server with `deploy/restore.sh` and is described in `docs/DEPLOY.md`.
- **Download.** Asks "Confirm it is you". The file is the database only, never `secret.key`, so a downloaded copy can't decrypt the keys. Every download is an event and an email ("Backup downloaded", info, email on).
- **Order.** The offsite upload starts when the local backup has finished, not at a fixed time, so it never copies a half-written file.
- **Backup credentials.** Write-only like the API keys and saved behind "Confirm it is you"; changing the endpoint needs the credentials again in the same request (hard rule 20).

**Monitoring (PR 24).**
- **Heartbeat.** After every cycle and position check the app pings `ops.heartbeat_url`, an outside dead-man's-switch service. If the pings stop, that service emails Marc. It is the only alert that still works when the server itself is down.
- **Uptime.** An outside uptime check watches `/healthz`.
- **Settings → Server** shows:
  - version, address and certificate expiry;
  - running since and how it started;
  - clock offset;
  - disk use (warning at 80%, critical at 90%);
  - OS updates and the next reboot window;
  - local backup, offsite backup and restore test;
  - heartbeat;
  - the instance id.
- **Notifications:** a backup or restore test failed; disk at 80% (warning) or 90% (critical); the certificate expires within 14 days; the clock is off by more than 1 second; restarted and resumed (info); restarted paused (warning).

**Readiness.** The manual item "Hosting with login (VPS)" is ticked once:
- the app has run on the VPS for 10 sessions;
- an offsite restore test has passed;
- the heartbeat has been checked by stopping the app once outside a session.

**Phone.** The layout already works at phone width. Installing it as an app on the home screen (PWA) is left for later.

## Error pages and API errors

Every failure the browser can see has a defined page or message, and none of them shows a stack trace, a key or trading data to someone who isn't signed in. Built as PR 25.

**API error format.** Every error response from `/api` is JSON in one shape: `{"error": {"code": "<stable code>", "message": "<one sentence for people>", "error_id": "e-…"}}`.
- **`error_id`.** Present on 500 responses and matches the log line (see Logging, error handling and shutdown).
- **Never in the body:** tracebacks, SQL, file paths or values of secrets.
- **Shape.** Validation errors (422) add `fields`: a list of the field name and its message.
- **Status codes and what the frontend does:**

| Status | Code examples | What the frontend does |
| --- | --- | --- |
| 400 | `bad_request` | Message in the card or dialog that sent it |
| 401 | `not_signed_in`, `session_expired` | Goes to sign-in and comes back to the same page afterwards |
| 403 | `confirm_required` | Opens "Confirm it is you", then repeats the request once |
| 403 | `forbidden` | Message; never retried |
| 400 | `bad_host` | Never seen in normal use; the message names the expected address |
| 403 | `origin_refused` | Never seen in normal use; message only |
| 413, 415 | `too_large`, `unsupported_media_type` | Message in the card or dialog that sent it |
| 404 | `not_found` | The record's not-found page (below) |
| 409 | `conflict`, `settings_changed` | Says what changed and offers to reload; never overwrites |
| 422 | `invalid` | Messages next to the fields |
| 429 | `rate_limited`, `locked_out` | Says when to try again (from `Retry-After`) |
| 500 | `internal` | Toast or card with the error id and "Open in Logs" |
| 503 | `shutting_down`, `starting_up` | The backend banner; retried every 15 s |

**In the app** (frontend, inside the normal sidebar and header):
- **Not found, a page.**
  - **When.** Unknown routes: the backend answers every non-`/api` path with the frontend, so the router decides.
  - **Content.** "There is no page here" and Go to Dashboard.
- **Not found, a record.** A decision, order, model call, run, strategy, replay or backtest id that doesn't exist. The page:
  - names it ("Decision 98213 doesn't exist");
  - explains the likely reason;
  - offers Go to Dashboard, plus a search fitting the record (for example "Search the LLM inspector").
- **Something went wrong.**
  - **Scope.** Each screen has its own error boundary, so one broken screen doesn't take down the sidebar or other screens.
  - **Content.** It shows the `error_id` from `POST /api/logs/client` (PR 20), "Reload page", "Copy error id" and "Open in Logs". It says the agent itself is not affected.
- **The app isn't answering.** Shown when the first load of a screen can't reach the API.
  - **What it says.** Open positions keep their stops at Alpaca. It retries every 15 seconds, with "Try now".
  - **After a successful load,** the existing backend banner is used instead (see Interaction and states).
- **Offline.** When the browser reports no network, a banner says so and requests pause until it's back.

**Served by Caddy** (static HTML in `deploy/errors/`, used from PR 24's Compose setup):
- **Rules for these pages.**
  - They are plain HTML with inline CSS and an inline SVG icon. They use system fonts, so they work when nothing else does.
  - They load nothing from the app and don't need JavaScript. A small script only adds the automatic reload.
  - They show no data, no version, no host details and no server commands, because anyone can see them without signing in.
  - Each says that open positions keep their stops at Alpaca.
- **502 or 504, app down.** "Stock Trading Agent isn't responding". It reloads every 30 seconds, and says the outside heartbeat emails Marc if it lasts.
- **503, updating.**
  - **When.** `deploy.sh` sets a maintenance flag file before it stops the app and removes it after the health check. While the flag exists, Caddy answers 503 with `Retry-After: 15`.
  - **Content.** "Updating", saying it takes about a minute and that the previous version comes back if the new one doesn't start.
  - **Scope.** The flag also covers `restore.sh`.
- **API requests during an outage.** These get the same JSON error shape from Caddy (`app_unavailable` or `updating`), so the frontend's banner logic doesn't need a special case.

**Not covered.** Caddy's own failure or the server being off can't be shown by the server. The outside uptime check and heartbeat cover those (see VPS deployment → Monitoring).

**GUI.** `design/screens/Errors.dc.html` shows every page above. The texts are final; copy them as written.

## Security hardening

From Devin's security audit of 27 October 2026. The audit found the code disciplined (encrypted write-only keys, paper-only clients, validated model output, locked dependencies), with one systemic hole: until sign-in exists, any web page Marc visits can send write requests to the app on 127.0.0.1. These rules close that hole for local use now, and stay in force behind sign-in on the VPS. Built as PR 28, before PR 23 is merged.

**Request gate.** One middleware in front of every `/api` route and the WebSocket, with no exceptions (hard rule 18). It runs before sign-in, so it protects the local app with `SA_AUTH=off` and adds a second layer behind sign-in.
- **Host.** The `Host` header must be `127.0.0.1`, `localhost` or `[::1]` with `SA_PORT`, or the host of `SA_PUBLIC_URL`, or a name in `security.allowed_hosts`; in `make dev` also the Vite dev server's address. Anything else gets 400 `bad_host`. This stops DNS rebinding, where a hostile site points its own name at 127.0.0.1.
- **Origin on writes.** POST, PUT, PATCH and DELETE are refused with 403 `origin_refused` when:
  - an `Origin` header is present and isn't the app's own origin (the `Host` origins above, plus the Vite dev origins in `make dev` only);
  - or `Sec-Fetch-Site` is present and isn't `same-origin` or `none`.

  A request without either header (curl, scripts) passes the gate. Browsers always send `Origin` on cross-site writes, so a web page can't.
- **Content type.** A write with a body must be `application/json` (415 `unsupported_media_type`). The CSV import is the only multipart endpoint. This blocks the `text/plain` form trick even if a header check were missing.
- **Size.** Request bodies are capped at `security.max_body_kb` (1 MB), uploads at `security.max_upload_kb` (5 MB): 413 `too_large`, checked while reading, not after.
- **WebSocket.** The same `Host` and `Origin` checks apply to the upgrade request.
- **Response headers from the app itself** (Caddy adds HSTS on the VPS):
  - `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'`, relaxed for the Vite dev server only in `make dev`;
  - `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Cross-Origin-Opener-Policy: same-origin`;
  - `Cache-Control: no-store` on `/api`.
- **Fonts.** IBM Plex is bundled with the frontend now (pulled forward from PR 24), so the CSP needs no outside hosts.

**Sign-in fails closed (PR 23).**
- `SA_AUTH` is read once, as an enum (`off` or `required`, case and spaces ignored), and every check uses that value.
- With `SA_AUTH=required` the app checks at startup that every route except `/healthz` and sign-in carries the session dependency, the same walk as the test, and refuses to start otherwise.
- The startup guard and the session layer ship in one PR; neither is merged alone.

**One source for the address.** The app is started through `python -m app.serve`, which reads `SA_HOST` and `SA_PORT` and starts Uvicorn with them. No Makefile target, script or Compose file passes `--host` or `--port`; a test checks the repo for it. The address actually bound is logged at startup.

**Secrets in errors.** The log scrubber (hard rule 9) applies to exceptions as well as messages:
- the formatted traceback and the exception message are scrubbed at the moment they are written, in every handler (file, console, log index);
- besides stored keys, it masks `token=`, `apikey=`, `api_key=`, `key=` and `secret=` values in URLs and `Authorization` headers;
- tested with a fake key inside an exception's arguments and inside a URL in an exception message.

**Paths from requests.** No file path is built from request input without a whitelist check (hard rule 19):
- prompt versions must match `^[a-z0-9_]+\.v[0-9]+$`, and the resolved path must lie inside `backend/prompts/`;
- `backend/prompts/manifest.json` holds the SHA-256 of every prompt file. `load_prompt` refuses a file whose hash doesn't match, and a test fails when a listed prompt changes (this enforces hard rule 8). A new prompt version adds its line;
- the same check applies to run, replay and backtest folder names.

**Bounded inputs.**
- Every list endpoint clamps `limit` to 1–200 and the log context `window` to 1–100; a value that isn't a number gets 422, never 500.
- Exports (logs, LLM calls, CSVs) are streamed and capped at `security.export_max_rows` (100,000) with a last line saying the cap was reached.
- The log search is passed to FTS5 as a quoted phrase; a search the index can't parse gets 422.
- `strategy:<id>` and other typed ids that don't parse get 422.
- A test sends `-1`, `0`, a huge number and `abc` to every query parameter of every GET route and expects no 500.

**Frontend error reports** (`POST /api/logs/client`):
- rate-limited per client address (`client_ip()`), not per tab id; the table of recent senders holds at most 1,000 entries;
- message capped at 2 KB and stack at 8 KB; control characters and line breaks are removed from single-line fields, and the log line is marked `source=frontend`.

**Stored credentials go only where they were saved** (hard rule 20).
- **SMTP.** When the host, port or username differs from the saved one, the request must include the password (422 `password_required`); the stored password is never sent to a new server. The port must be 465, 587 or 2525. A failed test returns a category (connection refused, timeout, TLS failed, sign-in refused) and never the server's own text. Connect timeout 10 seconds.
- **Heartbeat and backup storage.** `ops.heartbeat_url` and the backup endpoint must be `https://` and must not resolve to a loopback or private address.
- **Alpaca keys.** As already specced: keys for a different paper account require "Start new run". A test covers it.

**More actions need "Confirm it is you"** (PR 23, when `SA_AUTH=required`):
- a settings save that loosens a risk limit (a higher risk per trade, position size or exposure, a higher daily loss limit, or a kill-switch threshold raised or turned off);
- changing the SMTP server, the "To" address or the heartbeat URL.

Tightening a limit never asks.

**Files on disk.**
- The app sets `umask 077` at startup before creating anything.
- `SA_DATA_DIR` is `0700`; the database, backups, run folders, logs and the log index are `0600`. Wrong modes found at startup are corrected, with one INFO line.
- Run databases opened only for reading use `mode=ro`.
- `run.lock` is created with `O_CREAT | O_EXCL`, so two starts can't both win.

**Codebase and CI.**
- ruff's security rules (`S`, flake8-bandit) are on; every ignore has a comment.
- Dependency audit: `pip-audit` on `uv.lock` and `pnpm audit --prod` run in CI on every PR and weekly. High or critical fails the build unless listed in `security/audit-allowlist.yml` with a reason and an expiry date.
- Secret scan: gitleaks in CI on every push and as a pre-commit hook.
- `pnpm install --frozen-lockfile` in CI.
- GitHub Actions are pinned to commit SHAs, with `permissions: contents: read` unless a job needs more; Dependabot also updates Actions.
- `make security` runs the security tests and both audits.
- `docs/SECURITY.md`: the threat model in one page (single user, paper only, local then VPS), what each layer protects, and what isn't covered.

**Error codes.** New in the API error table (see Error pages and API errors): 400 `bad_host`, 403 `origin_refused`, 413 `too_large`, 415 `unsupported_media_type`, 422 `password_required`.

**Not changed.** Trading behaviour, risk limits and the database schema; PR 28 has no migration.

## Model providers: OpenAI and Anthropic

Decisions (live and shadows), the weekly review, the daily briefing, verdicts, replays and backtests can each use an OpenAI model or an Anthropic (Claude) model, chosen per task. Nothing else changes: the same prompts, the same input snapshot, the same output schema, and the same checks in code. The model only ever picks; code still validates every answer and every rule (hard rule 7). Built as PR 29.

**Why.** Marc can run the agent with an OpenAI key, an Anthropic key or both, and compare models on the same inputs.

**Choosing models.**
- **Live decisions:** `decision.model`, changed in Settings → Decisions like any setting (a new settings version with a reason) or through Promote strategy. The default is `gpt-4o-mini`. Settings recommends trying a new model in a shadow first, but doesn't require it.
- **Reviews:** one model each for the weekly review, the daily briefing and verdicts, in Settings → Reviews.
- **Shadows, backtests, replays:** per strategy or run, from the same list.
- **Pickers** show every model in the list as "id · provider". A model whose provider has no passing key is shown but can't be chosen ("needs the OpenAI key" or "needs the Anthropic key").

**Model list.** The `models` block in config is the only place that names models. Each entry has:
- `provider`: `openai` or `anthropic`;
- prices in USD per million tokens (`input`, `output`), the only price source;
- `cutoff`: the model's reliable knowledge cutoff, used by the backtest lookahead check;
- for Anthropic models, `effort` (`low`, `medium` or `high`; default `low`);
- optionally `timeout_seconds`, overriding `decision.call_timeout_seconds` for that model.

Settings, the shadow dialog, backtests and replays only offer models from this list. `model_prices` and `backtest.model_cutoffs` are folded into it, and `reviews.model` becomes `reviews.models` (one per task); the old keys are read for one release with a warning.

**One interface.** Engine code talks to one model client interface: send a prompt, a snapshot and a JSON schema; get back parsed JSON, token counts, latency, a request id or a typed error. Each provider has one adapter behind it, and a fake for tests. Provider SDKs are imported only in the adapters (hard rule 21). Decision code never branches on the provider.

**OpenAI adapter.** As today: the OpenAI Python SDK with strict structured outputs and `decision.temperature` (0) or `reviews.temperature` (0.2, for every review task on an OpenAI model).

**Anthropic adapter.**
- The official `anthropic` Python SDK and the Messages API. The prompt file is the system prompt; the snapshot is the user message, as for OpenAI.
- **Structured output.** JSON outputs through `output_config.format` with type `json_schema`. Anthropic doesn't accept some schema keywords (numeric `minimum`/`maximum`, string lengths, array lengths over 1, `additionalProperties` other than `false`). The adapter removes those from the copy it sends; Pydantic still enforces all of them on the answer, so the rules are identical for both providers. A test proves no unsupported keyword is ever sent.
- **Sampling.** No `temperature`, `top_p` or `top_k` is sent; current Claude models reject them. The model's `effort` from the list is sent instead. Settings shows the temperature as "OpenAI models only".
- **Thinking.** Claude models may think before answering; that is allowed, billed as output tokens and counted in the cost. Only the final answer is stored, never the thinking.
- **Stop reasons.** `refusal` becomes the `refusal` error and `max_tokens` becomes `invalid_output` with reason `truncated`; both count as skip or hold like any invalid answer. `max_tokens` per call is `decision.max_output_tokens` (2,048).
- **Errors and retries.** The same rules as OpenAI: one try, no retry. HTTP 529 (overloaded) and 5xx are `api_error` with the status and Anthropic's error type stored.

**Answers and determinism.** Without a temperature, a Claude model can answer the same input differently. So:
- the answer cache key uses provider, model, effort (or temperature), prompt hash and snapshot hash;
- replays and backtests with a Claude model say "not repeatable: the model has no temperature 0" next to the result.

**Keys.** Two model keys in Settings → API keys: OpenAI and Anthropic. Each is optional on its own; at least one is required.
- Both are write-only and encrypted like the others, tested with one list-models call before saving, and scrubbed from logs (patterns `sk-` and `sk-ant-`). Each can be imported once from `.env` (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`), as today.
- **Resume** needs at least one model key, plus a passing key for every provider used by live decisions and the three review tasks. The banner names what's missing, for example "Live decisions use gpt-4o-mini: add the OpenAI key or choose a Claude model".
- **Saving a setting** that picks a model whose provider has no passing key is refused with a message.
- **Removing a model key** (new "Remove" action, behind "Confirm it is you") is refused while live decisions or a review task use that provider; shadows on it pause and say why.
- **A shadow** whose provider key is missing or failing is skipped for that cycle with a warning; live is never held up.
- **A backtest** with a strategy whose provider has no working key is refused before it starts.
- **A failing key during the session** (for example revoked) behaves as today: calls fail as `api_error`, the "Data source or API key failing" notification fires, and decisions on that provider count as skip or hold.

**Costs.** Calls count toward the same caps as today (decisions, shadows, reviews, backtests), priced from the model list. Prompt caching is not used in this version.

**Backtests.** The lookahead check uses the model's `cutoff`. Current Claude 5.5 models have a reliable knowledge cutoff of June 2026, so their backtests can only start on 1 July 2026; the backtest screen says so, as it does for any model.

**What is recorded.** `llm_calls` gets `provider` and `provider_request_id` (new Alembic revision); the existing OpenAI request id column stays for older rows and is copied into the new column by the migration. Decisions, reviews and strategy versions already store the model; the provider is derived from the model list.

**GUI.**
- Settings → API keys: the OpenAI and Anthropic rows, each marked optional with the line "At least one of OpenAI and Anthropic is required", each saying what uses it, each with Test, Replace and Remove.
- Settings → Decisions: the model picker lists the model list as "id · provider", with models that need a missing key disabled; temperature marked "OpenAI models only".
- Settings → Reviews: three model pickers (weekly review, daily briefing, verdicts).
- The new-shadow dialog, the backtest form and `make replay --model` use the same model list.
- LLM inspector: the model filter lists every model; the call detail shows the provider, and an error block shows the provider's message and request id.
- Logs: component `model.anthropic` next to `model.openai`.

**Not changed.** Prompts (used prompts stay immutable and work for both providers), the output schema, the option builder, risk gates and Readiness criteria. The default live model stays `gpt-4o-mini`.

## Decision v3: checks by code, room and break-even

A review of a decision.v2 answer (SBUX, 28 October 2026, gpt-4.1-mini) showed five weak spots:
- **Buying into resistance.** Three levels sat 0.3–0.7 × ATR above the entry. Every option had to pass them first, yet the model called the nearest target "a clear path", because v2's rule compares options with each other, not with the room above the entry.
- **Trend not defined.** It called the daily trend "up" while the daily 50-day average was below the 200-day average. The prompt never defines the trend.
- **Confidence without a reference.** It gave 0.65 for a 2R target, where break-even is 0.33 and a random entry reaches 2R roughly a third of the time.
- **Decision before reasoning.** `action` and `confidence` come before `thesis` in the output, so the explanation is written after the choice and included a reason that wasn't in the input.
- **Messy input.** Prices with 14 decimals, the same price in four fields, an implausible bid shown as if usable, and fields the prompt doesn't explain (`rank`, `quote`, `stop_reference_price`).

Decision v3 fixes these with more work in code and a new prompt, `prompts/decision.v3.md`. The risk rules don't change: no threshold is lowered. Built as PR 30; it reaches live only through replay, a shadow and Marc's decision, as v2 did.

**Snapshot v3** (only for decision.v3 strategies; v1 and v2 snapshots stay exactly as they are, so their replays stay comparable):
- **Rounded:** prices and levels to cents, ATR to 4 decimals, distances, percentages and ratios to 2.
- **No duplicates:** `price` and `price_time` once; `signal_bar` with its time and close once. `stop_reference_price`, quote timestamps, the feed and fetch times stay in the stored provenance and are not sent.
- **Quote:** `bid` and `ask` only when plausible, otherwise null, plus `plausible` and `spread_unknown`.
- **`checks`**, worked out by code and given as facts:
  - `trend_1h`: `up` when the last 1h close > EMA 20 > EMA 50; `down` when close < EMA 20 < EMA 50; otherwise `mixed`;
  - `trend_daily`: `up` when the last daily close > SMA 50 > SMA 200; `down` when close < SMA 50 < SMA 200; otherwise `mixed`;
  - `regime_trend` and `regime_volatility` (as `regime` today);
  - `room_atr` and `room_level`: the distance from the entry limit to the first level in the way, in ATR(1h), counting only the kinds in `decision.v3.room_level_kinds`;
  - `earnings`: days to earnings, the date and the time of day;
  - `news_status`;
  - `momentum`: `strong` when `rs_vs_spy_pct` is above 0 and `obv_trend` is up; `weak` when `rs_vs_spy_pct` is below 0 and `obv_trend` is down; otherwise `mixed`.
- **Levels:** each also gets `distance_atr`, its signed distance from the entry limit in ATR (positive above).
- **Levels at the same price are merged.** Levels within `decision.v3.level_merge_atr` (0.05) × ATR of each other become one id. It keeps the highest-ranked kind and lists every kind in `kinds`, for example a 1h swing high and the session high at 246.79 (ACN, 28 October). A level that is several kinds at once is the stronger level, and the list carries less noise.
- **Options:** `levels_in_way` lists each level with its `distance_atr`. Each option also gets `breakeven_confidence` = 1 ÷ (1 + rr).
- **Constraints** gain `min_room_atr`, `min_confidence` (the strategy's `min_confidence_enter`) and `min_edge`.
- **`rank`** stays and is explained in the prompt: the symbol's place in the daily liquidity screen (1 = most liquid), not a reason to enter.

**Room filter** (option builder version 2, for v3 strategies only).
- **Rule.** An entry option is removed when the first level in its way, of the kinds in `decision.v3.room_level_kinds`, is less than `decision.v3.min_room_atr` (1.0) × ATR above the entry limit.
- **Which levels count.** Swing highs, swing lows above the price (old support that is now overhead), the session high, and the prior session high and low above the price. VWAP and the prior close are shown but don't count: they are soft levels that price crosses all day.
- **Nothing left.** When every option is removed, no model call is made. The decision is `no_call` with reason `no_options` and a room reason, for example "room: L7 1h swing high 0.31 ATR above the limit (min 1.0)".
- **Overrides.** Strategies can override `min_room_atr`, so a shadow can test 0.5 or 1.5.

**Prompt v3**, changes against v2:
- **Reason before deciding.** The output starts with `conflicts`, then `thesis`, then `action`, `option_id` and `confidence`.
- **Trend:** enter only when `trend_1h` is up and `regime_trend` is up. With `trend_daily` mixed, enter only when everything else agrees and confidence is well above break-even; with it down, never.
- **High volatility:** with `regime_volatility` high, every check must agree; prefer the nearer target.
- **Confidence starts at `breakeven_confidence`** and moves up only for concrete reasons in the input. 0.6 or more needs every check to agree and a clear path to the target.
- **News:** `news_used` lists only headlines that changed the judgement; it stays empty otherwise.
- **Data flags:** a new `data_flags` field (`earnings_date_conflict`, `price_conflict`, `stale_or_missing_data`). For example a headline announcing results within the blackout while `checks.earnings` says otherwise. With a data flag, skip a candidate.
- **No reasons outside the input:** never justify a stop or target with something the snapshot doesn't contain.
- **Field guide:** every snapshot field is explained, including `checks`, `quote.plausible`, `spread_unknown` and `rank`.
- **Momentum:** `checks.momentum` `weak` is a conflict: for a candidate it argues for skip; for a held position it supports `exit` only together with other evidence.
- **Stop and target basis:** the thesis doesn't describe where the stop or target sits; code adds that.
- **Name:** the prompt calls the app Stock Trading Agent.

**Output schema v3**, all fields required, in this order: `symbol`, `conflicts` (up to 5 short phrases), `data_flags` (enum list, may be empty), `thesis`, `action`, `option_id`, `confidence`, `levels_used`, `news_used`. `conflicts` takes the place of v2's `risks` in storage and in the drawer. The schema keeps the same strictness. Under PR 29 its range checks are stripped from the copy sent to Anthropic and enforced by Pydantic.

**Code checks under v3** (in addition to v2's):
- **Confidence gate:** enter needs confidence ≥ max(`decision.min_confidence_enter`, `breakeven_confidence` + `decision.v3.min_edge` (0.10)). Below it the decision is skip with reason `edge_too_small`.
- **Data flags:** a candidate answer with `enter` and any data flag becomes skip with reason `data_flag`. Every data flag, in any context, is logged at WARNING and recorded as an event ("Model flagged a data conflict: SBUX earnings_date_conflict"), so data bugs surface quickly.
- **Room at order time:** checked again from the final limit. If the fresh limit leaves less than `min_room_atr`, the entry fails the Fresh quote gate with reason `room`.
- **Explanation check:** as v2, against the rounded v3 snapshot.

**Held positions in v3.** A second review (SBUX held, 28 October 14:46) showed the held side needs the same treatment:
- the model said a break-even stop "locks in the current 0.49 R profit", where it locks in 0 R;
- one stop option moved the stop by 3 cents;
- break-even was offered at +0.49R, about 1 × ATR below the price, in a high-volatility regime;
- `original_invalidation` was `"unavailable"`;
- the model didn't know the code would sell the position the next day before earnings.

A third review (ACN held, 28 October 15:46) added: 0.65 confidence for a target 3.4 × ATR away against a stop 1.9 × ATR away, where no edge gives 0.36; a held decision that still called the stop's level L13, the id it had at entry, while level ids had shifted and that level was now L14; falling relative strength and OBV called "supporting continuation"; and two levels listed at the same price.

v2 stays as it is; for v3 strategies:
- **Stop options (option builder version 2):**
  - `locks_profit` is true only for a stop above the entry;
  - every stop option gets `locked_r` (R at that stop against the initial risk, replacing `r_at_stop`) and `distance_atr` (how far below the market price);
  - a stop option that moves the stop less than `decision.v3.min_stop_move_atr` (0.25) × ATR is dropped;
  - break-even (T2) is offered only once the position is at least `decision.v3.breakeven_min_r` (1.0) R in profit;
  - trail and swing-low options are offered only at least `decision.v3.held_stop_min_atr` (1.0) × ATR below the market price.
  - These thresholds are strategy-overridable, so a shadow can test them. Dropped options are listed with their reason in the drawer ("break-even from +1.0R; now +0.49R").
- **Position context:**
  - `position` gains `r_now`, `stop_distance_atr`, `target_distance_atr` and `levels_to_target` (the levels between the price and the target, each with `distance_atr`);
  - `invalidation` is always filled by code: the initial stop with its basis, naming the level by kind, time and price rather than by id, because ids are renumbered every cycle. For example "below 237.91: 0.1 ATR below the 20 Oct 12:30 1h swing low (238.22)" or "below 101.74: 2 × ATR stop";
  - `stop_level`: the id of that level in this cycle's `levels` when it is still listed (matched by kind, time and price), otherwise null;
  - `neutral_probability`: the chance of reaching the target before the stop with no edge at all, from the distances to the market price: stop distance ÷ (stop distance + target distance);
  - `entry_thesis` replaces `original_thesis`, and the prompt treats it as what was believed at entry, not as a fact;
  - level ids in `entry_thesis` are rewritten before it is sent, because ids are renumbered every cycle. An id becomes this cycle's id of the same level (matched by kind, timeframe, time and price), or "the 20 Oct 12:30 1h swing low (238.22)" when that level is no longer listed. The stored entry thesis keeps its original text.
- **Scheduled exit:** when code will sell the position anyway (today: the earnings exit when holding through earnings is off), `position.scheduled_exit` says why and when, for example "earnings exit Thu 29 Oct 15:46 NY". `hold` is then the default, and the coming earnings aren't listed as a conflict.
- **Confidence for held decisions** starts at `position.neutral_probability` and moves only for concrete reasons in the input, as entries start at break-even. It is defined per action:
  - `hold`: target before the current stop;
  - `tighten_stop`: target before the new stop;
  - `exit`: target before the current stop, as if held (a low number supports the exit).
  - Held confidence is stored but not gated, and it's kept out of the entry calibration on Readiness and Insights.
- **Claim checks** (warning `unsupported_claim`, like a failed explanation check; the decision stands):
  - a thesis that says the stop "locks in" profit while the chosen stop's `locked_r` is 0 or less;
  - a sentence that mentions the stop (or the target) together with a level id other than the stop's (or target's) own level. At the ACN entry of 28 October the stop's level was L13; by 15:46 a new level had shifted the ids and it was L14, yet the held decision still said "L13", copied from the entry thesis.
- **Code describes the stop and target.** The model's thesis explains *why*. The stop's and target's basis come from code and are shown next to the thesis in the drawer, the feed and the stored decision. The prompt tells the model not to describe where the stop or target sits.

**Replay.** `make replay PROMPT=decision.v3` builds v3 snapshots from the stored v2 inputs (levels, indicators, options, earnings and headlines), with no refetch. The report adds:
- options and snapshots removed by the room filter, and which v2 or live entries they would have blocked, with those entries' outcomes;
- skips by `edge_too_small` and `data_flag`;
- confidence against break-even, and calibration per 0.1 confidence bucket against first-touch outcomes where known;
- the most frequent conflicts, and every data flag with its snapshot;
- v2 against v3 on the same snapshots: enter rate, agreement, and R of the chosen options.
- held decisions: actions per context, stop options dropped per reason, and what the chosen stops did afterwards (stopped out within 5 sessions, and R given up against holding the old stop).
- held confidence against `neutral_probability`, and the claim-check failures by kind.

It can run with any model in the list (PR 29), so v3 can be compared on OpenAI and Claude models at once.

**Path to live. Nothing switches automatically.**
1. Replay v3 on every replayable candidate snapshot of the current run.
2. Run a shadow "D · decision.v3" for at least 10 sessions and at least 20 v3 entry proposals, next to C (v2), live and rules-only. Free a shadow slot first if all 3 are used.
3. Marc decides with the Strategies comparison, Insights and the replay report. Promotion to live only with "Start new run".

**Storage.**
- `options_version` is `2` for v3 options.
- The snapshot stores `checks` and the room reason.
- `no_options` reasons include `room`, and skip reasons include `edge_too_small` and `data_flag`.
- Dropped stop options are stored with their reasons next to the offered ones.
- No schema migration: these live in the existing JSON columns and reason lists.

**GUI.** The decision drawer for a v3 decision (`design/screens/DecisionV3.dc.html`):
- **Checks by code:** the checks block;
- **Options offered:** levels in the way with their ATR distance, and a Break-even column;
- **Agent reasoning:** conflicts before the reasoning, data flags after it;
- **Safety gates:** the room and confidence-versus-break-even gates;
- the room reason when there were no options.
- for a held decision (`design/screens/DecisionV3Held.dc.html`): the position with R now and its distances, the scheduled exit, the stop options with `locked_r` and distance, and the dropped options with their reason.

The dashboard's latest-decision card and the feed show `no_options · room` like other no_call reasons.

**Not changed.** The v1 and v2 prompts, snapshots and options; live's prompt; every risk gate and threshold; the rules pre-filter; rules-only.

## Entry selection (PR 33)

When more entries pass than there are free slots, today the first ones in rank order take the slots: rank order is the selection. Rank is 20-session strength against SPY; it says nothing about the trade offered or how many dollars each R is worth. `selection.mode: expected_profit` instead chooses, per cycle, the set of entries with the highest total expected profit within the existing limits. No limit, gate, size rule or order type changes: only which of the passing entries are sent, and in what order. `selection.mode: rank` keeps today's behaviour exactly.

**Settings.** `selection.mode` (`rank` or `expected_profit`; default `rank`), `selection.confidence_weight` (0.5), `selection.last_slot_min_ev_r` (null, off). All three are versioned settings and strategy overrides, so a shadow can test `expected_profit` next to live. Live switches only when Marc changes the setting.

**Steps in a cycle** (`expected_profit` only):

1. **Held positions first,** as today: their model calls run and their exits and stop moves are carried out before any entry is chosen, so a slot freed by an exit is available in the same cycle.
2. **Pre-score and call order.** For every candidate that passed the rules and has a usable market price, code builds the options (decision.v2 and v3) and sizes the first option with the shared sizing function. The pre-score is the dollars at risk of that size; it is what one R of this trade is worth after the max-position cap. Candidates are called in pre-score order, ties by rank; the call cap of 10 applies to that order. decision.v1 candidates have no options and keep rank order.
3. **Collect.** All candidate model calls of the cycle run first and nothing is submitted yet. Calls may run concurrently within the existing rate limiter and call cap. Each answer is validated as today.
4. **Score each enter** that passed validation and a dry run of the entry gates against the cycle's starting state:
   - break-even `be` = 1 ÷ (1 + R/R) of the chosen option;
   - probability used `p` = be + `confidence_weight` × (confidence − be), so the model's confidence counts for half until calibration exists;
   - expected R = p × (1 + R/R) − 1;
   - dollars at risk = shares × (entry limit − stop) from the shared sizing function;
   - expected profit = expected R × dollars at risk.
   An enter with expected R ≤ 0 is never selected (reason `not_selected`, "no expected profit").
5. **Choose the set.** Try every set of scored entries that fits: free slots (4 − open positions − working entry orders), the sector cap, total open risk, cash, and orders per cycle. Within a set, sizes are worked out in descending expected-profit order, each against the budget left after the earlier ones (the same rule as the gates). The set with the highest total expected profit wins; ties go to the set with the best ranks, then to fewer orders. With `last_slot_min_ev_r` set, an entry that would take the last free slot also needs at least that expected R. At most 10 candidates, so every set is tried; no heuristic.
6. **Submit** the chosen entries in descending expected-profit order, each through the full gates and the fresh-quote check, exactly as today. If one fails at that point, the best unchosen entry that still fits is tried, once through the list.
7. **Not chosen:** the decision's final action is `blocked` with reason `not_selected` and is shown as changed by code. For verdicts (PR 32) it is a model enter blocked by code, judged as an enter.

**Supervised mode.** The chosen set becomes the approvals, in expected-profit order; the others are `not_selected`. Gates run again on approval, as today.

**Storage.** Each scored candidate decision stores `selection`: mode, pre-score, call position, be, p, expected R, dollars at risk, expected profit, rank, chosen or not, and the cycle's winning total. The cycle stores free slots, the number of scored entries, the chosen ids, and the total the rank order would have chosen under the same rules (for comparison).

**GUI.** The decision drawer gets a "Selection" gate row and a "Selection in this cycle" block: every scored entry of the cycle with R/R, p used, dollars at risk, expected R and expected profit, chosen or not, and the total that rank order would have chosen (see `design/screens/DecisionV2.dc.html`). The same block appears in the v3 drawers. The feed shows `not_selected` like other code blocks.

**Comparison before switching live.** `make replay-selection` replays every stored cycle where more entries passed than there were slots (gates `max_open_positions`, `sector_cap` or `total_open_risk` blocked a model enter), live and shadows, under both modes:

- each chosen entry's result comes from its option's stored touch (PR 32): target +R/R, stop −1R, not filled 0, neither the 5-session move in R; dollars = R × dollars at risk;
- the report gives per mode: entries, total R, total dollars, average expected R, and every cycle where the two modes chose differently, with what each would have earned;
- it is a per-cycle comparison: a different choice would have changed later cycles' slots, so the report says so and doesn't add those effects.

Then a shadow with `selection.mode: expected_profit` runs next to live. Marc switches live when the shadow and the replay agree.

**Later, not in PR 33:** fitting `confidence_weight` from the confidence calibration once Readiness has 50 filled entries; replacing a held position with a better candidate (no: more trades and costs, on a confidence that isn't calibrated).

## Decision v4: calibrated confidence, memory and the entry thesis

The analysis of 31 October (`docs/analysis/2026-10-31-decision-v4-candidates.md`: 1,055 called decisions, v1 to v3, 19–30 October) found that v3 fixed the false "locks in profit" claims and the loose daily-trend claims. These problems remain:

- **Confidence is not a probability.**
  - Enters sit at 0.6–0.7 whatever the setup.
  - On a skip the number means nothing: gpt-4.1-mini answers 0.0, gpt-4o-mini 0.4. The skips at 0.0 had the most options that reached their target first (34%).
  - Held confidence sits about 0.3 above `neutral_probability`, up to 0.85 on a −0.61R position where the neutral probability was 0.15.
  - Entry selection (PR 33) turns confidence into money, so this matters more now.
- **Flips without a reason.**
  - 11 skip↔enter flips between consecutive cycles, and about 28 reversals in a session after moves of 0.3–0.5%.
  - The model doesn't see its own previous answer.
- **Conflicts listed, then ignored.**
  - Entries with their own listed conflicts and no word on why they don't count.
  - Held decisions under v3 list none, even with adverse checks.
- **"Clear path" into nearby levels:** it still occurs under v3.
- **No exits.** 233 held calls, 0 exits. The model re-asserts the entry thesis instead of checking it, because it can't see what changed since entry.
- **News.** About 35–40% of headlines aren't about the symbol. Citations carry no effect, and the same old headline is cited cycle after cycle.
- **Input noise.** Levels far out of reach and long headline lists make up most of the tokens.

Decision v4 keeps everything v3 does (options, checks, room filter, break-even machinery, claim checks, conflicts first) and adds the following. Built as PR 34; like v2 and v3 it reaches live only through the eval set, a shadow and Marc's decision.

**Unchanged on purpose.**
- The trend rules of v3 stay. The analysis found trend and momentum checks going against target-first in one rally week, which is too little evidence to drop them or to enforce them harder.
- Instead, a strategy override `checks_gate` lets a shadow test the enforced version (see Code checks).
- The room filter stays at 1.0 ATR.

**Snapshot v4** (decision.v4 strategies only; v1–v3 snapshots unchanged):
- **`previous_decision`**: the model's last decision on this symbol in the current session, or null.
  - It holds the cycle time, action, option id, `best_option_id`, confidence, conflicts and price.
  - It also holds `since`, the facts code computed: the price move in ATR, which checks changed (name: old → new), headline ids that are new since then, and levels that are new within 1 ATR.
- **Headlines:**
  - each headline gains `about_symbol`, true when its title names the company or its ticker (the name from the pool or config);
  - at most `decision.v4.max_headlines` (12), the most recent first;
  - each gains `new`, true when it wasn't in the previous decision's input.
- **Levels:** only levels within `decision.v4.max_level_distance_atr` (4) of the entry limit are sent, plus any level used by an option or `position.stop_level`. The stored provenance keeps all of them.
- **Options:** each gains `stop_group` (S1, S2, …). Options with the same stop share a group, so the choice within a group is only the target distance.
- **Position** (held) gains:
  - `checks_at_entry`: `trend_1h`, `trend_daily`, `regime_trend` and `momentum` at the entry decision. Taken from its stored v3 or v4 checks, or computed with the v3 rules from its stored input for older positions.
  - `thesis_holds`: false when 2 or more of those four turned adverse. Adverse means up → mixed or down, mixed → down, and for momentum strong → mixed or weak, mixed → weak. It is a fact for the model; it never forces an exit.
  - `held_session_hours` (session time since the entry fill, regular hours only) and `sessions_held`.
- **`session`**: `minutes_left` until the close (early closes included).

**Prompt v4** (`prompts/decision.v4.md`), changes against v3:
- **Confidence for every action:**
  - `enter`: the chosen option, as v3;
  - `skip`: the best offered option's chance, with `best_option_id`, the option you'd take if you had to;
  - held: start at `neutral_probability`. More than 0.15 above it needs a named fact from the input; more than 0.30 above it is almost never right.
- **Resolve every conflict.** Each conflict has an `outweighed_by`: the facts that outweigh it, or null. A candidate with a conflict that nothing outweighs is a skip; a held position with one is a reason to consider exit. Held decisions list conflicts too: every adverse check and `thesis_holds` false.
- **No path claims near a level:**
  - never call the path clear, open, easy or manageable while a level in the way is within 1 ATR;
  - name the level and its distance instead.
- **What changed:**
  - when `previous_decision` exists and your action differs from it, `what_changed` names the input facts that changed (from `previous_decision.since`);
  - when nothing material changed, keep the previous action;
  - otherwise `what_changed` is null.
- **News:**
  - weigh only headlines with `about_symbol` true;
  - a headline that isn't `new` already counted last time and is not a reason to change;
  - every cited headline gets its effect.
- **Exit:** with `thesis_holds` false, say in the thesis why holding is still better, or exit. `session.minutes_left` and the time held matter when the target is far.
- **Tighten** only when the new stop protects progress made since the last stop move; not every cycle.

**Output schema v4**, all fields required, in this order:
- `symbol`;
- `conflicts`: up to 5 of `{text, outweighed_by}`, where `outweighed_by` is a short phrase or null;
- `data_flags`;
- `thesis`;
- `what_changed`: a short phrase or null;
- `action`;
- `option_id`;
- `best_option_id`: an entry option id on a candidate, null when held;
- `confidence`;
- `levels_used`;
- `news_used`: up to 5 of `{id, effect}`, where `effect` is at most 12 words.

For storage, the drawer and verdict facts, `conflicts[].text` takes the place of v3's conflicts. The same strictness as v3; under PR 29 the range checks are stripped from the copy sent to Anthropic and enforced by Pydantic.

**Code checks under v4** (in addition to v3's):
- **Flip-flop guard.** A candidate `enter` becomes `skip` with reason `flip_flop` (shown as changed by code) when all of these hold:
  - the previous decision on the symbol in this session was a model `skip`;
  - the price moved less than `decision.v4.flip_min_move_atr` (0.5) ATR since then;
  - no check changed;
  - no headline is both `new` and `about_symbol`;
  - no new level appeared within 1 ATR.

  What the model writes in `what_changed` doesn't override these facts. An enter → skip flip is never blocked; skipping is always allowed.
- **Confidence sanity** (warning `confidence_outlier`, never a gate):
  - confidence more than `decision.v4.max_confidence_above_anchor` (0.30) above the anchor;
  - the anchor is the chosen option's `breakeven_confidence` (enter), the best option's (skip), or `neutral_probability` (held);
  - stored, counted on Readiness and Insights, and shown in the drawer.
- **Path claim** (warning `unsupported_claim`, kind `path`): a sentence with "clear", "open", "easy" or "manageable" together with "path", "way" or "room", while the chosen option (or held: `levels_to_target`) has a level within 1 ATR.
- **Unresolved conflict** (warning `unresolved_conflict`): an enter with a conflict whose `outweighed_by` is null. The decision stands; it is counted.
- **Best option:** on a skip, `best_option_id` must be an offered option, otherwise the answer is invalid as today. Held answers must have it null.
- **Claim check fix:** only a sentence that attributes a level to the stop or the target is checked against the stop's or target's level. A conflict such as "levels L2 and L1 in the way" is not a claim (bug B1 of 31 October).
- **`checks_gate`** (strategy override, default off): when on, a candidate with `trend_1h` not up, `regime_trend` not up, or `trend_daily` down gets no model call, reason `no_call` · `checks`. Used by a shadow to test the code-enforced checklist against the model's judgement.

**Storage.**
- Each decision stores `confidence_anchor` (the break-even or neutral probability it was judged against), `best_option_id`, `what_changed`, the warnings, and `previous_decision.since`.
- The skip-side confidence and the best option's touch are kept in their own calibration series. They never count toward the entry calibration on Readiness and Insights until Marc turns that on.
- Shadows store their full input snapshot (bug B2 of 31 October).
- No schema migration: everything lives in the existing JSON columns, except where PR 34 finds a column missing; that needs a new Alembic revision.

**Eval set.**
- `backend/eval/decision_v4.json` lists the stored calls of the 31 October analysis (about 65), each with its category and the properties a good answer must have.
- `make eval-decision PROMPTS=decision.v3,decision.v4 MODEL=…` rebuilds both inputs from the stored data (no refetch), calls the model, runs the production validation plus the properties, and reports:
  - the pass rate per category and prompt;
  - every call v3 passed and v4 failed;
  - confidence against the stored option touches;
  - tokens, cost and latency.
- Cost counts toward the replay cap.

**Path to live. Nothing switches automatically.**
1. The eval set: v4 must pass at least as many properties as v3 in every category, with no new invalid answers.
2. `make replay PROMPT=decision.v4` on the current run's snapshots.
3. A shadow "E · decision.v4" for at least 10 sessions and 20 entry proposals, next to live. Optional: a second shadow with `checks_gate` on, for the checklist question.
4. Marc decides with Strategies, Insights, the replay and the eval report.

**GUI.**
- The decision drawer for a v4 candidate (`design/screens/DecisionV4.dc.html`):
  - Previous decision with what moved since;
  - checks compared with the previous cycle;
  - stop groups in the options;
  - each conflict with what outweighs it;
  - "What changed (model)" and, when it fired, the flip-flop guard's finding;
  - news marked about the symbol or not, with the effect of each cited headline;
  - the v4 gates.
- For a held position (`design/screens/DecisionV4Held.dc.html`):
  - "Does the entry thesis still hold?" (checks at entry against now, `thesis_holds`);
  - time held and session time left;
  - confidence against the neutral probability.
- A skip shows "skip · best A · 0.38".
- The feed shows `flip_flop` and `checks` like other code blocks.

**Not changed.** The v1–v3 prompts, snapshots and options; live's prompt and model; every risk gate and threshold; the rules pre-filter; entry selection (it uses v4's entry confidence like any other). The live model stays gpt-4.1-mini. A claude-haiku-5-5 shadow is added once its key is set.

## Out of scope and later versions

Not in v0.1: live trading, shorting, options, margin, extended-hours trading, fractional shares, EU exchanges, backtesting, VPS deployment and login (VPS deployment is specified for PR 23 and 24).

Likely v0.2+, in rough order:

- Ask the agent (as in Trading Agent). Briefings and reviews moved forward to PR 9 (see Reviews).
- Shadow strategies: specified for PR 13 (see Strategies and shadows).
- Backtesting: specified for PR 14 (see Backtesting).
- Larger universe with a liquidity screen: specified for PR 11 and 12 (see Larger universe).
- VPS deployment with HTTPS and login: specified for PR 23 and 24 (see VPS deployment). Later: install as a phone app (PWA) with push notifications.
- Live-readiness checklist (including Alpaca's real-time SIP data, so entries use the consolidated market quote instead of the IEX last trade), and a separate spec for live trading (including Dutch tax treatment, which needs checking with an advisor before going live).

## Sources

- [Alpaca Market Data API: plans and feeds](https://docs.alpaca.markets/docs/about-market-data-api)
- [FINRA Regulatory Notice 26-10: intraday margin standards replace the day trading requirements](https://www.finra.org/rules-guidance/notices/26-10)
- [Alpaca: FINRA retires the PDT rule](https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/)
- [Alpaca paper trading: what it does not simulate](https://blog.traderspost.io/article/alpaca-paper-trading)
