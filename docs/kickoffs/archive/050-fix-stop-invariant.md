# 050 · fix · Stop invariant

Kickoff for Devin. Start the session with: "Build docs/kickoffs/050-fix-stop-invariant.md."

```text
[050] Stop invariant. From the live data of 4–5 November: KO kept 7
shares without a stop at Alpaca for 6 hours, every repair pass logged
"already covered" at INFO, the error resolved itself after an hour, Needs
you never showed it, and the app was down for an hour with nobody told.
The 043/044 fixes closed that one path. This item makes "every position
has a stop at Alpaca" a check of its own, so the next path that loses a
stop is caught the same way. Start from the latest main.

Read first:
1. docs/analysis/050-live-data-2026-11-04.md (Devin's report and Claude's
   review; the table says what is already fixed).
2. docs/SPEC.md, as changed by the spec PR of this item (docs/CHANGES.md,
   entry [050]):
   - Architecture → Config layers;
   - Alpaca integration → Reconciliation (last bullet) and Stop check;
   - GUI → Needs you (the table) and Notifications (the missing_stop row);
   - Readiness → Errors;
   - Logging → Unclean stop detection;
   - VPS deployment → Backups (Not set up) and Monitoring (Heartbeat);
   - Model providers → Model list (supports_temperature).
3. design/screens/States.dc.html: "Position without a stop at Alpaca ·
   stop-missing episode", the Needs you reference (first row), the unclean
   banner wording.
4. AGENTS.md hard rules. This item touches the trading path: every change
   gets a test that fails before it and passes after. No network in tests.

Config, added to backend/config.example.yaml in the step that reads it
(rule 7):

execution:
  exit_in_flight_seconds: 60     # a market sell younger than this counts as protection

Steps, one commit each, in this order:

1. Config layers. config.py loads backend/config.example.yaml always, then
   config.yaml (local, optional) over it, then the active settings, key by
   key (the existing _deep_merge). Start-up logs WARNING once per key
   present in config.yaml or the settings but not in the example, and
   ignores it; and once per model id used by decision.model,
   reviews.models.*, ask.model or a strategy that is missing from the
   merged models list. Saving such a model is refused (the settings save
   path). Settings → Server shows "Config: n local overrides · n unknown
   keys". Tests: a key added only to the example is visible with an old
   config.yaml; an unknown key warns once and is ignored; a settings save
   with an unknown model is refused; gpt-6.1-sol without the flag in
   config.yaml still gets supports_temperature false from the example.
   Check that make test, make dev and the Docker image still boot from the
   example alone (rule 7), and that a VPS config.yaml keeps working.

2. Stop check (the core). One function, run at the end of every reconcile
   (startup, cycle, position check) after the repairs:
   - per open position, from that pass's Alpaca positions and open orders:
     protected = stop quantity (bracket stop leg, OCO stop, standalone
     stop; status new, accepted, held or partially filled) ≥ shares held,
     or stop quantity + in-flight exit quantity ≥ shares held, where an
     in-flight exit is a market sell accepted < exit_in_flight_seconds ago
     during the session, or a market-on-open sell queued outside it until
     2 minutes after the open. A pending (held-back) exit is not
     protection.
   - unprotected → run the protective repair once more for that position
     (sized from Alpaca's free shares, as 043 does), refetch, check again.
     Still unprotected → open a stop-missing episode: an events row,
     event_type stop_missing, level critical, details {symbol, shares,
     stop_qty, reason, started_at}, resolved_at null. One open episode per
     position.
   - while open: log ERROR at every later pass (CRITICAL once at the
     start); never INFO.
   - closes only when a pass finds the position protected or flat at
     Alpaca: resolved_at = that pass, resolution "condition", INFO log
     "KO stop confirmed at 21:46:05 after 6 h 0 min", info notification
     "stop restored".
   Tests, one per case, with the fake broker: bracket intact; stop leg
   gone (repair succeeds → no episode); repair refused → episode opens;
   the KO case (21 held, sell 14 filled, 7 left, stop refused for 21) →
   episode, then a later pass with a stop for 7 closes it; market sell in
   flight 30 s → protected, 90 s → not; MOO sell queued at 21:00 →
   protected until 15:32 NY next day; pending exit with its stop standing
   → protected, without → episode; partial stop (5 of 7) → episode; flat
   at Alpaca → closes; a restart with an open episode keeps it open.

3. Every skip has a reason. In the protective repair, every branch that
   sends no stop (covered by fills, in-flight sell, shares held by orders,
   flat) logs WARNING with the reason and the quantities, and returns the
   reason so the stop check can store it. Grep for protective_skip and
   every early return in the repair path.

4. State errors resolve by state. apply_auto_resolution skips event types
   stop_missing, protective_stop_failed, pending_exit, reconciliation,
   stream_degraded, live_decisions_failing and api_key: they resolve only
   from their own code (the stop check for the first two; existing
   clearing code for the rest; a reconciliation mismatch on
   acknowledgement). A protective_stop_failed resolves when the stop check
   finds that position protected. Dismissing one of these while it is
   open returns 409 with "still true: <state>". Readiness counts hours
   with an open stop_missing episode. Tests for each type: no time
   resolution after 2 hours of quiet; resolution on the condition.

5. Needs you and notifications. attention.py: an open stop_missing
   episode is a critical item, first in the list: "KO has no stop at
   Alpaca since 15:46 · 7 shares · <reason>", button "Open position"; it
   leaves only when the episode closes (acknowledging is not offered).
   EVENT_TYPE_MAP: stop_missing → missing_stop, with "still no stop · 1 h
   30 min" repeated every 30 minutes during the session while open
   (critical, so never held back by quiet hours or the repeat limit, but
   not more often than every 30 minutes for the same episode).

6. GUI. The red "No stop at Alpaca since 15:46 · 1 h 30 min" chip on the
   position card (dashboard and Positions), the position's decision
   drawers (What happened, from 045), and the legs line "stop none" in
   red, as in States.dc.html.

7. Unclean restart. From data/run.lock's last heartbeat: last alive time,
   downtime, and what was missed (position checks and cycles from the
   trading calendar between the last heartbeat and now; outside a session
   "missed nothing"). In the unclean banner, the WARNING log line and the
   restart notification, worded as in States.dc.html.

8. Ops wording. Settings → Server and the Needs you health line show
   "Heartbeat not set" while ops.heartbeat_url is empty. Offsite backup
   and the restore test show "not set up" without credentials and send no
   failure notification.

9. Verdict number check (report first, no fix in this step). Pull the 15
   verdict calls of 4 Nov 06:12 with number_check_failed: the model text,
   the facts, and which number the check rejected and why. Add the
   findings to docs/analysis/050-live-data-2026-11-04.md under
   "Verdict number check" and say whether the check, the facts or the
   prompt is wrong. If the fix is in code, make it a separate commit
   with a test from one of the 15 real texts; if it needs a prompt change,
   stop and ask (verdict.v2 is used and immutable).

Done when:
- the KO replay test (step 2) opens an episode at 15:46:34, keeps it
  through a restart, shows it in Needs you and notifies every 30 minutes,
  and closes it when the stop for 7 shares exists;
- no state error type resolves by quiet time;
- an install with an old config.yaml sees every default from the example;
- make test passes; docs/CHANGES.md has the [050] build entry; the
  kickoff moves to docs/kickoffs/archive/ in the last PR with the row set
  to merged.
```

## For Marc, no code

- Set `ops.heartbeat_url` (Settings → Server). A dead-man's-switch check with about 75 minutes' grace would have caught the outage of 4 November.
- Look at `journalctl -k` or `dmesg` around 4 Nov 18:47 UTC: an out-of-memory kill shows there.

## Not in this item

- A prompt field telling the model the stop is missing (`position.stop_at_broker`): waits for the next decision prompt version.
- A supervisor that restarts the app on a local machine: the VPS has Docker's restart policy; locally the heartbeat alert is the safeguard.
