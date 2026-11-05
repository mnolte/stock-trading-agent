# 045 · GUI catch-up: design review against the spec

By Claude, 5 November 2026. The review that item 045 acts on. Written before any change; the section "What 045 changed" at the end says which findings the design PR fixed and which are left to Marc (spec text) or to later items.

**What was reviewed.** Every file in `design/screens/` (24 screens) and `design/screenshots/` (36 PNGs at 1440, 560 and 390 px) against `docs/SPEC.md` at `main` 1c4bb9d, `design/DESIGN.md`, `design/CHANGES.md` and the open items of `docs/analysis/2026-11-01-ux-audit.md`. The app itself was looked at only where it has already moved away from the design (the Ask button).

**Severity.** *High*: Marc misses something that needs him, or Devin builds the wrong thing from the design. *Medium*: slows reading or hides a spec'd feature. *Low*: polish and hygiene.

**Kind.** *Spec mismatch*: the design shows something other than the spec. *Spec gap*: the spec has no text for it. *UX*: both agree, but it can be better.

---

## 1. Summary

The design is strong where it was built last: Needs you, the position bars, the v3–v5 drawers, Readiness, Universe and Ask. The problems are mostly age: ten screens were drawn at PR 6–22 and never caught up with 20 spec sections written since. Ranked by value:

| # | Finding | Kind | Severity |
| --- | --- | --- | --- |
| 1 | The header has three different layouts and wraps to 2–4 rows on every screen (160–220 px before content; about 300 px on a phone). The app has meanwhile moved Ask to the sidebar (commit cea419d, today), so design and spec are behind the app. | UX + spec | High |
| 2 | Decision drawers v2–v5 have no Outcome block, no Verdict block (Confirm / Change), no order status and no "Ask about this" link. Only the v1 drawer has them. Seven drawers use five section orders. | Spec mismatch | High |
| 3 | Settings is 11,193 px, shows 13 of the 27 notification events, lists `decision.v1` as the only decision prompt and has none of the settings added since PR 10 (entry selection, v3–v5 gates, `gui.near_stop_atr`, quote ages, broker timeout). | Spec mismatch | High |
| 4 | Needs you shows 3 of the 7 spec'd item kinds; none of the items added since PR 35 (held-back exit, KO-style overnight hold, live decisions failing, stream degraded, settings waiting for a restart) is designed, and neither are their banners. | Spec mismatch | Medium |
| 5 | The dashboard watchlist is drawn in fixed mode only (no rank column, no "15 rows, then Show all"); the run uses the screened pool. The latest-decision card has no v2+ state ("D · stop below L8 · target 2R"). The feed shows none of the newer code reasons (`flip_flop`, `not_selected`, `checks`, `no_options`). | Spec mismatch | Medium |
| 6 | LLM inspector filters are the PR 18 set: no `Ask` source, prompt list ends at `decision.v1`, reason list lacks the dozen reasons added since (`unsupported_claim` … `hold_below_breakeven`). | Spec mismatch | Medium |
| 7 | Decision v5 has no GUI paragraph in the spec, and the design has no v5 candidate drawer (`min_confidence`, `target_atr`, `base_rate` columns) and no "What each filter removed" block on Insights, although `CHANGES.md` says the v4 file carries the columns. | Spec gap | Medium |
| 8 | Verdicts to confirm: the draft is pre-highlighted among three equal buttons, so it isn't clear what confirms; no bulk confirm; no link to the decision. | UX | Medium |
| 9 | No phone screenshot for Positions, Reviews, the Ask drawer or any decision drawer, and no design at all between 390 and 1440 px (the app breaks at 820 and docks Ask from 1280). | UX | Medium |
| 10 | Hygiene: `gpt-4o-mini` in 9 files, "v0.1" in the sidebar, design notes rendered as product copy inside cards, the 10:46 sample finding that already misled one analysis, hyphen-minus in Reviews, `DESIGN.md` says "7 screens". | Low | Low |

Items 4–11 of the 1 November audit are all still open; they are folded into the sections below.

---

## 2. Cross-cutting

### 2.1 Header and shell (High)

