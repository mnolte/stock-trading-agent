# Spec changes

Newest first.

## 2026-10-15 — PR 2: Market and data

- Data-source interfaces with fakes for 5m bars, IEX quotes, Alpaca news, Finnhub earnings, ECB EUR/USD and Alpaca corporate actions.
- `AlpacaDataClient`: trading client always `paper=True`; startup check rejects non-paper accounts (account numbers must start with `PA`); shared rate limiter caps Alpaca calls at `alpaca.max_calls_per_minute` (150) with read-only exponential backoff.
- `CalendarStore` loads the Alpaca trading calendar at startup and again daily at 08:00 New York via APScheduler; `SessionClock` now handles holidays, weekends, early closes and the DST-difference week.
- `BarCache` caches 5-minute SIP bars in SQLite (`bars_5m`); `aggregate_1h` builds the session-aligned 1-hour bars including the final partial bar.
- `MarketDataService` fills 60 sessions of history per symbol at startup and refreshes earnings and corporate actions into the database.
- `config.example.yaml`: new `alpaca.max_calls_per_minute`.
- New dependencies: `alpaca-py`, `finnhub-python`, `pandas`, `apscheduler`.

## 2026-10-15 — PR 1: Skeleton

- Repo layout per SPEC.md → Architecture and stack.
- FastAPI app with `/api/health`, `/api/session`, `/api/header`; config loading from `.env` and `backend/config.example.yaml`.
- SQLite with SQLAlchemy 2.x and Alembic; initial migration with all tables from SPEC.md → Data model.
- Injected clock object (`RealClock` / `FakeClock`) and session-state calculator.
- React + TypeScript + Vite shell: sidebar, header with PAPER badge and fake-clock session state, empty screens, and shared components on `/dev/components`.
- Makefile (`install`, `run`, `lint`, `test`), GitHub Actions CI, and pytest with sockets blocked.

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
