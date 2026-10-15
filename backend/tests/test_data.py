from datetime import date, datetime
from pathlib import Path

import pytest
from app.config import AppConfig
from app.data.cache import BarCache
from app.data.factory import DataSources, make_data_sources
from app.data.fake import (
    FakeBarSource,
    FakeCalendarSource,
    FakeCorporateActionSource,
    FakeEarningsSource,
    FakeFXSource,
    FakeNewsSource,
    FakeQuoteSource,
)
from app.data.models import (
    CorporateActionEvent,
    EarningsEvent,
    earnings_counts_as_before_open,
    normalise_earnings_hour,
)
from app.data.service import MarketDataService
from app.db.base import Base
from app.db.models import Earning as EarningRow
from app.market.clock import UTC
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def fake_sources() -> DataSources:
    calendar = FakeCalendarSource()
    return DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar,
        alpaca=None,
    )


def test_make_data_sources_simulated_uses_fakes() -> None:
    config = AppConfig()
    config.env.mode = "simulated"
    sources = make_data_sources(config)
    assert isinstance(sources.bars, FakeBarSource)
    assert isinstance(sources.quotes, FakeQuoteSource)
    assert isinstance(sources.calendar, FakeCalendarSource)
    assert sources.alpaca is None


def test_make_data_sources_paper_requires_keys() -> None:
    config = AppConfig()
    config.env.mode = "paper"
    config.env.alpaca_api_key = ""
    config.env.alpaca_secret_key = ""
    with pytest.raises(RuntimeError, match="ALPACA_API_KEY"):
        make_data_sources(config)


async def test_fake_bars_cover_regular_session() -> None:
    calendar = FakeCalendarSource()
    source = FakeBarSource(calendar)
    session = calendar.session_for(date(2026, 10, 15))
    assert session is not None
    result = await source.fetch_5m_bars(
        "AAPL", session.open.astimezone(UTC), session.close.astimezone(UTC)
    )
    assert result.error is None
    assert len(result.data) == 78  # 6.5 hours of 5-minute bars


async def test_fake_bars_deterministic() -> None:
    calendar = FakeCalendarSource()
    source = FakeBarSource(calendar)
    session = calendar.session_for(date(2026, 10, 15))
    assert session is not None
    start, end = session.open.astimezone(UTC), session.close.astimezone(UTC)
    a = await source.fetch_5m_bars("AAPL", start, end)
    b = await source.fetch_5m_bars("AAPL", start, end)
    assert a.data == b.data


async def test_fake_quote_source() -> None:
    result = await FakeQuoteSource().fetch_quotes(["AAPL", "MSFT"])
    assert result.error is None
    assert set(result.data) == {"AAPL", "MSFT"}
    quote = result.data["AAPL"]
    assert quote.ask > quote.bid


def test_normalise_earnings_hour() -> None:
    assert normalise_earnings_hour("bmo") == "before_open"
    assert normalise_earnings_hour("amc") == "after_close"
    assert normalise_earnings_hour("dmh") == "during_market"
    assert normalise_earnings_hour("") == "unknown"
    assert normalise_earnings_hour("nonsense") == "unknown"


def test_earnings_unknown_counts_as_before_open() -> None:
    # For the earnings exit, an unknown time of day counts as before the open.
    assert earnings_counts_as_before_open("before_open")
    assert earnings_counts_as_before_open("unknown")
    assert not earnings_counts_as_before_open("after_close")
    assert not earnings_counts_as_before_open("during_market")


async def test_fake_earnings_filter() -> None:
    event = EarningsEvent(
        symbol="AAPL",
        date=date(2026, 10, 30),
        time_of_day="after_close",
        hour_raw="amc",
        fetched_at=datetime(2026, 10, 15, tzinfo=UTC),
    )
    source = FakeEarningsSource([event])
    result = await source.fetch_earnings(
        ["AAPL", "MSFT"], date(2026, 10, 1), date(2026, 10, 31)
    )
    assert len(result.data) == 1
    assert result.data[0].symbol == "AAPL"


async def test_fx_source_returns_eur_usd() -> None:
    result = await FakeFXSource().fetch_fx(date(2026, 10, 15))
    assert result.error is None
    assert result.data is not None
    assert result.data.usd_per_eur > 0


