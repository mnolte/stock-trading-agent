"""Shadow strategies on fakes with an injected clock (docs/SPEC.md,
Strategies and shadows). No network: the runner reads stored snapshots and
an in-memory bar reader; model clients are the fake."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.config import AppConfig
from app.data.models import Bar5m
from app.db.models import (
    Cycle,
    Decision,
    Run,
    ShadowDecision,
    ShadowEquity,
    ShadowPosition,
    Strategy,
)
from app.decide.client import FakeDecisionClient, ModelResponse
from app.market.calendar import WeekdayCalendarProvider
from app.market.clock import NY_TZ, UTC, FakeClock
from app.shadows.compare import same_as_live
from app.shadows.portfolio import LiveGateState, replay_bars
from app.shadows.runner import ShadowRunner
from app.shadows.strategy import StrategyError, validate_new_strategy
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

# Thursday 15 Oct 2026; the 10:46 cycle, next live event the 11:01 check.
CYCLE = datetime(2026, 10, 15, 10, 46, tzinfo=NY_TZ)
RUN_START = datetime(2026, 10, 1, 13, 0)  # naive UTC


@pytest.fixture
def sf(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    return session_factory


class Notes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def notify(self, event: str, line: str, **_kw: object) -> None:
        self.sent.append((event, line))


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def _snap(
    symbol: str,
    *,
    price: float | str = 100.0,
    rsi: float = 55.0,
    atr: float = 1.0,
    context: str = "candidate",
    rank: int | str = "unavailable",
    dte: int | str | None = 20,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "context": context,
        "time_ny": CYCLE.isoformat(),
        "rank": rank,
        "price": price,
        "last_bar_close": price,
        "last_bar_time": (CYCLE - timedelta(minutes=20)).isoformat(),
        "regime": {"trend": "up", "volatility": "normal"},
        "indicators_1h": {
            "ema20": 101.0,
            "ema50": 100.0,
            "rsi14": rsi,
            "atr14": atr,
            "vwap": 100.0,
            "obv_trend": "up",
            "rs_vs_spy_pct": 0.5,
        },
        "indicators_daily": {
            "sma50": 95.0,
            "sma200": 90.0,
            "atr_percentile": 50.0,
            "gap_pct": 0.1,
            "close": 101.0,
        },
        "levels": "unavailable",
        "position": None,
        "days_to_earnings": dte,
        "headlines": "unavailable",
    }


def _seed(
    sf: sessionmaker[Session],
    live: list[tuple[str, str, str | None, dict[str, Any]]],
    at: datetime = CYCLE,
) -> int:
    """A finished live cycle with (symbol, final_action, no_call_reason, snapshot)."""
    with sf() as session:
        if session.execute(select(Run)).first() is None:
            session.add(Run(started_at=RUN_START, starting_equity=Decimal("10000")))
        cycle = Cycle(
            kind="cycle",
            scheduled_time=_naive(at),
            started_at=_naive(at),
            finished_at=_naive(at),
            status="done",
        )
        session.add(cycle)
        session.flush()
        for symbol, action, reason, snap in live:
            session.add(
                Decision(
                    cycle_id=cycle.id,
                    symbol=symbol,
                    context=snap["context"],
                    input_snapshot=snap,
                    final_action=action,
                    no_call_reason=reason,
                    prompt_version="decision.v1",
                    prompt_hash="0" * 64,
                    model="none" if action == "no_call" else "fake-decision",
                )
            )
        session.commit()
        return cycle.id


def _strategy(
    sf: sessionmaker[Session],
    name: str,
    kind: str = "llm",
    overrides: dict[str, float] | None = None,
    status: str = "active",
) -> int:
    with sf() as session:
        s = Strategy(
            name=name,
            kind=kind,
            prompt_version="decision.v1" if kind == "llm" else None,
            model="gpt-4o-mini" if kind == "llm" else None,
            temperature=0.0 if kind == "llm" else None,
            overrides=overrides,
            status=status,
            created_at=RUN_START,
        )
        session.add(s)
        session.commit()
        return s.id


def _bar(symbol: str, start: datetime, o: str, h: str, low: str, c: str) -> Bar5m:
    return Bar5m(
        symbol=symbol,
        start=start.astimezone(UTC),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=1000,
        feed="sip",
    )


class Bars:
    def __init__(self, bars: list[Bar5m] | None = None) -> None:
        self.bars = bars or []

    def __call__(self, symbol: str, start: datetime, end: datetime) -> list[Bar5m]:
        return sorted(
            (b for b in self.bars if b.symbol == symbol and start <= b.start <= end),
            key=lambda b: b.start,
        )


def _runner(
    sf: sessionmaker[Session],
    clock: FakeClock,
    *,
    clients: dict[str, Any] | None = None,
    live: LiveGateState | None = None,
    bars: Bars | None = None,
    config: AppConfig | None = None,
    notes: Notes | None = None,
) -> ShadowRunner:
    clients = clients or {}
    return ShadowRunner(
        config=config or AppConfig(),
        clock=clock,
        calendar=WeekdayCalendarProvider(),
        client_factory=lambda s: clients.get(s.name) or FakeDecisionClient(enter_symbols=set()),
        session_factory=sf,
        live_state=lambda: live or LiveGateState(),
        bars=bars or Bars(),
        notifier=notes,
    )


def _rows(sf: sessionmaker[Session], strategy_id: int) -> dict[str, ShadowDecision]:
    with sf() as session:
        return {
            r.symbol: r
            for r in session.execute(
                select(ShadowDecision).where(ShadowDecision.strategy_id == strategy_id)
            ).scalars()
        }


def _positions(sf: sessionmaker[Session], strategy_id: int) -> list[ShadowPosition]:
    with sf() as session:
        return list(
            session.execute(
                select(ShadowPosition).where(ShadowPosition.strategy_id == strategy_id)
            ).scalars()
        )


# ----------------------------------------------------------------------
# virtual fills


def _pending(sf: sessionmaker[Session], sid: int, **kw: Any) -> int:
    with sf() as session:
        run = session.execute(select(Run)).scalar_one_or_none()
        if run is None:
            run = Run(started_at=RUN_START, starting_equity=Decimal("10000"))
            session.add(run)
            session.flush()
        values: dict[str, Any] = {
            "strategy_id": sid,
            "run_id": run.id,
            "symbol": "MSFT",
            "status": "pending",
            "qty": 10,
            "limit_price": Decimal("100.05"),
            "initial_stop": Decimal("98"),
            "stop": Decimal("98"),
            "target": Decimal("104"),
            "created_at": _naive(CYCLE),
            "expires_at": _naive(CYCLE + timedelta(minutes=10)),
        }
        values.update(kw)
        sp = ShadowPosition(**values)
        session.add(sp)
        session.commit()
        return sp.id


def _replay(sf: sessionmaker[Session], sid: int, bars: Bars, now: datetime) -> list[Any]:
    with sf() as session:
        run = session.execute(select(Run)).scalar_one()
        events = replay_bars(
            session, strategy_id=sid, run_id=run.id, now=now, bars=bars, config=AppConfig()
        )
        session.commit()
        return events


def test_limit_touched_fills_at_limit(sf: sessionmaker[Session]) -> None:
    sid = _strategy(sf, "A")
    _pending(sf, sid)
    t = datetime(2026, 10, 15, 10, 50, tzinfo=NY_TZ)
    bars = Bars([_bar("MSFT", t, "100.40", "100.50", "100.00", "100.20")])
    events = _replay(sf, sid, bars, t + timedelta(minutes=20))
    assert [e.kind for e in events] == ["filled"]
    (sp,) = _positions(sf, sid)
    assert sp.status == "open"
    assert sp.entry == Decimal("100.05")


def test_stop_counts_on_the_fill_bar(sf: sessionmaker[Session]) -> None:
    sid = _strategy(sf, "A")
    _pending(sf, sid)
    t = datetime(2026, 10, 15, 10, 50, tzinfo=NY_TZ)
    bars = Bars([_bar("MSFT", t, "100.40", "100.50", "97.80", "98.10")])
    events = _replay(sf, sid, bars, t + timedelta(minutes=20))
    assert [e.kind for e in events] == ["filled", "stop"]
    (sp,) = _positions(sf, sid)
    assert sp.entry == Decimal("100.05")
    assert sp.exit_reason == "stop" and sp.exit_price == Decimal("98")


def test_target_does_not_count_on_the_fill_bar(sf: sessionmaker[Session]) -> None:
    sid = _strategy(sf, "A")
    _pending(sf, sid)
    t = datetime(2026, 10, 15, 10, 50, tzinfo=NY_TZ)
    bars = Bars(
        [
            _bar("MSFT", t, "100.40", "104.50", "100.00", "104.20"),  # fills, touches target
            _bar("MSFT", t + timedelta(minutes=5), "103.0", "103.5", "102.5", "103.0"),
        ]
    )
    events = _replay(sf, sid, bars, t + timedelta(minutes=20))
    assert [e.kind for e in events] == ["filled"]
    assert _positions(sf, sid)[0].status == "open"
    # The next bar touching the target closes it.
    later = Bars(
        [*bars.bars, _bar("MSFT", t + timedelta(minutes=10), "103.5", "104.1", "103.4", "104")]
    )
    assert [e.kind for e in _replay(sf, sid, later, t + timedelta(minutes=30))] == ["target"]


def test_bar_before_order_time_is_ignored(sf: sessionmaker[Session]) -> None:
    """The 10:45 bar started before the 10:46 decision — not "later"."""
    sid = _strategy(sf, "A")
    _pending(sf, sid)
    early = datetime(2026, 10, 15, 10, 45, tzinfo=NY_TZ)
    bars = Bars([_bar("MSFT", early, "100.00", "100.10", "99.00", "99.50")])
    assert _replay(sf, sid, bars, early + timedelta(minutes=30)) == []
    assert _positions(sf, sid)[0].status == "pending"


def test_limit_not_touched_before_timeout_is_not_filled(sf: sessionmaker[Session]) -> None:
    sid = _strategy(sf, "A")
    _pending(sf, sid)
    bars = Bars(
        [
            _bar(
                "MSFT",
                datetime(2026, 10, 15, 10, 50, tzinfo=NY_TZ),
                "100.3",
                "100.6",
                "100.1",
                "100.4",
            ),
            _bar(
                "MSFT",
                datetime(2026, 10, 15, 10, 55, tzinfo=NY_TZ),
                "100.4",
                "100.7",
                "100.2",
                "100.5",
            ),
            # After the 10-minute timeout: touching now no longer counts.
            _bar(
                "MSFT",
                datetime(2026, 10, 15, 11, 0, tzinfo=NY_TZ),
                "100.0",
                "100.1",
                "99.0",
                "99.5",
            ),
        ]
    )
    events = _replay(sf, sid, bars, datetime(2026, 10, 15, 11, 30, tzinfo=NY_TZ))
    assert [e.kind for e in events] == ["not_filled"]
    assert _positions(sf, sid)[0].status == "not_filled"


def test_stop_counts_when_stop_and_target_touched_in_one_bar(sf: sessionmaker[Session]) -> None:
    sid = _strategy(sf, "A")
    _pending(
        sf,
        sid,
        status="open",
        entry=Decimal("100"),
        opened_at=_naive(datetime(2026, 10, 14, 11, 0, tzinfo=NY_TZ)),
        bars_through=_naive(datetime(2026, 10, 15, 10, 45, tzinfo=NY_TZ)),
    )
    t = datetime(2026, 10, 15, 10, 50, tzinfo=NY_TZ)
    bars = Bars([_bar("MSFT", t, "100", "104.5", "97.5", "101")])
    events = _replay(sf, sid, bars, t + timedelta(minutes=20))
    assert [e.kind for e in events] == ["stop"]
    (sp,) = _positions(sf, sid)
    assert sp.exit_price == Decimal("98")
    # 10 sh: (98 × 0.9995 − 100 × 1.0005) × 10 = −20.99 → −1.0495R on $20 risk.
    assert sp.pnl == Decimal("-20.99")
    assert sp.r is not None and sp.r.quantize(Decimal("0.0001")) == Decimal("-1.0495")


def test_target_only_and_gap_through_stop_fills_at_open(sf: sessionmaker[Session]) -> None:
    sid = _strategy(sf, "A")
    base = {
        "status": "open",
        "entry": Decimal("100"),
        "opened_at": _naive(datetime(2026, 10, 14, 11, 0, tzinfo=NY_TZ)),
        "bars_through": _naive(datetime(2026, 10, 15, 10, 45, tzinfo=NY_TZ)),
    }
    _pending(sf, sid, **base)
    _pending(sf, sid, symbol="AAPL", **base)
    t = datetime(2026, 10, 15, 10, 50, tzinfo=NY_TZ)
    bars = Bars(
        [
            _bar("MSFT", t, "101", "104.2", "100.5", "104"),  # target only
            _bar("AAPL", t, "96", "97", "95", "96.5"),  # gaps through the stop
        ]
    )
    _replay(sf, sid, bars, t + timedelta(minutes=20))
    by_symbol = {p.symbol: p for p in _positions(sf, sid)}
    assert by_symbol["MSFT"].exit_reason == "target"
    assert by_symbol["MSFT"].exit_price == Decimal("104")
    assert by_symbol["AAPL"].exit_reason == "stop"
    assert by_symbol["AAPL"].exit_price == Decimal("96")


# ----------------------------------------------------------------------
# decisions on stored snapshots


async def test_llm_shadow_enters_with_its_own_model_and_live_limit(
    sf: sessionmaker[Session],
) -> None:
    cycle_id = _seed(
        sf,
        [
            ("MSFT", "skip", None, _snap("MSFT")),
            ("AAPL", "no_call", "rules", _snap("AAPL", rsi=75)),
        ],
    )
    sid = _strategy(sf, "B · Bigger model")
    clock = FakeClock(CYCLE + timedelta(minutes=1))
    runner = _runner(
        sf, clock, clients={"B · Bigger model": FakeDecisionClient(enter_symbols={"MSFT"})}
    )
    report = await runner.after_cycle(cycle_id)
    rows = _rows(sf, sid)
    assert rows["MSFT"].final_action == "enter"
    assert rows["MSFT"].cost is not None and rows["MSFT"].cost > 0
    assert rows["AAPL"].final_action == "no_call"
    assert rows["AAPL"].no_call_reason == "rules"
    (sp,) = _positions(sf, sid)
    # Same limit rule as live: price + 0.05%, cents.
    assert sp.limit_price == Decimal("100.05")
    assert sp.status == "pending"
    assert report.strategies[0].calls == 1


async def test_shadow_without_live_quote_marks_synthetic_entry(sf: sessionmaker[Session]) -> None:
    cycle_id = _seed(sf, [("MSFT", "no_call", "rules", _snap("MSFT", atr=1))])
    sid = _strategy(sf, "Rules only", kind="rules_only")
    await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1))).after_cycle(cycle_id)
    (position,) = _positions(sf, sid)
    assert position.limit_price == Decimal("100.05")
    gates = _rows(sf, sid)["MSFT"].gates_result
    assert gates is not None
    assert gates["quote"]["synthetic"] is True


async def test_shadow_cannot_replace_suspect_live_price_with_synthetic(
    sf: sessionmaker[Session],
) -> None:
    """A live market price that failed the tape check stays blocked in the
    shadow — it is never swapped for a synthetic bar close."""
    snapshot = _snap("MSFT", price=105, atr=1)
    snapshot["last_bar_close"] = 100
    snapshot["quote"] = {
        "bid": "104.99", "ask": "105", "last": "105", "feed": "iex",
        "quote_timestamp": CYCLE.isoformat(),
        "trade_timestamp": CYCLE.isoformat(),
        "fetched_at": CYCLE.isoformat(),
    }
    cycle_id = _seed(sf, [("MSFT", "no_call", "price_suspect", snapshot)])
    sid = _strategy(sf, "Rules only", kind="rules_only")
    await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1))).after_cycle(cycle_id)
    assert _positions(sf, sid) == []
    gates = _rows(sf, sid)["MSFT"].gates_result
    assert gates is not None
    assert gates["blocked_by"] == "price_suspect"


async def test_shadow_rejects_stop_too_close_to_bar_close(sf: sessionmaker[Session]) -> None:
    snapshot = _snap("MSFT", price=101, atr=1)
    snapshot["last_bar_close"] = 100
    snapshot["quote"] = {
        "bid": "100.99", "ask": "101", "last": "101", "feed": "iex",
        "quote_timestamp": CYCLE.isoformat(),
        "trade_timestamp": CYCLE.isoformat(),
        "fetched_at": CYCLE.isoformat(),
    }
    cycle_id = _seed(sf, [("MSFT", "skip", None, snapshot)])
    sid = _strategy(sf, "B")
    response = {
        "symbol": "MSFT", "action": "enter", "confidence": 0.9,
        "stop_price": 99, "target_price": 106, "thesis": "test", "invalidation": "test",
        "risks": [], "news_used": [],
    }
    await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1)), clients={
        "B": FakeDecisionClient(responses={"MSFT": json.dumps(response)}),
    }).after_cycle(cycle_id)
    assert _positions(sf, sid) == []
    gates = _rows(sf, sid)["MSFT"].gates_result
    assert gates is not None
    assert gates["blocked_by"] == "stop_market_distance"


async def test_shadow_rechecks_reward_to_risk_on_final_limit(sf: sessionmaker[Session]) -> None:
    cycle_id = _seed(sf, [("MSFT", "skip", None, _snap("MSFT", atr=1))])
    sid = _strategy(sf, "Final")
    response = {
        "symbol": "MSFT", "action": "enter", "confidence": 0.9,
        "stop_price": 98, "target_price": 103, "thesis": "test", "invalidation": "test",
        "risks": [], "news_used": [],
    }
    await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1)), clients={
        "Final": FakeDecisionClient(responses={"MSFT": json.dumps(response)}),
    }).after_cycle(cycle_id)
    assert _positions(sf, sid) == []
    gates = _rows(sf, sid)["MSFT"].gates_result
    assert gates is not None
    assert gates["blocked_by"] == "code_checks"


async def test_shadow_sizes_on_final_limit_not_snapshot_ask(sf: sessionmaker[Session]) -> None:
    cycle_id = _seed(sf, [("MSFT", "skip", None, _snap("MSFT", atr=1))])
    sid = _strategy(sf, "Rules only", kind="rules_only")
    await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1))).after_cycle(cycle_id)
    (position,) = _positions(sf, sid)
    assert position.qty == 19
    assert position.limit_price is not None
    assert position.qty * position.limit_price <= Decimal("2000")
    gates = _rows(sf, sid)["MSFT"].gates_result
    assert gates is not None
    sizing = gates["sizing"]
    assert isinstance(sizing, dict)
    assert sizing["shares"] == position.qty


async def test_rules_only_baseline_stop_two_atr_target_two_r(sf: sessionmaker[Session]) -> None:
    cycle_id = _seed(sf, [("MSFT", "skip", None, _snap("MSFT", atr=1.5))])
    sid = _strategy(sf, "Rules only", kind="rules_only")
    await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1))).after_cycle(cycle_id)
    assert _rows(sf, sid)["MSFT"].final_action == "enter"
    (sp,) = _positions(sf, sid)
    assert sp.stop == Decimal("97.00")  # 100 − 2 × 1.5
    assert sp.target == Decimal("106.00")  # 100 + 2 × 3


async def test_rule_overrides_change_candidates(sf: sessionmaker[Session]) -> None:
    """A wider RSI range makes a live rules no_call a shadow candidate."""
    cycle_id = _seed(sf, [("MSFT", "no_call", "rules", _snap("MSFT", rsi=75))])
    wide = _strategy(sf, "Wide", kind="rules_only", overrides={"rsi_max": 80})
    plain = _strategy(sf, "Plain", kind="rules_only")
    await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1))).after_cycle(cycle_id)
    assert _rows(sf, wide)["MSFT"].final_action == "enter"
    assert _rows(sf, plain)["MSFT"].no_call_reason == "rules"


async def test_shadow_cannot_override_gates(sf: sessionmaker[Session]) -> None:
    """Risk gates are never overridable: an attempt is refused at creation,
    and at run time the live max-open-positions gate blocks the shadow."""
    config = AppConfig()
    with sf() as session, pytest.raises(StrategyError) as exc:
        validate_new_strategy(
            {"name": "X", "kind": "rules_only", "overrides": {"max_open_positions": 10}},
            config,
            session,
        )
    assert any("not overridable" in e for e in exc.value.errors)

    sid = _strategy(sf, "Rules only", kind="rules_only")
    for symbol in ("AAPL", "NVDA", "AMZN", "JPM"):
        _pending(
            sf,
            sid,
            symbol=symbol,
            status="open",
            qty=1,
            entry=Decimal("100"),
            opened_at=_naive(datetime(2026, 10, 14, 11, 0, tzinfo=NY_TZ)),
            bars_through=_naive(CYCLE),
        )
    cycle_id = _seed(sf, [("XOM", "skip", None, _snap("XOM"))])
    await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1))).after_cycle(cycle_id)
    row = _rows(sf, sid)["XOM"]
    assert row.final_action == "blocked"
    assert row.gates_result is not None
    assert row.gates_result["blocked_by"] == "max_open_positions"


async def test_paused_live_blocks_shadow_entries_but_stops_still_hit(
    sf: sessionmaker[Session],
) -> None:
    sid = _strategy(sf, "Rules only", kind="rules_only")
    _pending(
        sf,
        sid,
        symbol="AAPL",
        status="open",
        entry=Decimal("100"),
        opened_at=_naive(datetime(2026, 10, 14, 11, 0, tzinfo=NY_TZ)),
        bars_through=_naive(datetime(2026, 10, 15, 10, 30, tzinfo=NY_TZ)),
    )
    bars = Bars(
        [_bar("AAPL", datetime(2026, 10, 15, 10, 35, tzinfo=NY_TZ), "99", "99.5", "97", "97.5")]
    )
    cycle_id = _seed(sf, [("MSFT", "skip", None, _snap("MSFT"))])
    runner = _runner(
        sf, FakeClock(CYCLE + timedelta(minutes=1)), live=LiveGateState(paused=True), bars=bars
    )
    await runner.after_cycle(cycle_id)
    row = _rows(sf, sid)["MSFT"]
    assert row.final_action == "blocked"
    assert row.gates_result is not None and row.gates_result["blocked_by"] == "paused"
    aapl = next(p for p in _positions(sf, sid) if p.symbol == "AAPL")
    assert aapl.status == "closed" and aapl.exit_reason == "stop"


async def test_kill_switch_and_reconciliation_use_live_gate_names(
    sf: sessionmaker[Session],
) -> None:
    for live, name in (
        (LiveGateState(kill_switch_on=True), "kill_switch"),
        (LiveGateState(pending_ack=True), "reconciliation"),
    ):
        cycle_id = _seed(sf, [("MSFT", "skip", None, _snap("MSFT"))])
        sid = _strategy(sf, f"R-{name}", kind="rules_only")
        await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1)), live=live).after_cycle(cycle_id)
        row = _rows(sf, sid)["MSFT"]
        assert row.gates_result is not None and row.gates_result["blocked_by"] == name
        with sf() as session:
            session.get(Strategy, sid).status = "archived"  # type: ignore[union-attr]
            session.commit()


async def test_shadow_at_own_drawdown_stops_entering(sf: sessionmaker[Session]) -> None:
    """A shadow 9% below its own peak hits the drawdown gate; live is
    unaffected (its gates read live's account, not the shadow's)."""
    sid = _strategy(sf, "Rules only", kind="rules_only")
    with sf() as session:
        run = Run(started_at=RUN_START, starting_equity=Decimal("10000"))
        session.add(run)
        session.flush()
        session.add(
            ShadowPosition(
                strategy_id=sid,
                run_id=run.id,
                symbol="NVDA",
                status="closed",
                qty=10,
                entry=Decimal("100"),
                initial_stop=Decimal("90"),
                stop=Decimal("90"),
                created_at=RUN_START,
                expires_at=RUN_START,
                opened_at=RUN_START,
                closed_at=RUN_START + timedelta(days=1),
                pnl=Decimal("-900"),
            )
        )
        session.add(
            ShadowEquity(
                strategy_id=sid,
                run_id=run.id,
                time=RUN_START,
                equity=Decimal("10000"),
                cash=Decimal("10000"),
            )
        )
        # Yesterday's close already carried the loss: no daily-loss hit.
        session.add(
            ShadowEquity(
                strategy_id=sid,
                run_id=run.id,
                time=_naive(datetime(2026, 10, 14, 16, 0, tzinfo=NY_TZ)),
                equity=Decimal("9100"),
                cash=Decimal("9100"),
            )
        )
        session.commit()
    cycle_id = _seed(sf, [("MSFT", "skip", None, _snap("MSFT"))])
    await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1))).after_cycle(cycle_id)
    row = _rows(sf, sid)["MSFT"]
    assert row.gates_result is not None and row.gates_result["blocked_by"] == "drawdown_kill"


async def test_same_day_exit_is_deferred(sf: sessionmaker[Session]) -> None:
    sid = _strategy(sf, "A")
    _pending(
        sf,
        sid,
        status="open",
        entry=Decimal("100"),
        opened_at=_naive(datetime(2026, 10, 15, 10, 0, tzinfo=NY_TZ)),
        bars_through=_naive(CYCLE),
    )
    exit_reply = json.dumps(
        {
            "symbol": "MSFT",
            "action": "exit",
            "confidence": 0.7,
            "stop_price": None,
            "target_price": None,
            "thesis": "t",
            "invalidation": None,
            "risks": [],
            "news_used": [],
        }
    )
    cycle_id = _seed(sf, [("MSFT", "hold", None, _snap("MSFT", context="held"))])
    runner = _runner(
        sf,
        FakeClock(CYCLE + timedelta(minutes=1)),
        clients={"A": FakeDecisionClient(responses={("MSFT", "held"): exit_reply})},
    )
    await runner.after_cycle(cycle_id)
    assert _rows(sf, sid)["MSFT"].final_action == "deferred"
    (sp,) = _positions(sf, sid)
    assert sp.status == "open" and sp.deferred_exit is not None


async def test_stop_and_flatten_closes_shadow_positions_at_next_open(
    sf: sessionmaker[Session],
) -> None:
    sid = _strategy(sf, "Rules only", kind="rules_only")
    _pending(
        sf,
        sid,
        status="open",
        entry=Decimal("100"),
        opened_at=_naive(datetime(2026, 10, 14, 11, 0, tzinfo=NY_TZ)),
        bars_through=_naive(datetime(2026, 10, 15, 10, 45, tzinfo=NY_TZ)),
    )
    _pending(sf, sid, symbol="AAPL")  # working entry → cancelled
    clock = FakeClock(datetime(2026, 10, 15, 10, 52, tzinfo=NY_TZ))
    bars = Bars(
        [
            _bar(
                "MSFT", datetime(2026, 10, 15, 10, 50, tzinfo=NY_TZ), "100.5", "101", "100", "100.8"
            ),
            _bar(
                "MSFT",
                datetime(2026, 10, 15, 10, 55, tzinfo=NY_TZ),
                "101.2",
                "101.5",
                "100.9",
                "101",
            ),
        ]
    )
    runner = _runner(sf, clock, bars=bars)
    assert runner.flatten() == 2
    clock.set(datetime(2026, 10, 15, 11, 1, tzinfo=NY_TZ))
    await runner.after_check()
    by_symbol = {p.symbol: p for p in _positions(sf, sid)}
    assert by_symbol["AAPL"].status == "cancelled"
    msft = by_symbol["MSFT"]
    assert msft.exit_reason == "flatten"
    # The bar starting after the request (10:55), at its open.
    assert msft.exit_price == Decimal("101.2")


# ----------------------------------------------------------------------
# cost cap


async def test_cost_cap_pauses_llm_shadows_only(sf: sessionmaker[Session]) -> None:
    llm = _strategy(sf, "A")
    rules = _strategy(sf, "Rules only", kind="rules_only")
    first = _seed(sf, [("MSFT", "skip", None, _snap("MSFT"))], at=CYCLE - timedelta(days=1))
    with sf() as session:
        session.add(
            ShadowDecision(
                strategy_id=llm,
                cycle_id=first,
                symbol="MSFT",
                context="candidate",
                final_action="skip",
                cost=Decimal("5.00"),
                missed=False,
                created_at=_naive(CYCLE - timedelta(days=1)),
            )
        )
        session.commit()
    cycle_id = _seed(sf, [("MSFT", "skip", None, _snap("MSFT"))])
    notes = Notes()
    runner = _runner(sf, FakeClock(CYCLE + timedelta(minutes=1)), notes=notes)
    report = await runner.after_cycle(cycle_id)
    by_id = {r.strategy_id: r for r in report.strategies}
    assert by_id[llm].paused_by_cap and by_id[llm].calls == 0
    assert not by_id[rules].paused_by_cap
    with sf() as session:
        llm_rows = session.execute(
            select(ShadowDecision).where(
                ShadowDecision.strategy_id == llm, ShadowDecision.cycle_id == cycle_id
            )
        ).all()
    assert llm_rows == []
    assert _rows(sf, rules)["MSFT"].final_action == "enter"
    assert [e for e, _ in notes.sent] == ["model_failing"]
    assert "Shadow cost cap reached" in notes.sent[0][1]
    # Warned once per month, not every cycle.
    await runner.after_cycle(
        _seed(sf, [("MSFT", "skip", None, _snap("MSFT"))], at=CYCLE + timedelta(hours=1))
    )
    assert len(notes.sent) == 1


# ----------------------------------------------------------------------
# never delay live


class SlowClient:
    """Each call takes 6 minutes of (fake) time."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.inner = FakeDecisionClient(enter_symbols=set())

    async def complete(
        self, *, system: str, user: str, schema: dict[str, Any] | None = None
    ) -> ModelResponse:
        self.clock.advance(timedelta(minutes=6))
        return await self.inner.complete(system=system, user=user)