What the spec says (GUI, line 328): one header on every screen with PAPER, session pill with countdown, Ask button, Needs you chip, equity, today, regime, Pause/Resume, kill switch. DESIGN.md's shared-components list (item 2) still names the PR 1 header without Ask, Needs you or Pause.

What the design does:
- Three layouts. Main, Positions, Universe: title + PAPER on one line, pill below, chip below that (3 rows, 160 px). Performance, Insights, Reviews, Inspector, Readiness, Settings: title, subtitle, PAPER on its own row, pill, chip (4–5 rows, 200–220 px). With the Ask drawer docked (`AskDrawer.png`) the chip moves next to the pill and Kill switch drops to its own row.
- Phone (`Main-390.png`): the top bar shows "Dashboard" and the page header repeats "Dashboard · Thursday 15 October"; equity, today, regime, Ask, Pause and Kill switch stack to about 300 px; Ask appears twice (top bar and header).
- No New York time on any clock except the session pill, although DESIGN.md's interaction rules say every time shown has one.

The app: commit cea419d removed the header Ask button; `Sidebar.tsx` now has "Ask the agent" under the app mark. Spec and design both still say header.

Proposal:
- One header on every screen, two fixed rows (about 110 px): row 1 the title, PAPER and a short subtitle; row 2 the session pill and the Needs you chip on the left, then equity, today, regime, Pause and Kill switch on the right. Without the Ask button row 2 fits in 1,160 px, the content width at 1440. Nothing wraps, whatever the title length.
- Follow the app: Ask lives in the sidebar (full-width accent button under the mark, icon only when collapsed) and in the phone top bar; Ctrl/⌘+J stays. Update spec line 328, DESIGN.md item 2 and every screen.
- Phone: drop the repeated title; one line "Equity $10,184 · Today +$42.10 · Up · normal vol"; Pause and Kill switch as two half-width buttons on one row. Target: about 120 px, not 300.
- Decide the NY-time rule once (see 2.6) and model it in the design on at least Main and Inspector.

### 2.2 Decision drawers (High)

What the spec says: Decision detail (line 333) includes outcome after 1h, 1 session and 5 sessions with best and worst R and first touch, order status, links to the position and the model call; the verdict block with Confirm and Change (lines 594, 607); "Ask about this" in the drawer (line 1890); v2, v3, v4 GUI lists (lines 1034, 1587, 1759); the Selection block in v2 and v3 drawers (line 1625).

What the design does:

| Drawer | Outcome | Verdict | Order status | Selection block | Ask about this | Section order |
| --- | --- | --- | --- | --- | --- | --- |
| Decision (v1) | yes | yes | — | — | no | gates, signals, reasoning, outcome |
| DecisionV2 | no | no | footer line only | yes | no | options, levels, reasoning, gates, selection |
| DecisionV3 | no | no | no | no | no | checks, options, levels, reasoning, gates |
| DecisionV3Held | no | no | no | — | no | position, stop options, reasoning, checks |
| DecisionV4 | no | no | no | no | no | previous, checks, options, reasoning, gates |
| DecisionV4Held | no | no | no | — | no | thesis, position, reasoning, checks |
| DecisionV5Held | no | no | no | — | no | hold-or-exit, position, thesis, reasoning, checks |

Also: no v5 candidate drawer (see 2.5); no drawer at phone width (the 560 px drawer must fill a 390 px screen); the drawers are 1,603–2,768 px long (audit item 4).

Proposal, one layout for every version (audit item 5), top to bottom:
1. Header: when, cycle, strategy, model, prompt; symbol; regime chip; "changed by code" chip.
2. Action: badge, size, confidence bar; for a skip "skip · best A · 0.38"; for code changes "model: ENTER A → skip · flip_flop" as DecisionV4 already does.
3. **What happened** (new, audit item 4): final action, every code change with its reason, warnings, order status (filled 4 @ 418.20 · stop and target open), the verdict with Confirm and Change when drafted, and "Ask about this".
4. Tiles: entry, stop, target, R/R or R now.
5. Version blocks in this order: Previous decision (v4+), Hold or exit (v5 held), Does the entry thesis still hold (v4+ held), Position (held), Checks by code (v3+), Options offered or Stop options, Selection in this cycle (when scored).
6. Agent reasoning: conflicts, thesis, what changed, news used, data flags.
7. Safety gates.
8. Levels, folded by default.
9. Outcome: 1h, 1 session, 5 sessions, best and worst R, first touch, with "pending" until measured.
10. Footer links: Open position, Open model call, Open strategy.

