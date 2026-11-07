# 045 · feat · GUI catch-up

Kickoff for Devin. Start the session with: "Build docs/kickoffs/045-feat-gui-catch-up.md." Tracked in the GitHub issue "[045] feat: GUI catch-up" (the issue is the checklist; this file is the detail).

```text
[045] GUI catch-up. A GUI-only item: the design was reviewed against the
spec on 5 November and caught up with ~20 spec sections written since
PR 10–22. No trading, gate, prompt or schema change. Start from the latest
main. Read, in this order:
1. design/CHANGES.md, entry 2026-11-05 "[045] GUI catch-up" (what changed
   and why), then docs/analysis/045-gui-catch-up.md (the review).
2. design/DESIGN.md: Shared components 2 and 2a, Rules (Design notes,
   Decision drawers), Phone width, Screenshot check.
3. docs/SPEC.md: GUI (Header, the table, Needs you, Positions at a glance,
   Daily briefing card, Model call counter, Dashboard at scale, Settings
   screen); Decision v2–v5 GUI paragraphs; Entry selection GUI; LLM call
   history (Filters); Reviews (Verdicts); Insights GUI; Notifications
   table; Ask the agent GUI.
4. The screens you touch, with their PNGs at 1440, 390 and (Main,
   Positions, Settings) 1024.

Rules that hold for every step: numbers arrive as numbers and are formatted
in the browser; every time has the New York time in a tooltip; copy that
names a setting reads it from settings; every data card keeps its loading,
empty and error states; no screen scrolls sideways at 390 px.

Build in this order, one commit per step, each step shippable on its own:

1. Shell. One Header component with two fixed rows (title · PAPER ·
   subtitle / session pill · Needs you chip · equity · today · regime ·
   Pause · Kill switch); remove any remaining header Ask code; keep the
   sidebar Ask button (already on main) and the phone bar's Ask icon. Below
   820 px: hide the title row, one mono stats line (EUR in the tooltip),
   Pause and Kill switch as two half-width buttons. Between 821 and 1180 px
   the right-hand group wraps to a third row. Sidebar mark "v0.2".
   Done when: the header is 110 px tall at 1440 on every screen, about 150
   px at 390, and no header element wraps mid-text at any of 390, 1024,
   1440.

2. Decision drawer, one component tree for every version. Order: action ·
   tiles · What happened · version blocks · Agent reasoning · Safety gates
   · Levels (folded, <details>) · Outcome · footer links. What happened
   shows: final action; every change by code with its reason code (from
   the stored decision: change_reason, gate_detail); warnings (the
   llm_calls reasons); order status (bracket sent, fill, legs); the verdict
   (pending with its date, draft with Confirm draft and Change…, confirmed,
   or "none" with why: shadows, and held hold/exit/tighten decisions, get
   none); "Ask about this decision" (opens the Ask drawer with the decision
   as context, which already exists). Outcome on every version: entries get
   1h / 1 session / 5 sessions with best, worst and first touch; a skip gets
   its option touches; a hold or exit gets the position afterwards and, in
   the last cycle, the next open against the exit. v1 drawer: gates after
   the reasoning. v3 drawer: the Selection block when the cycle was scored.
   v4 held: Position before the thesis block. The drawer shrinks to the
   viewport (max-width 560 px); tiles become two columns below 820 px.
   Done when: every stored decision (v1–v5, live and shadow, candidate and
   held) renders with all nine sections present or explicitly empty, the
   Reviews confirm flow works from the drawer, and Playwright screenshots
   match Decision.png, DecisionV2–V5Held.png at 560 and Decision-390.png,
   DecisionV5-390.png.

3. Decision v5 candidate drawer (DecisionV5.dc.html): options table with
   target ATR, min_confidence, base_rate · n (dash under 30), Model column;
   trend gate as the first gate row; ev gate row with the expected value;
   time-left row; previous decision from the same context with final action
   and change reason.

4. Dashboard. Watchlist in screened mode: Rank column (hover text from the
   design), counts over the eligible list, 15 rows sorted by rank with held
   symbols always listed, "Show all n"; fixed mode unchanged. Cycle strip
   and feed over the pool: a "NO CALL · call cap" grouped row, "+n more" on
   the pre-filter row, "Next · cycle n of 6 today". Needs you: an Ask link
   per item; "Email failing since <time>" in the health line. Model calls
   tile hover adds the Ask cap and spend. Briefing card: text, "Numbers
   checked ✓" with its hover, next briefing, and a "Facts used · model ·
   cost" disclosure that opens the chips and the model line. New York time
   tooltip on Last action.

5. Positions. Order history: "All · Problems" filter (rejected, not
   filled, replaced after an error, a fill worse than the stop), period
   filter (Today, 5, 20 sessions, All), rejected rows red, "Load 20 more".
   Position card: the pending-exit state from States.dc.html ("exit held
   back since 15:46 · 3 tries", the legs line in amber with "exit pending:
   <reason>"). Risk budget, positions and account cards: the "Alpaca not
   answering · as of <time>" state when the API answers stale: true.

6. Reviews. Each verdict: one primary "Confirm <draft>" button, "Change…"
   revealing the other two labels, "Decision →" to the drawer; "Confirm all
   n drafts" at the top of the card. Minus signs everywhere (U+2212).

7. Insights. "What each filter removed" card between the funnel and the
   slices, from the counterfactual touches of PR 37: per filter (each rules
   check, trend gate, room, ev gate, spread, slots) the count removed,
   target first %, stop first %, neither %, average R if taken, and a
   sample status (counts from 100 decided touches, early under that, too
   few under 30). Before PR 37's data exists the card shows its empty state.

8. LLM inspector. Source filter: Ask and Backtests (counts); prompt list
   from the manifest (every version, every kind); reason list: every
   warning and error reason of the spec's Outcome table, and
   changed_by_code also by its stored change reason ("changed_by_code ·
   flip_flop"), as in the design's select. no_options, call cap and other
   no-call reasons never appear: no call was sent. Ask
   calls in the list and details ("Question k of n · context <screen>",
   effort for Claude models, "Open the chat"); user message labelled "input
   snapshot as sent to the model" with a "Stored input" link to the saved
   snapshot (PR 34's fix of 3 Nov).

9. Settings. Notifications: every event of the spec table (27), the table
   folded behind its summary line with counts. New sections: Entry
   selection (selection.mode, confidence_weight, last_slot_min_ev_r),
   Decision gates (decision.v3.min_room_atr, min_edge,
   decision.v4.near_level_atr, decision.entry_gate, min_ev_r,
   decision.v5.max_rr, trend_gate, base_rate_sessions,
   price_vs_signal_adverse_atr), Display (gui.near_stop_atr,
   inspector.slow_call_seconds); Trading gains "Prices and timeouts"
   (execution.quote_max_age_seconds, quote_max_deviation_pct,
   tighten_quote_max_age_seconds, fill_settle_seconds,
   api.broker_timeout_seconds). All versioned settings with a reason, as
   every other row; the ranges from the spec. Decision prompt picker lists
   every decision prompt with its hash and marks the ones a shadow runs.
   Model pickers: the models list from config. Section list: "Changed from
   default" filter, "· Ask the agent" jump link, the new sections.
   Done when: a setting changed in Settings → Decision gates changes the
   next cycle's behaviour and shows in History with its reason, and the
   defaults in backend/config.example.yaml are unchanged.

10. Universe: company name under each symbol (the pool's name column),
    with the override hint.

11. States and banners: live decisions failing, trade-updates stream
    degraded, missing or failing key, keys imported from .env, EUR
    unavailable, as worded in States.dc.html; the Needs you kinds that are
    missing (held-back exit, earnings exit pending at the close, overnight
    confidence outlier, settings waiting to apply), as worded in the Needs
    you reference; the "Nothing needs you" form.

12. Screenshots: docs/screenshots/045/ at 1440 and 390 for every screen
    touched, plus 1024 for Main, Positions and Settings; each next to its
    design PNG in the PR description, with design differences listed.

The spec now says what the design shows (decided 7 Nov, see below): the
header without Ask, the EUR rule, the Settings sections, the Decision
detail order, the v5 GUI paragraph, the inspector's sources and reasons.

Not in this item: Strategies rows for shadows F/G/H, the Trading-days
watchlist search.
```

