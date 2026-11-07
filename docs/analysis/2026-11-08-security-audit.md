# Security audit

Read-only analysis by Devin, 8 November 2026 (work item 052). Nothing
was changed by the audit itself.

Scope: the entire repo — backend (FastAPI/SQLite, ~150 files), frontend
(React/Vite), Makefile, Dockerfile, compose, deploy scripts + Caddyfile,
CI workflows, hooks, dependency audits (pip-audit on `uv.lock`,
`pnpm audit --prod`), and a secret sweep. Threat model:
`docs/SECURITY.md`. Method: re-verified every finding from the
27 October audit against current code, then a fresh pass over each
layer, ending with the repo's own hard-rule checklist (AGENTS.md 1–21).

## Verdict

The October findings are **all fixed**, and several fixed well beyond
the letter of the report (the gate, the auth layer, flatten ordering).
The codebase is disciplined: secrets are Fernet-encrypted write-only,
every Alpaca client is `paper=True` with a PA-prefix account check,
model output can never reach the broker unvalidated, the request gate
covers every `/api` route and the WebSocket in one place, and
dependency/supply-chain hygiene is good. The remaining findings are one
medium frontend open redirect, a handful of low-severity hardening
gaps at the gate/cookie/scrubber edges, and minor robustness warts.
No critical or high findings.

## Prior-findings status (2026-10-27 audit)

| # | Finding | Status | Evidence |
| - | ------- | ------ | -------- |
| 1 | Every write CSRF-able | **Fixed** | `RequestGateMiddleware` checks Host on every request and Origin/`Sec-Fetch-Site` + JSON-only content type on every write and WS upgrade (`backend/app/api/gate.py:193-282`); session writes additionally need the `X-CSRF-Token` double-submit (`backend/app/auth/middleware.py:132-138`). |
| 2 | `SA_AUTH=required` did nothing | **Fixed** | Full sign-in layer: sessions, Argon2id, TOTP with replay protection, route-table coverage walk that fails closed (`backend/app/auth/guard.py:143-162`); the value is a `Literal` normalised once in `EnvSettings` (`backend/app/config.py:50,62-66`) — the raw-vs-normalised bug is gone. |
| 3 | Secrets in tracebacks bypassed scrubber | **Fixed** | `SecretScrubFilter` renders `exc_info` itself, scrubs it into `exc_text` and clears it (`backend/app/logging_config.py:145-173`); `JsonFormatter` scrubs exception text again at emit (`logging_config.py:261-274`). |
| 4 | `load_prompt` path traversal | **Fixed** | Whitelist regex + resolved-inside check + `prompts/manifest.json` SHA-256; unknown versions refused outright (`backend/app/decide/prompt.py:23,133-153`). |
| 5 | Unbounded/negative log params | **Fixed** | `_int_param` clamps and 422s on garbage (`backend/app/api/routes.py:5646-5658`), FTS goes through phrase quoting (`routes.py:5661-5663`), export is streamed and capped at `security.export_max_rows` (`routes.py:5794-5834`). |
| 6 | `/api/logs/client` forging/leak | **Fixed** | Per-client-IP rate limit with a bounded sender map and CR/LF+control-char stripping (`routes.py:5534-5599`). |
| 7 | Stored SMTP password sent to a new host | **Fixed** | Destination change requires the password again *before any connection* plus `require_confirmed`; ports whitelisted to 465/587/2525; failures answer with a category only (`routes.py:3341-3370`, `notifications/service.py:610-682`). |
| 8 | WebSocket open to any origin | **Fixed** | The gate treats every WS upgrade as a write (origin check), and the session middleware covers `/ws` under `SA_AUTH=required` (`gate.py:215-235`, `middleware.py:124-130`). |
| 9 | No body cap, no security headers | **Fixed** | Streaming caps `max_body_kb`/`max_upload_kb` counted while reading; CSP/nosniff/XFO/Referrer-Policy/COOP on every response incl. gate refusals (`gate.py:240-264,305-352`). |
| 10 | File permissions relied on umask | **Fixed** | `umask 077` at boot plus an explicit 0700/0600 sweep over `data/` (`backend/app/filemodes.py`). |
| 11 | Guard verified config, not the bind | **Fixed** | `python -m app.serve` is the only entry and reads `SA_HOST`/`SA_PORT` itself; no `--host`/`--port` anywhere (a test enforces it). |
| 12 | Minors | **Fixed** | `_alembic_version` is `mode=ro` (`api/backtests.py:478-483`); `RunLock` is `O_EXCL` 0600 (`shutdown.py:156-157`); bad `strategy:` refs are 422 (`api/backtests.py:110-115`); FTS terms are quoted phrases; `/api/notifications` limit is clamped (`routes.py:3299`); the tab_id quirk is gone with the rewrite. |

## New findings

### Medium

