# Security audit

Read-only analysis by Devin, 27 October 2026 (fixed in PR 28). Kept as reported; findings may since have been fixed (see docs/CHANGES.md).

Scope: entire repo — backend (FastAPI/SQLite), frontend (React/Vite), Makefile, CI, deploy surface, docs. Read-only; nothing changed. Note: the working tree has uncommitted, in-progress PR 23 (sign-in) work landing while I audited (backend/app/auth/, users/sessions/sign_in_attempts models, migration, make create-user). Findings distinguish committed code from WIP.

Summary verdict
The codebase is unusually disciplined for a single-user app: secrets are Fernet-encrypted write-only, every Alpaca client is paper=True, model output can never reach the broker unvalidated, tests block sockets, and dependency hygiene (uv.lock, dependabot with 7-day cooldown, current versions) is good. The one systemic hole is that today the entire API is unauthenticated and CSRF-able from any webpage — that is by design for SA_AUTH=off on loopback, but until PR 23 finishes, anything that reaches 127.0.0.1:8000 has full control, including "drive-by browser" attacks. A few smaller holes sit underneath that.

Findings
1. Critical — every state-changing endpoint is CSRF-able on loopback
CORS only allows the Vite dev origins, but CORS does not block "simple" requests — it only gates preflighted ones and response reading. All mutation endpoints take body: dict[str, object]; FastAPI parses the body as JSON regardless of Content-Type, so a text/plain form POST delivers valid JSON with no preflight. Endpoints with no body (/api/pause, /api/paper-reset, /api/keys/{group}/test, /api/notifications/test, /api/universe/screen) need no tricks at all.

While the app runs on 127.0.0.1, any website Marc visits can therefore:

