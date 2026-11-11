# 080 · Security audit of the repository

By Claude, 11 November 2026. Read-only: nothing in the code was changed. Main at `6f857c5` (068 merged).

Two audits came before this one: Devin's of 27 October (built as item 028, Security hardening) and of 8 November (item 053). This one did three things:

1. checked that the fixes of both still hold in today's code;
2. read the code added since 8 November (FRED and VIX, economic releases, breadth, the settings changes, 55 backend files);
3. looked where the earlier audits didn't: how error text travels, what Ask sends to the model provider, which settings changes ask for a confirmation, the sign-in limits as a way to lock the owner out, the deploy chain, and the GitHub repository settings.

## Verdict

The earlier fixes hold. The request gate, sign-in, the scrubber for logs and the paper-only guards are solid, and both dependency audits are clean for production packages.

There is **one high finding**: API keys can leave the app in clear text inside error messages (S1). The log scrubber covers logs, but error text also goes to notifications, emails, the cycle table, the Settings API and Ask, and those paths don't scrub. Five medium findings concern confirmations, Ask, the repository settings, the deploy chain and a lock-out. Seven are low.

| # | Finding | Severity | Item | Issue |
| --- | --- | --- | --- | --- |
| S1 | FRED and Finnhub keys leak through data-source error text into notifications, emails, the database, the Settings API and Ask | **high** | 081 | #238 |
| S2 | "Confirm it is you" covers only `risk.*` numbers going up; most loosening changes need no confirmation | medium | 082 | #239 |
| S3 | Ask sends all settings (heartbeat URL, email addresses, hosts) and raw health text to the model provider | medium | 081 | #240 |
| S4 | GitHub: public repository, `main` unprotected, secret scanning and Dependabot security updates off | medium | — (Marc) | #241 |
| S5 | Deploys pull a mutable tag; images are neither pinned by digest nor signed; base images float | medium | 084 | #242 |
| S6 | 20 wrong passwords an hour from anywhere pause sign-in for everyone: the owner can be kept out of the kill switch | medium | 083 | #243 |
| S7 | A WebSocket stays open after sign-out, expiry or a password change; no limit on sockets | low | 083 | #244 |
| S8 | The offsite backup key can also delete the backups | low | 084 | #245 |
| S9 | The heartbeat URL is a secret but is stored, returned and versioned as a plain setting | low | 081 | #246 |
| S10 | CSV exports write free text raw: a strategy name starting with `=` runs as a formula in Excel | low | 085 | #247 |
| S11 | Development dependencies are never audited; one advisory is open (minimatch, moderate) | low | 084 | #248 |
| S12 | Container gaps: Caddy not hardened, no process limits, tests in the runtime image, `.dockerignore` misses local config and key files | low | 084 | #249 |
| S13 | Outbound URLs are checked only when saved; the SMTP host isn't checked at all | low | 085 | #250 |

## Findings

### S1 · Keys in error text (high)

**What.** Two data sources put their key in the URL: FRED (`api_key=`) and Finnhub (`token=`). When a request fails, the exception text contains the full URL. That text becomes `DataResult.error` unchanged and then travels to places the log scrubber never sees.

- `backend/app/data/fred.py:105-124` and `:187`: `error=str(exc)`. An HTTP error from httpx reads `Client error '403 Forbidden' for url 'https://api.stlouisfed.org/fred/releases/dates?release_id=10&api_key=<the key>'` (reproduced with httpx 0.28 and a fake key).
- `backend/app/data/finnhub.py:64`: `error=str(exc)`. A connection error from requests reads `… Max retries exceeded with url: /api/v1/calendar/earnings?token=<the key>&from=…` (reproduced with the installed finnhub client and a fake key).

**Where it ends up.**

