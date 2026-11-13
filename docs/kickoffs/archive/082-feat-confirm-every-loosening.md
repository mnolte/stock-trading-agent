# 082 · feat · Confirm every loosening

Kickoff for Devin. Start the session with: "Build docs/kickoffs/082-feat-confirm-every-loosening.md."

Backend and a small frontend change. No trading rule or default changes. Spec: `docs/SPEC.md` → Security hardening → Audit of 11 November 2026 → **Every loosening asks "Confirm it is you"**; Settings screen (the tighter/looser tags of item 069). Report: `docs/analysis/080-security-audit.md` (S2). Design: `design/screens/Settings.dc.html` → Access → "Confirm before risky actions" (`Settings-access.png`). Issue: #239.

```text
[082] Confirm every loosening. Goal: a signed-in session can't make the
agent riskier or quieter without "Confirm it is you": the direction of
every trading setting is defined once, in the backend, and every looser
change asks.
Start from the latest main (after 069's second PR if it is merged; if
not, say so and coordinate the direction map with it).

Read first:
1. AGENTS.md and docs/README.md.
2. docs/SPEC.md → Security hardening → Audit of 11 November 2026 →
   Every loosening…; GUI → Settings screen (069); docs/CHANGES.md [080].
3. backend/app/api/routes.py (update_settings, _settings_loosen_risk),
   app/settings_service.py (RANGES, LABELS), frontend settingsDiff.ts
   and the direction map of 069.

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR.
Put "Closes #239" in the PR description.

1. settings_service.DIRECTIONS: one entry per leaf setting in risk,
   execution, rules, regime, events, decision (all versions' gates),
   selection, schedule and universe:
     "up" | "down"       looser when the number goes up / down
     "on" | "off"        looser when switched on / off
     "wider"             a range: looser when it widens
     "longer"|"shorter"  a list: looser when it grows / shrinks
     "neutral"           with a comment saying why
   Examples: risk.risk_per_trade_pct up; risk.earnings_blackout_days
   down; risk.hold_through_earnings on; execution.max_spread_pct up;
   decision.min_confidence_enter down; decision.min_reward_risk down;
   decision.stop_atr_max up; regime.block_entries_in shorter;
   rules.rsi_min..rsi_max wider; the trend gate's regimes longer; the
   FOMC window off; schedule.last_cycle_no_entries off.
   classify(old_tree, new_tree) -> list of {key, old, new, direction:
   "looser"|"tighter"|"neutral"}; a key in those sections without an
   entry is "looser".

2. update_settings: replace _settings_loosen_risk. Confirm is required
   when classify returns any looser change, or:
   - execution.mode supervised -> automatic;
   - a notifications.events entry loses a channel, or quiet hours get
     longer;
   - universe.mode fixed -> screened;
   - decision.model or decision.prompt changes (live).
   The 403 confirm_required answer carries `fields`: the changes that
   need it (key, label, old, new). Promote-to-live and the heartbeat
   rule stay as they are.

3. API: GET /api/settings returns `directions` (key -> direction). The
   frontend's own map (069) goes; the save bar's tighter/looser tags and
   the looser-save dialog read the API's. The confirm dialog lists the
   changes from `fields`.

4. Tests:
   - every leaf key in those sections of config.example.yaml has a
     DIRECTIONS entry (fails closed otherwise);
   - one test per direction kind, both ways (looser asks, tighter
     doesn't);
   - supervised -> automatic asks; automatic -> supervised doesn't;
   - removing "email" from kill_switch asks; adding it doesn't;
   - SA_AUTH=off: nothing asks (unchanged);
   - a mixed save lists only the looser changes in `fields`.

5. Settings → Access: the "Confirm before risky actions" text as the
   design.

Rules: no network in tests; directions live in the backend only; no
default or range changes.

Done when: the tests pass; with SA_AUTH=required, lowering
decision.min_confidence_enter through the API without a fresh
confirmation answers 403 confirm_required; make test and the e2e pass.
```

## Why

Audit of 11 November, S2: `_settings_loosen_risk` asks only when a number under `risk` goes up. A stolen session could switch supervised to automatic, lower the confidence gate, empty the regime block and turn off the kill-switch email without the password or a code. Item 069 added tighter/looser tags, but the map lives in the frontend, where it protects nothing.

## Assumptions

- Changing the live model or prompt counts as a risky change: it changes every decision.
- A setting with no clear direction is marked "neutral" by hand, with a comment; the default for an unmapped key is "looser".

## Open questions for Marc

None. Decided without asking: fixed → screened universe asks (more symbols can be traded); longer quiet hours ask (alerts arrive later).
