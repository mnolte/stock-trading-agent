# Ops — alerts, log volume and small fixes

Kickoff for Devin. Start the session with: "Build docs/kickoffs/ops-alerts.md."

```text
Small ops PR, after fix-live-2026-10-29. From the live-data report of
29 Oct. Read on main: docs/CHANGES.md entry 2026-10-29 "Stream health,
new alerts, library log levels"; docs/SPEC.md → Notifications (the "New
error" row) and Logging, error handling and shutdown (third-party
loggers); backend/config.example.yaml → logging.library_debug.

1. "New error" notification: the first time an ERROR fingerprint
   appears in a session, send the Warning notification (browser and
   email) with the message and a link to Logs. Once per fingerprint per
   session. The Readiness auto-resolve after 60 quiet minutes stays as
   it is; this is what makes a repeating bug visible anyway.
2. Third-party loggers (websockets, uvicorn access, apscheduler, httpx)
   at WARNING and above unless logging.library_debug is true. Today
   stock-agent.log grew 52 MB in 2.5 days, mostly their DEBUG
   keep-alive lines. Report the size per day before and after.
3. Frontend: GET /api/chart/ with an empty symbol (5 times on 29 Oct).
   Don't request a chart without a symbol.
4. After a position is found closed, don't ask Alpaca for that position
   again in the same pass (GET /v2/positions/RTX returned 404 four times
   within 8 s at 19:01 UTC on 29 Oct).
5. Unclean stop of 27 Oct: the log has about 2 KB of NUL bytes between
   pid 449328's last line (12:54:55) and pid 5349's start (13:00:46).
   Check whether the unclean-stop detection (run.lock, PR 20) logged its
   WARNING and ran quick_check at 13:00:46. If it didn't, find out why
   and fix it with a test.

Tests for 1, 2 and 4; a screenshot of the notification. Use the PR
template. Restart only outside 14:30–21:00 Amsterdam this week
(15:30–22:00 from 2 November).
```