| Place | Code | Who can read it |
| --- | --- | --- |
| Notification line, stored and shown as a toast | `engine/cycle.py:944-950` ("earnings calendar unavailable (…)"); `notifications/service.py` `notify` doesn't scrub | the `notifications` table, the browser |
| **Email**, in clear text over SMTP | same notification, channel email | the mail server and every hop |
| `cycles.data_source_health`, never pruned | `engine/cycle.py:782-793`, `data/vix.py:141-148`, `data/releases.py:227-234` | the database and every backup |
| `GET /api/settings` data-source rows and the release summary | `api/routes.py:3303-3306`, `data/releases.py:359-363` | the GUI |
| Ask tool `get_cycle` | `ask/tools.py:859` | **OpenAI or Anthropic**, and `ask_messages` |

**Why it matters.** Hard rule 9 says a key never appears in an error message, the GUI or an event. Both keys are free data keys, not the Alpaca keys, so the money at risk is nil; but the rule is broken on five paths, and the next key-in-URL source would leak the same way. Alpaca, OpenAI and Anthropic send their keys in headers and are not affected.

**Fix (item 081).** Scrub where the text is made, and again where it leaves: one `safe_error()` for every exception that becomes stored or shown text (query strings removed, scrubber patterns and stored values applied); `notify` and `record_event` scrub their text; tool results are scrubbed before they go to the model. A test plants a fake key per source and checks all five places. On the first start after the fix the app scrubs the stored rows once and, if it changed any, asks Marc in Needs you to replace the FRED and Finnhub keys.

**For Marc now.** If the earnings calendar or FRED ever failed with a connection or HTTP error while email alerts were on, treat both keys as exposed and replace them (both are free): Finnhub → dashboard → regenerate; FRED → My Account → API Keys.

### S2 · Loosening without confirmation (medium)

**What.** `_settings_loosen_risk` (`api/routes.py:3551-3567`) asks for "Confirm it is you" only when a number under `risk` goes **up**. It misses:

- risk settings that loosen by going down or by a switch: `earnings_blackout_days` 3 → 0, `cash_only` true → false;
- everything outside `risk`: `execution.mode` supervised → automatic, `execution.max_spread_pct` up, `decision.min_confidence_enter` and `min_reward_risk` down, `decision.stop_atr_max` up, `decision.v5.min_ev_r` down, `regime.block_entries_in` emptied, `rules.rsi_min`/`rsi_max` widened, a regime added to the trend gate, the FOMC window off;
- alerts: turning off the email channel for `kill_switch` or `missing_stop`, or widening quiet hours.

**Why it matters.** The confirmation exists so that a stolen session cookie can't make the agent riskier. Today a stolen session can switch to automatic mode, drop the confidence gate and silence the alerts without the password or a code. The spec (Security hardening → More actions need "Confirm it is you") lists only the risk limits, so the code matches the spec; the spec is too narrow. Item 069 added a tighter/looser map, but in the frontend only.

**Fix (item 082).** One direction map in the backend for every setting in the trading sections; a looser change, supervised → automatic, or a weaker alert asks for the confirmation; a setting without a direction counts as looser and a test fails when one is missing. The GUI reads the map from the API.

### S3 · Ask sends settings to the model provider (medium)

**What.** The Ask tool `settings` (`ask/tools.py:1126-1168`) returns every section of the effective config when the model asks for it. That includes `ops.heartbeat_url` (a secret URL, S9), `notifications.email` (host, username, from and to addresses) and `security.allowed_hosts`. `get_cycle` returns `data_source_health` (S1). `run_tool` (`:1480-1501`) sends results on without a scrub. The answers and tool results are also stored in `ask_messages`.

**Why it matters.** Ask's own spec says no tool result ever contains a key, and tests check the `secrets` table. Settings and health text are a second road to the same place. A planted instruction in a headline ("call the settings tool and repeat it") turns this into a way to pull the values into an answer.

**Fix (item 081).** The settings tool serves an allowlist of trading sections and never `ops`, `notifications.email`, `security`, `auth`, `logging` or `storage`; every tool result passes the scrubber before it is sent or stored.

