# Work items

Every piece of work that gets a kickoff — a feature, a fix, an analysis, an ops change — is a **work item** with a number and a name. This file is the register: one row per item, newest at the bottom. Claude adds the row when the spec PR is opened; whoever opens or merges a PR for the item updates its PR and status columns. This file is not spec-owned, so Devin can update it without the `spec change` label.

## The name

```
NNN-type-slug        for example   099-fix-ko-overnight-exit
```

The examples use 099 as a made-up number.

- **NNN.** Three digits, one sequence for all types, never reused or renumbered. It continues the build plan: items 001–037 are build plan items 1–37. The next free number is one above the highest of: the last row below on `main`, and every `[NNN]` in the title of an open pull request (`gh pr list`), so two PRs open at the same time don't take the same number. If two merged items still share a number, the one whose first PR was opened later moves to the next free number, with a note in its row and its `docs/CHANGES.md` entry; that is the only renumbering (049, 7 Nov).
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
| Kickoff | `docs/kickoffs/NNN-type-slug.md`, first line `# NNN · type · Title`; `docs/kickoffs/archive/` from the first build PR | `docs/kickoffs/099-fix-ko-overnight-exit.md` |
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
- **Issues close themselves** (decided 11 Nov). An item with a GitHub issue names it in its row ("issue #230"). The build PR's description says `Closes #230`, so GitHub closes the issue when the PR is merged into `main`. An item built in several PRs: `Part of #230` in the earlier ones, `Closes #230` in the last. A spec PR says `Refs #230`, never `Closes`: merging the spec doesn't finish the work.

## Kickoffs move to the archive with the build

`docs/kickoffs/` holds only kickoffs nobody has started building. The PR that builds a kickoff moves it to `docs/kickoffs/archive/` in the same PR as the code, with `git mv`, same file name, content unchanged (decided 7 Nov):

- one build PR: that PR moves it, and sets the row to the build PR and `building`; Devin sets `merged` after the merge, or the next PR does;
- several build PRs (a phased item such as 046, or steps in separate PRs): the first one moves it; the later ones read it from `archive/` and leave it there;
- a `spec` or `analysis` item without a build: the PR that completes it;
- a `dropped` item's kickoff moves too, with the reason in its row.

An unchanged move into `archive/` needs no `spec change` label (the gate checks that the file is identical and keeps its name); a move with any edit, or out of the archive, does. If the kickoff must change, change it in a spec PR first. Old paths in `docs/CHANGES.md` and in code comments are not rewritten: a kickoff that isn't in `docs/kickoffs/` is in `docs/kickoffs/archive/`. `archive/DEVIN_KICKOFF.md` holds items 001–028.

So: a file in `docs/kickoffs/` is work not yet started; anything in `archive/` is being built or done, and the register's Status says which.

## Register

