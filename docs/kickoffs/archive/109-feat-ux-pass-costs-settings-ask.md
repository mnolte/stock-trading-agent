# 109 · feat · UX pass: model costs, model settings, Ask drawer, spacing

Kickoff for Devin. Start the session with: "Build docs/kickoffs/109-feat-ux-pass-costs-settings-ask.md."

Backend for model costs and the new settings, and the frontend for Insights, Settings, the Ask drawer and app-wide spacing. No trading behaviour changes: no rule, gate, order or model input changes. Spec: `docs/SPEC.md` → Insights → GUI and **Model costs** (new), LLM call history → Storage, Model providers (Model list, Costs, Backtests), GUI → Settings screen → **Every model setting has a control** (new), Ask the agent → Under each answer and GUI → Layout. Design: `design/DESIGN.md` → **Spacing (109)** (new); `design/screens/Insights.dc.html`, `Settings.dc.html` (`#model`), `AskDrawer.dc.html`, `AskStates.dc.html`, and the small spacing fixes in `Reviews`, `Readiness`, `Backtest`. Item files: `docs/changes/109-feat-ux-pass-costs-settings-ask.md`, `design/changes/109-feat-ux-pass-costs-settings-ask.md`. Issue: #321.

```text
[109] UX pass: model costs, model settings, Ask drawer, spacing. Goal:
Insights shows what every model call cost and how often each model is
called, cached prompts included; Settings has a control for every
model setting in config; the Ask drawer matches its 109 design; and no
paragraph sits cramped against its neighbour, checked in CI.
Start from the latest main. Two PRs:
  A (backend): "[109] feat: Model cost data and settings", "Part of #321".
  B (frontend): "[109] feat: Model costs, model settings, Ask, spacing",
     "Closes #321". B starts after A is merged.

Read first:
1. AGENTS.md (Commands: CI runs the tests) and docs/README.md.
2. docs/SPEC.md → the sections above; design/DESIGN.md → Spacing (109)
   and Rules; design/changes/109-…md and docs/changes/109-…md; issue #321.
3. Code: app/llm/base.py (_usage_cost, token_cost, ModelResponse
   cached_tokens_in / cache_write_tokens_in), app/inspector/record.py
   (build_llm_call), app/db/models.py (LlmCall, Backtest),
   app/api/insights.py, app/api/inspector.py (detail, export.csv),
   app/backtests/worker.py (summary), app/settings_service.py
   (EDITABLE, SUBSPECS, LABELS, DIRECTIONS), backend/config.example.yaml
   (decision, reviews, ask, models, shadows, backtest);
   frontend/src/pages/Insights.tsx, Settings.tsx, settingsValidation.ts,
   frontend/src/ask/AskDrawer.tsx, AskMarkdown.tsx,
   frontend/src/styles/global.css and tokens.css.

Move this kickoff to docs/kickoffs/archive/ with git mv in PR A. Set
status: building in docs/changes/109-…md in PR A and merged in PR B,
each with a dated "build PR #n" section.

PR A — backend
1. Migration (new Alembic revision): llm_calls gets tokens_cached_in
   (int, null), tokens_cache_write_in (int, null) and cache_saved
   (Money, null). Never rewrite old rows.
2. build_llm_call stores response.cached_tokens_in and
   cache_write_tokens_in, and cache_saved from one new function in
   app/llm/base.py next to _usage_cost:
     cache_saving(table, model, cached_in, cache_write_in) -> Decimal | None
     = (cached × (input − cached_input) − write × (cache_write − input))
       ÷ 1,000,000, with cached_input and cache_write falling back to
       input; None without a price. Decimal only.
   Every writer of llm_calls (decisions, shadows, reviews, verdicts,
   Ask) goes through build_llm_call, so all get it.
3. Inspector: GET /api/llm-calls/{id} returns the three fields;
   export.csv gets columns tokens_cached_in, tokens_cache_write_in,
   cache_saved (empty when null).
4. Backtests: when a run finishes, stops at its cap or is cancelled,
   the worker writes summary["model_usage"]: a list of {model, calls,
   tokens_in, tokens_cached_in, tokens_cache_write_in, tokens_out, cost,
   cache_saved, answers_reused}, summed from the run's own call records
   (answers_reused = answers served by the answer cache). Old runs
   without it count with calls and tokens unknown and their cost_usd.
5. GET /api/insights/model-costs?period=this_month|last_month|3_months|all
   returns {period: {from, to, sessions}, purposes: [...], models:
   [...], per_session: [...], answer: "…"} exactly as Insights → Model
   costs describes:
   - purposes in the spec's order (live, shadow, weekly, briefing,
     verdict, ask, backtest) and a total; per purpose: models, note
     facts (shadow strategy names, Ask questions and cost per question,
     backtest run ids and answers reused), calls, per_session (null for
     backtests; total without backtests), tokens_in, tokens_cached_in,
     cached_share, tokens_cache_write_in, tokens_out, cost, cache_saved,
     cap {used, limit, pct} from shadows.monthly_cost_cap_usd,
     reviews.monthly_cost_cap_usd (on weekly only, covering all three),
     ask.monthly_cost_cap_usd and backtest.monthly_cost_cap_usd, or for
     live {max_calls_per_cycle};
   - models: per model used, with its prices from the models block
     (input, cached_input, cache_write, output), the purposes that used
     it, calls, tokens, cost, per_call;
   - per_session: per New York session (per ISO week above 31
     sessions), cost of live, shadows, reviews (all three), ask;
   - answer: the fixed template from the spec, no model;
   - rows with null cache columns count as uncached; costs are the
     stored costs, never recomputed.
   Months are New York calendar months. Use the existing (source,
   sent_at) index; must answer within 300 ms on the 100,000-call
   fixture (add the case to the existing perf test).
6. Settings (app/settings_service.py): make these editable, with
   LABELS, DIRECTIONS and ranges:
   - decision.call_timeout_seconds (int 5–120), decision.max_output_tokens
     (int 256–16384): raising is looser;
   - reviews.temperature (number 0–2, neutral);
     reviews.max_words as a submap: weekly_summary (int 40–400),
     weekly_story (int 100–1000), briefing (int 60–600), neutral;
   - ask: max_tool_rounds (1–20), max_context_tokens (8000–200000),
     max_tool_result_tokens (500–20000), max_output_tokens (256–16384),
     answer_timeout_seconds (15–300), max_questions_per_minute (1–60),
     max_notes (10–1000), all int, raising is looser;
   - new sections shadows {monthly_cost_cap_usd: number 0–100} and
     backtest {monthly_cost_cap_usd: number 0–500}, raising is looser.
   Defaults stay those in config.example.yaml (no new keys). Each value
   is read where it is used at the next call, cycle or run, never cached
   at start; check the readers of each key and say in the PR which
   needed a change. Saved values without a control (item 101) must not
   list any of these keys any more.
7. GET /api/settings/models (or a field on the settings read): the
   models block as a list {id, provider, input, cached_input,
   cache_write, output, cutoff, effort, supports_temperature, api,
   timeout_seconds, key_ok, used_by}. Read-only; no write route.
8. Tests (no network, each its own database, fake clock):
   - cache_saving: OpenAI model with cached reads; Anthropic with reads
     and writes; writes larger than reads → negative; no price → None;
     no cached_input → 0 saving;
   - build_llm_call stores the three columns from a fake response;
     an older-shaped response (None) stores nulls;
   - model-costs: a fixture with every purpose, two models, a call from
     before the migration (nulls), a backtest with and without
     model_usage, calls on both sides of a month boundary in New York
     time → the period split, the totals, per_session, the cap pcts and
     the answer sentence; the 300 ms perf case;
   - settings: each new key saves inside its range and is refused
     outside it (422), its direction tags tighter and looser correctly,
     and a reader picks up a saved value at its next use;
   - backtest summary: model_usage sums the run's calls.

PR B — frontend
9. Insights → section 6 "What do the models cost?" as in
   Insights.dc.html: the jump chip "6 Model costs", the period toggle
   (in the URL as ?costs=), By purpose with the cap bars (amber from
   80%), By model with prices under the name, Cost per session (stacked
   bars; hover lists the four amounts), the note texts, the links (a
   purpose → LLM inspector with source and period; Backtests → the
   Backtest screen; the Model cost per trade tile → #q6). Below 760 px
   both tables become two-column rows with labels, as in the design.
   Each section's heading to its sentence 8 px; finding cards as in the
   design.
10. Settings → Model and reviews as in Settings.dc.html#model: the new
   rows (Call timeout, Max answer length, Review temperature, Word
   limits folded, Limits per answer folded, Shadows and backtests with
   this month's use, the read-only Models table); every picker lists
   every model of the models list (disabled with "needs the … key" as
   today); prompt selects list every version of their task from the
   manifest; defaults shown are the config's. The cap rows link to
   Insights #model-costs. Validation in settingsValidation.ts uses the
   same ranges as the backend.
11. Ask drawer as in AskDrawer.dc.html and AskStates.dc.html: the
   one-line header with three 36 px icon buttons; the one-line context
   chip with ellipsis and title; the composer box (grows 2–6 lines,
   budget left from the Ask cap, Send or Stop) and one hint line; the
   answer footer (meta line, then Copy, Save as note, Model calls
   buttons; a number not found leads the meta line); 12 px between an
   answer's blocks and 16 px between messages; notes with Edit and
   Delete buttons; "Jump to latest" while streaming when scrolled up.
   Keep Esc, Ctrl+J / ⌘J, focus return and the read-only rule as they
   are.
12. Spacing app-wide (design/DESIGN.md → Spacing (109)):
   - add .sa-prose to global.css (children 0 margin, 12 px apart,
     line-height 1.6) and use it for Ask answers (AskMarkdown), the
     weekly review and briefing stories, and explained warnings;
   - paragraphs of a sentence or more get line-height 1.5;
   - fix every place the new check (13) reports, on every screen, not
     only the ones in this kickoff; keep the 073 rules (16 px between
     groups, 8 px inside one) and 107's card alignment.
13. The spacing check: frontend/e2e/109-spacing.e2e.ts opens every
   screen (Main, Positions, Performance, Reviews, Insights, Strategies,
   Backtest, LLM inspector, Readiness, Universe, Logs, every Settings
   section, the Ask drawer with an answer, a decision drawer) with the
   sample data at 1440 × 900 and 390 × 844 and fails when a text block
   of 7 words or more is closer than 8 px to a neighbouring text block
   or to a bordered or filled box in the same column (vertical gap
   between siblings in a block or column-flex parent). Table rows, rows
   with their own padding and inline chips are not checked. An
   exception needs an entry in the test with its reason. The rule and
   the method are the ones in design/DESIGN.md → Spacing.
14. Screenshots in docs/screenshots/109/ at 1440 and 390 (and 1024 for
   Settings): Insights section 6 (This month and All time), Settings
   #model with both folds open, the Ask drawer mid-answer, Ask first
   open, Ask notes, Ask at the cap, Reviews, Readiness, Backtest; each
   next to its design/screenshots reference in PR B.

Regressions to check in PR B (say in the PR that each still holds):
073 (groups and labels in cards), 074 (Does it need me? chips), 075
(Insights questions, answer sentences, column order), 076–079 (plain
words, glossary, stories), 100 (unavailable controls say why, empty
states, statuses), 101 (last-cycle switch; Saved values without a
control), 107 (card edges and gaps at 1440, 1024 and 390; jump targets
on the card).

Rules: Decimal for money; no datetime.now() in engine code (the clock);
thresholds and ranges from one place each (backend ranges in
settings_service, the frontend reads them or mirrors them in
settingsValidation.ts with a test that they match); costs are never
recomputed; nothing written into design/. Tests are proven in CI: open
each PR as a draft early, run locally only the test files you change,
never the full suites, e2e or image builds.

Done when: all CI checks are green and each PR merges cleanly; PR B
lists the seven points of #321 under "Issues closed" with the file or
test for each, and says how many places the spacing check fixed.
```

## Why

Marc, 20 November: "On the insights page I am missing a section where all the model costs (including cached prompts) are represented … how often they are being called", "verify all the fields are present" in the model settings, "sometimes, especially around text paragraphs, the margin is missing", and "have a special look at the Ask section". The findings are in `docs/changes/109-feat-ux-pass-costs-settings-ask.md`.

## Assumptions

- The model list stays config-only; Settings shows it read-only (decided by Marc on 20 Nov: prices are not editable in Settings).
- Model costs are read on request from `llm_calls` and `backtests`, not from the nightly Insights snapshot.
- Raising a cost cap or a model limit counts as looser, like raising a risk limit: it can only cost more.

## Open questions

1. If a reader of one of the new settings caches its value at start, should a change need a restart (shown as "applies after a restart", as Server settings do) instead of a rewrite? The kickoff asks for reading at use; Devin reports which readers needed a change.
