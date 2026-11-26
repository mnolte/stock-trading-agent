"""Outcome worker: 1h / 1-session / 5-session prices and best/worst.

Targets follow the spec's clarified timing: "1 session" is the same New
York time on the next trading session, "5 sessions" the same time on the
5th; a target past the session's close (early close) falls back to the
close. Each price is the last closed 5-minute bar at or before the target,
stored with the bar's timestamp, and only once the SIP delay has passed.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
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
from app.data.models import Bar5m, DataResult
from app.data.service import MarketDataService
from app.db.models import (
    Bar5m as Bar5mRow,
)
from app.db.models import (
    Cycle,
    Decision,
    EquitySnapshot,
    Order,
    Outcome,
    Position,
    Run,
)
from app.engine.outcomes import OutcomeWorker
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

# SIP delay plus a minute, so fills are clearly past the delay.
DELAY = timedelta(minutes=16)


def _worker(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource | None = None,
    bars: FakeBarSource | None = None,
) -> tuple[OutcomeWorker, MarketDataService]:
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["MSFT", "SPY"]
    calendar = calendar or FakeCalendarSource()
    sources = DataSources(
        bars=bars or FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar,
        alpaca=None,
    )
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    worker = OutcomeWorker(
        market_data=market_data,
        calendar=calendar,
        config=config,
        clock=clock,
        session_factory=session_factory,
    )
    return worker, market_data


def test_report_later_stop_does_not_back_apply_to_earlier_bar() -> None:
    from app.engine.outcomes import _first_touch

    at = datetime(2026, 10, 22, 15, tzinfo=UTC)
    position = Position(
        symbol="WMT", qty=16, avg_entry_price=Decimal("100"), initial_stop=Decimal("95"),
        current_stop_price=Decimal("99"), target_price=Decimal("110"), opened_at=at,
    )
    bars = [Bar5m("WMT", at, Decimal("100"), Decimal("101"), Decimal("96"),
                  Decimal("100"), 100, "sip")]
    assert _first_touch(bars, position) == "neither"


def test_first_touch_uses_the_stop_leg_active_at_each_bar() -> None:
    from app.engine.outcomes import _first_touch

    at = datetime(2026, 10, 22, 15, tzinfo=UTC)
    position = Position(
        symbol="WMT", qty=16, avg_entry_price=Decimal("100"),
        initial_stop=Decimal("95"), current_stop_price=Decimal("99"),
        target_price=Decimal("110"), opened_at=at,
    )
    history = [
        Order(
            alpaca_order_id="old", symbol="WMT", side="sell",
            order_type="stop", leg="stop", qty=16,
            stop_price=Decimal("95"), status="replaced",
            created_at=at.replace(tzinfo=None),
            active_until=(at + timedelta(minutes=5)).replace(tzinfo=None),
        ),
        Order(
            alpaca_order_id="new", symbol="WMT", side="sell",
            order_type="stop", leg="stop", qty=16,
            stop_price=Decimal("99"), status="new",
            created_at=(at + timedelta(minutes=5)).replace(tzinfo=None),
        ),
    ]
    early = Bar5m(
        "WMT", at, Decimal("100"), Decimal("101"), Decimal("96"),
        Decimal("100"), 100, "sip",
    )
    late = Bar5m(
        "WMT", at + timedelta(minutes=5), Decimal("100"), Decimal("101"),
        Decimal("98"), Decimal("100"), 100, "sip",
    )
    assert _first_touch([early], position, history) == "neither"
    assert _first_touch([early, late], position, history) == "stop"


def test_first_touch_ignores_target_leg_after_it_ended() -> None:
    """The target gets the same retroactive protection the stop already
    had: a cancelled target leg no longer counts for later bars."""
    from app.engine.outcomes import _first_touch

    at = datetime(2026, 10, 22, 15, tzinfo=UTC)
    position = Position(
        symbol="WMT", qty=16, avg_entry_price=Decimal("100"),
        initial_stop=Decimal("95"), current_stop_price=Decimal("99"),
        target_price=Decimal("110"), opened_at=at,
        entry_decision_id=7,
    )
    history = [
        Order(
            alpaca_order_id="tp", symbol="WMT", side="sell",
            order_type="limit", leg="target", qty=16,
            limit_price=Decimal("110"), status="canceled",
            decision_id=7,
            created_at=at.replace(tzinfo=None),
            active_until=(at + timedelta(minutes=5)).replace(tzinfo=None),
        ),
    ]
    live = Bar5m(
        "WMT", at, Decimal("100"), Decimal("111"), Decimal("98"),
        Decimal("100"), 100, "sip",
    )
    dead = Bar5m(
        "WMT", at + timedelta(minutes=5), Decimal("100"), Decimal("111"),
        Decimal("98"), Decimal("100"), 100, "sip",
    )
    # While the leg was live a touch counts; after its cancel the same
    # high is ignored — the old code counted it as "target".
    assert _first_touch([live], position, history) == "target"
    assert _first_touch([dead], position, history) == "neither"
    assert _first_touch([live, dead], position, history) == "target"


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def _seed_decision(
    session_factory: sessionmaker[Session],
    scheduled_utc: datetime,
    *,
    symbol: str = "MSFT",
    final_action: str = "skip",
) -> int:
    with session_factory() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=_naive(scheduled_utc),
            started_at=_naive(scheduled_utc),
            finished_at=_naive(scheduled_utc + timedelta(minutes=1)),
            status="done",
        )
        session.add(cycle)
        session.flush()
        decision = Decision(
            cycle_id=cycle.id,
            symbol=symbol,
            context="candidate",
            input_snapshot={"symbol": symbol, "price": 420.0},
            rules_result={"status": "pass", "reason": None, "checks": []},
            model_output={"action": final_action, "confidence": 0.6},
            validation_result={
                "valid": True,
                "model_action": final_action,
                "final_action": final_action,
                "changed_by_code": False,
            },
            final_action=final_action,
            prompt_version="decision.v1",
            prompt_hash="abc",
            model="fake-decision",
        )
        session.add(decision)
        session.flush()
        session.add(Outcome(decision_id=decision.id))
        session.commit()
        return decision.id


def _outcome(session_factory: sessionmaker[Session], decision_id: int) -> Outcome:
    with session_factory() as session:
        return session.execute(
            select(Outcome).where(Outcome.decision_id == decision_id)
        ).scalar_one()


async def _cache_bars(
    market_data: MarketDataService, start: datetime, end: datetime
) -> None:
    """Pull the fake 5m bars into the cache for [start, end]."""
    await market_data.refresh_bars(start, end)


def _expected_close(
    bars: list[Bar5m], target: datetime, calendar: CalendarProvider
) -> tuple[Decimal, datetime]:
    """Close and start of the last fake bar closed at or before target."""
    closed = [
        b
        for b in bars
        if min(b.start + timedelta(minutes=5), _session_close(b, calendar))
        <= target
    ]
    return closed[-1].close, closed[-1].start


def _session_close(bar: Bar5m, calendar: CalendarProvider) -> datetime:
    session = calendar.session_for(bar.start.astimezone(NY_TZ).date())
    if session is None:
        return bar.start + timedelta(minutes=5)
    return session.close.astimezone(UTC)


async def _bars(
    market_data: MarketDataService, start: datetime, end: datetime
) -> list[Bar5m]:
    result = await market_data.sources.bars.fetch_5m_bars(["MSFT"], start, end)
    return result.data


# ----------------------------------------------------------------------
# 1-hour target


async def test_price_1h_fills_after_sip_delay(
    session_factory: sessionmaker[Session],
) -> None:
    """Decision Thu 10:46 NY → 1h target 11:46 NY; filled 15 min later."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)  # 10:46 EDT
    target = decision_time + timedelta(hours=1)
    clock = FakeClock(target + DELAY)
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(market_data, decision_time - timedelta(days=1), clock.now())

    decision_id = _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    assert outcome.price_1h is not None
    # The last bar closed at or before 11:46 NY ends at 11:45.
    assert outcome.price_1h_at == datetime(2026, 10, 15, 15, 40)
    fetched = await _bars(market_data, decision_time, target)
    expected_close, expected_start = _expected_close(
        fetched, target, market_data.calendar
    )
    assert outcome.price_1h == expected_close
    assert outcome.price_1h_at == _naive(expected_start)


