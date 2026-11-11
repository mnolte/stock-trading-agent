# 086 · feat · Prompt caching on complete() model calls

Kickoff for Devin. Start the session with: "Build docs/kickoffs/086-feat-prompt-caching-complete.md."

Issue #224. `converse()` (Ask the agent) already uses provider prompt caching — Anthropic `cache_control` on the system prompt and tools, OpenAI's automatic caching read from `input_tokens_details` — and `converse_cost` prices cached tokens from `models.<id>.cached_input` / `cache_write`.

`complete()` (decisions, shadows, reviews, replays, evals) does not:

- `ModelResponse` has no `cached_tokens_in` / `cache_write_tokens_in` fields.
- `token_cost` prices every input token at full price.
- `AnthropicAdapter.complete` sends `system` as a plain string — no `cache_control`, so Anthropic calls are never cached.
- OpenAI auto-caches shared prefixes ≥1024 tokens (decision.v3+ qualify), so the bill is already discounted, but stored `cost` values and monthly-cap consumption are overstated.
- `models` in `config.example.yaml` has no `cached_input` for gpt-4o-mini / gpt-4.1-mini.

The spec opted out until now ("Prompt caching is not used in this version"); this item changes `docs/SPEC.md` (Model providers → Model list and the new Prompt caching paragraph) and adds the `cached_input` prices to `backend/config.example.yaml`, so the PR carries the `spec change` label.

## Expected benefit

Decision prompts are large (v5 ≈ 4k tokens) and identical across every call in a cycle (up to `decision.max_calls_per_cycle: 10`, calls seconds apart). For Anthropic: first call per cycle pays the 1.25× write premium, the rest read at 0.1× — roughly ~80% off input cost at Sonnet prices, plus faster TTFT. For OpenAI: accounting accuracy only. Shadows get the same benefit per strategy prompt.

## Scope

- `ModelResponse`: add `cached_tokens_in` / `cache_write_tokens_in`; `token_cost` mirrors `converse_cost`.
- `AnthropicAdapter.complete`: `system` as a block list with `cache_control`; read `cache_read_input_tokens` / `cache_creation_input_tokens`.
- `OpenAIAdapter.complete`: read `prompt_tokens_details.cached_tokens`.
- `config.example.yaml`: `cached_input` prices for the OpenAI models.
- SPEC.md update (Model providers section) + `docs/CHANGES.md` entry.

Cached-token counts on `llm_calls` rows for the inspector are optional; skip them rather than grow the PR (a new Alembic revision for two display-only columns isn't worth it here).

## Cost calculation

`token_cost` prices the three input buckets separately, exactly like `converse_cost`:

```
cost = (plain_in × input + cached_in × cached_input + written_in × cache_write) / 1_000_000
```

where `plain_in = max(tokens_in − cached_in − written_in, 0)` and `tokens_in` is the provider's total input count.

- `models.<id>.cached_input` prices cached reads; falls back to `input` when absent.
- `models.<id>.cache_write` prices Anthropic cache-creation tokens; falls back to `input` when absent.
- OpenAI reports cached reads only (`prompt_tokens_details.cached_tokens` is included in `prompt_tokens` — subtract it, don't add it).
- Anthropic reports all three separately (`input_tokens` excludes cached/written — sum them for `tokens_in`).
- The corrected cost flows everywhere `token_cost` is already used: `decisions.cost` + `cost_source`, `llm_calls` rows, shadow runs, backtest estimates and the monthly caps. Calls that predate the change keep their stored cost — costs are never rewritten.
- Tests: a cached-read call, a cache-write call and a mixed call each priced by hand; missing price keys fall back to `input`.

## Caveats

- Anthropic cache write costs 1.25× input; a cycle with exactly one call pays a small premium for nothing. Net-positive from 2 calls per 5-minute window.
- Only the system prompt is a shared prefix — user snapshots differ per symbol; different prompt versions / shadow strategies don't share cache.
- decision.v1 (~800 tokens) is under the providers' cache minimum; irrelevant once the configured prompt is v3+.
