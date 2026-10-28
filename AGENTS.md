# AGENTS.md

Instructions for AI coding agents (Devin, Claude Code and others) working in this repo.

## Where answers live

- **`docs/SPEC.md` decides behaviour.** What the app does, every rule, threshold and gate.
- **`design/` decides look and wording.** Start at `design/DESIGN.md`. Copy text as written.
- If they disagree, the spec wins on behaviour and the design wins on look; list the difference in the PR description.
- `backend/config.example.yaml` holds every default from the spec. Thresholds live in config, never in code.

## How to work

- One build step per PR, in the order of `docs/SPEC.md` → Build plan. Don't start the next step in the same PR.
- **Ask instead of guessing.** If the spec is unclear, incomplete or contradicts itself on anything that changes behaviour, stop and ask in the PR. Don't invent a rule.
- Every rule and gate gets a test for the pass case and the fail case.
- Every behaviour change updates `docs/SPEC.md` (only when asked to) and adds an entry to `docs/CHANGES.md`.
- End every PR with: what was built, what was tested, what was not tested, deviations from the spec, design differences.

## Commands

Run before every commit:

```bash
make lint        # ruff + mypy (backend), pnpm lint + pnpm typecheck (frontend)
make test        # pytest with sockets blocked, vitest, playwright e2e when browsers are installed
```

`make dev` starts the backend (uvicorn --reload) and the Vite dev server; Ctrl-C stops both with a graceful shutdown. `make logs-reindex` rebuilds the log index from the log files. `make create-user` creates or resets the single account (server only). `deploy/` holds provision, deploy and restore scripts (see `docs/DEPLOY.md`); never run them from a PR. `make run` builds the GUI and serves everything from the backend on 127.0.0.1:SA_PORT. `MODE=simulated` with either command runs without network.

## Hard rules

1. **Paper only.** Create the Alpaca trading client with `paper=True`. The live base URL must not appear anywhere in the repo; a test checks this. Refuse to start if the account is not a paper account.
2. **Every position has a broker-side stop.** Entries are bracket orders. A position found without a stop gets a protective stop immediately, then an alert.
3. **Never resend an order automatically.** After a timeout or error on an order call, reconciliation decides what happened.
4. **Exact money.** Use `Decimal` for prices, quantities and money in the backend. Whole shares only.
5. **Time is injected.** No `datetime.now()` in engine code; use the clock object. Schedule in `America/New_York` with `zoneinfo`; never hard-code a UTC or Amsterdam offset.
6. **No network in tests.** Every external service sits behind an interface with a fake. Sockets are blocked in the test setup.
7. **The model never overrides code.** Rules and risk gates run in code; invalid model output becomes `skip` (or `hold` for a position).
8. **Prompts are immutable once used.** Never edit a prompt file (`decision.v1.md`, `decision.v2.md`, …) after it has made a decision; create the next version. Never move a level or pick another option in code to make a model's choice pass.
9. **Secrets.** Keys are entered in Settings and stored encrypted (`secrets` table, `data/secret.key`). The API is write-only for keys. Never print, log, commit or send them to the frontend; tests use fake keys. The log scrubber covers exception messages and tracebacks in every handler, not only log messages.
10. **Never open without sign-in.** Locally the backend binds to `127.0.0.1` with `SA_AUTH=off`. On any other address `SA_AUTH=required` is mandatory and the app refuses to start otherwise. Every route except `/healthz` and sign-in needs a session; a test walks the route table. Never publish the app port directly; only Caddy is public. One instance per Alpaca account: `SA_INSTANCE_ID` must differ per copy.
11. **Migrations are immutable once merged.** A schema change is always a new Alembic revision; never edit or reorder a merged one.
12. **Processes.** Never kill processes by name or broad pattern. Stop only a process you started, by its PID.
13. **No write lock across a network call.** Commit before any model, broker or data call; never keep a SQLite write transaction open while awaiting I/O. Live jobs and shadows share one event loop and one database file.
14. **No restarts during the session.** Don't restart, redeploy or migrate the running app between 09:30 and 16:00 New York time unless Marc asks for it or it's an emergency; say so in the PR if a change needs a restart.
15. **No silent failures.** No bare `except:`, no `except Exception: pass`, no catch that neither logs nor re-raises; use `logger.exception` (or log at WARNING for an expected condition with its reason). Start background tasks only through `spawn()`. Every component logs its steps at DEBUG and milestones at INFO with the cycle or request id in context. Never log keys, prompts or model responses.
16. **Clean shutdown.** Ctrl+C and SIGTERM must finish or interrupt work cleanly: never cut an order submission between sending and storing the answer, keep each unit of work in one transaction, and close the database and the log last.
17. **No day-trade logic.** The pattern day trader rule is retired (FINRA, 4 June 2026). Never read `pattern_day_trader`, `daytrade_count` or `daytrading_buying_power`; size against `cash`, never `buying_power`.
18. **Every request passes the request gate.** Host, Origin and `Sec-Fetch-Site` on writes, JSON-only write bodies and the body size cap apply to every `/api` route and the WebSocket; no route opts out, and sign-in comes on top of it, not instead. Start the server only through `python -m app.serve`; never pass `--host` or `--port` anywhere.
19. **Request input never becomes a path or an unbounded query.** Whitelist names with a regex and check the resolved path lies inside its base folder; clamp every `limit` and window; cap every export; bad input gets 422, never 500. Strip control characters from client text before logging it.
20. **Stored credentials go only where they were saved.** When the destination of a stored password or token changes (SMTP host, port or user; backup endpoint), require the credential again in the same request. Outbound URLs a user can set must be `https://` and must not point at a loopback or private address.
21. **One model client.** Engine code calls models only through the model client interface; OpenAI and Anthropic SDKs are imported only in their adapters under `app/llm/providers/`. Never branch on the provider in decision, risk or review logic, and never relax a check for one provider. Every model comes from the `models` list in config.

## Conventions

- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, pydantic v2, `alpaca-py`, OpenAI SDK. Type hints everywhere; ruff and mypy clean.
- Frontend: React + TypeScript + Vite, pnpm. Colours, type and spacing only from `design/tokens.css`. One chart library: Lightweight Charts.
- Times stored in UTC; shown in Amsterdam time with New York time on hover.
- Money stored in USD; the EUR figure is display only, from the ECB daily rate.

## Don't

- Add a second chart library, a UI kit or TA-Lib.
- Add shorting, options, margin, fractional shares or extended-hours trading.
- Use Alpaca's clock-hour bars for signals; build session-aligned 1h bars from 5-minute bars.
- Edit `docs/SPEC.md` or the design files on your own initiative.
- Write anything into `design/`. PR screenshots go in `docs/screenshots/pr-<n>/`.
- Mark a step done while tests fail or are skipped.