async def test_price_1h_waits_for_sip_delay(
    session_factory: sessionmaker[Session],
) -> None:
    """Before target + SIP delay the outcome stays empty."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    target = decision_time + timedelta(hours=1)
    clock = FakeClock(target + timedelta(minutes=5))
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(market_data, decision_time - timedelta(days=1), clock.now())

    decision_id = _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        report = worker.fill(session, clock.now())
        session.commit()

    assert _outcome(session_factory, decision_id).price_1h is None
    assert report.filled == 0


async def test_price_stays_empty_without_bars(
    session_factory: sessionmaker[Session],
) -> None:
    """Due but no cached bars: the target stays pending, not wrong."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    clock = FakeClock(decision_time + timedelta(hours=2))
    worker, _market_data = _worker(session_factory, clock)
    # Nothing cached on purpose.

    _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        report = worker.fill(session, clock.now())
        session.commit()

    assert report.pending >= 1
    with session_factory() as session:
        outcome = session.execute(select(Outcome)).scalar_one()
    assert outcome.price_1h is None


# ----------------------------------------------------------------------
# session targets


async def test_one_session_next_trading_day(
    session_factory: sessionmaker[Session],
) -> None:
    """Thu 10:46 → Fri 10:46 NY."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)  # Thu 10:46
    # Friday Oct 16, 10:46 EDT = 14:46 UTC; fill after the SIP delay.
    target = datetime(2026, 10, 16, 14, 46, tzinfo=UTC)
    clock = FakeClock(target + DELAY)
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(
        market_data, decision_time - timedelta(days=2), clock.now()
    )

    decision_id = _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    # Last bar closed at or before Fri 10:46: the 10:40–10:45 bar.
    assert outcome.price_1session_at == datetime(2026, 10, 16, 14, 40)
    assert outcome.price_1session is not None
    # 5 sessions lands on Thu Oct 22 — not due yet.
    assert outcome.price_5sessions is None


async def test_friday_to_monday(
    session_factory: sessionmaker[Session],
) -> None:
    """A Friday decision's 1-session target is Monday, not Saturday."""
    decision_time = datetime(2026, 10, 16, 19, 46, tzinfo=UTC)  # Fri 15:46
    target = datetime(2026, 10, 19, 19, 46, tzinfo=UTC)  # Mon 15:46
    clock = FakeClock(target + DELAY)
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(market_data, decision_time, clock.now())

    decision_id = _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    assert outcome.price_1session is not None
    assert outcome.price_1session_at == datetime(2026, 10, 19, 19, 40)


