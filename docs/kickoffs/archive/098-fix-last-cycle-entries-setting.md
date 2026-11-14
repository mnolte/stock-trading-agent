# 098 · fix · Last-cycle entries setting

Kickoff for Devin. Start the session with: "Build docs/kickoffs/098-fix-last-cycle-entries-setting.md."

Found 14 November: item 071 flipped `schedule.last_cycle_no_entries` to `false` in `backend/config.example.yaml`, but the live install still blocks entries in the last cycle. Every row in `settings_versions` (36 versions, back to 18 Oct) stores the key as `true`, and the newest row is applied over the YAML on every start (`load_saved_settings` → `apply_saved` in `app/api/main.py`, `app/config.py`). Saved settings are full-tree snapshots (`_deep_merge(base, cleaned)` in `POST /api/settings`), and the key is not in `EDITABLE` (`app/settings_service.py`), so no save through the app can ever change it — each one round-trips `true`. A local `config.yaml` cannot override it either: saved settings merge on top of the YAML layer.

The Schedule card already shows the result honestly: the last row reads "no" for entries (`schedule_table` in `app/settings_service.py`).

```text
[098] Last-cycle entries setting. Goal: the last-cycle entries rule is
changeable in Settings like every other schedule knob, and the stuck
value can be fixed through the app. One small PR. Start from the latest
main.

Read first: AGENTS.md; docs/SPEC.md → Settings and Rules pre-filter;
app/settings_service.py (EDITABLE, DIRECTION, schedule_table);
app/api/routes.py → POST /api/settings; item 071 in docs/CHANGES.md.

Move this kickoff to docs/kickoffs/archive/ with git mv in this PR.

1. Make schedule.last_cycle_no_entries editable: add it to EDITABLE
   under "schedule" as a bool, with its label and DIRECTION entry
   ("off" — blocking entries is the tighter side). It needs "Confirm
   it is you" when a save loosens it, like every other loosening
   (item 082); check confirm_fields picks it up from DIRECTION.
2. Surface it in the GUI: the Schedule card's last row already prints
   "no" / "yes, sent by 15:50" — turn that into the control the design
   shows for editable schedule values (design/screens/Settings.dc.html;
   if the design has no control for it, ask in the PR rather than
   inventing one).
3. Open spec question — ask Marc in the PR before building the fix:
   for config keys the GUI does not expose, should a saved settings
   version keep overriding the YAML default forever (today's
   behaviour), or should non-editable keys follow the YAML default on
   each save? Item 071 is the case that exposed it: the default flip
   never reached installs with a saved version. Do not pick a rule
   unilaterally — the answer changes what step 1 alone can fix.
4. Tests (no network): POST /api/settings accepts the bool and rejects
   non-bool; the schedule payload reflects the saved value; flipping
   the flag off (entries allowed) asks for "Confirm it is you" and
   flipping it on does not — DIRECTIONS already maps the key "off";
   the pre-filter still honours the key both ways (test_rules.py
   already covers both — keep them).

Rules: no network in tests; thresholds stay in config; every loosening
still asks "Confirm it is you".

Done when: Marc can open Settings → Schedule, see the last-cycle
entries switch, flip it off (loosen → confirm), and the next cycle's
pre-filter lets the 15:46 cycle enter; the Schedule row then reads
"yes, sent by 15:50". make test passes in CI.
```

## Why

A default change in `config.example.yaml` does not reach an install that has saved settings: the saved tree wins key by key, and keys the GUI never shows are frozen at whatever value the oldest active version carried. `last_cycle_no_entries` has been `true` in every saved version since 18 Oct, so item 071's flip to `false` never took effect — the agent still makes exits only in the last cycle, and there is no in-app way to change it.
