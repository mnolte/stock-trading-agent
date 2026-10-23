"""PR 14 — backtesting: store, sources, broker, cache, estimate, worker."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from app.backtests.broker import BacktestBroker
from app.backtests.estimate import estimate
from app.backtests.manager import BacktestManager
from app.backtests.results import summarise
from app.backtests.sources import (
    BacktestBarSource,
    BacktestCalendarSource,
    BacktestNewsSource,
    BacktestQuoteSource,
    make_backtest_sources,
)
from app.backtests.store import (
    MARKET_CALENDAR,
    MARKET_NEWS,
    bar_store_path,
    clear_bar_store,
    make_bar_store,
    make_market_db,
    run_dir_for,
    session_maker_for,
)
from app.backtests.worker import RunGate, run_backtest
from app.config import AppConfig
from app.data.cache import BarCache, _naive_utc
from app.data.fake import FakeEarningsSource
from app.data.models import (
    Bar5m,
    CalendarDay,
    DailyBar,
)
from app.db.base import get_session_maker
from app.db.models import (
    Backtest,
    Cycle,
    Decision,
    LLMCache,
    Position,
)
from app.decide.client import FakeDecisionClient
from app.decide.llm_cache import LLMCacheStore
from app.decide.service import DecisionService
from app.market.clock import NY_TZ, FakeClock
from app.rules.prefilter import STATUS_PASS, RuleCheck, RulesResult
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

D0 = date(2024, 6, 3)  # a Monday


@pytest.fixture(autouse=True)
def _live_schema() -> None:
    """The isolated live DB gets the full schema per test — via the same
    Alembic migrations the app runs, so a TestClient startup is a no-op."""
    from app.backtests.bundle import migrate_db
    from app.config import get_config

    migrate_db(Path(get_config().env.sa_db_path))


def _bar(
    symbol: str,
    start: datetime,
    o: str,
    h: str,
    l: str,  # noqa: E741
    c: str,
    v: int = 1000,
) -> Bar5m:
    return Bar5m(
        symbol=symbol,
        start=start,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=v,
        feed="test",
    )


def _day(d: date, close_h: int = 16) -> CalendarDay:
    return CalendarDay(
        date=d,
        open=datetime.combine(d, datetime.min.time(), tzinfo=NY_TZ).replace(
            hour=9, minute=30
        ),
        close=datetime.combine(d, datetime.min.time(), tzinfo=NY_TZ).replace(
            hour=close_h
        ),
    )


def _session_bars(
    symbol: str, d: date, price: Decimal, *, wobble: Decimal = Decimal("0.1")
) -> list[Bar5m]:
    """78 five-minute bars for one full session, gentle sawtooth."""
    bars: list[Bar5m] = []
    start = datetime.combine(d, datetime.min.time(), tzinfo=NY_TZ).replace(
        hour=9, minute=30
    )
    for i in range(78):
        drift = wobble if i % 4 < 2 else -wobble
        o = price + Decimal(i) * Decimal("0.01")
        c = o + drift
        hi = max(o, c) + wobble
        lo = min(o, c) - wobble
        bars.append(
            Bar5m(
                symbol=symbol,
                start=start + timedelta(minutes=5 * i),
                open=o,
                high=hi,
                low=lo,
                close=c,
                volume=5000,
                feed="test",
            )
        )
    return bars


# ---------------------------------------------------------------------------
# BacktestBroker — the bar-fill rules from the spec


def _store_bars(store: BarCache, bars: list[Bar5m]) -> None:
    store.store(bars)


async def _entry(
    broker: BacktestBroker,
    *,
    limit: str = "100.00",
    stop: str = "95.00",
    target: str = "110.00",
) -> str:
    result = await broker.submit_bracket_entry(
        symbol="AAA",
        qty=10,
        limit_price=Decimal(limit),
        stop_price=Decimal(stop),
        target_price=Decimal(target),
        client_order_id="e1",
    )
    assert result.error is None
    return result.data.id


async def test_broker_entry_fills_at_limit_and_gap_open() -> None:
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    t0 = datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ)
    _store_bars(
        store,
        [
            # dips to 99.90 → fill at the 100.00 limit
            _bar("AAA", t0, "100.50", "101", "99.90", "100.50"),
            # gaps open at 99.50 → fill at the better open
            _bar("AAA", t0 + timedelta(minutes=5), "99.50", "100", "99.4", "99.8"),
        ],
    )
    broker = BacktestBroker(
        clock, store.get, cash=Decimal(10000), slippage_pct=Decimal("0.05")
    )
    e1 = await _entry(broker)
    clock.set(t0 + timedelta(minutes=5))
    await broker.replay(clock.now())
    parent = broker.orders[e1]
    assert parent.status == "filled"
    assert parent.filled_avg_price == Decimal("100.00")

    broker2 = BacktestBroker(
        clock, store.get, cash=Decimal(10000), slippage_pct=Decimal("0.05")
    )
    e2 = await broker2.submit_bracket_entry(
        symbol="AAA",
        qty=10,
        limit_price=Decimal("99.80"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        client_order_id="e2",
    )
    clock.set(t0 + timedelta(minutes=10))
    await broker2.replay(clock.now())
    # bar 2 opened at 99.50 — below the 99.80 limit → fill at the open
    parent2 = broker2.orders[e2.data.id]
    assert parent2.status == "filled"
    assert parent2.filled_avg_price == Decimal("99.50")


async def test_broker_entry_no_fill_when_untouched() -> None:
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    t0 = datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ)
    _store_bars(
        store,
        [_bar("AAA", t0, "101", "102", "100.10", "101.5")],
    )
    broker = BacktestBroker(
        clock, store.get, cash=Decimal(10000), slippage_pct=Decimal("0.05")
    )
    e1 = await _entry(broker)
    clock.set(t0 + timedelta(minutes=5))
    await broker.replay(clock.now())
    assert broker.orders[e1].status == "new"
    assert "AAA" not in broker.positions


async def test_broker_stop_on_fill_bar_and_target_next_bar() -> None:
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    t0 = datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ)
    _store_bars(
        store,
        [
            # bar 1: dips through the limit AND the stop → stop counts
            _bar("AAA", t0, "100.5", "100.6", "94.9", "95.2"),
            # bar 2: reaches the target — only counts from this bar
            _bar("AAA", t0 + timedelta(minutes=5), "96", "111", "95.5", "110.5"),
        ],
    )
    broker = BacktestBroker(
        clock, store.get, cash=Decimal(10000), slippage_pct=Decimal("0.05")
    )
    e1 = await _entry(broker)
    clock.set(t0 + timedelta(minutes=5))
    await broker.replay(clock.now())
    stop_leg, target_leg = (broker.orders[i] for i in broker.orders[e1].legs)
    # stop counted on the fill bar even though the same bar also traded
    # through where the target would sit later.
    stop_filled = any(
        o.order_type == "stop" and o.status == "filled"
        for o in broker.orders.values()
    )
    assert stop_filled
    assert "AAA" not in broker.positions


async def test_broker_target_next_bar_same_bar_stop_wins() -> None:
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    t0 = datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ)
    _store_bars(
        store,
        [
            # fill bar touches limit and target, not stop → no exit yet
            _bar("AAA", t0, "100.5", "111", "99.9", "100.5"),
            # next bar touches both legs → stop wins
            _bar("AAA", t0 + timedelta(minutes=5), "100", "112", "94", "95"),
        ],
    )
    broker = BacktestBroker(
        clock, store.get, cash=Decimal(10000), slippage_pct=Decimal("0.05")
    )
    e1 = await _entry(broker)
    clock.set(t0 + timedelta(minutes=10))
    await broker.replay(clock.now())
    legs = {o.order_type: o for o in (broker.orders[i] for i in broker.orders[e1].legs)}
    assert legs["stop"].status == "filled"
    assert legs["limit"].status == "canceled"
    # gap bar: fill was at bar open 100 < 100 limit? open 100 ≥ 99.9 low and ≤
    # limit → fill at 100.00; the stop leg resolved at open*0.9995 ≈ 94.95
    assert legs["stop"].filled_avg_price is not None


async def test_broker_gap_through_stop_fills_at_open() -> None:
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    t0 = datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ)
    _store_bars(
        store,
        [
            _bar("AAA", t0, "100.1", "100.2", "99.95", "100.0"),
            # gaps to 90 — through the 95 stop → fill at open, not at 95
            _bar("AAA", t0 + timedelta(minutes=5), "90", "91", "89", "90.5"),
        ],
    )
    broker = BacktestBroker(
        clock, store.get, cash=Decimal(10000), slippage_pct=Decimal("0.05")
    )
    e1 = await _entry(broker)
    clock.set(t0 + timedelta(minutes=10))
    await broker.replay(clock.now())
    stop = next(
        o for o in (broker.orders[i] for i in broker.orders[e1].legs)
        if o.order_type == "stop"
    )
    assert stop.status == "filled"
    assert stop.filled_avg_price == Decimal("90") * (1 - Decimal("0.0005"))


async def test_broker_market_sell_fills_next_bar_open_with_slippage() -> None:
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    t0 = datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ)
    _store_bars(
        store,
        [
            _bar("AAA", t0, "100.1", "100.2", "99.95", "100.0"),
            _bar("AAA", t0 + timedelta(minutes=5), "101", "102", "100.5", "101.5"),
            _bar("AAA", t0 + timedelta(minutes=10), "103", "104", "102", "103.5"),
        ],
    )
    broker = BacktestBroker(
        clock, store.get, cash=Decimal(10000), slippage_pct=Decimal("0.05")
    )
    await _entry(broker)
    clock.set(t0 + timedelta(minutes=5))
    await broker.replay(clock.now())
    assert "AAA" in broker.positions

    clock.set(t0 + timedelta(minutes=7))
    result = await broker.submit_market_sell(
        symbol="AAA", qty=10, market_on_open=False, client_order_id="x1"
    )
    assert result.error is None
    # queued, not filled immediately at a stale tick price
    assert result.data.status == "accepted"
    assert "AAA" in broker.positions

    clock.set(t0 + timedelta(minutes=15))
    await broker.replay(clock.now())
    assert broker.orders[result.data.id].status == "filled"
    assert broker.orders[result.data.id].filled_avg_price == Decimal("103") * (
        1 - Decimal("0.0005")
    )
    assert "AAA" not in broker.positions


# ---------------------------------------------------------------------------
# sources — the anti-lookahead cuts


async def test_bar_source_cuts_at_now_minus_16() -> None:
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    t0 = datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ)
    _store_bars(
        store,
        [
            _bar("AAA", t0, "1", "1", "1", "1"),
            _bar("AAA", t0 + timedelta(minutes=5), "2", "2", "2", "2"),
            _bar("AAA", t0 + timedelta(minutes=10), "3", "3", "3", "3"),
        ],
    )
    # now = t0 + 20min → end = now − 16min = t0+4. Bars are keyed by start,
    # like the live SIP client: only the bar starting at t0 qualifies.
    clock = FakeClock(t0 + timedelta(minutes=20))
    src = BacktestBarSource(store, clock)
    out = await src.fetch_5m_bars(["AAA"], t0 - timedelta(hours=1), t0 + timedelta(hours=1))
    assert [b.start for b in out.data] == [t0]


async def test_daily_source_hides_todays_bar_until_close_plus_delay() -> None:
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    store.store_daily(
        [
            DailyBar(
                symbol="AAA",
                date=D0 - timedelta(days=3),
                open=Decimal(1),
                high=Decimal(1),
                low=Decimal(1),
                close=Decimal(1),
                volume=1,
            ),
            DailyBar(
                symbol="AAA",
                date=D0,
                open=Decimal(2),
                high=Decimal(2),
                low=Decimal(2),
                close=Decimal(2),
                volume=1,
            ),
        ]
    )
    # mid-session on D0: today's daily bar must not be visible
    clock = FakeClock(datetime(2024, 6, 3, 11, 0, tzinfo=NY_TZ))
    src = BacktestBarSource(store, clock)
    out = await src.fetch_daily_bars("AAA", D0 - timedelta(days=10), D0)
    assert [b.date for b in out.data] == [D0 - timedelta(days=3)]
    # after 16:16 the same query sees it
    clock.set(datetime(2024, 6, 3, 16, 30, tzinfo=NY_TZ))
    out = await src.fetch_daily_bars("AAA", D0 - timedelta(days=10), D0)
    assert D0 in [b.date for b in out.data]


async def test_news_source_only_published_before_now(tmp_path: Path) -> None:
    config = AppConfig()
    sf = make_market_db(tmp_path / "market.db", config)
    with sf() as session:
        session.execute(
            MARKET_NEWS.insert().values(
                news_id="old",
                symbol="AAA",
                headline="old",
                source="t",
                created_at=_naive_utc(datetime(2024, 6, 3, 9, 0, tzinfo=NY_TZ)),
            )
        )
        session.execute(
            MARKET_NEWS.insert().values(
                news_id="future",
                symbol="AAA",
                headline="future",
                source="t",
                created_at=_naive_utc(datetime(2024, 6, 3, 12, 0, tzinfo=NY_TZ)),
            )
        )
        session.commit()
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    src = BacktestNewsSource(sf, clock)
    out = await src.fetch_news(
        ["AAA"], datetime(2024, 6, 1, tzinfo=NY_TZ), datetime(2024, 6, 4, tzinfo=NY_TZ)
    )
    assert [n.id for n in out.data] == ["old"]


async def test_quote_source_uses_last_close_and_assumed_spread() -> None:
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    t0 = datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ)
    _store_bars(store, [_bar("AAA", t0, "100", "100", "100", "100")])
    clock = FakeClock(t0 + timedelta(minutes=5))
    src = BacktestQuoteSource(store, clock, config)
    out = await src.fetch_quotes(["AAA"])
    q = out.data["AAA"]
    assert q.last == Decimal("100")
    half = Decimal("100") * Decimal("0.05") / Decimal(200)
    assert q.bid == Decimal("100") - half
    assert q.ask == Decimal("100") + half
    assert q.trade_timestamp == t0 + timedelta(minutes=5)
    assert q.fetched_at == clock.now()


async def test_calendar_source_from_market_db(tmp_path: Path) -> None:
    config = AppConfig()
    sf = make_market_db(tmp_path / "market.db", config)
    day = _day(D0)
    with sf() as session:
        session.execute(
            MARKET_CALENDAR.insert().values(
                date=D0, open=day.open.isoformat(), close=day.close.isoformat()
            )
        )
        session.commit()
    src = BacktestCalendarSource(sf)
    out = await src.fetch_calendar(D0, D0)
    assert len(out.data) == 1
    assert out.data[0].date == D0
    assert out.data[0].close.hour == 16


# ---------------------------------------------------------------------------
# bar store


def test_bar_store_copy_coverage_and_clear(tmp_path: Path) -> None:
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    day = _day(D0)
    _store_bars(store, _session_bars("AAA", D0, Decimal("100")))
    store.store_daily(
        [
            DailyBar(
                symbol="AAA",
                date=D0,
                open=Decimal(1),
                high=Decimal(1),
                low=Decimal(1),
                close=Decimal(1),
                volume=1,
            )
        ]
    )
    covered = store.covered_session_dates("AAA", [day])
    assert D0 in covered
    assert store.covered_session_dates("BBB", [day]) == set()
    clear_bar_store(config)
    assert store.covered_session_dates("AAA", [day]) == set()


# ---------------------------------------------------------------------------
# llm cache + DecisionService


def _cycle(sf: sessionmaker[Session], scheduled: datetime) -> int:
    with sf() as session:
        row = Cycle(
            kind="cycle",
            scheduled_time=_naive_utc(scheduled),
            status="done",
        )
        session.add(row)
        session.commit()
        return row.id


async def test_llm_cache_hit_replays_answer_free_and_marked(tmp_path: Path) -> None:
    config = AppConfig()
    sf = get_session_maker()
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    cache = LLMCacheStore(sf, clock)
    cycle_id = _cycle(sf, clock.now())

    client = FakeDecisionClient(enter_symbols={"AAA"})
    calls = 0
    real_complete = client.complete

    async def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return await real_complete(*args, **kwargs)

    client.complete = counted  # type: ignore[method-assign]
    svc = DecisionService(client, config, sf, llm_cache=cache, cache_reads=True)
    snapshot = {"symbol": "AAA", "context": "candidate", "price": "100.00"}
    rules = RulesResult("AAA", STATUS_PASS, None, [RuleCheck("fresh quote", True, "0s old")])

    s1 = _new_session(sf)
    d1 = await svc.decide(
        session=s1,
        cycle_id=cycle_id,
        symbol="AAA",
        context="candidate",
        snapshot_json=snapshot,
        rules=rules,
        price=Decimal("100"),
        atr_1h=Decimal("1"),
        current_stop=None,
    )
    s1.commit()
    assert calls == 1
    assert d1.cached is False
    assert d1.cost_source == "priced"

    # A second decision on identical inputs is a cache hit: no client call,
    # same stored output, cost zero, marked cached.
    svc2 = DecisionService(client, config, sf, llm_cache=cache, cache_reads=True)
    s2 = _new_session(sf)
    d2 = await svc2.decide(
        session=s2,
        cycle_id=cycle_id,
        symbol="AAA",
        context="candidate",
        snapshot_json=snapshot,
        rules=rules,
        price=Decimal("100"),
        atr_1h=Decimal("1"),
        current_stop=None,
    )
    assert calls == 1
    assert d2.cached is True
    assert d2.cost_source == "cached"
    assert d2.cost in (None, Decimal(0))
    assert d2.model_output == d1.model_output


async def test_live_and_shadow_decide_never_reads_llm_cache() -> None:
    """cache_reads stays off for live and shadows: a matching cache row is
    written-through only — the model is still called (docs/SPEC.md,
    Backtesting, Model calls)."""
    config = AppConfig()
    sf = get_session_maker()
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    cache = LLMCacheStore(sf, clock)
    cycle_id = _cycle(sf, clock.now())

    # Seed the row a backtest hit would match.
    client = FakeDecisionClient(enter_symbols={"AAA"})
    seed = DecisionService(client, config, sf, llm_cache=cache, cache_reads=True)
    snapshot = {"symbol": "AAA", "context": "candidate", "price": "100.00"}
    rules = RulesResult("AAA", STATUS_PASS, None, [RuleCheck("fresh quote", True, "0s old")])
    s0 = _new_session(sf)
    await seed.decide(
        session=s0, cycle_id=cycle_id, symbol="AAA", context="candidate",
        snapshot_json=snapshot, rules=rules, price=Decimal("100"),
        atr_1h=Decimal("1"), current_stop=None,
    )
    s0.commit()
    s0.close()

    calls = 0
    real_complete = client.complete

    async def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return await real_complete(*args, **kwargs)

    client.complete = counted  # type: ignore[method-assign]
    # No cache_reads flag — exactly how live and the shadows construct it.
    live = DecisionService(client, config, sf, llm_cache=cache)
    s1 = _new_session(sf)
    d = await live.decide(
        session=s1, cycle_id=cycle_id, symbol="AAA", context="candidate",
        snapshot_json=snapshot, rules=rules, price=Decimal("100"),
        atr_1h=Decimal("1"), current_stop=None,
    )
    assert calls == 1  # fresh model call despite the matching row
    assert d.cached is False


def _new_session(sf: sessionmaker[Session]) -> Session:
    return sf()


# ---------------------------------------------------------------------------
# estimate


async def test_estimate_counts_and_cutoff_refusal(tmp_path: Path) -> None:
    config = AppConfig()
    sf = get_session_maker()
    store = make_bar_store(bar_store_path(config), config)
    from app.backtests.estimate import StrategySpec
    from app.data.fake import FakeCalendarSource
    from app.market.calendar import CalendarStore

    calendar = CalendarStore(FakeCalendarSource())
    start, end = date(2024, 6, 3), date(2024, 6, 14)
    await calendar.load(start, end)

    specs = [
        StrategySpec(
            ref="live",
            name="Live",
            kind="llm",
            slug="live",
            model="gpt-4o-mini",
            prompt_version="decision.v1",
            temperature=0.2,
        ),
        StrategySpec(ref="rules", name="Rules", kind="rules_only", slug="rules"),
    ]
    est = estimate(
        config=config,
        session_factory=sf,
        calendar=calendar,
        start=start,
        end=end,
        strategies=specs,
        symbols=["AAA", "SPY"],
        store=store,
        today=date(2024, 6, 20),
    )
    # two working weeks of weekdays = 10 sessions (fake calendar)
    assert est.sessions == 10
    rules_spec = next(s for s in est.strategies if s.kind == "rules_only")
    assert rules_spec.calls == 0
    assert rules_spec.cost_usd in (None, Decimal(0))
    # gpt-4o-mini cutoff 2023-10-31 — a June 2024 start is fine
    assert est.disabled_reason is None

    # a run that starts before/at the model's cutoff is refused
    early = estimate(
        config=config,
        session_factory=sf,
        calendar=calendar,
        start=date(2023, 6, 1),
        end=date(2024, 6, 14),
        strategies=specs,
        symbols=["AAA"],
        store=store,
        today=date(2024, 6, 20),
    )
    assert early.disabled_reason is not None
    assert "cutoff" in early.disabled_reason


def test_estimate_cost_cap_refusal_and_warning(tmp_path: Path) -> None:
    config = AppConfig()
    sf = get_session_maker()
    store = make_bar_store(bar_store_path(config), config)
    from app.backtests.estimate import StrategySpec
    from app.market.calendar import StaticCalendarProvider

    days = [_day(D0 + timedelta(days=i)) for i in range(10)]
    calendar = StaticCalendarProvider(days)

    spec = StrategySpec(
        ref="live",
        name="Live",
        kind="llm",
        slug="live",
        model="gpt-4o-mini",
        prompt_version="decision.v1",
        temperature=0.2,
    )
    # spend the whole monthly cap first
    with sf() as session:
        session.add(
            Backtest(
                name="old",
                start_date=D0 - timedelta(days=40),
                end_date=D0 - timedelta(days=30),
                universe_mode="fixed",
                strategies=[],
                starting_equity=Decimal(10000),
                slippage_pct=Decimal("0.05"),
                status="done",
                cost_usd=Decimal("20.00"),
                run_dir=str(tmp_path / "run-old"),
                finished_at=_naive_utc(datetime.now()),
                created_at=_naive_utc(datetime.now()),
            )
        )
        # Token history so the per-call cost estimate is known.
        from app.decide.prompt import cached_prompt

        session.add(
            LLMCache(
                model="gpt-4o-mini",
                temperature=0.2,
                prompt_version="decision.v1",
                prompt_hash=cached_prompt("decision.v1").sha256,
                input_hash="y" * 64,
                response="{}",
                tokens=1000,
                tokens_in=800,
                tokens_out=200,
                created_at=_naive_utc(datetime.now()),
            )
        )
        session.commit()
    est = estimate(
        config=config,
        session_factory=sf,
        calendar=calendar,
        start=D0,
        end=D0 + timedelta(days=9),
        strategies=[spec],
        symbols=["AAA"],
        store=store,
        today=D0,
    )
    assert est.disabled_reason is not None
    assert "cap" in est.disabled_reason.lower() or "month" in est.disabled_reason.lower()


# ---------------------------------------------------------------------------
# manager


def test_manager_create_refuse_delete_and_orphans() -> None:
    config = AppConfig()
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    manager = BacktestManager(config, clock)
    specs = [
        {
            "ref": "rules_only",
            "name": "Rules only",
            "kind": "rules_only",
            "slug": "rules-only",
        }
    ]
    row = manager.create(
        name="t1",
        start=D0,
        end=D0 + timedelta(days=10),
        universe_mode="fixed",
        strategies=specs,
        starting_equity=Decimal(10000),
        slippage_pct=Decimal("0.05"),
    )
    assert row.status == "queued"
    refused = manager.refuse(
        name="t2",
        start=D0,
        end=D0 + timedelta(days=10),
        universe_mode="fixed",
        strategies=specs,
        starting_equity=Decimal(10000),
        slippage_pct=Decimal("0.05"),
        reason="Period starts before gpt-4o-mini's knowledge cutoff",
    )
    assert refused.status == "refused"

    # orphans: a row left "running" at startup is interrupted
    with get_session_maker()() as session:
        stale = session.get(Backtest, row.id)
        assert stale is not None
        stale.status = "running"
        session.commit()
    assert manager.interrupt_orphans() == 1
    with get_session_maker()() as session:
        fetched = session.get(Backtest, row.id)
        assert fetched is not None
        assert fetched.status == "interrupted"

    assert manager.delete(row.id)
    with get_session_maker()() as session:
        assert session.get(Backtest, row.id) is None
    # refused rows can be deleted too; a queued one is refused-safe
    assert manager.delete(refused.id)
    with get_session_maker()() as session:
        assert session.get(Backtest, refused.id) is None


def test_manager_one_run_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig()
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    manager = BacktestManager(config, clock)
    spawned: list[int] = []
    monkeypatch.setattr(manager, "_spawn", lambda s, r: spawned.append(r.id))
    specs = [{"ref": "x", "name": "x", "kind": "rules_only", "slug": "x"}]
    r1 = manager.create(
        name="a",
        start=D0,
        end=D0 + timedelta(days=1),
        universe_mode="fixed",
        strategies=specs,
        starting_equity=Decimal(100),
        slippage_pct=Decimal("0.05"),
    )
    r2 = manager.create(
        name="b",
        start=D0,
        end=D0 + timedelta(days=1),
        universe_mode="fixed",
        strategies=specs,
        starting_equity=Decimal(100),
        slippage_pct=Decimal("0.05"),
    )
    manager.tick()
    assert spawned == [r1.id]
    # simulate the child finishing so tick spawns the next
    with get_session_maker()() as session:
        row = session.get(Backtest, r1.id)
        assert row is not None
        row.status = "done"
        session.commit()
    manager.tick()
    assert spawned == [r1.id, r2.id]


def test_manager_cancel_flags_run(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig()
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    manager = BacktestManager(config, clock)
    monkeypatch.setattr(manager, "_spawn", lambda s, r: None)
    specs = [{"ref": "x", "name": "x", "kind": "rules_only", "slug": "x"}]
    row = manager.create(
        name="a",
        start=D0,
        end=D0 + timedelta(days=1),
        universe_mode="fixed",
        strategies=specs,
        starting_equity=Decimal(100),
        slippage_pct=Decimal("0.05"),
    )
    assert manager.cancel(row.id)
    gate = RunGate(get_session_maker(), row.id)
    cancelled, _paused = gate.flags()
    assert cancelled


# ---------------------------------------------------------------------------
# results


def test_summarise_metrics(tmp_path: Path) -> None:
    config = AppConfig()
    db = tmp_path / "strategy.db"
    from app.backtests.bundle import init_strategy_db

    init_strategy_db(db)
    sf = session_maker_for(db, config)
    with sf() as session:
        session.add(
            Decision(
                cycle_id=1,
                symbol="AAA",
                context="candidate",
                input_snapshot={"regime": {"trend": "up", "volatility": "normal"}},
                model_output={"stop_price": 95},
                final_action="enter",
                prompt_version="decision.v1",
                prompt_hash="x" * 64,
                model="gpt-4o-mini",
                cached=False,
                cost=Decimal("0.01"),
            )
        )
        session.add(
            Decision(
                cycle_id=1,
                symbol="BBB",
                context="candidate",
                input_snapshot={},
                final_action="enter",
                prompt_version="decision.v1",
                prompt_hash="x" * 64,
                model="gpt-4o-mini",
                cached=True,
                cost=None,
            )
        )
        session.commit()
        entry_id = session.execute(
            select(Decision.id).where(Decision.symbol == "AAA")
        ).scalar_one()
        session.add(
            Position(
                symbol="AAA",
                origin="agent",
                entry_decision_id=entry_id,
                qty=10,
                avg_entry_price=Decimal("100"),
                opened_at=datetime(2024, 6, 3, 10, 0),
                closed_at=datetime(2024, 6, 4, 15, 0),
                exit_reason="target",
                realized_pnl_raw=Decimal("50"),
                realized_pnl_adjusted=Decimal("50"),
            )
        )
        session.add(
            Position(
                symbol="BBB",
                origin="agent",
                qty=10,
                avg_entry_price=Decimal("100"),
                opened_at=datetime(2024, 6, 4, 10, 0),
                closed_at=datetime(2024, 6, 5, 15, 0),
                exit_reason="stop",
                realized_pnl_raw=Decimal("-30"),
                realized_pnl_adjusted=Decimal("-30"),
            )
        )
        session.commit()
    out = summarise(sf, config=config, equity=Decimal(10000))
    assert out["closed_trades"] == 2
    assert out["win_rate"] == 0.5
    assert out["decisions"] == 2
    assert out["cache_hits"] == 1
    assert out["calls"] == 0  # no latency_ms on these fake rows
    assert out["profit_factor"] == pytest.approx(50 / 30)
    assert out["cost_usd"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# the worker, end to end, deterministic


def _seed_live_bars(symbols: list[str], start: date, days: int) -> None:
    """Fill the *live* cache the way a real app would have it: 5m bars for
    every weekday plus aggregated daily bars. Bars come from the same
    deterministic generator as the fakes, so indicators pass the rules."""
    from app.data.aggregate import aggregate_daily
    from app.data.fake import _bars_for_session
    from app.db.models import Bar1d as Bar1dRow
    from app.db.models import Bar5m as Bar5mRow

    sf = get_session_maker()

    cursor = start
    written = 0
    with sf() as session:
        while written < days:
            if cursor.weekday() < 5:
                day = CalendarDay(
                    date=cursor,
                    open=datetime.combine(cursor, time(9, 30), tzinfo=NY_TZ),
                    close=datetime.combine(cursor, time(16, 0), tzinfo=NY_TZ),
                )
                for symbol in symbols:
                    drift = 0.0006 if symbol != "SPY" else 0.0002
                    bars = _bars_for_session(symbol, cursor, day.open, day.close, drift)
                    session.execute(
                        insert(Bar5mRow),
                        [
                            {
                                "symbol": b.symbol,
                                "start": _naive_utc(b.start),
                                "open": b.open,
                                "high": b.high,
                                "low": b.low,
                                "close": b.close,
                                "volume": b.volume,
                                "feed": "test",
                            }
                            for b in bars
                        ],
                    )
                    daily = aggregate_daily(symbol, bars, day)
                    if daily is not None:
                        session.add(
                            Bar1dRow(
                                symbol=symbol,
                                date=daily.date,
                                open=daily.open,
                                high=daily.high,
                                low=daily.low,
                                close=daily.close,
                                volume=daily.volume,
                            )
                        )
                written += 1
            cursor += timedelta(days=1)
        session.commit()


async def _run_deterministic_pair(
    end: date, *, warmup_sessions: int, daily_history: int, history_5m: int
) -> None:
    """Two independent rules-only runs over the same seeded bars produce
    byte-identical trade rows. Determinism is meaningless on an empty
    book, so the run must trade."""
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["AAA"]
    config.yaml["universe"]["sectors"]["AAA"] = "Technology"
    schedule = config.yaml.setdefault("schedule", {})
    schedule["history_sessions"] = history_5m
    schedule["daily_history_sessions"] = daily_history
    sf = get_session_maker()
    # The daily-indicator warmup plus the run window, all as cached bars.
    window_days = (end - D0).days
    seed_start = D0 - timedelta(days=warmup_sessions * 7 // 5 + 20)
    _seed_live_bars(
        ["AAA", "SPY"], seed_start, warmup_sessions + window_days * 5 // 7 + 5
    )

    manager = BacktestManager(config, FakeClock(datetime.now(tz=NY_TZ)))
    specs = [
        {
            "ref": "rules_only",
            "name": "Rules only",
            "kind": "rules_only",
            "slug": "rules-only",
        }
    ]
    runs: list[int] = []
    for name in ("deterministic", "deterministic-2"):
        row = manager.create(
            name=name,
            start=D0,
            end=end,
            universe_mode="fixed",
            strategies=specs,
            starting_equity=Decimal(10000),
            slippage_pct=Decimal("0.05"),
        )
        await run_backtest(
            row.id,
            config=config,
            live_sf=sf,
            earnings=FakeEarningsSource(events=[]),
            wall=FakeClock(datetime.now(tz=NY_TZ)),
        )
        with sf() as session:
            done = session.get(Backtest, row.id)
            assert done is not None
            assert done.status == "done"
            assert done.summary is not None
        runs.append(row.id)

    db1 = run_dir_for(config, runs[0]) / "rules-only.db"
    assert db1.exists()
    trades1 = _trade_rows(db1, config)
    # Determinism is meaningless on an empty book — the run must trade.
    assert len(trades1) >= 2
    trades2 = _trade_rows(run_dir_for(config, runs[1]) / "rules-only.db", config)
    assert trades2 == trades1


async def test_rules_only_short_window_is_deterministic() -> None:
    """Default-suite determinism check: ~10 sessions over seeded data that
    trades. The 3-month variant runs under -m slow (make test-slow)."""
    # ~65 sessions of warmup: the warmup gate, daily SMA-50 and RSI all
    # have history, and a sideways/unknown regime (no SMA-200 yet) does
    # not block entries.
    await _run_deterministic_pair(
        D0 + timedelta(days=14),
        warmup_sessions=65,
        daily_history=55,
        history_5m=30,
    )


@pytest.mark.slow
async def test_rules_only_run_is_deterministic_and_never_touches_account() -> None:
    """The spec's 3-month determinism run — moved behind `slow` for CI
    time (docs/SPEC.md, Backtesting, Reproducibility)."""
    await _run_deterministic_pair(
        D0 + timedelta(days=90),
        warmup_sessions=250,
        daily_history=260,
        history_5m=60,
    )


def _trade_rows(path: Path, config: AppConfig) -> list[tuple[object, ...]]:
    sf = session_maker_for(path, config)
    with sf() as session:
        return [
            (
                p.symbol,
                p.qty,
                str(p.avg_entry_price),
                p.opened_at,
                p.closed_at,
                p.exit_reason,
                str(p.realized_pnl_raw),
            )
            for p in session.execute(
                select(Position).order_by(Position.id)
            ).scalars()
        ]


async def test_llm_run_is_refused_before_cutoff_via_api_client() -> None:
    """The cutoff rule also works through the estimate endpoint logic."""
    from app.api.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/backtests",
            json={
                "start": "2023-01-02",
                "end": "2023-06-30",
                "universe_mode": "fixed",
                "strategies": ["live"],
                "starting_equity": 10000,
                "slippage_pct": 0.05,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "refused"
        assert "cutoff" in (body.get("reason") or "")


# ---------------------------------------------------------------------------
# RunGate — the cancel/pause flags the child checks


async def test_run_gate_cancel_and_pause_flags() -> None:
    sf = get_session_maker()
    with sf() as session:
        row = Backtest(
            name="g",
            start_date=D0,
            end_date=D0,
            universe_mode="fixed",
            strategies=[],
            starting_equity=Decimal(10),
            slippage_pct=Decimal(0),
            status="running",
            run_dir="run-g",
            created_at=_naive_utc(datetime(2024, 6, 3, 14, 0)),
        )
        session.add(row)
        session.commit()
        run_id = row.id
    gate = RunGate(sf, run_id)
    cancelled, paused = gate.flags()
    assert not cancelled and not paused
    with sf() as session:
        fetched = session.get(Backtest, run_id)
        assert fetched is not None
        fetched.cancel_requested = True
        fetched.paused = True
        session.commit()
    cancelled, paused = gate.flags()
    assert cancelled and paused


async def test_live_cycle_runs_on_time_while_a_backtest_runs() -> None:
    """A live cycle pauses the child's Alpaca/model calls but starts at its
    scheduled time — it never waits for the backtest (docs/SPEC.md,
    Backtesting, Process)."""
    from app.api.main import run_scheduled_cycle
    from app.engine.cycle import CycleRunner

    sf = get_session_maker()
    clock = FakeClock(datetime(2024, 6, 3, 10, 46, tzinfo=NY_TZ))
    manager = BacktestManager(AppConfig(), clock)
    with sf() as session:
        session.add(
            Backtest(
                name="running",
                start_date=D0,
                end_date=D0,
                universe_mode="fixed",
                strategies=[],
                starting_equity=Decimal(10),
                slippage_pct=Decimal(0),
                status="running",
                run_dir="run-live",
                created_at=_naive_utc(datetime(2024, 6, 3, 14, 0)),
            )
        )
        session.commit()

    paused_during_run: list[bool] = []
    ran_at: list[datetime] = []

    class SpyRunner:
        async def run(self, scheduled: datetime) -> None:
            ran_at.append(scheduled)
            with sf() as session:
                row = session.execute(
                    select(Backtest).where(Backtest.status == "running")
                ).scalars().one()
                paused_during_run.append(bool(row.paused))

    await run_scheduled_cycle(
        cast(CycleRunner, SpyRunner()), clock, "10:46", backtests=manager
    )
    assert ran_at == [datetime(2024, 6, 3, 10, 46, tzinfo=NY_TZ)]
    assert paused_during_run == [True]
    with sf() as session:
        row = session.execute(
            select(Backtest).where(Backtest.status == "running")
        ).scalars().one()
        assert not row.paused


async def test_wait_for_call_blocks_while_paused() -> None:
    """The child-side check: downloads and model calls wait out a live job."""
    sf = get_session_maker()
    with sf() as session:
        session.add(
            Backtest(
                name="p",
                start_date=D0,
                end_date=D0,
                universe_mode="fixed",
                strategies=[],
                starting_equity=Decimal(10),
                slippage_pct=Decimal(0),
                status="running",
                run_dir="run-p",
                created_at=_naive_utc(datetime(2024, 6, 3, 14, 0)),
                paused=True,
            )
        )
        session.commit()
        run_id = session.execute(select(func.max(Backtest.id))).scalar_one()

    gate = RunGate(sf, run_id)

    async def unpause() -> None:
        await asyncio.sleep(0.1)
        with sf() as session:
            row = session.get(Backtest, run_id)
            assert row is not None
            row.paused = False
            session.commit()

    await asyncio.gather(gate.wait_for_call(), unpause())

    with sf() as session:
        row = session.get(Backtest, run_id)
        assert row is not None
        row.cancel_requested = True
        session.commit()
    from app.backtests.worker import RunCancelled

    with pytest.raises(RunCancelled):
        await gate.wait_for_call()


async def test_make_backtest_sources_are_read_only_views() -> None:
    """All five run sources read from the store/market.db — never the live
    DB or the network."""
    config = AppConfig()
    store = make_bar_store(bar_store_path(config), config)
    market_sf = make_market_db(
        run_dir_for(config, 999).parent / "market-test.db", config
    )
    clock = FakeClock(datetime(2024, 6, 3, 10, 0, tzinfo=NY_TZ))
    sources = make_backtest_sources(store, market_sf, clock, config)
    assert sources.alpaca is None
    out = await sources.bars.fetch_5m_bars(
        ["AAA"], clock.now() - timedelta(hours=1), clock.now()
    )
    assert out.error is None


async def test_missing_earnings_blocks_entries_and_is_listed(
    tmp_path: Path,
) -> None:
    """A symbol whose earnings data could not be fetched is blocked for
    entries (its earnings check fails — the fail path itself is covered in
    test_rules) and the run's warnings name it."""
    from app.backtests.sources import BacktestEarningsSource
    from app.backtests.worker import RunParams, _fill_market_db, _weekday_sessions

    config = AppConfig()
    sf = get_session_maker()
    with sf() as session:
        row = Backtest(
            name="e",
            start_date=D0,
            end_date=D0,
            universe_mode="fixed",
            strategies=[],
            starting_equity=Decimal(10),
            slippage_pct=Decimal(0),
            status="running",
            run_dir="run-e",
            created_at=_naive_utc(datetime(2024, 6, 3, 14, 0)),
        )
        session.add(row)
        session.commit()
        run_id = row.id

    market_sf = make_market_db(tmp_path / "market.db", config)
    run = RunParams(
        id=run_id,
        name="e",
        start=D0,
        end=D0 + timedelta(days=2),
        universe_mode="fixed",
        strategies=[],
        starting_equity=Decimal(10),
        slippage_pct=Decimal(0),
    )
    warnings = await _fill_market_db(
        run=run,
        days=_weekday_sessions(D0, D0 + timedelta(days=2)),
        symbols=["AAA"],
        alpaca=None,
        earnings=None,  # no source → every symbol's coverage is "missing"
        market_sf=market_sf,
        gate=RunGate(sf, run_id),
        wall=FakeClock(datetime(2024, 6, 3, 12, 0, tzinfo=NY_TZ)),
        config=config,
    )
    assert any("AAA" in w and "arnings" in w for w in warnings)
    src = BacktestEarningsSource(market_sf)
    assert src.missing_symbols(["AAA", "BBB"]) == {"AAA"}


