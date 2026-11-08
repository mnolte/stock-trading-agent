# 055 · feat · Loading states

Kickoff for Devin. Start the session with: "Build docs/kickoffs/055-feat-loading-states.md."

Frontend only. No backend, config or `docs/SPEC.md` change. The design is in `design/screens/Loading.dc.html` (every pattern) and `design/screens/MainLoading.dc.html` (the dashboard in its first load); the rules are in `design/DESIGN.md` → Loading.

```text
[055] Loading states. Goal: every screen loads, refreshes and waits the same
way, as design/DESIGN.md → Loading describes: no loading screen, skeletons in
the shape of the data after 200 ms, data fading in without moving the page,
busy buttons that keep their width, and nothing that moves with reduced
motion. Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. design/DESIGN.md → Interaction rules and Loading; design/CHANGES.md
   entry [055].
3. design/screens/Loading.dc.html and MainLoading.dc.html (open them in a
   browser: the replay and shimmer run), and their PNGs in
   design/screenshots/.
4. design/tokens.css: the new tokens and the loading classes at the end.

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.

Steps, one commit each; one PR, or one per step if it grows past ~600 lines:

1. CSS. Copy the 055 tokens and classes from design/tokens.css into
   frontend/src/styles/tokens.css and global.css (replacing sa-pulse). Keep
   the reduced-motion block.

2. Load timing in one place. In api/useResource (or a small hook next to
   it): expose `showSkeleton` that is false for the first 200 ms of a first
   load, true after that until the data is in, and held true for at least
   400 ms once it went true; `slow` after 8 s; and abort the first load at
   20 s into the normal error state ("No answer after 20 s"). Refreshes
   (the resource already has data) never set showSkeleton; they set
   `refreshing`, true after 400 ms. Unit-test the timing with fake timers:
   answer at 150 ms → no skeleton; at 250 ms → skeleton until 600 ms; at
   9 s → slow line; at 20 s → error; refresh → rows kept, refreshing at
   400 ms.

3. Card. components/Card.tsx and cardState.tsx: the card frame and title
   render at once; while showSkeleton the body is the card's own skeleton in
   a `.sa-loading` wrapper, aria-busy on the card and one visually hidden
   "Loading <card title>". Drop the visible "Loading…" text. When data
   arrives the body gets `.sa-in`. While refreshing: `.sa-refresh` in the
   card (position: relative) and `.sa-stale-rows` on the rows. "Still
   loading · n s" under the skeleton when slow.

4. Skeleton components, matching Loading.dc.html → Skeleton shapes:
   SkeletonTile, SkeletonText(lines), SkeletonTable(columns, rows 4–8,
   real headers), SkeletonList(rows), SkeletonChart(height), SkeletonChip.
   Each has the height of the filled version. No numbers, zeros or dashes.

5. Screens. Give every data card its shape instead of the generic bars:
   Dashboard first (match MainLoading.png at 1440 and 390, including the
   header: pill and stats as bars, no Needs you chip or phone-bar count
   until known, Pause aria-disabled until the state is known, kill switch
   live), then Positions, Performance, Insights, Inspector, Reviews,
   Strategies, Backtest, Readiness, Logs, Universe, Settings, the decision
   drawer (action line, four tiles, three section bars) and the Ask drawer.
   Replace the remaining "Loading…" texts (App.tsx, Performance,
   Insights, Inspector, Decision) and the App.tsx boot text with the shell
   plus skeletons.

6. Busy buttons. components/Button.tsx gets `busy` (spinner + label,
   min-width locked to the idle width, aria-busy, aria-disabled, clicks
   ignored, never the disabled attribute) and `waitingFor` (the muted line
   after 10 s). Use it for Save settings, Test key, Test and save, Send test
   email, Confirm draft / Change…, Confirm all, Approve / Reject, Resume,
   Pause, Retry, Run screen now, Start backtest, Acknowledge. At 20 s show
   "No answer" (not "failed"), keep unsaved input, and let the screen update
   if the action lands later.

7. Kill switch. Its buttons (header and the kill-switch dialog) are never
   aria-disabled or dimmed. Pressed: "Turning on…" with the spinner in full
   red; with no answer it is sent again every 5 s until the backend
   confirms. Stop & flatten sends one request id with every resend, and the
   backend acts on an id once (add that if it doesn't). At 30 s without an
   answer, show the critical banner "Kill switch not confirmed"
   (States.dc.html → Banners, text as there, role="alert"); resending
   continues under it, and it clears when the backend confirms, with the
   toast "Kill switch on since <time>". Tests: a second press and the
   resends create one event; a flatten resent 3 times sells once; the
   banner appears at 30 s and clears on the first answer (fake timers).

8. Progress and live rows. Backtest runs and history downloads: the 6 px
   bar with role="progressbar", aria-valuenow and the count in text; Universe
   "Run screen now": the sliding bar. Ask's live lookup step: the spinner.
   Rows new since the screen loaded (dashboard feed, order history, Logs with
   Follow) get `.sa-new`, removed after 1.6 s; never on the first load.

9. Screenshots in docs/screenshots/pr-<n>/: the dashboard at 1440 and 390
   during its first load (throttle the API in the e2e seed so the skeletons
   show) next to MainLoading.png, one refreshing table, one busy button, and
   one screen with reduced motion emulated. Playwright: assert that the
   dashboard's document height changes by less than 8 px between the
   skeleton and the data at 1440 (no layout shift), and that with
   prefers-reduced-motion emulated no element has a running animation.

Rules: no timer value hard-coded in components (they come from one module
that mirrors the tokens); no network in tests; make test and the e2e pass.

Done when: no screen shows a bare "Loading…" text or a full-page spinner;
every data card shows its own skeleton only after 200 ms; the kill switch is
never disabled; reduced motion stops every animation; the screenshots sit
next to the design PNGs in the PR.
```

## Why

The app shows three generic bars and "Loading…" in some cards, plain "Loading…" text in others, and nothing in the header while it loads. On a local install most answers come in under 200 ms, so those bars flash for a frame. On the VPS, or when Alpaca is slow (broker reads wait up to 5 s), cards jump when data arrives and buttons give no sign they were pressed.

## Assumptions

- The 20 s client timeout is new. It sits above the 5 s broker read timeout (`api.broker_timeout_seconds`) and below the page banner's 15 s retry, which keeps running.
- These are frontend timings, not trading thresholds, so they live in one frontend module that mirrors `tokens.css`, not in `config.example.yaml`.

## Answered questions

1. Kill switch with no answer (Marc, 8 Nov): keep resending every 5 s, and after 30 s show a critical banner that says it is not confirmed and how to stop the agent another way. Not "Check Alpaca": the kill switch is the agent's own state, which Alpaca can't show.
