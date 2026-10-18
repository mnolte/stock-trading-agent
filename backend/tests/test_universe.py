"""Universe pool and daily liquidity screen (docs/SPEC.md, Larger universe).

All fakes, no network: the bar source is scripted per symbol, the broker is
the in-memory fake and the clock is frozen. Each check gets a pass and a
fail case, plus the hysteresis, ranking and failure rules.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.broker.fake import FakeBroker
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
from app.data.models import Bar5m, CalendarDay, DailyBar, DataResult
from app.data.service import MarketDataService
from app.db.base import Base
from app.db.models import Event, Position, UniversePoolRow, UniverseScreenRow
from app.market.clock import NY_TZ, UTC, FakeClock
from app.universe.service import SYMBOL_RE, PoolError, UniverseService
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

# Thursday 2026-10-15, 08:30 New York — after the daily refresh, before the
# open, on a trading day.
SCREEN_TIME = datetime(2026, 10, 15, 12, 30, tzinfo=UTC)
SCREEN_DATE = date(2026, 10, 15)


class StubBars(FakeBarSource):
    """FakeBarSource with scripted daily bars and optional gaps."""

    def __init__(self, calendar: FakeCalendarSource) -> None:
        super().__init__(calendar)
        self.daily: dict[str, list[DailyBar]] = {}
        self.no_5m: set[str] = set()
        self.fail_all_daily = False
        self.daily_calls = 0

    async def fetch_daily_bars(
        self, symbol: str, start: date, end: date
    ) -> DataResult[list[DailyBar]]:
        self.daily_calls += 1
        if self.fail_all_daily:
            return DataResult(data=[], error="daily source down", source="fake")
        if symbol in self.daily:
            bars = [b for b in self.daily[symbol] if start <= b.date <= end]
            return DataResult(data=bars, source="fake")
        return await super().fetch_daily_bars(symbol, start, end)


class No5mBars(StubBars):
    async def fetch_5m_bars(
        self, symbol: str, start: datetime, end: datetime
    ) -> DataResult[list[Bar5m]]:
        if symbol in self.no_5m:
            return DataResult(data=[], source="fake")
        return await super().fetch_5m_bars(symbol, start, end)


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(SCREEN_TIME)


@pytest.fixture
def calendar() -> FakeCalendarSource:
    return FakeCalendarSource()


def make_config(
    tmp_path: Path,
    pool_rows: list[tuple[str, str]] | None = None,
    mode: str = "screened",
) -> AppConfig:
    config = AppConfig()
    config.env.mode = "simulated"
    config.yaml["universe"]["mode"] = mode
    if pool_rows is not None:
        pool_file = tmp_path / "pool.csv"
        pool_file.write_text(
            "symbol,sector\n"
            + "\n".join(f"{symbol},{sector}" for symbol, sector in pool_rows)
        )
        config.yaml["universe"]["pool_file"] = str(pool_file)
    return config


def make_service(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    config: AppConfig,
    bars: StubBars | None = None,
    broker: FakeBroker | None = None,
) -> tuple[UniverseService, MarketDataService, FakeBroker, StubBars]:
    bars = bars or StubBars(calendar)
    sources = DataSources(
        bars=bars,
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
    broker = broker or FakeBroker(clock)
    service = UniverseService(
        market_data,
        sources,
        calendar,
        broker,
        config,
        clock,
        session_factory=session_factory,
    )
    return service, market_data, broker, bars


def daily_series(
    symbol: str,
    sessions: list[CalendarDay],
    start_price: Decimal,
    end_price: Decimal,
    volume: int = 2_000_000,
) -> list[DailyBar]:
    """A linear ramp over the sessions → a known lookback return."""
    n = len(sessions)
    bars = []
    for i, s in enumerate(sessions):
        close = start_price + (end_price - start_price) * (
            Decimal(i) / max(n - 1, 1)
        )
        bars.append(
            DailyBar(
                symbol=symbol,
                date=s.date,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=volume,
            )
        )
    return bars


def next_trading_day(clock: FakeClock, calendar: FakeCalendarSource) -> datetime:
    """Advance the frozen clock to 08:30 NY of the next trading day."""
    day = clock.now().astimezone(NY_TZ).date() + timedelta(days=1)
    while calendar.session_for(day) is None:
        day += timedelta(days=1)
    when = datetime.combine(day, datetime.min.time(), tzinfo=NY_TZ) + timedelta(
        hours=8, minutes=30
    )
    clock.set(when)
    return when


def screen_rows(
    session_factory: sessionmaker[Session],
) -> dict[str, UniverseScreenRow]:
    with session_factory() as session:
        latest = session.execute(
            select(UniverseScreenRow.session_date).distinct()
        ).scalars().all()
        latest_date = max(latest) if latest else None
        rows = session.execute(
            select(UniverseScreenRow).where(
                UniverseScreenRow.session_date == latest_date
            )
        ).scalars().all()
    return {r.symbol: r for r in rows}


def _checks(row: UniverseScreenRow) -> dict[str, object]:
    return row.check_results or {}


async def test_seed_loads_pool_csv_once(session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(
        tmp_path, [("AAPL", "Technology"), ("JPM", "Financials")]
    )
    service, *_ = make_service(session_factory, clock, calendar, config)
    assert service.ensure_pool_loaded() == 2
    # Seeding runs once: edits are never overwritten by a re-seed.
    service.remove_symbol("JPM")
    assert service.ensure_pool_loaded() == 0
    with session_factory() as session:
        rows = session.execute(select(UniversePoolRow)).scalars().all()
    assert {r.symbol for r in rows} == {"AAPL", "JPM"}
    assert all(r.source == "default" for r in rows)
    assert next(r for r in rows if r.symbol == "JPM").removed_at is not None


async def test_screen_all_checks_pass(session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(
        tmp_path, [("AAPL", "Technology"), ("MSFT", "Technology")]
    )
    service, *_ = make_service(session_factory, clock, calendar, config)
    result = await service.run_screen()
    assert result["pool"] == 2 and result["eligible"] == 2
    rows = screen_rows(session_factory)
    assert {r.symbol for r in rows.values()} == {"AAPL", "MSFT"}
    assert all(r.status == "eligible" for r in rows.values())
    assert all(r.failed_in_a_row == 0 for r in rows.values())
    assert {r.rank for r in rows.values()} == {1, 2}


async def test_screen_min_price_fails(session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, [("AAPL", "Technology")])
    bars = StubBars(calendar)
    service, market_data, *_ = make_service(
        session_factory, clock, calendar, config, bars=bars
    )
    sessions = market_data.recent_sessions(SCREEN_DATE, 260)
    bars.daily["AAPL"] = daily_series(
        "AAPL", sessions, Decimal("5"), Decimal("5")
    )
    await service.run_screen()
    row = screen_rows(session_factory)["AAPL"]
    assert row.status == "excluded"
    assert _checks(row)["failed_check"] == "min_price"


async def test_screen_dollar_volume_fails(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, [("AAPL", "Technology")])
    bars = StubBars(calendar)
    service, market_data, *_ = make_service(
        session_factory, clock, calendar, config, bars=bars
    )
    sessions = market_data.recent_sessions(SCREEN_DATE, 260)
    # $50 × 10k shares ≈ $0.5M average dollar volume — far below $100M.
    bars.daily["AAPL"] = daily_series(
        "AAPL", sessions, Decimal("50"), Decimal("50"), volume=10_000
    )
    await service.run_screen()
    row = screen_rows(session_factory)["AAPL"]
    assert row.status == "excluded"
    assert _checks(row)["failed_check"] == "dollar_volume"


async def test_screen_tradable_fails(session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(
        tmp_path, [("AAPL", "Technology"), ("MSFT", "Technology")]
    )
    service, _, broker, _ = make_service(session_factory, clock, calendar, config)
    broker.non_tradable.add("AAPL")  # asset found, tradable=False
    broker.untradable.add("MSFT")  # asset not found at the broker
    await service.run_screen()
    rows = screen_rows(session_factory)
    assert rows["AAPL"].status == "excluded"
    assert _checks(rows["AAPL"])["failed_check"] == "tradable"
    assert rows["MSFT"].status == "excluded"
    assert _checks(rows["MSFT"])["failed_check"] == "tradable"


async def test_one_share_check_uses_equity_at_screen_time(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    """A $3,000 stock fails at $10k equity but passes at $20k — the check
    uses the equity at screen time, not the run's starting equity."""
    config = make_config(tmp_path, [("AZO", "Consumer Discretionary")])
    bars = StubBars(calendar)
    service, market_data, broker, _ = make_service(
        session_factory, clock, calendar, config, bars=bars
    )
    sessions = market_data.recent_sessions(SCREEN_DATE, 260)
    bars.daily["AZO"] = daily_series(
        "AZO", sessions, Decimal("3000"), Decimal("3000")
    )
    # equity $10,000 → max position $2,000 < one $3,000 share.
    await service.run_screen()
    row = screen_rows(session_factory)["AZO"]
    assert row.status == "excluded"
    assert _checks(row)["failed_check"] == "one_share"
    assert _checks(row)["max_position_usd"] == "2000.00"

    # Equity doubles before the next screen (start_cash stays $10,000 —
    # a stale value would still exclude it).
    broker.cash = Decimal("20000")
    next_trading_day(clock, calendar)
    await service.run_screen()
    row = screen_rows(session_factory)["AZO"]
    assert row.status == "eligible"
    assert _checks(row)["max_position_usd"] == "4000.00"