**1. Open redirect on `/login?next=`**
`frontend/src/pages/Login.tsx:13,97` — after a successful code step the
page does `window.location.assign(next)` with `next` taken straight
from the query string. `https://<app>/login?next=https://phish.example`
bounces a freshly signed-in victim to an attacker site; an absolute URL
or protocol-relative `//evil.com` both work (a `javascript:` URL is
blocked by modern browsers for `location.assign`, so XSS is unlikely —
the realistic impact is post-auth phishing on the app's own domain).
Fix: `const safe = /^\/(?!\/)/.test(next) ? next : "/"` — the sign-in
flow itself is the app's most security-sensitive page, so validate
there and in `client.ts:94`'s redirect builder (it already produces
same-origin paths, but the check belongs where the value is used).

### Low

**2. `Transfer-Encoding` detection is fragile in the request gate**
`backend/app/api/gate.py:243` — `te == "chunked"` misses `Chunked` and
token lists like `gzip, chunked`. A POST with only such a TE header and
no Content-Type/Content-Length fails `has_body`, so the streaming cap
and the JSON-only rule are both skipped while uvicorn still delivers
the body to the app. FastAPI then rejects non-JSON parses as usual, so
the practical hole is an uncapped body, not an injection — but the cap
exists for a reason. Fix: `te = _header(...).lower()` and
`"chunked" in [p.strip() for p in te.split(",")]`.

**3. Port-less loopback `Host`/`Origin` always passes the gate**
`backend/app/api/gate.py:65,110-128` — `Host: localhost` (no port) maps
to `port=None` → allowed, and an `Origin: http://localhost` header the
same. A page served by *another local process* on :80/:443 could
therefore issue writes whose Origin passes — the check only bites when
the other server runs on a non-SA_PORT loopback port *with* an explicit
port in the Origin. Low because it needs a hostile local web server on
a privileged port; tighten by requiring the explicit loopback port for
Origins (`Origin` without a port should be refused, not defaulted).

**4. CSRF double-submit compares with `!=`**
`backend/app/auth/middleware.py:136` — not exploitable in practice (a
timing oracle needs the cookie value to attack), but
`hmac.compare_digest` is the free, idiomatic fix.

**5. The non-`__Host` session cookie is accepted over HTTPS**
`backend/app/auth/middleware.py:111` (and `api.py:204-209`) — a request
carrying `sa_session` authenticates even when `__Host-sa_session` is in
use. A cookie planted over plain HTTP/subdomain could substitute a
session the attacker holds (login-CSRF shape; the victim lands in
*their own* single-user account, so impact is minimal, but the
`__Host-` guarantee is diluted). Fix: when `request_is_https`, only
accept the `__Host-` name.

**6. `backup_credentials` scrubs the blob, not the nested keys**
`backend/app/secrets_service.py:370-373` +
`backend/app/api/routes.py:6081-6092` — the stored value is one JSON
string; `scrub_values` replaces only whole stored values, so a lone
`secret_access_key` inside a message survives unless the key happens to
match a `_KEY_PATTERNS` shape (AKIA-style does; other S3 vendors may
not). Fix: register the parsed `access_key_id`/`secret_access_key`
fields as scrub values too (e.g. in `SecretsStore.save`/`scrub_values`
for that name).

**7. First-time SMTP setup needs no "Confirm it is you"**
`backend/app/api/routes.py:3345-3354` — `require_confirmed` fires only
when SMTP is already configured. A session-holder configuring SMTP for
the first time (their host, their password) redirects every future
alert — lockout warnings, sign-in notices — to infrastructure they
control. Under a stolen session that is quiet exfil of alert content;
suggest confirming whenever the SMTP card is saved, not only on change.

### Minor

**8. `export.csv`'s `kind` reaches `Content-Disposition` raw**
`backend/app/api/backtests.py:816` — any `kind` other than `decisions`
silently exports trades, and `kind` is interpolated into the filename
header. A `"`/CR-LF in `kind` makes h11 reject the response (500), not
an injection — whitelist `kind in ("trades", "decisions")` → 422.

**9. The global sign-in pause is in-memory**
`backend/app/auth/service.py:158,301` — `_global_locked_until` resets
on restart, so a crashed/killed app loses the 1-hour global pause (the
per-address DB-backed lockout survives). Low.

**10. `run-audits.sh` breaks under Python < 3.12**
`security/run-audits.sh` — `uvx --from pip-audit pip-audit` resolved
under this machine's Python 3.11 and failed to install `numpy==2.5.4`
(requires ≥3.12), reporting as an audit failure rather than a clean
run. CI is unaffected (`setup-python` pins 3.12). Verified working with
`uvx --from pip-audit --python 3.12 pip-audit` → no known
vulnerabilities. Fix: pin `--python 3.12` in the script.

## Residual risks (by design, worth restating)

- **Prompt injection via market data.** News/earnings text from Alpaca
  and Finnhub flows into decision prompts; a hostile headline could
  steer the model. Bound by rule 7 — invalid model output becomes
  `skip`/`hold` and every money decision passes code gates — so the
  worst case is a blocked or odd decision, never an unchecked order.
