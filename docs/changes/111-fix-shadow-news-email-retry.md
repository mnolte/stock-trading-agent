---
item: 111
type: fix
title: Shadow-held snapshots get news; email send retries once
status: merged
issue: 329
kickoff: —
spec_prs: []
build_prs: [330]
---

# 111 · fix · Shadow-held snapshots get news; email send retries once

## 2026-11-21 · build PR #330

Found analysing the live data of 14–21 November 2026:

- **Shadow-held snapshots claimed `news_status: "failed"` for a fetch that never ran.** `build_input_snapshot` defaults `news=None` to `"failed"`, and the shadow-only snapshot path passed no news and no status. In the week, 13 shadow calls (AAPL, CVX, MCD, COST, VZ, DIS, WMT, PG) carried `headlines: "unavailable"` and an `input_incomplete` warning, biasing shadow decisions toward skip/hold. The cycle now fetches news for the `shadow_only` symbols — they get model calls from the shadows, so they belong to the "symbols that get a model call" news set — and passes the fetched news and the real per-symbol status into the snapshot. A failed fetch still stores `failed` honestly.
- **A single transient SMTP failure lost the notification.** On 19 Nov the `shock_brake` warning (SPY −2.53%) timed out once and was never emailed; the `new_error` follow-up about it failed the same way. `_send_email` now retries the send once after `EMAIL_RETRY_DELAY_SECONDS` (10 s, injectable as `email_retry_delay`, 0 in tests). Sending stays in the background; a dead server costs one extra attempt, not a loop.
- `FakeMailer` gained `fail_times` so tests can fail a bounded number of sends.

Spec question for review: SPEC fetches news "only for symbols that get a model call". Shadow-held symbols do get model calls (from shadows), so this reads as compliant, but the spec never says explicitly that shadow-only snapshots carry news; if the intent was none, the alternative is a distinct status rather than a false `failed`.