async def test_holiday_in_between(
    session_factory: sessionmaker[Session],
) -> None:
    """Monday a holiday: the 1-session target slides to Tuesday."""
    calendar = FakeCalendarSource(holidays={date(2026, 10, 19)})
    decision_time = datetime(2026, 10, 16, 19, 46, tzinfo=UTC)  # Fri 15:46
    target = datetime(2026, 10, 20, 19, 46, tzinfo=UTC)  # Tue 15:46
    clock = FakeClock(target + DELAY)
    worker, market_data = _worker(session_factory, clock, calendar)
    await _cache_bars(market_data, decision_time, clock.now())

    decision_id = _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    assert outcome.price_1session is not None
    assert outcome.price_1session_at == datetime(2026, 10, 20, 19, 40)


async def test_early_close_clamps_to_close(
    session_factory: sessionmaker[Session],
) -> None:
    """Target past an early close (13:00) uses the session's last bar."""
    calendar = FakeCalendarSource(
        early_closes={date(2026, 10, 16): time(13, 0)}
    )
    decision_time = datetime(2026, 10, 15, 19, 46, tzinfo=UTC)  # Thu 15:46
    clock = FakeClock(datetime(2026, 10, 16, 21, 0, tzinfo=UTC))
    worker, market_data = _worker(session_factory, clock, calendar)
    await _cache_bars(market_data, decision_time, clock.now())

    decision_id = _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    # Clamped to the 13:00 ET close → the 12:55–13:00 bar.
    assert outcome.price_1session_at == datetime(2026, 10, 16, 16, 55)
    assert outcome.price_1session is not None


async def test_five_sessions_later(
    session_factory: sessionmaker[Session],
) -> None:
    """Thu 15 Oct → 5 sessions later is Thu 22 Oct (weekend skipped)."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)  # Thu 10:46
    # Sessions after: Fri 16, Mon 19, Tue 20, Wed 21, Thu 22.
    target = datetime(2026, 10, 22, 14, 46, tzinfo=UTC)
    clock = FakeClock(target + DELAY)
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(market_data, decision_time, clock.now())

    decision_id = _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    assert outcome.price_5sessions is not None
    assert outcome.price_5sessions_at == datetime(2026, 10, 22, 14, 40)


async def test_dst_week(
    session_factory: sessionmaker[Session],
) -> None:
    """The US falls back Sun Nov 1 2026: Fri EDT → Mon EST same wall time."""
    decision_time = datetime(2026, 10, 30, 14, 46, tzinfo=UTC)  # Fri 10:46 EDT
    # Monday Nov 2, 10:46 EST = 15:46 UTC (offset changed over the weekend).
    target = datetime(2026, 11, 2, 15, 46, tzinfo=UTC)
    clock = FakeClock(target + DELAY)
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(market_data, decision_time, clock.now())

    decision_id = _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    assert outcome.price_1session is not None
    # The bar closed at or before 10:46 EST started 10:40 EST = 15:40 UTC.
    assert outcome.price_1session_at == datetime(2026, 11, 2, 15, 40)


# ----------------------------------------------------------------------
# best / worst while open


async def test_best_worst_while_open(
    session_factory: sessionmaker[Session],
) -> None:
    """An open position's extremes come from the cached 5m bars."""
    opened = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)  # Thu 10:30
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(market_data, opened - timedelta(days=1), now)

    decision_id = _seed_decision(session_factory, opened)
    with session_factory() as session:
        session.add(
            Position(
                symbol="MSFT",
                origin="agent",
                entry_decision_id=decision_id,
                qty=3,
                avg_entry_price=420,
                current_stop_price=410,
                opened_at=_naive(opened),
            )
        )
        session.commit()

    with session_factory() as session:
        worker.fill(session, now)
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    # Same window the worker read: cached bars with start <= now.
    bars = market_data.get_5m_bars("MSFT", opened, now)
    assert outcome.best_price == max(b.high for b in bars)
    assert outcome.worst_price == min(b.low for b in bars)
    assert outcome.best_price_at is not None
    assert outcome.worst_price_at is not None


