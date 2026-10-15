# Using the spec

`SPEC.md` is the source of truth for what Stock Agent does. These rules say how to use it.

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