One file per version is fine, but build them from one template so the order can't drift again.

### 2.3 The design lags the spec by 20 sections (High for Settings, Medium elsewhere)

Screens drawn at PR 6–22 that the spec has changed since, and what is missing:

- **Settings** (PR 10): see 3.8. Notification table 13 of 27 events; decision prompt list `v1` only; weekly/briefing/verdict pickers fine; no entry-selection group; no v3–v5 gates; no `gui.near_stop_atr`; no execution quote ages or `api.broker_timeout_seconds`; no `inspector.slow_call_seconds`; no `universe.names`. Default model `gpt-4o-mini` while the spec says live is `gpt-4.1-mini`.
- **LLM inspector** (PR 18): see 3.6.
- **Insights** (PR 22): no "What each filter removed" (v5, line 1808); findings sample unchanged.
- **Positions** (PR 35): no pending-exit state ("exit held back since 15:46 · quantity not confirmed · retrying", line 158); no "Alpaca not answering · as of 18:46" stale state (line 135); order history unchanged (audit item 10).
- **Main** (PR 35): watchlist fixed mode only; no v2+ latest-decision card; no newer feed reasons; no new Needs you items; no "EUR unavailable" (line 129).
- **States** (PR 24): no banner for live decisions failing (line 268), trade-updates stream degraded (line 457), keys imported from `.env` (line 423), missing or failing key (line 422), another-instance exists; no card state for a broker read timeout.
- **Universe** (PR 11): no company `name` column or override, although v4 reads names from the pool (line 1773).

### 2.4 Needs you items and banners added after PR 35 (Medium)

Spec table (lines 352–360) has 7 kinds; `Main.dc.html` shows kinds 4, 5 and 6. Added since in other sections:
- held-back exit after 3 tries or at the session end, critical (line 161);
- earnings exit pending at the close, critical (line 162);
- a held decision in the last cycle with `confidence_outlier` and r_now < 0: "KO held overnight at −0.62R with confidence 0.65 against neutral 0.12" (line 1853);
- live decisions failing, stream degraded, key failing (kind 2);
- settings or prompts waiting for a restart (kind 7);
- "Ask about this" on each item (line 1890).

Proposal: one `NeedsYou` reference sheet in `States.dc.html` with one row per kind, its wording, button label and where it goes, plus the empty card ("Nothing needs you" + health line). The dashboard sample keeps 3 rows but picks the three most instructive (a critical one, a held-back exit, a verdict).

### 2.5 Decision v5 has no GUI text, and the design doesn't match its own change log (Medium)

The v5 section (lines 1784–1870) has no **GUI** paragraph; every other decision section has one. `design/CHANGES.md` (3 Nov) says "v5 candidates reuse `DecisionV4.dc.html`, with per-option `min_confidence` and `target_atr` in the options table", but `DecisionV4.dc.html` contains neither, nor `base_rate`.

Proposal: add a GUI paragraph to the v5 section (candidate options table with `target_atr`, `min_confidence` and `base_rate · n`; the Hold or exit block; the exit-consistency warning; the overnight Needs you item; Insights "What each filter removed" with per-filter count, target-first, stop-first and R), then design `DecisionV5.dc.html` (candidate) and the Insights block, and correct the CHANGES entry.

### 2.6 Two rules the design doesn't follow, so Devin can't either (Low, but decide)

- **EUR beside every money figure** (spec line 326, DESIGN.md Rules). The design shows EUR only in the header, the Positions header line and the account card. Position P&L, risk budget, order history, Performance tables and every drawer show USD only. Recommendation: amend the spec to "EUR beside equity, today's P&L and card totals; USD alone in rows and tables", which is what the design does and reads better.
- **New York time in a tooltip on every time shown** (DESIGN.md interaction rules). `Main.dc.html` has it on one element. Recommendation: keep the rule, and model it in the design on feed headers, the Last action column and the inspector list, so it is visibly part of the handoff.

