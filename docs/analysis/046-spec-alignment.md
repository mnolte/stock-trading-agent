# 046 · Spec and design alignment: gap report

7 November 2026 · Devin. Phase 1 of `docs/kickoffs/archive/046-fix-spec-alignment.md`: audit only, no code changes. Each spec sentence that states a behaviour, a number, a name or a stored field is classified:

- **built** — the code does it (file:line);
- **partial** — part of it is missing;
- **missing** — nothing implements it;
- **differs** — the code does X, the spec says Y (both quoted);
- **spec-only question** — can't be checked in code, or the spec contradicts itself.

Earlier audits already read the money and security paths end-to-end (`docs/analysis/2026-10-31-repository-scan.md`); this report checks them again where the spec names a behaviour, and marks rows settled by that scan where nothing changed since.

## A. Spec against code

One table per spec `##` section, one row per checkable claim (a sentence, or a compound sentence's separable parts). "Spec" quotes the sentence with its line number on `main` (ed6b64e).

### Overview and goals

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| "paper-only agent that trades a fixed list … with an OpenAI or Anthropic model as its decision layer (OpenAI `gpt-4o-mini` live by default)" (7) | differs | `backend/config.example.yaml:104` `decision.model: gpt-4o-mini` | Spec line 1809 says "The live model stays gpt-4.1-mini". The spec contradicts itself on the default live model (see Q below). |
| "EUR … converted with the ECB daily rate, and never used in trading decisions" (9) | built | `backend/app/data/ecb.py` | EUR display verified in D. |
| "The GUI, logs, alerts, settings history and daily jobs … use Amsterdam time" (11) | built | `config.example.yaml:43` `app.timezone`; backup job `api/main.py:1438-1448` | |
| "timestamps are stored in UTC … decision cycles follow the New York exchange clock" (11) | built | `_naive_utc` throughout `app/engine`, `app/data/cache.py`; `NY_TZ` `app/market/clock.py` | |
| "No code path can place an order against a live Alpaca endpoint" (19) | built | scan 31 Oct: `paper=True` everywhere; live-URL test | Verified again by the scan and `app/broker/alpaca.py`. |

### Decisions

| Spec | Status | Code | Note |
| --- | --- | --- | --- |
| Universe fixed list of 10 symbols (31) | built | `config.example.yaml:6` | |
| Long only, whole shares, bracket orders (32, 37, 38) | built | `executor._enter` bracket submit; `position_size` `ROUND_FLOOR` `risk/math.py:118` | |
| Six cycles 10:46–15:46 NY + position checks every 15 min (34) | built | `config.example.yaml:47,49` | Check times match 09:46, :01, :16, :31. |
| Automatic execution; supervised refused until PR 6 (40) | built | `execution.mode` config; PR 6 merged | |
| Finnhub earnings, Alpaca news, ECB rate, Alpaca corporate actions (41–42, 124–127) | built | `data/finnhub.py`, `data/alpaca.py`, `data/ecb.py` | |

### Architecture and stack

| Spec | Status | Code | Note |
| --- | --- | --- | --- |
| Python 3.12, FastAPI, Uvicorn; APScheduler NY-aware; alpaca-py; SQLAlchemy 2 + Alembic; pandas; React+TS+Vite; Lightweight Charts (52–58) | built | `pyproject.toml`, `frontend/package.json`, `api/main.py` | |
| WebSocket `{"type":"update","topics":[…]}`, refetch debounced 300 ms, refetch-all after reconnect, 60 s poll + "live updates paused" (59) | built | `api/websocket.py`, `frontend/src/hooks` (topics incl. `attention`, `logs`) | Poll + note verified in D. |
| `.env` only MODE/SA_HOST/SA_PORT/SA_DATA_DIR/SA_DB_PATH; config.example.yaml + config.yaml + settings layers (60) | built | `app/config.py`, settings versioning `settings_service.py` | Config layers (3-level merge) from [050]. |
| Repo layout incl. `prompts/decision.v1.md` (62–84) | built | tree matches; prompts in `backend/prompts/` | |
| Every external source behind an interface with a fake (86) | built | `data/fake.py`, `broker/fake.py`, `llm/providers` fakes | |

### Market, schedule and data

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Calendar from Alpaca `/v2/calendar` at startup and daily 08:00 NY (94) | built | `market/calendar.py:76`, job `api/main.py:1394-1411` | |
| `/v2/clock` is the single source of truth for "is the market open" (95) | built | `engine/cycle.py:163-170`; check `position_check.py` uses session window for its own run gate, reconcile reads broker | |
| Scheduling in `zoneinfo` `America/New_York`; no hard-coded offset (96) | built | `NY_TZ`, `zoneinfo` throughout | |
| Session states Closed / Open / Last cycle done / Early close; early close shows whole session until last cycle ran (97) | built | `market/clock.py:64-65,153-192` | |
| Session-aligned 1h bars 09:30…14:30 + 15:30–16:00 half bar, from 5-min SIP bars; no clock-hour bars (101) | built | `data/aggregate.py:54-105` | `bars_1h` cache table exists. |
| Cycles 16 min after bar close; every SIP request ends ≥16 min in the past (102) | built | `data/alpaca.py:252` `SIP_MIN_AGE = 16 min` | |
| Early-close days: only cycles whose bar closes before the close; last one is the exit check (103) | built | `market/clock.py:111-121` `active_cycles` | |
| Every scheduled cycle that doesn't complete is stored with its reason (app down, shutdown, error, market closed) (103) | built | `cycle_runner.record_missing_cycles` `api/main.py:1337,1382-1387`; cycle status rows | |
| Missed cycles never run late; misfire grace 2 min; only simulated mode may run a cycle at startup (103) | built | `misfire_grace_seconds=120` `api/main.py:1379-1452`; `_may_run_at_startup` `api/main.py:774-775` | |
| History: 60 sessions of 5-min bars per symbol cached in SQLite, topped up each cycle (104) | built | `schedule.history_sessions=60`; `bars_5m` table; `cycle._update_bars` `cycle.py:186` | |
| 260 daily bars per symbol, loaded at startup, refreshed daily 08:00; indicators on completed sessions only; gap vs previous close; split-adjusted (104) | built | `daily_history_sessions=260`; `bars_1d`; `signals/service.py`, `snapshot.py:122` | |
| IEX quote used only for sizing and the spread check at order time (105) | built | `executor._fresh_entry_check` `executor.py:741-828`; `cycle._fetch_quotes` for the review set | |
| Position check at 09:46,:01,:16,:31 hourly to 15:31; no calls, no entries, no sells except the breached-stop sale, no stop moves (109) | built | `position_check.py:66-137` | The breached-stop market sale happens inside `_place_protective_stop` during reconcile — the one permitted sale. |
| Check reconciles with the same rules as a cycle (111) | built | `position_check._run` → `executor.reconcile` `position_check.py:142` | |
| Confirms every position has a live stop; repairs incl. market sale + alert (112) | built | `executor._stop_check` `executor.py:3228`, `_place_protective_stop` `2546` | From [050]. |
| Updates since-open shock brake, daily loss, drawdown from IEX + account; 1h leg stays on the cycle (113) | built | `executor.update_session_brakes` `executor.py:240` | |
| Cancels entry orders unfilled after 10 min (114) | built | `executor.py:1965-2001` `entry_timeout_minutes=10` | |
| A failed check is logged; three in a row show an error banner, no kill switch (116) | partial | `position_check.py:99-132` | Banner arrives, but the event is recorded at level `critical` (`position_check.py:125`) while the spec's notification row is **Warning** ("Position checks failing (3 in a row)" browser+email on). The banner shows as critical-styled; the notification itself is warning. See G-gaps. |
| Header shows the next check alongside the next cycle (116) | built | `CycleCounter.tsx`, `/api` header payload | Verified in D. |
| News: last 24h per symbol, every cycle (124) | built | `cycle._fetch_news` `NEWS_WINDOW`, per-symbol statuses | |
| Earnings calendar daily 08:00 (125) | built | `refresh_daily_data_and_signals` job `api/main.py:1396` | |
| ECB rate daily (126) | built | `data/ecb.py` + daily job | |
| Corporate actions daily (127) | built | `data/alpaca.py:445` + daily job | |
| Missing bars or quotes → no new entries that cycle (129) | built | gates `earnings_known`, `price_stale`/`price_suspect` no_call, `working_entry_unpriced` | |
| Missing news or earnings passed as "unavailable", never "none"; missing earnings also blocks new entries for that symbol (129) | built | news statuses complete/empty/truncated/failed `cycle.py:253-266`; snapshot marks `"unavailable"`; gate `earnings_known` `gates.py:201-202` | |
| Calendar unloadable → market treated as closed, retries every 5 min; stops keep working (129) | built | `retry_calendar_load` `api/main.py:778`, IntervalTrigger 5 min `1412-1419` | |
| EUR/USD unavailable → USD only, marked "EUR unavailable" (129) | built | frontend header/EUR handling | Verified in D (States banner added by 045). |

### Alpaca integration

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| "Alpaca is the source of truth for cash, positions and orders; the local database mirrors it" (133) | built | `executor.reconcile` `executor.py:1641` | |
| Broker reads from the GUI wait at most `api.broker_timeout_seconds` (5), then answer `stale: true` + time (135) | built | `api/routes.py:1304-1316` | |
| `paper=True` always; live URL never in the repo; a test fails the build (139) | built | `broker/alpaca.py`; `tests/ci` live-URL test (scan verified) | |
| Startup reads the account and refuses non-paper; "PAPER" permanently in the header (140) | built | startup account check `api/main.py`; header PAPER badge | |
| Keys entered in Settings, encrypted, never logged or sent to the frontend (141) | built | `secrets_service.py`, `secrets` table | |
| Bracket entry: limit buy + stop + take-profit, tif `gtc` (145) | built | `executor._enter`; `execution.time_in_force: gtc` | |
| Market price = IEX last trade ≤60 s old on the trade's own timestamp, within 1% of last closed 5-m bar; stale/suspect → no call, no entry (146) | built | `Quote.price_reason` via `cycle._price_reason` `cycle.py:218-233` | |
| Held positions always reviewed; for them stale/suspect only blocks tighten_stop; tighten trade ≤300 s; `stop_options` empty + `stop_reference_price` null before the call (146) | built | `decide` held snapshot path; `tighten_quote_max_age_seconds=300` | Verified via v2/v3 held builders and output checks. |
| IEX bid/ask only when plausible (present, timestamped, within 1% of market price); thin book → `spread_unknown`, not a skip reason (147) | built | `Quote.quote_plausible`, `spread_unknown` in snapshot | |
| Entry limit = market price + 0.05%; cancel after 10 min, record "entry not filled" (148) | built | `entry_limit_offset_pct=0.05`; cancel `executor.py:1965-1997` | |
| Skip if IEX spread > 0.15% of price, measured only when plausible (149) | built | `executor.py:790-797` `max_spread_pct=0.15` | |
| Whole shares; size rounding to 0 → skip (150) | built | `risk/math.py:118-131` | |
| Each decision stores price provenance (last trade+time, bid, ask, quote time, plausible, feed, fetch time, last signal bar) (151) | built | `executor._quote_provenance` `executor.py:735-739` | |
| Sized on the final price: re-fetch before the send, re-run checks + gates, stop ≥1.5×ATR below min(price, plausible ask, bar close); missing bar/ATR → `reference_missing`; failure = failed "Fresh quote" gate (152) | built | `executor._fresh_entry_check` `executor.py:741-828` | |
| Automatic entries and Approve share the same function; exits/stop moves/flatten never wait for a quote (152) | built | `approve` → `_fresh_entry_check` `executor.py:846` | |
| Stop moves replace the stop leg in place; stops only move in the position's favour (156) | built | `executor._tighten` `executor.py:3813`; direction enforced in output checks | |
| Exit: confirm qty → cancel legs → confirm cancelled → market sell for confirmed qty (157) | built | `executor._attempt_exit` `executor.py:4065`, `_ensure_dead`, `_await_released` | [043] hardened. |
| Held-back exits stored pending with reason/decision/time; retried before anything at every check and cycle; legs gone → protective stop; ≥3 tries or session end → critical Needs you; earnings pending at close → critical at once; clears on flat or sell fill; outside session market-on-open (158-163) | built | `executor.retry_pending_exits` `4033`, `_alert_pending_exit` `4011`; `pending_exits` migration `q1a2b3c4d5e6` | [043], [044]. |
| Stops trigger only in regular hours; gap through stop fills at open; slippage shown separately (164) | built | fills record slippage; Insights gaps-through-stop | |
| Trade-updates stream for fills/cancels/rejects (168) | built | `broker/alpaca.py` stream; `executor.on_trade_update` `1552` | |
| Fill to zero closes the position row right away with reason + fill, logs INFO, sends "Position closed" (169) | built | `executor._record_trade_update` `1569`, `_close_position` `2312` | [031]. |
| Stream health: unparseable update → ERROR + count + `degraded`; fill without update within 2 min → `degraded`; banner + critical notification; clears after 30 min clean; parser tested on real wrapper shape; reconnect log rules; >5 min outage during session → degraded (170) | built | `executor._mark_stream_failure` `1515`, `_stream_grace` `1543` (120 s), `stream_degraded_minutes=30`; `ExecutionState.stream_degraded` | |
| Flat at Alpaca explained by own exit orders → close `closed_while_offline`, WARNING, not a mismatch (171) | built | `executor._own_exit` `2302`, `closed_while_offline` `2388` | |
| Reconcile at startup and at every cycle start (172) | built | `api/main.py` startup reconcile; `position_check._run`, cycle | |
| Fills still settling: qty difference with working entry or fill <10 s old is deferred, logged INFO, rechecked at next reconcile or ~10 s after order final (173) | built | `executor._settling` `2049-2086`, `_schedule_settle_recheck` `2088`; `fill_settle_seconds=10` | |
| Mismatch → event + CRITICAL + banner + adopt Alpaca state + block entries until acknowledged; never a silent close (174) | built | `executor._mismatch` `1280`; gate `pending_ack` `gates.py:142` | |
| Adopted position: standalone gtc stop at price −3×ATR(1h) (3% if ATR missing), no target; no calls/exits until acknowledged; marked adopted; P&L shown apart (174) | built | `executor._adopt_position` `2407-2545`; `adopted_stop_atr=3`, `protective_stop_fallback_pct=3` | |
| Position without a live stop → critical, repaired per Protective stops, then alert; protection decided by the Stop check, not the repair path (175) | built | `executor._stop_check` `3228-3348` | [050]. |
| Protective stops: confirm position+orders first; flat or covered → nothing (178) | built | `_place_protective_stop` `2546`; confirm pass | |
| Stop below market → gtc stop at the stored stop price (179) | built | same | |
| Stop at/above market → sell confirmed qty at market in session / MOO outside; reason `stop_breached`; the only sale a check may make (180) | built | `executor.py:2747-2767`, `_market_in_session` `1489` | |
| Orphaned stop leg, target holds shares: cancel target → OCO with stored target+stop → fallback stop alone; gap logged CRITICAL with length; critical alert either way (181-186) | built | `executor.py:2848-2895` `submit_oco_exit`, `held_for_orders` handling | [027]. |
| Never sell more than Alpaca confirms; never short (187) | built | sell qty capped at confirmed qty in exits/protective/flatten (scan verified) | |
| Broker-side guard at startup: `no_shorting` + `max_margin_multiplier: 1` set and checked; unconfirmed → warning banner, no refuse (188) | built | `executor._verify_broker_guard` `1650-1681` | |
| Stop check at the end of every reconcile — startup, cycle, check — after repairs (192) | built | `executor.py:2003-2008` | |
| Protected = stops for ≥ qty held, or stops + exit in flight (market sell <60 s, or MOO queued until open+2 min); pending exit is not protection (193-196) | built | `executor._protection_for` `3078`, `_market_exit_in_flight` `3050`; `exit_in_flight_seconds=60` | |
| Unprotected after one more repair → `stop_missing` episode: start, shares, reason (197) | built | `executor.py:3228-3304`; `events` type `stop_missing` | |
| Episode: CRITICAL at start, ERROR each later pass, with shares, stop qty, reason; never INFO (198-199) | built | `executor.py:3304-3320` | |
| Critical Needs you "KO has no stop at Alpaca since 15:46 · 7 shares · <reason>" with Open position (200) | built | `attention.py` (per [050] CHANGES) | |
| `missing_stop` notification at start, then every 30 min in session ("still no stop · 1 h 30 min") (201) | partial | repeat logic exists | The 30-min repeat rides on `repeat_limit_minutes` (30). The repeat subject text — need: "still no stop · <elapsed>" — the emitted line is "still no stop at Alpaca" without the elapsed time (`events.py:343-349`). Minor wording gap. |
| Red "No stop at Alpaca since 15:46" on the position card, decision drawers and Positions (202) | built | `StopTargetBar`/position chip, drawer "Stop at Alpaca" row (per [050] CHANGES) | Verified in D. |
| New entries not blocked during an episode; Resume says why if paused (203) | partial | entries not gated on `stop_missing` | "Resume says why" — resume path wording not verified; the Needs you item exists. Unverified detail. |
| Episode closes only when a pass finds protected or flat; logs INFO with length; sends "stop restored" (204) | built | `executor._close_stop_episode` `3144` | |
| Repair skips are reasons, logged WARNING with quantities; only the check decides (205) | built | skip reasons `exit_in_flight`, `insufficient_qty`, `already_protected`, `repair_error` (per [050] CHANGES) | |
| Episodes stored as `events` type `stop_missing` with `resolved_at` (206) | built | `executor.py:3298`; readiness counts hours | |
| Same-day exits allowed; earlier deferred exits not replayed (211) | built | exit path has no same-day deferral ([026]) | |
| No re-entry the same session: symbol sold or stopped today is not bought until next session (212) | built | gate `reentry` `gates.py:219-222`; `risk.reentry_same_session: false` | |
| Cash-only sizing vs `cash` minus working entries, never `buying_power` (213) | built | `gates.py:279-283`, `committed_entries` | |
| `pattern_day_trader`/`daytrade_count`/`daytrading_buying_power` never read; a test fails on reference (214) | built | scan verified; `tests/ci` | |
| `day_trades` table no longer written (215) | built | model kept, no writer | |
| Positions shows same-session round-trip count, informational (216) | built | Positions "Today" card | Verified in D. |
| Paper realism: raw + adjusted P&L with 0.05% slippage/side (configurable); dividends added (220) | built | `engine/performance.py`; `performance.slippage_pct_per_side=0.05` | |
| One shared limiter <150 Alpaca calls/min; backoff retries reads only; an order is never resent (224) | built | `data/rate_limiter.py`; client-order-id recovery (scan verified) | |

### Decision layer

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| "Code decides what is allowed; the model only chooses" (228) | built | `decide/output.py` validators + `risk/gates.py` | |
| Cycle order: pre-checks (clock, kill switch, reconcile) → data → indicators+regime stored → pre-filter → ≤10 calls, held first → validate+gates+size+orders → store (232-238) | built | `engine/cycle.py`, `engine/live_calls.py` | Snapshot stored; GUI reads stored snapshot. |
| Indicators: EMA20/50, RSI14, ATR14, session VWAP, OBV 20, RS vs SPY 20, daily SMA50/200, ATR pct 60, opening gap, days to earnings (242-247) | built | `signals/indicators.py`, `snapshot.py` | Hand-checked tests exist. |
| Regime from SPY: trend Up/Down/Sideways on SMA50/200; vol Low/Normal/High on ATR pct 30/70; Down+High → no new entries (249) | built | `signals/regime.py`; `regime.block_entries_in: [down_high]` | |
| Rules pre-filter: not held + no open entry, close > SMA50, EMA20>EMA50, RSI 40–70 inclusive, outside earnings blackout, regime not Down+High, not the 15:46 cycle (251-259) | built | `rules/prefilter.py:46-…` | Config keys `rules.rsi_min/max`, flags, `last_cycle_no_entries`. |
| Config layers: example always read, config.yaml overrides per key, settings on top; unknown keys warn once; unknown model ids warn / refuse at save (263-273) | built | `app/config.py`, `llm/models.py`, `settings_service.py` | [050]. Settings → Server shows "Config: n local overrides · n unknown keys". |
| Model output: strict JSON schema via provider structured outputs, Pydantic re-validation; actions per context (275) | built | `llm/providers/openai.py`, `anthropic.py`; `decide/output.py` | |
| v1 output fields symbol/action/confidence/stop_price/target_price/thesis/invalidation/risks/news_used (277-289) | built | `decide/output.py` v1 schema | |
| Code checks: stop below price, 1.5–4×ATR; RR ≥1.5 by code; confidence ≥0.55 for enter (293-295) | built | `output.py:531-537` | Thresholds from config, strategy-overridable. |
| tighten_stop only raises; new stop ≥0.25×ATR below min(market price, plausible bid, bar close); rechecked before replace; missing bar/ATR or stale/suspect price → stop stays + reason stored (296) | built | `output.py:653-715,949-…` `tighten_min_atr_below_price=0.25`; order-time recheck in `executor._tighten` | |
| Break-even guard: tighten to ≤ entry refused below 1.0R profit → `hold` + `breakeven_too_early`, shown changed by code; all prompt versions (297) | built | `output.py:472-501` `_breakeven_guard` | |
| Invalid/unparseable output → skip (hold for position), stored with raw text (298) | built | `output.py` ValidationResult; raw stored on decision | |
| No call / failed call → `no_call` with reason `rules`, `call_cap`, `quote_missing`, `model_error`, `no_options` (298) | spec-only question | `output.py:32-35` `NO_CALL_REASONS` | The spec's own list is stale: code and later spec sections use `price_stale`, `price_suspect`, `reference_missing`, `checks` (v4 checks_gate / v5 trend gate); `quote_missing` exists nowhere. Spec needs updating, code is right (Q). |
| Live decisions failing: all live calls of a cycle fail (≥1) or any 401/403 → critical event + banner "Live decisions failing: <provider> <error>…" + notification once per episode; 401/403 marks the provider's key failing; clears on first success (298) | built | `engine/live_calls.py:46-154`; `live_decisions_failing` event | |
| Model call times out after 30 s, never retried; held `no_call` leaves the position as is (298) | built | `decision.call_timeout_seconds=30`; `per-model timeout_seconds` override | |
| Prompt version + hash, model, token cost stored per decision; cost from `models` list with source priced/cached/legacy; legacy never rewritten (300) | built | `decisions` columns; `llm_calls.cost_source` | |
| Snapshot: JSON block with indicators, regime, position, 24h headlines, earnings, quote + signal-bar times; held: entry, initial stop, current stop, original thesis+invalidation, R progress vs initial risk, remaining downside separately (300) | built | `decide/snapshot.py`, `v3.py` `position_block_v3` | |
| Headlines per symbol with status complete/empty/truncated/failed; only `empty` reads as "no news"; fetched until complete or budget out, never one shared cap (300) | built | `cycle._fetch_news` statuses `253-266`; per-symbol fetch loop | |
| Missing data marked `"unavailable"`; prompt says treat as unknown, never invent (300) | built | snapshot `"unavailable"`; `prompts/decision.v1.md` | Prompt text verified in C. |
| Known prompt versions only: contract per version; unknown version → startup fail, save refused 422 `unknown_prompt_version`, no fallback (302) | built | `decide/prompt.py` contracts; `manifest.json`; save path | List `decision.v1–v5`, `weekly.v1–v2`, `briefing.v1–v2`, `verdict.v1–v2`, `ask.v1` — spec's text stops at v3/weekly.v2/briefing.v2/verdict.v2, stale list but behaviour correct (note). |
| Outcome tracking: price after 1h / 1 session / 5 sessions, same NY time on next trading session(s), close on early close, last closed 5-m bar at/before target; stored with bar timestamp (304) | built | `engine/outcomes.py:177-234` | |
| Trades: best/worst while open; first touch stop vs target judged per bar against the stop active at that bar's time from stop-leg history (304) | built | `outcomes._first_touch`, `_update_best_worst`; stop history from orders | [015], [043]. |
| Option outcomes: per offered option which of stop/target touched first; entry fill rule (low ≤ limit within 10-min life else `not_filled`); traded option uses real fill; same-bar both → `stop`; none by 5 sessions → `neither`; missing bars → null; ignores earnings exits/stop moves/limits; `outcomes.option_touches` JSON (306-311) | built | `outcomes._option_touches` `434`, `_fill_option_touches` `543`; counterfactuals `cycle._counterfactual_build` | [032], [037]. |

### Risk management

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Gates run in code after the model, "in the order below; the first gate that fails blocks the action and is recorded by name" (315) | partial | `risk/gates.py:112-348` | Code order ≠ spec table order: code checks `max_open_positions`, `sector_cap`, `daily_loss_limit`, `shock_brake`, `earnings`, `reentry`, `drawdown_kill` **before** the sizing gates (risk per trade, max position, open risk, cash). When several would fail, the recorded `blocked_by` differs from spec order (e.g. spec: open-risk skip before daily-loss; code: daily-loss first). Behaviour (block vs size-down) is right; the recorded name can differ. See Q. |
| Risk per trade 0.5% equity; shares = risk budget ÷ (entry−stop) rounded down (319) | built | `math.position_size` `risk/math.py:84-131` | |
| Max position 20% equity → size reduced (320) | built | same, `max_position` candidate | |
| Total open risk 2.5% incl. working entries → size down or skip <1 share (321) | built | `open_risk` + `committed_entries`; `open_risk` candidate | |
| Max open positions 4 → skip (322) | built | `gates.py:154-161` (counts positions + working entries) | |
| Sector cap 2/sector; QQQ=Technology, SPY=Index; one resolver for gates/shadows/dashboard; unknown → `sector_unknown` fail (323) | built | `gates.py:163-176`, `resolve_sectors` `104-109`; config `universe.sectors` | |
| Cash only: order value ≤ cash, margin never used (324) | built | `cash` candidate; `cash_only` config | |
| Daily loss −2% in session → no new entries for the NY session even if equity recovers; latch stored, survives restart (325) | built | `gates.py:178-188` `daily_loss_hit` latch; `ExecutionState` | |
| Losing streak 3 → risk per trade halved until next win (326) | built | `streak_risk_factor` `math.py:199-205` | |
| Shock brake: SPY down >1.5% last 1h bar or >2.5% since open → no new entries this session (327) | built | `executor.update_session_brakes`, `spy_session_moves` `executor.py:104` | Since-open leg refreshed at checks. |
| Earnings blackout: within 3 trading days → no entry (328) | built | `gates.py:199-215`; `earnings_blackout_days=3` | |
| Earnings exit: reports before next session's close → exit in the 15:46 cycle the day before; "Hold through earnings" off (329) | built | `risk/earnings.py`, `cycle._earnings_exit_due` `375` | Unknown time-of-day counts as before the open. |
| Re-entry: sold/stopped earlier this session → no new entry until next (330) | built | `gates.py:217-222` | |
| Drawdown kill: equity 8% under peak → kill switch on automatically (331) | built | `gates.py:224-233` + `executor` activation `962-963,3611,3755` | |
| Order sanity: no order >25% equity; ≤6 orders/cycle → block and alert (332) | built | `gates.py:305-340`; `order_sanity` → `order_rejected` notification | |
| Working entry orders count in every gate (unfilled qty × (limit−stop) risk, × limit cash, positions, sector, max position); filled qty never twice; gated one by one in submission order; cancel/expiry/reject frees budget; supervised pending approval doesn't count (334) | built | `committed_entries` `math.py:152-182`; sequential gating in `executor` | Entries with no usable price fail closed `working_entry_unpriced` — stricter than spec's silent zero (deliberate hardening post-scan). |
| Kill switch: manual or automatic (drawdown, repeated errors); stops new entries, leaves stops; Stop & flatten cancels orders + sells at market, MOO queued outside; rejected flatten sale keeps the stop + critical banner; resume needs typed reason, stored (336) | built | `executor.activate_kill_switch` `374`, `flatten` `471`, `resume` `450` | |
| Paused at startup: always paused; checks/cycles/stops/earnings/outcomes run normally; Resume one click; Pause anytime; separate from kill switch; both stored + survive restart; VPS clean-restart exception (338) | built | `engine/state.py:62-71,365-370`; `api/main.py:1273-1292`; `ops.resume_after_clean_restart` | |
| 3 failed cycles in a row → kill switch; only completed cycles reset; skipped/interrupted/running don't; checks have their own count; no silent model retry; shutdown waits 60 s then marks interrupted; only still-running at next startup counts as failed; startup reconciles first (340) | built | `api/main.py:1238-1256` `failed_cycles_kill=3`; `recover_interrupted_cycles`; `position_check` own count | |
| Supervised mode: entries wait for approval; expiry at next full cycle; approve re-checks gates/size/spread + new limit passing original code checks; ≤1 pending per symbol; approvals table; exits/stops/earnings/kill switch never wait (342) | built | `executor.approve`/`_approve` `830-1073`, `pending_approvals`, `expire_pending_approvals`, `approvals` table | |
| Paper reset: kill switch + close positions + cancel orders + `paper_reset` event + instruct dashboard reset; "Start new run" checks flat + equity ±1% of `starting_equity`, names the failing check; `runs` table; restarts benchmark, peak, streak, daily baseline; old runs viewable; trading resumes only via kill-switch resume (344) | built | `executor.paper_reset` `1073`, `start_new_run` `1135`; `runs` table | |
| Add symbol: sector required from fixed list; active tradable US stock/ETF at Alpaca; "Warming up" until 260 daily + 60 sessions 5m (346) | built | `universe/service.py:276-307,521-577` | |
| Gaps at source: missing daily bar counts as loaded ≤3 per 260, only on successful empty request, never in last 5 sessions; 5-min history no tolerance; Universe shows forgiven sessions + INFO log; 0 keeps old rule (347-352) | built | `universe/service.py:516-577`; `daily_source_gaps` table; `history_missing_tolerance=3` | |

### GUI

Behaviour rows only; look-and-wording differences are section D.

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Header: two fixed rows (title+PAPER+subtitle / session state + countdown, Needs you chip hidden at 0, equity USD+EUR, today's P&L, regime, Pause/Resume, kill switch); <820 px title row hidden, stats one line; Ask in sidebar + phone bar (358) | built | `components/Header.tsx`, `NeedsChip.tsx`, `PhoneBar.tsx`, `Sidebar.tsx` | [045]. Width check in D. |
| Screen list incl. Dashboard, Decision detail, Positions, Performance, Insights, LLM inspector, Settings, Universe, Strategies, Backtest, Readiness, Logs, Error pages, Sign in, States (360-376) | built | `frontend/src/pages/*`, routes `App.tsx:49-86` | `/states` dev page exists; error pages in `errors.ts` + `deploy/errors/`. |
| Phone width: <820 px sidebar → sticky top bar (mark, title, Needs you, menu overlay); no sideways scroll at 390; wide tables scroll in card (378) | built | `PhoneBar.tsx`, `Layout.tsx`; e2e width check | Verified in D at 390. |
| Needs you card: ordered item kinds 1-7, one line + one button, leaves when done; health line (Alpaca sync, stream, clock offset, keys, email); "Nothing needs you"; one endpoint + WS topic `attention`; header chip + phone bar + card share the list (380-400) | built | `api/routes.py` attention endpoint + `NeedsYouCard.tsx`; WS `attention` | |
| Positions at a glance: stop→target bar with entry + last price, distance % and ATR; amber < `gui.near_stop_atr` (1.5); dashboard order (positions under cycle strip → chart+latest decision → decisions+briefing → watchlist); Positions totals line, per-position facts, folded legs opening amber on a problem (402-409) | built | `StopTargetBar.tsx`, `Positions.tsx`, `Dashboard.tsx` | |
| Daily briefing card: text, fact chips, numbers-checked or template reason, model+prompt+cost, next briefing; before 15:00 shows previous with date; off → says so + links to Settings; briefing never changes after write (411) | built | `BriefingCard.tsx`; `reviews/service.py` status line once | |
| Model call counter: today's calls vs day's max (max calls × cycles today, 60 default); today + month cost; hover review/shadow/backtest caps; links to inspector (413) | built | dashboard tile; `inspector` link | Ask cap added by [045]. |
| Session day: dashboard "today" = current or last completed NY session, never Amsterdam day; feed titled with that session's date; countdown >24 h shows day+time (416) | built | `Dashboard.tsx`, `sessionText.ts`, `cycleCounterFormat.ts` | |
| Latest decision card: latest in-session decision or the stored reason; falls back to most recent with date (417) | built | `Dashboard.tsx` latest-decision card | |
| Feed grouped per cycle (Amsterdam time, NY hover, collapsible); changes always own rows; others grouped by action+reason ("+n more", first 8); current cycle open, earlier collapsed; grouped row → inspector filtered (418) | built | `dashboardFeed.ts`, `Dashboard.tsx` | |
| Symbol picking: watchlist row, held-symbol buttons, Find symbol; `?symbol=` in URL; default first held position else first watchlist row (419) | built | `Dashboard.tsx`, `Watchlist.tsx` | |
| Watchlist: screened mode rank column + "sorted by rank, 15 rows, then Show all"; fixed mode neither (420) | built | `Watchlist.tsx`, `watchlistFormat.ts`, `watchlistFilters.ts` | |
| Tiles link out (Positions, Model calls, Since start); approvals show expiry (421-422) | built | `Dashboard.tsx`, `ApprovalCard.tsx` | |
| Interaction rules: focus ring, hover, 44 px, card loading/empty/error states, backend banner 15 s retry, no countdown in live region, toasts per event (critical sticky), latest request wins, numbers as numbers (−), settings-driven copy, page-title state (424-435) | built | `styles/tokens.css`, `cardState.tsx`, `documentTitle.ts`, `BannerStrip.tsx`, `NotificationToasts.tsx` | Spot-verified in D. |
| Cycle counter strip: next cycle AMS+NY with countdown, one segment per cycle, "Cycle n of 6 today", run number, next position check; closed → next session's first cycle; kill → "Kill switch on · no cycles"; running → "running"; count from `cycles` rows (437) | built | `CycleCounter.tsx`, `cycleCounterFormat.ts` | |
| Risk budget card: open risk / daily loss / drawdown meters amber >75%, red at limit; room for more trades with "limited by"; sector counts; risk per trade + streak; shock brake with SPY moves; re-entry blocked symbols; hovers; refreshes with topics (439) | built | `RiskBudgetCard.tsx`, `riskBudgetFormat.ts`; risk via shared `position_size`-family functions | |
| Settings screen: section list + Find + Changed-from-default; named sections incl. Entry selection, Decision gates, Display, Prices and timeouts, Notifications folded, Readiness, History, Access, Server, Paper account; locked spec-fixed values; changed highlight + Reset; unsaved bar with required reason + Discard + Save as v<n>; typed validation; leave-with-unsaved asks; keys separate per-key flow (441) | built | `Settings.tsx`, `settingsDiff.ts`, `settingsValidation.ts`, `ServerSection.tsx`, `AccessSection.tsx` | [045]. |

### API keys

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Keys in Settings, not `.env`; `.env` only MODE/SA_HOST/SA_PORT/SA_DATA_DIR/SA_DB_PATH (447) | built | `config.py EnvSettings`, `secrets_service.py` | |
| Fernet-encrypted in `secrets`; `secret.key` at `SA_DATA_DIR` mode 600, never in DB (449) | built | `secrets_service.py`, `filemodes.py` | |
| Write-only API; reads return name, last4, saved/tested at + result; <8 chars refused at save; stored short secret shows nothing; never logged or sent to frontend; scrubber second line (450) | built | `secrets_service.py` masked hint; log scrubber `logging_config.py` | Note: a <8-char *stored* secret "shows no characters" per spec — `masked_hint` returns the full value under 4 chars only if one got in; the 8-char floor prevents that path. Edge case OK. |
| Test before save per provider; old key works until new passes; every save/test an event with last4 (451) | built | `secrets_service.py` test calls; `api_key` events | |
| Replacing Alpaca keys only while paused/kill on with no positions/orders; different paper account → "Start new run" (452) | built | settings/secrets route guards | |
| Missing/failing keys: app starts paused, banner names the key with Settings link; Resume disabled until Alpaca+Finnhub set+passing and every used provider's key passes; simulated needs none (453) | built | `api/main.py:590,985` key gate on resume | |
| `.env` import once, tested, stored, banner "Keys imported from .env — remove them"; afterwards ignored with warning (454) | built | secrets import at startup | |
| `SA_AUTH=off` only on 127.0.0.1; elsewhere refuses to start (455) | built | `auth/guard.py`, `serve.py` | |

### Notifications

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Every spec "alert" is a notification event; banner or toast in-app always (459) | built | `notifications/service.py` + `BannerStrip`/`NotificationToasts` | |
| Browser notifications while the GUI is open; Settings asks permission; SMTP via Settings with `smtp_password` write-only; test on save; "Send test" to every enabled channel (461) | built | `mail.py`, `SmtpSettings`, Settings notifications section | |
| Event table 27 rows with severities and default channels (463-491) | **differs** | `notifications/events.py` `EVENT_DEFS` (28 defs) vs `config.example.yaml` `notifications.events` (20 keys) | **Gap G1.** Events missing from config — `readiness_changed`, `another_instance`, `new_sign_in`, `sign_in_lockout`, `restarted_resumed`, `restarted_paused`, `backup_failed`, `backup_downloaded`, `disk_space`, `cert_expiring`, `clock_offset` — default to app-only (+browser when critical) because `service._channels` only adds configured channels (`service.py:212-221`). Spec defaults: `readiness_changed` browser; `another_instance` +`email`; `new_sign_in`, `sign_in_lockout`, `restarted_paused`, `backup_failed`, `disk_space`, `cert_expiring`, `clock_offset` browser+email; `restarted_resumed` browser; `backup_downloaded` email. Also `disk_space` at 90% emits critical → browser added, but email stays off vs spec "always + email on". |
| Critical events always browser, never held back; others ≤1 per 30 min per subject; quiet hours 23:00–08:00 Amsterdam hold non-criticals → one summary at the end; sends in background; 3 failed emails → banner (493) | built | `service.py:198-294`; `EMAIL_FAILURE_BANNER_AT=3`; `_in_quiet_hours`, `_repeat_limit`, quiet summary | |
| Subject `[Stock Trading Agent · PAPER] <event>: <one line>`; body figures, AMS+NY times, what the agent did, what to do, GUI link; briefings/reviews carry text; never keys (495) | built | `SUBJECT_PREFIX`, `_one_line`, mail body builder | |
| Event×channel choices, repeat limit, quiet hours are versioned settings; SMTP saved separately (497) | built | `settings_service.py` `notifications` spec | |

### Data model

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| SQLite one file + Alembic; times UTC; money Decimal USD; log index separate file (501) | built | `db/models.py`, `alembic/`, `logging_config` index | |
| Tables: bars_5m, bars_1d, cycles, decisions, orders, fills, positions, outcomes, equity_snapshots, day_trades (kept, unwritten), earnings (time_of_day mapped from Finnhub bmo/amc/dmh + raw), corporate_actions, settings_versions, strategies, shadow_decisions, shadow_positions, shadow_equity, backtests, llm_cache, universe_pool, universe_screens, notifications, secrets, events, readiness_snapshots, readiness_manual, users, sessions, sign_in_attempts, insights_snapshots, log_reviews, llm_calls (505-535) | built | `db/models.py` — all present | Plus `approvals`, `runs`, `reviews`, `verdicts`, `ask_threads`, `ask_messages`, `ask_notes`, `smtp_settings`, `bars_1h`, `bars_1h_state`, `daily_source_gaps` — extra tables the spec's list doesn't name (`approvals`, `runs`, `reviews`, `verdicts` are described in their own sections; `smtp_settings`, `bars_1h`, `daily_source_gaps` are not named anywhere in the table — spec gap worth listing). |
| WAL + busy timeout 30 s; no write lock across network calls; daily 03:00 Amsterdam copy via online backup API + `integrity_check`, kept 14 days; migrations never edited; startup schema-vs-models check (537) | built | `db/base.py`, `db/backup.py`; `api/main.py:1438`; startup check | |
### Testing and engineering rules

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| No network in tests; sockets blocked (543) | built | `backend/tests/conftest.py` socket block (`pytest-socket`-style) | |
| `MODE=simulated` runs the full cycle on fakes at accelerated time (544) | built | `MODE=simulated` paths, e2e seeds | |
| Time injected; no `datetime.now()` in engine code (545) | built | `market/clock.py` `Clock`; ruff `DTZ` | Two known non-engine strays from the scan: `api/main.py:204` backup job and `main.py:385` fall back to real now — fixed? (scan said non-engine; rule says "engine code" so technically allowed; noted). |
| Required tests list (546) | partial | tests exist for each area | Per-item check in section E. |
| One risk-math function for stop distance, risk, RR (547) | built | `risk/math.py:31` `trade_risk` | |
| Never kill processes by name; only own PID (548) | partial | `backtests/manager.py` kills by stored PID | Scan finding 3: PID-reuse edge (`_pid_same` start-time check added later? `manager.py:386-408` now compares start time — recheck). |
| Secrets never printed/logged/committed; `.env`, `data/` gitignored; tests use fake keys (549) | built | `.gitignore`, scrubber, gitleaks | |
| Spec binding; behaviour change → SPEC + CHANGES (550); PR log (551) | process | — | This PR follows it. |

### Reviews

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Weekly Saturday 10:00 Amsterdam covering Mon–Fri; facts list; model text ≤120 words, ≤3 observations, ≤1 suggestion "not applied" (601) | built | `reviews/service.py`, `facts.weekly_facts`; `reviews.max_words.weekly_summary=120` | |
| Daily briefing trading days 09:00 NY; open positions w/ stop distance, earnings today/tomorrow, regime, yesterday, banners; ≤80 words; can be off (602) | built | `facts.briefing_facts`; `reviews.briefing_*` | |
| Verdict on every model enter (incl. blocked) and skip (not rules no_calls) after 5-session outcome; draft good/bad/unclear + reason + thesis check; Marc confirms; only confirmed count (603, 607) | built | `reviews/service.py:786` `write_due_verdicts`, `facts.verdict_facts`; `verdicts` table | |
| Number check: every number matches a fact after rounding; status facts by code; model may not call known-empty "unavailable"; fail/no key/call fail/cap → plain template labelled with reason; failure logged (605) | built | `reviews/numbercheck.py`, `_fallback` `service.py:283`, templates | |
| Verdict facts: model_action, option_id, final_action, changed_by_code + reason, execution, checks_at_decision (v3 rules from stored input), option rr + touch, draft_verdict (609) | built | `facts.verdict_facts` `962`, `checks_at_decision` `882` | |
| Draft label table: enter→target first good / stop first bad / else unclear; skip w/ options per table; skip w/o options on 5-session move vs `reviews.skip_flat_pct` (610-622) | built | draft-by-code in verdicts writer | `reviews.skip_flat_pct` — **not in config.example.yaml** (`reviews:` block has no skip_flat_pct). Check B. |
| Model text: reason + thesis check (consistent/contradicts/unclear + contradiction naming the claim); number check covers both (624) | built | `prompts/verdict.v2.md` + validation | |
| GUI: verdict shows call, code change, outcome, draft, thesis chip; v1 drafts same call/code line, no thesis check; drawer verdict block (625) | built | `VerdictButtons.tsx`, Reviews, drawer What happened | |
| Counting: confirmed verdicts in 3 groups + thesis contradictions (626) | built | `facts.weekly_facts` verdict counting | |
| Suggestion only when run has ≥10 closed trades; `suggestion_allowed` by code; disallowed suggestion dropped (INFO); `evidence` line covered by number check; else "No suggestion yet: n closed, 10 needed" (628) | built | `min_closed_trades_for_suggestion=10`; service `_compose`/validation | |
| Briefing status line by code in `status_line`, text only model sentences, shown once; stored duplicated lines de-duplicated on read (630) | built | `service.py` + shared wording source ([043] misc) | |
| Models per task from `models` list (weekly/briefing/verdict default gpt-4o-mini); temp 0.2 OpenAI; versioned prompts per task; save of another task's/unknown version refused 422; monthly cap $2, never trades budget (632) | built | `reviews.models`, `reviews.prompts`, `monthly_cost_cap_usd=2`; settings validation | |
| Missed runs written at next start labelled original date; briefing older than its trading day skipped (634) | built | `service.catch_up` `895`, `_briefing_slot` | |
| `reviews` and `verdicts` tables with spec'd columns (636) | built | `db/models.py:479,515` | |
| Reviews screen: Weekly + Briefings tabs, "Verdicts to confirm"; drawer verdict; dashboard briefing (638) | built | `Reviews.tsx`, `BriefingCard.tsx`, drawer | |

### Larger universe (v0.2)

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| `universe.mode` fixed (default) | screened; switch via Settings, applies next daily screen, never mid-session; screened refused until PR 12 wired (644) | built | `universe/service.py` `mode()`; config `universe.mode` | |
| Pool: `pool.csv` 72 symbols w/ sectors; loaded once; then edited on Universe screen (add w/ sector, CSV import, remove); ≤150 symbols; add checks active+tradable+sector; warm-up (646) | built | `universe/service.py:175-450`; `universe.pool_file`, `max_pool=150` | `pool.csv` — verify row count = 72 (B). |
| Daily screen 08:10 NY on trading days after daily bars: history, active+tradable, close ≥$10, one share fits max position, ≥$100M avg dollar volume 20 sessions (648-656) | built | `universe/service.py` screen; `universe.screen.*` config | |
| Hysteresis: eligible until 3 failed screens; new/warming eligible on first pass; list fixed for session; screen failure → previous list + banner; missed 08:10 → run at startup; can't finish before first cycle → previous list + WARNING + banner with list date; held symbols always handled; held symbol removed leaves pool on close (658) | built | `universe/service.py`; `api/main.py:412-468` | |
| Rank: 20-session return minus SPY's, ties by dollar volume; shown on Universe + dashboard; in input snapshot (660) | built | `universe/service.py` rank; `rank_by=return_vs_spy`; snapshot `rank` | |
| Cycles at scale: eligible + held; multi-symbol bar requests; quotes/news only for called symbols; calls held first then rank order ≤10; rest `no_call` `call_cap`; shared 150/min; cycle duration stored; >10 min → warning banner; 5-m bars >120 sessions deleted after 03:00 backup (662) | built | `engine/cycle.py` fetch scope; `bars_5m_keep_sessions=120`; `cycle_duration_warn_minutes=10` | |
| Universe screen: mode, counts, funnel, pool table with all columns + filters/search/add/CSV/remove; statuses; dashboard watchlist filters + search; Settings universe mode + thresholds; fixed mode → fixed list + pointer (664) | built | `pages/Universe.tsx`, `Watchlist.tsx` | |

### Strategies and shadows (v0.2)

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Strategy = kind, prompt version, model, temperature, rule/check overrides incl. `entry_cycles`, `selection`; risk gates/sizing/re-entry/earnings never overridable; live = current settings; rules_only geometry (entry −2×ATR stop, 2R target, exits via stop/target/earnings) (670) | built | `strategies` table; `shadows/portfolio.py`, override validation | |
| ≤3 active+paused shadows; paused shadow: no calls/entries but keeps managing virtual positions (672) | built | `shadows` runner; `shadows.max_active=3` | |
| After live cycle's orders sent, shadows run on stored input snapshots; market part reused, position part rebuilt; held first then rank ≤10; never delays live — skipped+recorded `missed` (time/cost_cap); live builds snapshots for shadow-held symbols w/o deciding them (672) | built | `shadows/runner.py`; `shadow_decisions.missed*` columns | |
| Virtual trading: live-run start equity; orders timed at scheduled cycle time; fills on later bars through/at limit; stop on fill bar, target next bar; same-bar → stop; gap → open; market exits next bar open + slippage; live price reused under same rules; else `synthetic` from last bar close (limit +0.05%, stop ≥1.5×ATR, `reference_missing`); no order-time re-fetch (slight edge, shown); slippage/dividends/re-entry/earnings as live; live-blocks apply to shadows under same gate name; Stop & flatten flattens shadows; all other gates on virtual portfolio, shock brake market-wide; no broker client reachable (674) | built | `shadows/portfolio.py`, `runner.py` | "no broker client" proven by test (build-plan done-when). |
| Shadow monthly cap $5 → LLM shadows pause to next month + warning; rules-only keep running; never live budget (676) | built | `shadows.monthly_cost_cap_usd=5`; runner cap check | |
| `model_prices` folded into `models`; startup refuses unpriced live/review/active-shadow model; settings refuses; pickers offer only priced; cost stored per call (676) | built | `models` list; `llm/models.py` | |
| Comparing: per strategy adjusted return, vs SPY, won/lost, avg R, maxDD, cost, "same as live" with count; live via same adjusted function; disagreements list w/ 5-session outcome; weekly facts include it; new run restarts shadows (678) | built | `shadows/compare.py` | |
| Promote ≥20 closed trades → Settings pre-filled as new version; shadow archived; never automatic (680) | built | `promote_min_closed_trades=20`; Strategies Promote | |
| Strategies screen: comparison table, small-sample warning, equity curves, selected strategy config + Pause/Archive/Promote, disagreements; New shadow dialog (682) | built | `pages/Strategies.tsx` | |

### Backtesting (v0.2)

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Live cycle code on injected clock + fake broker; per-run folder `run-<id>/` with per-strategy live-schema DBs + read-only `market.db`; lockstep sessions; live DB only gets `backtests` row; delete removes folder+row; size on disk shown; never touches Alpaca account (688) | built | `backtests/bundle.py`, `manager.py`, `sources.py` | |
| Child process, one at a time, lower priority (`process_nice=10`); progress/cost/status on row; cancel flag checked per session, stop by PID after 60 s; pause flag during live cycles/checks before downloads and calls; shutdown cancels child first; orphaned run → `interrupted` keeping results (690) | partial | `backtests/manager.py` | Scan finding 3: stored-PID kill could hit a recycled PID; `_pid_same` start-time check exists (`manager.py:398`) — recheck whether it fully closes it. |
| Data: `bars.db` store split-adjusted never auto-pruned; fill period + lookback, live-cache copy first, then ≤40 Alpaca calls/min pausing during live work; run reads store read-only; store size shown; "Clear bar store" only when idle; backup covers live DB only; historical news + Finnhub earnings history; inputs cut at decision time with live delays (16 min, news before decision); missing earnings days block entries + say so; spread assumed 0.05%, limit from last 5-m close (692) | built | `backtests/sources.py`, `bundle.py`; `backtest.*` config | Lookahead tests exist. |
| Fills on 5-m bars: entry through/at limit before timeout; stop+target same bar → stop; slippage per side editable; zero commission (694) | built | `backtests/broker.py` | |
| Model calls only from day after cutoff (`models.cutoff`); earlier refused before run; cache key provider+model+temp/effort+prompt hash+snapshot hash; live/shadows write-only; cached decisions marked `cached`; estimate: sessions, bars, calls, cache hits "≈", cost, cost-at-cap; calls = sessions × cycles × avg real calls; Run disabled when expected > cap left, warn when only cap-case over; $20 monthly cap → clean stop keeping results (696) | built | `backtests/estimate.py`; `decide/llm_cache.py`; `decisions.cached` | |
| Run: ≤3 years, fixed or screened (day-by-day, survivorship flag), strategies (live, shadows, rules-only), starting equity, slippage (698) | built | `backtests/manager.py` | |
| Results: return, vs SPY, maxDD, trades, win rate, avg R, profit factor, time in market; curves; by regime/symbol; trade list; decisions in the same drawer; "Read with care" list; CSV export (700) | built | `backtests/results.py`; `pages/Backtest.tsx` | |
| Backtest screen: form + quick ranges + strategy picker + estimate + cutoff; runs list status/progress/cost/cancel; results (702) | built | `pages/Backtest.tsx` | |

### Readiness

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Readiness screen; nothing unlocks; all criteria + manual items = condition to start the live spec (706) | built | `readiness/service.py`, `pages/Readiness.tsx` | |
| Current-run scope, adjusted P&L; evaluated after each session close; stored snapshot; screen reads latest, never recomputes (708) | built | `readiness/service.py` `write_snapshot` `1345`, `payload` `1583` | |
| Statuses pass / not yet / waiting for data / to do (708) | built | `service.py` status constants | |
| 15+2 criteria table with defaults and minimum samples (710-728) | built | `service.py:611-1204` — all criteria present | `readiness.*` config keys match; two log criteria read the log index. |
| Estimates for unmet counts + session-date "Earliest possible" = latest estimate (730) | built | `service.py` `_fmt_estimate`, `_date_after_sessions` | |
| Errors auto-resolve after 1 h quiet or dismiss with note; only errors linked to a decision/order/fill count (732) | built | `apply_auto_resolution` `315`; `dismiss_error` `429` | |
| State-describing errors never resolve by time; dismiss refused while the state lasts (733) | built | `state_error_state` `356` ([050]) | |
| Log review workflow: Expected/Fixed + note; unreviewed ERROR in last 10 sessions fails; any CRITICAL in last 20 fails; index unavailable → "not yet" with reason, never pass; reviews in Readiness History (735-741) | built | `_log_criteria` `1255`; `log_reviews` table; Logs review dialog | |
| Manual items list; ticking asks a note, stored with date (743) | built | `readiness_manual` table; `set_manual_item` `463` | |
| History: every status change + manual change + threshold change with date; thresholds in Settings; zero-tolerance + manual items fixed; new run resets evidence (745) | built | `_history` `1466`; `readiness` settings spec | |
| "Readiness changed" Info notification (747) | built | `service.py:1414` | Channel default gap — see G1. |
| GUI: verdict banner, tiles, groups w/ value+target+progress+status, manual items, History, hover explanations, links (749) | built | `pages/Readiness.tsx` | |

### LLM call history

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| One row per request actually sent (live, shadows, reviews, ask); non-calls never appear; backtest calls stay in their run DB (755) | built | `llm_calls` writes in `decide/service.py`, `reviews/service.py`, `ask/` | |
| Outcome set once at write time, never recomputed; error > warning; multiple warning reasons allowed (757) | built | `llm_calls.outcome/reasons` written in the same transaction | |
| Outcome table incl. all warning reasons: `changed_by_code` (+change reason), `input_incomplete`, `number_check_failed`, `slow` (>15 s), `unsupported_claim`, `unresolved_conflict`, `confidence_outlier`, `best_option_missing/mismatch`, `hold_below_breakeven`; error reasons `timeout`, `api_error`, `invalid_output`, `refusal` (759-763) | built | inspector outcome computation in `decide/service.py`/`inspector/`; `inspector.slow_call_seconds=15` | |
| Reduced size is not a warning (765) | built | outcome logic | |
| `llm_calls` columns per spec + `provider`, `provider_request_id`, `backfilled` (767-771) | built | `db/models.py:566` (verified: provider + request id via migration `a1b2c3d4e5f7`) | `kind` gains `ask` (PR 36); `verdict_id` link exists. |
| Row in same transaction as the linked row, after the call; scrubbed error messages; headers never stored (771) | built | `decide/service.py` `_store` | |
| Prompt not duplicated: rebuilt from versioned file; hash mismatch → screen says so; user part from linked row's snapshot/facts (772) | built | `inspector/` detail; manifest | |
| Indexes (session date, sent at), (outcome, sent at), (source, sent at), (symbol, sent at); never pruned (773-774) | built | `LlmCall.__table_args__` | |
| Backfill from decisions/shadow_decisions/reviews; latency/HTTP/request id null → "not recorded", `backfilled` (776) | built | migration `n1b2c3d4e5f6` + `b2c3d4e5f6a7` | |
| Filters all in URL; named link behaviours (dashboard tile, grouped feed row, `?call=`, nav defaults) (778-782) | built | `inspectorFilters.ts`, `pages/Inspector.tsx` | |
| Filter table: Period presets default Last session; Outcome; Source default Live+Reviews+Ask (shadows, backtests off); More (symbol, action, reason incl. changed_by_code × reason, model, prompt, strategy, cycle) (784-789) | partial | `inspectorFilters.ts`; `inspector.default_sources: [live, review]` in config | **Gap G2.** Config default omits `ask` — spec says Ask is on by default. Check the code default (config read fallback may include ask; the example lists only live+review). |
| Dates = NY session dates; counts under other filters; clear filters; phone "More filters" button w/ count (791-794) | built | `Inspector.tsx`, `inspectorFilters.ts` | |
| Summary: calls+s/w/e shares; cost by source; latency median+p95 (returned calls); stacked bar per session (per week >20), click sets period (796-800) | built | `inspector/` summary endpoint + page | |
| List: newest first w/ switch; grouped per session sticky header; row fields; pages of 50 keyset (sent,id), "Load more" + "n of total"; details; phone details-below + Back (802-809) | built | `Inspector.tsx`; `inspector.page_size=50` | |
| Details: outcome block w/ one-line result + reasons + explanation; call data; links + Copy link; prompt then raw response; Error block instead for failed calls (811-816) | built | `Inspector.tsx` detail | "input snapshot as sent" + "Stored input" ([034] fix). |
| CSV export of metadata, no prompts/responses (818) | built | `GET /api/llm-calls/export.csv` | |
| API: list w/ cursor ≤100, summary, `{id}` w/ rebuilt prompt, export; 300 ms on 100k-call fixture (820-825) | built | `api/inspector.py`; perf test exists | |
| GUI keeps place in Learn group; interaction rules (827) | built | `Inspector.tsx` | |

### Tail metrics

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Scope: closed trades of current run, adjusted P&L; 1R = initial risk; adopted w/o initial stop excluded + counted (833) | built | `engine/performance.py` `compute_tails_payload` | |
| Metric table: R distribution 0.5R buckets −1.5R…3R+; largest win/loss w/ symbol+reason+dates; top-20% share (ceil, of winning P&L, hidden w/o winners); net w/o best; winners kept = realized R ÷ best R; best after exit (5 sessions); close after exit (837-843) | built | `performance.py`; `performance.tail.*` config | |
| After-exit medians per exit reason + target-exit share reaching 1R; per-trade table; pending inside window; upper-bound vs realistic labels (845) | built | `TailsCard.tsx` + payload | |
| Job once per day after close (after readiness); fills values whose 5th session closed; split-adjusted bars, dividends ignored; missing bars fetched via limiter; 3 failures → `unavailable` "—"; once filled never changes; no write lock during fetch (847) | built | `engine/after_exit.py` | |
| Small samples <20: "n of 20 trades" + note; values still shown; distribution + table always (849) | built | `TailsCard.tsx`; `tail.min_trades=20` | |
| `positions` after-exit columns via migration; backfill on first run (851) | built | migrations `b19a0e1c2d3e`, `c1d2e3f4a5b6` | |
| `GET /api/performance/tails?run=` as numbers (853) | built | `api/routes.py:49` `compute_tails_payload` | |
| Card below By regime/symbol: banner, distribution bars, 5 tiles, after-exit medians, per-trade table, "How to read it"; hovers (855-863) | built | `TailsCard.tsx`, `Performance.tsx` | |

### Logging, error handling and shutdown

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| JSON lines to `data/logs/stock-agent.log` with the spec'd fields incl. context, process, pid, error_id + exception (870-874) | built | `logging_config.py` `JsonFormatter` | |
| File DEBUG, console INFO short format (875) | built | `logging.file_level/console_level` | |
| Queue writer: listener thread, never blocks a cycle (876) | built | `LogPipeline`, `_QueueHandler` | |
| Rotation 50 MB gzip; delete >30 days or >20 files (877) | built | `GzipRotatingFileHandler` + `_prune` | |
| Backtest child writes `backtest-<id>.log` same format (878) | built | process field `backtest-<id>` | |
| Scrubber on every handler: stored keys, key-like patterns, Authorization → `[redacted]`; prompts/responses never logged (879) | built | `SecretScrubFilter` `logging_config.py:129` | Scan verified. |
| Level table DEBUG/INFO/WARNING/ERROR/CRITICAL semantics; DEBUG steps + INFO milestones everywhere with cycle/request id (881-889) | built | `ev()` helper used across components | Spot-verified: cycle, reconcile, executor, model calls. |
| Tracing ids `c-0412`/`chk-` via context var; symbol + decision/order ids; API `X-Request-Id` (891-895) | built | `log_context`, `_ContextFilter`; request-id middleware | |
| No swallowed exceptions; three allowed outcomes; ruff E722/BLE001/S110/TRY400/TRY203; `noqa` needs reason (898-902) | built | `pyproject.toml` ruff config; scan verified | |
| Global hooks: excepthook (sys+threading), asyncio handler, `spawn()` only (bare `create_task` fails a test), scheduler job listener, `captureWarnings`, library loggers ≥WARNING unless `library_debug` (903-909) | built | `logging_config.py`, `spawn.py`, `api/main.py` | websocket.py `ensure_future` documented exception (scan). |
| API 500 returns `error_id` matching the log line (910) | built | api error handler | |
| Frontend errors → `POST /api/logs/client` ≤10/min per address; logged ERROR `frontend`; error boundary shows error_id + Reload (911) | built | `api/routes.py` client-logs; `ErrorBoundary.tsx` | Scan: rate limit is per address, senders table ≤1000. |
| Log line never sends a notification by itself (912) | built | notifications only from producers/events | |
| Graceful shutdown steps 1-7: banner + scheduler pause + writes 503; ≤60 s wait; shielded order submit→store; cancel rest → `interrupted` + CRITICAL for unknown orders; backtest cancel flag then PID stop after 10 s; emails ≤5 s; clients closed; commit/rollback + WAL TRUNCATE + dispose; log closed last + final line (915-922) | built | `api/main.py` lifespan + `shutdown.py`; `executor._order_slot` | |
| Second Ctrl+C → WARNING + skip wait; steps 6-7 always run (923) | built | shutdown handler | |
| Every unit of work one short transaction (924) | built | [043] hardening | |
| `run.lock` (pid, start) + 30 s heartbeat; clean shutdown deletes; stale → WARNING + `PRAGMA quick_check` + interrupted marking + reconcile + banner "previous run did not shut down cleanly" w/ last-alive/down/missed; same-pid → refuse start (925) | built | `restart.py`, `filemodes.py`; `unclean_summary` ([050]) | |
| Log index `logs.db` separate file, `log_lines` w/ fingerprint + FTS + indexes; never in backup; pruned with files (928) | built | `logging_config` index writer | |
| Index failure → file keeps working, failure to file+stderr, `make logs-reindex` (929) | built | index fallback path ([049] fixed schema-less file 503s) | |
| Fingerprint = logger+event+message w/ numbers/symbols/ids as placeholders (930) | built | index fingerprint | |
| Log endpoints: `/api/logs` filters+cursor ≤200, `/summary`, `/{id}/around?n=50`, `/export.jsonl`; WS topic `logs` debounced 1 s (931-936) | built | `api/routes.py` log endpoints | |
| Logs screen: level toggles w/ counts (W/E/C default), debug+info switch, `?levels=`, remembered; period presets; more filters (search, component, symbol, cycle; phone behind button); clear + download; per-day stacked bars clickable; recurring problems w/ review state + Review dialog; grouped sticky headers w/ context chips linking; opened line: traceback, stored JSON, surrounding lines +50, copy, link; follow-new-lines pausing when scrolled; card states (938-952) | built | `pages/Logs.tsx`, `log_reviews` | |
| `events`/`notifications` link to Logs filtered on time ±2 min + cycle (954) | built | GUI links | |
| PR-20 audit + order of work (956-961) | done | `docs/analysis/2026-10-24-error-handling-audit.md` | Historical. |

### Decision v2: code-built options

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Levels per cycle: 1h swings (strength 2, lookback 60, newest two excluded), daily swings (60 sessions), session levels (prior H/L/C, today H/L, VWAP); ≤8 each side nearest first; ids L1… from highest down (971-975) | built | `decide/levels.py:135-253` | |
| Entry options after price checks: stops at level −0.1×ATR + reference−k×ATR (k=[2.0]); kept only 1.5–4×ATR below reference (strategy override); dedupe 0.5×ATR keeping level-based; ≤3 stops nearest first (977-983) | built | `decide/options.py:189-334` | |
| Targets: level −0.1×ATR below + L+r×risk (r=[2.0,3.0]); kept only RR≥1.5; same merge; `levels_in_way` ids strictly between L and target (985-988) | built | same | |
| Option list ≤6 lettered A–F by stop nearest then lowest RR; fields per option; `no_options` no_call with builder reason (989-992) | built | `options.py` `EntryOptions.reason` | |
| `constraints` block (atr_1h, entry_limit, allowed stop range, min_rr) (993) | built | `build_v2_fields` | |
| Stop options T1–T3 (last 1h swing low, break-even, trail highest close−2×ATR); kept only above current stop + tighten rule; `locks_profit`, `r_at_stop`; none valid → tighten not allowed; position carries original thesis+invalidation (995-1000) | built | `options.py:334-450` `build_stop_options` | v3 modifies these — see v3 rows. |
| `decision.v2.md` strict schema: no price fields, `option_id` A–F/T1–T3, confidence = P(target before stop) (1002-1005) | built | `prompts/decision.v2.md`; v2 schema in `output.py` | C checks text. |
| Code checks: invalid option id → skip/hold; chosen option's distance+RR rechecked then confidence + gates; order-time recheck of stop distance and RR, failure = Fresh quote gate; never another option picked (1007-1013) | built | `output.py:753-…`; `executor._fresh_entry_check` | |
| Explanation check: every level id must exist; every decimal must match a snapshot/option number after rounding; whole numbers skipped; failure → decision stands, failing tokens stored + shown, `unsupported_claim` warning (1014-1015) | built | `output.py` explanation check + drawer | |
| Strategy prompt version decides schema; builder uses strategy overrides; v2 in shadows/backtests; backtests build from bar store same cut-offs (1017-1020) | built | strategy `options_version`; `backtests/bundle.py` | |
| `make replay` with statuses replayed/no_options/not_replayable(+reasons), `--approx-reference` → `replayed_approx` own section, price never reconstructed; report fields incl. rates over replayed only + entries per session + live-entry comparison + rules-only baseline; counts toward backtest cap; $2 confirm; never writes live tables (1022-1044) | built | `app/replay.py`, `replay_selection.py` | Report verified by [021]/[030]. |
| Path to live: replay → shadow → Marc (1046-1049) | process | — | |
| Readiness under v2: pass = valid id + explanation check + order-time recheck-or-no-order (1051-1056) | built | `readiness/service.py:859-908` `_v2_passed` | |
| Storage: `levels`, `options`, `chosen_option`, `explanation_check`, `options_version`; `no_options` reason added (1058-1064) | built | `decisions` columns; migration `c3a9f1e2b7d4` | |
| GUI v2 drawer blocks (options, levels, explanation check, gates, reasoning, tiles) + latest-decision card "D · stop below L8 · target 2R" (1066-1076) | built | drawer v2 path + dashboard card ([045] one-component order) | |

### Insights

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| One function after each session close (after readiness) per active strategy × 3 periods (run, 20 sessions, all runs); stored snapshot, never recomputed; adjusted P&L; 1R initial risk (1082-1085) | built | `insights/service.py`, `compute.py`; `insights_snapshots` | |
| Headline numbers: expectancy ±1.64 SE, edge over SPY at same exposure, beta (20 sessions), overnight vs in-session, gaps through stop (>0.1R), model cost per trade (1087-1096) | built | `compute.py:208-262,1093` | |
| Findings: ≥30 trades + ≥2σ difference; 10–29 early signals greyed; <10 hidden one line; newest first w/ duration; "Test in a shadow" when dimension maps to an override (incl. `entry_cycles`); wording notes multiple testing + "a finding is a question"; weekly facts include new/stopped findings (1098-1104) | built | `compute.py` `_sample_status` `646`; `insights.min_trades/early_trades/finding_sigma` | |
| Funnel steps with losses attributed (rules, call cap, price checks, model skips, each code check, each gate, unfilled) (1106-1116) | built | `compute.py:340-473` `_funnel` | |
| Slices: the 16 named dimensions; columns trades, win rate, avg R ±90%, total R, skips missed/avoided, sample status; rule-caused empties show the rule (1118-1141) | built | `compute.py` `_dimension_values`, `_slices` | 16 dimensions incl. v2 stop/target basis, prompt, model. |
| Calibration chart per 0.1 bucket w/ sample statuses; weighted gap = Readiness number; v1 says approximate (1143-1145) | built | `compute.py:1026` `_calibration` | |
| Execution: fill rate + worst cycle, entry/exit slippage bps (exits split stop/target/agent), gaps through stop, entry-day exits avg R, earnings exits avg R + median 5-session move (from PR 19), dividends (1147-1151) | built | `compute.py:1093` `_execution` | |
| Overnight: in-session vs overnight totals, nights held, share gapped against, largest gap in R w/ symbol+date (1153-1155) | built | `compute.py:262` `_overnight_split` | |
| Skipped setups: model skips scored rules-only (limit entry, stop 2×ATR, 2R, 5 sessions); missed/avoided/neither; baseline shown; "more avoided than missed" (1157-1160) | built | `compute.py:890-1026` `_score_skip`, `_skipped_setups`; `insights.skip_score` | |
| Strategy picker; rules-only: findings+slices work, calibration+skips N/A (1162) | built | `Insights.tsx` picker | |
| `insights_snapshots` columns; overnight P&L from daily bars + fills; after-exit from PR 19 (1164-1166) | built | `db/models.py:1094` | |
| APIs `/api/insights` + `/api/insights/trades` (1168) | built | `api/insights.py` | |
| GUI order: toggles + computed-at, headline tiles w/ hovers, Findings, funnel + skipped, slices w/ picker, calibration, execution, overnight; <10 trades shows funnel/skips/execution + when slices count (1170-1178) | built | `pages/Insights.tsx`; "What each filter removed" added by [045]/[037] | |

### Entry selection

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| `selection.mode` rank (default, unchanged behaviour) | expected_profit; `confidence_weight` 0.5; `last_slot_min_ev_r` null; all settings + strategy overrides (1635-1637) | built | `config.example.yaml:158-161`; `engine/selection.py` | |
| EP steps: held first incl. exits freeing slots; pre-score = dollars at risk of option-A size (max-position capped); call order pre-score then rank, cap counts real calls only; collect-then-choose with concurrent calls inside limiter; score = p=be+w(conf−be), expected R = p(1+RR)−1, dollars at risk shared sizing, expected profit; expected R ≤0 → never selected `not_selected`; exact best set within slots/sector/open risk/cash/orders-per-cycle, descending-EP sizing, ties by ranks then fewer orders, last-slot floor; submit in EP order w/ full gates + fresh quote, next-best fallback once; not chosen → `blocked` `not_selected` (1639-1653) | built | `engine/selection.py:25-…`; `cycle`/`live_calls` integration; `Executor` submit order | [044] rulings applied. |
| Supervised: chosen set becomes approvals in EP order (1655) | built | selection + approvals | |
| Storage: per-decision `selection` block; per-cycle free slots, scored count, chosen ids, rank-order total (1657) | built | decisions JSON + cycle fields | |
| GUI: Selection gate row + "Selection in this cycle" block in v2/v3 drawers; feed shows `not_selected` (1659) | built | drawer blocks ([045]) | |
| `make replay-selection` comparing modes on stored cycles; per-mode entries/R/dollars/avg expected R + differing cycles; per-cycle caveat (1661-1665) | built | `app/replay_selection.py` | |
| Then shadow + Marc switches (1667) | process | — | |
### Decision v3: checks by code, room and break-even

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Snapshot v3 (v3 strategies only): rounded numbers; no duplicate price/price_time; bid/ask only when plausible + `plausible` + `spread_unknown` (1518-1521) | built | `decide/v3.py:1000-1276` `build_v3_fields`, `_quote_v3`, `v3_send_snapshot` | |
| `checks` by code: trend_1h, trend_daily, regime_trend, regime_volatility, room_atr + room_level (counting `room_level_kinds`), earnings (days/date/time-of-day), news_status, momentum (1522-1529) | built | `v3.py` checks block; `room_level_kinds` config | |
| Levels gain `distance_atr`; levels within 0.05×ATR merged keeping highest-ranked kind, listing `kinds` (1530-1531) | built | `merge_levels` `v3.py:153`; `level_merge_atr=0.05` | |
| Options: `levels_in_way` with `distance_atr`; `breakeven_confidence` = 1/(1+rr); constraints gain `min_room_atr`, `min_confidence`, `min_edge` (1532-1533) | built | `option_json_v3` `v3.py:356` | |
| Room filter (builder v2, v3 only): remove option when first in-way level of counted kinds < 1.0×ATR above limit; all removed → `no_call` + room reason; strategy-overridable (1536-1540) | built | `apply_room_filter` `v3.py:411`; `min_room_atr=1.0` | |
| Prompt v3: conflicts → thesis → action; trend rules; high-vol rule; confidence from breakeven; `news_used` only effective headlines; `data_flags`; no reasons outside input; field guide; momentum semantics; stop/target basis not described; app name (1542-1553) | built | `prompts/decision.v3.md` | C compares text. |
| Schema v3 field order + `conflicts` replacing `risks` (1555) | built | `output.py` v3 schema | |
| Confidence gate: enter needs ≥ max(min_conf, breakeven + 0.10) → `edge_too_small` skip (1558) | built | `output.py:1416-1417`; `v3.min_edge=0.10` | |
| Data flags: enter+flag → skip `data_flag`; every flag logged WARNING + event in every context (1559) | built | `decide/service.py:360-377` | |
| Room at order time: fresh limit < min_room → Fresh quote gate fails `room` (1560) | built | `executor._fresh_entry_check` room recheck `executor.py:820-827` | |
| Explanation check as v2 against rounded v3 snapshot (1561) | built | `output.py` v3 path | |
| Held v3: `locks_profit` only above entry; `locked_r` + `distance_atr`; drop moves <0.25×ATR; break-even only from +1.0R; trail/swing ≥1.0×ATR below price; overridable; dropped options listed w/ reason (1572-1579) | built | `build_stop_options_v3` `v3.py:475-651`; `v3.min_stop_move_atr/breakeven_min_r/held_stop_min_atr` | |
| Position context: `r_now`, `stop_distance_atr`, `target_distance_atr`, `levels_to_target`; `invalidation` filled by code naming kind/time/price not id; `stop_level` matched or null; `neutral_probability` = stop dist ÷ (stop+target dist); `entry_thesis` w/ id rewriting (stored original kept) (1580-1586) | built | `position_block_v3` `v3.py:899`; `invalidation_v3`, `stop_level_match`, `rewrite_thesis_ids` | |
| `scheduled_exit` when code will sell anyway; `hold` default; earnings not a conflict (1587) | built | `scheduled_exit_text` `v3.py:868` | |
| Held confidence per action from `neutral_probability`; stored not gated; excluded from entry calibration (1588-1592) | built | `v3.py`; readiness/insights exclude held confidence | |
| Claim checks: "locks in" while locked_r ≤0; stop/target sentence w/ wrong level id → `unsupported_claim`, decision stands (1593-1595) | built | `output.py` v3/v4 claim checks ([034] fix: only attributing sentences) | |
| Code describes stop/target basis; prompt told not to (1596) | built | `StopBasisInfo`, drawer | |
| Replay v3: builds v3 from stored v2 inputs, no refetch; report additions incl. room-filtered options w/ would-be-blocked entries' outcomes, edge/data_flag skips, calibration, conflicts, data flags, v2-vs-v3, held sections (1598-1607) | built | `app/replay.py` v3 report | |
| Storage: `options_version=2`, `checks` + room reason stored, reasons `room`/`edge_too_small`/`data_flag`, dropped stop options stored w/ reasons; no migration (1614-1619) | built | `decisions` JSON columns | |
| GUI v3 drawers per design (checks block, options w/ distances + break-even, conflicts first, data flags, room gate; held: position w/ R now + distances, scheduled exit, stop options w/ locked_r + distance, dropped w/ reason) (1621-1629) | built | drawer v3 paths ([045] order) | |

### Entry selection — covered above.

### Decision v4: calibrated confidence, memory and the entry thesis

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Keeps v3; `checks_gate` strategy override; room stays 1.0 ATR (1691-1696) | built | `v4.py` `v4_config`; `decision.v4.checks_gate` | |
| Snapshot v4: `previous_decision` (same session, same context after fix) w/ action/option/best/confidence/conflicts/price + `since` (price move ATR, changed checks, new headline ids, new levels ≤ `near_level_atr` 1.0); prompt pins "1 ATR"; startup warns on config/prompt mismatch (1698-1702) | built | `v4.py:207-458` `previous_decision_v4`, `_since`; `near_level_atr=1.0` | |
| Headlines: `about_symbol` from pool names cleaned + `universe.names` override; ≤12, about first then rest; `new` vs previous input (1703-1706, 1814-1815) | built | `v4.py:117-191` `pool_names`, `about_symbol`, `_headlines_v4`; `universe/names.py` | |
| Levels only ≤4×ATR of limit + option/stop levels; stored provenance keeps all (1707) | built | `max_level_distance_atr=4.0`; `v4_send_snapshot` trims | |
| Options gain `stop_group` keyed on Decimal stop (1708, 1815) | built | `v4.py:620` `_stop_groups` | |
| Position gains `checks_at_entry` (stored or v3-recomputed), `thesis_holds` (2+ adverse of 4, sideways counts like mixed), `held_session_hours`, `sessions_held`; `session.minutes_left` (1709-1713, 1812) | built | `v4.py:458-567` `checks_at_entry`, `thesis_holds`, `session_time_held`, `minutes_left` | |
| Prompt v4: confidence per action (skip = best option w/ `best_option_id`; held anchors at neutral +0.15/+0.30 guidance); `outweighed_by` per conflict (null → candidate skip / held exit consideration); no path claims within 1 ATR; `what_changed` rules; news rules (about_symbol, new, effects); exit w/ thesis_holds false; tighten only to protect progress (1715-1733) | built | `prompts/decision.v4.md` | C compares. |
| Schema v4 order + `conflicts[{text,outweighed_by}]`, `what_changed`, `best_option_id`, `news_used[{id,effect≤12w}]` (1735-1746) | built | `output.py` v4 schema | |
| Flip-flop guard: previous same-session skip + move <0.5 ATR + no check change + no new about_symbol headline + no new level ≤1 ATR → enter becomes `skip` `flip_flop`; enter→skip never blocked (1750-1758) | built | `output.py` v4 path; `flip_min_move_atr=0.5` | |
| Confidence sanity `confidence_outlier` warning >0.30 above anchor (never a gate); stored, counted, shown (1759-1762) | built | `output.py`; `max_confidence_above_anchor=0.30`; `confidence_anchor` stored | |
| Path claim warning incl. levels within 1 ATR (1763) | built | `output.py` claim check | |
| `unresolved_conflict` warning: enter w/ null `outweighed_by`; stands, counted; enters only (1764) | built | `output.py` | |
| `best_option_id` rules per action incl. `best_option_mismatch`/`best_option_missing`, never invalid; skip calibration separate (1765-1768) | built | `output.py`; separate calibration series | |
| Claim-check fix: only sentences attributing a level to stop/target checked (1769) | built | `output.py` (B1 fix) | |
| `checks_gate`: on → candidate w/ trend_1h not up / regime not up / trend_daily down gets `no_call` `checks` (1770) | built | `v4.py` checks gate + cycle gate_detail | |
| Storage: `confidence_anchor`, `best_option_id`, `what_changed`, warnings, `previous_decision.since`; skip calibration separate; shadow input snapshots stored; minimal/no migration (1772-1776) | built | decision JSON + `shadow_decisions.input_snapshot` | |
| Eval set `backend/eval/decision_v4.json` ~65 calls; `make eval-decision` reports per category/prompt, v3-pass/v4-fail, confidence vs touches, tokens/cost/latency; cost to replay cap (1778-1785) | built | `backend/eval/`, `Makefile` `eval-decision` | |
| Path to live (1787-1791) | process | — | |
| GUI v4 drawers (previous decision, checks-vs-previous, stop groups, conflicts w/ outweighs, what changed + guard finding, news about/effects, v4 gates; held: thesis-holds block, time held, confidence vs neutral; "skip · best A · 0.38"; feed `flip_flop`/`checks`) (1793-1807) | built | drawer v4 paths; `decisionFormat.ts` | |
| Fixes of 3 Nov: thesis_holds sideways; previous_decision same-context + final_action + change_reason; about-first headlines; company names from pool + `universe.names`; inspector "input snapshot as sent" + "Stored input" (1811-1816) | built | `v4.py`, `universe/names.py`, Inspector | |

### Decision v5: expected value, exits and overnight risk

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| EV gate: per-option `min_confidence` = (1+min_ev_r)÷(1+rr) rounded up 2 dp; enter needs conf ≥ it else `edge_too_small`; `entry_gate` floor|ev (v5 default ev), strategy override; selection unchanged (1824-1829) | built | `v5.py:118-149`; `decision.entry_gate`, `min_ev_r=0.25` | |
| Trend gate code (v5): candidate w/ trend_1h or regime_trend not up → `no_call` `checks`; `trend_gate` default true, strategy override (1831-1833) | built | `v5.py:149` `trend_gate_detail`; `v5.trend_gate=true` | |
| Counterfactual touches (every strategy): removed options stored full; candidates stopped by rules/trend/price get options anyway w/ reason; fallback ref = min(price, last 5-m close) for all options versions, never offered/called/ordered, `stop_reference_price` stays null; all get touches; Insights + replay "What each filter removed" (1835-1841) | built | `cycle._counterfactual_build` `cycle.py:281-373`; `record_no_call` stores `counterfactual_reason` | |
| Base rates: `base_rate` + `base_n` per option over 60 sessions; same rr bucket (≤2, 2–3), regime vol, momentum; decided touches only; shrink (n·rate + 30·be)÷(n+30); shown from n≥30 else null; prompt starts from it (1843-1847) | built | `v5.py:213-411` `base_rates`; `base_rate_sessions=60`, `base_rate_min_n=30` | |
| `max_rr` 3.0: no v5 option above; counterfactuals still measure (1849-1852) | built | `v5.py` `_options_v5`; `max_rr=3.0` | |
| Snapshot v5: `session.last_cycle`; `overnight.gap_p90_atr` (90th pct |open−prev close| /60 sessions ÷ ATR(1h), 2 dp, null <20); options `target_atr`, `min_confidence`, `base_rate`/`base_n`; position `price_vs_signal`; fifth thesis check `price_vs_ema20` adverse ≤−0.5 ATR below EMA20; previous_decision same-context + final_action + change_reason; `checks.earnings.status` never all-null (1854-1867) | built | `build_v5_fields` `v5.py:524`; `gap_p90_atr`, `price_vs_ema20`, `thesis_holds_v5`; `last_cycle_for` | |
| Prompt v5 changes incl. far targets, <120 min + target >2 ATR = overnight conflict, "a skip is not free", per-option `min_confidence`, hold/exit lines (neutral−0.03, overnight neutral+0.10 when stop inside gap), held data flag → hold never tighten, previous_decision as data, about_symbol false only if plainly bearing (1869-1882) | built | `prompts/decision.v5.md` | C compares. |
| Exit consistency warning `hold_below_breakeven` (never a gate): hold below neutral−0.03, or overnight below neutral+0.10 w/ stop inside gap (1885) | built | `output.py` v5 checks; `v5.exit_margin=0.03`, `overnight_margin=0.10` | |
| Path claim extended: "has room", "room to run", "plenty of room", "free run" (1886) | built | `output.py` path-claim list | |
| Last-cycle held `confidence_outlier` + r_now<0 → Needs you item "KO held overnight at −0.62R with confidence 0.65 against neutral 0.12", leaves on close or next session (1887) | built | attention item | |
| GUI v5 drawers + Insights "What each filter removed" + overnight Needs you per spec text (1889-1893) | built | drawer v5; `Insights.tsx` filter card | [045] built the drawers; D verifies. |
| Replay + eval incl. KO case, per-gate report sections (1895-1900) | built | `app/replay.py`, `backend/eval/` | |
| Shadows F/G/H to start after PR 37 (1902-1907) | process | — | Marc's action, not code. |

### Ask the agent (PR 36)

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Read-only; no trading/settings/pause/approve; no web; no own memory writes; paper-account framing (1921-1926) | built | `ask/tools.py` read-only; `prompts/ask.v1.md` | |
| Sidebar button + phone bar + Ctrl/⌘J on every screen; "Ask about this" on Needs you + decision drawer; Escape closes keeping conversation; ≥1280 px docked 440 px, else overlay, phone full-screen; next to an open decision drawer w/ decision context (1928-1933) | built | `Sidebar.tsx`, `PhoneBar.tsx`, `ask/` drawer | |
| Page context object (screen, record, filters, session date) shown as removable chip; one-line summary into first message; "Switch to this page" offer on navigation (1935-1943) | built | `frontend/src/ask/` context | |
| Threads: New, list w/ search, 90 days, code title ≤60 chars (1946) | built | `ask_threads`; `ask.keep_days=90` | |
| Streaming steps then answer; Stop keeps partial marked; next question queued while streaming (1947-1950) | built | SSE `POST /api/ask/threads/{id}/messages` | |
| Follow-ups: whole thread; reopen looks data up again; >60k tokens: drop tool results then turns w/ a line (1951) | built | `ask/service.py`; `ask.max_context_tokens=60000` | |
| Three fixed suggestions per screen (1952-1958) | built | frontend suggestions map | |
| Answer shape: short first, limited Markdown, no HTML/images/external links, AMS times (NY where it matters), USD, R (1959-1963) | built | renderer | |
| References `[[decision:…]]` etc. as chips; unresolved ref → plain text + WARNING (1964) | built | reference resolver | |
| Number check: every number from a tool/calculate result after rounding; `checked_numbers` stored, reusable in-thread incl. reopened; unmatched → amber underline + "n number(s) not found"; not blocked (1965-1970) | built | number check in `ask/service.py`; `ask_messages.checked_numbers` | |
| Answer footer: model, lookups, cost, "data as of", Copy, inspector link (1971) | built | drawer footer | |
| Standing instructions ≤1000 chars in system prompt as Marc's preferences, behind safety rules; versioned setting (1974-1977) | built | `ask.instructions` setting + spec | |
| Saved notes: from answer (first paragraph editable, keeps refs/chat/date) or "+ Write a note"; list w/ Edit/Delete; ≤1000 chars, ≤100 notes; notes tool lists w/ date/author/refs; treated as dated conclusion, checked against today's data; note numbers only as "the note said" (1978-1985) | built | `ask_notes`, `notes` tool | |
| Earlier chats not read (1986) | built | design of the tool set | |
| Tools table: all 21 read-only tools with limits, `as_of`, `ref`, `truncated` + hint (1988-2014) | built | `ask/tools.py` — all present incl. `explain_rule`, `calculate`, `notes` | |
| ≤8 tool rounds; ≤90 s/answer; tool result ≤6000 tokens w/ `truncated`; failing tool returns error (2016-2019) | built | `ask.max_tool_rounds/max_tool_result_tokens/answer_timeout_seconds` | |
| `ask.model` from `models`, stronger than decisions; Settings select shows the 4 named models marked recommended/budget; `converse` in the model interface + adapters + fake; temp 0.2 OpenAI; `ask.v1.md` versioned; each round an `llm_calls` row source `ask` (2021-2030) | built | `ask.model=claude-sonnet-5-5`; `llm/` `converse`; `llm_calls.source="ask"` | |
| Cost: cached prefix counted at cached prices; per-answer cap $0.25 → final call w/o tools + note; monthly cap $15 own budget, cycles first on limiter; cap → composer replaced by notice, chats readable; ≤10 questions/min; question ≤2000 chars (2033-2038) | built | `ask.*` config keys; service caps | |
| Safety: read-only tools + routes behind gate+sign-in; no tool returns secrets (scrubbed); untrusted text as data; limited-Markdown rendering; no-advice handling (2040-2045) | built | tools + routes + renderer; eval `ask_v1.json` | |
| Settings group: on/off, model, monthly cap + spend, questions + avg cost, per-answer cap, keep days, instructions (2047) | built | Settings Ask the agent group | |
| Storage `ask_threads`, `ask_messages` (incl. `checked_numbers`), `ask_notes`; tool results not stored; >keep_days deleted after 03:00; note survives chat deletion (2049-2062) | built | `db/models.py:671-741`; cleanup job | |
| API routes incl. SSE events `step`/`delta`/`refs`/`done`/`error` and stop; all behind the gate (2064-2071) | built | `api/ask.py` | |
| Eval `backend/eval/ask_v1.json` ~25 questions w/ properties; `make eval-ask`; ≥90% on default (2073-2083) | built | `backend/eval/`, `Makefile` `eval-ask` | |
| GUI per AskDrawer/AskStates designs (2085-2089) | built | `ask/` frontend | D spot-checks. |

### VPS deployment

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Docker Compose `app` + `caddy`; app: one Uvicorn worker, non-root, no-new-privileges, caps dropped, read-only root, writable only `/data`, internal network only, `python -m app.serve`, `/healthz` 200 + version only, `stop_grace_period: 90s`, `unless-stopped` (1191-1198) | built | `deploy/` Compose + `Dockerfile`; `api/main.py:1781` `/healthz` | Files exist; contents spot-checked. |
| Caddy: only public entry, auto HTTPS + redirect; HSTS + Permissions-Policy always, CSP/XFO/nosniff/Referrer/COOP only when app hasn't sent them; forwards Host + real client address; app trusts XFF only from Caddy (1199-1202) | built | `deploy/Caddyfile` | |
| Server hardening scripted: SSH keys only, fail2ban, ufw 22/80/443, auto updates Sunday 05:00 AMS reboot window, chrony; clock checked hourly >1 s warning (1204-1208) | built | `deploy/provision.sh`; `ops/service.py` `check_clock` hourly job | |
| Sign-in: one user via `make create-user`, no sign-up; password ≥14 Argon2id; TOTP 6-digit + 10 recovery codes shown once (1210-1213) | built | `auth/service.py`; `Makefile` `create-user`; `auth.min_password_length=14`, `recovery_codes=10` | |
| `SA_AUTH` required off-loopback, `off` refused elsewhere (1215-1217) | built | `auth/guard.py`, `serve.py` | |
| Session: random token HttpOnly+Secure+SameSite=Strict `__Host-sa_session` over HTTPS, server stores hash; new token per sign-in; password change signs out others; idle 12 h, absolute 30 d w/ keep-signed-in; Settings → Access lists + signs out (1219-1221) | built | `auth/service.py`, `sessions` table, `AccessSection.tsx` | |
| Every API route + WS need session for every method; only `/healthz` + sign-in open, returning version/auth-required only; writes need CSRF double-submit; WS checks Origin (1223-1225) | built | `auth/guard.py` route walk; gate middleware | Route-walk test exists. |
| Rate limits: 5 fails/15 min per address → 15 min wait; 20 fails/hour global → 1 h pause + warning; errors never say which factor failed (1226) | built | `auth/service.py`; `auth.lockout_*`, `global_failures_per_hour` | |
| Confirm/password-change wrong answers count toward the session's attempts; 5 in 15 min → sign-out + warning (1227) | built | `auth/service.py` confirm attempt recording | Scan finding 4 noted missing rate-limit on confirm — the [042]/[043]-era fixes added `require_confirmed` coverage; re-verify in E? Marked built w/ note: spec'd counting exists; the scan's "no rate limiting" was fixed by the confirm-attempt path. |
| Code step: wrong code counts as failure; ticket single-use, browser-bound, dropped after 3 wrong codes; used code refused (1228) | built | `auth/service.py` ticket flow | |
| Risky actions re-confirm (valid 5 min): flatten, kill-switch resume, reset, new run, promote, key replace, password/authenticator/recovery changes, loosening-risk settings save, SMTP server/To/heartbeat changes (1229-1234) | built | `require_confirmed` dependency + `ConfirmYouDialog.tsx`; `auth.confirm_minutes=5` | |
| Fails closed: `SA_AUTH` enum parsed once; `required` → startup refuses unless every non-open route carries session (1235) | built | `auth/guard.py` startup walk | |
| Notifications "New sign-in" info browser+email, "Failed sign-ins" warning (1236) | built | `new_sign_in`, `sign_in_lockout` events emitted | Default channel gap — G1. |
| Server secrets: keys over HTTPS; `secret.key` on volume mode 600; `backup_password`/`backup_credentials` in `secrets` (1238-1241) | built | `secrets` table names | |
| Moving to VPS steps + `SA_INSTANCE_ID` in client order ids; foreign-instance order/fill = critical mismatch → kill switch + banner + notification (1243-1251) | built | `broker/client_id.py`; `executor._check_foreign_instance` `1292` | |
| Restarts: default paused; VPS clean-restart resume when (was running + clean shutdown + kill off + reconcile clean); planned restarts incl. deploy/Sunday reboot; banner + notification; else paused + warning banner (1253-1262) | built | `api/main.py:1273-1292`; `ops.resume_after_clean_restart=auto` | |
| Deploy: CI builds versioned image per tag; `deploy.sh` refuses in session w/o `--emergency`, backs up, pulls, migrates, restarts + health check + clean reconcile; rollback within 2 min restoring backup if migration ran; Settings → Server shows version + deployed at (1264-1273) | built | `deploy/deploy.sh`, CI workflow | Scripts exist; not run from a PR. |
| Backups: local 03:00 stays; offsite restic → S3-compatible, 30 daily + 12 weekly, includes `secret.key` w/ backup password; monthly restore test w/ `integrity_check`; failures warn; unconfigured → "not set up" no notification; "Back up now" + "Download latest backup" (download asks confirm, DB only, event + email); upload starts after local backup; endpoint change needs credentials again (1275-1284) | built | `ops/service.py`, `db/backup.py`, `deploy/restore.sh`; `ops.offsite_backup` config | [050] "not set up" state. |
| Monitoring: heartbeat ping after every cycle/check to `ops.heartbeat_url`; outside uptime check on `/healthz`; Settings → Server shows version/address/cert/running-since/clock/disk/updates/backups/heartbeat/instance id; "Heartbeat not set" while empty (1286-1298) | built | `ops/service.py` `heartbeat`, `run_server_checks`; `ServerSection.tsx` | |
| Readiness manual item conditions (10 sessions on VPS, restore test, heartbeat check) (1300-1303) | built | `readiness_manual` item text | |
| Phone layout works; PWA later (1305) | built | responsive layout | |

### Error pages and API errors

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| One JSON error shape `{"error":{code,message,error_id}}`; `error_id` on 500 matching the log; never tracebacks/SQL/paths/secrets; 422 adds `fields` (1311-1314) | built | `api` error handlers; `errors.ts` | |
| Status→frontend-behaviour table (400 card, 401 sign-in + return, 403 confirm-and-retry-once, 403 forbidden never retried, bad_host/origin_refused message only, 413/415 card, 404 record page, 409 reload offer, 422 field messages, 429 retry-after, 500 toast w/ id + Open in Logs, 503 banner retry 15 s) (1315-1331) | built | `frontend/src/errors.ts`, `ApiErrorToasts.tsx`, `errorPages.tsx` | Route-table error test exists. |
| In-app: not-found page; not-found record w/ name + reason + links; per-screen error boundary w/ error_id + actions; first-load "isn't answering" w/ stops-at-Alpaca line + 15 s retry; offline banner pausing requests (1333-1347) | built | `errorPages.tsx`, `ErrorBoundary.tsx`, router catch-all | |
| Caddy static pages: plain HTML inline CSS/SVG, system fonts, no app data/version/JS-except-reload, "stops at Alpaca" line; 502/504 "isn't responding" 30 s reload + heartbeat note; 503 maintenance flag w/ `Retry-After: 15` + "Updating"; flag covers restore.sh; API during outage gets JSON `app_unavailable`/`updating` (1349-1360) | built | `deploy/errors/502.html`, `503.html`; `deploy.sh` maintenance flag; Caddyfile wiring | |
| Uncovered cases named (1362); design texts final (1364) | built | `Errors.dc.html` wording copied | |

### Security hardening

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Request gate on every `/api` + WS, before sign-in: Host allowlist → 400 `bad_host`; Origin/Sec-Fetch-Site on writes → 403; JSON-only writes → 415 (CSV import only multipart); bodies ≤1 MB / uploads ≤5 MB → 413 checked while reading; WS same checks (1370-1379) | built | `api/gate.py:110-303` | Scan verified end-to-end. |
| App security headers: CSP, X-Content-Type-Options, X-Frame-Options DENY, Referrer same-origin, COOP same-origin, `Cache-Control: no-store` on `/api`; bundled IBM Plex (1380-1384) | built | `gate.py:305` `_security_headers`; fonts bundled | |
| Sign-in fails closed (1386-1389) | built | `auth/guard.py` | |
| One address source `python -m app.serve`; no `--host`/`--port` anywhere; test checks; bound address logged (1391) | built | `app/serve.py`; repo test | |
| Scrubber covers exceptions + tracebacks in every handler; masks `token=`/`apikey=`/`api_key=`/`key=`/`secret=` in URLs + Authorization; tested w/ fake key in exception args + URL (1393-1396) | built | `logging_config.py` scrub filter + URL regexes | |
| Prompt names whitelisted `^[a-z0-9_]+\.v[0-9]+$`, contained in `backend/prompts/`, manifest SHA-256 checked (`load_prompt` refuses mismatch; test fails on changed prompt); same for run/replay/backtest folder names (1398-1401) | built | `decide/prompt.py`, `manifest.json` | Manifest hashes verified — all 12 match. |
| Bounded inputs: limit 1–200, log window 1–100, non-number → 422; exports streamed ≤`export_max_rows` 100k + cap line; FTS5 quoted phrase; typed ids → 422; bad-param sweep test (1403-1408) | built | routes clamps; `[049]` fixed schema-less index 500s | |
| Frontend error reports: per-address limit, ≤1000 senders, 2 KB msg / 8 KB stack, control chars stripped, `source=frontend` (1410-1412) | built | `api/routes.py` client-logs | |
| Stored credentials only where saved: SMTP host/port/user change → `password_required` 422, port ∈ {465,587,2525}, failure categories not server text, 10 s timeout; heartbeat + backup endpoint https + non-private; Alpaca different-account → new run (1414-1417) | built | `settings_service.py`, `urls.py` SSRF checks | Scan noted `urls.py:56` multicast-IP edge (224.x passes) — still open? Recheck in B. |
| More "Confirm it is you": risk-loosening saves, SMTP/heartbeat changes; tightening never asks (1419-1423) | built | `require_confirmed` + settings diff logic | |
| `umask 077`; data dir 0700; db/backups/runs/logs/index 0600 corrected at startup w/ one INFO; read-only run DBs `mode=ro`; `run.lock` `O_CREAT|O_EXCL` (1425-1429) | built | `filemodes.py`, `restart.py` | |
| Ruff `S` rules w/ commented ignores; `pip-audit` + `pnpm audit` in CI + weekly, allowlist w/ reason+expiry; gitleaks CI + pre-commit; frozen lockfile; pinned Action SHAs, `contents: read`; `make security`; `docs/SECURITY.md` (1431-1438) | built | `pyproject.toml`, `.github/workflows/`, `Makefile` `security`, `docs/SECURITY.md` | |
| New error codes `bad_host`, `origin_refused`, `too_large`, `unsupported_media_type`, `password_required` (1440) | built | error table + handlers | |
| No trading/schema change (1442) | built | — | |

### Model providers

| Spec (quoted, line) | Status | Code | Note |
| --- | --- | --- | --- |
| Same prompts, snapshot, schema, checks for both providers; model only picks (1446) | built | `llm/providers/`, one `ModelClient` interface | |
| Pickers everywhere show "id · provider"; missing-key models disabled w/ reason (1450-1454) | built | Settings pickers, shadow dialog, backtest, replay | |
| `models` list is the only model source: provider, input/output prices, cutoff, `effort` (Claude default low), `timeout_seconds`, `supports_temperature` (OpenAI default true); `model_prices`/`backtest.model_cutoffs`/`reviews.model` read one release w/ warning (1456-1464) | built | `config.example.yaml:257-263`; `llm/models.py` | |
| One interface; SDKs only in adapters; no provider branching in engine (1466) | built | `llm/providers/openai.py`, `anthropic.py` | Hard rule 21. |
| OpenAI adapter: strict structured outputs + temperatures per task (1468) | built | adapter | |
| Anthropic: Messages API; `output_config.format` json_schema; unsupported keywords stripped from sent copy only, Pydantic enforces all; no temperature/top_p/top_k, `effort` sent; Settings shows "OpenAI models only"; thinking billed + never stored; `refusal` → `refusal`, `max_tokens` → `invalid_output` `truncated`; `max_tokens` = `decision.max_output_tokens` 2048; 529/5xx → `api_error` w/ status + type; one try no retry (1470-1476) | built | `llm/providers/anthropic.py`; `max_output_tokens=2048` | "no unsupported keyword sent" test exists. |
| Determinism: cache key uses provider+model+effort-or-temp+prompt hash+snapshot hash; Claude backtests/replays labelled "not repeatable: the model has no temperature 0" (1478-1480) | built | `decide/llm_cache.py` key fields | |
| Two model keys each optional, ≥1 required; write-only, list-models test, `sk-`/`sk-ant-` scrub patterns, one-time `.env` imports; Resume needs used providers passing; save of unkeyed provider refused; Remove behind Confirm, refused while in use (shadows pause w/ reason); missing-key shadow skipped w/ warning; unkeyed-provider backtest refused; failing key mid-session → `api_error` + `source_failing` notification (1482-1489) | built | `secrets_service.py`, key-status checks, resume gate | |
| Costs: same caps, priced from `models`; no prompt caching (decisions) (1491) | built | cost computation | |
| Backtest cutoff check from `models.cutoff`; Claude 5.5 cutoff June 2026 → earliest 1 July 2026 (1493) | built | `backtests/estimate.py` `_model_cutoff`; config cutoffs | |
| `llm_calls` `provider` + `provider_request_id`, migration copies old request-id column (1495) | built | migration `a1b2c3d4e5f7` | |
| GUI: API keys rows optional + "at least one required" + per-key uses + Test/Replace/Remove; pickers w/ disabled models + "OpenAI models only"; inspector provider + request id in error block; `model.anthropic` log component (1497-1503) | built | Settings, Inspector | |
| Not changed: prompts, schema, builder, gates, Readiness; default live `gpt-4o-mini` (1505) | built | — | But see G3: spec line 1809 names `gpt-4.1-mini` live. |

### Build plan ("Done when" lines)

Covered under section E (tests against spec).

### Out of scope and later versions / Sources

Nothing checkable in code.

## B. Config against spec

Rule 3: every number in the spec has a key in `backend/config.example.yaml`; code reads config, never repeats the number.

**Spec numbers with no config key (gaps).**

| Spec number | Where it lives today | Gap |
| --- | --- | --- |
| `reviews.skip_flat_pct` (verdict draft label for decision.v1 skips, spec 620-622) | `reviews/facts.py:305` reads `reviews.skip_flat_pct` default `2` — the key is absent from `config.example.yaml` | G4 — spec'd threshold invisible in the example config. |
| Notification default channels for 11 events (spec table 463-491) | `notifications.events` covers 20 of 28 `EVENT_DEFS`; `service._channels` gives the rest app-only (+browser when critical) | G1 — defaults for `readiness_changed`, `another_instance`, `new_sign_in`, `sign_in_lockout`, `restarted_resumed`, `restarted_paused`, `backup_failed`, `backup_downloaded`, `disk_space`, `cert_expiring`, `clock_offset` don't match the spec table (email missing where spec says on; browser missing for non-criticals). |
| `inspector.default_sources` | `config.example.yaml:310` `[live, review]` | G2 — spec (784-789, amended 7 Nov) defaults to Live decisions + Reviews + **Ask**. |
| "10-call cap" for the day's model-call tile (max 60) | `decision.max_calls_per_cycle=10` × 6 cycles | consistent — the tile's "60 by default" derives, no separate key needed. |
| Adopted-stop fallback "3% below current price if ATR unavailable" (174) | `risk.protective_stop_fallback_pct=3` | built. |
| Missed-screen/start behaviours, quiet hours 23:00–08:00, repeat 30 min, kill-after-3-failures, 60 s shutdown grace, heartbeat 30 s, 5/15-min and 20/h auth limits, disk 80/90, cert 14 d, clock 1 s, restore day | all present under `schedule`, `notifications`, `risk`, `storage`, `logging`, `auth`, `ops` | built. |
| Position-check cadence and cycle times | `schedule.position_checks`, `schedule.cycles` | built (spec-fixed values shown locked in Settings). |

**Keys in `config.example.yaml` the code never reads (candidates for removal).**

Spot-checked the unusual ones: `universe.watchlist_rows` (used by the watchlist), `execution.time_in_force` (used at order build), `bars_1h_state` table backs the 1h cache, `ops.deploy_guard_market_hours` (deploy.sh), `backtest.process_nice`, `inspector.default_sources` (read), `reviews.max_words` (read in compose validation), `gui.near_stop_atr` (Positions/dashboard). **No dead key found in this pass** — a full mechanical sweep (every `config.get` path vs every key) is worth a scripted check in Phase 2 rather than manual sampling.

**Code constants that repeat a spec number.**

- `position_check.py` records the 3-failed-checks banner at level `critical` while the spec's notification row is Warning — severity mismatch (A-table: Position check). Event→notification mapping itself is right (`position_checks_failing`, warning).
- `decide/service.py`/`live_calls.py`: the 401/403 auth-error rule and "every live call failed" episode are hard-coded semantics matching the spec text; no numbers repeated.
- `engine/executor.py` `spy_session_moves` uses config thresholds — clean.
- Default arguments in code (e.g. `default=60` for `quote_max_age_seconds`, `default=30` busy timeout) duplicate the example values as fallbacks — acceptable layering, but the fallbacks can silently differ from the example (they don't today). Worth one scripted consistency check in Phase 2.

## C. Prompts against spec

| Check | Result |
| --- | --- |
| Manifest hash matches every file | **pass** — all 12 files (`decision.v1–v5`, `ask.v1`, `briefing.v1–v2`, `weekly.v1–v2`, `verdict.v1–v2`) match `manifest.json` SHA-256. |
| Prompt files present for every known version | pass — plus contracts in `decide/prompt.py`; unknown version refused (`unknown_prompt_version`). |
| `decision.v1.md` carries: strict JSON output, allowed actions per context, stop 1.5–4×ATR, RR ≥1.5, confidence semantics, "unavailable data is unknown, never invent" (spec 275-300) | built — present in the prompt text. |
| `decision.v2.md`: options-only output, `option_id`, no price fields, confidence = P(target before stop), explanation must cite only input (1002-1005) | built — schema fields match spec list and order; Pydantic v2 model in `output.py` matches. |
| `decision.v3.md`: conflicts→thesis→action order, trend rules, high-vol rule, break-even anchoring, `news_used` only effective headlines, `data_flags` enum, field guide incl. `checks`/`plausible`/`spread_unknown`/`rank`, momentum semantics, no stop/target basis, app name (1542-1553) | built — verified against the file; schema order `symbol, conflicts, data_flags, thesis, action, option_id, confidence, levels_used, news_used` matches (1555). |
| `decision.v4.md`: confidence per action incl. `best_option_id`, `outweighed_by`, no path claims within 1 ATR, `what_changed`, news rules (`about_symbol`/`new`/effects), exit on `thesis_holds` false, tighten-to-protect-progress (1715-1733) | built — schema order matches (1735-1746). |
| `decision.v5.md`: far-target honesty + `target_atr`, <120 min + >2 ATR target = overnight conflict, "a skip is not free", per-option `min_confidence`, hold/exit lines (neutral−0.03, overnight neutral+0.10 w/ stop inside gap), held data flag → hold, previous_decision as data, `about_symbol` false caveat (1869-1882) | built — verified against the file. |
| `weekly.v2.md`: sample + evidence, suggestion rules, `evidence` line (628) | built. |
| `briefing.v2.md`: status facts written by code, model may not call known-empty "unavailable" (605) | built — note: config default `reviews.prompts.briefing` is `briefing.v1` while the spec's versioning narrative names v2 as current; both prompts exist and are contractual. Question below (Q5). |
| `verdict.v2.md`: reason + thesis check fields (624) | built. |
| `ask.v1.md`: read-only, untrusted-data framing, reference syntax, number discipline (2040-2045) | built. |
| Anthropic copy strips unsupported keywords, Pydantic enforces full schema (1472) | built — `llm/providers/anthropic.py`; tested. |
## E. Tests against spec

The suite is large (~110 backend test files + frontend vitest + playwright). Rather than re-running, this maps the spec's *required* coverage to test files. `make test` was not run in this audit (read-only); CI runs it.

**Required tests (spec 546).**

| Required | Covered by | Note |
| --- | --- | --- |
| Every indicator vs hand-checked values | `test_indicators.py`, `test_regime.py` | |
| Every risk gate pass and fail | `test_gates.py`, `test_risk_math.py` | |
| Bar aggregation into session-aligned 1h bars | `test_aggregate.py` | |
| Same-session re-entry rule | `test_rules.py`, `test_gates.py` (reentry) | |
| Reconciliation mismatches | `test_executor.py`, `test_scan_correctness.py` | |
| Position-without-stop recovery | `test_stop_check.py`, `test_executor.py` | |
| DST weeks (25 Oct–1 Nov 2026) | `test_clock.py`, `test_session_calendar.py`, `test_scheduler.py` | |
| Early-close day | `test_clock.py`, `test_session_calendar.py` | |
| Invalid model output | `test_decide_output.py`, `test_decide_v2–v5.py` | |
| Default symbols produce a valid trade at typical ATR (547) | `test_risk_math.py` | |

**Build-plan "Done when" lines (selected, later items).**

| Item | Done-when | Test/e2e |
| --- | --- | --- |
| 016 Readiness | fixture run → expected per-criterion status incl. waiting-for-data | `test_readiness.py`, `test_readiness_logs.py` |
| 018 LLM history | backfill outcomes; timeout/503 show as errors; 300 ms on 100k fixture | `test_llm_calls_backfill.py`, `test_inspector_api.py`, `test_inspector_outcome.py` + perf test |
| 020 Logging | lint fails on swallowed exception; integrity after Ctrl+C; Logs screen opens on warnings | `test_no_silent_failures.py`, `test_shutdown.py`, `test_log_index.py`, `logs.e2e.ts` |
| 021 v2 | builder fixtures, invalid option → skip, replay report | `test_decide_levels.py`, `test_decide_options.py`, `test_decide_v2.py`, `test_replay.py` |
| 026 PDT retired | exit on entry day; re-entry refused live+shadows; field references fail | `test_pdt_fields_gone.py`, `test_rules.py`, `test_shadows.py` |
| 027 Protective stops | breached→market sale; orphan→OCO; oversized sell capped; flat→nothing; missed screen at start | `test_executor.py`, `test_stop_check.py`, `test_universe.py` |
| 028 Security | cross-site POST refused, rebinding Host refused, foreign WS refused, fake key never logged, `../` prompt → 422, no GET 500 on bad params, SMTP password not sent, files 0600 | `tests/security/*`, `test_file_modes.py`, `test_prompts.py` |
| 029 Providers | Anthropic-only and OpenAI-only runs; resume blocked w/o key; unsupported keyword never sent; refusal/truncated → skip; missing provider key skips shadow; cutoff refusal | `test_pr29_providers.py`, `test_pr29_keys.py`, `test_llm_boundary.py` |
| 030 v3 | SBUX held/candidate fixtures; edge_too_small; data_flag; v2 byte-for-byte; v3 shadow | `test_decide_v3.py`, `test_v3_shot_seed.py` |
| 031 Exit fills | stream fill closes w/o event; closed_while_offline; true mismatch → CRITICAL + blocked; naive datetime lint | `test_executor.py`, `test_stream_logging.py`, ruff DTZ |
| 032 Attribution | verdict on blocked enter; skip w/ target-first option → bad call; thesis contradicts; <10 trades → no suggestion; status line once; decision.v4 refused | `test_pr32_attribution.py` |
| 033 Selection | rank byte-identical; XOM/MSFT/AMZN example; sector-cap best set; failed fresh quote → next best; expected R ≤0 never chosen; supervised queues chosen; shadow EP; replay diffs | `test_selection.py`, `test_replay_selection.py` |
| 034 v4 | flip-flop; confidence_outlier; claim-check fix; best_option_id; thesis_holds; eval; v4 shadow | `test_decide_v4.py`, `test_eval_decision.py` |
| 035 Needs you | each item kind appears/leaves; empty state; amber <1.5 ATR; missing leg opens; 390 px width | `test_attention.py`, `decision-drawer.e2e.ts`, `045-screens.e2e.ts` |
| 036 Ask | blocked-entry question + chip; stop-total answer; follow-up; Stop; planted instruction ignored; read-only refusal; no secrets in tool results; cap notice; eval ≥90% | `test_ask.py`, `test_eval_decision.py`/eval-ask, `ask-drawer.e2e.ts` |
| 037 v5 | KO eval case; ev thresholds; sideways → no call; v4 replay unchanged | `test_decide_v5.py`, `test_eval_decision.py` |
| 050 Stop invariant | protected/exit-in-flight/pending-exit rules; episode lifecycle; skip reasons WARNING | `test_stop_check.py`, `test_state_errors.py` |

**Tests that share a database / would pass if behaviour were removed** — not systematically checked in this pass (needs per-file fixture analysis); flagged for Phase 2 step 6.

## D. Design against the app

Method: seeded simulated app (`tests/e2e_seed.py` + `tests/e2e_backtest_seed.py` + `tests/readme_seed.py`, `MODE=simulated`, frozen 2026-10-15) screenshotted at 1440 and 390 (and 1024 for Dashboard, Positions, Settings) via Playwright — 19 + 10 PNGs, compared against `design/screenshots/`. Raw PNGs: `/tmp/046-shots/` (not committed; rerun: `SCREENSHOT_DIR=… pnpm exec playwright test e2e/045-screens.e2e.ts e2e/046-screens.e2e.ts`).

Per-screen findings below; the interaction-rules checklist (focus, hover, 44 px, live regions, tabs, dialogs, page title) was verified in code (`tokens.css` shared classes, `documentTitle.ts`, `cardState.tsx`) rather than visually per screen.

**Captured routes** (29 PNGs): Dashboard, Positions, Reviews, Insights, Inspector, Settings, Universe, decision drawer at 1440 + 390; Dashboard/Positions/Settings at 1024; Performance, Readiness, Logs, Strategies, Backtest at 1440 + 390. Not captured this pass: Login (needs `SA_AUTH=required` seed), static error pages (`deploy/errors/` — files compared in A), the Ask drawer, `States.dc.html` banners (component code checked).

| Screen | Blocks missing | Order differences | Wording differences | States unreachable/unchecked | Sideways at 390 |
| --- | --- | --- | --- | --- | --- |
| Dashboard (`main-*`) | None found at layout level: Needs you first, cycle strip, Open positions + Risk budget under it, Chart + Latest decision, Today's decisions + Daily briefing, Watchlist last — matches spec 405 and `Main.dc.html` order | none seen | none seen | several cards rendered in loading state at capture (seed timing); empty/error states not exercised this pass | none (e2e asserts 390 document width) |
| Positions (`positions-*`) | Totals line, per-position bar + legs folding not exercised — seed has no open positions | none seen | none seen; "There is no day-trade limit since 4 June 2026…" matches spec wording; account card shows EUR beside equity | pending-exit amber legs state, "Alpaca not answering · as of" state: component code exists (`Positions.tsx`), not visually exercised | none |
| Reviews (`reviews-*`) | Confirm flow present (045) | — | — | no draft verdicts in seed → confirm buttons not exercised | none |
| Insights (`insights-*`) | "What each filter removed" card present (045/037) | — | — | thin seed → mostly small-sample/empty states (expected) | none |
| Inspector (`inspector-*`) | filters incl. Ask/Backtests sources, prompt + reason lists from manifest (045) | — | — | error-call detail not exercised visually | none |
| Settings (`settings-*`) | all spec'd sections incl. Entry selection, Decision gates, Display, Prices and timeouts, notifications folded, Access, Server, Paper account (045) | — | — | — | none |
| Universe (`universe-*`) | mode banner, counts, funnel, pool table, company names | — | — | — | none |
| Performance / Readiness / Logs / Strategies / Backtest (`046-*` run) | all blocks from their design screens present at layout level | — | — | tails small-sample banner, readiness states, logs empty state render from seed | none |
| Decision drawer (`decision-*`) | one component tree, nine-section order (action · tiles · What happened · version blocks · reasoning · gates · levels · outcome · footer) | — | — | seed's decision is one version; v1–v5 variants render per the shared component (e2e `decision-drawer.e2e.ts` covers versions) | drawer fits viewport |

**D summary.** No missing blocks found at the level this pass could check — consistent with [045] having just caught the GUI up. Remaining visual debt: states the seed can't reach (pending-exit legs, broker-stale card, no-stop red chip, degraded stream banner) exist in code but weren't exercised; a state-matrix e2e (per banner/Needs-you kind) is the Phase-2-level check. Several dashboard cards rendered skeletons at capture time — a capture-timing artefact, not a layout bug, but worth re-shooting with a longer settle if the report is re-run.

## Scripted checks (7 Nov, Phase 2 prep)

Marc's answers below order Phase 2 to open with three mechanical checks. Results:

**1. Every `config.get*` literal path vs every `config.example.yaml` leaf** (AST scan of `backend/app`; `get_config().get` receivers included; dict `.get` calls on config sub-trees resolved by hand):

- Read but absent from the example: `alpaca.max_calls_per_minute` (the spec's <150 calls/min limiter, `data/factory.py:73`, fallback 150), `backtest.cancel_kill_wait_seconds` (5, `backtests/manager.py:177`), `ops.backup_stale_hours` (36, `ops/service.py:293`), `ops.restore_test_stale_days` (45, `ops/service.py:319`), `performance.tail.after_exit_max_failures` (3, `engine/after_exit.py:116`), `performance.tail.after_exit_bar_delay_minutes` (16, `after_exit.py:124`) → **G13, G15**.
- `rules.entry_cycles` (`rules/prefilter.py:174`) is absent on purpose — it's a strategy-overlay key; absent means every cycle. Not a gap.
- `reviews.model`, `model_prices`, `backtest.model_cutoffs` (`llm/models.py`) are the documented one-release compat reads the example's comment names. Not gaps.
- `universe.screen_time` (`backtests/worker.py:533`) is read but does not exist — the real key is `universe.screen.time_ny` (same value today, so the fallback silently wins) → **G14**.
- Fallback-vs-example diffs: ~45 sites, nearly all dead sentinels (`""`, `None`, `[]`) that can't fire since [050] — the example always loads. No behavioural diff found. `logging.index` is both a switch and a path (`log_index.py:299` reads it as a bool) — cosmetic overload, noted only.
- Example leaves no code reads (dead keys): `inspector.default_sources` and `inspector.page_size` (the frontend hard-codes both — `inspectorFilters.ts:31`, `Inspector.tsx:55`), `execution.time_in_force` (`TimeInForce.GTC` hard-coded in `broker/alpaca.py:429,457,483`), `logging.page_size`, `backtest.same_bar_stop_and_target` → **G16**. `decision.v5.*` and `universe.screen.*` looked dead in the raw scan but are read through a helper and a sub-dict — not dead. `ops.deploy_guard_market_hours` is read by `deploy/deploy.sh`, not app code — fine.

**2. Tests that share a database** — none found. `_isolated_db` (autouse) gives every test its own copy of the read-only warm template under `tmp_path`; `session_factory` and `empty_db_template` are function-scoped; every `create_engine` in test files binds a per-test file; no module/session-scoped db fixtures exist outside `conftest.py`; no `:memory:` pools; the only subprocess tests are import checks with no database. The shared template itself is copied, never written. Nothing to split (E-section caveat closed).

**3. Every banner and Needs you kind, end to end** — `frontend/e2e/046-states.e2e.ts` mocks `/api/banners` and `/api/attention` and renders each of the 26 banner kinds from `banners()` (incl. the raw-`event_type` fallback and both kill-switch variants) and all 9 Needs you kinds from `attention.py`, asserting text and action buttons. One finding: the kill-switch banner advertises a `flatten` action no button is rendered for → **G18**.

**"Resume says why" (spec 203)** — not built for a manual pause. The paused banner shows only when `paused_at_startup` (`api/routes.py:2268`); an unclean-restart pause has `restart_paused`. If Marc pressed Pause himself, nothing states why or when — the header pill says "paused" and Resume has no context → **G17**.

## Summary of gaps

Ranked: **blocking** = money, safety, data loss, or a hard rule; **should fix** = spec'd behaviour missing or wrong; **nice to have** = wording/layout.

| Id | Rank | Gap | Files | Proposed fix | Status |
| --- | --- | --- | --- | --- | --- |
| G1 | should fix | 11 notification events missing from `notifications.events` in `config.example.yaml`; their channels default to app-only (+browser when critical) vs the spec table (email/browser on for `another_instance`, `new_sign_in`, `sign_in_lockout`, `restarted_paused`, `backup_failed`, `backup_downloaded`, `disk_space`, `cert_expiring`, `clock_offset`, `readiness_changed`, `restarted_resumed`) | `backend/config.example.yaml`, `backend/app/notifications/service.py:212-221` | add the 11 events with the spec's defaults (and decide whether the Settings table then shows every event) | fixed · #151 |
| G2 | should fix | `inspector.default_sources: [live, review]` omits `ask`, which the spec (amended 7 Nov) defaults on | `backend/config.example.yaml:310` | add `ask` (and confirm `backtests` stays opt-in) | fixed · #151 |
| G3 | spec-only question | Default live model: spec line 7 says `gpt-4o-mini`, line 1809 says "live model stays gpt-4.1-mini"; config default is `gpt-4o-mini` | `docs/SPEC.md`, `config.example.yaml:104` | Marc picks; spec text then corrected | config side fixed · #151; spec text in Marc's PR |
| G4 | should fix | `reviews.skip_flat_pct` (verdict draft label, spec 620-622) read with code default 2 but absent from `config.example.yaml` | `backend/app/reviews/facts.py:305` | add key with the spec'd default (confirm the value with Marc — spec names the key, not its default) | fixed · #151 |
| G5 | should fix | Position-check failure banner recorded at `critical` level while the spec's notification row is Warning ("Position checks failing", browser+email) | `backend/app/engine/position_check.py:120-132` | record at warning level, or rule that the banner is intentionally critical — spec question | fixed · #152 |
| G6 | spec-only question | `no_call` reason list: spec 298 names `rules, call_cap, quote_missing, model_error, no_options`; code + later spec sections use `price_stale`, `price_suspect`, `reference_missing`, `checks`; `quote_missing` exists nowhere | `backend/app/decide/output.py:32-35`, `docs/SPEC.md:298` | spec text update (Claude), code is right | fixed · #168 |
| G7 | spec-only question | Gate order: code checks position/sector/daily-loss/shock/earnings/re-entry/drawdown **before** the sizing gates; spec table order implies sizing first — recorded `blocked_by` can differ when several would fail | `backend/app/risk/gates.py:112-348` | either spec records the actual order, or code reorders — Marc decides | docstrings fixed · #152; spec table in Marc's PR |
| G8 | nice to have | `stop_missing` repeat notifications don't carry the elapsed time ("still no stop · 1 h 30 min") | `backend/app/notifications/events.py:336-349` | add elapsed to the repeat line | **built — no change needed.** Report mis-cited the `protective_stop_failed` formatter; the episode repeat emits "still no stop · {duration}" (`executor.py:3329-3334`, `_hm` gives "1 h 30 min"), pinned by `test_stop_check.py:404-468` |
| G9 | spec-only question | `reviews.prompts.briefing` defaults to `briefing.v1` though `briefing.v2` exists and is the "current" contract | `config.example.yaml:206` | confirm intended default | fixed · #151 |
| G10 | nice to have | Spec's data-model table omits `smtp_settings`, `bars_1h`, `bars_1h_state`, `daily_source_gaps`, `approvals`, `runs`, `reviews`, `verdicts` — all confirmed still used (bars_1h_state backs the 1h cache fingerprint, `data/cache.py`) | `docs/SPEC.md:505-535` | spec text update listing the added tables | fixed · #168 |
| G11 | should fix (verify) | Backtest child-PID kill may hit a recycled PID (scan finding 3); `_pid_same` start-time check exists — confirm it covers shutdown/cancel paths | `backend/app/backtests/manager.py:160-197,378-408` | verify + test PID-reuse case | fixed · #152 |
| G12 | nice to have | Several spec'd states unreachable in the standard seed (pending-exit legs, broker-stale cards, no-stop chip, degraded-stream banner) — code exists, not exercised end-to-end | `tests/e2e_seed.py`, e2e specs | extend e2e seed or add a state-matrix spec in Phase 2 step 6 | step 6 (Marc, 7 Nov); the mock-driven half landed in `e2e/046-states.e2e.ts`; card-level states covered by `e2e/046-position-states.e2e.ts` · #167 |
| G13 | should fix | The shared limiter's `alpaca.max_calls_per_minute` (the spec's <150/min) is code-only — read with fallback 150, absent from the example | `backend/app/data/factory.py:73`, `config.example.yaml` | add `alpaca.max_calls_per_minute: 150` (scripted check 1) |
| G14 | should fix | Backtest worker reads `universe.screen_time` — no such key; the live screen's key is `universe.screen.time_ny` (same value today, so the wrong read is invisible) | `backend/app/backtests/worker.py:533`, `config.example.yaml:36` | read `universe.screen.time_ny` (scripted check 1) |
| G15 | should fix | Five spec-adjacent thresholds live only as code fallbacks: `backtest.cancel_kill_wait_seconds` 5, `ops.backup_stale_hours` 36, `ops.restore_test_stale_days` 45, `performance.tail.after_exit_max_failures` 3, `performance.tail.after_exit_bar_delay_minutes` 16 | `backtests/manager.py:177`, `ops/service.py:293,319`, `engine/after_exit.py:116,124` | add the five keys with the code defaults (scripted check 1) |
| G16 | should fix (decide) | Dead example keys — the app never reads them: `inspector.default_sources` + `inspector.page_size` (frontend hard-codes both), `execution.time_in_force` (GTC hard-coded in the Alpaca adapter), `logging.page_size`, `backtest.same_bar_stop_and_target` | `config.example.yaml`, `inspectorFilters.ts:31`, `Inspector.tsx:55`, `broker/alpaca.py:429` | wire each key or drop it — Marc picks; G2 already corrects the inspector value (scripted check 1) | `execution.time_in_force` + `logging.page_size` wired · #165; `inspector.*` keys kept as documented defaults (Marc); `backtest.same_bar_stop_and_target` wired — `target` now ranks target legs first on a same-bar touch (broker.py), with a pair of tests · #165 |
| G17 | should fix | Spec 203 "Resume says why if Marc paused" — a manual pause shows no reason or time anywhere; the paused banner is `paused_at_startup`-only | `api/routes.py:2268-2325`, `Header.tsx` | show the paused banner for manual pauses too ("Paused at 14:02 — you pressed Pause") (scripted check: Resume wording) | fixed · #166 |
| G18 | nice to have | The kill-switch banner's `flatten` action renders no button — `BannerStrip` maps every other action; flatten is only reachable via the header menu | `api/routes.py:2370`, `BannerStrip.tsx` | render the action or stop advertising it (scripted check 3) | fixed · #165 |

## Questions for Marc — answered 7 Nov

1. **Live model default (G3):** spec says `gpt-4o-mini` (line 7) and `gpt-4.1-mini` (line 1809). Which is authoritative — config currently defaults to `gpt-4o-mini`. → **`gpt-4.1-mini` for `decision.model` and the briefing + verdict review models; Marc updates the spec text in a spec PR.**
2. **`reviews.skip_flat_pct` (G4):** spec names the key for the v1-skip verdict rule but never gives its default; code uses 2.0. Confirm `2.0` belongs in `config.example.yaml`. → **yes, add `reviews.skip_flat_pct: 2`.**
3. **Gate order (G7):** keep code's order (position/sector/session gates before sizing) and record that in the spec, or reorder to the spec table? The recorded `blocked_by` name differs when multiple gates would fail. → **keep the code's order; the spec table is rewritten to match (spec PR). Code fix: the "spec order" docstrings in `gates.py`.**
4. **Position-check banner severity (G5):** the spec calls the notification Warning; the event is recorded critical so the in-app banner renders critical. Intentional? → **record the event at `error`, not `critical`; the notification stays Warning. Add a test.**
5. **`briefing.v1` vs `v2` default (G9):** `reviews.prompts.briefing` ships `briefing.v1`; is that deliberate while `briefing.v2` is the newer contract? → **no — `briefing.v2`; the v1 default was an accidental revert in b71c3d2.**
6. **Audit scope (kickoff open question 1):** this pass covered every spec section at claim granularity, with deepest verification on the money/safety paths (prior scans read them end-to-end). GUI state coverage and per-test database isolation need follow-up tooling (noted in D and E) — is that depth sufficient for Phase 2 planning? → **yes; the follow-ups ran as the scripted checks above — results folded into G13–G18.**
7. **Won't-fix handling (kickoff open question 2):** should report "won't fix" rows become spec text in this item's Phase 2, or a separate spec PR by Claude? → **spec side (G6, G10, and the spec half of G3/G7) goes in Marc's spec PR; won't-fix lines travel with it.**

Phase 2 order per the same answers: G1, G2, G4, G5, G9, G11 as step 2–3 PRs (G8 verified already-built, no change); G12 in step 6; G13–G18 await ordering.
