# 092 · fix · Daily bars: no source gap before the session closes

Kickoff for Devin. Start the session with: "Build docs/kickoffs/092-fix-premature-source-gaps.md."

Spec: `docs/SPEC.md` → Market data (line ~104, daily bars refreshed at
08:00 New York), the `daily_source_gaps` table (line ~625: "daily
sessions the source confirmed it has no bar for"), and **Adding a
symbol → "Gaps at the source"** (line ~390). Code:
`backend/app/data/service.py` (`refresh_daily`),
`backend/app/data/cache.py` (`record_daily_gaps`, `daily_gap_dates*`),
`backend/app/data/alpaca.py` (`fetch_daily_bars`), callers in
`app/api/main.py`, `app/universe/service.py` (`_refresh_history`),
`app/data/vix.py` and `app/api/routes.py` (sync retry).

Move this kickoff to `docs/kickoffs/archive/` with `git mv` in the build
PR.

## The bug (seen live, 13 Nov 2026)

Every watchlist symbol showed "Warming up: 258 of 260 daily bars · 60
of 60 sessions" and no entries were possible all day. The database had
zero `bars_1d` rows for 2026-11-12 (except VIXY, whose refresh runs on
its own schedule) and 76 `daily_source_gaps` rows for that date — one
per symbol. Alpaca had the bar; the app simply never asked for it again.

The chain:

1. `refresh_daily` computes `fetch_start` per symbol and always
   refetches `end` — the newest session. For a warm symbol that is a
   one-day request for *today*.
2. The scheduled refreshes run pre-market: the 08:00 New York cron
   (`daily-data-refresh`) and the 08:10 universe screen's
   `_refresh_history`. `fetch_daily_bars` caps `end` at
   `now - SIP_MIN_AGE`, so today's daily bar does not exist yet —
   Alpaca returns empty.
3. `refresh_daily` then calls `record_daily_gaps` for every session in
   `[fetch_start, end]` that was not returned — including the session
   that has not happened yet. Today is marked a "source-confirmed" gap.
4. On the next refresh, gap dates are excluded from `missing`, so the
   date is never requested again. Yesterday's bar is gone for good.
5. The warming check forgives source gaps only outside the last 5
   sessions (they feed prior-session levels, ATR and the gap check), so
   a yesterday-gap keeps every symbol warming — in this case until
   about 19–20 Nov, a full week without entries.

It stayed hidden because a restart's startup refresh refetches `end`
after Alpaca has published the day's bar, clearing that day's gap
mark. On 12 Nov the only start was 02:10 New York — pre-market — so
nothing ever refetched the day. Every weekday without an intraday
restart hits this.

## The rule

A daily session's absence is a source gap only once that session has
closed **and** the source has had a chance to publish the bar — in
practice, once a later refresh could have fetched it. A session still
open (or just closed, while the vendor may still be publishing) is
*missing*, not gapped: it stays in the fetch range and is asked for
again. Genuine gaps — a halt, a feed gap on a completed session — are
still recorded, one refresh later than today.

Concretely: `refresh_daily` needs the fetch time (`now`, injected —
the callers all have the clock) and must not write gap marks for
sessions whose close is not safely past. Decide the exact cutoff with
the publication lag in mind (`SIP_MIN_AGE` and the fact that the daily
bar appears some time after the close); a session that closed on an
earlier New York date than the fetch is the safe case. Sessions that
fail the cutoff simply remain missing, so `fetch_start` keeps covering
them — no extra requests beyond the one-day refetch that already
happens.

## Build

1. Pass `now` into `refresh_daily` (new parameter; update the callers:
   the scheduled refresh and retry in `app/api/main.py` and
   `app/api/routes.py`, `_refresh_history` in
   `app/universe/service.py`, `app/data/vix.py`, `app/smoke_data.py`,
   tests). MarketDataService has no clock — take `now` as an argument,
   never `datetime.now()` (hard rule 5).
2. When recording gaps, skip sessions that are not yet confirmable
   (see The rule). Keep recording for older confirmed sessions
   unchanged.
3. Tests, pass and fail, in `backend/tests/test_data.py` style:
   - a fetch for `end` = today before the close returns no bar → no gap
     row for today, and the next day's refresh range starts at that
     date (the bar is asked for again);
   - a fetch covering a session closed days ago that returns no bar →
     the gap row is written as today;
   - a just-closed session whose bar the source has not published yet
     → no gap row (the cutoff case);
   - existing gap behaviour for halted symbols unchanged.
4. `docs/CHANGES.md` entry. Check whether `docs/SPEC.md` line ~625
   ("only a successful fetch for the period writes a row") wants the
   confirmation wording tightened — spec changes only when Marc asks;
   ask in the PR if unsure.

## Not in scope

- Refetching already-recorded gap dates (the 3-session tolerance for
  real halts stays as is; recent gaps could be retried, but that is a
  separate trade-off).
- The 5-minute history path (`refresh_history`) — it has no gap table.
- Backfilling 2026-11-12 in production: done by hand on 13 Nov (the
  false gap rows were deleted; the next scheduled refresh fills it).
