# Fix — trade-updates stream and clock check (urgent, before PR 31)

Kickoff for Devin. Start the session with: "Build docs/kickoffs/fix-stream-clock.md."

```text
Urgent fix, before PR 31. From the live-data report of 29 Oct.

Read on main: docs/CHANGES.md entry 2026-10-29 "Stream health, new
alerts, library log levels"; docs/SPEC.md → Alpaca integration →
Reconciliation ("Stream health") and Notifications (the three new
rows); AGENTS.md hard rules 3, 6 and 15.

1. Trade-updates stream (critical). TradingStream is created with
   raw_data=True (backend/app/broker/alpaca.py:172), so callbacks get
   the wrapper {"stream": "trade_updates", "data": {...}}.
   parse_trade_update reads "order" at the top level, parse_order({})
   raises KeyError 'id' (alpaca.py:89), and every update since 28 Oct
   has been lost. Entry fills and stop fills were only seen by the
   15-minute position check (RTX stop filled 18:47:34 UTC, seen 19:01).
   - Unwrap msg["data"] when the message has stream == "trade_updates"
     (keep handling the inner payload and SDK models too).
   - Record one real paper trade-update message per event type you can
     get (new, fill, partial_fill, canceled, replaced) as test fixtures
     in the exact shape the SDK delivers, keys scrubbed, and test the
     parser on them. The existing test in tests/test_alpaca.py:224 feeds
     the inner payload only; keep it and add the wrapper cases.
   - Stream health per the spec: an unparseable update logs ERROR with
     the scrubbed raw message, counts, and marks the stream degraded; so
     does a fill seen by reconciliation whose update didn't arrive within
     2 minutes. degraded shows the backend banner, sends the critical
     "Trade-updates stream degraded" notification, and clears after 30
     minutes without a new failure. stream_connected alone no longer
     means healthy.
   - Do not build PR 31's "exits close from the stream" here; only make
     the stream deliver what it receives today.
2. Clock check (high). app/ops/service.py:174 calls get_clock on the
   broker, which doesn't have it (the type: ignore hid it); every check
   since 28 Oct raised AttributeError and clock.offset_ms stayed null.
   Use the data client's get_clock (app/data/alpaca.py:277). Remove the
   type: ignore. A failing check sends the new "Clock check failing"
   notification once per session, not every hour.
3. Tests (no network): the wrapper fixtures parse into the right
   TradeUpdateInfo; a malformed message marks the stream degraded and
   sends one notification; a fill found by reconcile without an update
   within 2 minutes marks it degraded; the clock check reads the data
   client's clock with a fake and stores an offset; mypy passes without
   the type: ignore.

After merging: restart outside 14:30–21:00 Amsterdam this week
(15:30–22:00 from 2 November). The next session, confirm in the PR that
entry and stop fills arrive through the stream within seconds (log
lines with times), and that server-status.json shows a clock offset.
Use the PR template.
```
