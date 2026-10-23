import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

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
from app.data.finnhub import FinnhubEarningsSource
from app.data.models import (
    Bar5m,
    CorporateActionEvent,
    DailyBar,
    DataResult,
    EarningsEvent,
    earnings_counts_as_before_open,
    normalise_earnings_hour,
)
from app.data.service import MarketDataService
from app.db.base import Base
from app.db.models import CorporateAction as CorporateActionRow
from app.db.models import Earning as EarningRow
from app.market.clock import UTC
from sqlalchemy import create_engine, event, select
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


def test_bar_cache_duplicate_batches_are_idempotent(session_factory: sessionmaker[Session]) -> None:
    cache = BarCache(session_factory)
    at = datetime(2026, 10, 22, 15, tzinfo=UTC)
    bar = Bar5m("WMT", at, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), 10, "sip")
    assert cache.store([bar, bar]) == 1
    assert cache.store([bar]) == 0
    daily = DailyBar("WMT", at.date(), bar.open, bar.high, bar.low, bar.close, bar.volume)
    assert cache.store_daily([daily, daily]) == 1
    assert cache.store_daily([daily]) == 0


def test_large_bar_cache_batch_respects_sqlite_variable_limit(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'large.db'}")

    @event.listens_for(engine, "connect")
    def limit_variables(connection: Any, _record: Any) -> None:
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    Base.metadata.create_all(engine)
    cache = BarCache(sessionmaker(bind=engine))
    at = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [Bar5m("WMT", at + timedelta(minutes=5 * i), Decimal("100"), Decimal("101"),
                  Decimal("99"), Decimal("100"), 10, "sip") for i in range(2000)]
    assert cache.store(bars) == 2000
    assert cache.store(bars) == 0
    daily = [DailyBar("WMT", (at + timedelta(days=i)).date(), Decimal("100"), Decimal("101"),
                      Decimal("99"), Decimal("100"), 10) for i in range(2000)]
    assert cache.store_daily(daily) == 2000
    engine.dispose()


def test_make_data_sources_simulated_uses_fakes() -> None:
    config = AppConfig()
    config.env.mode = "simulated"
    sources = make_data_sources(config)
    assert isinstance(sources.bars, FakeBarSource)
    assert isinstance(sources.quotes, FakeQuoteSource)
    assert isinstance(sources.calendar, FakeCalendarSource)
    assert sources.alpaca is None


def test_make_data_sources_paper_without_keys_builds_unkeyed_client() -> None:
    """Paper mode without stored keys still comes up: the app starts
    paused and every Alpaca call fails as an ordinary error (docs/SPEC.md,
    API keys). The clients exist so a Settings save can swap keys in."""
    config = AppConfig()
    config.env.mode = "paper"
    sources = make_data_sources(config)
    assert sources.alpaca is not None
    assert sources.earnings is not None


async def test_fake_bars_cover_regular_session() -> None:
    calendar = FakeCalendarSource()
    source = FakeBarSource(calendar)
    session = calendar.session_for(date(2026, 10, 15))
    assert session is not None
    result = await source.fetch_5m_bars(
        ["AAPL"], session.open.astimezone(UTC), session.close.astimezone(UTC)
    )
    assert result.error is None
    assert len(result.data) == 78  # 6.5 hours of 5-minute bars


async def test_fake_bars_deterministic() -> None:
    calendar = FakeCalendarSource()
    source = FakeBarSource(calendar)
    session = calendar.session_for(date(2026, 10, 15))
    assert session is not None
    start, end = session.open.astimezone(UTC), session.close.astimezone(UTC)
    a = await source.fetch_5m_bars(["AAPL"], start, end)
    b = await source.fetch_5m_bars(["AAPL"], start, end)
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
    bars = (await source.fetch_5m_bars(["AAPL"], start, end)).data

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