- **`/api/auth/status`** is intentionally open and reveals
  `required`/`signed_in` only; `/api/auth/sign-in`+`/code` are the only
  other open paths and sit behind per-IP and global lockouts.
- **Non-browser writes pass the gate** (no Origin/`Sec-Fetch-Site`) —
  intended for curl/scripts; the CSRF story is browser-focused by
  spec. Under `SA_AUTH=required` the session + double-submit still
  applies; under `off` the app is loopback-only by the startup guard.
- **Inspector/decision detail return prompt text and raw model
  responses** — deliberate transparency features; behind sign-in on the
  VPS.
- **Docs are exposed only when `SA_AUTH=off`** (`main.py:1695-1702`).

## What's done well (verified in code, not assumed)

- **Request gate + headers**: deny-by-default Host allowlist (IPv6
  brackets, port rules, DNS-rebinding names refused), Origin +
  `Sec-Fetch-Site` on every write and WS upgrade, JSON-only writes,
  streaming byte-counted caps, strict CSP + `no-store` on `/api` —
  outermost so refusals carry them too (`api/gate.py`, `main.py:1762-1779`).
- **Auth**: Argon2id, TOTP `±1` step with `hmac.compare_digest` *and*
  step-replay protection, single-use hashed recovery codes,
  `sap_`-pending tickets bound to client IP with 3 attempts,
  per-address (5/15 min) and global (20/h) lockouts with notification,
  idle+absolute session expiry, confirm-failure lockout that kills the
  session, sessions deleted on password change, secure cookie policy
  per scheme (`auth/`).
- **Secrets**: Fernet-encrypted, key file 0600 with permission healing
  and `O_EXCL`, write-only API (masked last-4 only), candidate keys
  scrubbed *before* they could reach an error, env import-then-ignore,
  `sa_t|p|c_`-shaped tokens scrubbed by pattern (`secrets_service.py`,
  `logging_config.py:59-72`).
- **Log pipeline**: one scrub filter shared by file/console/index, live
  provider values (stored keys + TOTP + pending tokens), exception text
  scrubbed at filter *and* emit, index inserts scrubbed, per-IP client
  log cap with control-char stripping.
- **Every route**: parameterised SQLAlchemy / `?`-bound sqlite, the
  `mode=ro` readers, whitelisted names before filesystem use
  (`is_safe_name`, `PROMPT_NAME_RE`, bucket regex), typed/clamped
  params → 422 never 500, `require_confirmed` on kill-switch/flatten/
  resume/keys/settings-risk-loosening/SMTP/backup/server actions,
  `security.*` not editable through the API at all.
- **Outbound URLs** (heartbeat, SMTP, backup endpoint): `https://` +
  literal *and* DNS-resolved IP must be public, no userinfo
  (`ops/urls.py`); destination changes re-require the credential
  (hard rule 20).
- **Broker/engine**: `paper=True` on every client, PA-prefix account
  check at startup, `no_shorting`+1× margin guard, flatten = cancel →
  confirm legs dead → wait for `held_for_orders` release → sell free
  qty only, never resend after error, `Decimal` money, injected clock.
- **Frontend**: no `dangerouslySetInnerHTML`/`innerHTML`/`eval`, no
  secrets in storage (tab id, log prefs only), CSRF double-submit
  header on every write, same-origin WS from `location`, error
  reporting fire-and-forget.
- **Infra**: non-root image (uid 10001), `read_only` + `cap_drop: ALL`
  + `no-new-privileges` compose, `SA_AUTH=required` baked in, Caddy the
  only public port with `X-Forwarded-For {remote_host}` (client XFF
  can't be spoofed), HSTS + Permissions-Policy, provision locks sshd
  to keys-only, restore whitelists snapshot names.
- **Supply chain**: SHA-pinned actions, `contents: read` default, image
  push only on tags with `GITHUB_TOKEN`, `uv sync --locked`,
  dependabot weekly + 7-day cooldown, pip-audit + `pnpm audit --prod`
  + gitleaks in CI, `.dockerignore` excludes `.env`/`data/`.
- **Dependency audits today**: `pip-audit` on the frozen `uv.lock`
  (Python 3.12) — clean; `pnpm audit --prod` — clean. Manual secret
  sweep of `git ls-files` — only planted fakes in tests.
- **Tests enforce the rules**: sockets blocked, live-Alpaca-URL absence
  walk, route-table session coverage, no-host-flags, gate/auth suites.

## Recommended priority

1. Fix the `?next=` open redirect (finding 1) — only medium, but it sits
   on the sign-in path.
2. Tighten the two gate edges: TE token parsing (2) and port-less
   loopback Origins (3).
3. `compare_digest` the CSRF compare (4); accept only `__Host-` under
   HTTPS (5).
4. Register the nested backup-credential fields for scrubbing (6);
   extend `require_confirmed` to first SMTP save (7).
5. Whitelist `kind` (8); persist or accept the global-pause reset (9);
   pin pip-audit's Python (10).

Nothing here blocks the current local or VPS posture; all findings are
filed as GitHub issues under `[052]`.
