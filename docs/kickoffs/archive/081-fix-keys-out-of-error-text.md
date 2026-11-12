# 081 · fix · Keys out of error text

Kickoff for Devin. Start the session with: "Build docs/kickoffs/081-fix-keys-out-of-error-text.md."

**Do this one first: finding S1 is high.** Backend, one migration, a small Settings change. Spec: `docs/SPEC.md` → Security hardening → **Audit of 11 November 2026** (the paragraphs "Error text is scrubbed where it is made", "Ask sees trading settings only", "The heartbeat URL is a secret") and hard rule 9 in `AGENTS.md`. Report: `docs/analysis/080-security-audit.md` (S1, S3, S9). Design: `design/screens/Settings.dc.html` → Server → Heartbeat (`Settings-server.png`). Issues: #238 (S1), #240 (S3), #246 (S9).

```text
[081] Keys out of error text. Goal: no key can leave the app inside an
error message, a notification, an email, a database row, an API answer or
an Ask tool result; Ask sees trading settings only; the heartbeat URL is
stored like a key.
Start from the latest main.

Read first:
1. AGENTS.md (hard rule 9 as changed) and docs/README.md.
2. docs/SPEC.md → Security hardening → Audit of 11 November 2026;
   docs/analysis/080-security-audit.md → S1, S3, S9; docs/CHANGES.md [080].
3. backend/app/logging_config.py (the scrub patterns and providers),
   app/secrets_service.py (scrub, scrub_values), app/data/fred.py,
   app/data/finnhub.py, app/data/releases.py, app/data/vix.py,
   app/engine/cycle.py (data_source_health, the source_failing notify),
   app/notifications/service.py (notify), app/engine/state.py
   (record_event), app/ask/tools.py (settings, get_cycle, run_tool),
   app/ops/service.py (heartbeat).

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.
Two PRs: steps 1–5 first (S1, the urgent part), then 6–8. Put
"Closes #238" in the first and "Closes #240, closes #246" in the second.

1. safe_error. In app/errors.py: safe_error(exc_or_text, extra=()) -> str.
   - removes the query string from every URL in the text (keep scheme,
     host, path);
   - applies the scrubber's key patterns (reuse them from
     logging_config, one source) and every registered secret value, plus
     `extra`;
   - cuts to 300 characters.
   Unit tests: an httpx.HTTPStatusError and a requests ConnectionError
   carrying "api_key=FAKE…" and "token=FAKE…"; an Authorization header;
   a stored key value without a URL.

2. Use it at every source of stored or shown exception text:
   - every DataResult(error=…) in app/data/ (alpaca, finnhub, fred,
     ecb, releases, vix, news) and app/backtests/sources.py;
   - data_source_health values, key-test results, llm error messages
     (check they already scrub), API `detail=` built from an exception
     (routes.py: kill-switch 422s, backup-now 500, and the rest);
   - a test walks app/ with ast for `str(exc)`, `f"{exc}"`, `repr(exc)`
     outside logging calls and fails on any not in a short allowlist
     with a reason (same style as the ruff ignores).

3. Second line. NotificationService.notify scrubs line, body and
   details; record_event scrubs details (strings at any depth), with the
   same patterns and values. Tests with a planted key for both.

4. Stored rows, once. At startup, guarded by a flag row so it runs one
   time: scrub cycles.data_source_health, notifications.subject and
   body, events.details and ask_messages content with the current secret
   values and the patterns; commit in batches of 500, outside any
   session hours restriction (it is a write on old rows only); log the
   count per table. If any row changed, add the Needs you item "A key
   appeared in stored error text. Replace the FRED and Finnhub keys"
   (order 2; leaves when both keys were replaced after the scrub, or on
   dismiss with a reason). Test with planted rows.

5. End-to-end test per data source (fake clients, no network): a fake
   key; one HTTP error and one connection error; assert the key is
   absent from the DataResult, the notifications row, the fake mailer's
   body, the cycles row, GET /api/settings, GET /api/server and the
   get_cycle tool result.

6. Ask (S3).
   - settings tool: serve only the allowlisted sections from the spec;
     universe without names; channels as {email: "on"|"failing"|"off",
     browser: …} without host, user or addresses. A test fails when
     config.example.yaml gains a top-level section that is in neither
     the allowlist nor a "never" list.
   - run_tool: pass every result through one scrub (strings at any
     depth) before it is returned; the stored tool result is the
     scrubbed one.
   - Tests: no tool result for any tool contains a stored secret, the
     heartbeat URL, an email address from settings or the SMTP host.

7. Heartbeat URL as a secret (S9).
   - secrets name `heartbeat_url`; POST /api/server/heartbeat (confirm
     required, the https-and-public check, write-only) and
     …/heartbeat/remove; status returns host + "••••" + last 4, last
     ping and configured.
   - Migration: move the current effective ops.heartbeat_url into
     secrets and blank the key in every stored settings version (a data
     migration in app startup like step 4 if Alembic can't reach the
     Fernet key; say which you chose). `ops.heartbeat_url` in config or
     the environment is imported once and then ignored with a warning,
     like the API keys.
   - GET /api/server, GET /api/settings and settings History no longer
     contain the URL. Settings → Server → Heartbeat as the design:
     masked value, last ping, "Replace…".

8. Screenshots in docs/screenshots/081/: Settings → Server with the
   masked heartbeat; the Needs you item of step 4.

Rules: no network in tests; fake keys only; never log or return a key
while testing the scrubber (assert on absence); hard rule 13 for the
startup scrub (short transactions).

Done when: the tests of steps 1–7 pass; `grep -rn "str(exc)" backend/app`
shows only allowlisted lines; make test, make security and the e2e pass.
```

## Why

Audit of 11 November, S1 (high): FRED and Finnhub send their key in the URL. A failed request's exception text contains that URL, and `DataResult.error = str(exc)` carries it into the notification line and its email, `cycles.data_source_health`, the Settings API and the Ask tool `get_cycle`, which sends it to the model provider. Reproduced with fake keys. The log scrubber is not on any of those paths.

S3 and S9 are the same class: text the owner would call secret (the heartbeat URL, email addresses) leaves through Ask or sits in settings versions.

## Assumptions

- Removing the query string from URLs in error text loses nothing needed for diagnosis; the path and status remain.
- The one-off scrub can only find keys that are still stored. A key replaced earlier is found by the patterns only if it has a known shape or sits in a URL parameter.

## Open questions for Marc

None. Decided without asking: after the one-off scrub finds anything, the app asks you to replace both keys rather than judging which leaked.