async def test_refresh_earnings_upserts_and_prunes(
    session_factory: sessionmaker[Session],
) -> None:
    """A revised report updates in place; one that vanishes is pruned."""
    event = EarningsEvent(
        symbol="AAPL",
        date=date(2026, 10, 30),
        time_of_day="after_close",
        hour_raw="amc",
        fetched_at=datetime(2026, 10, 15, tzinfo=UTC),
    )
    calendar = FakeCalendarSource()
    earnings = FakeEarningsSource([event])
    sources = DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=earnings,
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar,
        alpaca=None,
    )
    service = MarketDataService(
        sources, calendar, AppConfig(), session_factory=session_factory
    )
    await service.refresh_earnings(date(2026, 10, 1), date(2026, 10, 31))

    # Finnhub revises the timing: the stored row must follow.
    earnings.events = [
        EarningsEvent(
            symbol="AAPL",
            date=date(2026, 10, 30),
            time_of_day="before_open",
            hour_raw="bmo",
            fetched_at=datetime(2026, 10, 16, tzinfo=UTC),
        )
    ]
    await service.refresh_earnings(date(2026, 10, 1), date(2026, 10, 31))
    with session_factory() as session:
        row = session.execute(select(EarningRow)).scalars().one()
        assert row.time_of_day == "before_open"
        assert row.hour_raw == "bmo"

    # The report is pulled from the calendar: the row is pruned.
    earnings.events = []
    await service.refresh_earnings(date(2026, 10, 1), date(2026, 10, 31))
    with session_factory() as session:
        assert session.execute(select(EarningRow)).scalars().all() == []


async def test_finnhub_earnings_one_call_for_whole_calendar() -> None:
    """The whole calendar is fetched in one call and filtered to the
    universe locally — a per-symbol burst overruns the free-tier rate
    limit and the fetch comes back 429."""
    calls: list[tuple[str, str, str | None]] = []

    class StubClient:
        def earnings_calendar(
            self, _from: str, to: str, symbol: str | None
        ) -> dict[str, object]:
            calls.append((_from, to, symbol))
            return {
                "earningsCalendar": [
                    {"symbol": "AAPL", "date": "2026-10-30", "hour": "amc"},
                    {"symbol": "TSLA", "date": "2026-10-30", "hour": "bmo"},
                ]
            }

    source = FinnhubEarningsSource("test-key")
    source._client = StubClient()
    result = await source.fetch_earnings(
        ["AAPL"], date(2026, 10, 1), date(2026, 10, 31)
    )
    assert result.error is None
    assert calls == [("2026-10-01", "2026-10-31", None)]
    assert [e.symbol for e in result.data] == ["AAPL"]
    assert result.data[0].time_of_day == "after_close"
    assert result.data[0].hour_raw == "amc"


async def test_refresh_earnings_dedupes_double_listed_report(
    session_factory: sessionmaker[Session],
) -> None:
    """A fetch listing one report twice commits one row, not two —
    sessions never autoflush, so the first pending insert is invisible
    to the exists check (regression: duplicate rows, then every refresh
    crashed on scalar_one_or_none)."""
    events = [
        EarningsEvent(
            symbol="AAPL",
            date=date(2026, 10, 30),
            time_of_day="before_open",
            hour_raw="bmo",
            fetched_at=datetime(2026, 10, 15, tzinfo=UTC),
        ),
        EarningsEvent(
            symbol="AAPL",
            date=date(2026, 10, 30),
            time_of_day="after_close",
            hour_raw="amc",
            fetched_at=datetime(2026, 10, 15, tzinfo=UTC),
        ),
    ]
    calendar = FakeCalendarSource()
    sources = DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(events),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar,
        alpaca=None,
    )
    service = MarketDataService(
        sources, calendar, AppConfig(), session_factory=session_factory
    )
    result = await service.refresh_earnings(date(2026, 10, 1), date(2026, 10, 31))
    assert result.data == 1
    with session_factory() as session:
        row = session.execute(select(EarningRow)).scalars().one()
    assert row.hour_raw == "amc"  # the later listing wins

    # A second refresh updates in place instead of crashing on a dup.
    again = await service.refresh_earnings(date(2026, 10, 1), date(2026, 10, 31))
    assert again.data == 0


