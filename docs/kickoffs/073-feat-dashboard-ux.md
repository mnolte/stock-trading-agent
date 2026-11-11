# 073 · feat · Dashboard UX

Kickoff for Devin. Start the session with: "Build docs/kickoffs/073-feat-dashboard-ux.md."

Frontend only, plus one field in the cycle summary (step 2). No trading rule, setting or threshold changes. Spec: `docs/SPEC.md` → GUI → **Dashboard layout** and the **Daily briefing card** paragraph; `docs/CHANGES.md` entry [073]. Design: `design/screens/Main.dc.html` and `MainLoading.dc.html`, `design/CHANGES.md` entry [073]. Issue: #230.

```text
[073] Dashboard UX. Goal: the dashboard reads as the design does: cards at
their own height, a three-part cycle strip, four equal tiles, and a latest
decision card with one fixed layout.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md → GUI → Dashboard layout (item 073), Daily briefing card,
   Cycle counter, Next release; docs/CHANGES.md [073].
3. design/CHANGES.md [073]; design/screens/Main.dc.html with Main.png,
   Main-1024.png, Main-390.png; MainLoading.dc.html.

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR.
Put "Closes #230" in the PR description, so the issue closes on merge.

1. Rows of cards. The three card rows (positions + risk, chart + latest
   decision, decisions + briefing) align at the top (align-items:
   flex-start). Remove the flex spacers that stretch the latest decision and
   briefing cards. On a phone each card is as tall as its content.

2. This-cycle strip. Three parts, stacked under 760 px:
   - a sentence: "18:46 cycle · 12:46 NY · 69 symbols checked, 8 passed the
     pre-filter and 8 asked the model: <what happened>." The last clause
     comes from the cycle's changes ("nothing changed", "1 entry: MSFT",
     "JPM stop raised");
   - labelled values: Checked, Passed, Model calls ("8 of 10"), Cost, Took.
     Took is the stored cycle duration. Add it to the cycle summary endpoint
     if it isn't there (duration in seconds, a number);
   - the cycle counter, as today, with "Position check 19:01 · 13:01 NY".
   The release line (067) moves under the counter, after a "Release" chip.
   Every time is Amsterdam first, then "· HH:MM NY". Values are joined with
   " · " by one helper; nothing is concatenated by hand ("$0.000.next" today).

3. Tiles. Four in the tile grid (class from 056, 4 columns from 821 px, 2
   below): Since start, Positions, Model calls today, Last cycle. Last cycle
   shows the duration and "8 calls" — and ", up to 8 at once · 2.4 s" once
   item 078 stores model_seconds and max_in_flight; before that, leave that
   part out. Amber above schedule.cycle_duration_warn_minutes. Links:
   Performance, Positions, LLM inspector, Logs (filtered to that cycle).

4. Latest decision card. Exactly the design's layout, 20 px padding, 16 px
   between groups, 8 px inside:
   - header: title and "Open full decision →" (the 44 px button goes);
   - meta line: symbol · "18:46 cycle · 12:46 NY" · model · prompt version,
     or "no model call" for a no_call;
   - badge, symbol, and one line: "confidence 0.66", or the no_call reason
     in words from the label map: "pre-filter: RSI 74 is above 70",
     "call cap: 10 calls went to higher-ranked symbols", "price check: the
     last trade is 4 min old", "trend gate: …", "no options: …". Add any
     missing reason to the label map; never "confidence —";
   - Why (max 4 lines, then "more" opening the drawer), Context (regime,
     VIX, earnings, release chips), Signals (indicator chips), Key facts
     (max 3 rows). Each group has its small uppercase label.

5. Today's decisions: rows keep 44 px; a grouped row's symbol list wraps
   under the reason so the count stays on the row.

6. Phone (below 760 px), as Main-390.png:
   - an "On this page" chip row under Needs you (Positions, Chart, Latest
     decision, Today, Briefing, Watchlist), anchors with scroll-margin;
   - feed rows: the action above the symbols and reason;
   - a position's distance to its stop on its own line;
   - the watchlist as two-line rows without sideways scroll: symbol, price,
     day change; under them the rules result and the last action. Rank,
     sector and earnings columns from 760 px up.
   Also at every width: Why ends with "more" (opens the drawer); a tile's
   "Open →" sits at the tile's bottom.

7. Loading skeleton (MainLoading.dc.html): four tiles; the latest decision
   skeleton with the four group labels.

8. Screenshots in docs/screenshots/073/: dashboard at 1440, 1024 and 390,
   with MSFT (held) and with SPY (a no_call) selected.

Rules: no network in tests; labels from the one label map; times through the
056 formatter; nothing in design/.

Done when: the dashboard matches Main.dc.html at 1440, 1024 and 390 with no
sideways scroll, the watchlist included (no inner scroll at 390); no card has more than 24 px of empty space at its bottom at
1440; a no_call never shows "confidence —"; a Playwright check finds no
"undefined", "NaN" or a digit directly followed by a letter in the strip;
make test and the e2e pass.
```

## Why

Review of the running app on 11 November (seeded, 1440 and 390 px, `nl-NL`):

- **Latest decision:** regime, VIX and earnings chips sat between the signal chips with a sentence in the middle; "confidence —" on a no_call; the header "18:46 · none" didn't say which cycle or model; 200+ px of empty card under the button.
- **Cycle strip:** "1 model call · $0.000.next cycle 19:46", Amsterdam and New York times mixed without labels.
- **Stretched cards:** Today's decisions and Daily briefing stretched to the chart card's height, about 250 px empty each.
- **Tiles:** two tiles each stretched over half the page.

## Assumptions

- The cycle summary already has the counts; only the duration may need adding.
- Last cycle's "up to n at once" waits for 078; the tile works without it.

## Open questions for Marc

None. Decided without asking: Last cycle replaces nothing (the tile row grows from 2 to 4, with Positions back as a tile); "Open full decision" becomes a header link like the other cards' links.
