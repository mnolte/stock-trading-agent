# Security

Threat model, the layers that cover it, and what this design does not
try to do. The normative rules are docs/SPEC.md → Security hardening and
AGENTS.md hard rules 9 and 18–20.

## Threat model

- **A web page Marc visits attacks the local app.** Any browser tab can
  send POSTs to 127.0.0.1; a `text/plain` write isn't even a CORS
  preflight. Without guards, Stop & flatten, settings or new API keys
  were one `fetch()` away.
- **DNS rebinding.** A hostile name pointed at 127.0.0.1 would serve the
  GUI or API to that origin.
- **Secrets in logs.** A key inside a URL, an exception argument or an
  Authorization header must never survive to file, console or index.
- **Stored credentials redirected.** A saved password must only go to
  the server it was saved for.
- **Unbounded or hostile input.** Query params, search text, exports and
  client-reported errors must be clamped, quoted or rejected.
- **Supply chain.** Unpinned Actions, unaudited dependencies, committed
  secrets.

- **Keys in error text** (audit of 11 Nov, item 081). A key inside a
  URL ends up in the exception text; that text must be scrubbed before
  it is stored, shown, emailed or given to a model, not only before it
  is logged.
- **A stolen session.** It must not be able to make the agent riskier
  or quieter without the password or a code (item 082).
- **The owner locked out.** Rate limits must not keep the owner from
  the kill switch (item 083).
- **The deploy chain.** The image that runs must be the image CI built
  (item 084).

## Layers

- **Request gate** (`app/api/gate.py`): one middleware in front of
  everything including sign-in — Host allowlist (`bad_host`), Origin and
  `Sec-Fetch-Site` on writes (`origin_refused`), JSON-only write bodies
  (`unsupported_media_type`), streaming body caps (`too_large`).
- **App headers**: CSP, nosniff, X-Frame-Options, Referrer-Policy, COOP
  on every response, `no-store` on /api; Caddy's `?`-defaults defer.
- **Sign-in** (PR 23): `SA_AUTH`, sessions, CSRF, `require_confirmed`
  for dangerous changes — on top of the gate, never instead.
- **Scrubber**: shared patterns in every log handler and the log index.
- **Prompt/file integrity**: whitelisted names, resolved-inside checks,
  `prompts/manifest.json` SHA-256, `mode=ro` reads, `O_EXCL` run.lock.
- **Bounded inputs**: clamped limit/window, typed ids → 422, quoted FTS
  phrase, capped exports, per-IP client-error rate limit.
- **Credentials**: SMTP destination change → `password_required`, ports
  465/587/2525, 10 s timeout, category-only failures; backup and
  heartbeat URLs must be https and non-private.
- **Supply chain**: pinned Actions, `contents: read`, pip-audit and
  `pnpm audit --prod` in CI with an expiry-dated allowlist, gitleaks.

## Not covered

- A hostile process already on the machine (it owns the database file).
- Physical access, evil extensions, or a compromised browser.
- The paper Alpaca account itself (live trading is refused at boot).
- Denial of service beyond the listed caps — this is a local app.

## GitHub settings

Settings of the repository itself; no code can set them. Checked on
11 November 2026 (audit finding S4, issue #241): all four were open.

- [ ] **Public or private.** The repository is public: the spec, the
  designs, every analysis of the paper account and the deploy layout
  are readable by anyone. Decide; if it stays public, keep real
  domains, addresses and account numbers out of it (none are in it
  today).
- [ ] **Protect `main`.** Require a pull request, require the CI checks
  (`backend-lint`, `backend-test`, `frontend-check`, `e2e`, `security`,
  `spec-label`, `test-image`, `compose-test`), no force pushes, no
  deletion.
- [ ] **Secret scanning and push protection:** on.
- [ ] **Dependabot security updates:** on, and read the open alert.
- [ ] **Your GitHub account:** two-factor sign-in on; tokens given to
  Devin and Claude limited to this repository.
