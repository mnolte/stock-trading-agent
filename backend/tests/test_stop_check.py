"""The stop invariant (docs/SPEC.md, Alpaca integration — Stop check;
item 050). One check at the end of every reconcile: protected means a
live stop (or an exit in flight) covering the shares Alpaca reports.
Unprotected after one more repair opens a `stop_missing` episode that
only a later pass — finding the position protected or flat — closes.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from app.broker.fake import FakeBroker, _OrderRec, _PosRec
from app.config import AppConfig
from app.data.factory import DataSources
from app.data.fake import (
    FakeBarSource,
    FakeCalendarSource,
    FakeCorporateActionSource,
    FakeEarningsSource,
    FakeFXSource,
    FakeNewsSource,
    FakeQuoteSource,
)
from app.data.models import Bar5m
from app.data.service import MarketDataService
from app.db.models import Event, Position
from app.engine.executor import Executor
from app.engine.state import ExecutionState
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from app.signals.service import SignalService
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import NOW, events_of, open_position

pytestmark = pytest.mark.anyio

# NOW (tests.helpers): Thursday 15 Oct 2026, 13:00 New York — mid-session.
AM = timedelta(minutes=1)


class _Notices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def notify(self, event: str, line: str, **kwargs: Any) -> None:
        self.calls.append((event, line, kwargs))


def _sources(**overrides: object) -> DataSources:
    calendar = FakeCalendarSource()
    sources = DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar,
        alpaca=None,
    )
    for key, value in overrides.items():
        setattr(sources, key, value)
    return sources


def _executor(
    session_factory: sessionmaker[Session],
    broker: FakeBroker,
    clock: FakeClock | None = None,
    config: AppConfig | None = None,
    notifier: _Notices | None = None,
) -> Executor:
    clock = clock or FakeClock(NOW)
    config = config or AppConfig()
    sources = _sources(quotes=FakeQuoteSource(clock))
    calendar = cast(CalendarProvider, sources.calendar)
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    signals = SignalService(market_data, calendar, config, clock, session_factory)
    executor = Executor(
        broker=broker,
        market_data=market_data,
        signals=signals,
        calendar=calendar,
        config=config,
        clock=clock,
        state=ExecutionState(clock=clock),
        session_factory=session_factory,
        notifier=cast(Any, notifier),
    )
    for symbol, price in (broker.prices or {"MSFT": Decimal("420")}).items():
        executor.market_data.bars_cache.store([Bar5m(
            symbol=symbol, start=NOW - timedelta(minutes=30),
            open=price, high=price, low=price, close=price, volume=100,
            feed="sip",
        )])
    return executor


def _holding(broker: FakeBroker, symbol: str = "MSFT", qty: int = 4) -> None:
    broker.positions[symbol] = _PosRec(
        qty=qty, avg_entry_price=Decimal("415")
    )


def _position_row(
    session: Session,
    symbol: str = "MSFT",
    qty: int = 4,
    entry: str = "415",
    stop: str = "410",
) -> Position:
    open_position(session, symbol, qty=qty, entry=entry, stop=stop)
    session.commit()
    return session.execute(
        select(Position).where(Position.symbol == symbol)
    ).scalar_one()


def _episodes(session: Session) -> list[Event]:
    return events_of(session, "stop_missing")


def _market_sell(
    broker: FakeBroker, symbol: str, qty: int, *, status: str = "new"
) -> None:
    """An open market sell at the broker — what a fresh exit looks like
    before it fills (the fake fills submitted sells at once)."""
    now = broker._now()
    rec = _OrderRec(
        id=broker._next_id(),
        client_order_id=None,
        symbol=symbol,
        side="sell",
        order_type="market",
        order_class="",
        status=status,
        qty=qty,
        filled_qty=0,
        filled_avg_price=None,
        limit_price=None,
        stop_price=None,
        created_at=now,
        updated_at=now,
        filled_at=None,
    )
    broker.orders[rec.id] = rec


async def test_live_stop_is_protected(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    _holding(broker)
    await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="x"
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _position_row(session)

    report = await executor.reconcile()

    assert report.ok
    with session_factory() as session:
        assert _episodes(session) == []


async def test_missing_stop_repaired_no_episode(
    session_factory: sessionmaker[Session],
) -> None:
    """The stop leg is gone; the repair succeeds — no episode."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    _holding(broker)
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _position_row(session)

    report = await executor.reconcile()

    assert report.protective_stops == ["MSFT"]
    with session_factory() as session:
        assert _episodes(session) == []


