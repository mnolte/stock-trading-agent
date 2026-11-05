# Work items

Every piece of work that gets a kickoff — a feature, a fix, an analysis, an ops change — is a **work item** with a number and a name. This file is the register: one row per item, newest at the bottom. Claude adds the row when the spec PR is opened; whoever opens or merges a PR for the item updates its PR and status columns. This file is not spec-owned, so Devin can update it without the `spec change` label.

## The name

```
NNN-type-slug        for example   099-fix-ko-overnight-exit
```

The examples use 099 as a made-up number.

- **NNN.** Three digits, one sequence for all types, never reused or renumbered. It continues the build plan: items 001–037 are build plan items 1–37. The next free number is the row after the last one below.
- **type:**
  - `feat`: new behaviour or a new screen;
  - `fix`: behaviour that is wrong against the spec, or a bug;
  - `analysis`: a read-only report, no code change;
  - `ops`: deployment, CI, tooling, dependencies, performance of the build or tests;
  - `spec`: a spec or design change that needs no build (a clarification, a rule decided in review).
- **slug.** Kebab-case, 2–5 words: what the item does, never when it was made. No dates. A version number only when the thing itself is versioned (`decision-v5`).
- **Title.** The same words in plain English, for PR titles and changelog headings: "099 · fix · KO overnight exit".

## Where the name goes

| Thing | Format | Example |
| --- | --- | --- |
| Kickoff | `docs/kickoffs/NNN-type-slug.md`, first line `# NNN · type · Title` | `docs/kickoffs/099-fix-ko-overnight-exit.md` |
| Spec branch (Claude) | `spec/NNN-slug` | `spec/099-ko-overnight-exit` |
| Build branch (Devin) | `type/NNN-slug` | `fix/099-ko-overnight-exit` |
| PR title | `[NNN] type: Title` | `[099] fix: KO overnight exit` |
| Spec PR title | `[NNN] spec: Title` | `[099] spec: KO overnight exit` |
| Follow-up PR | same item number, "(follow-up)" | `[099] fix: KO overnight exit (follow-up)` |
| Commits | start with `[NNN]` | `[099] Exit before the close when the stop sits inside the gap` |
| Screenshots | `docs/screenshots/NNN/` | `docs/screenshots/099/` |
| Analysis report | `docs/analysis/NNN-slug.md`, date in its first line | `docs/analysis/099-entry-filters.md` |
| Changelog heading | `## YYYY-MM-DD — [NNN] Title` | `## 2026-11-05 — [099] KO overnight exit` |
| Build plan (SPEC) | item number = work item number | `99. **KO overnight exit.**` |
| Start Devin | one line | "Build docs/kickoffs/099-fix-ko-overnight-exit.md." |

- **Item numbers and GitHub PR numbers never mix.** An item is always written with three digits (`099`, `[099]`, "item 099"), a GitHub PR always with `#` (`#106`).
- Older spec text says "PR 37" for build plan item 37. That means item 037, not GitHub #37.
- **One item, one build PR where possible.** An urgent part can be split off as its own item, for example a settings fix that must ship before a session.
- **Small fixes found during a review** of item NNN go into a follow-up PR of that item. They get their own number only when they need their own kickoff.
- **Dependabot and other automatic PRs** keep their own names and get no number.

## Register

