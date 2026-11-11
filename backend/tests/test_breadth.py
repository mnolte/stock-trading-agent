"""Breadth and sectors (item 068): the four per-cycle breadth shares,
the 11 sector ETFs' table, per-decision market_context, reference-symbol
exclusion and snapshot/hash invariance — all on fakes, no network."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

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
from app.data.models import Bar5m, DailyBar
from app.data.service import MarketDataService
from app.db.models import Cycle, Decision
from app.decide.client import FakeDecisionClient
from app.decide.service import DecisionService
from app.market.clock import NY_TZ, FakeClock
from app.rules.prefilter import RulesResult
from app.signals.breadth import (
    BREADTH_FIELD_NAMES,
    BreadthService,
    breadth_fields,
)
from app.signals.indicators import relative_strength_pct
from app.signals.service import SymbolEval
from app.signals.snapshot import IndicatorSnapshot
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

SECTOR_ETFS = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Materials": "XLB",
    "Health Care": "XLV",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}

TODAY = date(2026, 10, 15)  # Thursday
CYCLE_NOW = datetime(2026, 10, 15, 12, 46, tzinfo=NY_TZ)


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


def _market(
    session_factory: sessionmaker[Session],
    config: AppConfig | None = None,
) -> tuple[MarketDataService, FakeCalendarSource, AppConfig]:
    calendar = FakeCalendarSource()
    cfg = config or AppConfig()
    market_data = MarketDataService(
        _sources(calendar), calendar, cfg, session_factory=session_factory
    )
    return market_data, calendar, cfg


def _breadth(
    market_data: MarketDataService, cfg: AppConfig
) -> BreadthService:
    return BreadthService(market_data, cfg)


def _seed_symbol_bars(
    market_data: MarketDataService,
    symbol: str,
    sessions: list[Any],
    start_price: float,
    session_ret: float,
) -> None:
    """Every 5m bar of each session, price compounding ``session_ret``
    per session and drifting linearly inside it — enough for complete
    session-aligned 1h bars."""
    price = Decimal(str(start_price))
    bars: list[Bar5m] = []
    for session in sessions:
        day_open = price
        day_close = price * Decimal(str(1 + session_ret))
        price = day_close
        n = 0
        t = session.open
        while t < session.close:
            n += 1
            frac = Decimal(n) / Decimal(78)
            close = day_open + (day_close - day_open) * frac
            bars.append(
                Bar5m(
                    symbol=symbol,
                    start=t,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1000,
                    feed="sip",
                )
            )
            t += timedelta(minutes=5)
    market_data.bars_cache.store(bars)


def _sessions(calendar: FakeCalendarSource, count: int) -> list[Any]:
    days: list[Any] = []
    cursor = TODAY - timedelta(days=1)
    while len(days) < count:
        s = calendar.session_for(cursor)
        if s is not None:
            days.append(s)
        cursor -= timedelta(days=1)
    return list(reversed(days))


# ---------------------------------------------------------------------------
# breadth_fields — the four shares
# ---------------------------------------------------------------------------


def _snap(
    symbol: str,
    *,
    close: str = "100",
    sma50: str | None = "90",
    sma200: str | None = "80",
    ema_fast: str | None = "101",
    ema_slow: str | None = "100",
) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        symbol=symbol,
        price=Decimal(close),
        ema_fast=Decimal(ema_fast) if ema_fast is not None else None,
        ema_slow=Decimal(ema_slow) if ema_slow is not None else None,
        rsi=Decimal("50"),
        atr_1h=Decimal("1"),
        session_vwap=Decimal("100"),
        obv_trend="flat",
        rs_vs_spy_pct=Decimal("0"),
        daily_close=Decimal(close),
        daily_sma_fast=Decimal(sma50) if sma50 is not None else None,
        daily_sma_slow=Decimal(sma200) if sma200 is not None else None,
        daily_atr_percentile=None,
        opening_gap_pct=None,
        days_to_earnings=None,
        earnings_available=False,
    )


def _eval(
    symbol: str,
    snap: IndicatorSnapshot,
    *,
    up_today: bool | None = None,
) -> SymbolEval:
    """One universe item; ``up_today`` sets today's last 5m close vs the
    previous daily close (None leaves both inputs missing)."""
    daily: list[DailyBar] = []
    today_5m: list[Bar5m] = []
    if up_today is not None:
        prev = Decimal("100")
        daily.append(
            DailyBar(
                symbol=symbol,
                date=TODAY - timedelta(days=1),
                open=prev,
                high=prev,
                low=prev,
                close=prev,
                volume=1000,
            )
        )
        last = prev * (Decimal("1.01") if up_today else Decimal("0.99"))
        today_5m.append(
            Bar5m(
                symbol=symbol,
                start=datetime(2026, 10, 15, 15, 55, tzinfo=NY_TZ),
                open=last,
                high=last,
                low=last,
                close=last,
                volume=1000,
                feed="sip",
            )
        )
    return SymbolEval(
        symbol=symbol,
        snapshot=snap,
        rules=RulesResult(symbol, "pass", None, []),
        bars_1h=[],
        daily=daily,
        today_5m=today_5m,
        price=snap.price,
        day_change_pct=Decimal("0.5"),
    )


def test_breadth_shares_over_25_symbols() -> None:
    items = []
    for i in range(25):
        # Distinct pass/fail mix per metric so every share differs.
        above50 = i < 15
        above200 = i < 10
        ema_up = i < 5
        up_today = i < 12
        snap = _snap(
            f"S{i:02d}",
            close="100" if above50 else "50",
            sma50="90" if above50 else "200",
            sma200="80" if above200 else "200",
            ema_fast="101" if ema_up else "99",
            ema_slow="100",
        )
        items.append(_eval(f"S{i:02d}", snap, up_today=up_today))
    fields = breadth_fields(items, min_symbols=20)
    assert fields["breadth_above_sma50_pct"] == Decimal("60.00")
    assert fields["breadth_above_sma200_pct"] == Decimal("40.00")
    assert fields["breadth_ema_up_pct"] == Decimal("20.00")
    assert fields["breadth_up_today_pct"] == Decimal("48.00")


def test_breadth_null_below_min_symbols() -> None:
    items = [
        _eval(
            f"S{i:02d}",
            _snap(f"S{i:02d}"),
            up_today=True,
        )
        for i in range(19)
    ]
    fields = breadth_fields(items, min_symbols=20)
    for name in BREADTH_FIELD_NAMES:
        assert fields[name] is None, name


def test_breadth_share_null_when_inputs_sparse() -> None:
    """25 symbols but only 19 carry 1h EMA inputs: that share is null
    while the others compute (docs/SPEC.md, Breadth and sectors)."""
    items = []
    for i in range(25):
        ema = ("101", "100") if i < 19 else (None, None)
        snap = _snap(
            f"S{i:02d}",
            ema_fast=ema[0],
            ema_slow=ema[1],
        )
        items.append(_eval(f"S{i:02d}", snap, up_today=True))
    fields = breadth_fields(items, min_symbols=20)
    assert fields["breadth_ema_up_pct"] is None
    assert fields["breadth_above_sma50_pct"] == Decimal("100.00")
    assert fields["breadth_up_today_pct"] == Decimal("100.00")


# ---------------------------------------------------------------------------
# sector table — ranks, missing bars, decision context
# ---------------------------------------------------------------------------


async def _seed_sector_bars(
    market_data: MarketDataService, calendar: FakeCalendarSource
) -> list[Any]:
    sessions = _sessions(calendar, 5)
    # SPY flat; each ETF drifts by a distinct amount so ranks are unique.
    _seed_symbol_bars(market_data, "SPY", sessions, 500.0, 0.0)
    for i, (_sector, etf) in enumerate(SECTOR_ETFS.items()):
        _seed_symbol_bars(
            market_data, etf, sessions, 100.0, 0.001 * (i + 1)
        )
    return sessions


async def test_sector_table_ranks_all_eleven(
    session_factory: sessionmaker[Session],
) -> None:
    market_data, calendar, cfg = _market(session_factory)
    await _seed_sector_bars(market_data, calendar)
    service = _breadth(market_data, cfg)
    market = service.cycle_market([], CYCLE_NOW)

    sectors = market.sectors
    assert set(sectors) == set(SECTOR_ETFS)
    ranks = sorted(
        entry["rank"] for entry in sectors.values() if entry["rank"]
    )
    assert ranks == list(range(1, 12))
    for sector, entry in sectors.items():
        assert entry["etf"] == SECTOR_ETFS[sector]
        assert entry["rs_vs_spy_pct"] is not None
    # Strongest drift ranks first: Real Estate drifted most (+0.011).
    assert sectors["Real Estate"]["rank"] == 1
    assert sectors["Technology"]["rank"] == 11


async def test_sector_missing_bars_leave_null_and_rank_rest(
    session_factory: sessionmaker[Session],
) -> None:
    market_data, calendar, cfg = _market(session_factory)
    sessions = _sessions(calendar, 5)
    _seed_symbol_bars(market_data, "SPY", sessions, 500.0, 0.0)
    for i, (_sector, etf) in enumerate(SECTOR_ETFS.items()):
        if etf == "XLE":
            continue  # Energy's ETF never downloaded
        _seed_symbol_bars(market_data, etf, sessions, 100.0, 0.001 * (i + 1))
    service = _breadth(market_data, cfg)
    market = service.cycle_market([], CYCLE_NOW)

    energy = market.sectors["Energy"]
    assert energy["etf"] == "XLE"
    assert energy["rs_vs_spy_pct"] is None
    assert energy["rank"] is None
    ranks = sorted(
        entry["rank"] for entry in market.sectors.values() if entry["rank"]
    )
    assert ranks == list(range(1, 11))


async def test_decision_context_stock_vs_sector(
    session_factory: sessionmaker[Session],
) -> None:
    market_data, calendar, cfg = _market(session_factory)
    sessions = _sessions(calendar, 5)
    _seed_symbol_bars(market_data, "SPY", sessions, 500.0, 0.0)
    # XLK (Technology) +0.5%/session; the stock +1.0%/session → positive
    # rs_vs_sector and rs_vs_spy.
    _seed_symbol_bars(market_data, "XLK", sessions, 100.0, 0.005)
    for _sector, etf in SECTOR_ETFS.items():
        if etf != "XLK":
            _seed_symbol_bars(market_data, etf, sessions, 100.0, 0.001)
    # The stock's own 1h closes: reuse the seeded series via the cache.
    service = _breadth(market_data, cfg)
    market = service.cycle_market([], CYCLE_NOW)

    # Stock 1h bars: same shape as the ETF helper but +1%/session.
    stock_1h = [
        b
        for b in market_data.get_1h_bars_range("XLK", sessions)
        if b.complete and b.end <= CYCLE_NOW
    ]
    assert stock_1h  # sanity: XLK bars exist
    ctx = market.decision_context("Technology", stock_1h)
    assert ctx["sector_etf"] == "XLK"
    assert ctx["sector_rank"] is not None
    assert ctx["sector_rs_vs_spy_pct"] is not None
    # Stock == XLK here → rs_vs_sector is 0.
    assert ctx["rs_vs_sector_pct"] == pytest.approx(0.0, abs=1e-6)

    # A stock that outperforms its ETF: seed AAPL-like series +2%/session.
    _seed_symbol_bars(market_data, "MSFT", sessions, 100.0, 0.02)
    msft_1h = [
        b
        for b in market_data.get_1h_bars_range("MSFT", sessions)
        if b.complete and b.end <= CYCLE_NOW
    ]
    ctx2 = market.decision_context("Technology", msft_1h)
    assert ctx2["rs_vs_sector_pct"] is not None
    assert ctx2["rs_vs_sector_pct"] > 0
    # And it equals the shared function on the matched closes.
    etf_bars = market._etf_bars["XLK"]
    bench = {b.start: b.close for b in etf_bars}
    matched = [(b.close, bench[b.start]) for b in msft_1h if b.start in bench]
    expected = relative_strength_pct(
        [p[0] for p in matched], [p[1] for p in matched], 20
    )
    assert ctx2["rs_vs_sector_pct"] == pytest.approx(float(expected or 0))


async def test_decision_context_index_sector_is_all_null(
    session_factory: sessionmaker[Session],
) -> None:
    market_data, calendar, cfg = _market(session_factory)
    await _seed_sector_bars(market_data, calendar)
    service = _breadth(market_data, cfg)
    market = service.cycle_market([], CYCLE_NOW)
    ctx = market.decision_context("Index", [])
    assert ctx == {
        "sector_etf": None,
        "sector_rank": None,
        "sector_rs_vs_spy_pct": None,
        "rs_vs_sector_pct": None,
    }


# ---------------------------------------------------------------------------
# reference-symbol exclusion
# ---------------------------------------------------------------------------


def test_universe_excludes_sector_etfs(
    session_factory: sessionmaker[Session],
) -> None:
    calendar = FakeCalendarSource()
    cfg = AppConfig()
    cfg.yaml["universe"]["symbols"] = [
        "AAPL",
        *SECTOR_ETFS.values(),
        "VIXY",
        "MSFT",
    ]
    market_data = MarketDataService(
        _sources(calendar), calendar, cfg, session_factory=session_factory
    )
    symbols = market_data.universe()
    for etf in SECTOR_ETFS.values():
        assert etf not in symbols
    assert "VIXY" not in symbols
    assert {"AAPL", "MSFT", "SPY"} <= set(symbols)
    assert set(market_data.reference_symbols()) == set(
        SECTOR_ETFS.values()
    ) | {"VIXY"}


def test_universe_excludes_sector_etf_even_when_held(
    session_factory: sessionmaker[Session],
) -> None:
    from app.db.models import Position

    calendar = FakeCalendarSource()
    cfg = AppConfig()
    cfg.yaml["universe"]["symbols"] = ["AAPL"]
    market_data = MarketDataService(
        _sources(calendar), calendar, cfg, session_factory=session_factory
    )
    with session_factory() as session:
        session.add(
            Position(
                symbol="XLK",
                qty=1,
                avg_entry_price=Decimal("100"),
                opened_at=datetime(2026, 10, 14, 15, 0),
            )
        )
        session.commit()
    assert "XLK" not in market_data.universe()


async def test_screened_mode_never_screens_sector_etfs(
    session_factory: sessionmaker[Session],
    tmp_path: Any,
) -> None:
    """A sector ETF whose bars would pass every screen check still gets
    no screen row and never enters the cycle universe (item 068)."""
    from tests.test_universe import (
        SCREEN_DATE,
        SCREEN_TIME,
        StubBars,
        daily_series,
        make_config,
        make_service,
    )

    clock = FakeClock(SCREEN_TIME)
    calendar = FakeCalendarSource()
    config = make_config(
        tmp_path,
        [
            ("SPY", "Index"),
            ("AAPL", "Technology"),
            ("XLK", "Technology"),
        ],
    )
    bars = StubBars(calendar)
    service, market_data, *_ = make_service(
        session_factory, clock, calendar, config, bars=bars
    )
    sessions = market_data.recent_sessions(SCREEN_DATE, 260)
    bars.daily["AAPL"] = daily_series(
        "AAPL", sessions, Decimal("100"), Decimal("120")
    )
    bars.daily["XLK"] = daily_series(
        "XLK", sessions, Decimal("100"), Decimal("130")
    )
    bars.daily["SPY"] = daily_series(
        "SPY", sessions, Decimal("500"), Decimal("500"), volume=3_000_000
    )
    result = await service.run_screen()
    assert "failed" not in result and "skipped" not in result
    with session_factory() as session:
        from app.db.models import UniverseScreenRow

        screened = {
            row.symbol
            for row in session.execute(select(UniverseScreenRow)).scalars()
        }
    assert "XLK" not in screened
    assert market_data.universe() == ["AAPL", "SPY"]


# ---------------------------------------------------------------------------
# decision market_context + snapshot/hash invariance
# ---------------------------------------------------------------------------


def test_market_context_never_touches_snapshot_or_hash(
    session_factory: sessionmaker[Session],
) -> None:
    """The same decision stored with and without market_context keeps an
    identical input snapshot and prompt hash — the fields live outside
    the model's input (docs/SPEC.md, Breadth and sectors)."""
    config = AppConfig()
    decide = DecisionService(
        FakeDecisionClient(), config, session_factory=session_factory
    )
    snapshot_json = {"symbol": "MSFT", "context": "candidate", "price": "1"}
    ctx = {
        "sector_etf": "XLK",
        "sector_rank": 2,
        "sector_rs_vs_spy_pct": 1.5,
        "rs_vs_sector_pct": -0.3,
    }
    cycle_id = _seed_cycle(session_factory)
    with session_factory() as session:
        plain = decide.record_no_call(
            session,
            cycle_id=cycle_id,
            symbol="MSFT",
            context="candidate",
            snapshot_json=snapshot_json,
            rules=None,
            reason="call_cap",
            detail="call cap reached",
        )
        with_ctx = decide.record_no_call(
            session,
            cycle_id=cycle_id,
            symbol="AAPL",
            context="candidate",
            snapshot_json=snapshot_json,
            rules=None,
            reason="call_cap",
            detail="call cap reached",
            market_context=ctx,
        )
        session.commit()
        plain_snapshot = plain.input_snapshot
        plain_hash = plain.prompt_hash
        plain_mc = plain.market_context
        ctx_snapshot = with_ctx.input_snapshot
        ctx_hash = with_ctx.prompt_hash
        stored_mc = with_ctx.market_context
    assert plain_snapshot == ctx_snapshot
    assert plain_hash == ctx_hash
    assert plain_hash  # the loaded prompt's sha256
    assert stored_mc == ctx
    assert plain_mc is None


