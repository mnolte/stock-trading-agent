# Spec changes

Newest first.

## 2026-11-10 — [060] VIX data, collected and shown

Issue #205, step 1 of 2 (step 2, decision.v7 with the VIX, is item 061, issue #206). Nothing in trading changes.

- **New data:** FRED `VIXCLS`, Cboe's VIX daily close, posted around 16:00 New York the next trading day, fetched at 08:00 and 17:00 New York and at startup (260 sessions of history); VIXY's 5-minute bars from Alpaca as a reference symbol, never traded.
- **Per cycle,** stored on `cycles`: `vix_close`, `vix_date`, `vix_percentile_1y` (252 closes), `vix_change_5d_pct`, `vixy_change_today_pct`, `vixy_change_since_vix_pct` (VIXY since the VIX close shown, bridging FRED's lag), `vix_stale` (more than the normal 1 session late). Backtests fill the same fields from history.
- **GUI:** the VIX beside the header's Regime label (tooltip below 820 px) and on the latest-decision card; stale shows amber with its date, missing shows "—"; two Insights slices (VIX level, VIX 5-session change); FRED in Settings → Data sources.
- **API keys:** an optional FRED key, only if the keyless CSV stops working.
- **Config:** `market.vix.source`, `percentile_sessions`, `stale_sessions`, `reference_etf`.
- Build plan item 39.

## 2026-11-09 — [058] decision.v6 (build)

Build of the 058 decision.v6 spec entry on branch `fix/058-decision-v6` (PR #202, stacked on #201).

- **`prompts/decision.v6.md`**: immutable prompt on the v5 contract — a sideways market is no longer a conflict by itself; a leading stock (`trend_daily` up, `momentum` strong) may be entered, and in high volatility its target must be at most `decision.v6.sideways_max_target_atr` (2.0) ATR away.
- **`sideways_rule`** (`decide/output.py`): code-enforced, v6 entries only — sideways + non-leading stock, or sideways + high volatility + target over the cap, turns the model's `enter` into `skip` with reason `sideways_rule`; the change is stored with `changed_by_code`. v5 never runs it.
- **Picker and drawer**: Settings → Model lists v6 with its header description (shown as the row's help text — a long `<option>` label overflowed the page at phone width); the decision drawer treats v6 like v4/v5.
- **Eval**: `eval/decision_v4.json` gained sideways predicates and cases; `make eval-decision PROMPTS=decision.v5,decision.v6` — sideways predicates 5/5, 5/5, 5/5. Live stays on the current prompt until switched in Settings.

## 2026-11-09 — [058] Trend gate regimes (build)

Build of the 058 trend-gate change on branch `fix/058-trend-gate-sideways` (PR #201, stacked on #200).

- **`decision.v5.trend_gate_regimes`** (`decide/v5.py`, `engine/cycle.py`, `shadows/`, `settings_service.py`, Settings UI): the daily regimes the v5 trend gate lets through, default `["up", "sideways"]`. `down` is always rejected and `trend_1h` must still be `up`. Shadow strategies can override the list; the v5 prompt itself is unchanged.

## 2026-11-09 — [058] Live data of 9 November (build, steps 0–4)

Build of the 058 spec entry on branch `fix/058-live-data-9-nov` (PR #200; the trend gate and decision.v6 follow in their own PRs).

- **Option touches** (`engine/outcomes.py`, `data/alpaca.py`, migration `s1a2b3c4d5e6`): an outcome row whose stored-bar window isn't covered fetches the symbol's 5-minute bars once per session through the historical limiter, stores them and retries; `option_touches_tried_at` records the attempt. Once the five-session window is more than `outcomes.touches_give_up_sessions` (2) sessions old and still uncovered, its options become `unavailable`, the row finishes and its verdict drafts with "option touches unavailable". Shadow decisions behave the same.
- **`price_stale`** (`data/models.py`, `engine/cycle.py`): the warning names the last IEX trade's age and price; the 60-second threshold is unchanged.
- **Rebuild reload** (`errors.ts`, `api/main.py`): a stale chunk after a rebuild reloads once and reports at INFO — no error group, no notification.
- **Restore test** (`ops/service.py`, `ServerSection.tsx`): with backup credentials unset, Settings → Server shows "not set up"; old records stay as history.
- **Recently sent** (`Settings.tsx`): the notification rows switched from a fixed `92px 1fr auto` grid to a wrapping flex row — a long channel status ("email held back · browser held back") used to squeeze the subject to zero width at phone size.

## 2026-11-09 — [058] Live data of 9 November

From the live-data report of 9 November (Devin): a quiet day, flat by design under the v5 trend gate. Four small fixes, a change to the trend gate and decision.v6.

- **Option outcomes, missing bars:** fetch the window's bars once from Alpaca's historical endpoint; if still not covered 2 sessions after the 5-session time (`outcomes.touches_give_up_sessions`), the options get `unavailable` and the row is done, so its verdict can draft. At most one try per row per session. Before, 17 rows were retried every cycle and their verdicts stayed pending until the bars were pruned (about 120 sessions).
- **`price_stale` warning:** names the last IEX trade's age and price.
- **Frontend after a rebuild:** a failed dynamic import reloads the page once and logs INFO; no error, no notification.
- **Restore test "not set up":** an earlier failure isn't shown as the current state.
- **Trend gate (decision v5), Marc's decision:** live now passes candidates in an `up` or `sideways` regime (`decision.v5.trend_gate_regimes`, default `[up, sideways]`, strategy override); `down` never, and `trend_1h` must still be `up`. On 9 November the gate had stopped all 7 candidates that passed the rules. The v5 prompt is unchanged: it treats a sideways regime as a conflict and, with high volatility, asks every check to agree, so the first effect is model calls that mostly end in skips while the regime stays high-volatility.
- **Decision v6** (`prompts/decision.v6.md`, Marc's decision): decision.v5 with the trend and volatility rules rewritten for a sideways market. A stock that leads a sideways market (`trend_daily` up, `momentum` strong) may be entered; in a high-volatility sideways market it must lead, every other check must agree, and its target must be at most 2 ATR away. Backed by the code check `sideways_rule` (`decision.v6.sideways_max_target_atr`, 2.0). v5 contract reused; eval cases added; live switches to v6 once the eval passes (no shadow period).
- Known prompt versions now read `decision.v1`–`v6` (the sentence still said v1–v3).

## 2026-11-09 — [057] Log reviews on fingerprints with line breaks

- `POST /api/logs/review` ran the fingerprint through `_one_line`,
  which rewrote line breaks to spaces. Fingerprints of multi-line
  messages (tracebacks, trailing newlines) kept theirs in the index,
  so the stored review never matched and the problem stayed
  "unreviewed". The fingerprint is now stored verbatim — it is a join
  key, never rendered — and the Logs summary resolves the latest
  review per fingerprint deterministically.

## 2026-11-09 — [056] UX pass on the running app

From `docs/analysis/056-ux-review.md`. Behaviour of the GUI only; nothing in trading, risk or data changes.

- **Settings screen:** one section at a time with its own address (`/settings/risk`), a Section select on phones, search and Changed from default across all sections, unsaved changes kept across sections.
- **Frontend errors:** an error boundary per card; the same error from the same card is logged once per page load; outside Logs an error shows as its group's summary (first line, no URLs or paths, ≤ 120 characters, with a count), never a stack trace.
- **Dates and codes:** one date format ("14 Sep", "14 Sep 2025") with fixed English month names; stored codes shown as words.
- **Decision detail:** when no order was sent, What happened says why.
- **Backtest:** results as one table (a column per strategy and SPY); From and To default from the agent's clock; Run says what is missing.

## 2026-11-08 — [054] Restore-test 'not set up' warning

- With offsite credentials unset, `check_backups` replaced the stored
  `restore_test` status with `{"not_set_up": true}` — no `at` — and the
  staleness check then logged `unparseable restore_test.at None —
  ignored` on every hourly run. The staleness parse now skips the
  `not_set_up` state; a genuinely unparseable `at` still warns.

## 2026-11-08 — [052] Symbol-keyed config keys

Config layers: `models` and the symbol-keyed `universe.sectors` and
`universe.names` are open-ended — entries the example doesn't list are
kept, like model ids. Before this, a sector saved for an added symbol
(TSLA, ACN, WMT, COST, COP on 8 Nov) was dropped at every layer and the
`sector_unknown` gate blocked all entries for it.

## 2026-11-08 — [053] Security audit findings fixed

Follow-up to the 053 audit (#164): all ten open findings #153–#162 are
fixed, one commit each.

- Post-sign-in `next` redirects are constrained to same-origin paths —
  absolute, protocol-relative and backslash-normalised URLs fall back to
  `/` (`safeNextPath` in the API client, used by Login and the 401
  redirect).
- The request gate reads `Transfer-Encoding` case-insensitively and
  detects `chunked` inside comma-separated token lists, so
  `gzip, chunked` can no longer skip the body cap or JSON-only checks.
- Loopback `Origin` headers must carry an explicit port; port-less
  `http://localhost` origins are refused (Host handling unchanged).
- The CSRF double-submit comparison uses `hmac.compare_digest`.
- Over HTTPS only `__Host-sa_session` authenticates; the plain
  `sa_session` name is accepted only on plain HTTP/loopback.
- `backup_credentials` scrubbing now registers the nested
  `access_key_id`/`secret_access_key` values, not only the whole blob.
- Saving the SMTP card always requires "Confirm it is you", including
  first-time setup.
- Backtest CSV export whitelists `kind` to `trades`/`decisions`; anything
  else is a 422 before it reaches `Content-Disposition`.
- The global sign-in pause persists on the user row
  (`users.global_locked_until`, migration `r1a2b3c4d5e6`) instead of
  living only in memory, so a restart no longer lifts it early.
- `security/run-audits.sh` pins `uvx --python 3.12` so pip-audit does not
  resolve under an older interpreter.

## 2026-11-08 — [045] Notifications: drain waits for in-flight sends

`NotificationService.drain()` used to return as soon as the queue looked
empty — but the background worker and a manual drain can run at once, so
the last item could still be inside `_process` (mid-commit) when the
caller read the table. `drain()` now also waits for items other drains
are processing (`_inflight` counter), which makes "drained" mean every
queued notification's row has actually landed. This flaked
`test_new_error_notification_once_per_fingerprint` in the image CI job;
a regression test (`test_drain_waits_for_an_in_flight_item`) pins the
semantics.

## 2026-11-08 — [045] GUI catch-up: remaining spec gaps closed

Audit of issue #139 against `main` after #146 found five leftovers; all now match `docs/SPEC.md` and `design/`:

- **v5 drawer ev gate**: the `Confidence ≥ min_confidence` check row now appends the expected value in R (`EV +0.35R`, p·(rr+1) − 1 on the chosen option) — SPEC "Decision v5 — GUI".
- **Screened watchlist**: the 15-row cut never hides a held symbol ("held symbols always listed"); unranked held rows sort before other unranked rows.
- **LLM inspector**: `hold_below_breakeven` joins the reason filter labels/options (the reason the v3+ validators already store).
- **Settings prompt picker**: decision versions an active or paused shadow runs are marked `· shadow E` (prompt_options carries `shadows`).
- **Held-back exit Needs-you item**: its own `pending_exit` kind with the States.dc.html wording — "KO exit held back since 15:46." plus "Quantity not confirmed at Alpaca after 3 tries; the stop at 94.22 still stands. Retried at every check; market-on-open if still pending." — "Open position" action, and the item leaves when the exit fills rather than when acknowledged (the event still resolves by condition in the executor).

## 2026-11-08 — [046] Spec alignment: default models and gate order (G3, G7 spec halves)

- The default live model is `gpt-4.1-mini` in the overview, comparison table, model-providers item and Settings text; review defaults read `gpt-4o-mini` weekly, `gpt-4.1-mini` briefing and verdict — matching `config.example.yaml`.
- The risk-gate table is rewritten in the order `gates.py` evaluates: pre-checks (kill switch, paused, reconciliation, same symbol), then max positions, sector, daily loss, shock brake, earnings blackout, re-entry, drawdown, losing streak (halves risk, never blocks), the sizing limits (risk per trade, position size, open risk, cash) and order sanity. Earnings exits noted as not an entry gate.

## 2026-11-08 — [046] Spec alignment: no_call reasons and the data-model table (G6, G10)

- The `no_call` reason list at "Decision" now names the reasons the code actually stores — `price_stale`, `price_suspect`, `reference_missing` and `checks` replace the stale `quote_missing`; `no_options` stays (spec 298).
- The data-model table lists the eight tables it omitted: `smtp_settings`, `bars_1h`, `bars_1h_state`, `daily_source_gaps`, `approvals`, `runs`, `reviews`, `verdicts` — all in use (G10 from `docs/analysis/046-spec-alignment.md`).

## 2026-11-08 — [046] Spec alignment: step 6 — card-level states in e2e (G12)

- `e2e/046-position-states.e2e.ts` covers the states the standard seed can't reach: broker-stale positions ("Alpaca not answering · as of …"), the no-stop-at-Alpaca chip with its reason, and a held-back exit with tries and failure — all mocked `/api/positions`, both viewports. G12 from `docs/analysis/046-spec-alignment.md`.

## 2026-11-08 — [046] Spec alignment: paused banner says why (G17)

- A manual pause now shows the paused banner — "Paused at HH:MM — you paused it." — with the same reconcile detail and Resume action as the startup banner. "Started paused" still marks only a fresh start. Gap G17 from `docs/analysis/046-spec-alignment.md`; spec row 203 ("Resume says why if Marc paused").

## 2026-11-07 — [046] Spec alignment: kill-switch banner flatten action (G18)

- The kill-switch banner advertised a `flatten` action that rendered no button — `BannerStrip` now maps it to "Stop & flatten", opening the same confirmation dialog as the header's Kill switch menu. Gap G18 from `docs/analysis/046-spec-alignment.md`.

## 2026-11-07 — [046] Spec alignment: position-check severity, gate-order wording, PID-reuse test

Phase 2 of item 046, the backend group of the gap report (`docs/analysis/046-spec-alignment.md`).

- **Position checks failing (G5)**: the durable `position_check_failed` event is recorded at `error` instead of `critical` — the spec's notification row is Warning, so the banner no longer renders critical. The notification mapping (`position_checks_failing`, Warning) is unchanged.
- **Gate order (G7, code half)**: `risk/gates.py` docstrings describe the code's order as the order of record; the spec's gate table is rewritten to match in Marc's spec PR.
- **Backtest PID reuse (G11)**: verified every `os.kill` site in `backtests/manager.py` is `_pid_same`-guarded; a new test pins the cancel path — a live pid with a different start time is never signalled.
- **G8 verified built, no change**: the `missing_stop` repeat already carries the elapsed time ("still no stop · 1 h 30 min", `executor.py` + `test_stop_check.py`); the report's citation was a different notification.

## 2026-11-07 — [046] Spec alignment: config keys and model defaults

Phase 2 of item 046, the config group of the gap report (`docs/analysis/046-spec-alignment.md`).

- **Notification defaults (G1)**: the 11 events missing from `notifications.events` now carry the spec table's channels — `readiness_changed` and `restarted_resumed` get browser, the nine VPS/security events get browser + email. They previously fell back to app-only (+browser when critical).
- **Inspector (G2)**: `inspector.default_sources` is `[live, review, ask]` per the spec's filter table; shadows and backtests stay opt-in.
- **Reviews (G4, G9)**: `reviews.skip_flat_pct: 2` added (the code fallback, confirmed by Marc); `reviews.prompts.briefing` back to `briefing.v2` — the v1 default was an accidental revert.
- **Models (G3, config half)**: `decision.model` and the briefing/verdict review models default to `gpt-4.1-mini`; weekly stays `gpt-4o-mini`. The spec text moves in Marc's spec PR.

## 2026-11-07 — [052] Browser console warnings

- **Favicon**: `frontend/public/favicon.svg` (the SA app mark) linked from `index.html`; the browser no longer requests `/favicon.ico` and 404s.
- **Router**: the decision-drawer `<Routes>` in `App.tsx` gets a catch-all that renders nothing, ending the dev warning "No routes matched location" on every page that isn't a decision deep link.
- **CSP**: the Settings section-nav phone media query moved from an inline `<style>` element to `styles/global.css`; the strict CSP (`default-src 'self'`, no `style-src`) no longer blocks it. The rules and their `!important` use are unchanged.

## 2026-11-07 — [051] Kickoffs move to the archive with the build

- The build PR now moves its kickoff to `docs/kickoffs/archive/`, in the same PR as the code (`git mv`, unchanged). An item built in several PRs: the first moves it, the later ones read it from `archive/`. Before (048), the move waited for the PR that set the item to `merged`. So `docs/kickoffs/` lists only work nobody has started; the register's Status still says `building` or `merged`.
- Rule in `docs/work-items.md` (Kickoffs move to the archive with the build), AGENTS.md (Names), CLAUDE.md, `docs/README.md` rule 9 and the PR template. The spec-label gate is unchanged: an unchanged move still needs no label.
- Moved now: `050-fix-stop-invariant.md` (built in #148), row 050 set to `merged`. Left open: `046-fix-spec-alignment.md` (ready; its first Phase 1 PR moves it).

## 2026-11-07 — [050] Stop invariant (build)

Build of the 050 spec entry on branch `fix/050-stop-invariant`.

- **Stop check** (`engine/executor.py`): at the end of every reconcile, every broker position is checked against the open orders for live protection (bracket legs, OCO stops, standalone stops; statuses `new`, `accepted`, `held`, `partially_filled`) with quantity from `qty_available` vs stored legs plus an exit in flight (`execution.exit_in_flight_seconds`, 60, in `config.example.yaml`). One repair attempt, then a durable `stop_missing` episode that closes only when a check finds the position protected or flat. The repair's skip reasons (`exit_in_flight`, `insufficient_qty`, `already_protected`, `repair_error`) are returned to the caller and logged at WARNING; the old "prior fills cover the position" comparison against the full original quantity can't cover a partial-exit remainder anymore.
- **State errors** (`readiness/service.py`, `api/routes.py`, `engine/executor.py`, `secrets_service.py`): `stop_missing`, `protective_stop_failed`, `pending_exit`, `reconciliation`, `stream_degraded`, `live_decisions_failing` and `api_key` are excluded from quiet-time auto-resolution and from banner dismiss while still true (POST `/api/events/{id}/dismiss` answers 409). Clearing paths: stop check, pending-exit clear, stream recovery, live-call recovery, acknowledge, key test pass. Readiness `stops_missing` counts hours with an open episode.
- **Needs you** (`attention.py`): an open `stop_missing` episode is the first item — critical, "KO has no stop at Alpaca since 15:46 · 7 shares · <reason>. Code retries at every check.", action "Open position", no acknowledge. `stop_missing` maps to the `missing_stop` notification (30-minute repeats during the session).
- **GUI**: red "No stop at Alpaca since … · n h m min" chip on position cards and the dashboard mini list, "stop none" red in the Orders at Alpaca block, a "Stop at Alpaca" row in decision drawers' What happened, "Heartbeat not set" and offsite "not set up" in Settings → Server with a "Config: n local overrides · n unknown keys" row.
- **Config layers** (`config.py`, `llm/models.py`, `settings_service.py`): `config.example.yaml` always loads; `config.yaml` overrides key by key; saved settings override both. Unknown keys warn once and are ignored, counted in `config_layers`. `ask.model` validates like the other models; missing named models warn at start instead of refusing.
- **Unclean stop** (`restart.py`, `api/main.py`, `api/routes.py`, `notifications/events.py`): `unclean_summary` computes "Last alive 18:46:35 · down 61 min · missed 3 position checks and the 19:46 cycle" (or "missed nothing") from the run-lock heartbeat and the trading calendar; it appears in the banner, a WARNING log line and the `restart_paused` notification.
- **Ops** (`ops/service.py`): without `backup_password`/`backup_credentials` the offsite copy and restore test record `not_set_up` — no failure notification.
- **Analysis**: the 4 Nov verdict `number_check_failed` burst was the pre-`bc81993` check counting `1` in `1-hour` (and `7` in `L7`) as numbers; fixed the same morning, no new code — see the analysis doc's "Verdict number check".

## 2026-11-07 — [045] GUI decisions (spec)

Marc's answers to the five open questions of the 045 kickoff; the spec now matches the 045 design.

- **Ask button** in the sidebar and the phone top bar, not the header (GUI → Header, now two fixed rows; Ask the agent → Opening it and GUI). The app already had it there (cea419d).
- **EUR** beside equity, today's P&L and card totals only; other amounts USD with EUR in a tooltip (Overview → Currency, the decisions table, GUI).
- **Decision detail** row: one drawer order for every version, with What happened (final action, code changes, warnings, order, verdict, Ask about this) and an outcome for skips, holds and exits.
- **Decision v5 → GUI** (new paragraph): candidate drawer columns (`target_atr`, `min_confidence`, `base_rate · n`), trend gate and ev gate rows, time-left row; held Hold or exit block; the overnight Needs you item; Insights "What each filter removed".
- **LLM call history**: Source adds Ask and Backtests (Ask on by default); the Outcome table lists `unresolved_conflict`, `confidence_outlier`, `best_option_missing`, `best_option_mismatch`, `hold_below_breakeven`, and the change reasons stored with `changed_by_code`; the reason filter splits `changed_by_code` by change reason.
- **Settings screen**: the section list with Entry selection, Decision gates, Display, Trading → Prices and timeouts, and Model and reviews with three groups; every "Settings → Decisions / Reviews / Ask the agent" now reads "Settings → Model and reviews → …".

## 2026-11-07 — [050] Stop invariant (spec)

From the live data of 4–5 November (`docs/analysis/050-live-data-2026-11-04.md`): KO kept 7 shares without a stop at Alpaca for 6 hours, the error resolved itself after an hour, and the app was down for an hour unnoticed.

- **Alpaca integration → Stop check** (new): at the end of every reconcile, every open position must have working stops for the shares Alpaca holds, or stops plus an exit in flight (a market sell younger than `execution.exit_in_flight_seconds`, 60, or a queued market-on-open sell). Otherwise one more repair, then a stop-missing episode: CRITICAL then ERROR in the log, a critical Needs you item, the `missing_stop` notification every 30 minutes during the session, a red line on the position. It closes only when a check finds the position protected or flat. Repair skips log WARNING with a reason; only the check decides.
- **Readiness → Errors**: errors that describe a state (stop missing, protective stop failed, pending exit, reconciliation mismatch, stream degraded, live decisions failing, key failing) no longer resolve after an hour of quiet; they resolve when the state clears, and can't be dismissed while it lasts.
- **Needs you**: a new first row for a position without a stop right now; the banner row now reads "a protective stop that was placed". **Notifications**: the `missing_stop` row covers "still none, every 30 minutes".
- **Architecture → Config layers** (new): `config.example.yaml` always loads, `config.yaml` overrides it, saved settings override both, key by key. Until now a local `config.yaml` hid every later default (the cause of the gpt-6.1-sol briefing failures and of `missing_stop` being browser-only). Unknown keys and unknown model ids warn at start; saving an unknown model is refused.
- **Unclean stop detection**: the banner, log and notification say when the app was last alive, how long it was down and which checks and cycles it missed.
- **Monitoring**: the heartbeat applies to a local install too; "Heartbeat not set" while empty. **Backups**: "not set up" instead of a failure without offsite credentials.
- **Model list**: `supports_temperature` for OpenAI models (default true).
- Kickoff `docs/kickoffs/050-fix-stop-invariant.md`. `execution.exit_in_flight_seconds` arrives with the code (rule 7).

## 2026-11-06 — [048] Kickoff archive

- `docs/kickoffs/` now holds only open work. A kickoff moves unchanged to `docs/kickoffs/archive/` in the PR that sets its item to `merged` (usually the last build PR; for a `spec` or `analysis` item, the PR that completes it); a `dropped` item's kickoff moves too. Rule in `docs/work-items.md` (Done kickoffs go to the archive), AGENTS.md (Names), CLAUDE.md, `docs/README.md` rule 9 and the PR template.
- Moved now, everything merged: `pr-29.md` … `pr-37.md`, `fix-live-2026-10-29.md`, `fix-live-2026-10-30.md`, `fix-scan-2026-10-31.md`, `fix-v4-2026-11-03.md`, `ops-alerts.md`, `quote-plausibility.md`, and `docs/DEVIN_KICKOFF.md` (items 001–028) as `docs/kickoffs/archive/DEVIN_KICKOFF.md`. Left open: `045-feat-gui-catch-up.md` (ready, not built).
- Spec-label gate, rule 4: an unchanged move of `docs/kickoffs/<name>.md` to `docs/kickoffs/archive/<name>.md` needs no `spec change` label, so a build PR can archive its kickoff without becoming spec work. An edit in the same move, another name, or a move out of the archive still needs the label. Five tests in `backend/tests/ci/test_spec_label.py`.
- Register statuses brought up to date: 038–044 and 047 `merged`, 045 `ready`.
- Paths in older entries below and in code comments are not rewritten; a kickoff not in `docs/kickoffs/` is in `docs/kickoffs/archive/`.

## 2026-11-06 — [049] Log routes 503 on schema-less index

*Item 049; merged as [048] in #143, renumbered 7 Nov because 048 was already the kickoff archive (#142).*

- An existing `logs.db` without the `log_lines` table made `/api/logs/{id}/around` and `/api/logs/export.jsonl` crash with `no such table` — a 503 on bad input, which broke `test_every_get_route_survives_bad_params` inside the image.
- `_log_db` now treats a file without the index schema as unavailable, and both routes answer like `/api/logs` does (`index_available: false`) instead of raising 503.

## 2026-11-06 — [047] Drawer model-call e2e flake

- The PR 9 drawer e2e assumed the latest-decision card's target had reached the model; a live cycle writing a newer rules no_call for the same symbol made it fail at random.
- `tests/e2e_seed.py` now writes the `llm_calls` row a real model decision always has, so a decision with "Open model call" exists from the start; the test opens the card's drawer for the sections and resolves a decision with a call through `/api/llm-calls` for the footer link.

## 2026-11-05 — Live pending exits (issue #136)

- Store held-back live exits on the position with the decision, reason, first attempt time and failure count.
- Retry before cycle and position-check work. Reconcile saved sell client IDs before any new submission, and repair missing protection while an exit waits.
- Surface a critical Needs you item after three failures or at session close, including earnings exits. Clear pending state on a confirmed flat position or sell fill.
- Adds Alembic revision `q1a2b3c4d5e6`; existing installations need the schema upgrade on their next permitted restart.

## 2026-11-05 — Executor/API misc lows (issue #134)

- Trade-update fill and position bookkeeping now takes the reconciliation lock, so a broker snapshot cannot race stream writes.
- Missing Alpaca positions use the SDK's HTTP status; unstructured errors only use the specific missing-position phrase, not arbitrary `404` text.
- Malformed log records report through the index's stderr/file fallback instead of disappearing silently.
- Stored briefing status lines and plain-text fallbacks share one wording source, including regime and yesterday, so the GUI and email show the status only once.

## 2026-11-05 — OpenAI fixed-temperature models

- Model entries can set `supports_temperature: false`. The OpenAI adapter then omits temperature from structured-output and chat tool calls instead of sending a value the model rejects. GPT-6.1-sol has this flag; other models keep their existing temperatures. Calls are still never retried. The Responses API path continues to omit temperature.

## 2026-11-05 — [044] Spec rulings from the 043 audit

Answers the seven open questions in issue #124, found during the audit on #123.
- **Entry selection:** the call cap counts real model calls only. A candidate that ends without a call uses no slot.
- **Counterfactual touches:** the fallback stop reference (min of price and the last 5-minute close) applies to every version with code-built options, for counterfactual options only.
- **Agent and earnings exits:**
  - the quantity is confirmed before the legs are cancelled;
  - a held-back exit is stored as pending and retried at every position check and cycle;
  - the protective stop is placed if the legs are gone;
  - a critical Needs you item after 3 tries, or at once for an earnings exit still pending at the close.
- **Market price:** with a stale or suspect price, a held position gets no stop options and no stop reference, so a tighten can't be chosen.
- **Verdicts:** confirmed. Every model enter, blocked or not, and every model skip gets a verdict (Reviews → Verdicts, unchanged).
- **Option order:** confirmed and clarified: nearest stop first, then the lowest reward to risk.
- **Constants to config:** `decision.v5.price_vs_signal_adverse_atr` (0.5) and `decision.v4.near_level_atr` (1.0). The prompts' "1 ATR" wording pins the latter, so a change needs a new prompt version. Both keys come with the code (rule 7).

## 2026-11-05 — [043] Trading path audit fixes

- Exits and flatten now sell only broker-released shares (`qty_available` after `_await_released`) — a confirmed cancel is not a released hold, and a market sell against held shares was rejected, stalling the exit. Blocked sells now raise a critical event instead of vanishing, and flatten records a failed `cancel_all_orders`.
- The protective repair fails on a broken open-orders read instead of treating it as "no live sells", marks nested leg rows dead with their parent, and surfaces a negative broker qty as a critical mismatch in adoption and coverage instead of pretending a sell stop covers a short.
- `orders.alpaca_order_id` is now unique (migration merges any duplicates): two writers could grow a duplicate and crash every later reconcile. `submitted_at` is populated. `approve()` is single-flight, re-checks status before the send, resolves a broker-recovered order as approved, and never overwrites a terminal status. `Position.closed_at` is the exit fill time, not detection time.
- Every order call holds the shutdown slot from send through store+commit (rule 16), and `_enter` marks an unrecovered submit failure `blocked`. Marginal fill prices are clamped to the broker average when the derivation is implausible.
- Live and shadow option touches share one scan: the shadow copy had inverted entry precedence, no malformed-input guards and a divergent window. Rows with missing bars stay NULL and retry instead of being frozen by a tombstone. Weekend/holiday decisions get session targets. After-exit windows fetch one price basis when a split is in play instead of double-adjusting; `_initial_stop` resolves v2+ decisions so R metrics work again; blocked model-enters get verdicts; a `price: 0` snapshot no longer crashes the review run; the rank-order last-slot floor matches the optimal search.
- Smaller fixes: LLM cache key gains `prompt_version`; WS frames over 64 KiB are refused; `/chart` and `/dashboard` validate tickers; backtest list reads are capped; `SA_INSTANCE_ID` rejects order-kind words at startup; a mid-strategy shadow failure rolls back partial rows; several silent `except` paths now log.
- Second pass: `SA_INSTANCE_ID` over 10 alnum chars no longer flags every own order as a foreign instance (kill-switch trip); the touch window's 10-minute and 5-session bounds are exact; outcome rows past the bar-retention window stop retrying and unblock verdicts; `held` nested legs no longer cancel working entries or fake stop coverage (held under a *filled* parent still counts — Alpaca keeps it held); orphan repair commits per leg instead of holding the write lock across broker calls; `replace_stop` stamps a fresh client order id; a broker 404 counts as confirmed-dead; adopted-position submits keep send→store→commit inside the order slot; shadow temperature/options_version/refusal handling matches live; dividends, biggest-miss and `_live_trade_r` edge cases fixed; the last-slot floor binds the actual last-slot taker (lowest expected profit).
- Third pass: the bar cache's "upsert" was insert-only — refetched SIP revisions and the still-forming newest session were discarded, so a mid-session restart could keep a permanently partial daily bar; `store`/`store_daily` now update. `option_touches` retries while the traded option's real touch is unknown and persists terminal null verdicts; shadow scans the chosen option counterfactually. Offsite backs up the verified daily copy, restore accepts dated or live-path snapshots, cert/backup checks run off the event loop, a 404 heartbeat counts as dead, the estimate loads the lookback calendar, bundle DBs refresh corporate actions, backtest terminal status can't be clobbered, `/api/positions` filters in SQL, `llm_calls` links are nulled not deleted on rollback, and `backup_failed`/held-exit events notify under their real keys.

## 2026-11-05 — [042] Backend test audit fixes

- The startup route-coverage walk (`app/auth/guard.py`) now expands lazily included routers — under this FastAPI version it previously saw none of the API surface, so a router included outside `/api` would have shipped unguarded without the check firing. The session-walk test asserts it actually checked routes.
- `POST /api/auth/totp` no longer 500s: the reset read `user.username` after the session closed.
- `client_order_id` refuses an instance id that collides with an order-kind word (such ids parsed as the pre-instance shape and disabled foreign-instance detection).
- `EnvSettings` honours `SA_ENV_FILE`, so a repo-root `.env` no longer feeds test configs.
- Test suite: implemented the empty shadow same-day-exit test; the secrets and base-rate tests now exercise real code; the deploy market-hours guard test runs the script's own functions; gate boundary, `require_confirmed`, auth-endpoint, upload-cap, WebSocket fetch-metadata, `protective_stop_failed`, `wait_for_work`, rule-13 calendar, instance-clamp and session-clock/DST coverage added; naive/aware datetime bugs and drifted seed rows fixed; Argon2 hashing is cheap in tests and the bounded-inputs sweep is ~7× faster.

## 2026-11-05 — [041] Stale header numbers

The header's equity and today figures come from the executor's cached
account snapshot, which only `refresh_account` updates. The position
check and the decision cycle both refresh it *after* reconciliation has
already pushed `header` to the GUI, so the debounced header refetch
raced the broker read and often served the previous snapshot — the
figures lagged until a page refresh while the rest of the page updated.
`update_session_brakes` now pushes `header` once the refreshed snapshot
is stored. The push lives there and not inside `refresh_account` because
the risk-budget endpoint calls `refresh_account` while its card
refreshes on `header` updates — a push there would refetch the card in a
loop.

## 2026-11-04 — [040] EP shadow gate_detail crash

An `UnboundLocalError` in `ShadowRunner._decide_candidates_ep` killed
the whole `shadows-cycle-N` background task: `gate_detail` was only
assigned inside the v5 trend-gate and v4 checks-gate branches but read
right after them, so any EP-mode shadow whose dispatch ran neither
branch crashed (issue #110; introduced by `dca92ba`). That is every
v2/v3 shadow on `selection_mode: expected_profit`, plus v4 with
`checks_gate` off (the default) and v5 with `trend_gate: false` —
the remaining strategies and candidates of the cycle got no rows.
The variable is now initialised before the dispatch, matching the
parallel block in `_decide`, and the gate branches are covered for
both the firing case (`no_call · checks`) and the model-call case.

## 2026-11-04 — [039] Protective stop repair

The KO incident of 4 November: a held decision to exit 21 shares ran
at the same moment as a settle-triggered reconcile. The protective
repair saw the agent's own market sell as an orphaned holder, cancelled
it mid-fill (14 of 21 sold), then sent the replacement OCO and the
fallback stop for the stale qty — both rejected, two critical alerts.
After the partial exit, every later check skipped the remaining 7
shares for good: recorded sell fills (14) already exceeded the
remaining qty (7), so "sell fills already cover" fired on every pass.

- A fresh market sell is an exit in flight, not a holder: the repair
  defers and lets it finish; the next pass fixes whatever remains.
  A market sell older than `execution.fill_settle_seconds` is stuck
  and is cancelled like any other holder.
- "Sell fills already cover" now compares fills to the position's
  full original size (entry order qty, else `qty + exit_filled_qty`)
  instead of the remaining qty — a partial exit no longer disarms
  protection for the remainder.
- A confirmed cancel is not a released hold: after the holder legs
  die, the repair polls the position until `qty_available` — a new
  `BrokerPosition` field — frees the shares before sending the OCO;
  the free qty is confirmed again before the fallback stop, and every
  sell the repair sends is capped at what Alpaca reports free.
- A live stop covering only part of the position is a holder too:
  it no longer reads as covered; it is cancelled and replaced by an
  OCO for the full size. Cancels dedupe on the parent order so a
  nested leg is killed through its parent.
- The fake broker now models `held_for_orders` and `qty_available`
  (cancel cascade to legs included), with `hold_release_lag` and
  `enforce_available` knobs so tests replay the real rejection.
- Follow-up: when the broker-qty sync shrinks a position by more than
  recorded exit fills explain, the unexplained shrink now counts in
  `exit_filled_qty` too — `qty + exit_filled_qty` is the original size
  for every position, including adopted ones with no entry order, so
  the cover check can't under-count after a foreign sale or a fill the
  stream never delivered.

## 2026-11-04 — Verdict outcome line shows the profit or loss

The outcome line on a verdict carried no number for every call that
offered options — all current drafts are skips with options, so a row
read "A, B, C: stops first or never filled" with nothing on what the
call made or lost. The stored `pct_5sessions` fact now ends every
options-based line ("… · −10.5% after 5 sessions"), and a real trade's
dollar P&L sits next to its R ("filled · stop first · −1.0R ·
−$152.30"; an open trade gets the 5-session move instead). The
decision drawer's verdict block now renders the same outcome line the
Reviews row shows, per the spec's "shows the same".

## 2026-11-04 — Number check: identifiers are not figures

The verdict number check failed every model text that named a
timeframe or a level: "1-hour trend" and "L7" produced literals with
no matching fact, so 92 of 277 verdict.v2 calls fell back to the plain
template — every one a false positive, no invented figure among them.
The check now masks identifier-shaped text before scanning: letter-led
ids ("L7", "EMA20", "v2"), hyphenated duration labels ("1-hour",
"5-session") and attached time abbreviations ("1h", "30min").
Space-separated counts ("5 sessions", "option 1") and ranges ("1-2%")
stay checkable; the decision explanation check uses the same mask.
Draft verdicts already written as templates are unchanged; dropping
them rewrites them on the next verdict scan.

## 2026-11-04 — [038] Naming convention for work items



## 2026-11-04 — Decision v5: the code

The v5 build (`docs/kickoffs/archive/pr-37.md`) lands behind the version
gates: v1–v4 keep their behaviour; a `decision.v5` strategy or prompt
version takes the v5 path.

- `config.example.yaml` gains `decision.entry_gate` (`floor`,
  today; `ev` is the v5 default via the strategy override),
  `decision.min_ev_r` (0.25) and the `decision.v5.*` block
  (`exit_margin` 0.03, `overnight_margin` 0.10, `gap_sessions` 60,
  `long_target_minutes` 120, `trend_gate` true, `max_rr` 3.0,
  `base_rate_sessions` 60, `base_rate_min_n` 30).
- The expected-value entry gate gives each option `min_confidence`
  = (1 + min_ev_r) ÷ (1 + rr); the trend gate stops v5 candidates
  before the model when `trend_1h` or `regime_trend` isn't `up`;
  removed and blocked options get counterfactual touches; snapshot v5
  adds `session.last_cycle`, `overnight.gap_p90_atr`, `target_atr`,
  `price_vs_signal`, `price_vs_ema20` and the earnings status; the
  `hold_below_breakeven` warning, the wider path claim and the
  overnight Needs you item land; replay and eval rebuild and validate
  v5; the drawer shows the new fields.

## 2026-11-04 — Decision v5: fewer missed trades and fewer losing entries

- From a second review of v4 against the data of 31 October, focused on missed opportunities and losing entries.
- **Counterfactual touches** (every strategy):
  - removed options are stored in full;
  - candidates stopped by the rules pre-filter, the trend gate or the price checks get options built without a model call;
  - all of them get option touches;
  - Insights and replays show "What each filter removed".
  Today the room filter and the pre-filter can't be judged at all.
- **v5 additions:**
  - per-option `base_rate`, the recent target-first rate of comparable options shrunk toward break-even, as the starting point for confidence;
  - `max_rr` 3.0, since far targets reached their target first about 5% of the time;
  - the trend gate becomes a strategy override, since one week of data went against it.
- **Correction:** shadow H "no first-hour entries" was based on the Insights design's sample data (−0.18R over 31 trades at 10:46), not the run. It is replaced by "H · RSI ≤ 60": 7% target-first at RSI 60–70 against 23–24% below.
- `prompts/decision.v5.md` (not yet used) updated for base rates and the softer trend wording; the PR 37 kickoff updated.

## 2026-11-04 — Decision v4: the code fixes of 3 November

From the v4 review (`docs/analysis/2026-11-03-decision-v4-review.md`,
kickoff `fix-v4-2026-11-03.md`), all code — no prompt or threshold
changes:

- `thesis_holds`: the regime's `sideways` counts like `mixed`, so
  up → sideways and sideways → down are adverse (the KO entry case).
- `previous_decision` now comes only from the same context (candidate
  or held) and carries `final_action` and `change_reason` — a
  spread-blocked enter reads blocked · spread, not a held position.
- Headlines with `about_symbol` lead the capped list (newest first),
  then the rest — market noise can't push a company's own news out.
- `stop_group` keys on the Decimal stop: 101.70 and 101.7 share a group.
- `universe_pool.name` (new column): Alpaca's asset name cleaned of
  corporate suffixes, filled on add/import and refreshed at each daily
  screen (which backfills existing rows). Screened-mode `about_symbol`
  matches pool names with `universe.names` overriding; fixed mode is
  unchanged.
- `checks.earnings.status`: `none_in_window`, `scheduled` or
  `unavailable` — never all null without a status.
- `llm_calls.sent_input` (new column): the snapshot exactly as sent.
  "User · input snapshot" shows it, rebuilt with the same strip
  function for older rows; the stored snapshot sits behind
  "Stored input".
- The 5-minute-bar provenance stores as `last_5m_close`/`last_5m_time`,
  so it can't read as the signal bar's close; v1–v3 rows untouched.

## 2026-11-03 — Option outcomes: verdicts get their 5-session results back

- `_fill_option_touches` read `entry_limit`/`entry` off each stored
  option, but the v2/v3/v4 option column never carries one — every
  recorded `option_touches` was all-null and every options-based verdict
  drafted "mixed or unknown outcomes" / `unclear`. The limit is one
  value per decision: it now comes from the snapshot's
  `constraints.entry_limit`, falling back to an option's `stop + risk`
  (the stored risk is limit − stop per share).
- Migration `l2m3n4o5p6q7` clears the all-null touch dicts so the worker
  recomputes them, and drops the draft verdicts written from them (42 on
  the live data) so the scan rewrites them with real outcomes. Confirmed
  verdicts keep Marc's call.
- `verdict_facts` falls back to an option's stored `rr` when no
  per-option entry exists to recompute it.
- The verdict scan now waits for `option_touches` on decisions that
  offered options — the 5-session result is not complete without them —
  instead of drafting on missing touches; and the touch fill marks
  no-option rows done (`{}`) and leaves a null touch with a warning on
  unparseable prices rather than aborting the pass.
- The touch scan now skips the fill bar itself, per the spec's "after
  the fill bar": a bar that dips to the limit and to a stop in the same
  five minutes no longer counts as a stop touch.

## 2026-11-03 — Decision v4 review; Decision v5 (PR 37)

- Review of the v4 prompt and the KO call of 3 Nov 15:46 NY: `docs/analysis/2026-11-03-decision-v4-review.md`.
  - KO was held into the night at −0.62R with confidence 0.65 against a neutral probability of 0.12, its stop within a typical overnight gap.
  - The entry was made in a sideways, high-volatility market against the prompt's trend rule.
- **Decision v4 → Fixes of 3 November** (code, kickoff `fix-v4-2026-11-03.md`):
  - sideways counts in `thesis_holds`;
  - `previous_decision` from the same context, with the final action and reason;
  - on-symbol headlines first;
  - Decimal stop groups;
  - company names from the pool for `about_symbol`;
  - an earnings status;
  - the LLM inspector shows the snapshot as sent;
  - clearer last-bar labels.
- New section **Decision v5** (PR 37, kickoff `pr-37.md`):
  - the expected-value entry gate (`decision.entry_gate`, `decision.min_ev_r` 0.25, per-option `min_confidence`) replacing the 0.55 floor for v5;
  - the trend gate in code;
  - snapshot fields for the last cycle, the overnight gap, target distance, price since the signal bar, and a fifth thesis check;
  - prompt v5 with a hold-or-exit rule from the neutral probability, an overnight rule, honest odds for far targets, "a skip is not free" and model-written fields as data;
  - warnings `hold_below_breakeven` and a wider path claim;
  - a Needs you item for an outlier overnight hold at a loss.
- New prompt `backend/prompts/decision.v5.md`, not in the manifest until PR 37 wires it. Config blocks come with the code (rule 7).
- Build plan item 37. Shadows to start after PR 37: F (v5), G (v4 + ev gate), H (no 10:46 entries).

## 2026-11-03 — Ask: the number check sees tool-result strings and magnitudes

- Tool results serialize every money and price field via `_dec` → `str`,
  but `_numbers_in` only collected `int`/`float`/`Decimal` — so figures
  like `if_stop_usd` never reached the known set and correct answers
  were flagged "not found in the data". Strings that parse as numbers
  now count.
- The check also matches by magnitude: a loss stored as `"-24.15"`
  verifies "USD 24.15 loss" in the answer text, rounded as before. The
  check attests the number was in the data, not the sign of the claim.

## 2026-11-03 — Settings: the Ask section reaches the form

- `GET /api/settings` never returned the `ask` section — `_settings_form`
  listed every editable section except it (PR 36 wired the save path but
  not the read path). The Ask rows rendered empty, and the first edit of
  an Ask control crashed the page diffing `settings.ask` against a
  section the response never sent. `reviews.prompts` was missing the
  same way. The form now returns `ask.enabled`, `ask.model`,
  `ask.monthly_cost_cap_usd`, `ask.max_cost_per_answer_usd`,
  `ask.keep_days` and `ask.instructions` with the config-example
  defaults, plus `reviews.prompts.<task>` with the review-service
  defaults. A test walks every editable section against the form so a
  third gap can't slip through.
- The client-side settings diff tolerates a section present in the
  edited form but missing from the saved tree — each leaf diffs against
  `—` instead of throwing — and Ask changes label and badge correctly.

## 2026-11-03 — Gaps at the source (SPEC §101)

- `schedule.history_missing_tolerance` (3) now matches the spec: a
  daily session counts as missing at the source only when the fetch for
  that period succeeded and returned no bar — recorded in the new
  `daily_source_gaps` table; a failed fetch writes no row, and a bar
  that arrives later clears the mark. Never forgiven among the last 5
  sessions (they feed prior-session levels, ATR and the gap check); 5m
  coverage stays strict; `0` restores the old behaviour.
- The Universe screen shows forgiven sessions — "warmed · 1 daily bar
  missing at the source: Thu 12 Mar" — and each session is logged at
  INFO the first time it is forgiven.

## 2026-11-03 — Signals and indicators audit fixes

- The order-time ATR is now the same ATR the snapshot computes: `indicators.atr_period` on `schedule.history_sessions` of complete 1h bars, instead of a `signals.` section that never existed over 5 sessions. Approve's fresh-quote gate and adopted-position stops can no longer disagree with the decision-time value just because they saw less history.
- `config.example.yaml` gains the missing `indicators:` section — every indicator period that was only a code default (EMA 20/50, RSI 14, ATR 14, OBV 20, RS 20, SMA 50/200, 60-session ATR percentile) now lives in config, as every default from the spec must. `schedule.history_missing_tolerance` (3) lets sessions with no source bar (halts, feed gaps) count toward history warmup so a single missing day can never leave a symbol "Warming up" forever.
- A missing `decision.stop_atr_min` in the active settings no longer crashes mid-order path; the fresh-quote gate uses the same default as the decision validator. Malformed numeric settings anywhere in the signal path fall back to their defaults and log a warning instead of aborting the cycle (`AppConfig.get_int` / `get_decimal`).
- The market-regime volatility label now honours its documented behaviour: an ATR percentile is only computed over the full configured window, so a short SPY history reads `unknown` rather than mislabeling a partial window and wrongly triggering or skipping the Down+High entry block. The per-symbol daily ATR percentile follows the same rule.
- Only complete 1h bars feed the indicators — the same rule the shock brake already applied. An incomplete bar means some of its 5-minute bars never arrived; it is dropped from EMA, RSI, ATR, OBV, level and signal-bar computation.
- Relative strength vs SPY pairs closes by bar time instead of by index, so a data gap on either series can no longer slide one against the other.
- The opening gap is only computed when the first cached 5m bar is the session-open bar, and "previous close" means the last session completed *before* today — after the close, today's own bar is no longer used as the gap base. The watchlist day change uses the same corrected base.
- Crossed books (ask below bid) are no longer "plausible" quotes, and a trade stamped in the future reads `price_suspect` instead of passing freshness. A locked book stays plausible.
- Alpaca daily bars take their session date from the timestamp's date part rather than a UTC→New York conversion that files a `T04:00:00Z` bar one day early in winter. Malformed bars (non-positive or non-finite prices, high < low, open/close outside the range, negative volume) are dropped and logged instead of reaching the indicator math.
- A flat price series reads RSI 50 (neutral), not RSI 100.
- `decision.options.max_options` above 6 is honoured up to 26 option ids (A–Z) instead of silently dropping the extras. Stop/target and v3 level merges re-check a level-based replacement against the remaining kept levels, so two kept levels can no longer sit inside the dedupe distance.
- Stored input snapshots carry the exact indicator values (`indicators_precise`) for shadow rule re-evaluation; the block is stripped from every prompt version's send path. Shadow rules can no longer pass a boundary check the live evaluation failed on a rounded display value.
- Level timestamps for daily swings, prior-session and session markers are New York-aware like the 1h swings; the session marker uses the New York date of the last 5m bar.
- `rules.entry_cycles` is now evaluated on the live path too — the cycle in effect is the last scheduled cycle at or before evaluation time.
- The "since open" shock-brake move only counts when the session-open bar itself is cached/complete; a later first bar no longer re-anchors the move mid-session.
- `SignalService.is_last_cycle` delegates to `SessionClock` instead of duplicating it, and the watchlist's per-symbol last action is one batched query instead of one per symbol.
- Leftovers of the retired day-trade rule are gone: the dead `exit_blocked_same_day` guard and stale comments that still described the rule as enforced (the `day_trades` table and `deferred_exit` column stay for history — migrations are never edited).

## 2026-11-03 — Trading-path audit hardening

- Approving an entry no longer evaluates the risk gates on a stale read snapshot: the approval's session commits before the network calls, so the gate context sees positions and orders committed after the request started.
- A position check no longer sends a market sell when Alpaca confirms the symbol is flat; a confirmed-flat read returns early instead of falling back to the stored quantity. The stale flat-symbol cache no longer skips the fresh broker read.
- Stop tightening picks the newest live stop leg instead of crashing the cycle when more than one open stop row exists.
- Every broker order call — entries, exits, stop replaces, flatten sells, breach/OCO/protective and adopted stops — holds the shutdown order slot, so shutdown cannot cut a submission between send and store (hard rule 16).
- The shock brake's since-open SPY move requires a fresh quote timestamp; a stale quote can no longer fabricate a market move. Paper reset confirms no orders remain open after `close_all_positions`, matching flatten's cancel-confirm discipline.
- `get_open_orders` fails loudly at the 500-row page limit on both the open and closed queries instead of silently missing live stop legs. Fractional broker quantities are logged before truncation.
- Read calls through the rate limiter retry only transient failures (timeouts, 5xx, 429); permanent 4xx errors surface at once.
- "Confirm it is you" burns the TOTP step like sign-in does, so a used code cannot be replayed within the drift window. Expired pending sign-in tickets are pruned. Route coverage at startup uses the same guarded-path check as the session middleware.
- The Host check rejects a non-numeric port, and `/api/universe/import` is JSON-only like every other write (the larger upload cap stays). Client-supplied reason and note text is stripped of control characters before it is stored or logged.
- News statuses merge per cycle instead of being overwritten by later fetches, and a candidate that reaches a model call without prefetched headlines gets its own news fetch first.
- Entry-option risk and reward-to-risk come from `trade_risk`, the one shared risk function. `client_order_id` clamps the instance segment to stay inside Alpaca's 48-character limit. Adding a symbol or importing a CSV that loses a concurrent-add race returns 422 instead of 500.

## 2026-11-03 — Warm-up: gaps at the source

- From PR 100. One daily bar that never existed at the source (a halt, a feed gap) kept a symbol "Warming up: 259 of 260" forever.
- **Adding a symbol → Gaps at the source:**
  - up to `schedule.history_missing_tolerance` (3) sessions without a daily bar at the source count as loaded;
  - only when the request succeeded and returned no bar, and never in the last 5 sessions;
  - 5-minute history stays strict;
  - forgiven sessions are shown on Universe and logged once.

## 2026-11-03 — Ask the agent: config defaults and the gpt-6.1-sol model (PR 36)

- `config.example.yaml` gains the `ask` block: enabled, model `claude-sonnet-5-5`, $15 monthly cap, $0.25 per-answer cap, 90-day chat retention, 60k context tokens, 8 tool rounds, 6k tool-result tokens, 90s answer timeout, 10 questions per minute, 100 notes and empty standing instructions.
- `gpt-6.1-sol` joins the models list at $2.00 input / $10.00 output per million tokens with $0.10 cached input, cutoff 2026-04-30 and `api: responses` (OpenAI requires the Responses API for its tool calls). Claude models gain `cached_input` prices for prompt-cache accounting.

## 2026-11-02 — Decision drawer correctness and accessibility fixes

- Outcome R uses the position's initial stop, not its moving stop, in live and backtest decision details.
- Option-based decisions resolve proposed stop and target from the chosen or best offered option. Held decisions retain their stored position levels.
- The drawer ignores superseded requests, allows failed loads to be retried, and retains stale data when a refresh fails.
- Keyboard focus stays inside the drawer and returns to the opener. Background content is inert while it is open.
- V4 held decisions show position facts, neutral probability and scheduled exits. Confidence wording follows the decision context and model action.
- Code-check details remain visible. Warning-only checks are distinct from failed gates. Tables scroll within the drawer, long conflicts wrap, and footer actions have full-size targets.
- Verdict actions prevent duplicate submissions and show save or refresh failures.
- A second pass fixes exit-order summaries and split-fill totals, keeps the price line visible outside the level range, and uses the current price for held decisions.
- Backtest links never fall through to live decisions or link to live positions. Missing outcome data is not labelled complete. Decision and backtest decision IDs outside SQLite's positive integer range return 422.
- Open drawers refetch on relevant live-update topics, reconnects and the existing disconnected polling fallback. Refresh failures retain the displayed decision and offer Retry. Focus returns inside when a rerender removes the focused control.
- Thesis comparisons do not colour unavailable checks as adverse. The v4 thesis fact now counts momentum changing from mixed to weak, as specified; it remains a fact, not an exit gate.
- New live-update subscribers cannot consume a debounced topic before already-mounted subscribers receive it. Updates with no subscribers remain available to later subscribers.
- Held v2 drawers show the stored R-so-far when the richer v3 position block is absent. An empty held-stop list no longer claims the model was not asked. Position-size rows keep the "changed by code" marker visible.
- The signal-stack label reports the last closed 1h bar for position-check times too (previous hour's :30), the selection block drops a stray last-row border, and missing money shows — instead of $—.
- The footer reports replacement stop legs on tighten decisions instead of "No order recorded".
- Headlines without an `about_symbol` flag no longer claim "not about" the symbol, and long level notes truncate instead of overflowing.
- Option rows tolerate a missing `levels_in_way` field, and unknown earnings data shows "earnings unavailable" instead of disappearing.

## 2026-11-01 — Ask the agent: memory written by Marc, checked numbers

- **Model:** Ask uses a stronger model than decisions. Recommended: `claude-sonnet-5-5` (default) and `gpt-6.1-sol`, both $2/$10 per million tokens. Budget: `claude-haiku-5-5`, `gpt-4.1-mini`. The eval picks the default between the two recommended. `gpt-6.1-sol` joins the models list with PR 36, after its price and cutoff are checked.
- **Cost:**
  - about $0.05–0.10 a question;
  - a cached prompt prefix;
  - a per-answer cap `ask.max_cost_per_answer_usd` (0.25) that ends the lookups and answers with what it has;
  - the monthly cap raised to $15.
  Settings shows this month's spend, the number of questions and the average per question.

- **Ask the agent → Memory.** Nothing is remembered silently.
  - **Standing instructions** (`ask.instructions`, 1,000 characters) in Settings → Ask the agent, added to every chat. They shape how the agent answers, never what it may do.
  - **Saved notes:** "Save as note" under an answer, or written by hand; listed in the drawer with Edit and Delete; kept until Marc deletes them.
  - A new `notes` tool. The agent treats a note as a dated conclusion and checks it against today's data. It never writes notes and doesn't read earlier chats.
- **Checked numbers:** each answer stores the numbers it took from lookups, so a reopened thread can reuse them without being flagged.
- Storage: `ask_notes` and `ask_messages.checked_numbers`. API: the notes routes. Eval: three more properties.
- `prompts/ask.v1.md` gains "Marc's preferences and notes". It is still unused and not in the manifest, so this is not an edit of a used prompt.
- Design:
  - `AskDrawer` has "Save as note" in the answer footer;
  - `AskStates` gains a Notes panel and a phone answer that uses a note;
  - `Settings` gains the Ask the agent group.
- Kickoff `pr-36.md` updated.

## 2026-11-01 — Ask the agent (PR 36)

- New section **Ask the agent**: a chat drawer on every screen, with that screen as context, that answers questions from the app's own data.
  - **Conversation:** threads kept 90 days, streamed lookup steps and text with Stop, follow-ups with the whole thread, fixed suggestions per screen.
  - **Answers:** short answers first, reference chips to the records used, and a number check that marks any number not found in the data.
  - **Tools:** 19 read-only tools over the existing services (status, positions, orders, decisions, cycles, performance, trades, insights, strategies, readiness, reviews, logs, model calls, settings without secrets, stored market data, spec passages, a Decimal calculator). Each is bounded and returns references.
  - **Model and limits:** `converse` with tools and streaming in the one model interface; `ask.model` (gpt-4.1-mini); its own $5 monthly cap and rate limit; `llm_calls` source `ask`.
  - **Safety:** read-only by construction, no secrets in any tool result, untrusted text treated as data, strict Markdown rendering.
  - **Storage, settings and eval:** `ask_threads` and `ask_messages`; Settings → Ask the agent; `make eval-ask` with about 25 questions.
- New prompt `backend/prompts/ask.v1.md`, not in the manifest until PR 36 wires it. The `ask` config block comes with PR 36 (rule 7).
- Header: the Ask button on every screen. Out of scope: actions from the chat (later, behind "Confirm it is you"). Build plan item 36. Kickoff `docs/kickoffs/archive/pr-36.md`.

## 2026-11-01 — Needs you and positions at a glance (PR 35)

- From the UX audit of 1 November (`docs/analysis/2026-11-01-ux-audit.md`).
- GUI:
  - a **Needs you** card on the dashboard: one list, most urgent first, of what waits for Marc. Each item has one action and leaves when done;
  - a health line under it;
  - a "Needs you · n" chip in the header on every screen.
- **Positions at a glance:**
  - a stop-to-target bar and the distance to the stop in ATR, amber under `gui.near_stop_atr` (1.5);
  - open positions moved up on the dashboard;
  - on Positions: a totals line, four facts per position, and Alpaca legs folded into a line that opens on a problem.
- **Phone:** below 820 px a sticky top bar with the menu replaces the sidebar; no sideways scroll at 390 px.
- Build plan item 35. Kickoff `docs/kickoffs/archive/pr-35.md`. `gui.near_stop_atr` comes with PR 35 (rule 7).
- Config: `gui.near_stop_atr: 1.5` added to `backend/config.example.yaml`.

## 2026-11-01 — Decision v4: best option and conflict checks clarified

- From the PR 34 review. `best_option_id` never invalidates an answer:
  - on an enter, code sets it to the chosen option (`best_option_mismatch` when the model named another);
  - on a skip, a missing or unoffered id leaves the skip standing, stores null with `best_option_missing`, and keeps the decision out of the skip calibration;
  - held answers store null.
- `unresolved_conflict` applies to enters only. The eval's `conflicts_resolved` predicate does the same; held rows are measured with `conflicts_on_adverse`.

## 2026-11-01 — Decision v4: code and config (PR 34)

- New prompt `backend/prompts/decision.v4.md` and its manifest entry; `decision.v4` becomes a registered prompt version usable by live, shadows, replays and `make eval-decision`.
- `backend/config.example.yaml` gains the `decision.v4` block: `max_headlines`, `max_level_distance_atr`, `flip_min_move_atr`, `max_confidence_above_anchor` and `checks_gate` — the last also a shadow strategy override.
- `checks_gate` gates shadow candidates in both selection modes, matching live; eval snapshots rebuild earnings and `checks_at_entry` like the replay does.

## 2026-11-01 — Decision bookkeeping fixes (claim checks, shadow snapshots, thesis ids)

- Claim checks: only stop/target attributions are claims. A sentence naming a level id without the words stop or target (e.g. "levels L2 and L1 in the way") is no longer marked an unsupported claim — IBM call 1734's conflict failed the check on exactly that. Sentences that do attribute a level to the stop or target are checked unchanged.
- `shadow_decisions.input_snapshot` is stored on every shadow row now, including rows built on a live decision's snapshot — the spec's "what the shadow decided on" record. Missed, no-call and rules-only rows had NULL before.
- `rewrite_thesis_ids` replaces the id token only when the kind words already precede it: a stored thesis "the 1h swing low L16" now becomes "the 1h swing low (317.61)" instead of "the 1h swing low the 23 Oct 10:30 1h swing low (317.61)". Ids without the kind named in the same clause still get the full description.

## 2026-11-01 — Positions: one row per entry order

- Root cause of the 30 Oct SHEL duplicate: the fills sync opened the row at 18:46:43, a stale flat reading closed it a second later while the fill was still settling, and the adopt path re-created the same 18 shares as `adopted` — no decision link, no target. Positions now carry `entry_order_id` (the broker entry order they were opened from) with a unique index; the fills pass checks it before inserting and the adopt path skips a symbol whose position row is open or was closed inside `fill_settle_seconds`.
- Alembic `i1b2c3d4e5f6` backfills the link, merges duplicate rows sharing one entry order into the surviving open row, and restores the survivor's stop/target from the bracket legs. Applied to the local DB it repairs SHEL exactly as intended: one open row, entry decision 2909, target 110.78 (the bracket's target leg order 32 is live at Alpaca, status `new`).

## 2026-11-01 — Decision v4 (PR 34)

- From the analysis of 31 October (`docs/analysis/2026-10-31-decision-v4-candidates.md`):
  - confidence isn't a probability (enters at 0.6–0.7, skips at 0.0 or 0.4, held about 0.3 above neutral);
  - 11 hourly flips and about 28 reversals in a session on unchanged inputs;
  - conflicts listed and then ignored;
  - "clear path" into nearby levels survives in v3;
  - 0 exits in 233 held calls;
  - about 35–40% of headlines are about another company.
- New section **Decision v4**:
  - snapshot v4 adds `previous_decision` with code-computed `since`, headlines with `about_symbol` and `new`, capped headlines and levels, `stop_group`, `checks_at_entry` and `thesis_holds`, time held and session minutes left;
  - prompt v4 defines confidence for every action and adds `best_option_id` on a skip, held confidence anchored to `neutral_probability`, conflicts with `outweighed_by`, `what_changed`, news effects, and path and exit rules;
  - code adds the flip-flop guard, confidence sanity and path-claim warnings, `unresolved_conflict`, the `checks_gate` strategy override and `make eval-decision`.
- The trend rules and the room filter stay as in v3. One rally week isn't enough to drop or enforce them; a `checks_gate` shadow can test the enforced version.
- New prompt `backend/prompts/decision.v4.md`, deliberately not in `prompts/manifest.json`; PR 34 adds the entry with its code. The `decision.v4` config block comes with PR 34 (rule 7).
- Reviews: the weekly, briefing and verdict prompts become editable in Settings → Reviews, next to their models; each select lists only that task's versions (PR 34).
- Build plan item 34.
- Analyses added to `docs/analysis/`: the dashboard UX review (24 Oct), the security audit (27 Oct), the live data and decision reports of 29 Oct, the model-call and live data reports of 30 Oct, the entry-selection report and the repository scan of 31 Oct. The key hints in the 30 Oct report are redacted.

## 2026-11-01 — Settings: Server section shows a description off-VPS

- The Settings section menu listed "Server" on every build, but the section rendered only when `SA_AUTH=required` (the VPS build) — the menu link pointed at an anchor that did not exist. The section now always renders and, like Access, collapses to a one-line description locally: the health rows, backup buttons and `/api/server` fetch stay VPS-only.
- Reconcile: the flat-position check now honours "Fills still settling" like the quantity check does — a DB position missing from Alpaca's positions response while its entry fill is inside `fill_settle_seconds` is deferred (INFO `reconcile.settling`), not closed as `flat_without_fill`. Real incident on 30 Oct: SHEL's entry filled at 18:46:43 and the 18:46:44 reconcile saw an empty positions endpoint, closed the just-opened row as `broker_close`, then adopted the same 18 shares a second later — two spurious CRITICAL events and a phantom closed position.
- Backup job logs: an expected `OffsiteError` (missing credentials, restic absent, endpoint refused — messages written for Marc) now logs its reason on one WARNING line ("monthly restore test failed: no backup password or storage credentials stored") instead of an ERROR with a bare "failed" plus a traceback. Unexpected failures keep `logger.exception`. Applies to the restore test and the daily offsite upload, in both `ops.service` and the `run_daily_backup` chain. Note the level change: these expected failures no longer count as unreviewed errors for Readiness.

## 2026-10-31 — Stream drops and stale-chunk reload fixes

- Stream health: a lost trade-updates socket that reconnects cleanly stays at the SDK's WARNING. "The first failed connection of an outage is logged at ERROR" (docs/SPEC.md) now means a failed (re)connection attempt, not the drop itself — a self-healing blip no longer logs ERROR or sends a "New error" notification. A failed reconnect still logs ERROR and notifies.
- Frontend: a stale lazy chunk after a rebuild reported two errors — the `vite:preloadError` handler's and the React boundary's, because `preventDefault()` resolves the lazy import to `undefined` and React throws before the reload lands. The boundary now skips its report while the chunk reload is pending, so one stale chunk logs one error.

## 2026-10-31 — Review attribution, remainder — implementation

- Verdict facts per spec: `checks_at_decision` is recomputed with the decision.v3 rules on the stored input (v3 rows keep their stored checks; v1/v2 get them too — trend 1h and daily, regime, room in ATR), `execution` reports `filled`/`not_filled`/`not_sent`/null, and `change_text` carries the failing check's human detail.
- The draft label follows the SPEC table: an enter is judged on the chosen option's (or the real trade's) first touch, a model skip on the offered options' touches, and only optionless v1 skips on the 5-session move — the trade-R shortcut is gone.
- Weekly facts count model skips only (a code-blocked enter is never a model skip) and confirmed verdicts in the three groups — executed enters, enters blocked by code with their reasons, and skips — plus thesis contradictions and the `sample` block (closed trades, model skips with an outcome, confirmed verdicts; week and run).
- `verdicts.prompt_version` records the review prompt (new Alembic revision on `c3d4e5f6a7b8`); `/api/verdicts` and the decision verdict now carry the model's call, code's change with its reason, the outcome line, the thesis check and `label_by_code`.
- Briefing: `text` stores only the model's sentences; `status_line` is code's and shows above the text in the GUI and email; a leading copy in older rows is stripped on read, never rewritten.
- Known prompt versions: saving settings with `decision.prompt` without a contract is refused `unknown_prompt_version`, as is a backtest strategy naming one; the replay CLI already raised.
- Kickoff `docs/kickoffs/archive/pr-32.md` steps 6 and 8 and the listed test gaps; the read-only live-database check and a frontend pass under the design screenshot remain open.

## 2026-10-31 — Analysis: decision.v4 candidates

- `docs/analysis/2026-10-31-decision-v4-candidates.md`: ranked,
  evidence-backed change list for decision.v4, re-measuring the
  30 October findings on 1,055 called decisions (19–30 Oct, live +
  shadow) with recomputed option touches. No behaviour change.

## 2026-10-31 — Settings saves restored — implementation

- `validate_settings` lost two branches in PR 29: `reviews.models` was overwritten by the quiet-hours cleaner, so every model save failed on a quiet-hours message; and `notifications.quiet_hours` had no branch left, so every quiet-hours save failed as "not a number". Both branches are back.
- `config.example.yaml`: the notification event `earnings_exit_tomorrow` was renamed `exit_deferred_or_earnings_exit` in PR 26, but the defaults kept the old key — the settings form echoed it into every channel save, which then failed as unknown. The key is renamed in the defaults and the form now drops event keys the code no longer knows, so an older saved tree can't poison a save either.
- `test_settings_saves.py` saves every reviews.models and quiet_hours case through the API and walks every key the Settings page can send (a dropped validation branch now fails the suite); `settings-save.e2e.ts` saves both through the page.
- The Settings page had the same PR 29 gap on its side: `diffSettings`/`diffMap` know the other dict fields (`universe.screen`, `notifications.quiet_hours`, `notifications.events`) but not `reviews.models`, so a review-model change counted no unsaved change and was dropped from the save body. The leaf diffs are in, with labels for the three model rows.
- `AuthService.scrub_values` — one of the log scrubber's secret providers — logged every failure, and the scrubber filtered that record through the same provider: an unbounded loop at shutdown (backend CI hung on it). `_all_secrets` now falls back to the last collected values when a provider emits a record mid-collection, and `scrub_values` warns once per failure episode.
- Kickoff `docs/kickoffs/archive/fix-scan-2026-10-31.md`, step 1.

## 2026-10-31 — Repository scan fixes

- From the repository scan of 31 October. Its main findings:
  - review models and quiet hours can't be saved (a regression from PR 29);
  - four auth routes return 500 on a bad body;
  - backtests can kill a reused process id;
  - "Confirm it is you" and Change password allow unlimited guesses;
  - a CSV import calls Alpaca while it holds a write lock;
  - working entries without a limit price count as zero risk;
  - several smaller issues.
- Sign-in and access: **Confirm and password change** count failed attempts per session. After 5 in 15 minutes the session is signed out and the warning is sent.
- API keys: a secret shorter than 8 characters is refused at save.
- A body that is not JSON at all still answers 400 `bad_request` (logged at WARNING); typed route bodies only changed the parsed-but-invalid cases to 422 `invalid`.
- An app's secret providers are unregistered at shutdown, and `install_secret_scrub_filter` no longer stacks a second scrub filter on a handler that already has one. Registered per app and never removed, a stale `AuthService.scrub_values` keeps running on every later record in the process, fails on its dead store once per failure episode, and that warn is a stray "logs" publish on whatever app is still live (it flaked `test_websocket_needs_session_and_own_origin`: an `update` beat the `pong`); filters that survive their app made one extra `_all_secrets` collection per record per boot.
- Kickoff `docs/kickoffs/archive/fix-scan-2026-10-31.md`. The rest are code bugs against the existing spec and AGENTS.md.

## 2026-10-31 — Misfire grace on every scheduled job

- Every scheduler job now carries `schedule.misfire_grace_seconds` (120 s) and `coalesce=True`; only `backtests-tick` keeps its own 10-second grace. Previously jobs without an explicit grace fell back to APScheduler's 1-second default, so a brief event-loop stall silently skipped the run — a one-off warning for the 1-minute `readiness-tick`, but a lost day/week/month for the `daily-data-refresh`, `daily-db-backup`, `daily-briefing`, `weekly-review` and `restore-test` crons.
- `scheduled-cycle-audit`, `calendar-retry`, `daily-data-refresh` and `daily-db-backup` also gain `coalesce=True`; several misses inside the grace merge into one run, never a queue of catch-ups.
- `test_scheduler.py` asserts every job carries the configured grace.

## 2026-10-31 — Entry selection: implementation (PR 33)

- `config.example.yaml` gains the `selection` block (`mode` rank, `confidence_weight` 0.5, `last_slot_min_ev_r` null) — versioned settings, strategy overrides and the Settings → Trading rows.
- `selection.mode: expected_profit` collects candidate answers, scores enters (expected R × dollars at risk via the shared sizing), picks the best fitting set under the unchanged limits and submits it in expected-profit order with the next-best fallback once; the rest are `blocked`/`not_selected`. `mode: rank` keeps today's path exactly.
- `decisions.selection`, `cycles.selection` and `shadow_decisions.selection` store the score and the set result; `not_selected` counts as a code block of a model enter for verdicts and filters on its own in the inspector.
- `make replay-selection` compares both modes on stored cycles with a slot, sector or open-risk block, judged by `outcomes.option_touches`. The decision drawer shows the selection gate row and the 'Selection in this cycle' block; Settings → Trading offers the three keys.
- Live stays `selection.mode: rank` until Marc switches it after the replay and a shadow.

## 2026-10-31 — Entry selection (PR 33)

- From the 31 October report on how entries are chosen when more pass than there are slots: there is no selection step. Entries are gated and sent in rank order, so the first ones to pass take the 4 slots, and candidates after the 10th call are never seen. Rank (20-session strength against SPY) says nothing about the offered trade or about dollars at risk. The 20% max-position cap cuts the risk, and so the profit, on tight-stop stocks.
- New section **Entry selection**. With `selection.mode: expected_profit`, code collects all candidate decisions of a cycle and scores each enter as expected R × dollars at risk. Confidence is moved halfway to break-even (`selection.confidence_weight` 0.5). Code then sends the set with the highest total that fits the existing limits, and the rest get `not_selected`. Model calls go in pre-score order (dollars at risk). `make replay-selection` compares both modes on stored cycles.
- `selection.mode` defaults to `rank`, so live behaviour is unchanged until Marc switches it after the replay and a shadow.
- Cycles at scale and Working entry orders refer to the selection mode. Build plan item 33.
- The `selection` config block comes with PR 33 (rule 7).

## 2026-10-30 — Live fixes of 30 October — implementation

- `docs/kickoffs/archive/fix-live-2026-10-30.md` lands in code: the "Live decisions failing" critical event, banner and notification (once per episode, cleared by the first good call) with the provider key marked failing on a 401/403; "Trade opened" fires once when the entry order is final, with the filled quantity; reconcile defers the quantity check while an entry order works or a fill is fresh; GUI broker reads answer the last reconciled values, marked stale, past the timeout; a held-position tighten accepts an older last trade and a still-blocked tighten shows as changed by code (`price_stale`/`price_suspect`); stream reconnect errors log once per outage then once per 10 minutes, and a long outage degrades the stream only in session; no chart request without a symbol; screenshot specs write to a temp dir unless `SCREENSHOT_DIR`.
- `config.example.yaml`: `api.broker_timeout_seconds` (5), `execution.fill_settle_seconds` (10), `execution.tighten_quote_max_age_seconds` (300), `notifications.events.live_decisions_failing` ([browser, email]).
- Logger names outside `app`/`frontend` log at WARNING and up; the app's own bare logger names (`model.*`, `broker.*`, `data.*`, `rules`, `risk`, `check`, `db`, `api`, `backtest.worker`) moved under `app.*`. Reconnects announce `app.broker.stream` INFO with the outage length.

## 2026-10-30 — Decision reload keeps saved model credentials

- Reloading decision settings or saving a model key now keeps the shared, credential-aware model client. Previously reload replaced it with a client without the secrets store, causing model calls to use the `missing` placeholder even when the saved key passed its Settings test.
- Regression tests cover both providers, cached and uncached adapters, saved and missing keys, key replacement and removal, and prompt refresh. No spec or design change.

## 2026-10-30 — Live fixes of 30 October

- From the live-data report of 30 October. A settings reload dropped the model keys (fixed in #64), and two cycles ran with no model answer while only a per-symbol warning went out. The PG entry notification said 10 shares for a 12-share position. Two critical `qty_mismatch` events fired while PG was still filling, and cleared themselves a second later. Two GUI reads waited 87 seconds on Alpaca. An HD tighten was blocked as `price_stale`, visible only in `gates_result`. httpcore and urllib3 DEBUG lines still filled the log, and the overnight stream reconnect loop logged an ERROR every 30 seconds for an hour.
- Decision layer: **Live decisions failing** (critical, banner, email). It fires when every live call of a cycle fails or any call gets a 401 or 403; an auth error also marks the key as failing.
- Market price: a tighten accepts a last trade up to 5 minutes old (`execution.tighten_quote_max_age_seconds`). A blocked tighten is shown as changed by code.
- Alpaca integration: **Broker reads from the GUI** wait at most 5 seconds (`api.broker_timeout_seconds`), then fall back to the last reconciled values, marked stale. **Fills still settling**: no quantity comparison while an entry order is working or within 10 seconds of a fill (`execution.fill_settle_seconds`).
- Stream health: reconnect errors are logged once per outage, then once per 10 minutes with a count. An outage of more than 5 minutes in a session marks the stream `degraded`.
- Notifications: "Trade opened" waits for the entry order to be final and shows the filled quantity. New row "Live decisions failing".
- Logging: every logger outside `app.*` and `frontend` logs at WARNING and up, not a fixed list.
- Kickoff `docs/kickoffs/archive/fix-live-2026-10-30.md`. No design change: the GUI uses the existing banner and the stale-data pattern from `States.dc.html`.

## 2026-10-30 — Analysis: IEX quote plausibility

- `docs/analysis/2026-10-29-quote-plausibility.md` plus the reproducible script `scripts/analysis/quote_plausibility.py` (read-only, `mode=ro`). No behaviour change.

## 2026-10-30 — Ops: alerts, log volume and small fixes — implementation

- `docs/kickoffs/archive/ops-alerts.md` lands in code: the "New error" warning notification (one per ERROR fingerprint per session, link to Logs), third-party loggers at WARNING unless `logging.library_debug`, no `/api/chart/` call without a symbol, and no repeat broker ask for a position already found flat in the same pass.
- `config.example.yaml`: `notifications.events.new_error: [browser, email]` — the spec's on/on default for the new row.

## 2026-10-30 — Review attribution (PR 32) — implementation

- The 2026-10-30 spec entry above lands in code on `feat/pr32-review-attribution` (stacked on PR 31). `config.example.yaml` gains `reviews.min_closed_trades_for_suggestion` (10) and `reviews.prompts` moves to `weekly.v2` / `verdict.v2` (briefing stays v1). No new spec behaviour beyond that entry.

## 2026-10-30 — Review attribution (PR 32)

- From the model-call analysis of 30 October (1,067 calls, 19–29 October): 32 of 99 verdicts judged a skip that code had made out of a model enter, because the verdict facts held only the final action; `verdict.v1` called a skip followed by +1.1% a good call ("avoided a small gain"); the one weekly review suggested a rule change on 1 closed trade; the briefing showed its status line twice; and an unknown prompt version silently used decision.v1's contract.
- Outcome tracking: **option outcomes**. For decisions with options, which of each offered option's stop or target was touched first in 5 sessions, traded or not (`outcomes.option_touches`).
- Reviews → **Verdicts**: verdicts judge the model's call; facts carry the model's action, code's change and its reason, execution, the checks at decision time (v3 rules) and option outcomes. The draft label is set by code from an outcome table; the model writes the reason and a thesis check (`consistent`, `contradicts`, `unclear`). Confirmed verdicts are counted per model call.
- Reviews → **Weekly suggestion**: only from `reviews.min_closed_trades_for_suggestion` (10) closed trades in the run, with an evidence line; otherwise "No suggestion yet".
- Reviews → **Briefing text**: the status line is stored and shown once.
- Decision layer → **Known prompt versions only**: no fallback to another version's contract; `unknown_prompt_version` at startup and on save.
- New prompts `verdict.v2.md` and `weekly.v2.md`. They are not in `prompts/manifest.json` yet, so they can't load until PR 32 adds their code and their manifest entries. `config.example.yaml` changes (the new defaults and `reviews.min_closed_trades_for_suggestion`) come with PR 32 (rule 7).
- Not in this change, kept for a decision.v4 once the v3 shadow has data: ignoring unrelated headlines (RTX the company vs NVIDIA's RTX laptops) and saying what each cited headline changed; explaining why a conflict is outweighed instead of "manageable"; a meaning for confidence on a skip.
- Build plan item 32.

## 2026-10-30 — Live fixes: stream, clock check, break-even guard

- Trade-updates stream: the raw_data envelope (`{"stream": "trade_updates", "data": {...}}`) is now unwrapped before parsing — every update since 28 Oct had been dropped at parse. One bad message is logged at ERROR with the scrubbed raw, counted and dropped; the next message still delivers. The parser is tested on recorded paper envelopes for new, fill, partial_fill, canceled and replaced.
- Stream health per spec: an unparseable update, or a fill reconciliation records whose update never arrived inside `execution.stream_update_grace_seconds` (120 s), marks the stream `degraded`. Degraded shows the banner "Order updates delayed: positions are checked every 15 minutes", sends the critical "Trade-updates stream degraded" event/notification once per episode, and clears after `execution.stream_degraded_minutes` (30) without a new failure. Fills from before the run started are offline fills, not stream failures. Settings → Connections shows `degraded`.
- Clock check: `OpsService.check_clock` reads the data client's `fetch_clock` (the broker interface has no clock — the check had been raising AttributeError since 28 Oct). A failing check stores offset null and sends the new "Clock check failing" notification once per New York session.
- Break-even guard: a `tighten_stop` to a stop at or below the entry (locks in 0 R or less) while `r_now` is below `decision.breakeven_min_r` (1.0) becomes `hold` with reason `breakeven_too_early`, shown as changed by code — for every prompt version. The v2/v1 prompts and snapshots are unchanged.

## 2026-10-29 — Break-even guard

- From the decision report of 29 October: live decision.v2 tightened four stops to break-even at +0.13R to +0.40R, each called "locking in profit" because the v2 snapshot marks a stop equal to the entry as `locks_profit`. RTX turned a +0.40R open gain into −$2.07 within two hours.
- Decision layer: a new **break-even guard** for every prompt version. A tighten to a stop that locks in 0 R or less is refused below `decision.breakeven_min_r` (1.0 R); the action becomes `hold` with reason `breakeven_too_early`. Decision v3 also stops offering these options.
- Kickoff `fix-stream-clock.md` renamed `fix-live-2026-10-29.md` and extended with the guard and per-message error handling in the stream.

## 2026-10-29 — Stream health, new alerts, library log levels

- From the live-data report of 29 October: every trade update since 28 October was lost (the stream wrapper wasn't unwrapped), while the stream still showed as connected; the clock check never ran; repeating errors were hidden by the 60-minute auto-resolve; and library DEBUG lines filled the log.
- Alpaca integration → Reconciliation: **Stream health**. Unparseable updates, or fills that reconciliation finds without an update within 2 minutes, mark the stream `degraded` with a banner and a critical notification.
- Notifications: "Trade-updates stream degraded" (critical), "New error" (the first time an ERROR fingerprint appears in a session), "Clock check failing".
- Logging: third-party loggers at WARNING unless `logging.library_debug` is true.
- Kickoffs: `fix-live-2026-10-29.md` (urgent, before PR 31) and `ops-alerts.md`. PR 31 now starts after the stream fix. The quote analysis also covers `price_stale`.

## 2026-10-29 — Correction: ACN stop level, entry thesis ids

- Correction to the ACN finding: the entry thesis was right. At entry the stop's level (the 20 Oct 12:30 1h swing low, 238.22) was L13; a cycle later a new level shifted the ids and it was L14. The held decision copied "L13" from the entry thesis.
- Decision v3: level ids in `entry_thesis` are rewritten to this cycle's ids, or to the level's kind, time and price when it's no longer listed, before the thesis is sent. The stored thesis is unchanged. The claim check already catches the stale id.

## 2026-10-29 — Kickoffs tidied

- `docs/kickoffs/archive/pr-30.md`: reads both PR 30 change log entries, and states the label and changelog rules.
- `docs/kickoffs/docs-readme.md` removed: the README rewrite was merged in #46.

## 2026-10-29 — Decision v3 implemented: checks by code, room and break-even (PR 30)

- `decision.v3` is live for strategies that pick it: snapshot v3 (rounded,
  de-duplicated, merged levels, `checks` worked out by code, every
  distance in ATR, `breakeven_confidence` per option), option builder
  version 2 (the room filter and the held stop-option drops), the v3
  output schema (`conflicts`, `data_flags`), the confidence and data-flag
  gates, the order-time room recheck, the held position facts and the
  claim checks. decision.v1 and decision.v2 are unchanged, live still
  runs decision.v1 and no shadow was started.
- The new-shadow dialog offers `decision.v3`; `min_room_atr`,
  `room_level_kinds`, `min_stop_move_atr`, `breakeven_min_r` and
  `held_stop_min_atr` are overridable per strategy.
- `make replay PROMPT=decision.v3` replays stored snapshots through the
  v3 builder without refetching; the stored decision keeps the quote and
  reference provenance the model is not shown.

## 2026-10-29 — Decision v3: held confidence, stop claims, momentum, merged levels (PR 30)

- From a review of a held ACN decision (28 October 15:46, decision.v2, gpt-4.1-mini):
  - confidence 0.65 where no edge gives 0.36;
  - the held decision called the stop's level L13, the id it had at entry, while ids had shifted and it was now L14;
  - weak momentum read as "supporting continuation";
  - two levels at one price.
- Decision v3 gains:
  - `checks.momentum` (relative strength against SPY and OBV);
  - levels within 0.05 × ATR merged into one id with `kinds` (`decision.v3.level_merge_atr`);
  - for held positions, `position.neutral_probability` as the starting point for confidence, `position.stop_level`, and an invalidation that names the level by kind, time and price;
  - the stop and target basis written by code, not by the model;
  - a claim check for a stop or target attributed to the wrong level.
- `prompts/decision.v3.md` updated to match. It has not been used yet, so it can still change.
- The `input_incomplete` warning on that call came from `original_invalidation` being `"unavailable"`, fixed on 29 October.

## 2026-10-29 — Process: spec pull requests and kickoff files

- Spec and design changes now arrive as pull requests from Claude on a `spec/…` branch with the `spec change` label, instead of files copied in by hand.
- Kickoffs live in `docs/kickoffs/pr-<n>.md` (PR 29, 30, 31 and the README rewrite moved there); `docs/DEVIN_KICKOFF.md` is the archive up to PR 28.
- `docs/README.md` rule 9 and `CLAUDE.md` → When changing docs describe the flow. The root `README.md` stays Devin's.
- Design: `DecisionV3Held.dc.html` and its screenshot added to `design/` (they were in the spec package but not yet in the repo).

## 2026-10-29 — Held snapshots: original invalidation filled for decision.v2

- The position block's `original_invalidation` was `"unavailable"` for every decision.v2 entry: the v2 model output has no `invalidation` field — the invalidation is the chosen stop, written by code (Decision v2 → Model output). It is now built from the stored entry: `below <initial stop>: <chosen option's stop_basis> stop`, for example "below 101.74: 2 × ATR stop". The shadow runner fills it the same way. Entries whose stored decision has no `invalidation` and no chosen-option basis keep `"unavailable"`. No migration.

## 2026-10-29 — Exit fills and reconciliation (PR 31)

- From the SBUX and HD stop-outs on 28 October: the trade-updates stream recorded the fills, but only reconciliation closed the position rows, so every routine stop or target exit showed up as a CRITICAL "closed at broker" mismatch. At the same time, that path recorded no event and set no acknowledgment, unlike every other mismatch.
- Alpaca integration → Reconciliation:
  - exits close from the stream with their reason;
  - fills missed while the app was down are explained from Alpaca's order history and closed as `closed_while_offline` (WARNING);
  - real mismatches always record an event, log CRITICAL, show a banner and block entries until acknowledged.
- AGENTS.md rule 5: every datetime is timezone-aware, with ruff `DTZ` on (after the naive/aware `TypeError` in the stop-option builder that failed three cycles and turned the kill switch on twice).
- Build plan: PR 31.

## 2026-10-29 — Process: spec changes are labelled

- New CI job `spec-label`: a pull request that changes a spec file (listed in `.github/spec-paths.txt`) must carry the label `spec change`; spec and design changes also need their changelog entry. A label without spec files gives a warning only.
- `docs/README.md` rule 8 and a line in AGENTS.md → How to work.
- Prompted by PR 28, where the PR 30 spec commit ended up on a security branch unnoticed.

## 2026-10-29 — Process: the example config is the end state

- PR 28 found that `main` no longer booted: the spec package's `config.example.yaml` (with PR 29's `models` and `reviews.models`) had been copied in before PR 29's code existed. PR 28 added the one-release fallback (`prices_for()`, the `reviews.models` walk) to fix it.
- `docs/README.md` gets rule 7: copy only the config blocks for the PR being built; `main` must boot from its own example config at every commit.

## 2026-10-28 — Decision v3 (PR 30)

- From a review of a decision.v2 answer (SBUX, 28 October): the model bought into three levels 0.3–0.7 ATR above the entry, called a mixed daily trend "up", gave 0.65 confidence for a 2R target, and wrote its reasoning after the decision.
- New "Decision v3" section and `prompts/decision.v3.md`:
  - snapshot v3 (v3 strategies only): rounded numbers, no duplicates, plausible-only bid and ask, `checks` (trend 1h and daily labels worked out by code, regime, room to the first level, earnings, news status), `distance_atr` on levels, `breakeven_confidence` per option;
  - room filter: options whose first level in the way is under 1.0 × ATR above the entry are removed (`decision.v3.min_room_atr`, strategy-overridable); with none left, `no_options` with a room reason and no model call;
  - prompt: conflicts and thesis before the action, defined trend rules, confidence starting at break-even, data flags, every field explained, the new name;
  - code checks: confidence ≥ max(0.55, break-even + 0.10) (`edge_too_small`), an enter with a data flag becomes skip (`data_flag`, WARNING and event), room checked again at order time;
  - replay report additions and the path to live (replay, shadow D, Marc decides, Start new run).
- Held positions in v3 (from a second review, SBUX held at 14:46): `locks_profit` only above the entry, plus `locked_r` and `distance_atr` per stop option; stop moves under 0.25 × ATR dropped; break-even only from +1.0R; trails and swing-low stops at least 1.0 × ATR below the price; `r_now`, distances and levels to the target; invalidation always filled by code; `entry_thesis` treated as belief, not fact; `scheduled_exit` (for example the earnings exit); confidence defined per held action and kept out of entry calibration; a claim check on "locks in profit".
- v1 and v2 prompts, snapshots and options don't change. No migration. Config: `decision.v3`. Build plan: PR 30.

## 2026-10-28 — Model providers: OpenAI and Anthropic (PR 29)

- Decisions, shadows, reviews, replays and backtests can use an Anthropic (Claude) model as well as an OpenAI model, through one model client interface with an adapter per provider. Same prompts, snapshot, schema and checks in code.
- New `models` list in config (provider, prices, knowledge cutoff, effort for Claude models); it replaces `model_prices` and `backtest.model_cutoffs`, which are read for one release with a warning. New `decision.max_output_tokens` (2,048).
- Anthropic adapter: JSON outputs via `output_config.format`; schema keywords Anthropic doesn't accept are removed from the sent copy only, Pydantic still enforces them; no temperature (Claude models use `effort`); refusal and truncated answers count as skip or hold; one try, no retry.
- Answers from Claude models aren't repeatable at temperature 0, so the answer cache key includes provider and effort, and replays and backtests say so.
- Model keys: OpenAI and Anthropic are each optional; at least one is required. Resume needs a passing key for every provider used by live decisions and the review tasks; a model key can be removed when nothing live uses it; a shadow without its provider's key is skipped, never delaying live.
- Models are chosen per task: live decisions (`decision.model`), weekly review, daily briefing and verdicts (`reviews.models.*`, replacing `reviews.model`), plus shadows, backtests and replays. Pickers disable models whose provider has no passing key.
- `llm_calls`: `provider` and `provider_request_id`.
- The default live model stays `gpt-4o-mini`; the live model can be changed in Settings or through Promote strategy.
- AGENTS.md: new hard rule 21. Build plan: PR 29.

## 2026-10-28 — Backups (PR 24 review)

- The backup download is the database only, never `secret.key`; every download is an event and an email.
- The offsite upload starts when the local backup has finished.
- Backup credentials are write-only, saved behind "Confirm it is you", and needed again when the endpoint changes.

## 2026-10-27 — Sign-in details (PR 23 review)

- Sign-in: wrong codes count toward the lockouts; the ticket between password and code is single-use, tied to the browser and dropped after 3 wrong codes; a used code can't be replayed. New session token at every sign-in; a password change signs out every other session; `__Host-` cookie name over HTTPS.
- Open endpoints return nothing beyond the version or whether sign-in is required; the session check covers every HTTP method.
- Design: signed-in devices show the masked address only (no location lookup).

## 2026-10-27 — Security hardening (PR 28)

- From Devin's security audit. The main finding: until sign-in exists, any web page Marc visits could send write requests to the app on 127.0.0.1 (Stop & flatten, settings, key replacement), because CORS doesn't stop simple `text/plain` POSTs.
- New "Security hardening" section:
  - a request gate on every route and the WebSocket: Host (against DNS rebinding), Origin and `Sec-Fetch-Site` on writes, JSON-only writes, body size; the app's own security headers; fonts bundled now;
  - `SA_AUTH` parsed once and failing closed; the startup guard ships with the session layer; `python -m app.serve` as the only source of the bind address;
  - exception text and tracebacks scrubbed in every handler, plus URL tokens;
  - prompt versions whitelisted, contained in `prompts/` and hash-checked against a manifest (enforces hard rule 8);
  - clamped list parameters, capped exports, safe log search, no 500 on bad query values;
  - frontend error reports rate-limited per address, bounded and sanitised;
  - the stored SMTP password is never sent to a changed server; outbound URLs https and public;
  - `umask 077`, file modes, read-only run databases, exclusive `run.lock`;
  - CI: ruff `S` rules, `pip-audit` and `pnpm audit`, gitleaks, pinned Actions, `make security`, `docs/SECURITY.md`.
- VPS deployment: two more actions ask "Confirm it is you" (loosening a risk limit; changing the SMTP server, "To" address or heartbeat URL); the app container also drops all capabilities with `no-new-privileges`.
- VPS deployment: Caddy always adds HSTS and Permissions-Policy and sets the other headers only when the app hasn't, so PR 28's app headers win. PR 24 doesn't wait for PR 28: bundled fonts, `python -m app.serve` and file modes are built by whichever comes first; the first real deploy waits for PR 28.
- Error pages: new codes `bad_host`, `origin_refused`, `too_large`, `unsupported_media_type`, `password_required`.
- AGENTS.md: hard rule 9 extended (exceptions), new rules 18–20. Config: `security` block. Build plan: PR 28, before PR 23 is merged.

## 2026-10-27 — Protective stops hardened (PR 27)

- From Devin's live-data review: a protective stop was rejected because the stored stop was above the market; a stop leg was held invisibly under a filled parent while the target held all shares; and a sell stop for an already-exited position could have opened a short.
- New "Protective stops" rules in Alpaca integration:
  - confirm the position and orders at Alpaca before acting;
  - a stop at or above the market becomes a market sale with reason `stop_breached`, replacing the one-cent clamp; this is the only sale a position check makes;
  - an orphaned stop with the target holding the shares is repaired by cancelling the target and placing an OCO exit order, with the stop alone as fallback and the gap logged at CRITICAL;
  - every sell is capped at the confirmed quantity, so the agent never goes short;
  - broker-side `no_shorting` and 1× margin are set and checked at startup.
- Larger universe: a daily screen missed while the app was down runs at startup; the first cycle uses the previous list with a warning and a dated banner if needed.
- Accepted as fixed by Devin: held-leg recovery, the cycle error double-append, `active_until` on terminal trade updates, APScheduler executor logs at WARNING (job errors still at ERROR), and the old `quote_suspect` reason counted with `price_suspect`.
- Build plan: PR 27.

## 2026-10-27 — Name: Stock Trading Agent

- The application is called **Stock Trading Agent** everywhere it is shown: GUI, page titles, email subjects (`[Stock Trading Agent · PAPER] …`), error pages and docs.
- Technical names stay `stock-agent` (repo, database and log files, containers, zip names), so nothing has to be migrated.
- Used prompts (`decision.v1.md`, `decision.v2.md`) keep the old name, because used prompts are never edited. The next prompt version uses the new one.
- No behaviour change; it can ride along with any PR touching the GUI.

## 2026-10-27 — Day-trade rule retired (PR 26)

- FINRA replaced the pattern day trader rule and the $25,000 minimum with intraday margin standards on 4 June 2026 (Regulatory Notice 26-10). Alpaca applied them from that date and removed `pattern_day_trader`, `daytrade_count` and `daytrading_buying_power` from its API by 6 July 2026.
- Removed:
  - the same-day exit deferral (the agent may now sell a position the day it bought it);
  - the day-trade guard and its notification;
  - the day-trade counter and the Stop & flatten day-trade warning;
  - deferred exits in the snapshot, the feed and Insights.
- Added: no re-entry in a symbol closed earlier the same session (`risk.reentry_same_session: false`), as a brake on churning. Cash-only sizing against `cash`, never `buying_power`, is stated explicitly.
- `day_trades` is no longer written and is kept for history. AGENTS.md: new hard rule 17. Config: `pdt_max_day_trades` and the `pdt_guard` notification removed. Build plan: PR 26, first in the queue.

## 2026-10-26 — Error pages and API errors (PR 25)

- New "Error pages and API errors" section:
  - **API errors:** one JSON shape (`code`, `message`, `error_id` on 500, `fields` on 422), never tracebacks, SQL, paths or secrets. A table maps each status (400, 401, 403, 404, 409, 422, 429, 500, 503) to what the frontend does.
  - **In-app pages:** not found for a page or a record, "Something went wrong" per screen with the error id, "The app isn't answering" on first load, and an offline banner.
  - **Static pages from Caddy:** 502/504 "isn't responding" and 503 "Updating" (maintenance flag set by `deploy.sh` and `restore.sh`). They are self-contained and reveal no data, version or host details; API calls during an outage get the same JSON shape.
- GUI table: Error pages row. Build plan: PR 25.

## 2026-10-26 — VPS deployment (PR 23 and 24)

- New "VPS deployment" section: production on a Linux VPS (provider open; 2 vCPU, 4 GB, 40 GB, EU).
  - **Containers:** Docker Compose with `app` (one worker, non-root, read-only filesystem, data on a volume, internal network only, 90 s stop grace) and `caddy` (HTTPS, security headers, the only public entry point).
  - **Hardening:** SSH keys only, ufw, fail2ban, automatic updates with a Sunday 05:00 reboot window, chrony.
- **Sign-in (PR 23):** one user from `make create-user`; password (Argon2id) plus authenticator code with recovery codes; session cookie with 12 h idle and 30-day maximum; CSRF and WebSocket origin checks; lockouts; "Confirm it is you" before risky actions; `SA_AUTH`, and the app refuses to start without sign-in off loopback.
- **Safety:** an instance id in every client order id, and reconciliation turns the kill switch on when another instance trades the account. After a clean planned restart on the VPS the agent resumes on its own; otherwise it stays paused.
- **Deployment (PR 24):**
  - CI images and `deploy.sh` with a market-hours guard, pre-deploy backup, migrations, health check and rollback;
  - encrypted offsite backups with a monthly restore test;
  - an outside heartbeat and uptime check;
  - Settings → Server;
  - `docs/DEPLOY.md`, including how to move the local database.
- Data model: `users`, `sessions`, `sign_in_attempts`; `secrets` gains backup_password and backup_credentials. Config: `auth`, `ops`. `.env`: `SA_AUTH`, `SA_PUBLIC_URL`, `SA_TRUSTED_PROXY`, `SA_INSTANCE_ID`. Notifications: 8 new events. AGENTS.md rule 10 rewritten. Readiness manual item "Hosting with login" gets concrete conditions.
- Build plan: PR 23 (local) and PR 24 (after PR 20 and 23).

## 2026-10-26 — Insights (PR 22)

- New "Insights" section and screen (Learn group):
  - **Headline numbers:** expectancy with a 90% range, edge over SPY at the same exposure, beta to SPY, overnight vs in-session P&L, gaps through the stop, and model cost per trade.
  - **Findings:** at least 30 trades and at least twice the noise; early signals at 10–29; never applied automatically.
  - **Funnel:** from symbol checks to closed trades, with what was lost at each step.
  - **Slices** in 16 stock-specific dimensions: time of day, weekday, sector, symbol, regime, earnings distance, opening gap, VWAP, pullback depth, strength vs SPY, SPY that day, volatility, confidence, stop and target basis, sessions held, prompt and model.
  - **Also:** calibration, execution (fills, slippage, deferred exits, earnings exits, dividends), overnight gaps, and skipped setups scored with the rules-only geometry.
- Strategy override `entry_cycles`, so time-of-day findings can be tested in a shadow. The weekly review's facts include new findings and findings that stopped holding.
- Data model: `insights_snapshots`. Config: `insights` block. GUI table: Insights row.
- Build plan: PR 22.

## 2026-10-26 — Replay statuses (PR 21 follow-up)

- The first replay showed 762 "no_options" rows, but the builder never rejected a snapshot. About 720 rows were from before `stop_reference_price` existed (pre-PR 15) and 42 had price "unavailable". Every snapshot with complete inputs (125) got options and a model call.
- Replay now gives each snapshot a status:
  - `replayed`, which includes `no_options` from the builder with `EntryOptions.reason`;
  - `not_replayable`, with a reason: `missing_reference`, `price_unavailable`, `missing_atr` or `missing_bars`;
  - `replayed_approx`, with the opt-in `--approx-reference`: min(price, last 5-min close) for pre-PR 15 snapshots, reported separately and never mixed into the main rates.
- The report leads with counts per status, bases every rate on replayed rows, and lists every live v1 entry with what v2 did, or why it couldn't.
- The report also shows entries and R per session, with the number of distinct sessions, because same-day entries are one market move, not independent trades (added after the second replay: 59 of 60 approximate entries fell on 22 Oct).
- Path to live: the replay gate checks validity only. The entry-rate comparison needs at least 20 v2 entry proposals, which the shadow (at least 10 sessions and 20 proposals) provides.

## 2026-10-25 — Decision v2: code-built options (PR 21)

- New "Decision v2" section, from the 22 October analysis (32 of 33 entry proposals failed the code checks, mostly a stop about 1 ATR away):
  - Code finds levels (1h and daily swings, session levels) and builds up to 6 entry options that already meet the stop-distance and reward-to-risk rules, each with its basis and the levels in the way; held positions get up to 3 stop options (swing low, break-even, ATR trail).
  - The model picks an option id or skips, and writes no prices. `confidence` now means the chance of reaching the target before the stop.
  - Code checks the choice, rechecks it at order time and checks the explanation (level ids exist, decimals match the input). No option means no call, stored as `no_options`.
  - Risk rules unchanged.
- New prompt `backend/prompts/decision.v2.md`. Config: `decision.options`, `decision.replay`. Storage: levels, options, chosen option, explanation check on decisions and shadow decisions. LLM call history: warning `unsupported_claim`; invalid choice is `invalid_output`. Readiness: under v2 the proposal criterion also needs the explanation check and order-time recheck.
- `make replay` runs a prompt on stored snapshots and reports validity, explanation failures, choices and first touch, with rules-only alongside.
- Path to live: replay, then a shadow "C · Options (decision.v2)" for at least 10 sessions, then Marc decides; promotion only with "Start new run". Live stays on v1 in PR 21.
- AGENTS.md rule 8 covers every prompt version and forbids moving levels to make a choice pass.
- Build plan: PR 21, after PR 13 and 15, independent of PR 16–20.

## 2026-10-24 — Logging, error handling and shutdown (PR 20)

- New "Logging, error handling and shutdown" section: JSON-lines log file with all five levels (console info and up), rotation, key scrubbing and a level table saying what goes where; tracing ids per cycle, check, job and request; no swallowed exceptions (ruff rules, global hooks, `spawn()` for tasks, API `error_id`, frontend error reporting and an error boundary); graceful shutdown on Ctrl+C and SIGTERM (scheduler paused, 60 s grace, shielded order submission, interrupted cycles, backtest child stopped, WAL checkpoint, log closed last; second Ctrl+C skips the wait) and unclean-stop detection with `data/run.lock`.
- Log index in a separate `data/logs/logs.db`, logs API, and a Logs screen in the Control group: warning and up by default with a "Show debug and info" switch, per-level toggles, period, search, component, symbol and cycle filters, warnings and errors per day, recurring problems, traceback and surrounding lines, follow new lines.
- AGENTS.md hard rules 15 (no silent failures) and 16 (clean shutdown). Config: `logging` block. States: shutting down, unclean previous run. PR 20 starts with an error-handling audit report.
- Build plan: PR 20, after PR 17.
- Readiness (added the same day): two Operations criteria from the log, built in PR 20. "Errors in the log": 0 unreviewed ERROR problems in the last 10 sessions, where each recurring error is reviewed on the Logs screen as expected or fixed with a note; a fixed one that recurs is unreviewed again. "Critical lines in the log": 0 in the last 20 sessions, with no review possible. If the log index is unavailable, both show not yet. New `log_reviews` table; config `readiness.log_error_sessions` and `log_critical_sessions`.

## 2026-10-24 — Tail metrics (PR 19)

- New "Tail metrics" section: a "Winners and tails" card on Performance measuring whether results lean on a few large winners and whether fixed targets cut them off: R distribution, largest win and loss, top 20% share of gross profit, net without the best trade, winners kept (realized ÷ best open R), and per exit reason the best and the close in the 5 sessions after the exit. Small-sample banner below 20 closed trades. Measures only; exits and prompts don't change.
- Data model: `positions` gains after-exit best price and time, close price, status and done at, filled by a daily job after the close. Config: `performance.tail`. API: `/api/performance/tails`.
- Build plan: PR 19, independent of PR 16–18. Source: the Pareto question of 24 October.

## 2026-10-24 — LLM call history (PR 18)

- New "LLM call history" section: the LLM inspector browses every model call since the start (live decisions, shadows, reviews; backtests stay on the Backtest screen), not just today's. Filters: period (last session, 5, 20, all, custom; New York session dates), outcome (success, warning, error), source, plus symbol, action, reason, model, prompt, strategy and cycle, all in the URL. Summary per filtered set with a bar per session; list grouped per session with keyset paging; details with outcome reasons and an Error block; CSV export of metadata.
- Outcome rules: warning = `changed_by_code`, `input_incomplete`, `number_check_failed`, `slow`; error = `timeout`, `api_error`, `invalid_output`, `refusal`. Set once at write time.
- Data model: `llm_calls`, backfilled from decisions, shadow decisions and reviews. Config: `inspector` block. GUI table row updated.
- Build plan: PR 18, after PR 17.

## 2026-10-24 — Dashboard and interaction polish (PR 17)

- GUI: new "Dashboard at scale and off-hours". "Today" is the current or last New York session; outside a session the feed, model-call tile and strip show the last session with its date; countdowns over 24 h show day and time. Latest decision falls back to why the symbol wasn't called, or its most recent decision with date. Feed grouped per cycle; changes always shown; other decisions grouped by action and reason. Symbols are picked from the watchlist, held-symbol buttons or Find symbol, never a button per symbol; `?symbol=` in the URL. Rank and the screened hint only in screened mode. Linked tiles. Approvals show their expiry.
- GUI: new "Interaction and states": the Interaction rules in design/DESIGN.md are binding (focus ring, hover, 44 px, card loading/empty/error, backend banner, live regions, toast dedupe and persistent critical toasts, ARIA patterns, latest request wins, numeric API values with minus sign, settings-driven copy, page title).
- Build plan: PR 17, independent of PR 16. Source: Devin's dashboard UX review of 24 October.

## 2026-10-24 — Readiness (PR 16)

- New "Readiness" section: a screen in the Control group showing whether the current run's paper evidence would justify live trading. 15 criteria in Evidence, Model and Operations (closed trades, sessions, return vs SPY, drawdown, average R, profit factor, beats rules-only, proposals passing code checks, calibration, cycle coverage, stops, kill switch, errors, price checks, reconciliation) with minimum samples ("waiting for data"), estimates at the current pace, and five manual items. Nothing unlocks automatically; live trading needs its own spec.
- Errors resolve after 1 hour without recurring or are dismissed with a note; only errors affecting a decision, order or fill count.
- Data model: `readiness_snapshots`, `readiness_manual`; `events` gains affects, resolved at, resolution, note. Config: `readiness` block. Settings: Readiness section. Notifications: "Readiness changed".
- Build plan: PR 16.

## 2026-10-23 — Market price instead of the IEX ask (PR 15 question)

- Entries: the IEX quote on the free plan is one small exchange's book and often shows thin orders 2.5–7% off the tape, so it is no longer the market price. Market price = IEX last trade, ≤ 60 s old and within 1% of the last closed 5-min bar (`price_stale` / `price_suspect` otherwise: no model call, no entry). IEX bid/ask used only when plausible (both within 1% of the market price); a thin book is `spread_unknown` and doesn't block. Entry limit = market price + 0.05%. Stop distance from the lowest of market price, plausible ask and bar close; tighten_stop from the lowest of market price, plausible bid and bar close. Held positions are always reviewed. Replaces the `quote_missing` / `quote_suspect` rules of the 2026-10-23 integrity entry.
- Shadows: use live's price and quote when fetched (same rules); otherwise the bar close as a synthetic market price; count shown on Strategies.
- Later versions: live readiness includes real-time SIP data.

## 2026-10-23 — Integrity fixes from the 22 October analysis (PR 15)

- Entries: usable quotes only (quote timestamp, never the trade's or fetch time; missing or zero bid/ask → quote_missing; ask > 1% from the last bar close → quote_suspect; both before any model call); quote provenance stored per decision; size, order value and all gates redone on the final limit; stop must sit ≥ 1.5 × ATR below the lowest of ask, last trade and bar close before submit. Config: `execution.quote_max_deviation_pct`.
- Code checks: tighten_stop must be ≥ 0.25 × ATR below the lowest of bid, last trade and bar close, rechecked before replacement. Config: `decision.tighten_min_atr_below_price`.
- Risk gates: one sector resolver (pool or config) for gates, shadows and dashboard; unknown sector fails the gate. Daily loss limit latched for the rest of the New York session, survives restarts.
- Snapshot: quote and signal-bar times; held positions get initial stop, original thesis and invalidation, R against initial risk; per-symbol news status (complete, empty, truncated, failed), no shared cap.
- Outcomes: first touch judged against the stop active at each bar's time. Data model: `positions.initial_stop`.
- Reviews: status facts written by code; known-empty never called unavailable.
- Schedule: every incomplete cycle stored with its reason; no restarts or deploys during regular hours except emergencies.
- Decision cost stored with its source (priced, cached, legacy). AGENTS.md: new hard rule 14, no restarts during the session.
- Build plan: PR 15 Integrity fixes.
- Reference prices for the entry and tighten-stop distance checks: usable quote and last closed 5-min bar required, last trade optional (used when present); a missing required reference fails closed (`reference_missing`).
- The two source reports are in `docs/analysis/` (2026-10-22-live-data-and-llm.md, 2026-10-22-bugs-and-faulty-logic.md). The twelve items in the PR 15 kickoff are the acceptance list; the reports give the evidence and reproductions.

## 2026-10-21 — PR 14 review

- Backtesting: the bar store is filled for the run period plus the indicator lookback, so signals work from the first session.
- LLM cache: live and shadows write to it but never read from it; backtest decisions answered from it are marked `cached` (new `decisions.cached`, always false live).
- AGENTS.md hard rule 13 audit (PR 14) fixed write locks held across I/O in the cycle's model call, reconciliation, adoption/protective-stop placement and verdict writing.

## 2026-10-21 — Answers to PR 14 questions

- Run layout: `data/backtests/run-<id>/` with one live-schema DB per strategy and a read-only `market.db` (earnings, news, corporate actions); no strategy column in the live schema; strategies advance in lockstep; deleting a run removes its folder.
- Bars: separate never-pruned store `data/backtests/bars.db`, filled before a run (copy from live cache, then download); runs read bars only from it; not in the 03:00 backup; size shown, "Clear bar store" when idle.
- Estimate: calls from each strategy's average real calls per cycle (fallback live's, then the 10-call cap; rules-only 0), cache hits from `llm_cache` by model, temperature, prompt hash and cycle time (new `cycle_time` column), cost via `model_prices`; also the cost at the cap; Run disabled only when the expected cost exceeds what's left.
- Process: child process, one at a time, lower priority; progress on the `backtests` row; cancel flag, stop by PID after 60 s; pause flag during live cycles and checks before every download and model call; interrupted runs keep partial results. Backtest download limit lowered from 50 to 40 calls per minute so live's 150 plus backtests stay under Alpaca's 200.

## 2026-10-21 — PR 13 merge follow-ups

- Data model: `shadow_decisions` gains `input_snapshot`, filled only for symbols that only a shadow holds (other rows share live's snapshot), and `missed_reason`.
- Storage: every SQLite connection uses WAL and a busy timeout (`storage.sqlite_busy_timeout_seconds: 30`); no job keeps a write transaction open across a model or network call (found in PR 13: the shadow runner held its write lock during model calls). AGENTS.md: new hard rule 13.

## 2026-10-20 — Answers from the PR 13 review

- Shadow fills: orders timed at the live cycle's scheduled time; entries fill at the limit or a better open; the stop is checked on the fill bar (stop first), the target from the next bar; gaps fill at the open; market exits (model, earnings, flatten) at the next 5-min bar's open plus slippage; a candidate without a price gets no_call (quote_missing). Shadows skip the spread check and fresh-quote gate (documented as a small advantage).
- Live's cycle builds snapshots for symbols held by any active or paused shadow; live makes no decision for them.
- Slots: active and paused shadows count (max 3); a paused shadow makes no calls or entries but manages its virtual positions; archive frees a slot.
- Missed shadow work stores its reason (time or cost_cap). Notification event renamed "Model failing, or review or shadow cost cap reached".
- Decision layer → Prompt and Reviews → Model and cost now point at `model_prices`.

## 2026-10-20 — Answers to PR 13 questions

- "Same as live" counts a symbol-cycle only if at least one side did more than a rules no_call; all no_call reasons are one action; missed shadow work excluded; shown with its count.
- Shadows follow live's Pause, kill switch and reconciliation block for new entries (same gate name), keep managing virtual positions, and are flattened virtually by Stop & flatten; all other gates use the shadow's own portfolio; shock brake is market-wide. Shadows credit dividends.
- Compare and the equity chart use live's adjusted return (Performance's function); raw on hover.
- Prices: the `model_prices` config block is the only source from PR 13; the decision/reviews `*_cost_per_mtok` keys are removed; unpriced models refused at startup, in Settings, New shadow and the backtest picker; each call stores its cost when made.

## 2026-10-20 — Token prices in config

- Decision layer, Prompt and Reviews, Model and cost (added by Devin): USD cost is computed from configured prices per million tokens, `decision.input_cost_per_mtok` / `output_cost_per_mtok` and the same keys under `reviews`. Added to `config.example.yaml` (gpt-4o-mini prices; check OpenAI's current prices).
- Two stale sentences fixed: the design handoff no longer says "7 screens", and Reviews no longer says the Dashboard is unchanged (it shows the daily briefing card).
- Strategies and shadows, Cost: from PR 13 one `model_prices` table keyed by model replaces those keys, because shadows and backtests can use other models; a model without a price can't be selected.

## 2026-10-20 — Fresh quote before entries; screened mode blocked until PR 12

- Alpaca integration, Entries: new "Fresh quote before every entry". The latest IEX quote is fetched right before each entry order (automatic and Approve, one shared function) and rejected if its own timestamp is older than 60 s; spread check, limit price and code checks are redone from it; any failure means no order, recorded as a failed "Fresh quote" gate. Exits never wait for a quote. Config: `execution.quote_max_age_seconds: 60`.
- Larger universe: until PR 12, Settings and startup refuse `universe.mode: screened`; the daily screen still runs and the Universe screen shows a preview. PR 12 removes the block.

## 2026-10-19 — Briefing card, model call counter, shadows and backtesting

- GUI: "Daily briefing card" (today's briefing on the dashboard next to the risk budget, with number-check status, model, cost and next briefing; previous briefing before 15:00) and "Model call counter" (decision calls today vs the day's maximum, cost today and this month; review, shadow and backtest cost on hover). Both in PR 10.
- New section "Strategies and shadows (v0.2)", PR 13: strategy configurations (llm or rules_only, prompt, model, temperature, rule and check overrides; risk gates never overridable); up to 3 shadows run after each live cycle on the same input snapshots with virtual portfolios and fills on 5-min bars; never delay live; no broker client; $5 monthly cap; comparison, "same as live", disagreements; Promote to live through Settings after 20 closed trades.
- New section "Backtesting (v0.2)", PR 14: live cycle code over past sessions with the fake broker and a database per run; data download at 50 calls/min paused during live work; lookahead guards; fills on 5-min bars (stop first when both touched); LLM strategies only after their model's knowledge cutoff; answer cache; estimate before start; $20 monthly cap; results with "Read with care" assumptions; CSV export.
- Data model: `strategies`, `shadow_decisions`, `shadow_positions`, `shadow_equity`, `backtests`, `llm_cache`. Config: `shadows` and `backtest` blocks. GUI table: Universe, Strategies and Backtest screens.
- Later versions: shadow strategies and backtesting moved forward to PR 13 and 14.

## 2026-10-18 — Notifications

- New "Notifications" section: in-app banners always; browser notifications while the GUI is open; email over SMTP (password stored encrypted as `smtp_password`). 16 events in three severities with default channels; critical events always reach the browser and are never held back; repeat limit 30 minutes; quiet hours 23:00–08:00 Amsterdam (non-critical held and sent as one summary); sending never blocks trading; three failed emails show a banner. Subject and body format defined.
- Settings: new Notifications section (event × channel, repeat limit, quiet hours, SMTP with Send test, recently sent). Data model: `notifications` table; `secrets` gains `smtp_password`. Config: `notifications` block.
- Build plan: part of PR 10 (may be split into its own PR before the paper run).

## 2026-10-18 — Cycle counter on the dashboard

- GUI: new "Cycle counter" paragraph. The this-cycle strip ends with the next cycle (Amsterdam and New York time, live countdown), a segment per cycle of today's session, "Cycle n of 6 today", the cycle number in this run and the next position check; states for market closed, kill switch on and a running cycle. Built in PR 10 with the risk budget card.

## 2026-10-18 — Risk budget card on the dashboard

- GUI: new "Risk budget card" paragraph. Meters for open risk (positions and working entries) vs the 2.5% cap, today's loss vs the 2% daily limit and drawdown vs the 8% kill (amber above 75%, red at the limit), plus room for more trades, sector use, risk per trade with losing streak, shock brake with SPY moves, and day trades. Computed by the risk-gate functions, not in the GUI. Built in PR 10.

## 2026-10-18 — Larger universe (v0.2, PR 11 and 12)

- New "Larger universe (v0.2)" section: `universe.mode` fixed (default, v0.1 unchanged) or screened. Pool of up to 150 symbols, defaults in `backend/universe/pool.csv` (72 symbols). Daily screen at 08:10 New York: history loaded, tradable, price ≥ $10, one share fits in max position, 20-session average dollar volume ≥ $100M; eligible symbols drop out after 3 failed screens in a row; list fixed per session; held symbols always handled. Rank = 20-session return minus SPY's; model calls go to held positions, then rules-passing candidates in rank order, still capped at 10.
- Cycles at scale: multi-symbol bar requests, quotes and news only for model calls, cycle duration warning at 10 minutes, 5-min bars kept for 120 sessions.
- Data model: `universe_pool`, `universe_screens`. Config: `universe.mode`, `pool_file`, `max_pool`, `screen.*`, `rank_by`, `watchlist_rows`; `schedule.bars_5m_keep_sessions`, `schedule.cycle_duration_warn_minutes`.
- GUI: Universe screen; dashboard watchlist filters and search; universe mode and screen thresholds in Settings.
- Build plan: PR 11 (pool and liquidity screen) and PR 12 (cycles at scale).

## 2026-10-18 — Settings redesign and API keys in Settings (PR 10)

- New "API keys" section: Alpaca paper key ID and secret, OpenAI and Finnhub keys are entered in Settings instead of `.env`. Stored encrypted (Fernet, `data/secret.key`, mode 600), write-only API, only the last 4 characters shown, tested before saving, never logged. Replacing Alpaca keys needs the agent paused with no positions or orders. Missing or failing keys keep Resume disabled. Keys still in `.env` are imported once, then ignored.
- GUI: new "Settings screen" paragraph: section list and filter, one row per setting with its default, locked values, changed values highlighted with Reset, an unsaved-changes bar with a required reason, inline validation, and a warning before leaving with unsaved changes.
- Data model: new `secrets` table. `.env.example` no longer has key variables. README and AGENTS.md hard rule 9 updated.
- Build plan: ten PRs; PR 9 Reviews and PR 10 Settings and API keys listed; PR 10 is the last step before the 4-week paper run.

## 2026-10-18 — Pending entries and failed-cycle streak

- Risk gates: working entry orders (submitted, accepted or partially filled, not yet cancelled, expired or rejected) count toward total open risk, cash, max open positions, sector cap and max position size; filled quantity counts once. Entries in one cycle are gated in submission order. Supervised entries awaiting approval don't count until approved.
- Errors: only a completed cycle resets the failed-cycles count; skipped, interrupted and running rows leave it unchanged. Position checks keep their own failure count.

## 2026-10-17 — Reviews (PR 9)

- New "Reviews" section: weekly review (Saturday 10:00), daily briefing (09:00 New York, optional) and a draft verdict per enter/skip decision once its 5-session outcome is in, confirmed or changed by Marc. Code computes the facts; every number in the model's text is checked, with a plain template as fallback. Own $2 monthly cost cap; never affects trading. Missed reviews are written at the next start.
- New tables `reviews` and `verdicts`; new `reviews` block in `config.example.yaml`; prompts weekly.v1, briefing.v1, verdict.v1.
- Later versions: briefings and reviews moved forward from v0.2 to PR 9.

## 2026-10-17 — Paused at startup

- Risk management: new "Paused at startup" paragraph. The agent always starts with new entries paused; reconciliation, position checks, broker-side stops, earnings exits, outcome tracking and reviews of held positions keep running. Resume is one click in the header (no reason). Pause and the kill switch are separate states; both are stored, and a kill switch that was on stays on after a restart.
- GUI header: Pause/Resume button next to the kill switch. README: quick start says the agent starts paused.

## 2026-10-17 — Decision card and drawer like Trading Agent

- GUI, Dashboard: the latest-decision card now always shows the market regime, the earnings date and a three-line summary (failed check and proposed levels for a skip, position and levels for a held symbol, pre-filter reason when no model call was made).
- GUI, Decision detail: rebuilt in Trading Agent's drawer layout (safety gates, signal stack with 1h and daily columns, reasoning with risks and news, entry/stop/target/invalidation tiles, outcome, order status).
- Outcome tracking: for trades, also record whether the stop or the target was touched first.

## 2026-10-17 — Answers from the PR 8 review

- Data model: the daily backup at 03:00 uses SQLite's online backup API and `PRAGMA integrity_check`, never a plain file copy. Migrations are immutable once merged; startup refuses to run if the schema doesn't match the models.
- Errors: on a normal shutdown, wait up to 60 s for a running cycle or check; if it's still running it is marked interrupted and does not count as a failure. Only rows still running at the next startup (a real crash) count. Startup reconciles with Alpaca before anything else.
- AGENTS.md: new hard rule 11, migrations are immutable once merged.
- `config.example.yaml`: `storage.backup_time`, `storage.shutdown_grace_seconds`.

## 2026-10-16 — Screenshot location made explicit

- AGENTS.md, docs/DEVIN_KICKOFF.md and design/DESIGN.md: PR screenshots are saved in `docs/screenshots/pr-<n>/` and never in `design/`; the PR shows them next to the reference PNG. (The old wording "next to the matching file in design/screenshots/" read as "save them there".)

## 2026-10-16 — Answers to PR 7 questions

- Outcome tracking: "1 session" = same New York time on the next trading session; "5 sessions" = same time on the 5th trading session after the decision; clamped to the close on early-close days. Price = last closed 5-minute bar at or before the target time, stored with the bar's timestamp.
- GUI: Performance shows results by symbol (new "By symbol" card in the design); the day-trade counter belongs on Positions & orders, not Performance.

## 2026-10-16 — Answers to PR 6 questions

- Reset paper account: two steps. The button turns the kill switch on, closes positions, cancels orders and records `paper_reset`; Marc resets the balance in the Alpaca dashboard; "Start new run" checks no positions/orders and equity = `performance.starting_equity` ±1%, then starts a new run (`runs` table) that restarts the SPY benchmark, drawdown peak, losing streak and daily loss baseline. Resume via the normal kill-switch resume.
- Supervised approvals: the fresh-quote limit must pass the original code checks; one pending approval per symbol (newer replaces older); Approve disabled while the kill switch is on; `approvals` table with status and reason.
- WebSocket: `{"type":"update","topics":[...]}`, GUI refetches those endpoints (300 ms debounce), full refetch after reconnect, 60 s polling with a "live updates paused" note while the socket is down.
- Adding a symbol: sector required from a fixed list; must be an active, tradable US stock or ETF at Alpaca; "Warming up" with no entries until history is loaded.
- `config.example.yaml`: `performance.starting_equity`, `universe.allowed_sectors`.

## 2026-10-15 — Answers from the PR 5 review

- Risk management, kill switch: Stop & flatten never refuses because of the day-trade rule. It warns; if Alpaca rejects a sale, the position keeps its stop and a critical banner shows the rejection. (Closes the open question in design/DESIGN.md.)

## 2026-10-15 — Answers to PR 5 questions

- Day-trade rule: a same-day exit is recorded as deferred, not carried out. The next session's first cycle reviews the position again with the deferred exit in its input snapshot; the dashboard shows "Exit deferred (day-trade rule)".
- Execution mode: automatic only in PR 5; the app refuses to start with mode supervised until PR 6. Supervised mode: entries only; an approval expires at the next full cycle; approving re-checks gates, size and spread and sets a new limit from the current price.
- Reconciliation: a position found at Alpaca that the agent never recorded gets a protective stop at current price − 3 × 1h ATR (3% if ATR is unavailable), no target; no model calls or exits until acknowledged; marked as adopted, P&L shown apart. Data model: `positions.origin`.
- PR 5 done-when: read-only checks against the real paper account; Marc runs the first real paper session after review.
- `config.example.yaml`: `risk.adopted_stop_atr: 3`, `risk.protective_stop_fallback_pct: 3`.

## 2026-10-15 — Answers from the PR 4 review

- Decision layer: held positions are called first when the 10-call cap applies.
- Model output uses OpenAI structured outputs (strict JSON schema), still validated with Pydantic.
- `no_call` is an official final action, with a reason: `rules`, `call_cap`, `quote_missing` or `model_error`. Model calls time out after 30 s and are not retried; for a held position `no_call` leaves it as it is.
- Schedule: missed cycles are never run late (misfire grace 2 minutes); only simulated mode may run a cycle at startup.
- `config.example.yaml`: `decision.call_timeout_seconds: 30`, `schedule.misfire_grace_seconds: 120`.

## 2026-10-15 — Answers from the PR 3 review

- Market, schedule and data: daily indicators use Alpaca daily bars, 260 sessions per symbol, refreshed daily at 08:00 New York; computed on completed sessions only. All bar requests are split-adjusted. (60 sessions of 5-minute bars could not give SMA 200 or the 60-session ATR percentile.)
- Data model: new `bars_1d` table.
- Rules pre-filter: RSI 40–70 is inclusive.
- One cycle, step 3: the signal snapshot is stored; the GUI reads it and never recomputes per request.
- `config.example.yaml`: `schedule.daily_history_sessions: 260`, `schedule.bar_adjustment: split`.

## 2026-10-15 — Answers to PR 2 questions

- Market, schedule and data: Early close shows for the whole early-close session until its last cycle has run, then Last cycle done.
- SIP bar requests always end at least 16 minutes in the past (free-plan limit).
- Trading calendar unavailable: market treated as closed, retry every 5 minutes. EUR/USD unavailable: GUI shows USD only, marked "EUR unavailable".
- Data model `earnings`: time of day normalised to before_open, after_close, during_market or unknown (from Finnhub bmo, amc, dmh), raw value kept. Risk: an unknown time of day counts as before the open for the earnings exit.

## 2026-10-15 — 15-minute position check

- Market, schedule and data: new "Position check" between the hourly cycles, at 09:46, 10:01, 10:16, 10:31, then :01, :16 and :31 past each hour until 15:31 New York time. No model calls and no trades; it reconciles with Alpaca, makes sure every position has a stop, updates the since-open shock brake, daily loss limit and drawdown kill, and cancels stale entries.
- Decisions: the Cycle row mentions the check. Data model: `cycles.kind` (cycle or position check). Build plan: part of PR 5.
- `config.example.yaml`: `schedule.position_checks`, `schedule.position_check_failures_alert`.

## 2026-10-15 — Application timezone

- Overview: new "Timezone" paragraph. The application timezone is Europe/Amsterdam (GUI, logs, alerts, daily jobs); timestamps stay stored in UTC and decision cycles stay on the New York exchange clock.
- `config.example.yaml`: `app.timezone: Europe/Amsterdam`; `schedule.timezone` renamed to `schedule.market_timezone`.

## 2026-10-15 — Trading currency

- Overview: new "Currency" paragraph. All trading, risk limits and stored money values are in US dollars; euro amounts are display only and never used in decisions or risk checks.

## 2026-10-15 — First handoff

SPEC.md v0.1 as exported on 15 October 2026. It includes:

- Alpaca paper account only; free data plan (IEX real-time quotes, 15-minute delayed SIP bars).
- 10-symbol universe, long only, whole shares, 1-hour session-aligned bars, six cycles per session at :46.
- Bracket orders with broker-side stops on every position; reconciliation against Alpaca each cycle; PDT guard.
- gpt-4o-mini decision layer with strict JSON output, code checks, and outcome tracking for every decision.
- 14 risk gates, kill switch and Stop & flatten.
- 8-step build plan.

Details settled in this package that SPEC.md does not spell out:

- Environment variables: `MODE`, `SA_HOST`, `SA_PORT`, `SA_DATA_DIR`, `SA_DB_PATH`, plus the four API keys (`.env.example`).
- Every config key and default in `backend/config.example.yaml`.
- Command names: `make install`, `make run`, `make lint`, `make test`; frontend `pnpm lint` and `pnpm typecheck`.
- The full system prompt in `backend/prompts/decision.v1.md`.
