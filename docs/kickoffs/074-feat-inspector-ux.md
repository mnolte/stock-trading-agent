# 074 · feat · LLM inspector UX

Kickoff for Devin. Start the session with: "Build docs/kickoffs/074-feat-inspector-ux.md."

Backend (one module, two endpoint fields) and frontend. No change to how outcomes and reasons are decided or stored. Spec: `docs/SPEC.md` → LLM call history → **Reasons explained** and **Inspector, easier to read**; `docs/CHANGES.md` entry [074]. Design: `design/screens/Inspector.dc.html` (click AAPL 18:46, GOOGL 16:46, the folded Verdict row and NVDA on 14 Oct), `design/CHANGES.md` entry [074]. Issue: #231.

```text
[074] LLM inspector UX. Goal: every warning and error explains itself —
what it means, the likely cause here, what to do — and the call is easy to
read: top reasons, folded repeats, clean labels, input as sections.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md → LLM call history: the Outcome table, Reasons explained
   (the table) and Inspector, easier to read; docs/CHANGES.md [074].
3. design/CHANGES.md [074]; design/screens/Inspector.dc.html with
   Inspector.png, Inspector-390.png, Inspector-error.png.

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.
Two PRs if it grows past ~800 lines: steps 1–3, then 4–7. Put
"Closes #231" in the description of the last one ("Part of #231" in the
first).

1. app/llm/reasons.py: one dict, reason code → title, means, cause, fix,
   links, with the spec table's text word for word. For changed_by_code,
   one entry per change reason (rr_below_min, stop_distance, spread,
   max_open_positions, sector_cap, total_open_risk, edge_too_small,
   flip_flop, price_stale, price_suspect, not_selected, trend gate, every
   gate name the code has). For api_error, one entry per status class
   (401/403, 404, 429, 5xx, no status). A test fails when a reason code or
   change reason in the code has no entry.

2. Call details. GET /api/llm-calls/{id} gains `explanations`: one item
   per reason, with the four texts and `detail`, the call's own specifics:
   - changed_by_code: the gate or check and its numbers, from the stored
     decision ("reward to risk 1.3, minimum 1.5");
   - input_incomplete: which fields were "unavailable" and the headline
     status, from the stored snapshot; for verdicts, which facts were
     unavailable (usually option touches) and the give-up date;
   - timeout / api_error: status, provider message (already scrubbed),
     and for a batched call (078) how the other calls of its batch did;
   - slow: latency against inspector.slow_call_seconds.
   Pure functions over stored rows, unit-tested per reason.

3. Summary. GET /api/llm-calls/summary gains `top_reasons`: reason (and
   change reason or review kind), count, the one-line fix, and the filter
   that lists them, most frequent first, at most 6. With a period of one
   session, `buckets` are per cycle (plus one "after the close" bucket for
   reviews) instead of one bucket.

3b. Attention. llm_calls.attention (none, watch, act; new migration,
   backfill none), set at write time by one pure function over the call
   and the earlier calls it needs (same session, last 20 of the model,
   last 5 reviews of the kind) with the spec's rules ("Does it need
   me?"). Never recomputed. Filter attention= on list, summary and CSV;
   the summary returns the three counts; top_reasons rows carry their
   level. Needs you gets one item per reason at `act` (spec's Needs you
   table), leaving when a later call of that kind succeeds. Tests: one per
   rule, on both sides of each threshold (2 vs 3 cycles in a row, 2 vs 3 of
   20, and so on), and that a later success doesn't rewrite stored levels.

4. Details panel. Each What happened panel shows its level as a chip
   (Nothing to do / Keep an eye on it / Needs you) with the reason on
   hover; Top reasons rows and the summary show the levels too. The outcome block keeps its chip and one-line result;
   under it one "What happened" panel per explanation (What it means / Why
   here / What to do, then link buttons). Success: "Valid · used as the
   model chose." only.

5. List. Consecutive calls of one session with the same source, kind,
   action, outcome and reasons fold into one row ("Verdict ×9 · input
   incomplete") that opens in place; a fold never contains the selected
   call; live decision changes and errors never fold. Review rows show the
   decision they judge ("NVDA skip"), never the kind twice.

6. Labels and input.
   - Model line: "gpt-4o-mini · temperature 0.2" or "claude-haiku-5-5 ·
     effort low"; a null value is "not recorded" (latency, tokens, status),
     never "—" or "0.00 s".
   - Prompt hash differs: the spec's sentence with both short hashes.
   - The user part as labelled sections (Call/Facts, Market, 1h
     indicators, Daily, Position, Options, Headlines) with a "Raw JSON"
     toggle; the raw view and the API use ensure_ascii=False, so × and −
     show as themselves.
   - The answer as label–value rows (empty fields left out) with Raw JSON
     and Copy; "The model answered enter → code made it skip" above them
     when code changed the action; a non-JSON answer shows raw.
   - Input order: single fields first under "Call", then the groups;
     headlines one row each (time · source · title).
   - "↑ Newer" and "↓ Older" in the details header, K and J (and the
     arrow keys while the list has focus), following the list's order and
     filters; the buttons disable at the ends.
   - Below 760 px the What happened and answer grids stack (label above
     text).
   - A batched call (078) shows "Sent · with 7 others · 18:46:04"; before
     078 is merged, leave the row out.

7. Screenshots in docs/screenshots/074/: AAPL changed_by_code, GOOGL
   input_incomplete, the Verdict fold open and closed, the NVDA timeout,
   the one-session chart, and 390 px.

Rules: no network in tests; explanation text only from reasons.py (no
copy in the frontend beyond labels); keys never in a message (the existing
scrubber); nothing in design/.

Done when: every reason code and change reason has an explanation (test);
every attention rule has its tests;
each detail function has a test with a stored fixture; the inspector
matches the design at 1440 and 390 with no sideways scroll; list and
summary still answer within 300 ms on the 100,000-call fixture; make test
and the e2e pass.
```

## Why

Review of the running app on 11 November:

- 9 verdicts showed "input incomplete" with no word on what was incomplete or whether to act. (They were the 058 case: option touches without 5-min bars, which settle by themselves after 2 sessions.)
- "Verdict verdict", "gpt-4o-mini · — · temp 0", "Latency 0.00 s".
- "hash differs from the prompt file — the text is withheld" didn't say what that means or what to check.
- The input was one ~3,000 px JSON block with `×` escapes.
- Nine identical rows in a row; with one session the chart was one big block.

## Assumptions

- The stored decision, snapshot and verdict facts hold everything the detail sentences need; nothing new is stored.
- Explanations are fixed text, not model output.

## Open questions for Marc

None. Decided without asking: links open existing screens with filters (Settings section, Logs at that minute, the decision); no new "dismiss" or "acknowledge" state for warnings.
