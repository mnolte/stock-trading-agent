# 078 · feat · Concurrent requests

Kickoff for Devin. Start the session with: "Build docs/kickoffs/078-feat-concurrent-requests.md."

Backend. Changes when requests run, not what they are: no change to which symbols get a model call, to any limit, gate or threshold, or to order handling. Spec: `docs/SPEC.md` → **Concurrent requests** (new section), Cycles at scale and Entry selection step 3 (both point to it); `docs/CHANGES.md` entry [078]. No design of its own; its numbers show on the dashboard (073), the inspector (074) and Universe → Data sync (077). Issue: #235.

```text
[078] Concurrent requests. Goal: a cycle sends its model calls in two
waves (held, then candidates), each at once, and every sync job sends its
independent requests at once, at most concurrency.max_requests (20) in
flight per batch — with the call cap, rate limits, timeouts and the order
of storing and applying unchanged.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md (rule 7: the example config).
2. docs/SPEC.md → Concurrent requests (all), Cycles at scale, Entry
   selection, Rate limits, LLM call history → Storage (hard rule 13);
   docs/CHANGES.md [078].
3. backend/app/engine/cycle.py (the rank loop around "for i, item in
   enumerate(reviews)" and _run_expected_profit), app/data/service.py
   (refresh_daily, refresh_history, refresh_bars), app/universe/service.py
   (the screen's Semaphore(10)), app/data/alpaca.py (_HTTP_POOL_MAXSIZE),
   app/data/rate_limiter.py, app/shadows/runner.py.

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.
Two PRs: steps 1–4 (the batch helper and the cycle), then 5–8 (syncs,
shadows, verdicts, display). Put "Part of #235" in the first and
"Closes #235" in the second.

1. Config. Add to backend/config.example.yaml, after `gui:`:
     concurrency:                     # item 078, docs/SPEC.md — Concurrent requests
       max_requests: 20               # most requests one batch keeps in flight (1-50); also sizes the HTTP pools; restart to change
   Add `concurrency.max_requests` to the README's config table (the
   README test checks every key). Settings → Server shows it read-only
   with "restart to change". Validation: integer 1–50, else refuse to
   start with a clear message.

2. One batch helper, app/concurrency.py:
     async def run_batch(items, send, *, limit, priority="live") -> list
   - at most `limit` sends in flight (an asyncio.Semaphore per batch);
   - returns results in the order of `items`, each a value or the
     exception it raised (one failure never cancels the others);
   - records max in flight and wall time, and logs one INFO line per
     batch: kind, size, most in flight, wall time, failures;
   - live priority: a process-wide gate per source so that live cycle
     requests are sent before waiting sync, shadow, review, Ask or
     backtest requests to the same source (a priority queue in front of
     the source's rate limiter; the limiter itself is unchanged).
   The source's rate limiter and timeout stay inside `send`. Size every
   HTTP client's pool to max(max_requests, 20) per host (replaces the
   fixed _HTTP_POOL_MAXSIZE).

3. The cycle (rank mode and expected_profit), per the spec's steps:
   - bars refresh, account refresh and market context in parallel after
     reconciliation, each with its own health line;
   - held wave: build every held snapshot first, fetch held headlines, then
     run_batch the calls; validate, store and apply in held order;
   - plan candidates against the state after the held wave (price checks,
     trend gate, options and room decided by code; the called set is the
     first ones needing a call up to max_calls_per_cycle minus held calls;
     the rest stored as no_call call_cap, as today);
   - candidate wave: headlines for the called set, build every snapshot,
     run_batch the calls, then validate and store in call order, then
     gates, fresh quote and submit one at a time in rank order (rank mode)
     or via the selection path (expected_profit, whose collect step this
     replaces).
   Split _decide_item into prepare (sync: snapshot, prompt), call (async,
   no DB access) and finish (sync: validate, store the decision and its
   llm_calls row in one transaction). The DB session is only touched by
   the cycle task, never inside `send`.

4. Tests (fake model with random latencies, fake clock):
   - stored decision ids and applied actions follow rank order whatever
     the completion order (run 20 seeds);
   - 15 symbols needing a call, cap 10: exactly 10 calls, 5 call_cap rows;
     with 2 held: 2 held calls + 8 candidates;
   - max_requests 3: the fake never sees more than 3 in flight;
   - one call times out at 30 s: the others are stored, the cycle goes on;
   - rank mode, 1 free slot, two passing enters: the first is sent, the
     second ends blocked by max_open_positions at submit;
   - max_requests 1: the replay fixture gives the same stored decisions as
     before this change, apart from sent_at and cycles where two or more
     entries passed (list those cycles in the PR);
   - a simulated cycle with 10 calls at a fake 2 s latency spends under
     5 s from the first call sent to the last answer.

5. Syncs, each with run_batch: daily bars (one request per symbol),
   history and warm-up (per start-date group, symbols chunked), the daily
   screen (replaces Semaphore(10)), missing bars for option touches, the
   morning refresh (earnings, corporate actions, FRED series and releases,
   ECB as parallel sources), backtest downloads (within their 40/min).
   Results written after the batch, one transaction per symbol as today;
   a partial failure keeps the rest and lists the failed ones. Test: the
   daily refresh over 72 symbols never exceeds 20 in flight or 150 calls
   per minute (fake limiter clock).

6. Shadows: per active shadow, one batch per cycle with the same plan rule
   and its own 10-call cap, after live's orders, never delaying live
   (existing missed-work rule). Verdict drafts that come due together: one
   batch. Briefing, weekly and Ask unchanged.

7. Storage (new migration): cycles.model_seconds, cycles.max_in_flight;
   llm_calls.batch_id, llm_calls.batch_size. Expose them in the cycle
   summary and the call detail endpoints (073 and 074 show them).

8. Docs: README config table (step 1); docs/DEPLOY.md one line on the
   setting.

Rules: no network in tests; no datetime.now() in engine code (the clock);
orders, reconciliation, the kill switch and Stop & flatten stay
sequential; every llm_calls row written with its linked row in one
transaction (hard rule 13); no change to what the model is sent.

Done when: the tests in step 4 and 5 pass; a simulated session with the
72-symbol pool keeps every cycle under 10 minutes and under 150 Alpaca
calls per minute (the item 012 check still passes); main boots from the
example config; make test and the e2e pass.
```