async def test_refresh_earnings_repairs_existing_duplicate_rows(
    session_factory: sessionmaker[Session],
) -> None:
    """A database written before the dedupe may already hold two rows for
    one (symbol, date): the refresh keeps the first and drops the extras."""
    day = datetime(2026, 10, 30)
    with session_factory() as session:
        for _ in range(2):
            session.add(
                EarningRow(
                    symbol="AAPL",
                    date=day,
                    time_of_day="before_open",
                    hour_raw="bmo",
                    fetched_at=datetime(2026, 10, 15),
                )
            )
        session.commit()
    event = EarningsEvent(
        symbol="AAPL",
        date=date(2026, 10, 30),
        time_of_day="after_close",
        hour_raw="amc",
        fetched_at=datetime(2026, 10, 16, tzinfo=UTC),
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
    service = MarketDataService(
        sources, calendar, AppConfig(), session_factory=session_factory
    )
    result = await service.refresh_earnings(date(2026, 10, 1), date(2026, 10, 31))
    assert result.data == 0
    with session_factory() as session:
        rows = session.execute(select(EarningRow)).scalars().all()
    assert len(rows) == 1
    assert rows[0].hour_raw == "amc"


async def test_refresh_corporate_actions_dedupes_double_listed_action(
    session_factory: sessionmaker[Session],
) -> None:
    """Same dedupe as earnings: one action listed twice in a single fetch
    commits one row, and the next refresh does not crash."""
    action = CorporateActionEvent(
        symbol="AAPL",
        action_type="split",
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
        corporate_actions=FakeCorporateActionSource([action, action]),
        calendar=calendar,
        alpaca=None,
    )
    service = MarketDataService(
        sources, calendar, AppConfig(), session_factory=session_factory
    )
    result = await service.refresh_corporate_actions(
        date(2026, 10, 1), date(2026, 11, 30)
    )
    assert result.data == 1
    with session_factory() as session:
        rows = session.execute(select(CorporateActionRow)).scalars().all()
    assert len(rows) == 1
    # Idempotent: the second refresh neither duplicates nor crashes.
    again = await service.refresh_corporate_actions(
        date(2026, 10, 1), date(2026, 11, 30)
    )
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


async def test_refresh_history_fetches_only_missing_sessions(
    session_factory: sessionmaker[Session], fake_sources: DataSources
) -> None:
    """A warm cache re-fetches only the forming latest session per symbol,
    not the whole window."""
    calendar = FakeCalendarSource()
    config = AppConfig()
    config.yaml["schedule"]["history_sessions"] = 5
    service = MarketDataService(
        fake_sources, calendar, config, session_factory=session_factory
    )
    await service.refresh_history(date(2026, 10, 15))
    starts: list[datetime] = []
    original = fake_sources.bars.fetch_5m_bars

    async def spy(
        symbols: list[str], start: datetime, end: datetime
    ) -> DataResult[list[Bar5m]]:
        starts.append(start)
        return await original(symbols, start, end)

    fake_sources.bars.fetch_5m_bars = spy  # type: ignore[method-assign]
    again = await service.refresh_history(date(2026, 10, 15))
    last = calendar.session_for(date(2026, 10, 15))
    assert last is not None
    # Only the forming latest session is fetched — never the full window.
    assert starts == [last.open.astimezone(UTC)] * len(starts)
    for symbol in ("SPY", "AAPL"):
        assert again[symbol].error is None
        assert again[symbol].data == 0  # refetched, already stored


async def test_fake_daily_bars_cover_sessions() -> None:
    calendar = FakeCalendarSource()
    source = FakeBarSource(calendar)
    result = await source.fetch_daily_bars(
        "AAPL", date(2026, 10, 12), date(2026, 10, 16)
    )
    assert result.error is None
    # One bar per weekday session in the range.
    assert [b.date for b in result.data] == [
        date(2026, 10, 12),
        date(2026, 10, 13),
        date(2026, 10, 14),
        date(2026, 10, 15),
        date(2026, 10, 16),
    ]
    bar = result.data[0]
    assert bar.low <= bar.open <= bar.high
    assert bar.low <= bar.close <= bar.high


async def test_fake_daily_bars_match_5m_rollup() -> None:
    # The fake's daily bars are the same session data aggregated, so the
    # previous daily close always matches the last 5m close of that session.
    calendar = FakeCalendarSource()
    source = FakeBarSource(calendar)
    day = date(2026, 10, 15)
    daily = (await source.fetch_daily_bars("AAPL", day, day)).data[0]
    session = calendar.session_for(day)
    assert session is not None
    bars_5m = (
        await source.fetch_5m_bars(
            ["AAPL"], session.open.astimezone(UTC), session.close.astimezone(UTC)
        )
    ).data
    assert daily.open == bars_5m[0].open
    assert daily.close == bars_5m[-1].close


async def test_daily_cache_roundtrip(
    session_factory: sessionmaker[Session], fake_sources: DataSources
) -> None:
    service = MarketDataService(
        fake_sources, FakeCalendarSource(), AppConfig(), session_factory=session_factory
    )
    bars = (
        await service.sources.bars.fetch_daily_bars(
            "AAPL", date(2026, 10, 13), date(2026, 10, 15)
        )
    ).data
    cache = BarCache(session_factory)
    assert cache.store_daily(bars) == 3
    # Storing again inserts nothing (unique on symbol+date).
    assert cache.store_daily(bars) == 0
    cached = cache.get_daily("AAPL", date(2026, 10, 13), date(2026, 10, 15))
    assert [b.date for b in cached] == [b.date for b in bars]


async def test_bar_cache_batched_counts(
    session_factory: sessionmaker[Session],
) -> None:
    """The grouped counts the Universe screen uses match the per-symbol
    lookups they replace."""
    calendar = FakeCalendarSource()
    source = FakeBarSource(calendar)
    cache = BarCache(session_factory)
    sessions = [
        s
        for d in (13, 14, 15)
        if (s := calendar.session_for(date(2026, 10, d))) is not None
    ]
    assert len(sessions) == 3

    for symbol, count in (("AAPL", 2), ("MSFT", 1)):
        for session in sessions[:count]:
            bars = (
                await source.fetch_5m_bars(
                    [symbol],
                    session.open.astimezone(UTC),
                    session.close.astimezone(UTC),
                )
            ).data
            cache.store(bars)
    daily = (
        await source.fetch_daily_bars(
            "AAPL", date(2026, 10, 13), date(2026, 10, 15)
        )
    ).data
    cache.store_daily(daily)

    covered = cache.covered_session_counts(["AAPL", "MSFT", "ZZZ"], sessions)
    assert covered == {"AAPL": 2, "MSFT": 1, "ZZZ": 0}
    daily_counts = cache.daily_bar_counts(["AAPL", "ZZZ"], sessions)
    assert daily_counts == {"AAPL": 3, "ZZZ": 0}
    # Same answers as the per-symbol methods.
    assert covered["AAPL"] == len(cache.covered_session_dates("AAPL", sessions))
    assert cache.covered_session_counts([], sessions) == {}
    assert cache.daily_bar_counts(["AAPL"], []) == {"AAPL": 0}


async def test_refresh_daily_fills_cache(
    session_factory: sessionmaker[Session], fake_sources: DataSources
) -> None:
    calendar = FakeCalendarSource()
    config = AppConfig()
    config.yaml["schedule"]["daily_history_sessions"] = 5
    service = MarketDataService(
        fake_sources, calendar, config, session_factory=session_factory
    )
    results = await service.refresh_daily(date(2026, 10, 15))
    for symbol in ("SPY", "AAPL"):
        assert results[symbol].error is None
        assert results[symbol].data == 5
        sessions = [
            s
            for s in (
                calendar.session_for(date(2026, 10, d)) for d in range(9, 16)
            )
            if s is not None
        ]
        daily = service.get_daily_bars(symbol, sessions)
        assert len(daily) == 5
        assert daily[0].date < daily[-1].date


# ----------------------------------------------------------------------
# cycles at scale (docs/SPEC.md, Cycles at scale)


class _CountingBars(FakeBarSource):
    """Records the symbol list of every 5m bar request."""

    def __init__(self, calendar: FakeCalendarSource) -> None:
        super().__init__(calendar)
        self.calls: list[list[str]] = []

    async def fetch_5m_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> DataResult[list[Bar5m]]:
        self.calls.append(list(symbols))
        return await super().fetch_5m_bars(symbols, start, end)


async def test_refresh_bars_uses_one_multi_symbol_request(
    session_factory: sessionmaker[Session], fake_sources: DataSources
) -> None:
    """One grouped request tops up the whole universe — SPY included —
    and every requested symbol gets a per-symbol result."""
    counting = _CountingBars(FakeCalendarSource())
    fake_sources.bars = counting
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["MSFT", "AAPL"]
    service = MarketDataService(
        fake_sources, FakeCalendarSource(), config,
        session_factory=session_factory,
    )
    session = FakeCalendarSource().session_for(date(2026, 10, 15))
    assert session is not None
    results = await service.refresh_bars(session.open, session.close)
    assert counting.calls == [["MSFT", "AAPL", "SPY"]]
    assert set(results) == {"MSFT", "AAPL", "SPY"}
    assert all(r.error is None for r in results.values())


async def test_refresh_bars_request_failure_marks_all(
    session_factory: sessionmaker[Session], fake_sources: DataSources
) -> None:
    """When the grouped request fails, every symbol reports the error —
    the cycle marks all refresh results errored."""

    class FailingBars:
        async def fetch_5m_bars(
            self, symbols: list[str], start: datetime, end: datetime
        ) -> DataResult[list[Bar5m]]:
            return DataResult(data=[], error="source down", source="fake")

        async def fetch_daily_bars(
            self, symbol: str, start: date, end: date
        ) -> DataResult[list[DailyBar]]:
            return DataResult(data=[], error="source down", source="fake")

    fake_sources.bars = FailingBars()
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["MSFT", "AAPL"]
    service = MarketDataService(
        fake_sources, FakeCalendarSource(), config,
        session_factory=session_factory,
    )
    session = FakeCalendarSource().session_for(date(2026, 10, 15))
    assert session is not None
    results = await service.refresh_bars(session.open, session.close)
    assert set(results) == {"MSFT", "AAPL", "SPY"}
    assert all(r.error == "source down" for r in results.values())


async def test_prune_5m_bars_keeps_recent_sessions(
    session_factory: sessionmaker[Session], fake_sources: DataSources
) -> None:
    """5m bars older than ``bars_5m_keep_sessions`` sessions are deleted;
    retained sessions survive."""
    config = AppConfig()
    config.yaml["schedule"]["bars_5m_keep_sessions"] = 2
    calendar = FakeCalendarSource()
    service = MarketDataService(
        fake_sources, calendar, config, session_factory=session_factory
    )
    old_session = calendar.session_for(date(2026, 10, 12))  # Monday
    new_session = calendar.session_for(date(2026, 10, 15))  # Thursday
    assert old_session is not None and new_session is not None
    old_bars = (
        await fake_sources.bars.fetch_5m_bars(
            ["AAPL"], old_session.open, old_session.close
        )
    ).data
    new_bars = (
        await fake_sources.bars.fetch_5m_bars(
            ["AAPL"], new_session.open, new_session.close
        )
    ).data
    assert old_bars and new_bars
    cache = BarCache(session_factory)
    cache.store(old_bars)
    cache.store(new_bars)

    now = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)  # Thu 16:00 NY
    removed = service.prune_5m_bars(now)
    # keep=2 → Oct 14 + Oct 15 kept; Oct 12 pruned.
    assert removed == len(old_bars)
    assert cache.get("AAPL", old_session.open, old_session.close) == []
    assert (
        len(cache.get("AAPL", new_session.open, new_session.close))
        == len(new_bars)
    )
