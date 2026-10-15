from datetime import date, datetime, time, timedelta
from decimal import Decimal

from app.data.aggregate import aggregate_1h, session_1h_windows
from app.data.models import Bar5m, CalendarDay
from app.market.clock import NY_TZ, UTC


def _session(day: date, close: time = time(16, 0)) -> CalendarDay:
    return CalendarDay(
        date=day,
        open=datetime.combine(day, time(9, 30), tzinfo=NY_TZ),
        close=datetime.combine(day, close, tzinfo=NY_TZ),
    )


def _bar(symbol: str, start: datetime, price: str, volume: int = 100) -> Bar5m:
    p = Decimal(price)
    return Bar5m(
        symbol=symbol,
        start=start.astimezone(UTC),
        open=p,
        high=p + Decimal("0.10"),
        low=p - Decimal("0.10"),
        close=p,
        volume=volume,
        feed="test",
    )


def test_session_windows_regular_day() -> None:
    session = _session(date(2026, 10, 15))
    windows = session_1h_windows(session)
    # 09:30-10:30 .. 14:30-15:30 plus the 15:30-16:00 half bar.
    assert len(windows) == 7
    assert windows[0] == (
        datetime(2026, 10, 15, 9, 30, tzinfo=NY_TZ),
        datetime(2026, 10, 15, 10, 30, tzinfo=NY_TZ),
    )
    assert windows[-1] == (
        datetime(2026, 10, 15, 15, 30, tzinfo=NY_TZ),
        datetime(2026, 10, 15, 16, 0, tzinfo=NY_TZ),
    )


def test_session_windows_early_close() -> None:
    # 13:00 early close (e.g. Thanksgiving Friday).
    session = _session(date(2026, 11, 27), close=time(13, 0))
    windows = session_1h_windows(session)
    assert len(windows) == 4
    assert windows[-1] == (
        datetime(2026, 11, 27, 12, 30, tzinfo=NY_TZ),
        datetime(2026, 11, 27, 13, 0, tzinfo=NY_TZ),
    )


def test_aggregate_combines_5m_bars() -> None:
    day = date(2026, 10, 15)
    session = _session(day)
    start = datetime(2026, 10, 15, 9, 30, tzinfo=NY_TZ)
    bars = [
        _bar("AAPL", start + timedelta(minutes=5 * i), str(100 + i))
        for i in range(12)
    ]
    result = aggregate_1h("AAPL", bars, session)
    assert len(result) == 1
    bar = result[0]
    assert bar.start == start
    assert bar.end == start + timedelta(hours=1)
    assert bar.open == Decimal("100")
    assert bar.close == Decimal("111")
    assert bar.high == Decimal("111.10")
    assert bar.low == Decimal("99.90")
    assert bar.volume == 1200
    assert bar.complete is True


def test_aggregate_full_session_produces_seven_bars() -> None:
    day = date(2026, 10, 15)
    session = _session(day)
    open_dt = session.open
    # 78 regular 5-minute bars across the whole session.
    bars: list[Bar5m] = []
    current = open_dt
    while current < session.close:
        bars.append(_bar("AAPL", current, "100"))
        current += timedelta(minutes=5)
    assert len(bars) == 78
    result = aggregate_1h("AAPL", bars, session)
    assert len(result) == 7
    assert all(b.complete for b in result)
    assert result[-1].start == datetime(2026, 10, 15, 15, 30, tzinfo=NY_TZ)
    assert result[-1].end == datetime(2026, 10, 15, 16, 0, tzinfo=NY_TZ)


def test_aggregate_marks_incomplete_window() -> None:
    day = date(2026, 10, 15)
    session = _session(day)
    start = datetime(2026, 10, 15, 9, 30, tzinfo=NY_TZ)
    bars = [_bar("AAPL", start + timedelta(minutes=5 * i), "100") for i in range(5)]
    result = aggregate_1h("AAPL", bars, session)
    assert len(result) == 1
    assert result[0].complete is False


def test_aggregate_early_close_final_partial_bar() -> None:
    day = date(2026, 11, 27)
    session = _session(day, close=time(13, 0))
    bars: list[Bar5m] = []
    current = session.open
    while current < session.close:
        bars.append(_bar("AAPL", current, "100"))
        current += timedelta(minutes=5)
    # 42 five-minute bars in a 3.5-hour session.
    assert len(bars) == 42
    result = aggregate_1h("AAPL", bars, session)
    assert len(result) == 4
    last = result[-1]
    assert last.end == session.close
    assert last.complete is True
