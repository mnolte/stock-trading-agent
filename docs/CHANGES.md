# Spec changes

Newest first.

## 2026-10-15 — Answers from the PR 4 review

- Decision layer: held positions are called first when the 10-call cap applies.
- Model output uses OpenAI structured outputs (strict JSON schema), still validated with Pydantic.
- `no_call` is an official final action, with a reason: `rules`, `call_cap`, `quote_missing` or `model_error`. Model calls time out after 30 s and are not retried; for a held position `no_call` leaves it as it is.
- Schedule: missed cycles are never run late (misfire grace 2 minutes); only simulated mode may run a cycle at startup.
- `config.example.yaml`: `decision.call_timeout_seconds: 30`, `schedule.misfire_grace_seconds: 120`.

## 2026-10-15 — Answers from the PR 3 review

- Market, schedule and data: daily indicators use Alpaca daily bars, 260 sessions per symbol, refreshed daily at 08:00 New York; computed on completed sessions only. All bar requests are split-adjusted. (60 sessions of 5-minute bars could not give SMA 200 or the 60-session ATR percentile.)
- Data model: new `bars_1d` table.
- Rules pre-filter: RSI 40–70 is inclusive.
- One cycle, step 3: the signal snapshot is stored; the GUI reads it and never recomputes per request.
- `config.example.yaml`: `schedule.daily_history_sessions: 260`, `schedule.bar_adjustment: split`.

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