| Nr | Type | Title | Kickoff | Spec PRs | Build PRs | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 001 | feat | Skeleton | `DEVIN_KICKOFF.md` | — | — | merged |
| 002 | feat | Market and data | `DEVIN_KICKOFF.md` | — | — | merged |
| 003 | feat | Signals, regime and rules | `DEVIN_KICKOFF.md` | — | — | merged |
| 004 | feat | Decision layer | `DEVIN_KICKOFF.md` | — | — | merged |
| 005 | feat | Execution | `DEVIN_KICKOFF.md` | — | — | merged |
| 006 | feat | GUI | `DEVIN_KICKOFF.md` | — | — | merged |
| 007 | feat | Performance and outcomes | `DEVIN_KICKOFF.md` | — | — | merged |
| 008 | feat | Hardening | `DEVIN_KICKOFF.md` | — | — | merged |
| 009 | feat | Reviews | `DEVIN_KICKOFF.md` | — | — | merged |
| 010 | feat | Settings and API keys | `DEVIN_KICKOFF.md` | — | — | merged |
| 011 | feat | Pool and liquidity screen | `DEVIN_KICKOFF.md` | — | — | merged |
| 012 | feat | Cycles at scale | `DEVIN_KICKOFF.md` | — | — | merged |
| 013 | feat | Strategies and shadows | `DEVIN_KICKOFF.md` | — | — | merged |
| 014 | feat | Backtesting | `DEVIN_KICKOFF.md` | — | — | merged |
| 015 | fix | Integrity fixes | `DEVIN_KICKOFF.md` | — | — | merged |
| 016 | feat | Readiness | `DEVIN_KICKOFF.md` | — | — | merged |
| 017 | feat | Dashboard and interaction polish | `DEVIN_KICKOFF.md` | — | — | merged |
| 018 | feat | LLM call history | `DEVIN_KICKOFF.md` | — | — | merged |
| 019 | feat | Tail metrics | `DEVIN_KICKOFF.md` | — | — | merged |
| 020 | feat | Logging, error handling and shutdown | `DEVIN_KICKOFF.md` | — | — | merged |
| 021 | feat | Decision v2: code-built options | `DEVIN_KICKOFF.md` | — | — | merged |
| 022 | feat | Insights | `DEVIN_KICKOFF.md` | — | — | merged |
| 023 | feat | Sign-in and access | `DEVIN_KICKOFF.md` | — | #43 | merged |
| 024 | ops | VPS deployment | `DEVIN_KICKOFF.md` | — | #44 | merged |
| 025 | feat | Error pages and API errors | `DEVIN_KICKOFF.md` | — | #45 | merged |
| 026 | fix | Day-trade rule retired | `DEVIN_KICKOFF.md` | — | #41 | merged |
| 027 | fix | Protective stops hardened | `DEVIN_KICKOFF.md` | — | — | merged |
| 028 | fix | Security hardening | `DEVIN_KICKOFF.md` | — | #47 | merged |
| 029 | feat | Model providers | `pr-29.md` | — | #61 | merged |
| 030 | feat | Decision v3 | `pr-30.md` | #50, #51, #53 | #54 | merged |
| 031 | fix | Exit fills and reconciliation | `pr-31.md` | #55 | #58 | merged |
| 032 | feat | Review attribution | `pr-32.md` | #57 | #59, #75 | merged |
| 033 | feat | Entry selection | `pr-33.md` | #67 | #78 | merged |
| 034 | feat | Decision v4 | `pr-34.md` | #79, #83 | #82 | merged |
| 035 | feat | Needs you and positions at a glance | `pr-35.md` | #84 | #90 | merged |
| 036 | feat | Ask the agent | `pr-36.md` | #91, #92 | #98, #99 | merged |
| 037 | feat | Decision v5 | `pr-37.md` | #103, #105 | #106 | merged |
| 038 | spec | Naming convention for work items | — | #107 | — | planned |
| 039 | fix | Protective stop repair | — | — | #111, #112 | building |
| 040 | fix | EP shadow gate_detail crash | — | — | #113 | building |
| 041 | fix | Stale header numbers | — | — | #115 | building |
| 042 | fix | Backend test audit fixes | — | — | — | building |

Status is one of `planned` (spec PR open), `ready` (spec merged, not started), `building` (build PR open), `merged`, or `dropped` (with the reason in the row).

### Before the convention

Work with a kickoff but no number, kept under its old file name. The old names stay as they are, so links in `docs/CHANGES.md` keep working.

| Kickoff | What | Spec PRs | Build PRs |
| --- | --- | --- | --- |
| `fix-live-2026-10-29.md` | Live fixes of 29 Oct: stream envelope, clock check, break-even guard | #55 | #56 |
| `fix-live-2026-10-30.md` | Live fixes of 30 Oct: decisions failing, fill settling, GUI timeouts | #65 | #64, #66 |
| `ops-alerts.md` | Alerts, log volume, small fixes | #55 | #62 |
| `quote-plausibility.md` | Analysis: IEX quote plausibility | — | #63 |
| `fix-scan-2026-10-31.md` | Repository scan fixes | #71 | #72, #74 |
| — | Analysis: decision v4 candidates | — | #73 |
| — | Decision bookkeeping B1–B4 | — | #80, #81 |
| `fix-v4-2026-11-03.md` | Decision v4 code fixes of 3 Nov | #103 | #104 |
| — | Trading path audit fixes; Ask number check | — | #100, #102 |