@pytest.mark.parametrize(
    ("closed_offset", "missing", "expected_visit"),
    [
        (timedelta(microseconds=-1), None, False),
        (timedelta(0), None, True),
        (timedelta(microseconds=1), None, True),
        (None, None, True),
        (timedelta(days=-1), "best_price", True),
        (timedelta(days=-1), "worst_price", True),
        (timedelta(days=-1), "first_touch", True),
        (timedelta(days=-1), "outcome", False),
    ],
)
def test_best_worst_only_visits_unsettled_or_incomplete_trades(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch,
    closed_offset: timedelta | None, missing: str | None, expected_visit: bool,
) -> None:
    now = datetime(2026, 10, 15, 18, tzinfo=UTC)
    opened = now - timedelta(days=2)
    worker, _ = _worker(session_factory, FakeClock(now))
    decision_id = _seed_decision(session_factory, opened)
    _seed_position(session_factory, decision_id, opened)
    _seed_bars(session_factory, [(opened + timedelta(minutes=5), 420, 440, 400, 420)])
    with session_factory() as session:
        position = session.execute(select(Position)).scalar_one()
        position.closed_at = (
            _naive(now - DELAY + closed_offset) if closed_offset is not None else None
        )
        outcome = session.execute(select(Outcome)).scalar_one()
        outcome.price_1h = outcome.price_1session = outcome.price_5sessions = Decimal("420")
        outcome.best_price = Decimal("430")
        outcome.worst_price = Decimal("410")
        outcome.first_touch = "neither"
        if missing == "outcome":
            session.delete(outcome)
        elif missing is not None:
            setattr(outcome, missing, None)
        session.commit()

    visited: list[int] = []
    original = worker._update_best_worst

    def track(session: Session, position: Position, at: datetime) -> bool:
        visited.append(position.id)
        return original(session, position, at)

    monkeypatch.setattr(worker, "_update_best_worst", track)
    with session_factory() as session:
        report = worker.fill(session, now)
        session.commit()
    assert bool(visited) == expected_visit
    assert report.best_worst == int(expected_visit)
    if missing != "outcome":
        outcome = _outcome(session_factory, decision_id)
        assert outcome.best_price == Decimal("440" if expected_visit else "430")
        assert outcome.worst_price == Decimal("400" if expected_visit else "410")
        assert outcome.first_touch == ("stop" if expected_visit else "neither")