## Why

Marc, 11 November: "Execute requests in the cycle concurrently whenever possible. Try and make it work with the max model calls setting. Execute requests, when synchronizing with data sources, concurrently whenever possible. Make the requests executed concurrently a setting in config.example.yml, default to 20."

- Today the rank loop calls the model, applies the answer, then calls the next: 10 calls at 1.5–2 s each take about 18 s.
- The daily bar refresh and the screen already run 10 at once with their own semaphores; history refresh runs its groups one after another; the morning sources run in sequence.

## The model call cap: it stays

It works with concurrency because whether a symbol needs a call is decided by code before any call is sent (rules, price checks, trend gate, options and room). The called set is fixed per wave, so the cap can't be exceeded however answers arrive. The one change on purpose: candidates are planned against the state after the held wave rather than after each earlier entry, so a second entry that no longer fits is blocked by its gate at submit, after its call, instead of before it. That can cost a few extra calls in a cycle where 2 or more entries pass; never more than the cap.

## Assumptions

- The providers' rate limits allow 10 calls at once on the current accounts. A 429 stays an `api_error` (not retried) and the inspector (074) explains it; lowering `max_requests` is the fix.
- `concurrency.max_requests` is not in this spec PR's example config: the README test requires every example key to be in the README, which Devin owns, so step 1 adds both in the build PR.

## Open questions for Marc

None blocking. Decided without asking: one setting for every batch (not one per source); a restart to change it, because it also sizes the HTTP pools.
