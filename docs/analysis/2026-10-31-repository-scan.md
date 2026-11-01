# Repository scan: bugs, gaps and security findings

Read-only analysis by Devin, 31 October 2026 (fixed in #72 and #74). Kept as reported; findings may since have been fixed (see docs/CHANGES.md).

Confirmed bugs
1. Two settings keys are unsaveable — regression from PR 29
settings_service.py:367-391

Commit 5ab087a replaced elif kind == "quiet_hours": with elif kind == "review_models": but left out[key] = _clean_quiet_hours(value) at the end of the new branch:

reviews.models (kind review_models): the models dict is cleaned correctly, then overwritten by _clean_quiet_hours(value), which demands start/end HH:MM keys — the save always fails with the misleading error "Quiet hours start: '' is not an HH:MM time".
notifications.quiet_hours (kind quiet_hours): no branch matches anymore → falls to the else → _clean_number → float(dict) → always "Quiet hours: not a number".
The frontend submits both keys (Settings.tsx:1484 patches notifications.quiet_hours; :1440 submits reviews.models), so both are broken end-to-end. No test covers saving either key.

2. Auth endpoints return 500 on malformed JSON bodies
backend/app/auth/api.py:141,165,227,290

body = await request.json() then body.get(...). A syntactically valid non-object body ([1], "x", 5) raises AttributeError; malformed JSON raises JSONDecodeError. Both surface as 500 instead of the required 422 (hard rule 19: "bad input gets 422, never 500"). Everywhere else uses body: dict[str, object], which FastAPI validates — these four endpoints are the exception.

3. Backtest kills may hit a recycled PID
backend/app/backtests/manager.py:167-184, 318-321

_stop_after_grace and shutdown kill row.process_id — a PID stored in the DB — with SIGTERM/SIGKILL. After an app restart, the recorded PID may belong to an unrelated process (PID reuse); _pid_alive can't distinguish "our child" from "someone else's process". tick() has the flip side: a recycled PID keeps a dead run looking alive forever. Mitigation would be comparing a process start-time token, or only killing when self._run_id == run_id (same-process child). Hard rule 12's spirit is "stop only a process you started" — a stale DB PID doesn't satisfy that.

4. confirm/change_password have no rate limiting
backend/app/auth/service.py:437-455, 535-563

AuthService.confirm verifies password/TOTP and change_password verifies the current password — neither records an attempt nor applies lockout. The endpoints require a session, so this needs a stolen cookie first, but once held, a session gives unlimited password/TOTP guessing to mint a confirmed_until window (which then unlocks key replacement, backup download, sign-out-everywhere). A _record_attempt/_failed path like the sign-in steps would close it.

5. Write lock held across a broker call in CSV import
service.py:357-385

import_csv holds one session across the loop. After the first valid row is session.add()ed, the next row's _check_symbol_add runs session.execute(...) — autoflush writes the pending INSERT → SQLite write transaction open → then await self._broker.get_asset(symbol) — a network call under a write lock (hard rule 13: commit before any broker call). Single-symbol add_symbol is unaffected (no dirty state); imports with ≥2 valid rows hit it.

6. committed_entries under-counts risk for limit-less entries
math.py:140-159

For a WorkingEntry with limit_price=None (a market entry or any order the broker didn't echo a limit for), committed = qty × 0 = 0, so both cash and the "conservative worst case" risk contribution are 0 — contradicting the docstring's "full committed value as risk" intent. Currently dormant if every entry is a limit bracket, but the fallback risk += committed silently degenerates to zero; the intended worst case presumably means qty × entry estimate.

Smaller issues
urls.py:56-63 — the literal-IP branch omits is_multicast (the resolver branch checks it): https://224.x.x.x/ passes validation.
middleware.py:164 — _origin_ok does host.split(":")[0], which mangles Host: [::1]:8000 to [ — WebSocket origin check fails on IPv6. (Gate's _split_host handles brackets correctly; middleware is the redundant second line, so impact is limited.)
middleware.py:46-51 — multiple Cookie headers are concatenated without a ; separator, producing an unparsable blob. Rare, but silently breaks auth when it happens.
secrets_service.py:82 — masked_hint returns the full value when len(value) < 4; a ≤3-char secret is displayed in full in Settings.
secrets_service.py:102 + logging_config.py:78 — secrets shorter than 8 chars are never scrubbed from logs or hint masking.
service.py:382-407 — run_restore_test's finally references target_root, which is unbound if mkdtemp itself raises → UnboundLocalError masks the real error.
main.py:204 — run_daily_backup calls create_backup() without the injected clock, so the scheduled job falls back to datetime.now(UTC) (db/backup.py:30) — inconsistent with hard rule 5; in simulated mode the backup gets a real date, not the simulated one. Same for datetime.now(ZoneInfo("UTC")) at main.py:385.
service.py — temperature defaults disagree: the call uses default=0 (line 242) while cache lookup/store use default=0.2 (lines 207, 269). If decision.temperature is ever absent from config, cache keys and call params diverge.
routes.py log endpoints — with conn: on a sqlite3.Connection doesn't close it (context manager only commits). Relies on refcount GC; the export endpoint closes explicitly. Cosmetic.
executor.py:1875 — _open_positions_from_fills scans all buy orders with fills on every reconcile (every 15-minute position check); unbounded growth over the DB's life.
gates.py:293-310 — order_sanity (max_order_pct default 25%) is effectively unreachable: max_position_pct (20%) already caps order value below it. Harmless but dead unless config widens the gap.
gates.py:128 — limited_by is alphabetical, so blocked_by = limited_by[0] may misname the binding constraint (e.g. "cash" reported when "open_risk" was equally limiting). Cosmetic.
websocket.py:57-58 — asyncio.ensure_future bypasses spawn() tracking; spawn.py documents this as an intentional exception, so it's noted rather than counted as a violation.
Things verified clean
Paper-only enforced everywhere (paper=True at every TradingClient/TradingStream construction, PA account-number prefix check, test for the live URL).
No buying_power/pattern_day_trader/daytrade_count reads anywhere (retired PDT rule respected).
Reconciliation design is strong: kill-switch flatten confirms cancels before selling, _ensure_dead confirms legs dead before market sells, sell qty capped at broker-confirmed qty, order submit→store guarded by shutdown order slots, no order ever resent (client-order-id recovery instead).
Prompt manifest + hash + whitelist + containment checks are solid; route coverage walk fails closed.
Request gate is thorough (Host allowlist incl. DNS-rebinding defense, origin/fetch-site on writes incl. WS, JSON-only bodies, counted body cap with replay).
Secret hygiene is good: Fernet-encrypted store, write-only API, scrub filter on every handler incl. exception/traceback text, credential re-request enforced on SMTP host/port/user change and backup endpoint change.
Time injection is respected in engine code (the strays are log rotation, watchdog heartbeats, and the two non-engine fallbacks noted above).
Log/query endpoints clamp limit/window, bind all params, quote FTS phrases, cap exports.
No bare except:, no except Exception: pass, no eval/exec/pickle, no f-string SQL with user input, no --host/--port anywhere.
Coverage caveats
I read all of the security-critical and money-path files end-to-end (auth, gate, middleware, secrets, executor, gates, risk math, broker client, cycle, position check, decision service, settings, ops, backtest manager, logging, websocket, key routes sections). decide/output.py (validation), decide/v3.py, decide/options.py, reviews/, insights/, shadows/, engine/outcomes.py, engine/performance.py, engine/after_exit.py, inspector/ and most of api/routes.py were grep- and sample-checked rather than read line by line — a subtle logic bug could hide there. Frontend received a security-focused pass (CSRF, storage, CSP, XSS sinks) — all clean — not a full logic review.

Top priority: finding 1 — it's a live user-facing regression where two Settings fields silently cannot be saved. Finding 2 and 3 are worth fixing next.
