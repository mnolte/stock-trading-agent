# 056 · feat · UX pass

Kickoff for Devin. Start the session with: "Build docs/kickoffs/056-feat-ux-pass.md."

Frontend, plus one small backend change (step 2: error summaries). No trading, risk or data change. The findings are in `docs/analysis/056-ux-review.md`; the rules in `design/DESIGN.md` (Rules, Interaction rules); the design changes in `design/CHANGES.md` entry [056]; the spec text in `docs/CHANGES.md` entry [056].

```text
[056] UX pass. Goal: the app as reviewed on 9 Nov (docs/analysis/056-ux-review.md)
with its ten findings fixed: no screen goes down for one bad record, errors
read as one line outside Logs, Settings shows one section at a time, nothing
scrolls sideways at 390 px, and dates and codes read the same everywhere.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/analysis/056-ux-review.md (findings 1–10, with file and line).
3. design/DESIGN.md → Rules (Dates, Words not codes, Tile rows) and
   Interaction rules (Toasts, Error text, Failed to draw, Card height);
   design/CHANGES.md [056]; docs/CHANGES.md [056] and the SPEC.md lines it
   names (Settings screen, Frontend errors, Decision detail, Backtest GUI,
   the GUI dates sentence).
4. design/screens/Settings.dc.html (open it and click the section list),
   Backtest.dc.html, Performance.dc.html, Insights.dc.html and
   States.dc.html → Card states, with their PNGs.

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.

One PR per step, titled "[056] fix: <area>", or steps 6–8 together if small.
Each step starts with a test that fails before the fix.

1. Card error boundaries (finding 1). A CardBoundary around every card
   (components/Card.tsx is the natural place): on a render error it shows the
   Failed to draw state from States.dc.html, sends the error once per card
   and page load (key: card + message fingerprint), and offers Reload card
   (remounts the card) and Open in Logs. Keep the screen-level boundary for
   crashes outside cards. Then make Reviews' WeeklyView accept facts.trades
   as a count or a list (the readme seed stores 12) and fix the seed to the
   real shape. Test: a card that throws leaves its siblings rendered, and
   three re-renders send one POST /api/logs/client.

2. Error text (finding 2). Backend: each ERROR fingerprint group gets a
   `summary`: the first line of its message with URLs and file paths
   removed, cut to 120 characters, stored with the group; the Needs you API
   and the "New error" notification use it with the count ("Reviews screen
   failed to draw ×5 · last 07:30"). Frontend: Needs you, toasts and banners
   show only the summary; the stack trace only in Logs on the opened line.
   Toasts sit below the header and never cover Pause, Resume or the kill
   switch (DESIGN.md → Toasts). Tests: a summary strips
   "http://127.0.0.1:5400/…" and "(file.tsx:115:37)"; at 1440 and 390 the
   kill switch is clickable while 3 toasts show (Playwright elementFromPoint
   at the button centre).

3. Settings, one section at a time (finding 3). Routes /settings/<section>
   (keys, trading, risk, rules, selection, gates, earnings, model,
   display, notifications, readiness, schedule, history, access, server,
   paper-account); /settings opens the last one used (localStorage, wrapped
   in try/catch), else keys. The section list marks the current section; on
   a phone a "Section" select with "· n changed" replaces the scrolling row.
   Previous/next links end each section. "Find a setting" and "Changed from
   default" list matching rows from every section, grouped under section
   titles, each row editable in place. Unsaved changes survive switching
   sections; the save bar lists all of them and leaving the screen with
   unsaved changes asks first. Fix "Replace" on a key that isn't set (it is
   "Add key") and the hint "Current: v—" (say "no saved version yet").
   Tests: deep link /settings/risk shows only Risk limits; a change in Risk,
   a switch to Trading and Save saves the Risk change.

4. Phone width (finding 4). Dashboard Needs you (fixed by step 2), Insights
   Slices table, Backtest By market regime, Logs Recurring problems and the
   watchlist's Day column: wrap the tables in the card's scroll container as
   the designs do. Add a Playwright test that opens every route at 390x844
   on the seeded app and asserts document scrollWidth == 390.

5. Words and dates (findings 5 and 6). One label map (a new src/labels.ts) for
   exit reasons, actions, call sources and kinds, and change and warning
   reason codes; use it everywhere a code is shown ("Agent exit", "Earnings
   exit"; the inspector row shows the kind once). One date formatter
   (src/format.ts): "14 Sep", "14 Sep 2025", ranges "14–25 Sep 2026" and
   "28 Sep – 2 Oct", fixed English month names whatever the browser locale;
   ISO only in Logs, exports and URLs. Replace the Backtest date inputs with
   text fields in that format plus a calendar button, defaulting from the
   agent's clock (/api/header → now, simulated in simulated mode). Tests:
   formatter output under locales en-US, nl-NL and en-GB is identical; no
   rendered text on Performance, Insights, Backtest or Inspector matches
   /\b[a-z]+_[a-z_]+\b/ outside code elements (seeded e2e).

6. Backtest results and tile rows (findings 7 and 8). Backtest: the results
   tiles become the table in Backtest.dc.html (Metric · one column per
   strategy with its colour dot and name · SPY buy and hold). Run backtest
   says what is missing while disabled ("Pick a start date"). "Read with
   care" lists each assumption once; a zero shows as 0.0%, never −0.0%.
   Performance and Insights summary tiles: the grid from tokens
   (.sa-tiles, .sa-tiles-6: 6/3/2 per row); values wrap, never clip.

7. Small fixes (finding 9). Insights header title (it says Dashboard).
   Insights Findings button styled as the design's ghost button; the
   Slices first-row label. Cycle strip separator ("$0.000 · next cycle").
   Dashboard card heights: rows stretch only from 821 px; below that cards
   are as tall as their content. LLM inspector at 1181 px and up opens the
   newest call in Call details.

8. Repetition and reasons (finding 10). Universe in fixed-list mode: the
   mode banner only; drop the note under the list and the preview bullet in
   How the screen works. Decision drawer: when no order was sent, the Order
   row and the footer say why (paused, waiting for approval, kill switch, a
   gate, shadow, outside the entry window), from data the decision already
   stores; if a reason isn't stored, list it in the PR instead of guessing.

9. Screenshots in docs/screenshots/pr-<n>/ next to the design PNGs: Settings
   (1440, 1024, 390), Backtest, Performance, Insights (1440 and 390), a card
   in Failed to draw, Needs you with an error group, the dashboard at 390
   with 3 toasts showing. Re-run the review's script (method at the end of
   the analysis): no route scrolls sideways, no console error.

Rules: no network in tests; no change to trading code; prompts untouched;
backend/config.example.yaml unchanged (nothing here is a trading
threshold: the 120-character summary and the 6/3/2 grid are display
constants in the frontend and the log module).

Done when: findings 1–10 are fixed or listed in the PR with a reason;
make test and the e2e pass, including the 390 px scrollWidth test; and the
screenshots sit next to the design PNGs in the PR.
```

## Why

The design reviews of 1 and 5 November compared designs with the spec. This is the first review of the running app since 045 and 055 were built. It found one screen down because of one record, stack traces in the dashboard's most important card, a toast over the kill switch, and a Settings page six to ten screens long.

## Assumptions

- The error `summary` is computed when a fingerprint group is created or updated; existing groups get it on their next line. No migration if the groups table already has a free text column for it; otherwise an Alembic migration adding `summary`.
- "Last section used" in Settings is a per-browser convenience (localStorage), not a setting.

## Open questions for Marc

None. Defaults chosen: Settings opens API keys and data the first time (it decides whether the agent can run at all); the date format is English abbreviations, as the rest of the app's text is English.
