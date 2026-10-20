"""Outcome worker: 1h / 1-session / 5-session prices and best/worst.

Targets follow the spec's clarified timing: "1 session" is the same New
York time on the next trading session, "5 sessions" the same time on the
5th; a target past the session's close (early close) falls back to the
close. Each price is the last closed 5-minute bar at or before the target,
stored with the bar's timestamp, and only once the SIP delay has passed.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

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
from app.data.models import Bar5m
from app.data.service import MarketDataService
from app.db.base import Base
from app.db.models import (
    Bar5m as Bar5mRow,
)
from app.db.models import (
    Cycle,
    Decision,
    EquitySnapshot,
    Outcome,
    Position,
    Run,
)
from app.engine.outcomes import OutcomeWorker
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

# SIP delay plus a minute, so fills are clearly past the delay.
DELAY = timedelta(minutes=16)


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _worker(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource | None = None,
) -> tuple[OutcomeWorker, MarketDataService]:
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["MSFT", "SPY"]
    calendar = calendar or FakeCalendarSource()
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
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
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
    await market_data.refresh_daily(now.astimezone(NY_TZ).date())
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
