# 100 · feat · UX pass on the remaining screens

Kickoff for Devin. Start the session with: "Build docs/kickoffs/100-feat-ux-pass-remaining-screens.md."

Frontend on eight screens, plus four small backend additions (who was better in a disagreement, the backtest answer sentence, the readiness "what moves this forward" list, the attention level of a recurring log problem). No trading behaviour changes: no rule, gate, threshold, order or model call is touched. Spec: `docs/SPEC.md` → GUI → Interaction and states, Positions at a glance and Settings screen; Strategies and shadows → GUI; Backtesting → GUI; Readiness → GUI; Logging → Logs screen; Error pages → GUI (every part marked "item 100"); `docs/CHANGES.md` entry [100]. Design: `design/CHANGES.md` entry [100] lists every file and screenshot. Issue: #296.

```text
[100] UX pass on the remaining screens. Goal: Strategies, Backtest,
Readiness, Logs, Positions, Settings and the error pages read as well on
a phone as on a desktop, say what needs Marc before the detail, and never
show an empty card or an unavailable button without saying why.
Start from the latest main.

Read first:
1. AGENTS.md (Commands: CI runs the tests; How to work: a PR closes only
   issues it resolves) and docs/README.md.
2. docs/CHANGES.md [100], then the spec parts it names. Search
   docs/SPEC.md for "item 100": every rule of this item carries it.
3. design/CHANGES.md [100] and design/DESIGN.md → Rules (the four rules
   marked 100) and "How to read the screen files" (the second <style>
   block holds a screen's phone rules).
4. The screen files with their screenshots at 1440 and 390:
   Strategies, Backtest, Readiness, Logs, Positions, Settings (with
   Settings-390-save.png), Errors, and States → Empty states.
5. Issue #296: its nine points are what the last PR must prove.

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.
Three PRs, in this order. Put "Part of #296" in the first two and
"Closes #296" in the third.

=== PR 1 · Strategies and Backtest ===

1. Strategies → Compare.
   - The small-sample banner sits above the table and shows while any
     strategy has fewer than shadows.promote_min_closed_trades closed
     trades. Text as in the design, numbers from the data.
   - Columns in the design's order. The kind chip moves into the name
     cell. Closed trades shows "n · w won · l lost" and a bar "n of 20"
     (the setting, capped at 100%). Same as live shows
     same_as_live_count ("86% of 212").
   - The table header says "select a row for its details"; the name is
     a button (aria-pressed) so a row can be picked by keyboard.
2. New shadow and Promote. When slots.used == slots.max the button is
   aria-disabled with the line "All 3 slots in use · archive a shadow to
   add one" (aria-describedby). Promote below the minimum: aria-disabled
   with "Promote needs 20 closed trades · B has 4." Never `disabled`,
   never only a title.
3. Selected strategy: the label–value grid of the design (stacks below
   760 px). New row Synthetic prices "1 of 6 entries": the API adds per
   strategy `synthetic_entries` and `entries` (today there is one total
   for the run; keep it).
4. Disagreements (backend/app/shadows/compare.py → disagreements).
   - Each item gains `shadow_decision_id`, `result_text` and `better`.
   - `better` is "shadow", "live" or null, by this rule only: exactly
     one side's final action is enter. If that side's trade is closed
     with r > 0, that side is better; with r < 0, the other side; r == 0,
     open, pending or not filled: null. Every other disagreement: null.
   - `result_text` in words, from the same facts as today's `result`:
     "B's trade: −1.0R at the stop", "B's trade is open", "B's entry
     wasn't filled", "Live's trade: +0.8R at the target", "+1.2% after
     5 sessions", "Waiting for 5 sessions". Exit reasons through the
     056 label map. Keep `result` and `tone` for one release.
   - The response gains `counts`: {shadow_better, live_better,
     undecided} over every disagreement of the run, computed before the
     limit is applied.
   - GUI: the tally sentence, the filter (All, "<short> better", Live
     better, Not decided; aria-pressed; in the URL as ?better=), rows
     as the design (a dot in the strategy's series colour on the better
     chip), links "<short>'s decision" and "Live's decision" that open
     the decision drawer, "Show all n". Below 760 px a row stacks.
   - Tests: one per branch of the rule (shadow entered and won, lost,
     0R, open, not filled; live entered and won, lost; neither entered;
     both entered); counts equal the items' sum with a limit smaller
     than the total.
5. Backtest layout. Runs above New backtest in the left column. Below
   820 px the order is runs, results, form, with the "On this page"
   chips (Runs, Results, New backtest).
6. Backtest estimate ("Before you run"). Two lines by code, then the
   rows of the design:
   - budget: estimate <= left and cost at the cap <= left → green "Fits
     this month's budget: about $6.70 of the $8.54 left."; estimate <=
     left < cost at the cap → amber "May stop early: up to $9.10 at the
     call cap, $8.54 left."; estimate > left → red "Over this month's
     budget: about $9.10, $8.54 left." and Run unavailable with that
     line as its reason;
   - cutoff: green "Starts after <model>'s knowledge cutoff (<Mon
     YYYY>)." or red with the refusal reason; one line per LLM strategy
     picked.
   A pure function in the frontend with unit tests for the five cases.
7. Backtest results header.
   - Buttons "View n trades", "View decisions", "Download CSV" next to
     the title (they leave the bottom of the page).
   - Answer sentence by code, new module backend/app/backtests/
     answers.py, stored with the run's results when it finishes (older
     runs: computed once on first read and stored):
       "<First> made <ret> where buying and holding SPY made <spy>:
        <|diff|> points <less|more>, with a deepest fall of <dd>%
        against <spy dd>% and money in the market <tim>% of the time."
     then per further strategy:
       "<Name> made <ret>, <|diff|> points <less|more> than <First>."
     A difference of 0.0 reads "the same as". Signs with U+2212.
   - Under it, always: "This is evidence about the past, never a
     forecast. Read with care lists what the run had to assume."
   - Tests: one, two and three strategies; ahead of and behind SPY; an
     interrupted run (no sentence, the header says "interrupted · results
     so far").
8. Screenshots in docs/screenshots/100/: Strategies and Backtest at 1440
   and 390, Strategies with a free slot, a backtest over budget.

=== PR 2 · Readiness and Logs ===

9. Readiness wording (frontend, one map): stored `not yet` reads
   "building up" (blue) for closed_trades and sessions and "below
   target" (red) for every other criterion; `waiting for data` reads
   "needs data" (neutral) with "needs 30 closed trades · 3 so far" from
   the criterion's minimum sample and current count. History rows use
   the same words. Nothing stored changes.
10. Verdict banner: neutral while not ready, with "n of 17 criteria
    pass"; "That is expected n sessions into a run." while sessions <
    readiness.min_sessions; green when ready. Six tiles in the tile grid.
11. What moves this forward. backend/app/readiness/forward.py: a pure
    function of the latest stored snapshot (it never measures a
    criterion again), added to the readiness payload as `forward`:
    {act: [...], time: [...], manual_open: n}.
    - act: every criterion shown as below target, in the order of the
      table below, each {key, name, value_text, sentence, button,
      href}. The sentence fills its numbers from the snapshot row; a
      clause whose value the snapshot doesn't hold is dropped, never
      guessed.
    - time: the two counts with their estimate, then the needs-data
      criteria grouped by what they need ("Average R, Profit factor and
      Beats rules-only need 30 closed trades (3 so far)").
    - Empty act: "Nothing to act on now."

    Criterion             | Sentence                                 | Button
    Errors in the log     | Review the n error problems on Logs      | Review on Logs
                          | (<first two names>). Marked expected or  |
                          | fixed, this passes at the next check.    |
    Reconciliation        | n mismatches with Alpaca are not         | Open Positions
                          | acknowledged. Check the position at      |
                          | Alpaca, then acknowledge.                |
    Unresolved errors     | n errors that touched a decision, order  | Open Needs you
                          | or fill are open. Each ends when its     |
                          | state clears, or dismiss it with a note. |
    Critical lines        | n critical lines, the last on <date>.    | Open Logs
                          | They can't be reviewed away: this passes |
                          | once <window> sessions pass without one. |
    Positions without     | A position had no stop at Alpaca n       | Open Logs for
    a stop                | times, the last on <date>. This passes   | <date>
                          | when that leaves the <window>-session    |
                          | window, after <date2>.                   |
    Kill switch events    | The kill switch turned on n times, the   | Open Logs for
                          | last on <date>. This passes after        | <date>
                          | <date2>.                                 |
    Entry proposals       | f of n proposals were turned down by a   | Open Insights
                          | code check. Insights shows which check   |
                          | turns them down.                         |
    Confidence            | Stated confidence is g points from the   | Open Insights
    calibration           | actual win rate. Insights → Calibration  |
                          | shows in which group.                    |
    Cycles completed      | m cycles were missed on <dates>          | Open Logs for
                          | (<reason>). They leave the <window>-     | <date>
                          | session window after <date2>; until then |
                          | keep the app running through every       |
                          | session.                                 |
    Price data quality    | p% of candidate checks were blocked by a | Open Logs
                          | stale or suspect price. Logs shows which |
                          | symbols.                                 |
    Return vs SPY         | The run is x points behind SPY buy and   | Open Performance
                          | hold. Performance shows where.           |
    Max drawdown          | The deepest fall was x%, over the y%     | Open Performance
                          | target. It stays in this run's record;   |
                          | only a new run resets it.                |
    Average R             | Trades earn x R on average, under y R.   | Open Insights
                          | Insights shows which conditions lose.    |
    Profit factor         | Winners earned x times what losers lost, | Open Insights
                          | under y.                                 |
    Beats rules-only      | Live's average R (a) is not above the    | Open Strategies
                          | rules-only shadow's (b).                 |

    <date2> is the first session after which the event has left the
    window, from the trading calendar. Tests: one fixture snapshot per
    row, the grouping of needs-data, an empty act list, and a snapshot
    without the dates (the clause is dropped).
12. Logs → Recurring problems.
    - Config: add under `gui:` in backend/config.example.yaml
        log_watch_min_count: 5      # item 100: a recurring warning counted this often in the Logs period shows as "watch"
      with validation (integer 1–1000), the README config table (the
      README test checks every key) and Settings → Display, the row of
      the design ("Recurring warning counts as “watch” from"), next to
      gui.near_stop_atr. `gui` is outside DIRECTION_SECTIONS
      (settings_service.py), so it needs no direction entry; add its
      label next to ("gui", "near_stop_atr").
    - Backend: every recurring problem gains `attention`: "act" for a
      CRITICAL problem and for an ERROR problem that
      unreviewed_error_fingerprints counts as unreviewed (one rule for
      Needs you, Readiness and this list; don't write a second one);
      "watch" for a WARNING problem with count >= the setting in the
      chosen period; else "none". The response gains the three counts.
      Sorted by attention (act, watch, none), then count, then last
      seen.
    - GUI: the column "Does it need you?" with the chip and "n times in
      7 days" (the period's own words; "· not reviewed" for an
      unreviewed error), the three counts above the table, the level
      chip in the Problem cell. Below 760 px each problem is a stacked
      row: problem, then level and review.
    - Tests: the three levels, a reviewed-expected error (none), a
      fixed error that came back (act), the sort order, the setting at
      its bounds.
13. Logs → Log. Below 760 px a line is two rows: time and level, then
    the message at full width with its chips. The opened line's
    surrounding-lines table scrolls inside its box.
14. Screenshots in docs/screenshots/100/: Readiness and Logs at 1440 and
    390; Readiness with nothing below target; Logs with no problem that
    needs Marc.

=== PR 3 · Positions, Settings, error pages, empty states ===

15. Positions. Three links per position: Chart (/?symbol=<symbol>), Ask
    about this position (opens Ask with the position as its page
    context), Latest decision. Today's card ends in the one line of the
    design; the day-trade note is the hover text of "Bought and sold the
    same session". Order history: columns Order (symbol, side · leg,
    time under it), Status, Qty, Price, Fill, Slippage, Type; newest
    first by submit time; a long status wraps; at 390 px Order and
    Status fit without scrolling. The same table renders next to a
    docked Ask drawer (AskDrawer.png).
16. Settings. Below 760 px the unsaved-changes bar is the one-line
    <details>-style bar of Settings-390.png; open it is
    Settings-390-save.png. Same validation, same reason rule, same
    "looser" confirmation as the desktop bar: one component, two
    layouts. Focus moves into the opened bar and Escape closes it.
17. Error pages. Below 820 px the in-app error pages render inside the
    phone shell (top bar, no sidebar), text at full width
    (Errors-390.png). No text changes.
18. Empty states. One module (frontend/src/emptyStates.ts): for every
    row of States → Empty states, a function that returns {title, line,
    action} from the values the screen already loads. Replace the
    one-line empty texts in Dashboard, Positions, Performance, Reviews,
    Insights, Strategies, Backtest, Inspector, Logs and Readiness with
    it. A line whose value isn't loaded on that screen is left out. A
    card not in the table keeps its text if it already says why and what
    comes next; otherwise give it the pattern and list it in the PR.
    Tests: one per row; a Vitest that fails when a page renders a card
    whose whole text matches /^No [^.]+\.$/.
19. Unavailable controls. Go through every aria-disabled or disabled
    button in frontend/src (list them in the PR). Each one shows its
    reason as text beside it, or is removed when it can never be used
    there. Exceptions stay as they are: buttons while an action runs
    (design/DESIGN.md → Loading) and the kill switch.
20. Screenshots in docs/screenshots/100/: Positions at 1440, 1024 and
    390; Settings at 390 closed and open; one error page at 390; three
    empty cards (Strategies without shadows, order problems, Reviews
    weekly).

Rules for all three PRs: no network in tests; no datetime.now() in
backend code (the clock); numbers arrive as numbers and are formatted in
the browser, except the stored sentences of steps 7 and 11, which are
text by design; copy wording from the design files as written; nothing
written into design/. Tests are proven in CI: open each PR as a draft
early, run locally only the test files you change, never the full
suites or the e2e suite.

Done when, per PR: all CI checks are green and the PR merges cleanly;
each touched screen matches its design file at 1440 and 390 with no
sideways scroll; differences are listed under "Design differences".
Done when, item: the third PR lists the nine points of #296 under
"Issues closed" with the file or test that proves each.
```

