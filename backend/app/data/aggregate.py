from __future__ import annotations

from datetime import datetime, timedelta

from app.data.models import Bar1h, Bar5m, CalendarDay


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
