# Dashboard UX/UI review

Read-only analysis by Devin, 24 October 2026. Kept as reported; findings may since have been fixed (see docs/CHANGES.md).

Context
The dashboard is faithful to the design in overall structure: cycle strip → stat tiles → briefing + risk budget → chart + latest decision → watchlist → positions → feed. The implementation deviates from the design in a few places (several look deliberate), and — more importantly — the current build has some issues that only appear at the real data scale it's now running with (71-symbol screened pool) and in the non-session states it's in most of the time (closed, weekend).

A. Issues at real-world scale (highest impact)
1. The chart card renders a pill button for every pool symbol — 71 right now.
Dashboard.tsx:628 maps watchlist.rows to buttons; in screened mode /api/watchlist returns all 71 pool rows. The card currently shows a 6–7-row wall of symbol pills pushing the actual chart down; on phone width it's worse. The design has no pill row at all — you pick a symbol by clicking a watchlist row. Options: drop the pills (design's intent), cap them (held positions + top N by rank), or replace with a compact selector.

2. "Today's decisions" feed doesn't scale.
Dashboard.tsx:38-98 renders one chip + text link per decision, grouped only by minute. Yesterday's session produced ~71+ decisions per cycle × 6 cycles — hundreds of rows, all rendered, no cap or collapse. The design deliberately merges same-outcome items into one line (RULES — SPY, QQQ, NVDA, META, XOM held by pre-filter) and uses plain colored mono text rather than pills. Collapsing identical (action, reason) rows per cycle into SYMBOL, SYMBOL, … · reason would both match the design and fix the volume problem.

3. Feed rows break the 44px control rule and have no affordance.
Each feed item is a <Link> roughly 24–28px tall (Dashboard.tsx:82-92); the design rule says clickable rows are ≥44px (--sa-control-height), which the watchlist does honor. Also, the link sets an inline color, which overrides the global a:hover rule — so hovering a feed row gives zero visual feedback.

B. Loading / empty / error / off-hours states
4. Loading renders as "empty".
While fetches are in flight every card shows its empty state: "No decisions yet today", "No open positions", "No decision yet this session", stats as 0 of — / cash $— (Dashboard.tsx:52-55, 125-131, 282-285, 540-570). First load flashes false information. Needs a tri-state (loading / data / error) — even a muted "Loading…" per card.

5. Fetch failures are silent.
Every dashboard fetch ends in .catch(console.error) (Dashboard.tsx:407-427). If the API is down, the page looks calmly empty. The WS-paused note exists (Layout.tsx:141-151) but only covers the socket, not the initial REST load. Surface failures — a banner or an inline card note — and getChart failures currently masquerade as "No bars loaded".