## Why

Marc, 14 November: "Do a pass on the design and see where we can improve the UX." The pass of 11 November (items 073–079) left six screens untouched. Reading them at 1440 px and 390 px found:

- **Strategies** at 390 px showed a strategy's name and its chip, but no number: the return was scrolled out of view. Disagreement rows squeezed both actions into a 40 px column, and were out of date order.
- **Backtest** put a 1,000 px form above the runs list, so a running backtest was below the first screen; on a phone the results started about 1,750 px down. "Open trades (802)" read as trades that are still open.
- **Logs** at 390 px gave a log message an 80 px column: one or two words per line. Eight recurring problems were listed without saying which two matter.
- **Readiness** showed a red "Not ready" banner and red "not yet" chips for things only time fixes, 9 sessions into a run, and didn't say which three criteria Marc could move today.
- **Positions**: the order history's status, the column it is read for, was the last of ten.
- **Settings** at 390 px: the unsaved-changes bar covered a quarter of the screen.
- **Error pages** at 390 px: the sidebar squeezed the text into 145 px.
- **Empty cards** said "No open positions." or "No decisions." and nothing else; the design rule (say why and what comes next) had no texts to copy.

## Assumptions

- "Who was better" is only judged where one side entered and the other didn't. Hold against exit, or two entries with different options, stay "not decided": the stored facts don't say which was better.
- The readiness sentences use fields the daily snapshot already stores (value, target, sub text, estimate). Where a date isn't stored, the clause is dropped. If most rows need a field the snapshot lacks, say so in the PR before adding columns.
- `gui.log_watch_min_count` (5) is a display threshold. It changes no notification and no readiness criterion.
- The example config and the README table get the new key in PR 2, not in the spec PR: the README test requires every example key to be in the README, which Devin owns.
- Items 098 (#294) and 099 (#295) touch Settings and the dashboard. This item touches neither the last-cycle setting nor the dashboard's layout; merge `main` in before each PR.

## Open questions for Marc

None blocking. Decided without asking:

- The Readiness banner is no longer red while the run is simply young. Red stays for criteria that are below target.
- Five as the count from which a recurring warning shows "watch".
- Not in this item: Login, the Ask drawer's own states, the loading patterns and the decision drawers. I found nothing there that justified a change; there is also no design yet for a decision.v6 or v7 drawer, which follow the shared drawer order.