def _seed_cycle(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=datetime(2026, 10, 15, 16, 46),
            status="done",
        )
        session.add(cycle)
        session.commit()
        return cycle.id


# ---------------------------------------------------------------------------
# cycle persistence
# ---------------------------------------------------------------------------


async def test_cycle_stores_breadth_sectors_and_market_context(
    session_factory: sessionmaker[Session],
) -> None:
    """A full cycle on fakes persists the breadth/sector columns and the
    candidate's market_context — held decisions get none (item 068)."""
    from tests.test_decide_cycle import (
        CYCLE_1246,
        _evaluation,
        _item,
        _runner,
    )

    clock = FakeClock(CYCLE_1246)
    market_data_holder: dict[str, Any] = {}

    runner = _runner(
        session_factory,
        _evaluation(_item("MSFT", "pass"), _item("JPM", "held"), held={"JPM"}),
        clock=clock,
        universe=["MSFT", "JPM"],
    )
    market_data_holder["md"] = runner.market_data
    calendar = runner.calendar
    sessions = _sessions(calendar, 5)  # type: ignore[arg-type]
    _seed_symbol_bars(runner.market_data, "SPY", sessions, 500.0, 0.0)
    for i, (_sector, etf) in enumerate(SECTOR_ETFS.items()):
        _seed_symbol_bars(
            runner.market_data, etf, sessions, 100.0, 0.001 * (i + 1)
        )
    runner.breadth = BreadthService(runner.market_data, runner.config)

    report = await runner.run(CYCLE_1246)
    assert report.status == "done"
    with session_factory() as session:
        cycle = session.execute(
            select(Cycle).where(Cycle.kind == "cycle")
        ).scalar_one()
        # Two items < min_symbols → breadth shares stay null.
        for name in BREADTH_FIELD_NAMES:
            assert getattr(cycle, name) is None, name
        sectors: dict[str, Any] = cycle.sectors or {}
        assert set(sectors) == set(SECTOR_ETFS)
        ranks = sorted(
            e["rank"] for e in sectors.values() if e["rank"]
        )
        assert ranks == list(range(1, 12))

        decisions = {
            d.symbol: d
            for d in session.execute(select(Decision)).scalars()
        }
    msft = decisions["MSFT"]
    msft_mc: dict[str, Any] = msft.market_context or {}
    assert msft.market_context is not None
    assert msft_mc["sector_etf"] == "XLK"
    assert msft_mc["sector_rank"] == sectors["Technology"]["rank"]
    assert msft_mc["sector_rs_vs_spy_pct"] is not None
    # MSFT has no seeded 1h bars in this harness → rs_vs_sector is null.
    assert msft_mc["rs_vs_sector_pct"] is None
    # The model snapshot is untouched by market_context.
    assert "market_context" not in (msft.input_snapshot or {})
    assert "breadth" not in json.dumps(msft.input_snapshot or {})
    jpm = decisions["JPM"]
    assert jpm.market_context is None  # held decisions carry none

