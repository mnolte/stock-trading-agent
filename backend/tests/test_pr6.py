"""PR 6 backend behaviour: supervised approvals, the paper-reset two-step,
versioned settings, warming-up symbols and the WebSocket update bus
(docs/SPEC.md, GUI)."""

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from app.api.websocket import UpdateBus
from app.broker.fake import FakeBroker, _PosRec
from app.broker.models import BrokerAccount
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
from app.data.models import Bar1h, CalendarDay, DailyBar, Quote
from app.data.service import MarketDataService
from app.db.base import Base
from app.db.models import Approval, Cycle, Decision, Event, Order, Position, Run
from app.engine.executor import Executor
from app.engine.state import ExecutionState
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from app.rules.prefilter import RulesResult
from app.settings_service import SettingsError, validate_settings
from app.signals.service import SignalService, SymbolEval
from app.signals.snapshot import IndicatorSnapshot
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

# Thursday 15 Oct 2026, 13:00 New York (17:00 UTC) — mid-session.
NOW = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)
YESTERDAY = datetime(2026, 10, 14, 15, 0)  # naive UTC, stored format


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _sources() -> DataSources:
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


def _executor(
    session_factory: sessionmaker[Session],
    broker: FakeBroker,
    clock: FakeClock | None = None,
    config: AppConfig | None = None,
) -> Executor:
    clock = clock or FakeClock(NOW)
    config = config or AppConfig()
    sources = _sources()
    calendar = cast(CalendarProvider, sources.calendar)
    market_data = MarketDataService(
        sources, calendar, config, session_factory=session_factory
    )
    signals = SignalService(market_data, calendar, config, clock, session_factory)
    return Executor(
        broker=broker,
        market_data=market_data,
        signals=signals,
        calendar=calendar,
        config=config,
        clock=clock,
        state=ExecutionState(clock=clock),
        session_factory=session_factory,
    )


def _account(
    equity: str = "10000", cash: str = "10000", last_equity: str = "10000"
) -> BrokerAccount:
    return BrokerAccount(
        account_number="PA-TEST",
        equity=Decimal(equity),
        cash=Decimal(cash),
        last_equity=Decimal(last_equity),
    )


def _decision(
    session: Session,
    symbol: str,
    final_action: str,
    model_output: dict[str, object] | None = None,
) -> Decision:
    cycle = Cycle(
        kind="cycle",
        scheduled_time=NOW.replace(tzinfo=None),
        started_at=NOW.replace(tzinfo=None),
        status="running",
    )
    session.add(cycle)
    session.flush()
    decision = Decision(
        cycle_id=cycle.id,
        symbol=symbol,
        context="candidate" if final_action == "enter" else "held",
        final_action=final_action,
        model_output=model_output,
        prompt_version="decision.v1",
        prompt_hash="0" * 64,
        model="fake",
    )
    session.add(decision)
    session.flush()
    return decision


def _snapshot(symbol: str) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        symbol=symbol,
        price=Decimal("420"),
        ema_fast=Decimal("416.9"),
        ema_slow=Decimal("414.3"),
        rsi=Decimal("57"),
        atr_1h=Decimal("3.10"),
        session_vwap=Decimal("417.4"),
        obv_trend="up",
        rs_vs_spy_pct=Decimal("0.9"),
        daily_close=Decimal("415.8"),
        daily_sma_fast=Decimal("402.1"),
        daily_sma_slow=Decimal("381.6"),
        daily_atr_percentile=Decimal("48"),
        opening_gap_pct=Decimal("0.3"),
        days_to_earnings=9,
        earnings_available=True,
    )


def _item(symbol: str) -> SymbolEval:
    return SymbolEval(
        symbol=symbol,
        snapshot=_snapshot(symbol),
        rules=RulesResult(symbol, "pass", None, []),
        bars_1h=[],
        price=Decimal("420"),
        day_change_pct=Decimal("0.5"),
    )


def _quote(symbol: str, ask: str = "420.10", bid: str = "420.00") -> Quote:
    return Quote(
        symbol=symbol,
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=Decimal(ask),
        timestamp=NOW,
        feed="iex",
    )


