# 083 · feat · Sign-in resilience

Kickoff for Devin. Start the session with: "Build docs/kickoffs/083-feat-sign-in-resilience.md."

Backend (one migration), the sign-in page, Settings → Access, `docs/DEPLOY.md`. Spec: `docs/SPEC.md` → Security hardening → Audit of 11 November 2026 → **Sign-in can't be paused from outside for a known browser** and **A WebSocket lives as long as its session**; Sign-in → Rate limits. Report: `docs/analysis/080-security-audit.md` (S6, S7). Design: `design/screens/Login.dc.html` (state 4) and `Settings.dc.html` → Access → Known browsers. Issues: #243 (S6), #244 (S7).

```text
[083] Sign-in resilience. Goal: failed sign-ins from elsewhere can't keep
the owner out; there is a documented way to stop the agent from the
server; a WebSocket ends with its session.
Start from the latest main.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md → Sign-in (all), Security hardening → Audit of
   11 November 2026; docs/CHANGES.md [080].
3. backend/app/auth/ (service, api, middleware), app/api/websocket.py,
   the kill-switch path in app/engine/executor.py and its API route.
4. design/screens/Login.dc.html, Settings.dc.html → Access;
   Login.png, Settings-access.png.

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR.
Put "Closes #243, closes #244" in the PR description.

1. trusted_devices (new migration): id, token_hash (SHA-256, unique),
   device_name, created_at, last_used_at, failures JSON or a count with
   a window start. At most 10 rows; the oldest (by last_used_at) goes.

2. Cookie. After finish_sign_in succeeds: set __Host-sa_device (32
   random bytes, prefix "sad_" so the scrubber knows its shape; HttpOnly,
   Secure, SameSite=Strict, Path=/, 180 days). Over plain http on
   loopback (SA_AUTH=required in tests) use sa_device without Secure, as
   the session cookie does. Register the value with the log scrubber.

3. Rules, in AuthService:
   - _check_allowed takes the device token: a valid one skips the global
     pause only. The per-address lockout, the password and the code are
     unchanged.
   - a failure with a valid device token also counts on the device: 5 in
     auth.lockout_window_minutes revoke it (row deleted, cookie cleared).
   - revoke all on password change, sign out everywhere and new recovery
     codes; revoke one from Settings.
   - GET /api/auth/status adds `paused_until` (when the global pause is
     on) and `known_device` (true only with a valid cookie).

4. API: GET /api/auth/devices (name, known since, last sign-in, this
   browser), POST /api/auth/devices/{id}/remove (confirm required).

5. GUI: Login as the design's states (unknown browser: the pause and its
   end, form disabled; known browser: the amber note and a working
   form). Settings → Access → Known browsers with Remove.

6. Break-glass. python -m app.ops.kill_switch --reason TEXT [--flatten]
   and --status:
   - writes SA_DATA_DIR/kill.request (JSON: reason, flatten, requested
     at; 0600; O_EXCL) and waits up to 30 s for kill.result;
   - the running app checks for the file every 5 s in an existing loop
     (not a new thread), runs the same code path as the kill-switch
     route with source "cli", writes kill.result (ok or the error) and
     removes the request;
   - never resumes; refuses when the app isn't running (no fresh
     run.lock heartbeat) with a clear message; a reason is required.
   docs/DEPLOY.md: "If you can't sign in" with the one-line command.
   Tests with the fake broker: request -> kill switch on within one
   poll, event source cli, flatten order sequence as the button's;
   malformed file -> ignored, logged, removed.

7. WebSocket.
   - the feed loop re-checks its session every 60 s (auth.authenticate
     on the stored token hash) and closes 1008 "session_expired";
   - AuthService keeps a registry of sockets per session id; sign-out,
     sign out everywhere, a password change and a device removal close
     them at once;
   - security.max_sockets (20, add to config.example.yaml and the README
     table): a new socket closes the oldest;
   - with SA_AUTH=off only the cap applies.
   Tests: expiry closes within 60 s of the fake clock; sign-out closes
   at once; the 21st socket closes the 1st.

8. Screenshots in docs/screenshots/083/: the four sign-in states,
   Settings → Access.

Rules: no network in tests; the device cookie never appears in a log or
an API answer; no datetime.now() (the clock); the break-glass file is
read by the app, never executed.

Done when: with the global pause on, a browser with a valid device
cookie signs in and one without is refused; five wrong passwords with a
device cookie revoke it; the CLI turns the kill switch on in the
simulated stack; make test, make security and the e2e (pr23 suite
extended) pass.
```

## Why

Audit of 11 November. S6: 20 wrong passwords an hour from anywhere pause sign-in for an hour, for everyone, and 20 requests an hour keep it paused. With an expired session Marc can't reach Pause or the kill switch, and nothing documents how to stop the agent from the server. S7: a socket stays open after its session ends.

## Assumptions

- A device cookie is a convenience credential: it lifts one rate limit and nothing else. Stealing it doesn't sign anyone in.
- The request file is the simplest channel into a read-only container with one writable volume; a socket or a signal would need more moving parts.

## Open questions for Marc

None. Decided without asking: 180 days for the device cookie; the break-glass command can stop and flatten but never resume.