async def test_work_past_next_live_event_is_missed(sf: sessionmaker[Session]) -> None:
    symbols = ["MSFT", "AAPL", "NVDA", "AMZN", "META"]
    cycle_id = _seed(sf, [(s, "skip", None, _snap(s)) for s in symbols])
    sid = _strategy(sf, "A")
    clock = FakeClock(CYCLE)
    runner = _runner(sf, clock, clients={"A": SlowClient(clock)})
    report = await runner.after_cycle(cycle_id)
    rows = _rows(sf, sid)
    called = [r for r in rows.values() if r.tokens is not None]
    missed = [r for r in rows.values() if r.missed]
    # Calls at 10:46, 10:52 and 10:58 fit before the 11:01 check (30 s
    # margin); the rest is skipped and recorded as missed.
    assert len(called) == 3
    assert len(missed) == 2
    assert {r.missed_reason for r in missed} == {"time"}
    assert report.strategies[0].missed == 2
    assert clock.now() <= datetime(2026, 10, 15, 11, 4, tzinfo=NY_TZ)


async def test_cost_cap_reached_mid_cycle_records_cost_cap_reason(
    sf: sessionmaker[Session],
) -> None:
    symbols = ["MSFT", "AAPL", "NVDA"]
    cycle_id = _seed(sf, [(s, "skip", None, _snap(s)) for s in symbols])
    sid = _strategy(sf, "A")
    config = AppConfig()
    # The first fake call (~$0.0001) already exceeds this cap.
    config._active["shadows"]["monthly_cost_cap_usd"] = 0.00001
    notes = Notes()
    runner = _runner(sf, FakeClock(CYCLE + timedelta(minutes=1)), config=config, notes=notes)
    report = await runner.after_cycle(cycle_id)
    rows = _rows(sf, sid)
    assert report.strategies[0].calls == 1
    missed = [r for r in rows.values() if r.missed]
    assert len(missed) == 2
    assert {r.missed_reason for r in missed} == {"cost_cap"}
    assert [e for e, _ in notes.sent] == ["model_failing"]