def _events(session: Session, event_type: str) -> list[Event]:
    return list(
        session.execute(
            select(Event).where(Event.event_type == event_type)
        ).scalars()
    )


async def _pending_approval_id(
    session_factory: sessionmaker[Session],
    executor: Executor,
    symbol: str = "MSFT",
    stop: str = "410",
    target: str = "435",
) -> int:
    """Run a supervised entry and return the stored approval's id."""
    with session_factory() as session:
        decision = _decision(
            session,
            symbol,
            "enter",
            {"stop_price": float(stop), "target_price": float(target)},
        )
        await executor.apply_decision(
            session,
            decision,
            _item(symbol),
            None,
            _quote(symbol),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        return session.execute(
            select(Approval.id).where(
                Approval.symbol == symbol, Approval.status == "pending"
            )
        ).scalar_one()


# ----------------------------------------------------------------------
# supervised mode — approval creation


async def test_supervised_entry_creates_pending_approval(
    session_factory: sessionmaker[Session],
) -> None:
    """A passing entry in supervised mode waits for approval, no order sent."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    config = AppConfig()
    config.yaml["execution"]["mode"] = "supervised"
    executor = _executor(session_factory, broker, config=config)
    await executor.reconcile()

    with session_factory() as session:
        decision = _decision(
            session, "MSFT", "enter", {"stop_price": 410.0, "target_price": 435.0}
        )
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            None,
            _quote("MSFT"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        assert decision.final_action == "enter"
        assert decision.gates_result is not None
        assert decision.gates_result["pending_approval"] is True
        approval = session.execute(select(Approval)).scalar_one()
        assert approval.status == "pending"
        assert approval.symbol == "MSFT"
        assert approval.qty > 0
        assert approval.stop_price == Decimal("410")
        assert approval.target_price == Decimal("435")
        assert decision.gates_result["approval_id"] == approval.id
    # Nothing reached the broker.
    assert broker.orders == {}
    assert broker.submit_calls == 0


async def test_automatic_entry_still_sends_order(
    session_factory: sessionmaker[Session],
) -> None:
    """Automatic mode is unchanged: the bracket goes straight to Alpaca."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    executor = _executor(session_factory, broker)
    await executor.reconcile()
    with session_factory() as session:
        decision = _decision(
            session, "MSFT", "enter", {"stop_price": 410.0, "target_price": 435.0}
        )
        await executor.apply_decision(
            session,
            decision,
            _item("MSFT"),
            None,
            _quote("MSFT"),
            NOW,
            account=_account(),
            earnings_known=True,
        )
        session.commit()
        assert session.execute(select(Approval)).scalars().all() == []
    assert broker.submit_calls == 1


async def test_new_approval_replaces_pending_for_same_symbol(
    session_factory: sessionmaker[Session],
) -> None:
    """At most one pending approval per symbol; the newer one wins."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    config = AppConfig()
    config.yaml["execution"]["mode"] = "supervised"
    executor = _executor(session_factory, broker, config=config)
    await executor.reconcile()

    first = await _pending_approval_id(session_factory, executor)
    second = await _pending_approval_id(session_factory, executor)

    with session_factory() as session:
        rows = {
            row.id: row for row in session.execute(select(Approval)).scalars()
        }
        assert rows[first].status == "expired"
        assert rows[first].reason == "replaced by a newer approval"
        assert rows[second].status == "pending"


# ----------------------------------------------------------------------
# approve / reject / expire


async def test_approve_passes_checks_and_submits(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """All re-checks pass → bracket at Alpaca, approval approved."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    config = AppConfig()
    config.yaml["execution"]["mode"] = "supervised"
    executor = _executor(session_factory, broker, config=config)
    await executor.reconcile()
    approval_id = await _pending_approval_id(
        session_factory, executor, stop="410", target="440"
    )
    monkeypatch.setattr(
        executor, "_atr_1h", lambda symbol: Decimal("3")
    )

    result = await executor.approve(approval_id)

    assert result["ok"] is True
    with session_factory() as session:
        stored = session.get(Approval, approval_id)
        assert stored is not None
        assert stored.status == "approved"
        assert stored.resolved_at is not None
        order = session.execute(
            select(Order).where(Order.leg == "entry")
        ).scalar_one()
        assert order.symbol == "MSFT"
        assert order.status == "new"
    # The bracket sits at the broker with both legs.
    parents = await broker.get_open_orders()
    assert len(parents.data) == 1
    assert len(parents.data[0].legs) == 2


async def test_approve_rejects_when_stop_too_close(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Code check at approve time: stop < 1.5 × ATR rejects the approval."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    config = AppConfig()
    config.yaml["execution"]["mode"] = "supervised"
    executor = _executor(session_factory, broker, config=config)
    await executor.reconcile()
    # Model stop $419 vs the ~$420 limit: ~0.3 × ATR(3) — below 1.5.
    approval_id = await _pending_approval_id(
        session_factory, executor, stop="419", target="440"
    )
    monkeypatch.setattr(
        executor, "_atr_1h", lambda symbol: Decimal("3")
    )

    result = await executor.approve(approval_id)

    assert result["ok"] is False
    assert "ATR" in str(result["error"])
    with session_factory() as session:
        stored = session.get(Approval, approval_id)
        assert stored is not None
        assert stored.status == "rejected"
        assert stored.reason and "ATR" in stored.reason
    assert broker.submit_calls == 0  # nothing ever reached the broker


async def test_approve_rejects_when_gate_now_blocks(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gate that passes at cycle time can fail at approve time."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    config = AppConfig()
    config.yaml["execution"]["mode"] = "supervised"
    executor = _executor(session_factory, broker, config=config)
    await executor.reconcile()
    approval_id = await _pending_approval_id(session_factory, executor)
    monkeypatch.setattr(
        executor, "_atr_1h", lambda symbol: Decimal("3")
    )
    # Fill all position slots: max_open_positions gate now blocks.
    with session_factory() as session:
        for index, symbol in enumerate(["AAPL", "NVDA", "JPM", "XOM"]):
            session.add(
                Position(
                    symbol=symbol,
                    qty=1,
                    avg_entry_price=Decimal("100"),
                    current_stop_price=Decimal("90"),
                    opened_at=YESTERDAY + timedelta(hours=index),
                )
            )
        session.commit()

    result = await executor.approve(approval_id)

    assert result["ok"] is False
    with session_factory() as session:
        stored = session.get(Approval, approval_id)
        assert stored is not None
        assert stored.status == "rejected"
        assert stored.reason
    assert len(broker.orders) == 0


async def test_approve_refused_while_kill_switch_on(
    session_factory: sessionmaker[Session],
) -> None:
    """Approve is unavailable while the kill switch is on; stays pending."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    config = AppConfig()
    config.yaml["execution"]["mode"] = "supervised"
    executor = _executor(session_factory, broker, config=config)
    await executor.reconcile()
    approval_id = await _pending_approval_id(session_factory, executor)
    await executor.activate_kill_switch(
        reason="testing", automatic=False
    )

    result = await executor.approve(approval_id)

    assert result["ok"] is False
    assert "kill switch" in str(result["error"])
    with session_factory() as session:
        stored = session.get(Approval, approval_id)
        assert stored is not None
        assert stored.status == "pending"


async def test_approve_refused_while_paused(
    session_factory: sessionmaker[Session],
) -> None:
    """Approve is unavailable while paused; the approval stays pending."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    config = AppConfig()
    config.yaml["execution"]["mode"] = "supervised"
    executor = _executor(session_factory, broker, config=config)
    await executor.reconcile()
    approval_id = await _pending_approval_id(session_factory, executor)
    await executor.pause()

    result = await executor.approve(approval_id)

    assert result["ok"] is False
    assert "paused" in str(result["error"])
    with session_factory() as session:
        stored = session.get(Approval, approval_id)
        assert stored is not None
        assert stored.status == "pending"


async def test_reject_stores_reason_and_is_final(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    config = AppConfig()
    config.yaml["execution"]["mode"] = "supervised"
    executor = _executor(session_factory, broker, config=config)
    await executor.reconcile()
    approval_id = await _pending_approval_id(session_factory, executor)

    assert executor.reject_approval(approval_id, "not convinced") is True
    # A second resolve is a no-op.
    assert executor.reject_approval(approval_id, None) is False

    with session_factory() as session:
        stored = session.get(Approval, approval_id)
        assert stored is not None
        assert stored.status == "rejected"
        assert stored.reason == "not convinced"
        assert stored.resolved_at is not None


async def test_pending_approvals_expire_at_next_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    config = AppConfig()
    config.yaml["execution"]["mode"] = "supervised"
    executor = _executor(session_factory, broker, config=config)
    await executor.reconcile()
    approval_id = await _pending_approval_id(session_factory, executor)

    assert executor.expire_pending_approvals() == 1

    with session_factory() as session:
        stored = session.get(Approval, approval_id)
        assert stored is not None
        assert stored.status == "expired"
        assert stored.reason == "expired at the next cycle"
    assert executor.expire_pending_approvals() == 0


# ----------------------------------------------------------------------
# paper reset — two steps


async def test_paper_reset_flattens_and_records(
    session_factory: sessionmaker[Session],
) -> None:
    """Step 1: kill switch on, positions closed, orders cancelled, event."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(
        qty=4, avg_entry_price=Decimal("415")
    )
    await broker.submit_bracket_entry(
        symbol="AAPL",
        qty=1,
        limit_price=Decimal("225"),
        stop_price=Decimal("215"),
        target_price=Decimal("240"),
        client_order_id="open-order",
    )
    executor = _executor(session_factory, broker)

    result = await executor.paper_reset()

    assert executor.state.kill_switch_on is True
    assert broker.positions == {}
    closed = result["closed"]
    assert isinstance(closed, list)
    assert "MSFT" in closed
    assert result["errors"] == []
    assert "$10,000" in str(result["message"])
    assert "Start new run" in str(result["message"])
    # Open orders were cancelled too.
    open_orders = await broker.get_open_orders()
    assert all(
        o.status in ("filled", "canceled") for o in broker.orders.values()
    ) and open_orders.data == []
    with session_factory() as session:
        assert _events(session, "paper_reset")
        assert _events(session, "kill_switch")


async def test_start_new_run_creates_run_and_resets(
    session_factory: sessionmaker[Session],
) -> None:
    """Step 2 passes: run row, new peak, streak and daily-loss baseline."""
    broker = FakeBroker(FakeClock(NOW))  # equity 10000 = starting equity
    executor = _executor(session_factory, broker)
    executor.state.peak_equity = Decimal("12000")
    executor.state.daily_loss_hit = True

    result = await executor.start_new_run()

    assert result["ok"] is True
    assert executor.state.peak_equity == Decimal("10000")
    assert executor.state.daily_loss_hit is False
    assert executor.state.run_started_at is not None
    with session_factory() as session:
        run = session.execute(select(Run)).scalar_one()
        assert run.starting_equity == Decimal("10000")
        assert _events(session, "run_started")


async def test_start_new_run_reports_open_positions(
    session_factory: sessionmaker[Session],
) -> None:
    """A check that fails says which check failed."""
    broker = FakeBroker(FakeClock(NOW), prices={"MSFT": Decimal("420")})
    broker.positions["MSFT"] = _PosRec(
        qty=4, avg_entry_price=Decimal("415")
    )
    executor = _executor(session_factory, broker)

    result = await executor.start_new_run()

    assert result["ok"] is False
    failed = cast("list[str]", result["failed"])
    assert any("open positions" in f and "MSFT" in f for f in failed)
    with session_factory() as session:
        assert session.execute(select(Run)).scalars().all() == []


async def test_start_new_run_reports_wrong_equity(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW), cash=Decimal("11000"))
    executor = _executor(session_factory, broker)

    result = await executor.start_new_run()

    assert result["ok"] is False
    failed = cast("list[str]", result["failed"])
    assert any("equity" in f and "10000" in f for f in failed)


async def test_start_new_run_reports_auth_failure(
    session_factory: sessionmaker[Session],
) -> None:
    """Auth errors after Alpaca key rotation are surfaced, not hidden."""
    broker = FakeBroker(FakeClock(NOW))
    broker.fail_reads = True
    executor = _executor(session_factory, broker)

    result = await executor.start_new_run()

    assert result["ok"] is False
    failed = cast("list[str]", result["failed"])
    assert any("could not read" in f for f in failed)


# ----------------------------------------------------------------------
# versioned settings


async def test_validate_settings_clean_change(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW))
    active = AppConfig().effective()

    cleaned, changes = await validate_settings(
        {"risk": {"risk_per_trade_pct": 0.4}},
        active,
        broker=broker,
        held_symbols=set(),
    )

    assert cleaned["risk"]["risk_per_trade_pct"] == 0.4
    assert changes == ["Risk per trade: 0.5 → 0.4"]


async def test_validate_settings_rejects_bad_values(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW))
    active = AppConfig().effective()

    with pytest.raises(SettingsError, match="automatic or supervised"):
        await validate_settings(
            {"execution": {"mode": "copilot"}},
            active,
            broker=broker,
            held_symbols=set(),
        )
    with pytest.raises(SettingsError, match="RSI"):
        await validate_settings(
            {"rules": {"rsi_min": 80, "rsi_max": 70}},
            active,
            broker=broker,
            held_symbols=set(),
        )
    with pytest.raises(SettingsError, match="not editable"):
        await validate_settings(
            {"risk": {"pdt_max_day_trades": 2}},
            active,
            broker=broker,
            held_symbols=set(),
        )


async def test_validate_universe_add_symbol(
    session_factory: sessionmaker[Session],
) -> None:
    """New symbol needs a sector from allowed_sectors and a tradable asset."""
    broker = FakeBroker(FakeClock(NOW))
    config = AppConfig()
    active = config.effective()
    symbols = list(active["universe"]["symbols"])
    sectors = dict(active["universe"]["sectors"])

    with pytest.raises(SettingsError, match="sector is required"):
        await validate_settings(
            {"universe": {"symbols": symbols + ["NFLX"], "sectors": sectors}},
            active,
            broker=broker,
            held_symbols=set(),
        )
    with pytest.raises(SettingsError, match="sector must be one of"):
        await validate_settings(
            {
                "universe": {
                    "symbols": symbols + ["NFLX"],
                    "sectors": {**sectors, "NFLX": "Streaming"},
                }
            },
            active,
            broker=broker,
            held_symbols=set(),
        )
    broker.untradable.add("NFLX")
    with pytest.raises(SettingsError, match="not an active, tradable"):
        await validate_settings(
            {
                "universe": {
                    "symbols": symbols + ["NFLX"],
                    "sectors": {**sectors, "NFLX": "Communication Services"},
                }
            },
            active,
            broker=broker,
            held_symbols=set(),
        )
    broker.untradable.clear()
    broker.non_tradable.add("NFLX")
    with pytest.raises(SettingsError, match="not an active, tradable"):
        await validate_settings(
            {
                "universe": {
                    "symbols": symbols + ["NFLX"],
                    "sectors": {**sectors, "NFLX": "Communication Services"},
                }
            },
            active,
            broker=broker,
            held_symbols=set(),
        )
    broker.non_tradable.clear()

    cleaned, changes = await validate_settings(
        {
            "universe": {
                "symbols": symbols + ["NFLX"],
                "sectors": {**sectors, "NFLX": "Communication Services"},
            }
        },
        active,
        broker=broker,
        held_symbols=set(),
    )
    assert cleaned["universe"]["symbols"][-1] == "NFLX"
    assert cleaned["universe"]["sectors"]["NFLX"] == "Communication Services"
    assert any("added NFLX" in c for c in changes)


async def test_validate_universe_cannot_remove_held(
    session_factory: sessionmaker[Session],
) -> None:
    broker = FakeBroker(FakeClock(NOW))
    active = AppConfig().effective()
    symbols = list(active["universe"]["symbols"])
    sectors = dict(active["universe"]["sectors"])

    with pytest.raises(SettingsError, match="can't be removed"):
        await validate_settings(
            {
                "universe": {
                    "symbols": [s for s in symbols if s != "MSFT"],
                    "sectors": sectors,
                }
            },
            active,
            broker=broker,
            held_symbols={"MSFT"},
        )


def test_pending_settings_promote_at_cycle_start() -> None:
    """A save is pending until the next cycle promotes it (spec: Settings)."""
    config = AppConfig()
    new_tree = config.effective()
    new_tree["risk"]["risk_per_trade_pct"] = 0.4
    config.set_pending({"risk": {"risk_per_trade_pct": 0.4}}, 7)

    # Active stays untouched until promotion.
    assert config.get("risk", "risk_per_trade_pct") == 0.5
    assert config.pending_version == 7

    promoted = config.promote_pending()

    assert promoted == 7
    assert config.active_version == 7
    assert config.get("risk", "risk_per_trade_pct") == 0.4


# ----------------------------------------------------------------------
# warming-up symbols


def _session_days(count: int, end: datetime) -> list[CalendarDay]:
    days: list[CalendarDay] = []
    day = end.astimezone(NY_TZ).date()
    while len(days) < count:
        if day.weekday() < 5:
            days.append(
                CalendarDay(
                    date=day,
                    open=datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY_TZ),
                    close=datetime(
                        day.year, day.month, day.day, 16, 0, tzinfo=NY_TZ
                    ),
                )
            )
        day = day - timedelta(days=1)
    return list(reversed(days))


def _daily_bars(days: list[CalendarDay]) -> list[DailyBar]:
    return [
        DailyBar(
            symbol="NFLX",
            date=d.date,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=1000,
        )
        for d in days
    ]


def _hour_bars(days: list[CalendarDay]) -> list[Bar1h]:
    return [
        Bar1h(
            symbol="NFLX",
            start=d.open,
            end=d.open + timedelta(hours=1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=500,
            feed="fake",
            complete=True,
        )
        for d in days
    ]


def test_warming_up_until_history_loaded(
    session_factory: sessionmaker[Session],
) -> None:
    """A new symbol is 'Warming up' until 260 daily + 60 5m sessions load."""
    config = AppConfig()
    config.yaml["schedule"]["daily_history_sessions"] = 5
    config.yaml["schedule"]["history_sessions"] = 2
    executor = _executor(session_factory, FakeBroker(FakeClock(NOW)), config=config)
    signals = executor.signals
    now = NOW
    five_days = _session_days(5, now)

    # No bars at all → warming.
    detail = signals._warming_detail("NFLX", [], [], five_days, five_days, now)
    assert detail is not None and "Warming up" in detail

    # Enough daily bars but only one 5m session → still warming.
    detail = signals._warming_detail(
        "NFLX",
        _hour_bars(five_days[:1]),
        _daily_bars(five_days),
        five_days,
        five_days,
        now,
    )
    assert detail is not None and "Warming up" in detail

    # Full history → no longer warming.
    detail = signals._warming_detail(
        "NFLX",
        _hour_bars(five_days),
        _daily_bars(five_days),
        five_days,
        five_days,
        now,
    )
    assert detail is None


# ----------------------------------------------------------------------
# WebSocket update bus


def test_update_bus_fans_out_topics() -> None:
    bus = UpdateBus()
    first = bus.subscribe()
    second = bus.subscribe()

    bus.publish("positions", "orders")

    assert first.get_nowait() == {"positions", "orders"}
    assert second.get_nowait() == {"positions", "orders"}
    bus.unsubscribe(first)
    bus.publish("header")
    assert second.get_nowait() == {"header"}


# ----------------------------------------------------------------------
# API surface (simulated app through the TestClient)


def test_approvals_endpoint_shape(client: TestClient) -> None:
    body = client.get("/api/approvals").json()
    assert {"items", "recent", "mode", "kill_switch_on"} <= set(body)
    assert body["mode"] == "automatic"


def test_dashboard_endpoint_shape(client: TestClient) -> None:
    body = client.get("/api/dashboard").json()
    assert {"session", "stats", "feed", "approvals_pending"} <= set(body)
    stats = body["stats"]
    assert {
        "open_risk_pct",
        "positions",
        "day_trades",
        "day_trades_max",
        "since_start_pct",
        "execution_mode",
    } <= set(stats)


def test_runs_and_chart_endpoints(client: TestClient) -> None:
    runs = client.get("/api/runs").json()
    assert "runs" in runs and len(runs["runs"]) >= 1
    chart = client.get("/api/chart/SPY").json()
    assert {"bars", "sessions", "levels", "warming_up"} <= set(chart)


def test_settings_get_and_save_flow(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert {
        "settings",
        "active_version",
        "pending_version",
        "schedule",
        "connections",
        "history",
        "held_symbols",
    } <= set(body)
    assert "SPY" in body["settings"]["universe"]["symbols"]
    assert body["settings"]["risk"]["risk_per_trade_pct"] == 0.5

    # A bad save is refused with the reason.
    bad = client.post(
        "/api/settings",
        json={"settings": {"execution": {"mode": "copilot"}}},
    )
    assert bad.status_code == 422
    assert "automatic or supervised" in str(bad.json()["detail"])

    # A good save creates a pending version; active is untouched.
    saved = client.post(
        "/api/settings",
        json={
            "settings": {"risk": {"risk_per_trade_pct": 0.4}},
            "reason": "tighter risk",
        },
    )
    assert saved.status_code == 200
    payload = saved.json()
    assert payload["effective_from"] == "next cycle"
    assert "Risk per trade: 0.5 → 0.4" in payload["changes"]
    after = client.get("/api/settings").json()
    assert after["pending_version"] == payload["version"]
    assert after["settings"]["risk"]["risk_per_trade_pct"] == 0.5

    # The saved version must not leak into the next app's startup or the
    # shared test database.
    from app.config import get_config
    from app.db.base import get_session_maker
    from app.db.models import SettingsVersion

    config = get_config()
    config._pending = None
    config.pending_version = None
    config.reschedule_cycles = False
    with get_session_maker()() as session:
        session.query(SettingsVersion).delete()
        session.commit()


def test_reject_approval_api(client: TestClient) -> None:
    """Reject resolves a pending approval; outcome shows on the decision."""
    from app.db.base import get_session_maker

    now_naive = NOW.replace(tzinfo=None)
    with get_session_maker()() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=now_naive,
            started_at=now_naive,
            status="done",
        )
        session.add(cycle)
        session.flush()
        decision = Decision(
            cycle_id=cycle.id,
            symbol="MSFT",
            context="candidate",
            final_action="enter",
            model_output={"confidence": 0.8},
            prompt_version="decision.v1",
            prompt_hash="0" * 64,
            model="fake",
        )
        session.add(decision)
        session.flush()
        approval = Approval(
            decision_id=decision.id,
            symbol="MSFT",
            qty=3,
            limit_price=Decimal("420.10"),
            stop_price=Decimal("410"),
            target_price=Decimal("435"),
            status="pending",
            created_at=now_naive,
        )
        session.add(approval)
        session.commit()
        approval_id = approval.id
        decision_id = decision.id

    listed = client.get("/api/approvals").json()
    assert any(i["id"] == approval_id for i in listed["items"])

    response = client.post(
        f"/api/approvals/{approval_id}/reject",
        json={"reason": "not today"},
    )
    assert response.status_code == 200
    assert response.json()["rejected"] is True

    detail = client.get(f"/api/decisions/{decision_id}").json()
    assert detail["approval"]["status"] == "rejected"
    assert detail["approval"]["reason"] == "not today"

    # Resolving again 404s.
    assert (
        client.post(f"/api/approvals/{approval_id}/reject").status_code == 404
    )
