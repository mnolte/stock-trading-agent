# 112 · fix · Room for the cost-per-session chart

## 2026-11-21 · spec PR

- **`Insights.dc.html` → What do the models cost?:** By model and Cost per session no longer share a row. Each is its own full-width card.
- **Cost per session:**
  - The subtitle names the period and the unit: "This month · per session · backtests not included".
  - An answer sentence: "Average $0.51 a session; the highest was $0.75 on Mon 14 Dec, with Ask the largest part."
  - The chart is 160 px high, with gridlines at the top and the half and a dotted average line. Each bar is a button at most 36 px wide in an equal slot.
  - The selected bar (the highest when the card opens) is outlined, and its dates, total and four amounts show in a line under the chart. The legend adds "average".
  - A note explains the bucket rule.
- **Phone (≤ 600 px):** 2 px between bars, and every other label is hidden; the last one always shows.
- **Checks:** no sideways overflow at 390 px. 107's card-edge check passes at 1440, 1024 and 390 px (14 cards, 0 off).
- **Screenshots:** `Insights.png`, `Insights-390.png`.
