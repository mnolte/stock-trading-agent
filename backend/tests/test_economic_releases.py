"""Economic releases (item 067): the FOMC window rule and order gate,
next_release selection, FRED failure tolerance, the release_next_morning
tags and the Needs-you warning."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, cast

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
    FakeReleaseSource,
)
from app.data.releases import ReleaseCalendarService
from app.data.service import MarketDataService
from app.db.models import Cycle, Decision, EconomicEvent, Position
from app.decide.client import FakeDecisionClient
from app.decide.service import DecisionService
from app.engine.cycle import CycleRunner
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from app.risk.fomc import fomc_dates, in_fomc_window
from app.risk.gates import GateContext, evaluate_entry_gates
from app.rules.prefilter import RulesResult, evaluate_entry_rules
from app.signals.regime import MarketRegime
from app.signals.service import SignalService
from app.signals.snapshot import IndicatorSnapshot
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

FOMC_DAY = date(2026, 12, 9)  # a Wednesday


def _config(**events: Any) -> AppConfig:
    """AppConfig with a small, deterministic events section."""
    base = AppConfig()
    merged = {
        "fomc": {
            "enabled": True,
            "time": "14:00",
            "block_minutes_before": 30,
            "block_minutes_after": 45,
            "dates": [FOMC_DAY.isoformat()],
        },
        "fred_releases": {"cpi": 10},
        "release_time": "08:30",
        "lookahead_days": 60,
        "next_release_sessions": 2,
    }
    for key, value in events.items():
        merged[key] = value
    return base.overlay({"events": merged})


def _ny(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=NY_TZ)


# ---------------------------------------------------------------------------
# in_fomc_window — the shared helper
# ---------------------------------------------------------------------------


def test_fomc_window_inside_and_outside() -> None:
    config = _config()
    # 13:46 cycle start: inside 13:30–14:45.
    assert in_fomc_window(_ny(FOMC_DAY, 13, 46), config) is not None
    # 14:46 cycle start: past the window.
    assert in_fomc_window(_ny(FOMC_DAY, 14, 46), config) is None
    # Before the window opens.
    assert in_fomc_window(_ny(FOMC_DAY, 13, 29), config) is None
    # Another day entirely.
    assert in_fomc_window(_ny(date(2026, 12, 10), 13, 46), config) is None


def test_fomc_window_disabled_and_custom_minutes() -> None:
    off = _config(fomc={
        "enabled": False,
        "time": "14:00",
        "block_minutes_before": 30,
        "block_minutes_after": 45,
        "dates": [FOMC_DAY.isoformat()],
    })
    assert in_fomc_window(_ny(FOMC_DAY, 13, 46), off) is None
    wide = _config(fomc={
        "enabled": True,
        "time": "14:00",
        "block_minutes_before": 60,
        "block_minutes_after": 60,
        "dates": [FOMC_DAY.isoformat()],
    })
    assert in_fomc_window(_ny(FOMC_DAY, 13, 0), wide) is not None
    assert in_fomc_window(_ny(FOMC_DAY, 15, 0), wide) is not None
    assert in_fomc_window(_ny(FOMC_DAY, 15, 1), wide) is None


def test_fomc_dates_parses_and_sorts() -> None:
    config = _config(fomc={
        "enabled": True,
        "time": "14:00",
        "block_minutes_before": 30,
        "block_minutes_after": 45,
        "dates": ["2027-01-27", "bad", "2026-12-09"],
    })
    assert fomc_dates(config) == [date(2026, 12, 9), date(2027, 1, 27)]


# ---------------------------------------------------------------------------
# pre-filter check + order-time gate
# ---------------------------------------------------------------------------


def _regime() -> MarketRegime:
    return MarketRegime(
        trend="up",
        volatility="normal",
        key="up_normal",
        label="Up · normal vol",
        blocks_entries=False,
        atr_percentile=Decimal("48"),
    )


def _snapshot() -> IndicatorSnapshot:
    return IndicatorSnapshot(
        symbol="AAPL",
        price=Decimal("225"),
        ema_fast=Decimal("224"),
        ema_slow=Decimal("220"),
        rsi=Decimal("55"),
        atr_1h=Decimal("1.2"),
        session_vwap=Decimal("224.5"),
        obv_trend="up",
        rs_vs_spy_pct=Decimal("0.4"),
        daily_close=Decimal("224"),
        daily_sma_fast=Decimal("215"),
        daily_sma_slow=Decimal("200"),
        daily_atr_percentile=Decimal("45"),
        opening_gap_pct=Decimal("0.3"),
        days_to_earnings=20,
        earnings_available=True,
    )


def _rules(
    cycle_at: datetime | None, config: AppConfig | None = None
) -> RulesResult:
    return evaluate_entry_rules(
        "AAPL",
        snapshot=_snapshot(),
        regime=_regime(),
        held=False,
        open_entry_order=False,
        is_last_cycle=False,
        config=config or AppConfig(),
        cycle_at=cycle_at,
    )


def _fomc_check(result: RulesResult) -> Any:
    return next(c for c in result.checks if c.name == "fomc_window")


def test_prefilter_fomc_window_pass_and_fail() -> None:
    config = _config()
    inside = _rules(_ny(FOMC_DAY, 13, 46), config)
    assert inside.status == "fail"
    check = _fomc_check(inside)
    assert not check.passed
    assert "FOMC" in (check.detail or "")
    outside = _rules(_ny(FOMC_DAY, 14, 46), config)
    assert outside.status == "pass"
    assert _fomc_check(outside).passed
    # No cycle time (backtests that never set it): the check passes.
    assert _fomc_check(_rules(None, config)).passed


def _gate_ctx(**overrides: Any) -> GateContext:
    base: dict[str, Any] = {
        "symbol": "MSFT",
        "equity": Decimal("10000"),
        "cash": Decimal("10000"),
        "last_equity": Decimal("10000"),
        "peak_equity": Decimal("10000"),
        "open_positions": [],
        "working_entries": (),
        "closed_today": False,
        "losing_streak": 0,
        "shock_brake_1h": False,
        "shock_brake_session": False,
        "days_to_earnings": 10,
        "earnings_known": True,
        "kill_switch_on": False,
        "paused": False,
        "pending_ack": False,
        "orders_this_cycle": 0,
    }
    base.update(overrides)
    return GateContext(**base)


def test_order_gate_fomc_window_pass_and_fail() -> None:
    blocked = evaluate_entry_gates(
        _gate_ctx(in_fomc_window=True),
        entry=Decimal("100"),
        stop=Decimal("98"),
        config=AppConfig(),
    )
    assert not blocked.allowed
    assert blocked.blocked_by == "fomc_window"
    allowed = evaluate_entry_gates(
        _gate_ctx(in_fomc_window=False),
        entry=Decimal("100"),
        stop=Decimal("98"),
        config=AppConfig(),
    )
    assert allowed.allowed
    names = [c.name for c in allowed.checks]
    assert "fomc_window" in names


# ---------------------------------------------------------------------------
# ReleaseCalendarService — next_release, FRED failure, tags
# ---------------------------------------------------------------------------


def _sources(calendar: FakeCalendarSource) -> DataSources:
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


def _releases(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    config: AppConfig,
    source: Any = None,
) -> tuple[ReleaseCalendarService, MarketDataService]:
    calendar = FakeCalendarSource()
    sources = _sources(calendar)
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    svc = ReleaseCalendarService(
        source,
        market_data,
        config,
        clock,
        session_factory=session_factory,
    )
    return svc, market_data


def _seed_event(
    session_factory: sessionmaker[Session],
    kind: str,
    day: date,
    hhmm: str = "08:30",
    source: str = "fred",
) -> None:
    with session_factory() as session:
        session.add(
            EconomicEvent(
                release_type=kind,
                date=day,
                time=hhmm,
                source=source,
                fetched_at=datetime(2026, 10, 15, 12, 0),
            )
        )
        session.commit()


def test_next_release_picks_first_within_sessions(
    session_factory: sessionmaker[Session],
) -> None:
    # Thursday 2026-10-15 09:30 NY; CPI Friday 08:30 (1 session), FOMC
    # Wednesday 10-21 (4 sessions — outside the 2-session horizon).
    clock = FakeClock(datetime(2026, 10, 15, 13, 30, tzinfo=UTC))
    config = _config()
    svc, _ = _releases(session_factory, clock, config)
    _seed_event(session_factory, "cpi", date(2026, 10, 16))
    _seed_event(session_factory, "fomc", date(2026, 10, 21), "14:00", "config")
    nxt = svc.next_release(clock.now())
    assert nxt is not None
    assert nxt["type"] == "cpi"
    assert nxt["date"] == "2026-10-16"
    assert nxt["sessions_away"] == 1
    assert nxt["at"].endswith("+00:00")


def test_next_release_none_when_nothing_coming(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(datetime(2026, 10, 15, 13, 30, tzinfo=UTC))
    svc, _ = _releases(session_factory, clock, _config())
    assert svc.next_release(clock.now()) is None


async def test_refresh_stores_fred_dates_and_survives_errors(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(datetime(2026, 10, 15, 13, 30, tzinfo=UTC))
    config = _config()
    source = FakeReleaseSource(dates={10: [date(2026, 10, 16), date(2026, 11, 10)]})
    svc, _ = _releases(session_factory, clock, config, source)
    result = await svc.refresh()
    assert result.error is None
    assert result.data == 2
    with session_factory() as session:
        rows = list(
            session.execute(
                select(EconomicEvent).where(EconomicEvent.source == "fred")
            ).scalars()
        )
    assert {r.date for r in rows} == {date(2026, 10, 16), date(2026, 11, 10)}
    assert all(r.time == "08:30" for r in rows)
    # FOMC rows were synced from config at the same time.
    with session_factory() as session:
        fomc_rows = list(
            session.execute(
                select(EconomicEvent).where(EconomicEvent.source == "config")
            ).scalars()
        )
    assert [r.date for r in fomc_rows] == [FOMC_DAY]

    # A failing FRED leaves the FOMC rows and reports health — never raises.
    bad = FakeReleaseSource(error="fred down")
    svc2, _ = _releases(session_factory, clock, config, bad)
    result2 = await svc2.refresh()
    assert result2.error is not None
    health = svc2.health()
    assert health is not None and "failed" in health
    with session_factory() as session:
        fomc_rows = list(
            session.execute(
                select(EconomicEvent).where(EconomicEvent.source == "config")
            ).scalars()
        )
    assert fomc_rows  # config rows unaffected

    # No source at all: same tolerance.
    svc3, _ = _releases(session_factory, clock, config, None)
    result3 = await svc3.refresh()
    assert result3.error == "no release source"


def test_morning_releases_and_next_session(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(datetime(2026, 10, 15, 13, 30, tzinfo=UTC))
    svc, _ = _releases(session_factory, clock, _config())
    _seed_event(session_factory, "cpi", date(2026, 10, 16))
    _seed_event(session_factory, "pce", date(2026, 10, 16), "08:30")
    _seed_event(session_factory, "fomc", date(2026, 10, 16), "14:00", "config")
    tags = svc.next_session_morning_releases(clock.now())
    # Friday 10-16 is the next session after Thursday 10-15; the 08:30
    # releases land, the 14:00 FOMC does not.
    assert {t["type"] for t in tags} == {"cpi", "pce"}
    assert all(t["date"] == "2026-10-16" for t in tags)
    # Friday's next session is Monday 10-19: nothing seeded there.
    monday = FakeClock(_ny(date(2026, 10, 16), 16, 0).astimezone(UTC))
    svc2, _ = _releases(session_factory, monday, _config())
    assert svc2.next_session_morning_releases(monday.now()) == []


def test_summary_shape(session_factory: sessionmaker[Session]) -> None:
    clock = FakeClock(datetime(2026, 10, 15, 13, 30, tzinfo=UTC))
    config = _config()
    svc, _ = _releases(session_factory, clock, config)
    svc.sync_fomc()
    _seed_event(session_factory, "cpi", date(2026, 10, 16))
    summary = svc.summary(clock.now())
    assert summary["fomc"]["count"] == 1
    assert summary["fomc"]["next"] == FOMC_DAY.isoformat()
    assert summary["fomc"]["last"] == FOMC_DAY.isoformat()
    assert summary["releases"] == [
        {"type": "cpi", "label": "CPI", "next_date": "2026-10-16"}
    ]
    assert summary["next_release"]["type"] == "cpi"
    assert summary["fred"] == "not fetched yet"


# ---------------------------------------------------------------------------
# release_next_morning tags on decisions and positions
# ---------------------------------------------------------------------------


def _runner(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    config: AppConfig,
    releases: ReleaseCalendarService,
) -> CycleRunner:
    calendar = FakeCalendarSource()
    sources = _sources(calendar)
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    signals = SignalService(market_data, calendar, config, clock, session_factory)
    decide = DecisionService(FakeDecisionClient(), config, session_factory)
    return CycleRunner(
        market_data=market_data,
        signals=signals,
        decide=decide,
        sources=sources,
        calendar=cast(CalendarProvider, calendar),
        config=config,
        clock=clock,
        session_factory=session_factory,
        releases=releases,
    )


def _cycle_and_rows(
    session_factory: sessionmaker[Session],
) -> tuple[int, int]:
    """A cycle with one enter decision and one open + one closed position."""
    with session_factory() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=datetime(2026, 10, 15, 18, 46),
            started_at=datetime(2026, 10, 15, 18, 46),
            status="done",
        )
        session.add(cycle)
        session.flush()
        decision = Decision(
            cycle_id=cycle.id,
            symbol="AAPL",
            context="candidate",
            final_action="enter",
            prompt_version="decision.v1",
            prompt_hash="x" * 64,
            model="gpt-4o-mini",
        )
        session.add(decision)
        session.add(
            Position(
                symbol="MSFT",
                qty=4,
                avg_entry_price=Decimal("100"),
                current_stop_price=Decimal("98"),
                opened_at=datetime(2026, 10, 14, 15, 0),
            )
        )
        session.add(
            Position(
                symbol="JPM",
                qty=2,
                avg_entry_price=Decimal("200"),
                current_stop_price=Decimal("196"),
                opened_at=datetime(2026, 10, 10, 15, 0),
                closed_at=datetime(2026, 10, 14, 20, 0),
            )
        )
        session.commit()
        return cycle.id, decision.id


async def test_release_tags_decision_and_last_cycle_positions(
    session_factory: sessionmaker[Session],
) -> None:
    # 16:00 NY Thursday — past the 15:46 last cycle.
    clock = FakeClock(datetime(2026, 10, 15, 20, 0, tzinfo=UTC))
    config = _config()
    svc, _ = _releases(session_factory, clock, config)
    _seed_event(session_factory, "cpi", date(2026, 10, 16))
    runner = _runner(session_factory, clock, config, svc)
    cycle_id, decision_id = _cycle_and_rows(session_factory)

    runner._tag_release_morning(cycle_id)

    with session_factory() as session:
        decision = session.get(Decision, decision_id)
        assert decision is not None
        assert decision.release_next_morning is not None
        assert {t["type"] for t in decision.release_next_morning} == {"cpi"}
        open_pos = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert open_pos.release_dates is not None
        assert {t["type"] for t in open_pos.release_dates} == {"cpi"}
        closed = session.execute(
            select(Position).where(Position.symbol == "JPM")
        ).scalar_one()
        assert closed.release_dates is None

    # A second call does not duplicate the position tags.
    runner._tag_release_morning(cycle_id)
    with session_factory() as session:
        open_pos = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert len(open_pos.release_dates or []) == 1


async def test_release_tags_skip_positions_before_last_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    # 13:00 NY Thursday — mid-session, not the last cycle: the entry
    # decision is tagged, open positions are not.
    clock = FakeClock(datetime(2026, 10, 15, 17, 0, tzinfo=UTC))
    config = _config()
    svc, _ = _releases(session_factory, clock, config)
    _seed_event(session_factory, "cpi", date(2026, 10, 16))
    runner = _runner(session_factory, clock, config, svc)
    cycle_id, decision_id = _cycle_and_rows(session_factory)

    runner._tag_release_morning(cycle_id)

    with session_factory() as session:
        decision = session.get(Decision, decision_id)
        assert decision is not None
        assert decision.release_next_morning is not None
        open_pos = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert open_pos.release_dates is None


async def test_release_tags_noop_without_releases(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(datetime(2026, 10, 15, 20, 0, tzinfo=UTC))
    config = _config()
    svc, _ = _releases(session_factory, clock, config)
    # No economic_events rows at all.
    runner = _runner(session_factory, clock, config, svc)
    cycle_id, decision_id = _cycle_and_rows(session_factory)
    runner._tag_release_morning(cycle_id)
    with session_factory() as session:
        decision = session.get(Decision, decision_id)
        assert decision is not None
        assert decision.release_next_morning is None
        open_pos = session.execute(
            select(Position).where(Position.symbol == "MSFT")
        ).scalar_one()
        assert open_pos.release_dates is None


# ---------------------------------------------------------------------------
# Needs you: FOMC dates running out
# ---------------------------------------------------------------------------


def test_attention_fomc_dates_warning(client: Any) -> None:
    from app.config import get_config

    config = get_config()
    saved_active = config._active
    try:
        # Last configured FOMC 55 days after the frozen test clock
        # (2026-10-15) — inside the 60-day window.
        config.apply_saved(
            {"events": {"fomc": {"dates": ["2026-12-09"]}}}, 98
        )
        payload = client.get("/api/attention").json()
        fomc_items = [
            i for i in payload["items"]
            if "FOMC dates in config end on" in i["text"]
        ]
        assert len(fomc_items) == 1
        assert "09 Dec 2026" in fomc_items[0]["text"]
        assert "Fed's calendar" in fomc_items[0]["text"]

        # Extending the list past 60 days removes the item.
        config.apply_saved(
            {"events": {"fomc": {"dates": ["2027-06-09"]}}}, 99
        )
        payload = client.get("/api/attention").json()
        assert not any(
            "FOMC dates in config end on" in i["text"]
            for i in payload["items"]
        )
    finally:
        config._active = saved_active
        config.active_version = None
