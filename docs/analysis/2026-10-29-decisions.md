# Decision analysis

Read-only analysis by Devin, 29 October 2026 (led to the break-even guard). Kept as reported; findings may since have been fixed (see docs/CHANGES.md).

What ran
6 hourly cycles (10:46–15:46 ET), all done; 15-minute position checks all done.
438 decision rows: 330 no_call (rules pre-filter), 16 no_call (price_stale), 92 real model calls — decision.v2 on gpt-4.1-mini.
Live model calls: 57 skip, 15 blocked, 3 enter, 4 tighten_stop, 13 hold. Zero invalid outputs live; 1 error across all 239 calls (shadow only).
Cost today: $0.228 total ($0.166 live, $0.056 shadow, $0.006 review). Latency avg 2.3 s, max 5.6 s.
Equity ~9,954 → 10,008.82 (+0.55%). Only close: RTX stopped at 200.09, −$0.27 raw / −$2.07 adjusted.
Open into tomorrow: ACN, NKE, HD — all now at break-even stops.
Timeline of real actions
| Time (ET) | Action | Detail |
| --- | --- | --- |
| 10:46 | enter NKE 52 @ 38.27 | option A, stop 37.45 under L14, rr 1.86 |
| 11:46 | enter RTX 9 @ 200.12 | option A, stop 196.21, rr 2.0 |
| 12:46 | tighten RTX → 200.12 | T1 "break-even", model claimed it "locks in profits" |
| 12:46 | enter HD 6 @ 324.89 | option E, stop 317.37 under L16, rr 2.0 |
| 13:46 | tighten ACN → 243.78 | T1 "break-even" at +0.38R |
| 14:47 | RTX stopped @ 200.09 | break-even stop hit ~2h after tightening |
| 14:46 | tighten HD → 324.89 | T1 "break-even" at +0.23R |
| 15:46 | tighten NKE → 38.27 | T1 "break-even" at +0.13R, 14 min before close |

Prompt performance (decision.v2)
Good:

Schema adherence is near-perfect: every live call was valid JSON with legal actions/options. Skips are grounded — they cite real level ids from the input (L1–L8 in the way, obv_trend down, ATR-only stops), and explanation_check never failed (bad_ids/bad_numbers empty everywhere).
Entry picks match the prompt's stated preferences: HD chose E (real swing-low stop) over A (plain 2×ATR) — exactly what the prompt asks for.
hold was the default on positions, as instructed.
Weak — the break-even problem, confirmed live:
All 4 tighten_stop decisions picked the break-even option and wrote "locks in profit" / "locks in the current gains" — at +0.13R (NKE), +0.23R (HD), +0.38R (ACN), +0.40R (RTX). The model was technically permitted to say it, because the input told it locks_profit: true — but r_at_stop was 0.0 in every case. A break-even stop locks in zero; with slippage it locks in a loss. RTX proved it within two hours: a +0.40R open gain became −$2.07 realized. NKE's stop now sits ~0.3 ATR (11¢) under price, tightened at +0.13R — almost certain to be shaken out by noise. SBUX yesterday shows the same pattern (stopped at exactly its 103.15 entry).

This is the exact failure mode SPEC.md already diagnosed (L1438–1453) and decision.v3 fixes: locks_profit only when stop > entry, break-even only offered ≥ +1.0R, plus min-move and min-distance drops. v3 exists in code (v3.py:515 uses >; test_locks_profit_boundaries asserts the fix) but live still runs v2 — so the bug is fully active in production. The v2 prompt line "protects a gain the trade has earned" gives no floor; the model reads any positive R as "earned".

Neutral: the model chose enter 18×; 15 were gate-blocked — spread ×7, fresh_quote ×2, max_open_positions ×5, one earlier spread. TSLA was picked 4 cycles running and never executed (0.74% IEX spread vs 0.15% cap). These blocks are invisible to the model by design; the gates did their job. Your own verdicts today (59 reviewed: 45 good, 14 bad — 13 of them skips that missed gains) show the mirror-image bias: historically too conservative on skips, today eager to enter where quotes couldn't support it.

Bugs / faulty logic found
locks_profit wrong at break-even — options.py:390: stop >= entry should be stop > entry. Labels a zero-R stop as profit-locking; the model quotes the flag verbatim. Fixed in v3 only; still live in v2. This is the one that cost money today.
Broker websocket parse crash — alpaca.py:89 (raw["id"] inside parse_trade_update): at 18:47:34 — the moment RTX's stop filled — the stream raised KeyError: 'id' on a trade update whose order payload lacked id, erroring repeatedly (~4×/sec). The fill was still recorded via polling (position closed at the 19:01 check), but one malformed message breaks the whole stream dispatch. Needs a defensive raw.get("id") / skip-and-log.
Stale/wide IEX quotes — 16 price_stale no-calls + 9 spread/fresh_quote blocks, all on liquid names (UNP, MCD, TMO, HON, IBM, DIA, JNJ, LOW, COP, QCOM, TSLA). Not a code bug, but a systematic data-quality tax: ~23% of would-be model evaluations never happened, and the model's best conviction (TSLA) was unexecutable all day.
Shadow (gpt-4o-mini) is far noisier — same prompt, 87 calls: 118 enters blocked vs only 62 skips. It also produced the day's only invalid output (option_id 'E' on a tighten_stop — correctly caught, forced to hold). If shadows are meant to inform live, gpt-4o-mini is a much looser decision layer.
Ops notes (context, not today's decisions)
Yesterday's three dead cycles (200/204/208, offset-naive vs offset-aware in options.py:367) were fixed same evening (5625edc); no recurrence today.
A restart at 17:24 UTC (13:24 ET, inside the session) deployed dd33ae0, which is why original_invalidation went from unavailable on every held snapshot before 13:46 ET to populated after — and why input_incomplete warnings stop after cycle 227. Rule 14 discourages mid-session restarts; events show it was a manual unpause, so presumably yours.
held status on the three replacement stop orders is normal Alpaca bracket-leg state (RTX's identical order filled fine); broker-side stops are in place on all open positions.
Disk space warnings fire hourly (83% full) — unrelated, but noisy.
Bottom line: the prompt did its job on entries, skips and holds. The one real money-losing behavior — break-even "profit-locking" tightens at trivial R — is a known v2 code/prompt flaw that v3 already solves; nothing today argues against promoting v3, quite the opposite. The actionable new bug is the KeyError: 'id' in the Alpaca stream parser.
