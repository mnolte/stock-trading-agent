# Replay 20261026T131116Z

prompt `decision.v2` · model `gpt-4o-mini` · 2026-10-18 → 2026-10-26
estimated $0.35 · actual $0.4688

## Statuses

- replayed: 128 (calls 128 · no_options 0)
- replayed_approx: 719 (calls 719 · no_options 0)
- not_replayable: 45 — price_unavailable 45
- total snapshots: 892

## decision prompt

- snapshots: 128 · candidates: 125 · calls: 128
- enter rate (candidates): 0% (0 of 125 replayed candidates)
- invalid choices: 0 (0% of 128 calls)
- explanation failures: 0 of 128 calls
- agreement with v1: 128 of 128 calls
- choices by stop/target basis: —
- first touch: stop 0, target 0, neither 0 (of 0 entered)
- mean R: —

## decision prompt — approximate reference

Reference = min(stored price, last closed 5m bar close); the plausible ask isn't stored for these snapshots. Counted separately, never in the rates above.

- snapshots: 719 · candidates: 717 · calls: 719
- enter rate (candidates): 8% (60 of 717 replayed_approx candidates)
- invalid choices: 0 (0% of 719 calls)
- explanation failures: 0 of 719 calls
- agreement with v1: 81 of 719 calls
- choices by stop/target basis: atr/level: 14, atr/r: 23, level/level: 8, level/r: 17
- first touch: stop 40, target 0, neither 19 (of 60 entered)
- mean R over the next sessions: -0.65 (n=59)

## Live entries

- #312 WMT · 2026-10-22 15:46 UTC · v1 enter · replayed_approx · offered A,B,C,D · model said skip

## rules-only baseline

- snapshots: 892 · candidates: 887
- enter rate (candidates): 100% (887 of 887 candidates)
- first touch: stop 287, target 35, neither 482
- mean R over the next sessions: -0.31 (n=804)
