# Stock Agent

A paper-trading agent for liquid US stocks and ETFs. It trades through an Alpaca **paper** account, uses an OpenAI model to decide, and runs every trade through fixed rules and risk gates in code.

> **Paper only.** Stock Agent v0.1 cannot place real-money orders. It is a personal experiment, not financial advice.

## What it does

- Watches 10 symbols: SPY, QQQ, AAPL, MSFT, NVDA, AMZN, GOOGL, META, JPM, XOM.
- Runs six decision cycles per US session (10:46 to 15:46 New York time) on 1-hour bars.
- A rules pre-filter picks candidates; the model chooses enter or skip (or hold, tighten stop, exit for open positions); risk gates size and block trades.
- Every entry is a bracket order at Alpaca with a broker-side stop, so positions stay protected when the app is down.
- Records every decision, including skips, and what the price did afterwards.
- Compares its results with simply holding SPY.

## Requirements

- Python 3.12, Node 20+, pnpm
- An Alpaca account with paper trading API keys (a paper-only account works from the Netherlands)
- An OpenAI API key
- A Finnhub API key (free plan) for the earnings calendar

## Quick start

```bash
cp .env.example .env            # fill in the keys
cp backend/config.example.yaml backend/config.yaml
make install
make run                         # backend on 127.0.0.1:8000, GUI on 127.0.0.1:5173
```

Open the GUI and check the header says PAPER. The agent runs its first cycle at 10:46 New York time (16:46 in Amsterdam most of the year).

Without keys, run `MODE=simulated make run`: recorded bars and a fake broker, no network.

Reset the Alpaca paper account to $10,000 in the Alpaca dashboard before the first run.

## Configuration

| Where | What |
| --- | --- |
| `.env` | Secrets and paths: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `OPENAI_API_KEY`, `FINNHUB_API_KEY`, `MODE` (`paper` or `simulated`), `SA_HOST`, `SA_PORT`, `SA_DATA_DIR`, `SA_DB_PATH` |
| `backend/config.yaml` | Everything else: universe, cycle times, rules, risk limits, model, slippage assumption. Settings changed in the GUI are saved as new versions. |

## Running locally

The backend writes logs to `data/logs/stock-agent.log` (or `$SA_DATA_DIR/logs/` when `SA_DATA_DIR` is set), rotated daily with 30 days retained. Timestamps are in Amsterdam time; rotation itself follows the machine's local clock — run a VPS with `TZ=Europe/Amsterdam` so the rollover boundary matches the stamped times.

A dated copy of the database is written to `data/backups/` every day at 03:00 Amsterdam time (`storage.backup_time`), via SQLite's online backup API with an `integrity_check` on the copy; the last 14 days are kept (`storage.backup_days`).

On shutdown the backend waits up to `storage.shutdown_grace_seconds` (60) for a running cycle or check to finish; leftovers are marked `interrupted` and do not count as failures. Rows still `running` at the next startup mean a crash and do count.

To run in the background for long sessions:

```bash
make run &> /dev/null &
```

## Project layout

| Path | Contents |
| --- | --- |
| `backend/` | FastAPI app, agent engine, Alpaca and data adapters, SQLite storage, tests |
| `backend/prompts/` | Versioned model prompts (`decision.v1.md`) |
| `frontend/` | React + TypeScript GUI |
| `design/` | Design reference: `DESIGN.md`, `tokens.css`, screen files and screenshots |
| `docs/` | `SPEC.md` (what the app does), `CHANGES.md`, build notes |

## Safety

- Paper endpoint only; the live URL does not exist in the code.
- A stop order at Alpaca on every position, checked every cycle.
- Daily loss limit, drawdown kill switch, PDT guard, earnings blackout.
- Kill switch and "Stop & flatten" in the header.
- Keys stay in `.env`; they are never logged or sent to the GUI.

## Build plan

1. Skeleton
2. Market and data
3. Signals, regime and rules
4. Decision layer
5. Execution (first paper orders)
6. GUI
7. Performance and outcomes
8. Hardening

Details per step are in `docs/SPEC.md` → Build plan.
