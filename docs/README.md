# Using the spec

`SPEC.md` is the source of truth for what Stock Trading Agent does. These rules say how to use it.

## Rules

1. **The spec wins on behaviour, the design on look.** `design/DESIGN.md` explains the design files. Differences go under "Design differences" in the PR description.
2. **Ask, don't guess.** Anything unclear that changes behaviour (a threshold, an order type, what happens on an error) is a question in the PR, not a decision in code.
3. **Thresholds live in config.** Every number in the spec has a key in `backend/config.example.yaml`. Code reads config; it never repeats the number.
4. **One build step per PR.** Use the map below for which spec sections a step needs.
5. **Prompts are versioned.** `backend/prompts/decision.v1.md` is never edited after use; a change is a new file.
6. **Changes are logged.** Behaviour changes get a dated entry in `docs/CHANGES.md`, newest first.

## Which sections each PR needs

| PR | Step | Spec sections | Design screens |
| --- | --- | --- | --- |
| 1 | Skeleton | Architecture and stack; Data model; Testing and engineering rules | Header and sidebar from `Main` (shared components page) |
| 2 | Market and data | Market, schedule and data; Alpaca integration (paper only, rate limits) | none |
| 3 | Signals, regime and rules | Decision layer (indicators, regime, rules pre-filter); Risk management (risk math) | Watchlist rules column in `Main` |
| 4 | Decision layer | Decision layer (model output, prompt, outcome tracking) | `Inspector`, `Decision` |
| 5 | Execution | Alpaca integration; Risk management | Kill switch dialogs in `States` |
| 6 | GUI | GUI | `Main`, `Decision`, `Positions`, `Settings`, `States` |
| 7 | Performance and outcomes | Decision layer (outcome tracking); Alpaca integration (paper realism) | `Performance` |
| 8 | Hardening | Risk management (errors); Data model (backups) | `States` (error states) |
| 9 | Reviews | Reviews; Data model | `Reviews`, verdict in `Decision` |
| 10 | Settings and API keys | GUI (Settings screen, Risk budget card, Cycle counter, Daily briefing card, Model call counter); API keys; Notifications; Data model | `Settings`, risk budget card, cycle counter, briefing card and model call counter in `Main` |
| 11 | Pool and liquidity screen (v0.2) | Larger universe (pool, daily screen, rank, GUI); Data model | `Universe`, universe rows in `Settings` |
| 12 | Cycles at scale (v0.2) | Larger universe (cycles at scale); Decision layer (one cycle) | Watchlist filters in `Main` |
| 13 | Strategies and shadows (v0.2) | Strategies and shadows; Decision layer; Data model | `Strategies` |
| 14 | Backtesting (v0.2) | Backtesting; Decision layer; Risk management; Data model | `Backtest` |
| 15 | Integrity fixes | Alpaca integration (Entries); Decision layer (code checks, prompt, outcome tracking); Risk management (gates); Reviews (number check); Market, schedule and data | none |
| 16 | Readiness | Readiness; Notifications; Data model | `Readiness`, Readiness section in `Settings` |
| 17 | Dashboard and interaction polish | GUI (Dashboard at scale and off-hours; Interaction and states) | `Main`, `States` (card states, closed market, toasts), `design/DESIGN.md` → Interaction rules, `tokens.css` |
| 18 | LLM call history | LLM call history; Data model (`llm_calls`); GUI table | `Inspector` (+ `Inspector-390`, `Inspector-error` screenshots) |
| 19 | Tail metrics | Tail metrics; Data model (`positions`); GUI table | `Performance` (Winners and tails card) |
| 20 | Logging, error handling and shutdown | Logging, error handling and shutdown; GUI table; AGENTS.md rules 15–16 | `Logs`, `States` (shutdown and restart) |
| 21 | Decision v2: code-built options | Decision v2; Decision layer (no_call reasons); Data model; `backend/prompts/decision.v2.md` | `DecisionV2` |
| 22 | Insights | Insights; Strategies (entry_cycles); Data model | `Insights` |
| 23 | Sign-in and access | VPS deployment (Sign-in, Restarts, instance id); Security hardening (Sign-in fails closed, more confirmed actions); Data model; AGENTS.md rule 10 | `Login`, `Settings` (Access), `States` (Access and server, Confirm it is you), sidebar Sign out |
| 24 | VPS deployment | VPS deployment (How it runs, hardening, deploying, backups, monitoring) | `Settings` (Server), `States` (server banners) |
| 25 | Error pages and API errors | Error pages and API errors; GUI table | `Errors` |
| 26 | Day-trade rule retired | Alpaca integration → Day trades (rule retired); Risk; GUI; Notifications | `Positions` (Today card), `Main` (risk card), `Decision` (gate), `Settings` (notifications), `States`, `Insights` |
| 27 | Protective stops hardened | Alpaca integration → Protective stops; Position checks; Larger universe → Daily screen | none |
| 28 | Security hardening | Security hardening; Error pages and API errors (new codes); VPS deployment (Sign-in → Fails closed); AGENTS.md rules 9, 18–20 | `Settings` (SMTP note, Access → risky actions) |

## PR description template

```
## Step
PR <n>: <step name>

## Built
- ...

## Tested
- ...

## Not tested
- ...

## Deviations from the spec
- none / ...

## Design differences
- none / ...

## Questions
- ...
```