### 2.7 Widths (Medium)

- Missing `-390.png`: Positions, Reviews, AskDrawer, every decision drawer. DESIGN.md says every screen has one and CHANGES (1 Nov) says no app screen scrolls sideways; neither can be checked for these.
- Nothing is designed between 390 and 1440. The app's breakpoints are 820 (top bar) and 1280 (Ask docks). A 1,280 px laptop with Ask open leaves 840 px for the page; a 1,024 px tablet gets the sidebar and a 790 px page. Proposal: one 1024 px screenshot for Main, Positions and Settings, and a rule in DESIGN.md for how the two-column cards collapse.
- Phone positions row (`Main-390.png`): "JPM 8 sh · / 0.64% · 1.1 / ATR to stop" wraps mid-phrase; keep the stop distance as one no-wrap chip.

### 2.8 Hygiene (Low)

- `gpt-4o-mini` appears in Backtest, Decision, DecisionV2, Inspector (20×), Main, Reviews, Settings, Strategies. Live is `gpt-4.1-mini`; reviews and Ask are `claude-sonnet-5-5`.
- Sidebar mark says "Paper · Alpaca · v0.1"; the app says v0.2.
- Design notes rendered as product copy: the footers in Open positions ("Bar: stop (red) to target (green)…"), Today's decisions ("Changes … always show…"), the briefing card, the Options and Levels tables and the Reviews sidebar. DESIGN.md says "copy text as written", so Devin builds them. Mark them with one shared "design note" style that the handoff says to leave out, or move the useful ones to an info tooltip.
- The 10:46 finding ("Entries at the 10:46 cycle lose money: −0.18R over 31 trades") in Insights and in the Ask notes panel is sample data that was once read as a real result (spec correction of 4 Nov). Replace it with a finding that can't be confused with the run, or prefix every sample finding with "Sample ·".
- Sample data is not consistent across screens as DESIGN.md claims: the dashboard watchlist is a fixed list of 10, Universe shows a screened pool of 72 with 69 eligible.
- `Reviews.dc.html` writes "-1.0R" with a hyphen-minus in the Trades closed list; the rule is U+2212.
- `Decision.png` is 1440 × 1870 with the drawer in the left 560 px and the rest black; the other drawers are cropped to 560.
- DESIGN.md: "The 7 screens as HTML" (there are 24); the screenshot sentence predates the `-390` set; the shared-components header entry predates Ask, Needs you and Pause; the "Built in" column uses PR numbers where the register now uses `NNN`.

---

## 3. Per screen

For each: spec mismatches, UX, proposed change. Line numbers are `docs/SPEC.md`.

### 3.1 Dashboard (`Main.dc.html`)

Spec mismatches:
- Watchlist in fixed mode only (line 389: screened mode has a rank column, "sorted by rank, 15 rows, then Show all"). Audit item 7.
- Latest-decision card has no v2+ state (line 1044: "D · stop below L8 · target 2R" instead of free prices) and no `no_options · room` state (line 1597).
- Decisions feed shows `pre-filter` only; no `flip_flop`, `checks`, `not_selected`, `no_options`, `call cap`, `price_stale` rows (lines 1627, 1770, 146).
- Needs you: 3 of 7 kinds; see 2.4.
- No "EUR unavailable" state (line 129); no "Alpaca not answering · as of 18:46" on the risk budget and positions cards (line 135).
- Model-call tile hover names only the review cap; Ask now has its own $15 cap (line 2007). Add it to the hover, or add Ask cost to the tile's second line.

UX:
- "Cycle 4 of 6 today" sits under the countdown for the *next* cycle while the strip label says "18:46 cycle" (the one just done). Write "Next · cycle 4 of 6" to remove the ambiguity.
- The briefing card repeats the cards around it (audit item 6): 110 words plus five chips, then a 40-word footer explaining briefings. Proposal: text + "Numbers checked ✓ · next Fri 15:00"; chips and the explanation behind a disclosure.
- The Open positions card footer and the feed footer are design notes (2.8).
- Phone: see 2.1 and 2.7.

