"""Stream reconnect logging (docs/SPEC.md, Alpaca integration — Stream
health): the first failed connection of an outage at ERROR, the same
error at most once per 10 minutes with a count, the reconnect at INFO
with the outage length, an outage over 5 minutes in-session marking
the stream degraded."""

import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.broker.alpaca import StreamOutageFilter
from app.market.clock import FakeClock
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)  # 13:00 New York — open
AFTER_HOURS = datetime(2026, 10, 15, 22, 0, tzinfo=UTC)  # 18:00 — closed


def _record(name: str, level: int, msg: str) -> logging.LogRecord:
    return logging.makeLogRecord(
        {
            "name": "alpaca.trading.stream",
            "levelno": level,
            "levelname": logging.getLevelName(level),
            "msg": msg,
            "args": (),
        }
    )


def test_outage_first_error_then_counted_repeats(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = [0.0]
    degraded: list[bool] = []
    stream = StreamOutageFilter(
        degraded=lambda: degraded.append(True), now=lambda: clock[0]
    )
    drop = (
        "trading stream websocket error, restarting  connection: "
        "no close frame received or sent"
    )

    # A lost socket opens the outage at the SDK's WARNING — only a
    # failed connection is ERROR (spec).
    first = _record("alpaca.trading.stream", logging.WARNING, drop)
    assert stream.filter(first) is True
    assert first.levelno == logging.WARNING

    # The same failure is silent until the 10-minute report.
    for _ in range(5):
        assert stream.filter(
            _record("alpaca.trading.stream", logging.WARNING, drop)
        ) is False

    clock[0] += 601
    report = _record("alpaca.trading.stream", logging.WARNING, drop)
    assert stream.filter(report) is True
    assert "x7" in report.getMessage()

    # A failed reconnect during the outage logs at ERROR (spec).
    err = "error during websocket communication: TimeoutError"
    failed = _record("alpaca.trading.stream", logging.ERROR, err)
    assert stream.filter(failed) is True
    assert failed.levelno == logging.ERROR

    # Reconnect: INFO with the outage length on the app logger.
    with caplog.at_level(logging.INFO, logger="app.broker.stream"):
        assert stream.filter(
            _record(
                "alpaca.trading.stream", logging.INFO, "connected to: wss://x"
            )
        ) is True
    assert "reconnected after" in caplog.text
    assert "601" in caplog.text

    # The next outage starts fresh: first failure logs again.
    assert stream.filter(
        _record("alpaca.trading.stream", logging.ERROR, err)
    ) is True


def test_outage_past_five_minutes_marks_degraded_once() -> None:
    clock = [0.0]
    degraded: list[bool] = []
    stream = StreamOutageFilter(
        degraded=lambda: degraded.append(True), now=lambda: clock[0]
    )
    err = "error during websocket communication: TimeoutError"
    stream.filter(_record("alpaca.trading.stream", logging.ERROR, err))
    clock[0] += 301
    stream.filter(_record("alpaca.trading.stream", logging.ERROR, err))
    assert degraded == [True]
    # One callback per outage, even as failures continue.
    stream.filter(_record("alpaca.trading.stream", logging.ERROR, err))
    assert degraded == [True]


async def test_outage_degrades_only_in_session(
    session_factory: sessionmaker[Session],
) -> None:
    """The 5-minute outage marks the stream degraded while New York is
    in session; an overnight drop does not (docs/SPEC.md — Stream
    health)."""

    from app.broker.fake import FakeBroker

    from tests.test_executor import _executor

    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("100")})
    executor = _executor(session_factory, broker, clock)

    await executor.on_stream_failure("stream reconnect outage 320s")
    assert executor.state.stream_degraded

    executor2 = _executor(
        session_factory,
        FakeBroker(FakeClock(AFTER_HOURS), prices={"MSFT": Decimal("100")}),
        FakeClock(AFTER_HOURS),
    )
    await executor2.on_stream_failure("stream reconnect outage 320s")
    assert not executor2.state.stream_degraded