async def test_shadow_only_symbol_uses_the_snapshot_live_built(
    sf: sessionmaker[Session],
) -> None:
    """CAT is held only by the shadow: live built its snapshot but made no
    decision, so the shadow decides on it without a live decision row and
    it stays out of "same as live"."""
    sid = _strategy(sf, "A")
    _pending(
        sf,
        sid,
        symbol="CAT",
        status="open",
        entry=Decimal("100"),
        opened_at=_naive(datetime(2026, 10, 14, 11, 0, tzinfo=NY_TZ)),
        bars_through=_naive(CYCLE),
    )
    cycle_id = _seed(sf, [("MSFT", "skip", None, _snap("MSFT"))])
    runner = _runner(sf, FakeClock(CYCLE + timedelta(minutes=1)))
    assert runner.held_symbols() == ["CAT"]
    await runner.after_cycle(cycle_id, {"CAT": _snap("CAT", context="held")})
    cat = _rows(sf, sid)["CAT"]
    assert cat.live_decision_id is None
    assert cat.context == "held"
    assert cat.final_action == "hold"
    assert cat.tokens is not None  # the LLM shadow was called for it
    assert cat.input_snapshot is not None and cat.input_snapshot["symbol"] == "CAT"
    assert cat.position is not None and cat.position["qty"] == 10
    with sf() as session:
        result = same_as_live(session, sid, RUN_START)
    assert result.counted == 1  # MSFT only


