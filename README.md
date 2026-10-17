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
make run                         # builds the GUI, serves everything on 127.0.0.1:SA_PORT
```

The agent starts **paused**: it reconciles with Alpaca and watches open positions, but places no new entries. Open the GUI, check the header says PAPER, then press Resume. The first cycle runs at 10:46 New York time (16:46 in Amsterdam most of the year).

For development, `make dev` runs the backend with auto-reload on 127.0.0.1:SA_PORT and the Vite dev server on 127.0.0.1:5173; Ctrl-C stops both.

Without keys, run `MODE=simulated make run` (or `make dev`): recorded bars and a fake broker, no network.

Reset the Alpaca paper account to $10,000 in the Alpaca dashboard before the first run.

## Configuration

| Where | What |
| --- | --- |
| `.env` | Secrets and paths: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `OPENAI_API_KEY`, `FINNHUB_API_KEY`, `MODE` (`paper` or `simulated`), `SA_HOST`, `SA_PORT`, `SA_DATA_DIR`, `SA_DB_PATH` |
| `backend/config.yaml` | Everything else: universe, cycle times, rules, risk limits, model, slippage assumption. Settings changed in the GUI are saved as new versions. |

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
