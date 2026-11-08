# 052 · fix · Symbol-keyed config keys

Kickoff for Devin. Start the session with: "Build docs/kickoffs/052-fix-symbol-config-keys.md."

```text
[052] Symbol-keyed config keys. From the live data of 8 November: the
saved settings list 15 symbols but config.example.yaml's
universe.sectors knows only the original 10, so _filter_known drops the
sector entries for TSLA, ACN, WMT, COST and COP at every config layer.
resolve_sectors then returns no sector for them and the sector_unknown
gate blocks every entry — the symbols can never trade, and no config
layer (example, config.yaml, saved settings) can fix it because all
three are filtered against the example. universe.names has the same
latent problem (display only). models is already open-ended for exactly
this reason; symbol-keyed maps must be too. Start from the latest main;
the spec PR of this item is merged first.

Read first:
1. docs/SPEC.md, Config layers (as changed by this item's spec PR).
2. backend/app/config.py — _filter_known and _leaf_paths.
3. backend/app/risk/gates.py — resolve_sectors and the sector_unknown
   block.
4. docs/CHANGES.md entry for [052].

Change, one commit:

- _filter_known keeps the contents of open-ended maps: models (as
  today), universe.sectors and universe.names. Implement it as an
  explicit set of open leaf paths, not a wildcard — every other dict
  stays strict so a retired key still warns once and is ignored.
- A symbol key under universe.sectors/universe.names that the example
  doesn't have is kept silently (it is user data, like a model id);
  it must not count toward the "n unknown keys" figure on Settings →
  Server.

Tests (backend/tests, sockets blocked, fake config trees):
- pass: a settings tree with universe.sectors.TSLA resolves TSLA's
  sector through AppConfig/effective() and the sector_unknown gate does
  not fire for it;
- pass: universe.names for a symbol the example lacks is kept;
- fail: a genuinely unknown key (e.g. risk.pdt_max_day_trades or
  universe.sectors nested deeper than one level) is still dropped and
  warned once;
- fail: universe.symbols stays a plain list — an unknown element is not
  filtered (lists are already kept whole; assert it stays that way).

Notes:
- No restart during 09:30–16:00 New York (hard rule 14); this is a
  config-layer change, safe any other time.
- The saved settings version 27 (8 Nov) already dropped the retired
  keys; after this builds, the five sector warnings disappear too.
```