async def test_repair_refused_opens_episode(
    session_factory: sessionmaker[Session],
) -> None:
    """Still unprotected after the second attempt: one critical
    stop_missing episode with shares, stop qty and the reason."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    _holding(broker)
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _position_row(session)
    broker.fail_writes = True

    await executor.reconcile()

    with session_factory() as session:
        episodes = _episodes(session)
        assert len(episodes) == 1
        episode = episodes[0]
        assert episode.level == "critical"
        assert episode.resolved_at is None
        details = episode.details or {}
        assert details["symbol"] == "MSFT"
        assert details["shares"] == "4"
        assert details["stop_qty"] == "0"
        assert details["started_at"]
        assert "rejected" in str(details["reason"])

    # The episode stays open — one row only — on the next pass.
    await executor.reconcile()
    with session_factory() as session:
        assert len(_episodes(session)) == 1


async def test_episode_closes_when_stop_exists(
    session_factory: sessionmaker[Session],
) -> None:
    """The KO replay: 7 shares without a broker-side stop. The episode
    opens, stays open while the broker refuses, and closes with
    resolution ``condition`` once a stop for the 7 shares stands."""
    broker = FakeBroker(FakeClock(NOW), prices={"KO": Decimal("68")})
    _holding(broker, "KO", 7)
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _position_row(session, "KO", 7, entry="70", stop="65")
    broker.fail_writes = True

    await executor.reconcile()
    with session_factory() as session:
        episode = _episodes(session)[0]
        assert episode.details and episode.details["shares"] == "7"
        assert episode.resolved_at is None

    # A "restart": a second pass against the still-unprotected position
    # keeps the same episode open.
    await executor.reconcile()
    with session_factory() as session:
        assert len(_episodes(session)) == 1
        assert _episodes(session)[0].resolved_at is None

    broker.fail_writes = False
    report = await executor.reconcile()
    assert report.protective_stops == ["KO"]
    with session_factory() as session:
        episode = _episodes(session)[0]
        assert episode.resolved_at is not None
        assert episode.resolution == "condition"


async def test_episode_closes_when_flat(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    _holding(broker)
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _position_row(session)
    broker.fail_writes = True
    await executor.reconcile()

    # Alpaca is flat — whatever filled it, the state cleared.
    del broker.positions["MSFT"]
    broker.fail_writes = False
    await executor.reconcile()

    with session_factory() as session:
        episode = _episodes(session)[0]
        assert episode.resolved_at is not None
        assert episode.resolution == "condition"


async def test_market_sell_in_flight_counts_then_expires(
    session_factory: sessionmaker[Session],
) -> None:
    """A market sell < exit_in_flight_seconds old is protection; at 90 s
    it is not — the position needs its stop (KO, 4 Nov)."""
    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    _holding(broker)
    executor = _executor(session_factory, broker, clock=clock)
    with session_factory() as session:
        _position_row(session)
    _market_sell(broker, "MSFT", 4)

    await executor.reconcile()
    with session_factory() as session:
        assert _episodes(session) == []

    clock.advance(timedelta(seconds=90))
    broker.fail_writes = True
    await executor.reconcile()
    with session_factory() as session:
        assert len(_episodes(session)) == 1


async def test_moo_queued_outside_session_protects_until_two_after_open(
    session_factory: sessionmaker[Session],
) -> None:
    """An MOO sell queued outside the session counts until two minutes
    after the open — after that the position is unprotected."""
    after_close = NOW.astimezone(NY_TZ).replace(hour=21, minute=0)
    clock = FakeClock(after_close.astimezone(UTC))
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    _holding(broker)
    executor = _executor(session_factory, broker, clock=clock)
    with session_factory() as session:
        _position_row(session)
    await broker.submit_market_sell(
        symbol="MSFT", qty=4, market_on_open=True, client_order_id="moo-1"
    )

    await executor.reconcile()
    with session_factory() as session:
        assert _episodes(session) == []

    # The next session's open is 09:30 New York; at +3 minutes the
    # queued sell should have filled — no longer protection.
    ny = clock.now().astimezone(NY_TZ)
    session_day = executor.calendar.session_for((ny + timedelta(days=1)).date())
    if session_day is None:
        # find the next trading day via the fake calendar's sessions
        for offset in range(1, 5):
            session_day = executor.calendar.session_for(
                (ny + timedelta(days=offset)).date()
            )
            if session_day is not None:
                break
    assert session_day is not None
    clock.set(session_day.open + timedelta(minutes=3))
    broker.fail_writes = True
    await executor.reconcile()
    with session_factory() as session:
        assert len(_episodes(session)) == 1


async def test_pending_exit_is_not_protection(
    session_factory: sessionmaker[Session],
) -> None:
    """A held-back exit keeps needing its stop (docs/SPEC.md, Stop
    check): with the stop standing it is protected, without it opens an
    episode."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    _holding(broker)
    await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="x"
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        position = _position_row(session)
        position.pending_exit = {
            "reason": "exit", "since": NOW.isoformat(), "decision_id": 1,
        }
        session.commit()

    await executor.reconcile()
    with session_factory() as session:
        assert _episodes(session) == []

    # Without the stop, the pending exit is not protection.
    for rec in broker.orders.values():
        rec.status = "canceled"
    broker.fail_writes = True
    await executor.reconcile()
    with session_factory() as session:
        assert len(_episodes(session)) == 1


