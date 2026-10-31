# Spec changes

Newest first.

## 2026-10-31 — Review attribution, remainder — implementation

- Verdict facts per spec: `checks_at_decision` is recomputed with the decision.v3 rules on the stored input (v3 rows keep their stored checks; v1/v2 get them too — trend 1h and daily, regime, room in ATR), `execution` reports `filled`/`not_filled`/`not_sent`/null, and `change_text` carries the failing check's human detail.
- The draft label follows the SPEC table: an enter is judged on the chosen option's (or the real trade's) first touch, a model skip on the offered options' touches, and only optionless v1 skips on the 5-session move — the trade-R shortcut is gone.
- Weekly facts count model skips only (a code-blocked enter is never a model skip) and confirmed verdicts in the three groups — executed enters, enters blocked by code with their reasons, and skips — plus thesis contradictions and the `sample` block (closed trades, model skips with an outcome, confirmed verdicts; week and run).
- `verdicts.prompt_version` records the review prompt (new Alembic revision on `c3d4e5f6a7b8`); `/api/verdicts` and the decision verdict now carry the model's call, code's change with its reason, the outcome line, the thesis check and `label_by_code`.
- Briefing: `text` stores only the model's sentences; `status_line` is code's and shows above the text in the GUI and email; a leading copy in older rows is stripped on read, never rewritten.
- Known prompt versions: saving settings with `decision.prompt` without a contract is refused `unknown_prompt_version`, as is a backtest strategy naming one; the replay CLI already raised.
- Kickoff `docs/kickoffs/pr-32.md` steps 6 and 8 and the listed test gaps; the read-only live-database check and a frontend pass under the design screenshot remain open.

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
- Kickoff `docs/kickoffs/fix-scan-2026-10-31.md`, step 1.

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
- Kickoff `docs/kickoffs/fix-scan-2026-10-31.md`. The rest are code bugs against the existing spec and AGENTS.md.

## 2026-10-31 — Misfire grace on every scheduled job

- Every scheduler job now carries `schedule.misfire_grace_seconds` (120 s) and `coalesce=True`; only `backtests-tick` keeps its own 10-second grace. Previously jobs without an explicit grace fell back to APScheduler's 1-second default, so a brief event-loop stall silently skipped the run — a one-off warning for the 1-minute `readiness-tick`, but a lost day/week/month for the `daily-data-refresh`, `daily-db-backup`, `daily-briefing`, `weekly-review` and `restore-test` crons.
- `scheduled-cycle-audit`, `calendar-retry`, `daily-data-refresh` and `daily-db-backup` also gain `coalesce=True`; several misses inside the grace merge into one run, never a queue of catch-ups.
- `test_scheduler.py` asserts every job carries the configured grace.

## 2026-10-31 — Entry selection (PR 33)

- From the 31 October report on how entries are chosen when more pass than there are slots: there is no selection step. Entries are gated and sent in rank order, so the first ones to pass take the 4 slots, and candidates after the 10th call are never seen. Rank (20-session strength against SPY) says nothing about the offered trade or about dollars at risk. The 20% max-position cap cuts the risk, and so the profit, on tight-stop stocks.
- New section **Entry selection**. With `selection.mode: expected_profit`, code collects all candidate decisions of a cycle and scores each enter as expected R × dollars at risk. Confidence is moved halfway to break-even (`selection.confidence_weight` 0.5). Code then sends the set with the highest total that fits the existing limits, and the rest get `not_selected`. Model calls go in pre-score order (dollars at risk). `make replay-selection` compares both modes on stored cycles.
- `selection.mode` defaults to `rank`, so live behaviour is unchanged until Marc switches it after the replay and a shadow.
- Cycles at scale and Working entry orders refer to the selection mode. Build plan item 33.
- The `selection` config block comes with PR 33 (rule 7).

## 2026-10-30 — Live fixes of 30 October — implementation

- `docs/kickoffs/fix-live-2026-10-30.md` lands in code: the "Live decisions failing" critical event, banner and notification (once per episode, cleared by the first good call) with the provider key marked failing on a 401/403; "Trade opened" fires once when the entry order is final, with the filled quantity; reconcile defers the quantity check while an entry order works or a fill is fresh; GUI broker reads answer the last reconciled values, marked stale, past the timeout; a held-position tighten accepts an older last trade and a still-blocked tighten shows as changed by code (`price_stale`/`price_suspect`); stream reconnect errors log once per outage then once per 10 minutes, and a long outage degrades the stream only in session; no chart request without a symbol; screenshot specs write to a temp dir unless `SCREENSHOT_DIR`.
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
- Kickoff `docs/kickoffs/fix-live-2026-10-30.md`. No design change: the GUI uses the existing banner and the stale-data pattern from `States.dc.html`.

## 2026-10-30 — Analysis: IEX quote plausibility

- `docs/analysis/2026-10-29-quote-plausibility.md` plus the reproducible script `scripts/analysis/quote_plausibility.py` (read-only, `mode=ro`). No behaviour change.

## 2026-10-30 — Ops: alerts, log volume and small fixes — implementation

- `docs/kickoffs/ops-alerts.md` lands in code: the "New error" warning notification (one per ERROR fingerprint per session, link to Logs), third-party loggers at WARNING unless `logging.library_debug`, no `/api/chart/` call without a symbol, and no repeat broker ask for a position already found flat in the same pass.
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

- `docs/kickoffs/pr-30.md`: reads both PR 30 change log entries, and states the label and changelog rules.
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
