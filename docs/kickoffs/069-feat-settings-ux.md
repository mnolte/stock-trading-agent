# 069 · feat · Settings UX

Kickoff for Devin. Start the session with: "Build docs/kickoffs/069-feat-settings-ux.md."

Frontend, plus one small backend helper (step 3). No setting, default, threshold or trading rule changes. Spec: `docs/SPEC.md` → GUI → **Settings screen** and `docs/CHANGES.md` entry [069]. Design: `design/screens/Settings.dc.html` (click through the sections) and `design/CHANGES.md` entry [069].

```text
[069] Settings UX. Goal: Settings reads as the design does: a grouped
section list, defaults inline, fixed values as text, dollar amounts next to
percentages, a plain summary of what the risk limits mean today, labelled
two-value fields, an Advanced fold, override notes, and a save bar that
says whether each change makes the agent tighter or looser.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md → GUI → Settings screen (the 069 paragraph and the save bar
   sentence); docs/CHANGES.md [069].
3. design/CHANGES.md [069]; design/screens/Settings.dc.html with
   Settings.png, Settings-1024.png, Settings-390.png.

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.

One PR, or two (steps 1–2, then 3–6) if it grows past ~800 lines.
pages/Settings.tsx is 3,892 lines: split the row, section list and save bar
into components/settings/ as you touch them; no behaviour change from the
split itself.

1. Section list. Five groups with headings, in this order: Trading
   (trading, risk, rules, selection, gates, earnings), Model (model, with
   Ask the agent), Alerts and display (notifications, display),
   Connections (keys, access, server), Records (readiness, schedule,
   history, paper-account). Routes unchanged. Previous/next follow the new
   order. Phone (≤ 760 px): the Section select uses <optgroup> with the same
   groups and "· n changed"; Find a setting is shown above it (today it is
   hidden on phones).

2. Rows (the row component, Settings.tsx around lines 460–490):
   - the default goes inline at the end of the explanation ("· default
     2.5%"), in the faint colour; the separate "Default …" line under the
     control goes. Changed rows keep "Changed from … · Reset" under the
     control.
   - fixed values: "Always on" (or the fixed value) as text with the lock and
     "fixed by the spec" on the same line; no disabled switch (Cash only,
     One share fits).
   - shock brake: two labelled fields, "1h bar" and "since the open", each
     with its own aria-label; same for any other pair that isn't a range.

3. Money and the Risk limits summary.
   - Backend: GET /api/settings/preview?values=… (or POST with the draft
     values) returns, for a draft, the dollar amount of every "% of equity"
     setting at current equity, and the summary numbers: loss per stopped
     trade, after the losing-streak halving, positions × risk per trade vs
     total open risk (which binds first), daily loss stop in dollars, kill
     switch equity level and the peak it is measured from. Use the same
     equity source and the same risk functions as /api/risk-budget and the
     gates; Decimal throughout; no new math.
   - Frontend: "= $40.74" beside each % of equity control, updated as the
     value is typed (debounced 300 ms; keep the last value while loading);
     the "What these limits mean today" box at the top of Risk limits with
     the three sentences from the design, computed from the draft values.
   - Tests: the preview with a draft risk per trade 0.4 at equity 10,184.30
     returns 40.74 and 20.37; 4 × 0.4 vs 2.5 says positions bind first; a
     draft of 0.7 × 4 = 2.8 > 2.5 says the open-risk budget binds first.

4. Advanced fold and override notes.
   - Trading → Prices and timeouts in a <details> "Advanced · prices and
     timeouts", closed by default, with "n settings, all at default" or "n
     changed" in its summary. Find a setting and Changed from default open it
     when a match is inside.
   - A strategy-overridable setting that an active shadow overrides shows
     "Shadow A uses 45–65" at the end of its explanation, linking to that
     strategy on Strategies. Source: the strategies' override JSON.

5. Save bar.
   - Each listed change is tagged "tighter" (green) or "looser" (amber).
     Direction is fixed per setting in one map next to settingsDiff.ts (for
     example: risk per trade down = tighter; max open positions up =
     looser; RSI range narrower = tighter; trend gate off = looser; a
     regime added to trend_gate_regimes = looser). Settings outside risk,
     brakes, entry rules and decision gates get no tag.
   - Saving with any looser change opens a confirm dialog naming them
     ("Raises risk per trade 0.5 → 0.6%. Save as v5?"), Cancel focused.
   - Tests: direction for every key in the map; a mixed save shows only the
     looser ones in the dialog; a tighter-only save has no dialog.

6. Catch-up and small fixes.
   - Decision gates: "Regimes that pass the trend gate" (Up, Sideways
     checkboxes; Down not offered) and Decision v6 → "Sideways, high
     volatility: target at most" (decision.v6.sideways_max_target_atr), if
     058 steps 5–6 didn't add them already.
   - Notifications → Email: "Test and save" primary, "Send test" ghost.

7. Screenshots in docs/screenshots/pr-<n>/ next to the design PNGs: Risk
   limits at 1440, 1024 and 390 with two unsaved changes; Trading with the
   Advanced fold open; Decision gates; the looser-save dialog; the phone
   Section select open.

Rules: no network in tests; Decimal for money; no threshold moves into the
frontend (the preview endpoint computes, the GUI formats);
backend/config.example.yaml unchanged; dates and labels through the 056
formatter and label map.

Done when: every Settings section matches the design at 1440 and 390 with
no sideways scroll; the summary and dollar amounts match the preview
endpoint; a looser change can't be saved without the confirm; make test and
the e2e pass.
```

## Why

Review of the built Settings screen (056) on 11 November, seeded app, 1440 and 390 px:

- **The section list was 16 flat items** with API keys first, while most visits are to change a trading limit.
- **Every row spent a line on "Default …"**, even when the value was the default. On Risk limits that's 11 extra lines.
- **Fixed values looked editable:** a yellow switch for Cash only that can't be switched.
- **Percentages had no dollar meaning.** "0.5 % of equity" is $50.92 today; nothing said so. Nothing said how the limits combine either: 4 positions × 0.5 % = 2 % against a 2.5 % budget.
- **The shock brake read as a range** ("1.5 to 2.5 % SPY") but is two separate triggers.
- **Five technical timeout settings** sat between execution mode and slippage.
- **Nothing showed which shadow overrides a value.**
- **Save gave no hint that a change loosens risk.**
- **On a phone, Find a setting was hidden**, and the default text was centred under the input.

## Assumptions

- Direction (tighter or looser) is a property of each setting, kept in one map in the frontend; the backend doesn't need it.
- The preview endpoint is read-only and cheap; it never saves anything.

## Open questions for Marc

None. Decided without asking: Trading becomes the first group (most visits), keys move under Connections; the looser-save confirm is a dialog, not a typed confirmation.
