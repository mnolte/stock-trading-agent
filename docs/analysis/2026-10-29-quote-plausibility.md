# IEX quote plausibility — 2026-10-29

Run 1: 6414 decision snapshots (decisions + shadow_decisions).

## 1. Plausible quotes

- implausible: 6109 of 6414 (95.2%)

- per cycle time (UTC): 14:46 1126/1204 (93.5%); 15:46 974/1030 (94.6%); 16:46 842/894 (94.2%); 17:46 831/884 (94.0%); 18:46 1138/1201 (94.8%); 19:46 1198/1201 (99.8%)
- per symbol (top 20 implausible): QQQ 107/107 (100.0%); NVDA 107/107 (100.0%); AMZN 107/107 (100.0%); GOOGL 107/107 (100.0%); META 107/107 (100.0%); JPM 107/107 (100.0%); MSFT 105/106 (99.1%); XOM 99/104 (95.2%); SPY 98/104 (94.2%); TSLA 97/104 (93.3%); AAPL 93/104 (89.4%); ACN 91/93 (97.8%); COST 89/94 (94.7%); BKNG 86/86 (100.0%); MRK 86/86 (100.0%); WFC 86/86 (100.0%); IWM 86/86 (100.0%); GS 86/86 (100.0%); BA 86/86 (100.0%); PLD 86/86 (100.0%)
- per rank band: 1–10 224/314 (71.3%); 11–30 504/643 (78.4%); 31+ 5381/5457 (98.6%)

## 2. Why implausible

- failures: bid missing ×5788; ask missing ×5788; timestamp missing ×5788; ask away ×132; bid away ×128; other ×116
- distance distribution (all quote legs vs last): median 0.241%, p90 5.285%, p99 6.025%

## 3. Spreads and the 0.15% gate

- spread on plausible quotes: median 0.050%, p90 0.477%, p99 1.039%
- entries blocked by the spread gate: 151

## 4. Entries with spread_unknown

- entries: 28, with spread_unknown: 2 (7.1%)
- entry slippage vs last trade: unknown-spread median 0.160%, p90 0.160%, p99 0.160% (n=2, in %); plausible median 0.019%, p90 0.045%, p99 0.045% (n=4)

## 5. price_stale / price_suspect blocks

- total: 81 (1.3% of candidate checks)
- per day: 2026-10-26 ×27; 2026-10-27 ×30; 2026-10-28 ×8; 2026-10-29 ×16
- per symbol: UNP ×11; LOW ×10; HON ×8; LIN ×7; IBM ×5; DIA ×5; ABBV ×4; COP ×3; AMT ×3; SHW ×2
- last-trade age when blocked: median 131s, p90 394s

## 6. 1-minute bar range

- skipped: the database stores 5-minute bars only; a 1-minute
  range needs a new data source and was out of scope.

## 7. Conclusion

- 95% of snapshots had an implausible quote; on those the 0.15% spread gate cannot run (151 blocks recorded).
- Recommendation: keep the gate but treat implausible quotes as a spread_unknown warning only for ranks 1–10 where the failure rate is lowest; the alternative (drop the gate) loses the one real catch it has, and a bar-range replacement needs new data.
