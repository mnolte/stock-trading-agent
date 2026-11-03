# Decision v4 review: the prompt and the KO call of 3 November

By Claude, 3 November 2026. Two parts: a review of `backend/prompts/decision.v4.md` with the code that builds its input and checks its answer (`decide/v4.py`, `decide/output.py`), and one live call: KO held, 3 Nov 15:46 NY (21:46 Amsterdam), the session's last cycle. The goal is the most profit for the least loss. The fixes are in the kickoffs `fix-v4-2026-11-03.md` (code) and `pr-37.md` (decision.v5, the expected-value gate, overnight facts).

## The KO call in short

| Fact | Value |
| --- | --- |
| Position | 21 sh, entry 95.37 at 13:46 NY, stop 94.22 (risk 1.15), target 97.84 (+2.15R) |
| Now | 94.66, r_now −0.62R, stop 0.83 ATR below, target 6.07 ATR above, 14 minutes to the close |
| Neutral probability | 0.12 (the no-edge chance of the target before the stop) |
| Since 14:46 | price −0.92 ATR, checks unchanged, one new headline (Coca-Cola India can launch) |
| Market | regime sideways, volatility high (ATR percentile 92); KO's gap today −0.99% ≈ 1.8 ATR(1h) |
| Model | hold, confidence 0.65; conflict "levels L8, L6, L5 within 1 ATR" outweighed by "strong momentum and thesis holds"; thesis "the trade has room" |

### What went wrong

1. **Confidence 0.65 at a neutral probability of 0.12.**
   - That's 0.53 above the anchor, against the prompt's "more than 0.30 above it is almost never right".
   - At 0.65 the model believes the target (6 ATR away, 7 levels in the way, 14 minutes left) comes before a stop 0.83 ATR away nearly two times in three.
   - The `confidence_outlier` warning records it, and nothing acts on it.
2. **Holding overnight was the real decision, and nothing in the input or the prompt said so.**
   - In the last cycle, hold means an overnight hold.
   - The stop is 0.83 ATR away, while KO gapped about 1.8 ATR today. A gap below the stop fills below it, so the loss can exceed 1R.
   - Holding at the honest odds is worth about the same as selling. Expected R of holding at p = 0.15 is 0.15 × 2.15 − 0.85 × 1 = −0.53R, against −0.62R for selling now. The gap risk tips that toward selling.
3. **The entry broke the prompt's own trend rule.**
   - At entry the regime trend was `sideways` (`checks_at_entry`) and volatility high. The prompt says to enter only with the regime up, and that with high volatility every check must agree.
   - The entry thesis even says "although level L2 is within 1 ATR … balanced risk-reward", wording v4 bans.
   - Rules the model must follow and can break belong in code.
   - The entry was also 2 h 14 min before the close, with a target 4.7 ATR away, so an overnight hold was likely from the start.
4. **"The trade has room"** with a level 0.26 ATR above the price (L8). The path-claim check only knows "clear/open/easy/manageable … path/way/room", so "has room" passes.
5. **`thesis_holds: true` while the price fell 0.92 ATR in an hour.**
   - The checks are made on the last completed 1-hour bar (close 94.91). The price is now 94.66, below the 1-hour EMA 20 (94.71) and VWAP (94.81).
   - The facts lag by up to an hour, and nothing in the input shows it.
6. **The model's conflict reasoning is circular.** "Thesis holds" is code's summary of the same checks, not an independent fact, and it doesn't outweigh levels in the way.

### Bugs in the input

7. **The inspector shows the wrong input.** `llm_calls.input_snapshot` holds the stored snapshot, but the model was sent the stripped copy (`v4_send_snapshot`). So "User · input snapshot" shows fields the model never saw:
   - `indicators_precise` with 28 decimals;
   - quote provenance and `stop_reference_price`;
   - L16, which the level cap removed.

   The audit view must show what was sent.
8. **`about_symbol` false for "Coca-Cola India Launches…".** `universe.names` lists only the 10 fixed symbols. The screened pool (72 symbols, KO among them) has no names, so `about_symbol` can only match the ticker.
9. **Earnings all null.** `checks.earnings` has days, date and time all null, and `days_to_earnings` is null. The model can't tell "no report in the window" from "calendar unavailable", which the prompt says to treat differently.
10. **Two closes for one bar time.** `signal_bar.close` is 94.91 (the 1-hour bar), while the stored `last_bar_close` is 94.895 with the same time (a 5-minute bar).

## The prompt review (v4)

11. **A fixed confidence floor fights the prompt.**
    - Code needs confidence ≥ max(0.55, break-even + 0.10). The prompt says to start at break-even (0.33 at 2R) and that 0.6 or more should be rare.
    - An honest model rarely passes, so it skips real edges or inflates, which is the 0.6–0.7 clustering measured on 31 October.
    - The floor also demands +1.2R expected on a 3R trade and only +0.4R on a 1.5R trade.
    - Fix: an expected-value bar per option, `min_confidence` = (1 + `min_ev_r`) ÷ (1 + rr), with `min_ev_r` 0.25. That gives 0.50 at 1.5R, 0.42 at 2R and 0.32 at 3R (rounded up).
12. **No hold-or-exit rule.** `neutral_probability` is exactly the confidence at which holding equals selling. v4 never says so, and there were 0 exits in 233 held calls. Fix: exit below neutral − 0.03; overnight, hold only from neutral + 0.10 when the stop is within a typical gap.
13. **"Missing a trade costs nothing"** is wrong when maximising profit: 29% of skips had an option that reached its target first. Fix: "a skip is not free; enter when you can name an edge and clear the bar".
14. **Far targets.** The model's only measurably bad option choices were reaching for the farther target. Entry selection (PR 33) ranks on expected profit, which rewards a high `rr` if confidence isn't lowered with it. Fix: say so, and give `target_atr` per option.
15. **Untrusted text.** Headlines and the entry thesis are marked as data; the model's own earlier conflicts and reasons in `previous_decision` are not. Fix: all model-written fields are data.
16. **Data flags on held positions** had no rule. Fix: hold, never tighten.

## Bugs in the v4 code

17. `thesis_holds` treats only up → mixed or down as adverse for the regime. The regime uses `sideways`, so up → sideways and sideways → down never count (`_ADVERSE`).
18. `previous_decision` can come from the other context. After an entry fills, the first held decision is told its action "changed" from enter.
19. `previous_decision.action` is the model's action, not the outcome. An enter that code blocked looks like a position the model holds. It needs the final action and the reason.
20. The 12-headline cap is newest-first regardless of `about_symbol`, so market noise can push the company's own news out.
21. `stop_group` keys on the stop's text form (101.70 and 101.7 would be two groups).

## What worked

- The checks by code, the level merge, `levels_to_target` with distances, and `neutral_probability` (0.12, correct).
- The stripped send copy (the model didn't get the 28-decimal values).
- `since` (−0.92 ATR, measured right).
- Empty `stop_options` at a loss (no break-even tighten at −0.62R).
- The model didn't claim the stop locks in profit.