def test_pending_outcome_does_not_load_unused_decision_payloads(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    worker, _ = _worker(session_factory, FakeClock(now))
    _seed_decision(session_factory, now)
    unloaded: list[set[str]] = []

    def loaded(session: Session, instance: object) -> None:
        if isinstance(instance, Decision):
            unloaded.append(set(inspect(instance).unloaded))

    with session_factory() as session:
        event.listen(session, "loaded_as_persistent", loaded)
        report = worker.fill(session, now)
        assert report.filled == report.pending == 0
    assert len(unloaded) == 1
    assert {"input_snapshot", "model_output", "rules_result", "validation_result"} <= unloaded[0]


async def test_best_worst_not_tracked_for_skips(
    session_factory: sessionmaker[Session],
) -> None:
    """A decision without a position gets no best/worst."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    clock = FakeClock(decision_time + timedelta(hours=2))
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(market_data, decision_time, clock.now())

    decision_id = _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    assert outcome.price_1h is not None
    assert outcome.best_price is None
    assert outcome.worst_price is None


# ----------------------------------------------------------------------
# first touch: stop vs target


def _seed_position(
    session_factory: sessionmaker[Session],
    decision_id: int,
    opened: datetime,
    *,
    stop: float = 410,
    target: float | None = 435,
) -> None:
    with session_factory() as session:
        session.add(
            Position(
                symbol="MSFT",
                origin="agent",
                entry_decision_id=decision_id,
                qty=3,
                avg_entry_price=420,
                initial_stop=stop,
                current_stop_price=stop,
                target_price=target,
                opened_at=_naive(opened),
            )
        )
        session.commit()


def _seed_bars(
    session_factory: sessionmaker[Session],
    bars: list[tuple[datetime, float, float, float, float]],
) -> None:
    """Insert cached 5m bars directly: (start, open, high, low, close)."""
    with session_factory() as session:
        for start, open_, high, low, close in bars:
            session.add(
                Bar5mRow(
                    symbol="MSFT",
                    start=_naive(start),
                    open=Decimal(str(open_)),
                    high=Decimal(str(high)),
                    low=Decimal(str(low)),
                    close=Decimal(str(close)),
                    volume=1000,
                    feed="test",
                )
            )
        session.commit()


async def test_first_touch_stop(
    session_factory: sessionmaker[Session],
) -> None:
    """The bar that dips to the stop first wins over a later target touch."""
    opened = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_decision(session_factory, opened)
    _seed_position(session_factory, decision_id, opened)
    _seed_bars(
        session_factory,
        [
            (datetime(2026, 10, 15, 15, 0, tzinfo=UTC), 420, 421, 409.5, 415),
            # Later bar reaches the target — the stop still counts as first.
            (datetime(2026, 10, 15, 16, 0, tzinfo=UTC), 415, 436, 414, 435),
        ],
    )
    with session_factory() as session:
        worker.fill(session, now)
        session.commit()

    assert _outcome(session_factory, decision_id).first_touch == "stop"


async def test_first_touch_target(
    session_factory: sessionmaker[Session],
) -> None:
    """A bar reaching the target before any stop touch records 'target'."""
    opened = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_decision(session_factory, opened)
    _seed_position(session_factory, decision_id, opened)
    _seed_bars(
        session_factory,
        [
            (datetime(2026, 10, 15, 15, 0, tzinfo=UTC), 420, 436, 419, 435),
            (datetime(2026, 10, 15, 16, 0, tzinfo=UTC), 415, 420, 409.5, 411),
        ],
    )
    with session_factory() as session:
        worker.fill(session, now)
        session.commit()

    assert _outcome(session_factory, decision_id).first_touch == "target"


async def test_first_touch_neither(
    session_factory: sessionmaker[Session],
) -> None:
    """Bars that stay inside the stop/target range record 'neither'."""
    opened = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_decision(session_factory, opened)
    _seed_position(session_factory, decision_id, opened)
    _seed_bars(
        session_factory,
        [
            (datetime(2026, 10, 15, 15, 0, tzinfo=UTC), 420, 424, 416, 422),
            (datetime(2026, 10, 15, 16, 0, tzinfo=UTC), 422, 430, 418, 429),
        ],
    )
    with session_factory() as session:
        worker.fill(session, now)
        session.commit()

    assert _outcome(session_factory, decision_id).first_touch == "neither"


async def test_first_touch_same_bar_counts_as_stop(
    session_factory: sessionmaker[Session],
) -> None:
    """A bar touching both levels counts as a stop touch."""
    opened = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_decision(session_factory, opened)
    _seed_position(session_factory, decision_id, opened)
    _seed_bars(
        session_factory,
        [(datetime(2026, 10, 15, 15, 0, tzinfo=UTC), 420, 440, 405, 425)],
    )
    with session_factory() as session:
        worker.fill(session, now)
        session.commit()

    assert _outcome(session_factory, decision_id).first_touch == "stop"


async def test_first_touch_ignores_target_after_leg_ended(
    session_factory: sessionmaker[Session],
) -> None:
    """The position's stored target_price must not apply after its leg
    was cancelled — a bar reaching the old level records no target."""
    opened = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)
    cancelled_at = datetime(2026, 10, 15, 15, 30, tzinfo=UTC)
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_decision(session_factory, opened)
    _seed_position(session_factory, decision_id, opened)
    with session_factory() as session:
        session.add(
            Order(
                alpaca_order_id="tp-msft",
                client_order_id=None,
                symbol="MSFT",
                decision_id=decision_id,
                side="sell",
                order_type="limit",
                leg="target",
                qty=3,
                limit_price=Decimal("435"),
                status="canceled",
                created_at=_naive(opened),
                active_until=_naive(cancelled_at),
            )
        )
        session.commit()
    _seed_bars(
        session_factory,
        [
            # Reaches the old target only after the leg was dead.
            (datetime(2026, 10, 15, 16, 0, tzinfo=UTC), 420, 436, 415, 430),
        ],
    )
    with session_factory() as session:
        worker.fill(session, now)
        session.commit()

    assert _outcome(session_factory, decision_id).first_touch == "neither"


async def test_first_touch_uses_the_live_target_leg_price(
    session_factory: sessionmaker[Session],
) -> None:
    """With a target-leg row on file its limit is the level that counts —
    not the position's stored target_price."""
    opened = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_decision(session_factory, opened)
    _seed_position(session_factory, decision_id, opened)  # target 435
    with session_factory() as session:
        # The working leg sits higher than the stored target.
        session.add(
            Order(
                alpaca_order_id="tp-msft",
                client_order_id=None,
                symbol="MSFT",
                decision_id=decision_id,
                side="sell",
                order_type="limit",
                leg="target",
                qty=3,
                limit_price=Decimal("438"),
                status="new",
                created_at=_naive(opened),
            )
        )
        session.commit()
    _seed_bars(
        session_factory,
        [
            # Above the stored 435, below the live leg's 438.
            (datetime(2026, 10, 15, 16, 0, tzinfo=UTC), 420, 436, 415, 430),
        ],
    )
    with session_factory() as session:
        worker.fill(session, now)
        session.commit()

    assert _outcome(session_factory, decision_id).first_touch == "neither"


async def test_first_touch_none_for_skips(
    session_factory: sessionmaker[Session],
) -> None:
    """A decision without a position keeps first_touch NULL."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    clock = FakeClock(decision_time + timedelta(hours=2))
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(market_data, decision_time, clock.now())

    decision_id = _seed_decision(session_factory, decision_time)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    assert _outcome(session_factory, decision_id).first_touch is None


# ----------------------------------------------------------------------
# equity snapshots


async def test_equity_snapshot_carries_benchmark_and_fx(
    session_factory: sessionmaker[Session],
) -> None:
    """Snapshots store the same-day SPY buy-and-hold value and EUR/USD."""
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(market_data, now - timedelta(days=3), now)
    await market_data.refresh_daily(now.astimezone(NY_TZ).date(), now)
    market_data.latest_fx = await market_data.sources.fx.fetch_fx(
        now.date()
    )

    with session_factory() as session:
        session.add(
            Run(
                started_at=_naive(now - timedelta(days=9)),
                starting_equity=10000,
                spy_close=550,
            )
        )
        session.commit()
        worker.write_equity_snapshot(
            session, equity=Decimal(10200), cash=Decimal(8000)
        )
        session.commit()

    spy = worker._spy_price(now)
    assert spy is not None
    with session_factory() as session:
        snapshot = session.execute(select(EquitySnapshot)).scalar_one()
    assert snapshot.spy_benchmark_value is not None
    assert float(snapshot.spy_benchmark_value) == pytest.approx(
        float(Decimal(10000) * spy / Decimal(550)), rel=1e-6
    )
    assert snapshot.eur_usd_rate == Decimal("1.12")  # FakeFXSource


# ----------------------------------------------------------------------
# option touches (docs/SPEC.md, Option outcomes)


def _seed_option_decision(
    session_factory: sessionmaker[Session],
    scheduled_utc: datetime,
    *,
    options: list[dict[str, object]],
    entry_limit: float | None = 420.0,
) -> int:
    """A candidate decision with v2+-shaped options: no per-option
    entry — the limit lives in the snapshot's constraints."""
    with session_factory() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=_naive(scheduled_utc),
            started_at=_naive(scheduled_utc),
            finished_at=_naive(scheduled_utc + timedelta(minutes=1)),
            status="done",
        )
        session.add(cycle)
        session.flush()
        snapshot: dict[str, object] = {"symbol": "MSFT", "price": 420.0}
        if entry_limit is not None:
            snapshot["constraints"] = {"entry_limit": entry_limit}
        decision = Decision(
            cycle_id=cycle.id,
            symbol="MSFT",
            context="candidate",
            input_snapshot=snapshot,
            rules_result={"status": "pass", "reason": None, "checks": []},
            model_output={"action": "skip", "confidence": 0.6},
            validation_result={
                "valid": True,
                "model_action": "skip",
                "final_action": "skip",
                "changed_by_code": False,
            },
            final_action="skip",
            prompt_version="decision.v4",
            prompt_hash="abc",
            model="fake-decision",
            options={"options_version": "v4", "options": options},
        )
        session.add(decision)
        session.flush()
        session.add(Outcome(decision_id=decision.id))
        session.commit()
        return decision.id