# ----------------------------------------------------------------------
# "same as live"


def _pair(
    sf: sessionmaker[Session],
    sid: int,
    cycle_id: int,
    symbol: str,
    action: str,
    reason: str | None = None,
    missed: bool = False,
) -> None:
    with sf() as session:
        live = session.execute(
            select(Decision).where(Decision.cycle_id == cycle_id, Decision.symbol == symbol)
        ).scalar_one()
        session.add(
            ShadowDecision(
                strategy_id=sid,
                cycle_id=cycle_id,
                symbol=symbol,
                context="candidate",
                live_decision_id=live.id,
                final_action=action,
                no_call_reason=reason,
                missed=missed,
                created_at=_naive(CYCLE),
            )
        )
        session.commit()


def test_same_as_live_counts_per_spec(sf: sessionmaker[Session]) -> None:
    sid = _strategy(sf, "A")
    cycle_id = _seed(
        sf,
        [
            ("SPY", "no_call", "rules", _snap("SPY")),  # both rules → left out
            ("MSFT", "skip", None, _snap("MSFT")),  # vs call_cap → mismatch
            ("AAPL", "skip", None, _snap("AAPL")),  # match
            ("NVDA", "no_call", "call_cap", _snap("NVDA")),  # vs quote → match
            ("META", "skip", None, _snap("META")),  # missed → left out
            ("JPM", "enter", None, _snap("JPM")),  # vs rules no_call → mismatch
        ],
    )
    _pair(sf, sid, cycle_id, "SPY", "no_call", "rules")
    _pair(sf, sid, cycle_id, "MSFT", "no_call", "call_cap")
    _pair(sf, sid, cycle_id, "AAPL", "skip")
    _pair(sf, sid, cycle_id, "NVDA", "no_call", "price_stale")
    _pair(sf, sid, cycle_id, "META", "no_call", missed=True)
    _pair(sf, sid, cycle_id, "JPM", "no_call", "rules")
    with sf() as session:
        result = same_as_live(session, sid, RUN_START)
    assert (result.matched, result.counted) == (2, 4)
    assert result.pct == Decimal(50)