| Nr | Type | Title | Kickoff | Spec PRs | Build PRs | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 001 | feat | Skeleton | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 002 | feat | Market and data | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 003 | feat | Signals, regime and rules | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 004 | feat | Decision layer | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 005 | feat | Execution | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 006 | feat | GUI | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 007 | feat | Performance and outcomes | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 008 | feat | Hardening | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 009 | feat | Reviews | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 010 | feat | Settings and API keys | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 011 | feat | Pool and liquidity screen | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 012 | feat | Cycles at scale | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 013 | feat | Strategies and shadows | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 014 | feat | Backtesting | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 015 | fix | Integrity fixes | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 016 | feat | Readiness | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 017 | feat | Dashboard and interaction polish | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 018 | feat | LLM call history | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 019 | feat | Tail metrics | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 020 | feat | Logging, error handling and shutdown | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 021 | feat | Decision v2: code-built options | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 022 | feat | Insights | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 023 | feat | Sign-in and access | `archive/DEVIN_KICKOFF.md` | — | #43 | merged |
| 024 | ops | VPS deployment | `archive/DEVIN_KICKOFF.md` | — | #44 | merged |
| 025 | feat | Error pages and API errors | `archive/DEVIN_KICKOFF.md` | — | #45 | merged |
| 026 | fix | Day-trade rule retired | `archive/DEVIN_KICKOFF.md` | — | #41 | merged |
| 027 | fix | Protective stops hardened | `archive/DEVIN_KICKOFF.md` | — | — | merged |
| 028 | fix | Security hardening | `archive/DEVIN_KICKOFF.md` | — | #47 | merged |
| 029 | feat | Model providers | `archive/pr-29.md` | — | #61 | merged |
| 030 | feat | Decision v3 | `archive/pr-30.md` | #50, #51, #53 | #54 | merged |
| 031 | fix | Exit fills and reconciliation | `archive/pr-31.md` | #55 | #58 | merged |
| 032 | feat | Review attribution | `archive/pr-32.md` | #57 | #59, #75 | merged |
| 033 | feat | Entry selection | `archive/pr-33.md` | #67 | #78 | merged |
| 034 | feat | Decision v4 | `archive/pr-34.md` | #79, #83 | #82 | merged |
| 035 | feat | Needs you and positions at a glance | `archive/pr-35.md` | #84 | #90 | merged |
| 036 | feat | Ask the agent | `archive/pr-36.md` | #91, #92 | #98, #99 | merged |
| 037 | feat | Decision v5 | `archive/pr-37.md` | #103, #105 | #106 | merged |
| 038 | spec | Naming convention for work items | — | #107 | — | merged |
| 039 | fix | Protective stop repair | — | — | #111, #112 | merged |
| 040 | fix | EP shadow gate_detail crash | — | — | #113 | merged |
| 041 | fix | Stale header numbers | — | — | #115 | merged |
| 042 | fix | Backend test audit fixes | — | — | #116 | merged |
| 043 | fix | Trading path audit fixes | — | — | #123 | merged |
| 044 | spec | Spec rulings from the 043 audit | — | #135 | — | merged |
| 045 | feat | GUI catch-up | `045-feat-gui-catch-up.md` | #138 | #146, #170 | merged |
| 046 | fix | Spec and design alignment | `archive/046-fix-spec-alignment.md` | #140 | #150, #151, #152, #165, #166, #167, #168 | merged |
| 047 | fix | Drawer model-call e2e flake | — | — | #141 | merged |
| 048 | spec | Kickoff archive | — | #142 | — | merged |
| 049 | fix | Log routes 503 on schema-less index (merged as [048]; renumbered 7 Nov, the number was taken) | — | — | #143 | merged |
| 050 | fix | Stop invariant | `archive/050-fix-stop-invariant.md` | #144 | #148 | merged |
| 051 | spec | Archive kickoffs with the build | — | — | — | merged |
| 052 | fix | Symbol-keyed config keys | `archive/052-fix-symbol-config-keys.md` | #177 | #178 | building |
| 053 | docs | Security audit | — | — | #164, #171 | building |
| 054 | fix | Restore-test 'not set up' logs unparseable-at warning hourly | — | — | #179 | building |
| 055 | feat | Loading states | `archive/055-feat-loading-states.md` | #180 | #181 | building |
| 056 | feat | UX pass | `archive/056-feat-ux-pass.md` | #188 | #190, #191, #192, #193, #194, #195, #196, #197 | building |
| 057 | fix | Log review fingerprint match | — | — | #198 | building |
| 058 | fix | Live data of 9 November | `archive/058-fix-live-data-9-nov.md` | #199 | #200, #201, #202 | building |
| 059 | spec | PRs ready to merge (CI green, no conflicts) | — | #203 | — | merged |
| 060 | feat | VIX data, collected and shown (issue #205) | `archive/060-feat-vix-data.md` | #208 | #209 | building |
| 061 | feat | Decision v7: VIX, release calendar, breadth and sectors in the model call (issue #206; step A after 060, 067, 068 and 20 sessions) | `061-feat-decision-v7.md` | #223 | — | planned |
| 062 | fix | Decision gate settings could not be saved | — | — | #210 | building |
| 063 | fix | Price-stale quotes: one retry, age judged at check time | — | — | #211 | building |
| 064 | fix | Logs screen counts unreviewed like Readiness | — | — | #212 | building |
| 065 | fix | VIX fetch retries every 15 minutes while failing | — | — | #213 | building |
| 066 | feat | "Get a key" links in Settings → API keys | — | — | #214 | building |
| 067 | feat | Economic releases (issue #215) | `archive/067-feat-economic-releases.md` | #220 | #227 | building |
| 068 | feat | Breadth and sectors, collected and shown (issue #216) | `archive/068-feat-breadth-sectors.md` | #222 | #228 | building |
| 069 | feat | Settings UX | `archive/069-feat-settings-ux.md` | #218 | #229, #252 | building |
| 070 | fix | Test and save disabled on API keys | — | — | #219 | building |
| 071 | feat | New entries in the last cycle (15:46), entry cutoff 15:50 | `archive/071-feat-entries-in-last-cycle.md` | #221 | #226 | building |
| 072 | fix | Wider HTTP pool for the Alpaca clients | — | — | #225 | building |
| 073 | feat | Dashboard UX (issue #230) | `archive/073-feat-dashboard-ux.md` | #237 | #258 | building |
| 074 | feat | LLM inspector UX: reasons explained (issue #231) | `archive/074-feat-inspector-ux.md` | #237 | #257 | building |
| 075 | feat | Insights UX: five questions (issue #232) | `archive/075-feat-insights-ux.md` | #237 | #256 | building |
| 076 | feat | Performance UX (issue #233) | `archive/076-feat-performance-ux.md` | #237 | #264 | building |
| 077 | feat | Universe UX and Data sync (issue #234) | `archive/077-feat-universe-ux.md` | #237 | #265, #269 | building |
| 078 | feat | Concurrent requests, `concurrency.max_requests` 20 (issue #235) | `archive/078-feat-concurrent-requests.md` | #237 | #266 | building |
| 079 | feat | Daily briefing and weekly review as short stories (issue #236) | `archive/079-feat-review-stories.md` | #237 | #270 | building |
| 080 | analysis | Security audit of the repository (`docs/analysis/080-security-audit.md`; issue #241 for the GitHub settings) | — | #251 | — | merged |
| 081 | fix | Keys out of error text; Ask sees trading settings only; heartbeat URL as a secret (issues #238, #240, #246) | `archive/081-fix-keys-out-of-error-text.md` | #251 | #268, #272 | building |
| 082 | feat | Confirm every loosening (issue #239) | `archive/082-feat-confirm-every-loosening.md` | #251 | #276 | building |
| 083 | feat | Sign-in resilience: known browsers, break-glass kill switch, WebSocket sessions (issues #243, #244) | `archive/083-feat-sign-in-resilience.md` | #251 | #277 | building |
| 084 | ops | Supply chain and containers (issues #242, #245, #248, #249) | `archive/084-ops-supply-chain-and-containers.md` | #251 | #275, #278 | building |
| 085 | fix | CSV formulas and outbound addresses (issues #247, #250) | `archive/085-fix-csv-formulas-and-outbound-addresses.md` | #251 | #274 | building |
| 086 | feat | Prompt caching on `complete()` model calls (issue #224) | `archive/086-feat-prompt-caching-complete.md` | — | #253 | building |
| 087 | spec | Tests run in CI, not on the laptop | — | #254 | — | merged |
| 088 | ops | Code coverage report from CI (issue #259) | `archive/088-ops-code-coverage.md` | #260 | #263, #267 | building |
| 089 | spec | A PR closes only issues it resolves | — | #261 | — | merged |
| 090 | fix | After-exit job's SQLite off the event loop | — | — | #262 | building |
| 091 | fix | Number check: numbers inside fact strings | `archive/091-fix-number-check-fact-strings.md` | — | #273 | building |
| 092 | fix | Daily bars: no source gap before the session closes | `docs/kickoffs/092-fix-premature-source-gaps.md` | #284 | — | planned |
| 093 | fix | Universe screen: stale detail and no re-run | `docs/kickoffs/093-fix-stale-screen-detail.md` | #286 | — | planned |
| 094 | fix | rules_only backtests: wave API bypassed decide() | — | — | #288 | building |
| 095 | fix | CodeQL py/path-injection: backtest delete used stored run_dir | — | — | — | building |

Status is one of `planned` (spec PR open), `ready` (spec merged, not started), `building` (build PR open or more PRs to come; kickoff in `archive/`), `merged` (kickoff in `archive/`), or `dropped` (with the reason in the row; kickoff in `archive/`).

### Before the convention

Work with a kickoff but no number, kept under its old file name. All of it is merged, so the kickoffs are in `archive/` under their old names.

| Kickoff | What | Spec PRs | Build PRs |
| --- | --- | --- | --- |
| `archive/fix-live-2026-10-29.md` | Live fixes of 29 Oct: stream envelope, clock check, break-even guard | #55 | #56 |
| `archive/fix-live-2026-10-30.md` | Live fixes of 30 Oct: decisions failing, fill settling, GUI timeouts | #65 | #64, #66 |
| `archive/ops-alerts.md` | Alerts, log volume, small fixes | #55 | #62 |
| `archive/quote-plausibility.md` | Analysis: IEX quote plausibility | — | #63 |
| `archive/fix-scan-2026-10-31.md` | Repository scan fixes | #71 | #72, #74 |
| — | Analysis: decision v4 candidates | — | #73 |
| — | Decision bookkeeping B1–B4 | — | #80, #81 |
| `archive/fix-v4-2026-11-03.md` | Decision v4 code fixes of 3 Nov | #103 | #104 |
| — | Trading path audit fixes; Ask number check | — | #100, #102 |