async def test_option_touches_use_the_shared_entry_limit(
    session_factory: sessionmaker[Session],
) -> None:
    """Options carry no per-option entry: the snapshot's constraints
    supply the limit the fill check measures (SPEC, Option outcomes)."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)  # Thu 10:46
    target = datetime(2026, 10, 22, 14, 46, tzinfo=UTC)  # 5 sessions on
    clock = FakeClock(target + DELAY)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_option_decision(
        session_factory,
        decision_time,
        options=[
            {"id": "A", "stop": 415.0, "target": 430.0, "rr": 3.0, "risk": 5.0},
            {"id": "B", "stop": 418.0, "target": 435.0, "rr": 7.5, "risk": 2.0},
            {"id": "C", "stop": 419.6, "target": 432.0, "rr": 30.0, "risk": 0.4},
        ],
    )
    _seed_bars(
        session_factory,
        [
            # Fill bar inside the 10-minute window: low reaches the 420
            # limit — and dips under C's 419.6 stop, which must NOT count
            # (only bars after the fill bar decide).
            (datetime(2026, 10, 15, 14, 50, tzinfo=UTC), 421, 422, 419.5, 421),
            # Later bar: reaches A's 430 target, never B's 418 stop or
            # 435 target; stays above C's 419.6 stop and below its target.
            (datetime(2026, 10, 15, 15, 30, tzinfo=UTC), 421, 431, 419.8, 430.5),
            # Closed at the 5-session target so the fill runs this pass.
            (datetime(2026, 10, 22, 14, 40, tzinfo=UTC), 425, 426, 424, 425),
        ],
    )
    _seed_window_coverage(session_factory)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    outcome = _outcome(session_factory, decision_id)
    assert outcome.price_5sessions is not None
    assert outcome.option_touches == {
        "A": "target",
        "B": "neither",
        "C": "neither",
    }


async def test_option_touches_not_filled_past_the_window(
    session_factory: sessionmaker[Session],
) -> None:
    """No bar's low reaching the limit inside 10 minutes is not_filled —
    a dip after the window does not count."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    target = datetime(2026, 10, 22, 14, 46, tzinfo=UTC)
    clock = FakeClock(target + DELAY)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_option_decision(
        session_factory,
        decision_time,
        options=[
            {"id": "A", "stop": 415.0, "target": 430.0, "rr": 3.0, "risk": 5.0},
        ],
    )
    _seed_bars(
        session_factory,
        [
            # Inside the window the low stays above the 420 limit.
            (datetime(2026, 10, 15, 14, 50, tzinfo=UTC), 421, 422, 420.5, 421),
            # Past the window a low reaches the limit — too late.
            (datetime(2026, 10, 15, 15, 0, tzinfo=UTC), 421, 422, 419, 420),
            (datetime(2026, 10, 22, 14, 40, tzinfo=UTC), 425, 426, 424, 425),
        ],
    )
    _seed_window_coverage(session_factory)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    assert _outcome(session_factory, decision_id).option_touches == {
        "A": "not_filled"
    }