### 3.2 Decision drawers (seven files)

See 2.2 and 2.5. In addition:
- `DecisionV2.dc.html`: the Selection block says "Code picks the allowed set…" in a 60-word footer; the v3 drawer that should also carry the block (line 1625) doesn't.
- `DecisionV3Held.dc.html` and `DecisionV4Held.dc.html` order the held blocks differently from `DecisionV5Held.dc.html` (thesis → position vs hold-or-exit → position → thesis).
- `DecisionV5Held.dc.html` is the clearest of the seven; use its Hold or exit block as the pattern for a "What happened" block.

### 3.3 Positions & orders (`Positions.dc.html`)

Spec mismatches:
- No pending-exit state on a position (line 158–162) and no critical legs line for it; the legs line only shows the healthy case. Add the amber open state ("3 legs · stop missing · protective stop placed 18:46:04") the spec describes (line 378).
- No "Alpaca not answering · as of" state for the account card (line 135).
- No `Positions-390.png`.

UX:
- Order history: rejected orders red, "not filled" neutral; "problems only" filter; a period filter beyond "last 8" (audit item 10).
- "Not to be bought again today" shows "—"; a sample with a symbol would show the format.
- The totals line is good; add "limited by …" when open risk, not slots, is the binding limit, as the risk card does.

### 3.4 Performance (`Performance.dc.html`)

Spec: matches lines 335 and 823. UX (audit item 9): the 4 + 2 tile row leaves a gap; equity chart ticks are odd values ($10,181, $10,109); round them. Low.

### 3.5 Insights (`Insights.dc.html`)

Spec mismatches: no "What each filter removed" (line 1808). Section order matches line 1138. UX: the Findings sample (2.8). Add the v5 block between "Where trades come from" and "Skipped setups": one row per filter (each rules check, room, trend gate, ev gate, spread, slots) with count, target-first %, stop-first %, R they would have made, and the same small-sample status as the slices.

### 3.6 LLM inspector (`Inspector.dc.html`)

Spec mismatches:
- Source: Live decisions, Shadows, Reviews; spec adds `ask` (lines 736, 562) and backtests (line 337). The spec's own filter table (line 756) also lacks Ask: fix both.
- Prompt list: `decision.v1, briefing.v1, weekly.v1, verdict.v1`; missing `decision.v2–v5`, `briefing.v2`, `weekly.v2`, `verdict.v2`, `ask.v1`.
- Reason list: the v1 set; missing `unsupported_claim`, `edge_too_small`, `data_flag`, `flip_flop`, `confidence_outlier`, `unresolved_conflict`, `best_option_missing`, `best_option_mismatch`, `hold_below_breakeven`, `not_selected`, `no_options`, `price_stale`, `price_suspect`. The spec's reasons table (line 731) names only `unsupported_claim` of these; the rest are defined in their own sections. Add them to the table.
- Details: "User · input snapshot" with "Stored input" one click away (line 1775) is not shown.
- Model list: `gpt-4o-mini` first.

UX: the inspector is the best-structured screen; no change beyond the lists. Add a chip "ask" in the call list so Ask calls read differently from decisions.

### 3.7 Reviews (`Reviews.dc.html`)

Spec: matches lines 594–607 (call, code change with reason, outcome, draft label, thesis check). UX:
- Each verdict shows Good call / Bad call / Unclear with the draft outlined. It is not clear whether clicking the outlined one confirms or whether it is already confirmed. Proposal: one primary "Confirm draft" button, a "Change…" that reveals the two other labels, and "Confirm all n drafts" at the top; the dashboard "Confirm" button then does the same thing.
- No link from a verdict to its decision drawer; the inspector and Positions both link, Reviews should.
- Weekly list: "Week 42 · due Sat 17 Oct" is good; show the verdict count per week in the list.
- "-1.0R" hyphen-minus (2.8); no `Reviews-390.png`.