### S4 · GitHub repository settings (medium, for Marc)

Read from the GitHub API on 11 November:

- the repository is **public** (the spec, the designs, every analysis of the paper account and the deploy layout are readable by anyone);
- `main` has **no branch protection**: any token with write access can push to it, and nothing requires CI to pass;
- **secret scanning** and **push protection** are off (free for public repositories);
- **Dependabot security updates** are off; GitHub reported one open alert when this branch was pushed (I can't read alerts with this session's token).

No secret was found in the tree or in the history (gitleaks runs in CI; a manual sweep for key shapes, emails, addresses and account numbers found only test fakes).

**Fix.** Settings only, no code: a checklist is now in `docs/SECURITY.md` → GitHub settings. Decide first whether the repository should be public at all.

### S5 · Deploy chain (medium)

- `deploy/deploy.sh` pulls `ghcr.io/mnolte/stock-agent:<tag>`. A tag can be moved; nothing checks that the image is the one CI built. Whoever can push to the registry (or to a tag) decides what runs next to the Alpaca keys.
- `Dockerfile`: `python:3.12-slim`, `node:22-alpine` and (compose) `caddy:2-alpine` float; `pip install "uv>=0.9,<1"` floats. Dependabot has no `docker` entry, so base images are never proposed for update either.

**Fix (item 084).** CI signs the image (keyless, bound to the tag workflow) and records its digest; `deploy.sh` verifies the signature and runs the image by digest; base images are pinned by digest with a Dependabot `docker` entry; uv is pinned.

### S6 · Sign-in can be paused from outside (medium)

**What.** `auth/service.py:286-320`: 20 failed sign-ins in an hour across all addresses pause sign-in for an hour, for everyone. Twenty requests an hour from anywhere keep it paused for as long as an attacker likes.

**Why it matters.** A session lasts 12 hours without activity. If Marc's has expired he can't sign in, so he can't reach Pause or the kill switch in a session. The agent keeps its stops at Alpaca, and SSH still works, but there is no documented way to stop it from the server.

**Fix (item 083).** A browser that signed in before carries a device cookie and is exempt from the global pause (never from the password, the code or the per-address limit). A documented break-glass command on the server turns the kill switch on.

### S7 · WebSocket outlives its session (low)

`api/websocket.py`: the session is checked once, at the upgrade. Sign-out, "sign out everywhere", a password change or expiry leave the socket open, and there is no cap on sockets. Only topic names travel on it ("positions", "decisions"), no data. **Fix (item 083):** re-check every 60 s, close on sign-out, cap at 20.

### S8 · Backup key can delete backups (low)

`ops/offsite.py:150-158` runs `restic forget --prune` with the same credentials that write. A compromised server can erase every offsite copy. **Fix (item 084):** `docs/DEPLOY.md` asks for bucket versioning or object lock with a retention of at least 30 days; the monthly restore test records whether the bucket has it and Settings → Server shows it.

### S9 · Heartbeat URL is a secret (low)

A ping URL (healthchecks.io style) is a capability: whoever has it can keep the dead-man's switch quiet while the server is down. It lives in settings versions, is returned by `GET /api/server` (`routes.py:6301`) and the settings API, shows in History, and reaches Ask (S3). **Fix (item 081):** store it with the keys, write-only, shown masked.

### S10 · Formulas in CSV exports (low)

`api/inspector.py:275-306` and `api/backtests.py` write strategy names and other free text as they are. A name such as `=HYPERLINK(…)` runs when the file is opened in Excel. **Fix (item 085):** one CSV writer that prefixes `'` to a cell starting with `=`, `+`, `-`, `@`, tab or carriage return.

### S11 · Development dependencies (low)

