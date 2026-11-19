# 107 · fix · Dashboard cards line up

## 2026-11-19 · spec PR

- **`Main.dc.html`:** the six jump targets (`positions`, `chart`, `decision`, `today`, `briefing`, `watchlist`) move from empty `<span>`s onto their cards. At 1440 px every row now starts at 256 px and ends at 1416 px with 20 px between cards (three rows started at 276 px; two had 40 px gaps). At 1024 px the price chart and Today's decisions reach the right edge again.
- **`Main.dc.html`:** the rows Open positions | Risk budget, Price chart | Latest decision and Today's decisions | Daily briefing carry the class `dash-pair`: from 1200 px a 3 : 2 grid, so all three split at the same line (at 1440 px: 256–940 | 960–1416); below 1200 px each card takes the full width. Rules in the file's second `<style>` block.
- **`Performance.dc.html`, `Insights.dc.html`:** the same move of the jump targets (five each); in their columns the spans added an extra 20 px gap above each target.
- **`DESIGN.md` → Rules → On this page:** a jump target is the card's own `id`, never an empty element inside a row or column.
- Screenshots: `Main.png`, `Main-1024.png`, `Main-390.png`, `Performance.png`, `Performance-390.png`, `Insights.png`, `Insights-390.png`. Each was re-rendered from its screen file; a check of every card row's edges and gaps passes at 1440, 1024 and 390 px on all three screens.