6. "Today" ends at Amsterdam midnight, not at session end.
routes.py:2880-2898 builds today_decisions from Amsterdam-midnight. Right now (Sat 01:10, ~50 min after Friday's close) the dashboard already shows "No decisions yet today", "No decision yet this session", "Model calls today 0 of 600" — while Friday produced 400+ decisions. Off-hours this dashboard looks dead despite a rich last session. Suggest: when closed and today has no decisions, label the feed "Friday's decisions" and let Latest decision fall back to the last session's decision for the symbol (with its date). That's a behaviour question — worth a spec check per AGENTS.md before implementing.

7. Selecting a symbol with no decision today blanks the card.
routes.py:2932-2939 only searches today's decisions; a symbol that wasn't decided today shows "No decision yet this session" even though the watchlist has its rules status. A "why it wasn't called" fallback (e.g., its pre-filter status) would be more informative.

C. Deviations from the design (list in PR / reconcile)
Cycle counter segments (CycleCounter.tsx:13-35): impl uses 12×6px pills — done = dark --sa-border-strong, next = solid amber fill, later = hollow. Design uses wide bars: done = light #9FB4CC, next = amber outline, later = dark fill. In the impl, done vs. later are hard to distinguish, and the countdown sits inline at body size rather than prominent (Main.dc.html:78-82 puts it in a separated panel with a 15px mono countdown).
Unrealized P&L isn't colored (Dashboard.tsx:327-344): renders --sa-text-2 for any sign; design colors it (green/red). Violates the "colour means something" rule — it's the one number a user scans for.
Missing positions footnote — design has "Every position has a stop and target at Alpaca. …" under the open-positions table (Main.dc.html:224); impl omits it.
Chart misses the "E" agent-entry marker and session-date labels under separators (design has both; Main.dc.html:150,158). Level lines exist but aren't labeled on-chart the same way.
Approval card layout differs: design is horizontal (badge · details · buttons in one row, States.dc.html:79-87); impl is a vertical stack. Works, but differs.
Chart card header: design shows symbol at 18px + price at 24px + change inline (Main.dc.html:140-146); impl demotes symbol to the small uppercase card label and shrinks the price into a secondary row.
D. Spec-rule gaps
NY-on-hover missing in several places: feed times (Dashboard.tsx:76), latest-decision time (:153), watchlist last-action times, "· today" marker. The design rule is Amsterdam shown, New York in a tooltip — formatNyTitle already exists in decisionFormat.ts:76 but isn't used here.
Rank is never displayed on the dashboard — spec §463 says rank "is shown on the Universe screen and the dashboard". The watchlist sorts by it but has no rank column. Worth a spec question (maybe sort order counts, but it's invisible).
Screened-mode hint shown unconditionally (Watchlist.tsx:158-160): "With a screened pool: sorted by rank, 15 rows, then Show all" renders in fixed mode too — misleading there.
Earnings tooltip hardcodes "3 trading days" (Watchlist.tsx:183) while the actual blackout comes from the earningsBlackoutDays prop — a settings change makes the tooltip lie.
E. Accessibility
No :focus-visible styles anywhere in the app; Settings.tsx:64 even sets outline: 'none'. Keyboard users get no focus indication — a real blocker given the dashboard is mostly clickable rows and buttons.
role="status" on the cycle strip (Dashboard.tsx:492) wraps the countdown, which re-renders every second → screen readers announce every tick. Exclude the ticking element from the live region.
role="row" on <button> in the watchlist + a role="tablist" without arrow-key navigation — the ARIA patterns are half-implemented.
Critical toasts auto-dismiss after ~10s like everything else (NotificationToasts.tsx:76-80) — a critical/role="alert" toast (e.g., kill switch, reconciliation mismatch) should persist until dismissed.
Mobile sidebar overlay has no Escape-to-close or focus handling (Sidebar.tsx:323-339).
F. Interaction & polish
No hover feedback on non-anchor controls — inline styles can't express :hover, so watchlist rows, symbol pills, filter tabs, and toast cards give no hover response. A couple of CSS classes in global.css (or onMouseEnter) would fix the whole app.
Chart/symbol race: switching symbols quickly lets two getChart/getDashboard calls resolve out of order — the card label can show symbol A while the chart shows B's bars. Abort or check the latest requested symbol on resolve.
Selected symbol isn't persisted — reload drops back to symbols[0] (rank 1, currently ACN). Consider ?symbol= in the URL (deep-linkable) and/or defaulting to a held position.
Toasts: the e2e screenshot shows two identical "Daily briefing ready" toasts stacked — likely one notification row per channel; dedupe by event. top: 64 also lands on top of the header when it wraps.
Weekend countdown: "in 63:36" is per-spec (h:mm) but reads oddly; "Mon 16:46" with the day is already there — could drop the countdown beyond ~24h or format as days.
Feed grouping key is HH:MM — two cycles in the same minute would merge; group by cycle time/ID instead.
Approvals: no expiry shown — "expires at the next cycle" per spec, but the card only says "Waiting since". Adding "expires at the 19:46 cycle" helps the decision.
Minus glyphs: formatDayChange uses U+2212 − but since_start_pct renders the backend's ASCII - — inconsistent typography in adjacent numbers.
document.title is static — could carry ambient state (e.g., ● Market open — Stock Agent, ⏸ Paused) since this page lives in a pinned tab.
Stat tile links: "Model calls today" links to Inspector (spec-correct) but has no affordance; "Since start" could link to Performance.
Suggested order
Feed collapsing (item 2) + chart pill row (item 1) — they're actively bad at 71 symbols today.
Loading/error tri-state (items 4–5).
Unrealized P&L color + NY-hover + focus-visible + countdown aria fix (D + E quick wins).
Weekend/"last session" semantics (item 6) — needs a spec decision first.
Cycle-counter redesign + positions footnote + approval card layout to match the design.
Nothing was changed. If you want, I can turn this into a prioritized task list, or start on any of the items — the scale issues (1, 2) are the ones actually hurting the page right now.
