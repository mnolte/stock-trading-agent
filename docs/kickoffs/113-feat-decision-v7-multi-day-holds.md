# 113 · feat · Decision v7: multi-day holds

Kickoff for Devin. Start the session with: "Build docs/kickoffs/113-feat-decision-v7-multi-day-holds.md."

A new prompt version, decision.v7, a copy of v6. It treats a trade as a multi-day hold of up to 5 sessions and stops turning the time left in the session and the usual overnight gap into reasons to skip or exit. Its snapshot carries the stop Alpaca holds. Spec: `docs/SPEC.md` → **Decision v7: multi-day holds** (new), Decision v6, Decision v5 (exit consistency). Design: `design/screens/Settings.dc.html#gates` (Decision v7 → Holding period), `design/screenshots/Settings-gates.png` and `Settings-gates-390.png`. Item file: `docs/changes/113-feat-decision-v7-multi-day-holds.md`. Issue: #339. The market context planned as v7 (item 061) is now decision.v8 and is not part of this item.

```text
[113] feat: Decision v7: multi-day holds. Goal: a new prompt version
decision.v7 (a copy of decision.v6 with the changes below), snapshot
fields hold_sessions, sessions_left and Alpaca's stop, v7 exit
consistency without the overnight line, the decision.v7.hold_sessions
setting, long_target_minutes removed, and eval cases. Live is not
switched: Marc runs a shadow "I · v7" first.
Start from the latest main. One PR: "[113] feat: Decision v7: multi-day
holds", "Closes #339". Move this kickoff to docs/kickoffs/archive/ with
git mv in that PR. Set status: merged in
docs/changes/113-feat-decision-v7-multi-day-holds.md with a dated
"build PR #n" section.

Read first:
1. AGENTS.md (Commands: CI runs the tests; hard rule: a used prompt is
   never edited) and docs/README.md.
2. docs/SPEC.md → Decision v7: multi-day holds; Decision v6; Decision v5
   (snapshot, exit consistency); Alpaca integration → Stop check.
3. backend/prompts/decision.v6.md and manifest.json;
   backend/app/decide/prompt.py (prompt_spec), v5.py (V5Config,
   build_v5_fields), v4.py (sessions_held), output.py (exit consistency
   around line 1930, sideways_rule around 2125 and 2352);
   backend/app/engine/executor.py (_stop_check: open_stops per cycle);
   backend/app/replay.py (line 964), shadows/strategy.py (line 137),
   settings_service.py (EDITABLE decision.v5/v6, DIRECTIONS),
   eval_decision.py; config.example.yaml (decision.v5, decision.v6).

Steps:
1. Prompt. Copy backend/prompts/decision.v6.md to decision.v7.md and
   make exactly these changes; add its line to manifest.json. Never
   touch v1–v6.
   a. First line:
      <!-- prompt: decision, version: v7, created: <build date>. As v6,
      plus: a trade is meant to reach its target within hold_sessions
      sessions and is held overnight as needed; the time left in the
      session is no conflict; the usual overnight gap is not an exit
      reason by itself; position.stop is the stop Alpaca holds. Never
      edit a version that has been used; copy it to decision.v8.md
      instead. -->
   b. After the paragraph that starts "Code has already done the
      arithmetic", insert:
      ## How long a trade lasts

      - A trade is meant to reach its target within
        `constraints.hold_sessions` sessions. It is held overnight as
        often as needed in that time; that is how this strategy works.
      - The time left in today's session is no reason to skip an entry
        or to exit a position. A target today's session can't reach is
        normal.
      - What matters is what falls inside the hold: earnings
        (`checks.earnings`; code sells before them, see
        `position.scheduled_exit`) and, for a held position,
        `position.sessions_left`.
      - At `position.sessions_left` 0 the trade has had its time. Hold
        only when the target is near and the checks still agree;
        otherwise exiting is reasonable.
   c. Replace the whole "- **Time left.** …" bullet under "How to judge
      a candidate" with:
      - **Holding period.** Choose a target the trade can reasonably
        reach within `constraints.hold_sessions` sessions. A far target
        behind several strong levels may need longer; prefer the nearer
        one then. Never list the time left in today's session as a
        conflict.
   d. Under "Hold or exit is a numbers decision", replace
      "Otherwise: `hold`, except overnight (below)." with
      "Otherwise: `hold`."
   e. Replace the "- **Overnight.**" bullet and its three sub-bullets
      with:
      - **Overnight.**
        - When `session.last_cycle` is true, holding means holding
          overnight. That is normal for this strategy.
        - `overnight.gap_p90_atr` is how large the biggest overnight
          gaps in this stock usually are, in ATR. If
          `position.stop_distance_atr` is smaller, a gap can open below
          the stop and the loss can exceed 1R. That is the known cost
          of holding for days, not a reason to exit by itself.
        - List it as a conflict only with a concrete reason the next
          gap could be larger than usual: earnings before the next open
          without a `position.scheduled_exit`, or a `new` headline about
          the company. Then weigh it like any other conflict.
   f. Under "How to judge a held position", after the bullet about
      `position.price_vs_signal`, add:
      - `position.stop` is the stop Alpaca holds now
        (`position.stop_from` `alpaca`). When the agent's own record
        differs, `position.stop_stored` shows it; reason from
        `position.stop`.
   g. In "## Input":
      - `session` line: "`session`: `minutes_left` until the close, for
        information only, and `last_cycle` (true in the session's last
        cycle).";
      - `constraints` line: add `hold_sessions` (the sessions a trade
        is meant to reach its target in);
      - `position` list: change "entry, quantity, initial stop, current
        stop, target, opened at;" to "entry, quantity, initial stop,
        target, opened at; `stop` (the stop Alpaca holds), `stop_from`
        (`alpaca`, `stored` when Alpaca's stop was unknown this cycle,
        or `simulated` for a shadow) and `stop_stored` when the agent's
        record differs;", and "`held_session_hours` and
        `sessions_held`;" to "`held_session_hours`, `sessions_held` and
        `sessions_left` (hold_sessions − sessions_held, at least 0);".
   Everything else stays word for word as v6.
2. Contract. prompt_spec: "decision.v7" gets the v5/v6 contract
   (DECISION_V4_JSON_SCHEMA, uses_options, builder "v5"). Everywhere
   the code checks ("decision.v5", "decision.v6") include "decision.v7"
   (prompt.py, replay.py, shadows/strategy.py and any other place; list
   them in the PR). sideways_rule applies to v7 as to v6
   (output.py: v6=prompt_version in ("decision.v6", "decision.v7")).
3. Snapshot (v7 only; v5 and v6 snapshots unchanged byte for byte):
   - constraints.hold_sessions = decision.v7.hold_sessions;
   - position.sessions_left = max(0, hold_sessions − sessions_held);
   - position.stop: live positions use the open stop leg's price from
     the cycle's reconcile (open_stops in _stop_check's input) with
     stop_from "alpaca"; when that symbol has no open stop leg or the
     broker read failed this cycle, the stored stop with stop_from
     "stored"; shadows, replays and backtests use their own stop with
     stop_from "simulated";
   - position.stop_stored only when it differs from position.stop by
     more than $0.01;
   - r_now, stop_distance_atr, neutral_probability and the stop options
     are computed from position.stop (use the one shared risk function,
     as today).
4. Exit consistency (output.py around 1930): for v7, hold_below_breakeven
   fires on a hold below neutral − exit_margin and on thesis_holds false
   with confidence above neutral, but not on the overnight line. v5 and
   v6 unchanged.
5. Config and Settings:
   - config.example.yaml: decision.v7: {hold_sessions: 5} with a
     comment ("v7: sessions a trade is meant to reach its target in; a
     guide for the model, not a time exit"); remove
     decision.v5.long_target_minutes (and its V5Config field, loader
     line, DIRECTIONS entry and test).
   - settings_service.py: decision.v7.hold_sessions, int 1–20, no
     direction (like decision.v6.sideways_max_target_atr's section
     handling).
   - Settings → Decision gates: the Decision v7 group with Holding
     period, as in Settings.dc.html#gates and Settings-gates.png.
   - A saved decision.v5.long_target_minutes shows under "Saved values
     without a control" (item 101); check that it does.
   - Startup accepts decision.v7 as the live prompt, a shadow's prompt
     and a backtest's prompt; New shadow and the backtest picker offer
     it.
6. No time exit: nothing sells a position because sessions_left is 0.

Tests (no network, each its own database, FakeClock):
- the prompt file's hash is in manifest.json; v1–v6 hashes unchanged;
- snapshot v7: hold_sessions and sessions_left (0 at and past the
  limit); position.stop from Alpaca's open stop leg, stop_stored when
  they differ by more than one cent and absent when equal; the stored
  fallback when the broker read failed or no stop leg is open;
  "simulated" for a shadow; r_now and stop_distance_atr from
  position.stop; v5 and v6 snapshots unchanged (compare to a stored
  v6 snapshot);
- exit consistency: a v7 last-cycle hold with the stop inside the gap
  and confidence between neutral and neutral + 0.10 → no warning; the
  same for v6 → hold_below_breakeven; a v7 hold below neutral − 0.03 →
  warning;
- sideways_rule applies to a v7 entry;
- settings: hold_sessions 1 and 20 save, 0 and 21 are refused; a saved
  long_target_minutes is listed under saved values without a control;
- eval_decision.py gains the v7 cases from the spec (good candidates
  at 14:46 and 15:46 with a target 3 ATR away; a last-cycle hold with
  the stop inside the gap and no event; the same with earnings before
  the next open or a new headline; sessions_left 0 with a far target and
  mixed checks; Alpaca's stop differing from stop_stored). Run the eval
  against v7 in CI as the v6 cases run; list v6 cases v7 answers
  differently, with the reason.

Screenshots in docs/screenshots/113/: Settings → Decision gates at
1440 and 390 px next to design/screenshots/Settings-gates.png and
Settings-gates-390.png; the decision drawer of a v7 hold showing
sessions left and the stop source.

Rules: never edit a used prompt; Decimal for money; risk math from the
one shared function; no datetime.now() (the clock); thresholds in
config; tests never call a model or Alpaca; nothing written into
design/. Tests are proven in CI: open the PR as a draft early, run
locally only the tests for the changed files and ruff and mypy on them;
never the full suites, e2e or image builds.

Done when: all CI checks are green and the PR merges cleanly; the PR
lists the points of #339 under "Issues closed" with the file or test
for each; the live prompt is unchanged.
```

## Why

Marc, 25 November: "In the model calls I keep reading that there is very little time left or that an overnight hold is unfavorable. While the agent is build to keep symbols for up to 5 days." The cause is in decision.v5 and v6 (see the item file). Marc decided the same day: 5 sessions is a guide for the model, and v7 ships now while the market context moves to v8.

## Assumptions

- The drawer shows the new snapshot fields through its existing snapshot view; no design change is needed for it.
- Comparing the shadow "I · v7" with live for 10 sessions uses the existing Strategies comparison; entries per cycle come from Insights → entry cycles.

## Open questions

None.