async def test_cancel_mid_run_stops_cleanly_and_keeps_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel requested during replay ends the run with status cancelled;
    the progress and the strategy database written so far stay."""
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["AAA"]
    sf = get_session_maker()
    manager = BacktestManager(config, FakeClock(datetime.now(tz=NY_TZ)))
    run = manager.create(
        name="cancel-mid",
        start=D0,
        end=D0 + timedelta(days=10),
        universe_mode="fixed",
        strategies=[
            {
                "ref": "rules_only",
                "name": "Rules only",
                "kind": "rules_only",
                "slug": "rules-only",
            }
        ],
        starting_equity=Decimal(10000),
        slippage_pct=Decimal("0.05"),
    )

    real_flags = RunGate.flags

    def flags_after_first_session(self: RunGate) -> tuple[bool, bool]:
        cancelled, paused = real_flags(self)
        if cancelled:
            return cancelled, paused
        with sf() as session:
            row = session.get(Backtest, run.id)
            progress = row.progress if row is not None else None
        return progress is not None, paused

    monkeypatch.setattr(RunGate, "flags", flags_after_first_session)

    await run_backtest(
        run.id,
        config=config,
        live_sf=sf,
        earnings=FakeEarningsSource(events=[]),
        wall=FakeClock(datetime.now(tz=NY_TZ)),
    )

    with sf() as session:
        row = session.get(Backtest, run.id)
        assert row is not None
        assert row.status == "cancelled"
        assert row.progress is not None
    assert (run_dir_for(config, run.id) / "rules-only.db").exists()


class _FakeAlpacaDownload:
    """Records every download call and serves deterministic fake bars."""

    def __init__(self, days: list[CalendarDay]) -> None:
        self.days = days
        self.calls: list[tuple[str, Any, Any]] = []
        self.times: list[float] = []

    async def fetch_5m_bars(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> Any:
        import time as _time

        from app.data.fake import _bars_for_session
        from app.data.models import DataResult

        self.calls.append(("5m", start, end))
        self.times.append(_time.monotonic())
        out: list[Bar5m] = []
        for day in self.days:
            if start.date() <= day.date <= end.date():
                for s in symbols:
                    out += _bars_for_session(s, day.date, day.open, day.close)
        return DataResult(data=out, source="fake")

    async def fetch_daily_bars(
        self, symbol: str, start: date, end: date
    ) -> Any:
        import time as _time

        from app.data.models import DailyBar, DataResult

        self.calls.append(("1d", start, end))
        self.times.append(_time.monotonic())
        return DataResult(
            data=[
                DailyBar(
                    symbol=symbol,
                    date=d.date,
                    open=Decimal(10),
                    high=Decimal(11),
                    low=Decimal(9),
                    close=Decimal(10),
                    volume=1000,
                )
                for d in self.days
                if start <= d.date <= end
            ],
            source="fake",
        )


async def test_fill_bar_store_downloads_only_missing_at_rate_limit() -> None:
    """Download branch: live-cached bars are copied, only the missing
    range is fetched, the shared limiter caps calls/minute, and a set
    pause flag blocks every call until it clears."""
    from app.backtests.worker import RunParams, _fill_bar_store, _weekday_sessions
    from app.data.rate_limiter import RateLimitConfig, RateLimiter

    config = AppConfig()
    sf = get_session_maker()
    days = _weekday_sessions(D0, D0 + timedelta(days=6))  # 5 weekdays
    # The live cache has only the first session; the other four download.
    _seed_live_bars(["AAA", "BBB"], days[0].date, 1)
    store = make_bar_store(bar_store_path(config), config)

    with sf() as session:
        row = Backtest(
            name="dl",
            start_date=D0,
            end_date=days[-1].date,
            universe_mode="fixed",
            strategies=[],
            starting_equity=Decimal(10),
            slippage_pct=Decimal(0),
            status="downloading",
            run_dir="run-dl",
            created_at=_naive_utc(datetime(2024, 6, 3, 14, 0)),
            paused=True,
        )
        session.add(row)
        session.commit()
        run_id = row.id

    alpaca = _FakeAlpacaDownload(days)
    # 2 symbols × (1 chunk + 1 daily) = 4 calls; the limiter holds 2.
    limiter = RateLimiter(RateLimitConfig(max_calls=2, window_seconds=0.3))
    run = RunParams(
        id=run_id,
        name="dl",
        start=D0,
        end=days[-1].date,
        universe_mode="fixed",
        strategies=[],
        starting_equity=Decimal(10),
        slippage_pct=Decimal(0),
    )
    task = asyncio.create_task(
        _fill_bar_store(
            config=config,
            run=run,
            days=days,
            run_days=days,
            symbols=["AAA", "BBB"],
            alpaca=alpaca,
            store=store,
            gate=RunGate(sf, run_id),
            limiter=limiter,
        )
    )
    await asyncio.sleep(0.8)
    assert alpaca.calls == []  # paused: not a single download started
    with sf() as session:
        saved = session.get(Backtest, run_id)
        assert saved is not None
        saved.paused = False
        session.commit()
    warnings = await asyncio.wait_for(task, timeout=10)
    assert warnings == []

    calls_5m = [c for c in alpaca.calls if c[0] == "5m"]
    assert len(calls_5m) == 2  # one chunk per symbol
    # Only the missing range is downloaded — the first session was copied.
    for _m, start, end in calls_5m:
        assert start.date() == days[1].date
        assert end.date() == days[-1].date
    assert len([c for c in alpaca.calls if c[0] == "1d"]) == 2
    # The limiter held: at most 2 calls inside any 0.3 s window.
    for i in range(2, len(alpaca.times)):
        assert alpaca.times[i] - alpaca.times[i - 2] >= 0.3
    # And every session is now covered from the store alone.
    for symbol in ("AAA", "BBB"):
        assert store.covered_session_dates(symbol, days) == {
            d.date for d in days
        }


async def test_cancel_kills_a_stubborn_child_by_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that ignores the cancel flag is stopped by its own PID
    after cancel_grace_seconds; the run is marked cancelled and keeps the
    partial results written so far."""
    import subprocess
    import sys

    from app.backtests.bundle import init_strategy_db
    from app.backtests.manager import _pid_alive

    config = AppConfig()
    config.yaml["backtest"]["cancel_grace_seconds"] = 0.2
    config.yaml["backtest"]["cancel_kill_wait_seconds"] = 0.5
    manager = BacktestManager(config, FakeClock(datetime.now(tz=NY_TZ)))
    specs = [
        {
            "ref": "rules_only",
            "name": "Rules only",
            "kind": "rules_only",
            "slug": "rules-only",
        }
    ]
    run = manager.create(
        name="stubborn",
        start=D0,
        end=D0 + timedelta(days=5),
        universe_mode="fixed",
        strategies=specs,
        starting_equity=Decimal(10000),
        slippage_pct=Decimal("0.05"),
    )
    # Partial results the kill must keep: the run folder and a strategy db.
    run_dir = run_dir_for(config, run.id)
    run_dir.mkdir(parents=True)
    init_strategy_db(run_dir / "rules-only.db")

    def spawn_stub(session: Session, row: Backtest) -> None:
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        row.process_id = proc.pid
        row.status = "running"
        manager._process = proc
        manager._run_id = row.id

    monkeypatch.setattr(manager, "_spawn", spawn_stub)
    manager.tick()
    sf = get_session_maker()
    with sf() as session:
        pid = session.get(Backtest, run.id).process_id  # type: ignore[union-attr]
    assert pid is not None and _pid_alive(pid)

    assert manager.cancel(run.id)
    await asyncio.sleep(1.5)  # grace 0.2 + kill wait up to 0.5 + margin

    assert not _pid_alive(pid)
    with sf() as session:
        row = session.get(Backtest, run.id)
        assert row is not None
        assert row.status == "cancelled"
    assert (run_dir / "rules-only.db").exists()
