from __future__ import annotations

from datetime import date, datetime, timedelta

from app.data.models import Bar1h, Bar5m, CalendarDay, DailyBar


def bars_per_session(
    bars_5m: list[Bar5m], sessions: list[CalendarDay]
) -> dict[date, list[Bar5m]]:
    """Bucket 5-minute bars into their trading session in one pass.

    ``bars_5m`` and ``sessions`` are sorted by time; sessions never overlap.
    Bars outside every session window are dropped.
    """
    result: dict[date, list[Bar5m]] = {s.date: [] for s in sessions}
    idx = 0
    for bar in sorted(bars_5m, key=lambda b: b.start):
        while idx < len(sessions) and bar.start >= sessions[idx].close:
            idx += 1
        if idx == len(sessions):
            break
        if bar.start >= sessions[idx].open:
            result[sessions[idx].date].append(bar)
    return result


def session_bars(bars_5m: list[Bar5m], sessions: list[CalendarDay]) -> list[Bar5m]:
    """Only the bars inside one of the given session windows, in order.

    Alpaca's SIP feed returns the full extended session (04:00-20:00 New
    York); the cache and everything reading it is regular-hours only, so
    fetched bars are filtered here before they are stored.
    """
    return [
        bar
        for day_bars in bars_per_session(bars_5m, sessions).values()
        for bar in day_bars
    ]


def session_1h_windows(session: CalendarDay) -> list[tuple[datetime, datetime]]:
    """Session-aligned 1h windows: 09:30-10:30, ..., plus the final partial bar."""
    windows: list[tuple[datetime, datetime]] = []
    start = session.open
    while start < session.close:
        end = min(start + timedelta(hours=1), session.close)
        windows.append((start, end))
        start = end
    return windows


def aggregate_1h(symbol: str, bars_5m: list[Bar5m], session: CalendarDay) -> list[Bar1h]:
    """Aggregate 5-minute bars into session-aligned 1-hour bars for one session."""
    bars = sorted(bars_5m, key=lambda b: b.start)
    result: list[Bar1h] = []
    for win_start, win_end in session_1h_windows(session):
        matched = [
            b
            for b in bars
            if win_start <= b.start.astimezone(win_start.tzinfo) < win_end
        ]
        if not matched:
            continue
        expected = int((win_end - win_start).total_seconds() // 300)
        first, last = matched[0], matched[-1]
        result.append(
            Bar1h(
                symbol=symbol,
                start=win_start,
                end=win_end,
                open=first.open,
                high=max(b.high for b in matched),
                low=min(b.low for b in matched),
                close=last.close,
                volume=sum(b.volume for b in matched),
                feed=first.feed,
                complete=len(matched) == expected,
            )
        )
    return result


def aggregate_daily(symbol: str, bars_5m: list[Bar5m], session: CalendarDay) -> DailyBar | None:
    """Aggregate a session's 5-minute bars into one daily bar."""
    tz = session.open.tzinfo
    matched = sorted(
        (b for b in bars_5m if session.open <= b.start.astimezone(tz) < session.close),
        key=lambda b: b.start,
    )
    if not matched:
        return None
    return DailyBar(
        symbol=symbol,
        date=session.date,
        open=matched[0].open,
        high=max(b.high for b in matched),
        low=min(b.low for b in matched),
        close=matched[-1].close,
        volume=sum(b.volume for b in matched),
    )