POST /api/kill-switch {"action":"flatten"} — sell every position. routes.py:751-761
POST /api/paper-reset + /api/runs — wipe the run state. routes.py:2722-2736
POST /api/approvals/{id}/approve — execute orders in supervised mode.
POST /api/settings — weaken risk gates (validated, but an attacker can pick any allowed values — e.g. risk_per_trade_pct: 5, daily_loss_limit_pct: 100, disable the kill-switch thresholds). routes.py:2323-2383
POST /api/keys/alpaca — after pausing + flattening, replace the Alpaca keys with attacker-controlled paper keys; the tester validates them and the agent starts trading someone else's account. routes.py:2594-2674
POST /api/notifications/smtp — make the server open a TCP connection + STARTTLS + login to an arbitrary host:port (SSRF, see #7). routes.py:2416-2457
POST /api/strategies, /api/verdicts/{id}, /api/universe/import, /api/logs/review, /api/events/{id}/dismiss — quieter data manipulation.
Fix (already planned): the PR 23 spec's CSRF + Origin checks close this. Until they land, the practical mitigation is exactly what check_auth_guard enforces — keep the bind on loopback and treat any reachable process/page as trusted. Worth considering an interim Origin-header check middleware: it costs ~15 lines and kills the drive-by vector before PR 23 merges.

2. High — SA_AUTH=required is accepted but does nothing yet
check_auth_guard (uncommitted) refuses SA_AUTH=off off-loopback — good — but SA_AUTH=required + SA_HOST=0.0.0.0 starts today, and since no session middleware exists yet, the app is then wide open on a public interface. The guard validates intent, not capability. guard.py

Fix: until the session layer merges, refuse SA_AUTH=required too, or ship guard + sessions in the same PR.
Small bug in the WIP: auth_required() compares config.env.sa_auth == "required" raw while check_auth_guard normalizes with .strip().lower() — SA_AUTH=" Required " passes the guard but auth_required() returns False. Normalize once in EnvSettings (a validator) and compare everywhere. guard.py:27-44
3. High — secrets in exception text/tracebacks bypass the log scrubber
SecretScrubFilter scrubs record.getMessage() and record.exc_text — but record.exc_text is only populated after a formatter caches it, and JsonFormatter.format never sets it: it renders record.exc_info directly into exception.message/exception.traceback unscrubbed. logging_config.py:96-112 logging_config.py:195-205

So an exception whose args contain a secret — e.g. the Finnhub SDK embedding ?token=<key> in an error URL, or an SDK echoing a request header — lands in stock-agent.log and on the console in cleartext. (The log index is scrubbed only by accident of handler ordering.) Hard rule 9 territory.

Fix: in the filter, also render record.exc_info via traceback.format_exception and scrub it — or scrub at emit time in JsonFormatter itself. Cheap fix, high value.

4. Medium — load_prompt() path traversal
load_prompt(version) does PROMPTS_DIR / f"{version}.md" with version arriving from POST /api/settings (decision.prompt) or POST /api/strategies. Validation only requires the file exist, so ../../some/file resolves outside backend/prompts/. A surviving .md file then becomes the system prompt — its contents are sent to OpenAI every cycle and returned to the GUI via GET /api/llm-calls/{id} and decision detail. Chained with #1, that's an exfil path for any *.md on disk. prompt.py:54-58

Fix: validate version against ^[A-Za-z0-9_.-]+$ and assert path.resolve().is_relative_to(PROMPTS_DIR).

5. Medium — unbounded/negative query params on log endpoints
GET /api/logs computes limit = min(int(param), 200) — a negative value passes through, and in SQLite LIMIT -1 means unlimited. ?limit=-1 and /api/logs/{id}/around?window=-10000 each dump the whole index; /api/logs/export.jsonl has no LIMIT at all. int() on cursor/line_id throws 500 on garbage. Unauthenticated today, so a trivial memory/CPU DoS plus full log disclosure. routes.py:4352-4456

Fix: clamp to 1 ≤ limit ≤ 200, 1 ≤ window ≤ 100, 422 on non-numeric; give export a hard cap.

6. Medium — /api/logs/client is an unauthenticated log-forging/memory-leak endpoint
Rate limiting keys on tab_id — a client-supplied string, so rotating it bypasses client_errors_per_minute entirely. _client_error_times also grows forever (one dict entry per novel tab_id, never evicted). The logged message/stack is attacker-controlled text — with embedded newlines it writes convincing fake "frontend" ERROR lines into the app log and index. routes.py:4255-4286

Fix: rate-limit by client IP (the client_ip() helper exists), cap the map size, strip \r\n from fields.

7. Medium — SMTP settings = outbound connect primitive
save_smtp connects with STARTTLS to caller-supplied host:port before saving, and the 422 response echoes the SMTP error detail. Via CSRF (#1) this is a small internal-network probing oracle; also note the app will happily server.login() the previous stored password against a newly supplied host — an attacker who first flips the host can send the stored credential to their server. Read that again: password=None → falls back to secrets.get("smtp_password") → logs into host chosen by the requester. That leaks the SMTP password to a hostile server. service.py:600-666

Fix: when host/port/username differ from the saved row, require an explicit password in the request; never send the stored one to a new host.

8. Low — WebSocket has no auth/origin check
/ws/ws accepts any origin; cross-site WS connections aren't CORS-bound, so a webpage can subscribe and read update topics (low-sensitivity: topic names only). The PR 23 origin check closes it. websocket.py:47-72

9. Low — no request-size limit, no security headers
No body-size cap anywhere (e.g. /api/universe/import CSV, settings trees) — memory DoS, more relevant once Caddy fronts it. No X-Frame-Options/CSP/X-Content-Type-Options/Referrer-Policy — spec assigns headers to Caddy (PR 24), but the app sets none itself; until then the UI is clickjackable on loopback. main.py:1323-1395

10. Low — file permissions rely on umask
secret.key is correctly 0600 (and permissions are healed on read). But the DB, daily backups, run dirs and data/logs/*.log{,.gz} get default umask — on this machine data/ is 0750, good; on a VPS with umask 022 they'd be world-readable. The logs contain masked account numbers, positions, and anything that escapes finding #3.

Fix: chmod 750 the data dir at startup (or os.umask(0o077) for created artifacts), like the secret-key path does.

11. Low — guard verifies config, not the actual socket
check_auth_guard trusts SA_HOST from .env; uvicorn's real bind comes from --host in the Makefile/deploy scripts. SA_HOST=127.0.0.1 in .env + uvicorn --host 0.0.0.0 passes the guard while listening publicly. PR 24's compose should drive both from the same env var — flagging so the deploy scripts don't diverge.

12. Minor
_alembic_version(path) opens run DBs read-write just to SELECT version_num; use mode=ro like the other readers. backtests.py:396-402
RunLock.acquire is check-then-write (TOCTOU); two simultaneous starts can both win. Single-user app, low risk — O_EXCL would close it. shutdown.py:122-135
_resolve_strategies: int(ref.split(":",1)[1]) throws an unhandled ValueError → 500 on strategy:abc. backtests.py:102-104
_log_where FTS5 MATCH ? — the " doubling doesn't fully quote the term, so FTS operators in ?search= raise OperationalError → 500. Harmless but noisy. routes.py:4341-4345
log_client_error tab line has a precedence quirk — works, reads confusingly.
GET /api/notifications?limit= is not clamped — same negative-limit class as #5.
What's done well (verified, not just assumed)
Secrets: Fernet, 0600 key file with permission healing, write-only API (only masked last-4 hints leave), env import-then-ignore, audit events without values, scrub of candidate keys at the endpoint (routes.py:2640). secrets_service.py
Paper-only: paper=True on every TradingClient/TradingStream, PA-prefix account check at startup, live URL absent + a test that walks git ls-files for it. alpaca.py:244-260
LLM safety: strict JSON schema + extra="forbid", code checks for every risk rule, invalid output → skip/hold; v2 the model only picks code-built option ids; prices immutably stamped per call. output.py
Flatten ordering: cancels all orders and confirms legs are dead before market sells — the earlier review lesson is implemented. executor.py:391-517
SQL: parameterized SQLAlchemy throughout; the one text() is static; raw sqlite3 calls are read-only/?-bound (except #5's f-string, which only interpolates a clamped int — fine once clamped).
Settings: whitelist validation (EDITABLE map, ranges, cross-field rules, broker asset checks) — nothing unlisted can be changed.
Backtest spawn: fixed sys.executable -m argv, no shell; run-dir slug is whitelist-checked before filesystem use and slugify can't produce ...
SMTP: mandatory STARTTLS with default verified context, no plaintext fallback, header injection guarded by email, password never in the message/log.
CI/deps: uv sync --locked, sockets disabled in pytest, dependabot weekly + 7-day cooldown, current versions across the board (fastapi 0.153, starlette 1.9, uvicorn 0.54, httpx 0.28.1, cryptography 50, vite 5.4.21 which carries the 2025 dev-server fixes).
PR 23 WIP crypto (as far as landed): Argon2id, HMAC-SHA1 TOTP ±1 step with compare_digest, hashed session/recovery tokens, Fernet-encrypted TOTP secret, sessions deleted on password reset, check_auth_guard fails closed on every non-loopback host spelling. passwords.py
Frontend: no dangerouslySetInnerHTML/innerHTML/eval, no tokens in storage, WS same-origin derived from location, error reporting fire-and-forget.
Recommended priority
Before any non-loopback deploy: finish PR 23 session middleware + CSRF/origin checks; meanwhile make SA_AUTH=required refuse to start (finding 2) and consider the interim Origin check (finding 1).
Scrub exc_info/exc_text properly (finding 3) — one-line-class fix for the hard rule.
Contain load_prompt to PROMPTS_DIR (finding 4).
Clamp log-endpoint params + export cap; fix /api/logs/client (findings 5, 6).
Don't send a stored SMTP password to a changed host (finding 7); add a body-size limit (finding 9).
chmod the data dir; align SA_HOST with the real uvicorn bind in the deploy scripts (findings 10, 11).
Happy to file these as issues or implement any of them — nothing was changed.