### 3.8 Settings (`Settings.dc.html`)

Spec mismatches:
- Notifications: 13 rows of 27 (lines 434–460). Missing: readiness changed, another instance, new sign-in, failed sign-ins, backup/disk 80%/certificate/clock, disk 90%, restarted and resumed, restarted paused, stream degraded, new error, clock check failing, live decisions failing.
- Decisions: prompt list `decision.v1 · 3f9a1c2e` only; the picker should list every decision prompt (line 1465). Default model `gpt-4o-mini`; live is `gpt-4.1-mini` (line 1766).
- Section names: the spec says "Settings → Decisions" and "Settings → Reviews" (lines 1418, 1465, 1466); the design has one section "Model and reviews" with Decisions, Reviews and Ask the agent as groups, and the section list on the left doesn't show Ask. Pick one naming and use it in both.
- Settings the spec defines but the screen doesn't show: `selection.mode`, `confidence_weight`, `last_slot_min_ev_r` (line 559, "as settings and strategy overrides"); `decision.v3.min_room_atr`, `min_edge`; `decision.v4.near_level_atr`; `decision.entry_gate`, `min_ev_r`, `decision.v5.max_rr`, `trend_gate`, `base_rate_sessions`, `price_vs_signal_adverse_atr`; `gui.near_stop_atr`; `execution.quote_max_age_seconds`, `quote_max_deviation_pct`, `tighten_quote_max_age_seconds`, `fill_settle_seconds`; `api.broker_timeout_seconds`; `inspector.slow_call_seconds`; `universe.names`. The spec never says which config keys are GUI settings (spec gap). Proposal: an "Entry selection" group under Entry rules and a "Decision gates (v3–v5)" group under Model, both with the same one-row format; the rest stay config-only and the spec says so.
- `Settings-390.png` is 19,483 px tall.

UX (audit item 8): 11,193 px on one page. Proposal: the section list sticky with its change counts; a "Changed from default" filter next to "Find a setting"; collapse Notifications' event table behind its summary ("browser 9 · email 12"); History and Recently sent paginated to 10.

### 3.9 Universe (`Universe.dc.html`)

Spec: matches line 633. Add the company `name` (line 1773) as a muted second line under the symbol with an edit affordance for `universe.names`. The sample conflicts with the dashboard's fixed list (2.8).

### 3.10 Strategies (`Strategies.dc.html`) and Backtest (`Backtest.dc.html`)

Spec: match lines 651 and 671. Strategies' comparison table has no column for the entry gate or selection mode, which is what shadows F, G and H differ in (line 1860); add a "Differs from live" line under each shadow name. Model names stale.

### 3.11 Readiness (`Readiness.dc.html`)

Matches line 717. Nothing to change; the clearest screen in the set.

### 3.12 Logs (`Logs.dc.html`)

Matches line 343. Low: the Review dialog lives in States; link to it from the recurring-problems card note.

### 3.13 Sign in (`Login.dc.html`) and Error pages (`Errors.dc.html`)

Match lines 345 and 1331 ("texts are final"). No change.

### 3.14 States and dialogs (`States.dc.html`)

Missing banners and states listed in 2.3 and 2.4. The sheet itself is fine as a reference; add the Needs you reference rows and the broker-timeout card state. It scrolls sideways at 390 (audit item 11), acceptable for a reference sheet.

### 3.15 Ask the agent (`AskDrawer.dc.html`, `AskStates.dc.html`)