## Why

`docs/analysis/045-gui-catch-up.md`. Ten screens were drawn at PR 6–22 and never caught up with the spec sections written since; the header wrapped to 2–4 rows on every screen; only the v1 drawer had Outcome and Verdict blocks.

## Decided by Marc, 7 November

All five are in `docs/SPEC.md` (entry [045] of 7 Nov in `docs/CHANGES.md`); the design already matched.

1. **Ask button**: in the sidebar and the phone top bar, not the header (GUI → Header; Ask the agent → Opening it, GUI).
2. **EUR**: beside equity, today's P&L and card totals; every other amount is USD alone with EUR in its tooltip (Overview → Currency; GUI). `design/DESIGN.md` → Numbers says the same.
3. **Decision v5**: the GUI paragraph is in Decision v5 → GUI (candidate drawer columns, held Hold or exit block, the overnight Needs you item, "What each filter removed").
4. **Inspector**: Source adds Ask and Backtests (default on: live, reviews, Ask); the Outcome table lists every v2–v5 warning; the reason filter splits `changed_by_code` by change reason. The design's reason list was corrected to match (no no-call reasons).
5. **Settings**: one section "Model and reviews" with the groups Decisions, Reviews and Ask the agent, plus the new sections Entry selection, Decision gates and Display, and Trading → Prices and timeouts (GUI → Settings screen). Every reference reads "Settings → Model and reviews → …".

The Decision detail row of the GUI table now describes the drawer order with What happened, too.
