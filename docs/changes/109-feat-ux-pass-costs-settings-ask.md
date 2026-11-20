---
item: 109
type: feat
title: UX pass: model costs, model settings, Ask drawer, spacing
status: building
issue: 321
kickoff: 109-feat-ux-pass-costs-settings-ask.md
spec_prs: [322]
build_prs: [327, 328]
---

# 109 · feat · UX pass: model costs, model settings, Ask drawer, spacing

## 2026-11-20 · spec PR

Marc asked on 20 Nov for a UX and UI pass over every page, with four points: a section on Insights with every model cost (cached prompts included) and how often each model is called; every model setting present in Settings; paragraphs that no longer look cramped; and a better Ask drawer. No regressions against 073–079, 100, 101 and 107.

What the pass found:
- **Costs are scattered.** Each cap shows its own month in Settings, the inspector sums cost per source, Backtest shows its runs. Nothing shows all of it, per model, or how often each model is called. `llm_calls` stores the cost of a cached call but not the cached and cache-write tokens, so the saving can't be shown.
- **Settings misses 15 model settings that exist in config:** `decision.call_timeout_seconds`, `decision.max_output_tokens`, `reviews.temperature`, the three `reviews.max_words`, seven `ask` limits, `shadows.monthly_cost_cap_usd`, `backtest.monthly_cost_cap_usd`; the `models` block (prices, cached and cache-write prices, cutoff, effort, API) is shown nowhere. The pickers miss `gpt-4o-mini`; the weekly review's default showed `claude-sonnet-5-5` (config: `gpt-4o-mini`); the prompt selects missed `weekly.v3`, `briefing.v3` and `decision.v6`, and the prompt hashes were made up.
- **Spacing.** A check over every screen at 1440 px found text 0–6 px from its neighbour in about 20 places that are not table rows: section headings and their answer sentence (4 px), Settings labels and help (3 px), finding cards (6 px to the buttons), the suggestion's note, Readiness' verdict, two bullet lists. There was no written rule.
- **Ask drawer.** In the states board the title wrapped to two lines and the subtitle cut off; the context chip wrapped inside its pill; the composer put the hint in two cramped columns; answer actions were three loose links on two lines; Edit and Delete on notes were links that wrapped alone; the "number not found" line started a line with "·". The board's frames were 258 px, narrower than any real drawer.

Spec changes (`docs/SPEC.md`):
- **Insights → GUI:** question 6 and a new **Model costs** part: scope, period toggle, by purpose (with calls per session and the cap bars), cache saved, by model with prices, cost per session, the answer sentence, older calls, links, the API with its 300 ms budget.
- **LLM call history → Storage:** `tokens_cached_in`, `tokens_cache_write_in`, `cache_saved`; shown in call details and the CSV.
- **Model providers:** the `api` field of a model; the Models table in Settings; Costs points to Insights; backtests store `summary.model_usage`.
- **GUI → Settings screen:** "Every model setting has a control": the five groups with every new key, its default and range, tighter and looser, and the cap links.
- **Ask the agent → GUI and Under each answer:** the 109 layout (one-line header with icon buttons, one-line chip, one composer box with the budget, 12 px between answer blocks, footer buttons, note buttons, Jump to latest).

No trading behaviour changes. No new config keys: every key the screens show is already in `backend/config.example.yaml`.

Assumptions:
- The model list stays config-only and read-only in Settings (prices change rarely; a GUI editor would need its own validation and history). Decided by Marc on 20 Nov.
- Model costs are computed on request, not in the nightly Insights snapshot, because they cover the whole app and must match the caps live.

Answered by Marc on 20 Nov:
1. Model prices editable in Settings later? No: they stay in config, read-only in Settings.
2. A monthly price check? Yes. It is a scheduled task, not app code: on the 2nd of each month it compares the `models` block with the providers' price pages and opens an issue only when a price or cutoff differs.

## 2026-11-21 · build PR #327 (PR A — backend)

- `llm_calls` gained `tokens_cached_in`, `tokens_cache_write_in` and `cache_saved` (migration `e9f0a1b2c3d4`); all four call writers record the cache split and the USD saved, computed at write time from the stored prices. Pre-109 rows keep NULL and count as uncached.
- Inspector JSON and `export.csv` carry the three fields; backtest summaries store `summary.model_usage` (per-model calls, tokens, cost, `cache_saved`, `answers_reused`) for every terminal status.
- New `GET /api/insights/model-costs?period=` computes the Model costs payload on request: purposes in spec order, by-model rows with prices, cost per session without backtests, monthly-cap usage and the answer sentence.
- Settings: every model-related key is editable — `decision.call_timeout_seconds`, `decision.max_output_tokens`, `reviews.temperature`, `reviews.max_words.*`, the seven `ask` limits, `shadows.monthly_cost_cap_usd`, `backtest.monthly_cost_cap_usd` — with directions, labels, confirm-on-raise for the model sections and matching controls (`settings-controls.json` plus the Settings rows the e2e control check requires). New read-only `GET /api/settings/models` returns the model catalog.
- Kickoff moved to `docs/kickoffs/archive/`. Frontend (Insights card, Settings models table and new controls, Ask drawer, spacing) follows in PR B.

## 2026-11-21 · build PR #328 (PR B — frontend)

- **Insights question 6, "What do the models cost?"** (`frontend/src/pages/modelCosts.tsx`): the section fetches `/api/insights/model-costs` on request, keeps its period in `?costs=` (this month / last month / all time), and shows the answer sentence, the by-purpose table (calls, calls per session, input/cached/cache-write/output tokens, cost, cache saved, the monthly-cap bar), the by-model table with prices and cache savings, cost per session, period metadata, links into the inspector, and skeleton/error states.
- **Settings** (`frontend/src/pages/Settings.tsx`): the read-only Models catalog (`GET /api/settings/models`) lists every configured model with provider, purposes, prices, cutoff, sampling, API and key status; review word limits fold into a `details.sa-adv` block; the review and shadow/backtest cap helps link to `/insights#q6`; the Ask section shows the monthly spend line (`spent_usd`/`cap_usd` from the settings payload) and folds the per-answer limits.
- **Ask drawer** (`frontend/src/ask/`): one-line header with icon buttons, one-line context chip, a single composer box carrying the budget/cost line, `.sa-prose` on rendered answers (12 px between blocks), a compact footer action row, note edit buttons, and a "Jump to latest" control. Keyboard, focus, streaming, read-only and cap behaviour unchanged.
- **Spacing**: shared `.sa-prose` rhythm applied to Ask answers, review stories, briefings and explained warnings (`StoryText`, `Inspector`); `.mc-table`/`.ml-table` responsive rules in `global.css`. New `frontend/e2e/109-spacing.e2e.ts` walks every screen at both viewports and fails on cramped non-table text or mobile overflow.
- Screenshots spec `frontend/e2e/109-shots.e2e.ts` writes `docs/screenshots/109/` (Insights costs both periods, Settings model page with folds at 1440/1024/390, Reviews, Readiness, Backtests, Ask thread/notes/mid-answer/cap).