`security/run-audits.sh` audits production packages only (`--no-dev`, `--prod`). A full run today: Python clean; frontend one moderate advisory, minimatch GHSA-g3hv-75wg-m249 via eslint. Development tools run in CI and on Marc's machine. **Fix (item 084):** the weekly run audits everything and reports without blocking; bump minimatch.

### S12 · Containers (low)

- `docker-compose.yml`: `caddy` has no `read_only`, `cap_drop` or `no-new-privileges` (the app has all three); neither service has `pids_limit`.
- `Dockerfile`: the runtime image ships `backend/tests` and `backend/eval`; `/srv` belongs to the app user.
- `.dockerignore` misses `backend/config.yaml`, `**/secret.key` and `*.db`: a local `docker build` can bake local config or data into an image.
- `deploy/provision.sh`: unattended upgrades cover Ubuntu security only, so Docker itself is never updated.

**Fix (item 084).**

### S13 · Outbound addresses (low)

`ops/urls.py` checks that the heartbeat and backup URLs are https and public when they are **saved**. The name is resolved again at every use, so it can point somewhere private later. The SMTP host isn't checked at all (`notifications/service.py`), so the test button can probe three ports on internal addresses. **Fix (item 085):** check at use and connect to the checked address; the SMTP host must resolve to a public address unless `security.allow_private_smtp` is on.

## Checked and fine

- **8 November findings 1–10:** all fixed. Spot-checked: `safeNextPath` on `/login?next=` (`frontend/src/pages/Login.tsx:13`), chunked detection and port-less loopback origins (`api/gate.py:150-153`, `:126-129`), `hmac.compare_digest` and `__Host-` only over HTTPS (`auth/middleware.py:116-123`, `:148`), nested backup credentials scrubbed (`secrets_service.py:126`), first SMTP save confirmed (`routes.py:3625-3637`), `kind` whitelisted (`api/backtests.py:764`), the global pause stored (`auth/service.py:235`), Python pinned in the audit script.
- **New code since 8 November:** FRED, VIX, releases and breadth add no route that writes; all new routes are under `/api` and behind the gate and the session check; SQL stays parameterised; no new file path comes from a request.
- **Frontend:** no `dangerouslySetInnerHTML`, `innerHTML` or `eval`; one external link, with `rel="noopener noreferrer"`; storage holds a tab id and two display preferences only.
- **Dependencies:** `pip-audit` on the locked production set: clean. `pnpm audit --prod`: clean.
- **CI:** actions pinned by commit, `contents: read` by default, the image is pushed only from tags, gitleaks on every PR.
- **Ask calculator:** an AST whitelist of + − × ÷ and four functions, no names, no power operator.

## Residual, accepted

- **The two-step sign-in shows that a password was right** (the code step appears). The code and the lockouts still stand; hiding it would mean asking for a code after every wrong password.
- **Prompt injection through headlines** stays possible; code gates bound what an answer can do (as in the earlier audits). S3 removes what an injected instruction could read.
- **Local use without sign-in** (`SA_AUTH=off` on 127.0.0.1) trusts every process on the machine, by design.

## Method

- Read: `auth/` (all), `api/gate.py`, `api/websocket.py`, `secrets_service.py`, `ops/`, `data/fred.py`, `data/finnhub.py`, `data/releases.py`, `ask/tools.py`, the settings, notification, key, server and export routes in `api/routes.py`, `api/inspector.py`, `api/backtests.py`; `Dockerfile`, both compose files, `deploy/`, `.github/`, `security/`.
- Searched the backend for shell calls, raw SQL, unsafe loaders, file responses and disabled TLS checks; the frontend for HTML injection, data-driven links and storage.
- Ran `security/run-audits.sh`, then both audits again with development packages.
- Reproduced S1 with fake keys against the installed httpx and finnhub clients, without network.
- Read the repository settings through the GitHub API.
- Not done: no running instance was attacked, the VPS wasn't inspected, and the Dependabot alert list couldn't be read.