async def test_warming_up_then_eligible(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(
        tmp_path, [("AAPL", "Technology"), ("ANET", "Technology")]
    )
    bars = No5mBars(calendar)
    bars.no_5m.add("ANET")
    service, *_ = make_service(session_factory, clock, calendar, config, bars=bars)
    await service.run_screen()
    rows = screen_rows(session_factory)
    assert rows["ANET"].status == "warming_up"
    assert _checks(rows["ANET"])["failed_check"] == "history"
    assert rows["AAPL"].status == "eligible"

    # The warm-up fetch lands; the next screen makes it eligible.
    bars.no_5m.clear()
    next_trading_day(clock, calendar)
    await service.run_screen()
    rows = screen_rows(session_factory)
    assert rows["ANET"].status == "eligible"
    assert rows["ANET"].failed_in_a_row == 0


async def test_hysteresis_fail_fail_pass_then_three_fails(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    """An eligible symbol only drops out after 3 failed screens in a row;
    a pass resets the streak."""
    config = make_config(tmp_path, [("AAPL", "Technology")])
    service, _, broker, _ = make_service(session_factory, clock, calendar, config)
    await service.run_screen()
    assert screen_rows(session_factory)["AAPL"].status == "eligible"

    broker.non_tradable.add("AAPL")
    next_trading_day(clock, calendar)  # fail 1: still in, "1 fail"
    await service.run_screen()
    row = screen_rows(session_factory)["AAPL"]
    assert row.status == "eligible" and row.failed_in_a_row == 1

    next_trading_day(clock, calendar)  # fail 2: still in, "2 fails"
    await service.run_screen()
    row = screen_rows(session_factory)["AAPL"]
    assert row.status == "eligible" and row.failed_in_a_row == 2

    broker.non_tradable.clear()  # pass: streak resets, still eligible
    next_trading_day(clock, calendar)
    await service.run_screen()
    row = screen_rows(session_factory)["AAPL"]
    assert row.status == "eligible" and row.failed_in_a_row == 0

    broker.non_tradable.add("AAPL")
    for expected in (1, 2):
        next_trading_day(clock, calendar)
        await service.run_screen()
        row = screen_rows(session_factory)["AAPL"]
        assert row.status == "eligible" and row.failed_in_a_row == expected
    next_trading_day(clock, calendar)  # fail 3: drops out
    await service.run_screen()
    row = screen_rows(session_factory)["AAPL"]
    assert row.status == "excluded"
    assert row.failed_in_a_row == 3


async def test_new_symbol_becomes_eligible_on_first_pass(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    """A warming/excluded symbol joins the list on its first passing screen —
    no hysteresis on the way in."""
    config = make_config(
        tmp_path, [("AAPL", "Technology"), ("MSFT", "Technology")]
    )
    service, _, broker, _ = make_service(session_factory, clock, calendar, config)
    broker.non_tradable.add("MSFT")
    await service.run_screen()
    assert screen_rows(session_factory)["MSFT"].status == "excluded"
    broker.non_tradable.clear()
    next_trading_day(clock, calendar)
    await service.run_screen()
    row = screen_rows(session_factory)["MSFT"]
    assert row.status == "eligible" and row.failed_in_a_row == 0


async def test_rank_order_and_dollar_volume_tiebreak(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    """Rank = 20-session return minus SPY's; equal ranks break by dollar volume."""
    config = make_config(
        tmp_path,
        [
            ("SPY", "Index"),
            ("AAA", "Technology"),
            ("BBB", "Technology"),
            ("CCC", "Technology"),
        ],
    )
    bars = StubBars(calendar)
    service, market_data, *_ = make_service(
        session_factory, clock, calendar, config, bars=bars
    )
    sessions = market_data.recent_sessions(SCREEN_DATE, 260)
    flat = Decimal("100")
    bars.daily["SPY"] = daily_series("SPY", sessions, flat, flat)
    bars.daily["AAA"] = daily_series(
        "AAA", sessions, flat, Decimal("110")
    )  # +10%
    bars.daily["BBB"] = daily_series(
        "BBB", sessions, flat, Decimal("105"), volume=1_500_000
    )  # +5%, lower $vol
    bars.daily["CCC"] = daily_series(
        "CCC", sessions, flat, Decimal("105"), volume=3_000_000
    )  # +5%, higher $vol
    await service.run_screen()
    rows = screen_rows(session_factory)
    assert rows["AAA"].rank == 1
    # Same return vs SPY as BBB — the bigger dollar volume wins the tie.
    assert rows["CCC"].rank == 2
    assert rows["BBB"].rank == 3
    assert rows["SPY"].rank == 4
    assert rows["AAA"].return_vs_spy is not None
    assert rows["CCC"].return_vs_spy is not None
    assert rows["AAA"].return_vs_spy > rows["CCC"].return_vs_spy


async def test_failed_screen_keeps_previous_list(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    """A data error keeps the previous eligible list and raises the banner."""
    config = make_config(
        tmp_path, [("AAPL", "Technology"), ("MSFT", "Technology")]
    )
    bars = StubBars(calendar)
    service, *_ = make_service(session_factory, clock, calendar, config, bars=bars)
    await service.run_screen()
    rows = screen_rows(session_factory)
    assert {r.status for r in rows.values()} == {"eligible"}

    bars.fail_all_daily = True
    next_trading_day(clock, calendar)
    result = await service.run_screen()
    assert "failed" in result

    # The list in effect is still the previous screen's.
    rows = screen_rows(session_factory)
    assert {r.status for r in rows.values()} == {"eligible"}
    assert service.last_screen_failed()
    with session_factory() as session:
        event = session.execute(
            select(Event)
            .where(Event.event_type == "universe_screen_failed")
            .order_by(Event.id.desc())
        ).scalars().first()
    assert event is not None and event.level == "warning"


async def test_screen_skips_non_trading_days(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, [("AAPL", "Technology")])
    service, *_ = make_service(session_factory, clock, calendar, config)
    clock.set(datetime(2026, 10, 17, 12, 30, tzinfo=UTC))  # Saturday
    result = await service.run_screen()
    assert result == {"skipped": "not a trading day"}


async def test_pending_settings_apply_at_next_screen(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    """A saved threshold applies at the next screen even before a cycle
    promotes the pending version."""
    config = make_config(tmp_path, [("AAPL", "Technology")])
    service, *_ = make_service(session_factory, clock, calendar, config)
    await service.run_screen()
    assert screen_rows(session_factory)["AAPL"].status == "eligible"

    tree = config.effective()
    tree["universe"]["screen"]["min_price"] = 99999
    config.set_pending(tree, 2)
    next_trading_day(clock, calendar)
    await service.run_screen()
    row = screen_rows(session_factory)["AAPL"]
    # Previously eligible → hysteresis keeps it for this fail streak…
    assert row.status == "eligible" and row.failed_in_a_row == 1
    # …but the raw check used the pending threshold.
    assert _checks(row)["min_price"] == "fail"


async def test_add_symbol_checks(session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, [("AAPL", "Technology")])
    service, _, broker, _ = make_service(session_factory, clock, calendar, config)
    service.ensure_pool_loaded()

    row = await service.add_symbol("PANW", "Technology")
    assert row.symbol == "PANW" and row.sector == "Technology"

    with pytest.raises(PoolError, match="already in the pool"):
        await service.add_symbol("PANW", "Technology")
    with pytest.raises(PoolError, match="not an allowed sector"):
        await service.add_symbol("AVAV", "Space")
    broker.untradable.add("ZZZZ")
    with pytest.raises(PoolError, match="not an active, tradable"):
        await service.add_symbol("ZZZZ", "Technology")
    with pytest.raises(PoolError, match="not a valid"):
        await service.add_symbol("not a ticker", "Technology")


async def test_import_csv_rejects_unknown_sector(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, [("AAPL", "Technology")])
    service, *_ = make_service(session_factory, clock, calendar, config)
    service.ensure_pool_loaded()
    with pytest.raises(PoolError) as exc:
        await service.import_csv("PANW,Technology\nXOM,Space\n")
    assert "unknown sector" in str(exc.value)
    # Rejected atomically: nothing from the file landed.
    with session_factory() as session:
        rows = session.execute(
            select(UniversePoolRow).where(UniversePoolRow.removed_at.is_(None))
        ).scalars().all()
    assert {r.symbol for r in rows} == {"AAPL"}


async def test_import_csv_adds_and_skips(session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, [("AAPL", "Technology")])
    service, *_ = make_service(session_factory, clock, calendar, config)
    service.ensure_pool_loaded()
    added, skipped = await service.import_csv(
        "symbol,sector\nPANW,Technology\nAAPL,Technology\nJPM,Financials\n"
    )
    assert added == ["PANW", "JPM"]
    assert skipped == 1
    with session_factory() as session:
        rows = session.execute(
            select(UniversePoolRow).where(
                UniversePoolRow.removed_at.is_(None),
                UniversePoolRow.source == "import",
            )
        ).scalars().all()
    assert {r.symbol for r in rows} == {"PANW", "JPM"}


async def test_max_pool_enforced(session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path, [("AAPL", "Technology"), ("JPM", "Financials")])
    config.yaml["universe"]["max_pool"] = 3
    service, *_ = make_service(session_factory, clock, calendar, config)
    service.ensure_pool_loaded()
    await service.add_symbol("MSFT", "Technology")
    with pytest.raises(PoolError, match="pool is full"):
        await service.add_symbol("XOM", "Energy")
    with pytest.raises(PoolError, match="pool is full"):
        await service.import_csv("NVDA,Technology\n")


async def test_remove_symbol(session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    config = make_config(
        tmp_path, [("AAPL", "Technology"), ("SPY", "Index")]
    )
    service, *_ = make_service(session_factory, clock, calendar, config)
    service.ensure_pool_loaded()
    service.remove_symbol("AAPL")
    with session_factory() as session:
        row = session.execute(
            select(UniversePoolRow).where(UniversePoolRow.symbol == "AAPL")
        ).scalar_one()
    assert row.removed_at is not None
    with pytest.raises(PoolError, match="baseline"):
        service.remove_symbol("SPY")
    with pytest.raises(PoolError, match="not in the pool"):
        service.remove_symbol("NVDA")
    # Re-adding a removed row reactivates it.
    await service.add_symbol("AAPL", "Technology")
    assert "AAPL" in {r.symbol for r in service.active_pool()}


async def test_held_symbol_leaves_pool_when_position_closes(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    """Spec: a held symbol removed from the pool leaves it when the
    position closes — until then it is still screened and shown."""
    config = make_config(tmp_path, [("AAPL", "Technology")])
    service, *_ = make_service(session_factory, clock, calendar, config)
    with session_factory() as session:
        session.add(
            Position(
                symbol="AAPL",
                qty=4,
                avg_entry_price=Decimal("100"),
                opened_at=clock.now(),
            )
        )
        session.commit()

    service.ensure_pool_loaded()
    service.remove_symbol("AAPL")  # marked removed, but still held
    assert "AAPL" in {r.symbol for r in service.active_pool()}
    await service.run_screen()
    row = screen_rows(session_factory)["AAPL"]
    assert row.status == "eligible"
    shown = next(r for r in service.describe()["rows"] if r["symbol"] == "AAPL")
    assert shown["held"] and shown["status"] == "eligible"

    with session_factory() as session:  # position closes → leaves the pool
        session.execute(
            select(Position).where(Position.symbol == "AAPL")
        ).scalar_one().closed_at = clock.now()
        session.commit()
    assert "AAPL" not in {r.symbol for r in service.active_pool()}
    assert all(
        r["symbol"] != "AAPL" for r in service.describe()["rows"]
    )


async def test_fixed_mode_unchanged(session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    """Fixed mode is exactly v0.1: no pool seed, no screen rows, the
    universe is still the configured symbol list."""
    config = make_config(tmp_path, mode="fixed")
    service, market_data, *_ = make_service(
        session_factory, clock, calendar, config
    )
    assert service.ensure_pool_loaded() == 0
    result = await service.run_screen()
    assert result == {"skipped": "fixed mode"}
    with session_factory() as session:
        assert session.execute(select(UniversePoolRow)).scalars().all() == []
        assert session.execute(select(UniverseScreenRow)).scalars().all() == []
    assert market_data.universe() == [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM"
    ]


async def test_held_symbols_always_listed(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    """A held symbol outside the pool still shows — held positions are
    always handled."""
    config = make_config(tmp_path, [("AAPL", "Technology")])
    service, *_ = make_service(session_factory, clock, calendar, config)
    with session_factory() as session:
        session.add(
            Position(
                symbol="ZZZ",
                qty=4,
                avg_entry_price=Decimal("100"),
                opened_at=clock.now(),
            )
        )
        session.commit()
    payload = service.describe()
    held_row = next(r for r in payload["rows"] if r["symbol"] == "ZZZ")
    assert held_row["held"] and held_row["qty"] == 4
    assert payload["counts"]["held"] == 1


async def test_full_default_pool_screens_in_simulated_mode(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
) -> None:
    """The acceptance run: the shipped 72-symbol pool screens clean in
    simulated mode — every fake symbol is liquid and affordable."""
    config = make_config(tmp_path)  # real backend/universe/pool.csv
    service, *_ = make_service(session_factory, clock, calendar, config)
    result = await service.run_screen()
    assert result["pool"] == 72
    assert result["eligible"] == 72
    rows = screen_rows(session_factory)
    assert len(rows) == 72
    assert {r.status for r in rows.values()} == {"eligible"}
    assert sorted(
        r.rank for r in rows.values() if r.rank is not None
    ) == list(range(1, 73))


def test_pool_csv_defaults_valid(
    clock: FakeClock,
    calendar: FakeCalendarSource,
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    """The shipped pool parses cleanly: 72 rows, valid symbols, sectors
    inside the configured allowed list."""
    config = make_config(tmp_path)
    service, *_ = make_service(session_factory, clock, calendar, config)
    rows = service._read_pool_file()
    assert len(rows) == 72
    assert all(SYMBOL_RE.match(symbol) for symbol, _ in rows)
    allowed = set(service.allowed_sectors())
    assert all(sector in allowed for _, sector in rows)