Spec: matches lines 1879–2050 except the entry point (header button vs the app's sidebar button) and the missing "Ask about this" links elsewhere. The AskStates intro sentence names "the header's Ask button". UX: the steps, references and footer are clear; keep. Low: the "Switch to this page" offer on the context chip (line 1903) isn't drawn; add it to the Problems panel.

---

## 4. Spec changes this review asks for

None is a behaviour change; all are GUI text.

1. Line 328: Ask moves from the header to the sidebar and the phone top bar (follow the app, or revert the app; see question 1).
2. Decision v5: add a **GUI** paragraph (2.5).
3. Line 756: Source filter adds Ask (and Backtests from v0.2); line 731: list every warning reason.
4. Line 326: scope of the EUR rule (2.6).
5. Settings: name the sections once (Decisions / Reviews / Ask the agent as groups of "Model", or three sections) and say which config keys are GUI settings (3.8).
6. Decision detail (line 333): add "What happened" and the verdict block to the sentence so it covers v2–v5 explicitly.
7. `docs/README.md` or DESIGN.md: a design PR re-renders every screenshot the shared shell touches, at 1440 and 390, and adds 1024 for Main, Positions and Settings.

---

## 5. Proposed work, in order

Each is a work item with a kickoff; sizes are design effort.

| Item | What | Size |
| --- | --- | --- |
| 045 · design · Shell and header | One header layout; Ask in the sidebar; phone header; NY-time tooltips modelled; re-render every screen at 1440 and 390, add 1024 for three; DESIGN.md and CHANGES.md fixes; `gpt-4.1-mini`, v0.2, minus signs, design-note style | 1 day |
| 046 · design · Decision drawers | One section order; What happened block; Outcome, Verdict, order status, Ask about this on every drawer; `DecisionV5.dc.html` candidate; drawer at 390; v5 GUI paragraph in the spec | 1 day |
| 047 · design · Settings and inspector catch-up | 27 notification rows; prompt and reason lists; Entry selection and Decision gates groups; sticky section list and Changed filter; Ask source and Stored input in the inspector | 1 day |
| 048 · design · Needs you, positions and feed | Needs you reference rows and new items; pending-exit and stale states; screened watchlist; v2+ latest-decision card; feed reasons; order-history filter; Reviews confirm flow; Insights filter block | 1 day |

Each design item is followed by a build item for Devin. 045 first: it touches every screenshot, so the others render on top of it.

---

## 6. Questions for Marc

1. **Ask entry point.** Follow the app (sidebar button + phone bar + Ctrl/⌘+J) and change spec and design, or put the header button back? Recommendation: sidebar; it is what fixes the header.
2. **EUR rule.** Keep "beside every money figure" or narrow it to equity, today's P&L and card totals? Recommendation: narrow it.
3. **Hidden settings.** Show the decision gates and entry-selection settings in Settings, or declare them config-only in the spec? Recommendation: show them in two groups; they are what the shadows test, and Settings history is where a change should be visible.
4. **Verdicts.** One "Confirm draft" button plus "Confirm all", as proposed, or keep the three equal buttons?
5. **Order.** 045 → 046 → 047 → 048 as above, or drawers first?
6. **Where this report lives.** `docs/analysis/2026-11-05-design-review.md` in the 045 spec PR, as the 1 November audit was, unless you want it elsewhere.

---

## 7. What 045 changed

Done in the design PR of 5 November (`design/CHANGES.md`, entry [045]): 2.1 header and shell (Ask in the sidebar, following the app), 2.2 drawers (one order, What happened, Outcome and verdict on every drawer, the v5 candidate drawer, drawers at 390), 2.3 and 3.8 Settings (27 notification events, prompt lists, Entry selection, Decision gates, Display and Prices and timeouts groups, Changed filter), 2.4 Needs you reference and the new banners in States, 2.5 (design side), 2.7 (missing 390 and 1024 renders), 2.8 (model names, v0.2, the 10:46 sample, minus sign, DESIGN.md), 3.1 dashboard (screened watchlist, feed, briefing card, Ask links), 3.3 Positions (order history), 3.5 Insights (What each filter removed), 3.6 inspector, 3.7 Reviews (confirm flow), 3.9 Universe (names).

Left to Marc, as spec text (section 4): the Ask button's place, the EUR rule, the v5 GUI paragraph, the inspector filter and reasons tables, the Settings section names, the "What happened" sentence in Decision detail, and the screenshot rule. None changes behaviour.

Left for later items: 3.4 Performance tiles and axis ticks; 3.10 Strategies rows for shadows F, G and H; the Logs link to the Review dialog; a tablet layout rule in DESIGN.md beyond the three 1024 renders; the design-note style (DESIGN.md now states the rule; the notes themselves are unchanged).