async def test_partial_stop_opens_episode(
    session_factory: sessionmaker[Session],
) -> None:
    """Five shares of stop for seven held is not protection."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    _holding(broker, qty=7)
    await broker.submit_stop_order(
        symbol="MSFT", qty=5, stop_price=Decimal("410"), client_order_id="x"
    )
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _position_row(session, qty=7)
    broker.fail_writes = True

    await executor.reconcile()

    with session_factory() as session:
        episode = _episodes(session)[0]
        assert episode.details is not None
        assert episode.details["shares"] == "7"
        assert episode.details["stop_qty"] == "5"


async def test_episode_notifications(
    session_factory: sessionmaker[Session],
) -> None:
    """The missing_stop notification fires at the start, then again
    every 30 minutes during the session while the episode lasts — never
    more often, never held back."""
    from app.engine.state import set_event_sink
    from app.notifications.events import format_recorded

    clock = FakeClock(NOW)
    broker = FakeBroker(clock, prices={"MSFT": Decimal("420")})
    _holding(broker)
    notices = _Notices()

    def _sink(
        event_type: str, level: str, details: dict[str, Any] | None
    ) -> None:
        mapped = format_recorded(event_type, details)
        if mapped is not None:
            key, line, link = mapped
            notices.notify(key, line, link=link)

    set_event_sink(_sink)
    executor = _executor(session_factory, broker, clock=clock, notifier=notices)
    try:
        with session_factory() as session:
            _position_row(session)
        broker.fail_writes = True

        await executor.reconcile()
        # The episode's missing_stop alert fired at the start (via the
        # event sink, as in production).
        assert any(
            c[0] == "missing_stop" and "no stop at Alpaca" in c[1]
            for c in notices.calls
        )

        # 10 minutes later: still open, no repeat yet.
        clock.advance(timedelta(minutes=10))
        await executor.reconcile()
        assert not [
            c for c in notices.calls if "still no stop ·" in c[1]
        ]

        # Past 30 minutes, in session: "still no stop · 0 h 31 min".
        clock.advance(timedelta(minutes=21))
        await executor.reconcile()
        repeats = [c for c in notices.calls if "still no stop ·" in c[1]]
        assert len(repeats) == 1
        assert repeats[0][0] == "missing_stop"
        assert "0 h 31 min" in repeats[0][1]

        # Outside the session the repeat waits (critical is never held
        # back, but repeats are every 30 minutes during the session).
        clock.set(
            NOW.astimezone(NY_TZ).replace(hour=21).astimezone(UTC)
        )
        await executor.reconcile()
        assert len(
            [c for c in notices.calls if "still no stop ·" in c[1]]
        ) == 1

        # Closing sends the info "stop restored".
        broker.fail_writes = False
        await executor.reconcile()
        restored = [c for c in notices.calls if "stop restored" in c[1]]
        assert len(restored) == 1
        assert restored[0][0] == "missing_stop"
        assert restored[0][2].get("severity") == "info"
    finally:
        set_event_sink(None)


async def test_protective_stop_failed_resolves_by_condition(
    session_factory: sessionmaker[Session],
) -> None:
    """A protective_stop_failed resolves when the stop check finds the
    position protected — never after quiet time."""
    from app.readiness.service import apply_auto_resolution

    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    _holding(broker)
    executor = _executor(session_factory, broker)
    with session_factory() as session:
        _position_row(session)
    broker.fail_writes = True
    await executor.reconcile()

    # Two hours of quiet do not resolve the state errors.
    quiet = NOW + timedelta(hours=2)
    with session_factory() as session:
        apply_auto_resolution(session, now=quiet, minutes=60)
        session.commit()
        assert all(
            e.resolved_at is None
            for e in events_of(session, "protective_stop_failed")
        )
        assert _episodes(session)[0].resolved_at is None

    broker.fail_writes = False
    await executor.reconcile()
    with session_factory() as session:
        assert all(
            e.resolution == "condition"
            for e in events_of(session, "protective_stop_failed")
        )