async def test_option_touches_fall_back_to_stop_plus_risk(
    session_factory: sessionmaker[Session],
) -> None:
    """Without a constraints limit, stop + stored risk (limit − stop per
    share) still reconstructs the option's fill price."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    target = datetime(2026, 10, 22, 14, 46, tzinfo=UTC)
    clock = FakeClock(target + DELAY)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_option_decision(
        session_factory,
        decision_time,
        entry_limit=None,  # no constraints block at all
        options=[
            # risk 5.0 + stop 415.0 → the 420 limit.
            {"id": "A", "stop": 415.0, "target": 430.0, "rr": 3.0, "risk": 5.0},
        ],
    )
    _seed_bars(
        session_factory,
        [
            (datetime(2026, 10, 15, 14, 50, tzinfo=UTC), 421, 422, 419.5, 421),
            (datetime(2026, 10, 15, 15, 30, tzinfo=UTC), 421, 431, 419, 430.5),
            (datetime(2026, 10, 22, 14, 40, tzinfo=UTC), 425, 426, 424, 425),
        ],
    )
    _seed_window_coverage(session_factory)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    assert _outcome(session_factory, decision_id).option_touches == {
        "A": "target"
    }


async def test_option_touches_null_without_a_limit(
    session_factory: sessionmaker[Session],
) -> None:
    """No per-option entry, no constraints limit and no risk: the touch
    stays null — missing data, never a guessed fill."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    target = datetime(2026, 10, 22, 14, 46, tzinfo=UTC)
    clock = FakeClock(target + DELAY)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_option_decision(
        session_factory,
        decision_time,
        entry_limit=None,
        options=[
            {"id": "A", "stop": 415.0, "target": 430.0},
        ],
    )
    _seed_bars(
        session_factory,
        [
            (datetime(2026, 10, 15, 14, 50, tzinfo=UTC), 421, 422, 419.5, 421),
            (datetime(2026, 10, 22, 14, 40, tzinfo=UTC), 425, 426, 424, 425),
        ],
    )
    _seed_window_coverage(session_factory)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()

    assert _outcome(session_factory, decision_id).option_touches == {"A": None}


async def test_equity_snapshot_backfills_run_spy_close(
    session_factory: sessionmaker[Session],
) -> None:
    """A run started without a SPY close anchors on the first snapshot."""
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, market_data = _worker(session_factory, clock)
    await _cache_bars(market_data, now - timedelta(days=3), now)

    with session_factory() as session:
        run = Run(
            started_at=_naive(now - timedelta(days=9)),
            starting_equity=10000,
            spy_close=None,
        )
        session.add(run)
        session.commit()
        snapshot = worker.write_equity_snapshot(
            session, equity=Decimal(10200), cash=Decimal(8000)
        )
        session.commit()
        run_id = run.id
        benchmark = snapshot.spy_benchmark_value

    assert benchmark is not None
    with session_factory() as session:
        row = session.get(Run, run_id)
        assert row is not None and row.spy_close is not None


async def test_equity_snapshot_empty_bars(
    session_factory: sessionmaker[Session],
) -> None:
    """No data at all: the snapshot still stores equity and cash."""
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, _market_data = _worker(session_factory, clock)

    with session_factory() as session:
        snapshot = worker.write_equity_snapshot(
            session, equity=Decimal(10000), cash=Decimal(10000)
        )
        assert snapshot.spy_benchmark_value is None
        assert snapshot.equity == 10000
        session.commit()

    with session_factory() as session:
        rows = session.execute(select(EquitySnapshot)).scalars().all()
    assert len(rows) == 1


# ----------------------------------------------------------------------
# option touches: fetch missing bars once per session, then give up
# (docs/SPEC.md, Option outcomes — Missing bars; item 058)


