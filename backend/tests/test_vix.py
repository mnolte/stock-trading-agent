"""VIX collection (item 060): FRED parsing/fallback, the per-cycle
fields, the VIXY reference-ETF exclusion and cycle persistence."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
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
from app.data.fred import FredVixSource
from app.data.models import Bar5m, DailyBar, VixObservation
from app.data.service import MarketDataService
from app.data.vix import VixService
from app.db.models import Cycle, VixClose
from app.market.clock import NY_TZ, UTC, FakeClock
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# FRED source
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=None, response=None  # type: ignore[arg-type]
            )


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    monkeypatch.setattr(httpx, "get", handler)


async def test_fred_csv_parses_and_skips_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_text = (
        "observation_date,VIXCLS\n"
        "2026-10-13,17.40\n"
        "2026-10-14,\n"  # missing — empty, like real FRED output
        "2026-10-15,.\n"  # missing — dot variant
        "2026-10-16,18.25\n"
    )
    _patch_httpx(monkeypatch, lambda *a, **k: _FakeResponse(csv_text))
    source = FredVixSource()
    result = await source.fetch_vix_closes(date(2026, 10, 13), date(2026, 10, 16))
    assert result.error is None
    assert result.source == "fred"
    assert [(o.date, o.close) for o in result.data] == [
        (date(2026, 10, 13), Decimal("17.40")),
        (date(2026, 10, 16), Decimal("18.25")),
    ]


async def test_fred_csv_failure_without_key_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: Any, **k: Any) -> _FakeResponse:
        raise httpx.ConnectError("down")

    _patch_httpx(monkeypatch, boom)
    source = FredVixSource()
    result = await source.fetch_vix_closes(date(2026, 10, 13), date(2026, 10, 16))
    assert result.error is not None
    assert "down" in result.error
    assert result.data == []


async def test_fred_csv_failure_falls_back_to_keyed_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def handler(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append(url)
        if "fredgraph" in url:
            raise httpx.ConnectError("csv down")
        return _FakeResponse(
            json.dumps(
                {
                    "observations": [
                        {"date": "2026-10-15", "value": "18.10"},
                        {"date": "2026-10-16", "value": "."},
                    ]
                }
            )
        )

    _patch_httpx(monkeypatch, handler)
    source = FredVixSource(api_key="test-key")
    result = await source.fetch_vix_closes(date(2026, 10, 13), date(2026, 10, 16))
    assert result.error is None
    assert len(calls) == 2  # CSV first, keyed API as the fallback
    assert result.data == [VixObservation(date(2026, 10, 15), Decimal("18.10"))]


async def test_fred_key_test_fetches_one_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(url: str, params: dict[str, Any], **kwargs: Any) -> _FakeResponse:
        seen.update(params)
        return _FakeResponse(
            json.dumps({"observations": [{"date": "2026-11-06", "value": "25.64"}]})
        )

    _patch_httpx(monkeypatch, handler)
    source = FredVixSource(api_key="k" * 20)
    assert await source.test_key() is None
    assert seen["series_id"] == "VIXCLS"

    source2 = FredVixSource(api_key="k" * 20)

    def empty(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(json.dumps({"observations": []}))

    _patch_httpx(monkeypatch, empty)
    assert await source2.test_key() is not None


# ---------------------------------------------------------------------------
# VixService — per-cycle fields
# ---------------------------------------------------------------------------


def _sources(calendar: FakeCalendarSource | None = None) -> DataSources:
    calendar = calendar or FakeCalendarSource()
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


def _service(
    session_factory: sessionmaker[Session], clock: FakeClock
) -> tuple[VixService, MarketDataService]:
    calendar = FakeCalendarSource()
    sources = _sources(calendar)
    market_data = MarketDataService(
        sources, calendar, AppConfig(), session_factory=session_factory
    )
    return (
        VixService(sources.vix, market_data, AppConfig(), clock,
                   session_factory=session_factory),
        market_data,
    )


def _seed_closes(
    session_factory: sessionmaker[Session], closes: list[tuple[date, str]]
) -> None:
    with session_factory() as session:
        for day, close in closes:
            session.add(
                VixClose(date=day, close=Decimal(close), fetched_at=datetime(2026, 1, 1))
            )
        session.commit()


def _weekday_closes(end: date, count: int, start_value: float = 10.0) -> list[tuple[date, str]]:
    """``count`` weekday closes ending at ``end`` (inclusive), ascending."""
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return [(d, f"{start_value + i:.2f}") for i, d in enumerate(days)]


async def test_cycle_fields_percentile_and_changes(
    session_factory: sessionmaker[Session],
) -> None:
    # Wednesday 2026-11-11 12:00 New York; last completed session Tue 11-10.
    now = datetime(2026, 11, 11, 12, 0, tzinfo=NY_TZ)
    clock = FakeClock(now)
    vix, _market = _service(session_factory, clock)
    closes = _weekday_closes(date(2026, 11, 9), 30)  # newest usable: Mon 11-09
    _seed_closes(session_factory, closes)

    fields = vix.cycle_fields(now)
    assert fields["vix_date"] == date(2026, 11, 9)
    assert fields["vix_close"] == Decimal("39.00")
    # All 30 closes are <= the newest → 100th percentile of the window.
    assert fields["vix_percentile_1y"] == Decimal("100.00")
    # 5 sessions back: closes[-6] = 34.00 → (39-34)/34*100 = 14.71
    assert fields["vix_change_5d_pct"] == Decimal("14.71")
    # Mon 11-09 → Tue 11-10 is one session: the normal FRED lag, not stale.
    assert fields["vix_stale"] is False


async def test_cycle_fields_todays_close_not_usable_until_posted(
    session_factory: sessionmaker[Session],
) -> None:
    """A close dated the previous session only counts from 17:00 New York
    on the next trading day — during the day the newest usable close is
    one session older (docs/SPEC.md, VIX)."""
    now = datetime(2026, 11, 11, 12, 0, tzinfo=NY_TZ)  # Wed, before 17:00
    clock = FakeClock(now)
    vix, _market = _service(session_factory, clock)
    closes = _weekday_closes(date(2026, 11, 10), 30)  # includes Tue 11-10
    _seed_closes(session_factory, closes)

    fields = vix.cycle_fields(now)
    # Tue 11-10's close is not yet usable at Wed 12:00 → Mon 11-09 shows.
    assert fields["vix_date"] == date(2026, 11, 9)

    later = datetime(2026, 11, 11, 18, 0, tzinfo=NY_TZ)  # Wed 18:00
    fields2 = vix.cycle_fields(later)
    assert fields2["vix_date"] == date(2026, 11, 10)


async def test_cycle_fields_stale_when_fred_lags(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 11, 11, 12, 0, tzinfo=NY_TZ)
    clock = FakeClock(now)
    vix, _market = _service(session_factory, clock)
    # Closes stop at Fri 11-06 — FRED missed Mon and Tue.
    _seed_closes(session_factory, _weekday_closes(date(2026, 11, 6), 30))

    fields = vix.cycle_fields(now)
    assert fields["vix_date"] == date(2026, 11, 6)
    assert fields["vix_close"] is not None
    # Fri → Tue last completed is two sessions — beyond stale_sessions=1.
    assert fields["vix_stale"] is True


async def test_cycle_fields_all_null_without_closes(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 11, 11, 12, 0, tzinfo=NY_TZ)
    clock = FakeClock(now)
    vix, _market = _service(session_factory, clock)
    fields = vix.cycle_fields(now)
    for name in (
        "vix_close",
        "vix_date",
        "vix_percentile_1y",
        "vix_change_5d_pct",
        "vixy_change_today_pct",
        "vixy_change_since_vix_pct",
    ):
        assert fields[name] is None, name
    assert fields["vix_stale"] is None


async def test_cycle_fields_vixy_moves(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 11, 11, 12, 0, tzinfo=NY_TZ)
    clock = FakeClock(now)
    vix, market = _service(session_factory, clock)
    _seed_closes(session_factory, _weekday_closes(date(2026, 11, 9), 30))

    cache = market.bars_cache
    # VIXY daily closes: Mon 11-09 (the VIX date) 19.00, Tue 11-10 (prev
    # session) 20.00.
    cache.store_daily(
        [
            DailyBar("VIXY", date(2026, 11, 9), Decimal("18"), Decimal("19.5"),
                     Decimal("17.5"), Decimal("19.00"), 1000),
            DailyBar("VIXY", date(2026, 11, 10), Decimal("19"), Decimal("20.5"),
                     Decimal("18.5"), Decimal("20.00"), 1000),
        ]
    )
    # The latest closed 5m bar before now: 20.50.
    cache.store(
        [
            Bar5m(
                "VIXY",
                datetime(2026, 11, 11, 11, 50, tzinfo=NY_TZ).astimezone(UTC),
                Decimal("20.4"), Decimal("20.6"), Decimal("20.3"),
                Decimal("20.50"), 100, "sip",
            )
        ]
    )

    fields = vix.cycle_fields(now)
    # today: (20.50 - 20.00) / 20.00 = +2.50%
    assert fields["vixy_change_today_pct"] == Decimal("2.50")
    # since VIX close: (20.50 - 19.00) / 19.00 = +7.89%
    assert fields["vixy_change_since_vix_pct"] == Decimal("7.89")


async def test_refresh_stores_closes(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 11, 11, 12, 0, tzinfo=NY_TZ)
    clock = FakeClock(now)
    vix, _market = _service(session_factory, clock)

    class _StubSource:
        async def fetch_vix_closes(self, start: date, end: date) -> Any:
            from app.data.models import DataResult

            return DataResult(
                data=[VixObservation(date(2026, 11, 9), Decimal("21.5"))],
                source="fred",
            )

    vix.source = _StubSource()
    result = await vix.refresh(date(2026, 11, 11))
    assert result.error is None
    with session_factory() as session:
        row = session.execute(select(VixClose)).scalar_one()
    assert row.date == date(2026, 11, 9)
    assert row.close == Decimal("21.5")
    assert vix.latest is not None and vix.latest.error is None
    assert vix.health() == "ok"


async def test_refresh_failure_is_health_not_an_exception(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 11, 11, 12, 0, tzinfo=NY_TZ)
    clock = FakeClock(now)
    vix, _market = _service(session_factory, clock)

    class _Failing:
        async def fetch_vix_closes(self, start: date, end: date) -> Any:
            from app.data.models import DataResult

            return DataResult(data=[], error="fred down", source="fred")

    vix.source = _Failing()
    result = await vix.refresh(date(2026, 11, 11))
    assert result.error == "fred down"
    assert vix.health() == "failed: fred down"


# ---------------------------------------------------------------------------
# VIXY exclusion
# ---------------------------------------------------------------------------


def test_universe_never_contains_reference_etf(
    session_factory: sessionmaker[Session],
) -> None:
    calendar = FakeCalendarSource()
    sources = _sources(calendar)
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["AAPL", "VIXY", "MSFT"]
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    symbols = market_data.universe()
    assert "VIXY" not in symbols
    assert {"AAPL", "MSFT", "SPY"} <= set(symbols)


def test_universe_excludes_etf_even_when_held(
    session_factory: sessionmaker[Session],
) -> None:
    """A held position can never make the reference ETF a candidate —
    the filter applies after held symbols are added."""
    from app.db.models import Position

    calendar = FakeCalendarSource()
    sources = _sources(calendar)
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["AAPL"]
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    with session_factory() as session:
        session.add(
            Position(
                symbol="VIXY",
                qty=1,
                avg_entry_price=Decimal("20"),
                opened_at=datetime(2026, 11, 9, 14, 0),
            )
        )
        session.commit()
    assert "VIXY" not in market_data.universe()


# ---------------------------------------------------------------------------
# cycle persistence
# ---------------------------------------------------------------------------


async def test_cycle_stores_vix_fields(
    session_factory: sessionmaker[Session],
) -> None:
    """A full cycle on fakes persists the seven VIX columns on its row —
    and nothing else about the decision changes (item 060)."""
    from tests.test_decide_cycle import (
        CYCLE_1246,
        _evaluation,
        _item,
        _runner,
    )
    from tests.test_decide_cycle import (
        _sources as _cycle_sources,
    )

    now = CYCLE_1246  # 2026-10-15 12:46 New York (Thursday)
    clock = FakeClock(now)
    sources = _cycle_sources()
    # Seed enough closes that the newest usable one is deterministic.
    _seed_closes(session_factory, _weekday_closes(date(2026, 10, 14), 30))

    # Reuse the cycle harness but wire a VixService on top of it.
    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass"), held=set()),
        sources=sources,
        clock=clock,
        universe=["MSFT"],
    )
    vix_service = VixService(
        None,
        runner.market_data,
        runner.config,
        clock,
        session_factory=session_factory,
    )
    runner.vix = vix_service

    report = await runner.run(now)
    assert report.status == "done"
    with session_factory() as session:
        cycle = session.execute(
            select(Cycle).where(Cycle.kind == "cycle")
        ).scalar_one()
    # Wed 10-14's close is usable Thu 10-15 12:46? It posts Thu 17:00 —
    # so the newest usable close is Tue 10-13.
    assert cycle.vix_date == date(2026, 10, 13)
    assert cycle.vix_close is not None
    assert cycle.vix_stale is False
