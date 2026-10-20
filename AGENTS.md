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

`make dev` starts the backend (uvicorn --reload) and the Vite dev server; Ctrl-C stops both. `make run` builds the GUI and serves everything from the backend on 127.0.0.1:SA_PORT. `MODE=simulated` with either command runs without network.

## Hard rules

1. **Paper only.** Create the Alpaca trading client with `paper=True`. The live base URL must not appear anywhere in the repo; a test checks this. Refuse to start if the account is not a paper account.
2. **Every position has a broker-side stop.** Entries are bracket orders. A position found without a stop gets a protective stop immediately, then an alert.
3. **Never resend an order automatically.** After a timeout or error on an order call, reconciliation decides what happened.
4. **Exact money.** Use `Decimal` for prices, quantities and money in the backend. Whole shares only.
5. **Time is injected.** No `datetime.now()` in engine code; use the clock object. Schedule in `America/New_York` with `zoneinfo`; never hard-code a UTC or Amsterdam offset.
6. **No network in tests.** Every external service sits behind an interface with a fake. Sockets are blocked in the test setup.
7. **The model never overrides code.** Rules and risk gates run in code; invalid model output becomes `skip` (or `hold` for a position).
8. **Prompts are immutable once used.** Never edit `backend/prompts/decision.v1.md` after it has made a decision; create `decision.v2.md`.
9. **Secrets.** Keys are entered in Settings and stored encrypted (`secrets` table, `data/secret.key`). The API is write-only for keys. Never print, log, commit or send them to the frontend; tests use fake keys.
10. **Local only.** The backend binds to `127.0.0.1` in v0.1.
11. **Migrations are immutable once merged.** A schema change is always a new Alembic revision; never edit or reorder a merged one.
12. **Processes.** Never kill processes by name or broad pattern. Stop only a process you started, by its PID.
13. **No write lock across a network call.** Commit before any model, broker or data call; never keep a SQLite write transaction open while awaiting I/O. Live jobs and shadows share one event loop and one database file.

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