class _EmptyBarSource(FakeBarSource):
    """A bar source that never returns bars — the window stays uncovered."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def fetch_5m_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> DataResult[list[Bar5m]]:
        self.calls += 1
        return DataResult(data=[], source="fake")


def _seed_price_bars(
    session_factory: sessionmaker[Session],
) -> None:
    """Only the bars the 1h / 1-session / 5-session prices need — the
    touches window between them stays uncovered."""
    _seed_bars(
        session_factory,
        [
            (datetime(2026, 10, 15, 15, 40, tzinfo=UTC), 420, 421, 419, 420),
            (datetime(2026, 10, 16, 14, 40, tzinfo=UTC), 420, 421, 419, 420),
            (datetime(2026, 10, 22, 14, 40, tzinfo=UTC), 425, 426, 424, 425),
        ],
    )


_ONE_OPTION = [
    {"id": "A", "stop": 415.0, "target": 430.0, "rr": 3.0, "risk": 5.0}
]


def _seed_window_coverage(session_factory: sessionmaker[Session]) -> None:
    """One harmless bar per session of the Oct 15→22 touches window, so
    the stored bars cover it (item 058). Prices stay between every
    seeded stop and target, so they never change a touch verdict."""
    for day in (
        "2026-10-15",
        "2026-10-16",
        "2026-10-19",
        "2026-10-20",
        "2026-10-21",
        "2026-10-22",
    ):
        _seed_bars(
            session_factory,
            [
                (
                    datetime.fromisoformat(f"{day}T13:35:00+00:00"),
                    425,
                    426,
                    424,
                    425,
                )
            ],
        )


async def test_option_touches_fetch_missing_window_then_fill(
    session_factory: sessionmaker[Session],
) -> None:
    """An uncovered window is fetched once through the data source and
    the touches fill on the next pass (item 058)."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    target = datetime(2026, 10, 22, 14, 46, tzinfo=UTC)
    clock = FakeClock(target + DELAY)
    worker, _market_data = _worker(session_factory, clock)

    decision_id = _seed_option_decision(
        session_factory,
        decision_time,
        entry_limit=400.0,  # below every fake price → "not_filled"
        options=_ONE_OPTION,
    )
    _seed_price_bars(session_factory)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()
    # Prices landed; the touches window is uncovered → still NULL.
    assert _outcome(session_factory, decision_id).option_touches is None

    report = await worker.prefetch_touches(clock.now())
    assert report.decisions == 1
    assert report.shadows == 0
    # Once per session: a second pass the same day fetches nothing.
    again = await worker.prefetch_touches(clock.now())
    assert again.decisions == 0

    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()
    assert _outcome(session_factory, decision_id).option_touches == {
        "A": "not_filled"
    }


async def test_option_touches_empty_fetch_gives_up_after_sessions(
    session_factory: sessionmaker[Session],
) -> None:
    """A fetch that returns nothing is retried at most once per session;
    past ``touches_give_up_sessions`` every option is "unavailable" and
    the row is done so its verdict can draft (item 058)."""
    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    bars = _EmptyBarSource()
    # First session after the 5-session target (Thu 22 Oct).
    clock = FakeClock(datetime(2026, 10, 23, 15, 0, tzinfo=UTC))
    worker, _market_data = _worker(session_factory, clock, bars=bars)

    decision_id = _seed_option_decision(
        session_factory,
        decision_time,
        entry_limit=400.0,
        options=_ONE_OPTION,
    )
    _seed_price_bars(session_factory)
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()
    assert _outcome(session_factory, decision_id).option_touches is None

    await worker.prefetch_touches(clock.now())
    assert bars.calls == 1
    await worker.prefetch_touches(clock.now())  # same session: no refetch
    assert bars.calls == 1
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()
    # One session after the target — still waiting, not yet unavailable.
    assert _outcome(session_factory, decision_id).option_touches is None

    # Second session after: fetched again, still nothing.
    clock.set(datetime(2026, 10, 26, 15, 0, tzinfo=UTC))
    await worker.prefetch_touches(clock.now())
    assert bars.calls == 2
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()
    assert _outcome(session_factory, decision_id).option_touches is None

    # Third session after (more than touches_give_up_sessions = 2):
    # every option without a result is "unavailable".
    clock.set(datetime(2026, 10, 27, 15, 0, tzinfo=UTC))
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()
    assert _outcome(session_factory, decision_id).option_touches == {
        "A": "unavailable"
    }
    # Done — no further fetch attempts.
    await worker.prefetch_touches(clock.now())
    assert bars.calls == 2


async def test_shadow_option_touches_fetch_and_unavailable(
    session_factory: sessionmaker[Session],
) -> None:
    """Shadow decisions get the same fetch-once / give-up path on their
    own ``option_touches`` column (item 058)."""
    from app.db.models import ShadowDecision, Strategy

    decision_time = datetime(2026, 10, 15, 14, 46, tzinfo=UTC)
    bars = _EmptyBarSource()
    clock = FakeClock(datetime(2026, 10, 27, 15, 0, tzinfo=UTC))
    worker, _market_data = _worker(session_factory, clock, bars=bars)

    with session_factory() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=_naive(decision_time),
            started_at=_naive(decision_time),
            finished_at=_naive(decision_time + timedelta(minutes=1)),
            status="done",
        )
        session.add(cycle)
        session.flush()
        strategy = Strategy(
            name="shadow",
            kind="llm",
            prompt_version="decision.v1",
            model="fake",
            temperature=0.0,
            status="active",
            created_at=_naive(decision_time),
        )
        session.add(strategy)
        session.flush()
        session.add(
            ShadowDecision(
                strategy_id=strategy.id,
                cycle_id=cycle.id,
                symbol="MSFT",
                context="candidate",
                final_action="skip",
                options={"options": _ONE_OPTION},
                created_at=_naive(decision_time),
            )
        )
        session.commit()
        shadow_id = session.execute(select(ShadowDecision.id)).scalar_one()

    await worker.prefetch_touches(clock.now())
    assert bars.calls == 1
    with session_factory() as session:
        worker.fill(session, clock.now())
        session.commit()
    with session_factory() as session:
        row = session.get(ShadowDecision, shadow_id)
        assert row is not None
        assert row.option_touches == {"A": "unavailable"}