async def test_bar_cache_roundtrip(session_factory: sessionmaker[Session]) -> None:
    calendar = FakeCalendarSource()
    source = FakeBarSource(calendar)
    session = calendar.session_for(date(2026, 10, 15))
    assert session is not None
    start, end = session.open.astimezone(UTC), session.close.astimezone(UTC)
    bars = (await source.fetch_5m_bars("AAPL", start, end)).data

    cache = BarCache(session_factory)
    inserted = cache.store(bars)
    assert inserted == 78
    # Storing again inserts nothing (unique on symbol+start).
    assert cache.store(bars) == 0

    cached = cache.get("AAPL", start, end)
    assert len(cached) == 78
    assert cached[0].start == bars[0].start


async def test_refresh_bars_fills_cache_for_all_symbols(
    session_factory: sessionmaker[Session], fake_sources: DataSources
) -> None:
    calendar = FakeCalendarSource()
    service = MarketDataService(
        fake_sources, calendar, AppConfig(), session_factory=session_factory
    )
    session = calendar.session_for(date(2026, 10, 15))
    assert session is not None
    results = await service.refresh_bars(
        session.open.astimezone(UTC), session.close.astimezone(UTC)
    )
    assert len(results) == 10
    for symbol, result in results.items():
        assert result.error is None
        assert result.data == 78
        cached = service.get_5m_bars(
            symbol, session.open.astimezone(UTC), session.close.astimezone(UTC)
        )
        assert len(cached) == 78


async def test_get_1h_bars_from_cache(
    session_factory: sessionmaker[Session], fake_sources: DataSources
) -> None:
    calendar = FakeCalendarSource()
    service = MarketDataService(
        fake_sources, calendar, AppConfig(), session_factory=session_factory
    )
    session = calendar.session_for(date(2026, 10, 15))
    assert session is not None
    await service.refresh_bars(
        session.open.astimezone(UTC), session.close.astimezone(UTC)
    )
    bars = service.get_1h_bars("AAPL", session)
    assert len(bars) == 7
    assert bars[-1].end == session.close


async def test_refresh_earnings_stores_rows(
    session_factory: sessionmaker[Session],
) -> None:
    event = EarningsEvent(
        symbol="AAPL",
        date=date(2026, 10, 30),
        time_of_day="after_close",
        hour_raw="amc",
        fetched_at=datetime(2026, 10, 15, tzinfo=UTC),
    )
    calendar = FakeCalendarSource()
    sources = DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource([event]),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar,
        alpaca=None,
    )
    service = MarketDataService(sources, calendar, AppConfig(), session_factory=session_factory)
    result = await service.refresh_earnings(date(2026, 10, 1), date(2026, 10, 31))
    assert result.data == 1
    with session_factory() as session:
        row = session.execute(select(EarningRow)).scalars().one()
    assert row.symbol == "AAPL"
    assert row.time_of_day == "after_close"
    assert row.hour_raw == "amc"
    # Refreshing again is idempotent.
    again = await service.refresh_earnings(date(2026, 10, 1), date(2026, 10, 31))
    assert again.data == 0


async def test_refresh_corporate_actions_stores_rows(
    session_factory: sessionmaker[Session],
) -> None:
    action = CorporateActionEvent(
        symbol="AAPL",
        action_type="dividend",
        ex_date=date(2026, 11, 1),
        amount_or_ratio=None,
        fetched_at=datetime(2026, 10, 15, tzinfo=UTC),
    )
    calendar = FakeCalendarSource()
    sources = DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource([action]),
        calendar=calendar,
        alpaca=None,
    )
    service = MarketDataService(sources, calendar, AppConfig(), session_factory=session_factory)
    result = await service.refresh_corporate_actions(
        date(2026, 10, 1), date(2026, 11, 30)
    )
    assert result.data == 1


async def test_refresh_history_uses_configured_sessions(
    session_factory: sessionmaker[Session], fake_sources: DataSources
) -> None:
    calendar = FakeCalendarSource()
    config = AppConfig()
    config.yaml["schedule"]["history_sessions"] = 5
    service = MarketDataService(
        fake_sources, calendar, config, session_factory=session_factory
    )
    results = await service.refresh_history(date(2026, 10, 15))
    # 5 weekday sessions per symbol.
    for symbol in ("SPY", "AAPL"):
        assert results[symbol].error is None
        assert results[symbol].data == 5 * 78
