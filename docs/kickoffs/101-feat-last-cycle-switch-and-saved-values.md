# 101 · feat · A switch for last-cycle entries; saved values without a control

Kickoff for Devin. Start the session with: "Build docs/kickoffs/101-feat-last-cycle-switch-and-saved-values.md."

Backend (one comparison, one endpoint, one Needs you item) and frontend (Settings → Schedule and History). It answers the two questions of PR #294. No change to what any setting does. Spec: `docs/SPEC.md` → Config layers → **Saved values without a control**; GUI → Settings screen → Schedule; GUI → Needs you (order 7); `docs/CHANGES.md` entry [101]. Design: `design/screens/Settings.dc.html` (Schedule, Settings history) with `Settings-schedule.png` and `Settings-stored.png`; `design/CHANGES.md` entry [101]. Issue: #298.

```text
[101] A switch for last-cycle entries; saved values without a control.
Goal: Marc flips last-cycle entries with one switch in Settings, and no
saved value can differ from the config file again without the app
showing it and offering to take the config value over.
Start from the latest main. One PR. Put "Closes #298" in it.

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md → Config layers (all), GUI → Settings screen, GUI →
   Needs you; docs/CHANGES.md [101] and [098].
3. design/CHANGES.md [101]; Settings.dc.html → Schedule and Settings
   history, with the two screenshots.
4. backend/app/settings_service.py (EDITABLE, LABELS, DIRECTIONS,
   DIRECTION_SECTIONS, _direction_for, schedule_table),
   backend/app/api/routes.py → POST /api/settings and _settings_form,
   backend/app/config.py and app/api/main.py (load_saved_settings,
   apply_saved), backend/app/attention.py.

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR.

1. First, before any code: read the live install's active settings
   version and list every key whose saved value differs from the
   config layers (example + config.yaml), editable or not. Put the
   table in the PR description (key, saved, config, first version that
   holds the saved value). Marc reads it before he merges. Don't change
   any value.

2. The switch (Settings → Schedule).
   - Under the cycle table, the row of the design: label "New entries
     in the last cycle", help text as written, a switch. On means
     schedule.last_cycle_no_entries is false. It joins the unsaved
     changes like any setting ("New entries in the last cycle off → on"
     with its looser tag) and is found by "Find a setting".
   - Turning it on is a loosening: "Confirm it is you" (DIRECTIONS has
     the key as "off"; item 098's tests cover the API side).
   - The table's last "New entries" cell follows the unsaved value:
     "yes, sent by 15:50" (execution.entry_cutoff) or "no".
   - The intro sentence of the design replaces today's.

3. The comparison. settings_service.saved_without_control(config_layers,
   active_version, versions) -> list of
     {key, label, saved, config, since_version, since_at, direction}
   - a leaf key is listed when its saved value differs from the config
     layers' value and the key has no control (see step 5 for what
     "has a control" means);
   - left out: open-ended maps (models, universe.sectors,
     universe.names), secrets, and keys the config layers no longer
     have (list those separately in the WARNING of step 6, don't show
     them);
   - numbers compare by value (10 == 10.0, Decimal), not by text;
   - since_version is the oldest version in the unbroken run of
     versions, ending at the active one, that hold the saved value;
   - label from LABELS, else the key itself.
   Computed at start and after every save, kept in memory, served with
   GET /api/settings as `saved_without_control`.

4. Use the config value. POST /api/settings/use-config
   {key, reason, confirm?}:
   - only for a key in the current list (anything else: 422);
   - writes a new settings version equal to the active one with that
     key set to the config layers' value; reason required; the same
     audit line, history row and WebSocket topic as a normal save;
   - "Confirm it is you" when the change is looser, or when
     _direction_for(key) is None (unknown counts as looser, item 082);
   - a key that needs a restart to apply says so, as a normal save does.
   This endpoint is the one place a key outside EDITABLE can change,
   and only to the config value.

5. Every changeable setting has a control. A test builds the set of
   EDITABLE leaf keys and the set of keys the Settings page renders a
   control for (export the frontend's key list as JSON at build time,
   or keep one shared list both sides read; say which in the PR) and
   fails on any EDITABLE key without a control. Known API-only keys
   today (schedule.cycles, if it is one) get a control or leave
   EDITABLE; list each decision in the PR and ask if unsure.

6. Showing it.
   - Settings → History: the amber block of the design on top, one row
     per listed key, "Use the config value" opens the reason dialog
     (prefilled "Take over the config value of <key>"). With an empty
     list the block is not shown.
   - The section list shows the count on History ("History · 2").
   - Start: one WARNING line "saved values differ from config: <keys>"
     (names only, no values), once per start.
   - Needs you, order 7: "2 saved values differ from the config file"
     with Open Settings (/settings/history). It leaves when the list is
     empty.

7. Tests (no network, each its own database):
   - the comparison: a differing key without a control is listed; with
     a control it isn't; equal values aren't; 10 vs 10.0 isn't; an
     open-ended map and a secret aren't; since_version over three
     versions where the value changed once;
   - use-config: writes a version with only that key changed; unknown
     key 422; missing reason 422; a looser or directionless key asks to
     confirm; a tighter one doesn't;
   - a regression for item 098's case: a saved version with
     schedule.last_cycle_no_entries true against a config of false and
     the control removed from the key list → listed; with the control
     → not listed;
   - Needs you shows and clears the item;
   - step 5's test;
   - frontend: the switch joins unsaved changes and the last row
     follows it; the block renders and hides.

8. Screenshots in docs/screenshots/101/: Schedule at 1440 and 390 with
   the switch on and off; History with the block; the confirm dialog
   for turning the switch on.

Rules: no network in tests; no value in a log line (names only); the
comparison never changes a setting by itself; nothing in design/.
Tests are proven in CI: open the PR as a draft early, run locally only
the test files you change.

Done when: all CI checks are green and the PR merges cleanly; Marc can
open Settings → Schedule, flip the switch, confirm, save, and the next
last cycle may enter; the PR holds step 1's table and step 5's
decisions; "Issues closed" lists the six points of #298 with the file
or test for each.
```

## Why

Item 098 found that `schedule.last_cycle_no_entries` was `true` in all 36 saved settings versions since 18 Oct, so item 071's default never reached the live install: a saved version stores every setting and wins over the config file, and this key had no control. PR #294 made the key changeable through the API and asked two questions. Marc's answers, 14 Nov:

- **A control?** Yes: "I want a button I can flip."
- **Hidden keys?** Saved values keep winning, but every one that differs from the config file and has no control is listed, with a way to take the config value over.

## Assumptions

- The list can be long on the first open: every setting without a control whose default changed since the oldest saved version. Step 1 shows it before anything is merged.
- "Use the config value" may change a setting that has no other control. That is on purpose: without it the saved value can never follow the config file.
- A setting with no entry in the direction map counts as looser, so it asks to confirm.

## Open questions for Marc

None blocking. Decided without asking: the list sits in Settings → History, because that is where saved versions live; the Needs you item sits at order 7, with the other settings that wait.