def test_both_rules_no_call_cycle_does_not_change_share(sf: sessionmaker[Session]) -> None:
    sid = _strategy(sf, "A")
    c1 = _seed(sf, [("MSFT", "skip", None, _snap("MSFT"))])
    _pair(sf, sid, c1, "MSFT", "skip")
    with sf() as session:
        before = same_as_live(session, sid, RUN_START)
    c2 = _seed(
        sf,
        [(s, "no_call", "rules", _snap(s)) for s in ("AAPL", "NVDA", "XOM")],
        at=CYCLE + timedelta(hours=1),
    )
    for s in ("AAPL", "NVDA", "XOM"):
        _pair(sf, sid, c2, s, "no_call", "rules")
    with sf() as session:
        after = same_as_live(session, sid, RUN_START)
    assert (before.matched, before.counted) == (after.matched, after.counted) == (1, 1)


async def test_paused_shadow_keeps_its_slot_and_manages_positions(
    sf: sessionmaker[Session],
) -> None:
    """A paused shadow makes no calls or entries, still has its stops
    checked, and keeps its slot until archived."""
    sid = _strategy(sf, "A", status="paused")
    _strategy(sf, "B")
    _strategy(sf, "C", kind="rules_only")
    _pending(
        sf,
        sid,
        symbol="AAPL",
        status="open",
        entry=Decimal("100"),
        opened_at=_naive(datetime(2026, 10, 14, 11, 0, tzinfo=NY_TZ)),
        bars_through=_naive(datetime(2026, 10, 15, 10, 30, tzinfo=NY_TZ)),
    )
    bars = Bars(
        [_bar("AAPL", datetime(2026, 10, 15, 10, 35, tzinfo=NY_TZ), "99", "99.5", "97", "97.5")]
    )
    cycle_id = _seed(sf, [("MSFT", "skip", None, _snap("MSFT"))])
    await _runner(sf, FakeClock(CYCLE + timedelta(minutes=1)), bars=bars).after_cycle(cycle_id)
    assert _rows(sf, sid) == {}
    (aapl,) = _positions(sf, sid)
    assert aapl.exit_reason == "stop"
    with sf() as session, pytest.raises(StrategyError) as exc:
        validate_new_strategy({"name": "D", "kind": "rules_only"}, AppConfig(), session)
    assert any("slots" in e for e in exc.value.errors)
