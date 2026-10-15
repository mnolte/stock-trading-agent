# Spec changes

Newest first.

## 2026-10-15 — PR 3: Signals, regime and rules (built)

- Indicators on session-aligned 1h bars and completed-session daily bars (`app/signals/`): EMA 20/50 (SMA-seeded), RSI 14 and ATR 14 (Wilder), session VWAP anchored at the open, OBV trend over 20 bars, relative strength vs SPY, daily SMA 50/200, daily ATR percentile over the configured session window, opening gap, trading days to next earnings.
- Market regime from SPY daily bars: trend up/down/sideways, volatility low/normal/high by ATR percentile; Down + High blocks new entries. `indicators.*` periods added to `config.example.yaml`.
- Rules pre-filter (`app/rules/`): held / open order, daily close vs SMA 50, EMA 20 > EMA 50, RSI range, earnings blackout (unavailable data blocks), regime block, last-cycle rule — every threshold from config.
- Shared risk math (`app/risk/math.py`): one `trade_risk` function for stop distance, risk and reward to risk; whole-share sizing under the risk-per-trade, max-position, open-risk and cash limits.
- `/api/watchlist` serves per-symbol rules status, price, day change, next earnings and last action; `/api/header` now reports the real regime label. Dashboard shows the watchlist table with the Rules column per the Main design.
- Bar cache reads per symbol are single range queries (`get_5m_bars_sessions` + `bars_per_session`) instead of one query per session.
- Playwright e2e (`pnpm test:e2e`, `frontend/e2e/`) opens the Dashboard against a simulated backend and saves `docs/screenshots/pr-3/main-1440.png` and `main-390.png` (1440×900 and 390×844). App shell now wraps the sidebar above the content under ~792 px, matching `Main-390.png`.

## 2026-10-15 — PR 2: Market and data (built)

- Implemented "Market, schedule and data" and the paper-only Alpaca reads: 5m SIP bar download with SQLite cache, session-aligned 1h aggregation, IEX latest quote, news (one row per article × symbol, Alpaca article id), Finnhub earnings, ECB EUR/USD, corporate actions, trading calendar and `/v2/clock`. Fakes behind every interface.
- Applied the "Answers to PR 2 questions" decisions: SIP requests capped at `now - 16 min`, early-close state until the last cycle, `earnings.hour_raw` plus normalised `time_of_day` (new migration), calendar-unavailable = closed with a 5-minute retry job, `eur.available`/`rate` in the API instead of a stale or zero rate.
- Config moved to `app.timezone` and `schedule.market_timezone`; `alpaca.max_calls_per_minute` kept.
- `make smoke-data`: read-only check that fills the bar cache and does one read per source. No orders.
- PR 1 fixes needed to get a fresh database running: Alembic owns the schema (`create_all` removed), `AppConfig` finds `backend/config.example.yaml`, tests use a temp database.

## 2026-10-15 — Answers to PR 2 questions

- Market, schedule and data: Early close shows for the whole early-close session until its last cycle has run, then Last cycle done.
- SIP bar requests always end at least 16 minutes in the past (free-plan limit).
- Trading calendar unavailable: market treated as closed, retry every 5 minutes. EUR/USD unavailable: GUI shows USD only, marked "EUR unavailable".
- Data model `earnings`: time of day normalised to before_open, after_close, during_market or unknown (from Finnhub bmo, amc, dmh), raw value kept. Risk: an unknown time of day counts as before the open for the earnings exit.

## 2026-10-15 — 15-minute position check

- Market, schedule and data: new "Position check" between the hourly cycles, at 09:46, 10:01, 10:16, 10:31, then :01, :16 and :31 past each hour until 15:31 New York time. No model calls and no trades; it reconciles with Alpaca, makes sure every position has a stop, updates the since-open shock brake, daily loss limit and drawdown kill, and cancels stale entries.
- Decisions: the Cycle row mentions the check. Data model: `cycles.kind` (cycle or position check). Build plan: part of PR 5.
- `config.example.yaml`: `schedule.position_checks`, `schedule.position_check_failures_alert`.

## 2026-10-15 — Application timezone

- Overview: new "Timezone" paragraph. The application timezone is Europe/Amsterdam (GUI, logs, alerts, daily jobs); timestamps stay stored in UTC and decision cycles stay on the New York exchange clock.
- `config.example.yaml`: `app.timezone: Europe/Amsterdam`; `schedule.timezone` renamed to `schedule.market_timezone`.

## 2026-10-15 — Trading currency

- Overview: new "Currency" paragraph. All trading, risk limits and stored money values are in US dollars; euro amounts are display only and never used in decisions or risk checks.

## 2026-10-15 — First handoff

SPEC.md v0.1 as exported on 15 October 2026. It includes:

- Alpaca paper account only; free data plan (IEX real-time quotes, 15-minute delayed SIP bars).
- 10-symbol universe, long only, whole shares, 1-hour session-aligned bars, six cycles per session at :46.
- Bracket orders with broker-side stops on every position; reconciliation against Alpaca each cycle; PDT guard.
- gpt-4o-mini decision layer with strict JSON output, code checks, and outcome tracking for every decision.
- 14 risk gates, kill switch and Stop & flatten.
- 8-step build plan.

Details settled in this package that SPEC.md does not spell out:

- Environment variables: `MODE`, `SA_HOST`, `SA_PORT`, `SA_DATA_DIR`, `SA_DB_PATH`, plus the four API keys (`.env.example`).
- Every config key and default in `backend/config.example.yaml`.
- Command names: `make install`, `make run`, `make lint`, `make test`; frontend `pnpm lint` and `pnpm typecheck`.
- The full system prompt in `backend/prompts/decision.v1.md`.
