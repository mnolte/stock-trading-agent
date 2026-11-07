# 046 · fix · Spec and design alignment

Kickoff for Devin. Start the session with: "Build docs/kickoffs/046-fix-spec-alignment.md."

Two phases in one item. Phase 1 is an audit that produces a gap report and nothing else; Marc reads it and strikes or reorders lines. Phase 2 fixes the gaps in the agreed order, one PR per area. The GUI gaps have their own detail in `docs/kickoffs/045-feat-gui-catch-up.md` (item 045, PR #138); Phase 2 builds those steps too, skipping what the audit shows is already done.

```text
[046] Spec and design alignment. Goal: every behaviour in docs/SPEC.md is in
the code, every screen looks and reads like design/screens, and where they
can't agree the difference is written down. Start from the latest main (PR #138,
the design catch-up, is merged).

Read first:
1. AGENTS.md (hard rules) and docs/README.md (the section map, rules 1–10).
2. docs/SPEC.md, all of it, with docs/CHANGES.md open next to it (the dated
   entries say what changed recently and why).
3. design/DESIGN.md, design/CHANGES.md (entry [045]), then the screens you
   compare.
4. docs/analysis/045-gui-catch-up.md (the design review) and
   docs/analysis/2026-10-31-repository-scan.md (the last code scan), so you
   don't redo what they settled.
5. docs/kickoffs/045-feat-gui-catch-up.md (the GUI steps).

Rules for the whole item:
- The spec wins on behaviour, the design on look and wording. Where the
  code does something the spec does not describe, or the spec says two
  things, don't pick: write it as a question in the report (rule 2).
- Thresholds live in config (rule 3). A number found in code that the spec
  names is a gap even when the value is right.
- No trading-path change without a test that fails before the fix and
  passes after. No test touches the network. No datetime.now() in engine
  code. Money is Decimal. Every order has a stop leg. Nothing kills a
  process by name. The live Alpaca URL never appears.
- Prompts already used are never edited.
- backend/config.example.yaml must boot main at every commit.

PHASE 1 · the gap report (one PR, docs only: docs/analysis/046-spec-alignment.md)

A. Spec against code. Walk docs/SPEC.md section by section (the ## headings;
   the section map in docs/README.md says which PR built each). For every
   sentence that states a behaviour, a number, a name or a stored field,
   find where the code does it and classify:
   - built (file:line);
   - partial (what is missing, file:line);
   - missing;
   - differs (code does X, spec says Y; quote both);
   - spec-only question (the sentence can't be checked, or the spec
     contradicts itself).
   Cover at least: Market, schedule and data; Alpaca integration (orders,
   reconciliation, protective stops, held-back exits, fills settling,
   broker reads from the GUI); Decision layer (pre-filter, no_call reasons,
   live decisions failing); Risk management (every gate and brake, the
   shared risk math function); Decision v2–v5 (snapshot fields, code
   checks, gates, storage, counterfactual touches, base rates); Entry
   selection; Reviews (verdict facts, number check); Readiness (every
   criterion); LLM call history (fields, outcomes, reasons, filters);
   Insights (tiles, findings, funnel, slices, calibration, what each filter
   removed); Notifications (the full event table, rules, quiet hours);
   API keys; Logging, error handling and shutdown; Security hardening;
   VPS deployment; Ask the agent (tools, number check, caps, notes,
   standing instructions); Data model (every table and column named).
   Method that scales: one markdown table per section, one row per
   sentence, columns Spec (quoted, line number) · Status · Code · Note.
   Grep is enough for most rows; read the function when the status is not
   obvious. Don't run the market or call a provider.

B. Config against spec. For every number in the spec, the config key that
   holds it (rule 3). List: numbers in the spec with no key; keys in
   backend/config.example.yaml the code never reads; code constants that
   repeat a spec number.

C. Prompts against spec. For decision.v1–v5, ask.v1, briefing, weekly and
   verdict prompts: the manifest hash matches the file; the rules the spec
   says the prompt carries are in the text; the output schema fields match
   the spec's list and order; the Pydantic model matches the schema.

D. Design against the app. Seed the simulated app
   (backend/tests/e2e_seed.py + tests/readme_seed.py, as make readme-screens
   does) and screenshot every route at 1440 and 390 (and Dashboard,
   Positions, Settings at 1024). Put them next to the design PNGs in a
   table and list, per screen: blocks missing; blocks in another order;
   wording that differs (quote both); states the design has that the app
   can't reach (loading, empty, error, the banners in States); anything
   that scrolls sideways at 390. design/DESIGN.md → Interaction rules is
   the checklist for focus, hover, 44 px targets, live regions, tabs,
   dialogs and the page title.

E. Tests against spec. For each spec section, whether a test covers its
   "Done when" line (build plan) and its hard rules; which tests share a
   database; which tests would still pass if the behaviour were removed.

F. The report. docs/analysis/046-spec-alignment.md, first line
   "# 046 · Spec and design alignment: gap report", dated. Sections A–E
   as above, then a summary table of every gap ranked: blocking (money,
   safety, data loss, a hard rule), should fix (spec'd behaviour missing
   or wrong), nice to have (wording, layout). Each gap gets an id (G1,
   G2 …), the files, and the proposed fix in one line. End with the
   questions for Marc. Open it as a PR titled "[046] analysis: Spec and
   design alignment gap report" and stop. Do not start Phase 2 until Marc
   has commented on the report.

PHASE 2 · the fixes, in the order below, one PR per step, each titled
"[046] fix: <area>" and listing the gap ids it closes.

1. Blocking gaps from the report, in the report's order. Each with its
   failing test first.
2. Config and constants (B): add missing keys with the spec's defaults to
   backend/config.example.yaml, read them where the number was hard-coded,
   remove dead keys. One commit per key group.
3. Backend behaviour gaps (A, should fix), by spec section, oldest section
   first. Storage fields missing from the data model get an Alembic
   migration; never edit a merged migration.
4. Prompt and schema gaps (C): a new prompt version only when the text
   must change; otherwise fix the Pydantic model or the manifest.
5. GUI: build docs/kickoffs/045-feat-gui-catch-up.md steps 1–12, skipping
   what D shows is already there, and closing the D gaps that 045 does not
   name. Screenshots in docs/screenshots/046/ next to the design PNGs, with
   design differences listed in the PR.
6. Tests (E): a test per spec "Done when" line that had none; split tests
   that share a database.
7. Nice-to-have gaps, only if Marc keeps them in the report.

Every Phase 2 PR: docs/CHANGES.md entry when behaviour changes (rule 6);
the report's gap table updated with the PR number and "fixed" in the same
PR; docs/work-items.md row 046 updated.

Done when: every row in the report is "built", "fixed · #n" or
"won't fix · Marc, <date>"; make test passes; make readme-screens renders
every screen without a console error; and no sentence in docs/SPEC.md
describes behaviour the code does not have, or the spec has been changed
by Marc to say so.
```

## Why

The spec gained about 20 sections between PR 10 and PR 37 and three audits in a week (repository scan 31 Oct, trading path 5 Nov, design review 5 Nov) each found drift in a different place. One pass over the whole codebase, written down as a table, is cheaper than a fourth audit by surprise.

## What this item is not

- Not a new feature. Anything the spec doesn't describe is a question, not a fix.
- Not a spec rewrite. Spec text changes stay with Marc (the report lists them).
- Not a replacement for 045: the GUI detail lives there; 046 decides which of its steps are still needed and does them.

## Open questions for Marc

1. Phase 1 alone is probably two to three Devin sessions. Fine, or cap the audit to the sections built after PR 20 (where the drift is) and spot-check the rest?
2. Should the report's "won't fix" lines become spec text in the same item, or wait for a separate spec PR by Claude?
