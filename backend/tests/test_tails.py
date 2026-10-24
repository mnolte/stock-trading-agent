"""Tail metrics (PR 19): the pure tails function, the daily after-exit
job, and GET /api/performance/tails.

Every metric is checked against a hand calculation on a fixture run —
including a loss below −1R, a pending trade, a split inside the
after-exit window and a symbol whose bars are fetched through the
(injected) bar source. No network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
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
from app.data.models import Bar5m, CalendarDay, DataResult
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import (
    Bar5m as Bar5mRow,
    CorporateAction,
    Cycle,
    Decision,
    Fill,
    Order,
    Outcome,
    Position,
    Run,
)
from app.engine.after_exit import AfterExitWorker, split_factor_after
from app.engine.performance import TailTrade, compute_tails
from app.market.clock import NY_TZ, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

NY_OPEN = time(9, 30)
NY_CLOSE = time(16, 0)


def _day(d: date) -> CalendarDay:
    return CalendarDay(
        date=d,
        open=datetime.combine(d, NY_OPEN, tzinfo=NY_TZ),
        close=datetime.combine(d, NY_CLOSE, tzinfo=NY_TZ),
    )


def _tail(**kw: Any) -> TailTrade:
    base: dict[str, Any] = {
        "id": 1,
        "symbol": "AAA",
        "origin": "agent",
        "qty": 10,
        "entry": Decimal("100"),
        "exit_price": Decimal("110"),
        "initial_stop": Decimal("90"),
        "adjusted_pnl": Decimal("100"),
        "exit_reason": "target",
        "opened_at": datetime(2026, 10, 5, 13, 30),
        "closed_at": datetime(2026, 10, 6, 20, 0),
        "best_open_price": Decimal("112"),
        "after_exit_status": "done",
        "after_exit_best_price": Decimal("115"),
        "after_exit_close_price": Decimal("112"),
    }
    base.update(kw)
    return TailTrade(**base)


def _fn(trades: list[TailTrade], **kw: Any) -> dict[str, Any]:
    args = {"bucket_r": Decimal("0.5"), "top_share_pct": 20, "min_trades": 20}
    args.update(kw)
    return compute_tails(trades, **args)


def test_distribution_hand_calculation() -> None:
    """Buckets of 0.5R; each bucket includes its lower edge."""
    rs = ["-1.05", "-1.02", "0.9", "1.6", "1.6", "2.6"]
    trades = [
        _tail(id=i, adjusted_pnl=Decimal(r) * 100, exit_reason="exit")
        for i, r in enumerate(rs)
    ]
    out = _fn(trades)
    buckets = {(b["lower"], b["upper"]): b["count"] for b in out["distribution"]}
    # −1.05/−1.02 → [−1.5,−1); 0.9 → [0.5,1); 1.6s → [1.5,2); 2.6 → [2.5,3).
    assert buckets[(None, Decimal("-1.5"))] == 0
    assert buckets[(Decimal("-1.5"), Decimal("-1"))] == 2
    assert buckets[(Decimal("0.5"), Decimal("1"))] == 1
    assert buckets[(Decimal("1.5"), Decimal("2"))] == 2
    assert buckets[(Decimal("2.5"), Decimal("3"))] == 1
    assert sum(b["count"] for b in out["distribution"]) == 6


def test_bucket_lower_edge_is_included() -> None:
    out = _fn([_tail(adjusted_pnl=Decimal("-100"))])  # exactly −1.00R
    buckets = {(b["lower"], b["upper"]): b["count"] for b in out["distribution"]}
    assert buckets[(Decimal("-1"), Decimal("-0.5"))] == 1
    assert buckets[(Decimal("-1.5"), Decimal("-1"))] == 0


def test_largest_loss_below_minus_1r() -> None:
    """−1.05R (filled below the stop) flags below_stop."""
    trades = [
        _tail(id=1, adjusted_pnl=Decimal("-105")),  # −1.05R
        _tail(id=2, adjusted_pnl=Decimal("160"), symbol="BBB"),  # +1.6R
    ]
    out = _fn(trades)
    assert out["largest_loss"]["r"] == Decimal("-1.05")
    assert out["largest_loss"]["below_stop"] is True
    assert out["largest_win"]["symbol"] == "BBB"
    assert out["largest_win"]["below_stop"] is False


def test_top_share_net_without_best_winners_kept() -> None:
    """6 trades, winners 160+90+260+160=670: top ceil(20%×6)=2 → 420/670."""
    adjusted = [Decimal("160"), Decimal("90"), Decimal("-100"), Decimal("260"), Decimal("160"), Decimal("-110")]
    trades = [
        _tail(
            id=i,
            symbol=f"S{i}",
            adjusted_pnl=a,
            best_open_price=Decimal("100") + (Decimal(a) / 10 if a > 0 else Decimal("-5")),
        )
        for i, a in enumerate(adjusted)
    ]
    out = _fn(trades)
    assert out["top_share"]["trades"] == 2
    assert out["top_share"]["of"] == 6
    # gross winners = 670; top 2 (260+160) = 420 → 62.69%.
    assert out["top_share"]["share"] == Decimal(420) / Decimal(670)
    # net = 460; best = 260 → 200.
    assert out["net_without_best"]["net"] == Decimal("460") - Decimal("260")
    assert out["net_without_best"]["total"] == Decimal("460")
    # Winners kept: r = a/100; best_open_r = (100+a/10−100)/10 = a/100
    # → every ratio is 1 → mean 1.
    assert out["winners_kept"]["share"] == Decimal("1")
    assert out["winners_kept"]["winners"] == 4


def test_no_winners_hides_top_share() -> None:
    out = _fn([
        _tail(id=1, adjusted_pnl=Decimal("-50")),
        _tail(id=2, adjusted_pnl=Decimal("-30")),
    ])
    assert out["top_share"] is None
    assert out["winners_kept"] is None
    assert out["largest_loss"]["r"] == Decimal("-0.5")


def test_after_exit_medians_per_reason() -> None:
    def t(id: int, reason: str, best: str, close: str) -> TailTrade:
        return _tail(
            id=id,
            exit_reason=reason,
            exit_price=Decimal("110"),
            after_exit_best_price=Decimal(best),
            after_exit_close_price=Decimal(close),
        )

    out = _fn([
        t(1, "target", "123", "114"),   # best +1.3R, close +0.4R
        t(2, "target", "115", "112"),   # best +0.5R, close +0.2R
        t(3, "target", "121", "110"),   # best +1.1R, close 0R
        t(4, "stop", "114", "109"),     # best +0.4R, close −0.1R
        t(5, "stop", "113", "104"),     # best +0.3R, close −0.6R
        t(6, "exit", "115", "108"),     # best +0.5R, close −0.2R
    ])
    by = {g["reason"]: g for g in out["after_exit"]}
    assert by["target"]["median_best_r"] == Decimal("1.1")
    assert by["target"]["median_close_r"] == Decimal("0.2")
    assert by["target"]["share_1r"] == Decimal(2) / Decimal(3)
    assert by["stop"]["median_close_r"] == Decimal("-0.35")
    # Only the trade at 114 recovered to/above entry (100)? No — all
    # bests are ≥ entry 100 → all recovered.
    assert by["stop"]["recovered_above_entry"] == 2
    assert by["exit"]["median_best_r"] == Decimal("0.5")


def test_pending_and_excluded_are_counted() -> None:
    pending = _tail(
        id=1, after_exit_status="pending",
        after_exit_best_price=None, after_exit_close_price=None,
    )
    adopted = _tail(
        id=2, origin="adopted", initial_stop=None, symbol="ZZZ",
        adjusted_pnl=Decimal("50"), after_exit_status="pending",
        after_exit_best_price=None, after_exit_close_price=None,
    )
    out = _fn([pending, adopted])
    assert out["trades"] == 1
    assert out["pending"] == 1
    assert out["excluded"] == 1
    assert len(out["rows"]) == 1
    assert out["rows"][0]["after_exit_status"] == "pending"


def test_small_sample_boundary() -> None:
    trades = [_tail(id=i) for i in range(19)]
    out = _fn(trades)
    assert out["trades"] == 19
    assert out["trades"] < out["min_trades"]
    out20 = _fn([_tail(id=i) for i in range(20)])
    assert out20["trades"] == 20
    assert not out20["trades"] < out20["min_trades"]


# ----------------------------------------------------------------------
# The daily after-exit job


class _FailingBars(FakeBarSource):
    """A bar source that fails the first ``fail`` fetches."""

    def __init__(self, calendar: FakeCalendarSource, fail: int = 0) -> None:
        super().__init__(calendar)
        self.fail_remaining = fail
        self.calls = 0

    async def fetch_5m_bars(self, symbols, start, end):  # type: ignore[override]
        self.calls += 1
        if self.fail_remaining:
            self.fail_remaining -= 1
            return DataResult(data=[], error="down", source="fake")
        return await super().fetch_5m_bars(symbols, start, end)


def _worker(
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    *,
    bars: FakeBarSource | None = None,
) -> tuple[AfterExitWorker, MarketDataService, FakeCalendarSource]:
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["AAA"]
    calendar = FakeCalendarSource()
    bar_source = bars or FakeBarSource(calendar)
    sources = DataSources(
        bars=bar_source,
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
    worker = AfterExitWorker(
        market_data=market_data,
        sources=sources,
        calendar=calendar,
        config=config,
        clock=clock,
        session_factory=session_factory,
    )
    return worker, market_data, calendar


def _closed_position(
    session: Session, *, closed: datetime, symbol: str = "AAA"
) -> Position:
    position = Position(
        symbol=symbol,
        origin="agent",
        qty=10,
        avg_entry_price=Decimal("100"),
        initial_stop=Decimal("95"),
        opened_at=closed - timedelta(days=2),
        closed_at=closed,
        realized_pnl_raw=Decimal("50"),
        realized_pnl_adjusted=Decimal("49"),
        exit_reason="target",
    )
    session.add(position)
    session.flush()
    return position


async def test_job_fills_after_five_sessions(session_factory) -> None:
    """Exit Tue Oct 6 → window Oct 7,8,9,12,13; job runs after the 13th's
    close (+SIP delay)."""
    exit_at = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)
    now = datetime(2026, 10, 13, 21, 0, tzinfo=UTC)  # after 16:00 close
    clock = FakeClock(now)
    worker, market_data, _ = _worker(session_factory, clock)
    with session_factory() as session:
        p = _closed_position(session, closed=exit_at.replace(tzinfo=None))
        session.commit()
        pid = p.id
    # Cache bars through Oct 13.
    await market_data.refresh_bars(
        datetime(2026, 10, 5, tzinfo=UTC), now
    )
    report = await worker.fill(now)
    assert report.filled == 1
    with session_factory() as session:
        p = session.get(Position, pid)
        assert p.after_exit_status == "done"
        assert p.after_exit_done_at is not None
        bars = session.execute(
            select(Bar5mRow).where(
                Bar5mRow.symbol == "AAA",
                Bar5mRow.start >= exit_at.replace(tzinfo=None),
                Bar5mRow.start
                <= datetime.combine(date(2026, 10, 13), time(16, 0))
                .replace(tzinfo=NY_TZ).astimezone(UTC).replace(tzinfo=None),
            )
        ).scalars().all()
        best = max(bars, key=lambda b: b.high)
        last = max(bars, key=lambda b: b.start)
        assert p.after_exit_best_price == best.high
        assert p.after_exit_best_at == best.start
        assert p.after_exit_close_price == last.close
        before = (p.after_exit_best_price, p.after_exit_close_price)
    # Write-once: a second pass leaves the stored values untouched.
    report = await worker.fill(datetime(2026, 10, 14, 21, 0, tzinfo=UTC))
    assert report.filled == 0
    with session_factory() as session:
        p = session.get(Position, pid)
        assert (p.after_exit_best_price, p.after_exit_close_price) == before


async def test_job_stays_pending_inside_the_window(session_factory) -> None:
    exit_at = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)  # Friday
    now = datetime(2026, 10, 13, 21, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, market_data, _ = _worker(session_factory, clock)
    with session_factory() as session:
        _closed_position(session, closed=exit_at.replace(tzinfo=None))
        session.commit()
    await market_data.refresh_bars(datetime(2026, 10, 5, tzinfo=UTC), now)
    report = await worker.fill(now)
    assert report.filled == 0
    with session_factory() as session:
        p = session.execute(select(Position)).scalar_one()
        assert p.after_exit_status == "pending"


async def test_job_fetches_uncached_symbol(session_factory) -> None:
    """Nothing in the cache: the job goes through the bar source."""
    exit_at = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)
    now = datetime(2026, 10, 13, 21, 0, tzinfo=UTC)
    bars = _FailingBars(FakeCalendarSource())
    clock = FakeClock(now)
    worker, _market_data, _ = _worker(session_factory, clock, bars=bars)
    with session_factory() as session:
        _closed_position(
            session, closed=exit_at.replace(tzinfo=None), symbol="ZZZ"
        )
        session.commit()
    report = await worker.fill(now)
    assert bars.calls == 1
    assert report.filled == 1
    with session_factory() as session:
        p = session.execute(select(Position)).scalar_one()
        assert p.after_exit_status == "done"
        assert p.after_exit_best_price is not None


async def test_job_three_failures_become_unavailable(session_factory) -> None:
    exit_at = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)
    bars = _FailingBars(FakeCalendarSource(), fail=99)
    clock = FakeClock(datetime(2026, 10, 13, 21, 0, tzinfo=UTC))
    worker, _market_data, _ = _worker(session_factory, clock, bars=bars)
    with session_factory() as session:
        _closed_position(
            session, closed=exit_at.replace(tzinfo=None), symbol="ZZZ"
        )
        session.commit()
    for day in (13, 14, 15):
        now = datetime(2026, 10, day, 21, 0, tzinfo=UTC)
        report = await worker.fill(now)
        if day < 15:
            assert report.failed == 1
    assert report.unavailable == 1
    with session_factory() as session:
        p = session.execute(select(Position)).scalar_one()
        assert p.after_exit_status == "unavailable"


async def test_split_during_window_adjusts_bars_and_exit(session_factory) -> None:
    """A 2:1 split ex-date inside the window: cached pre-split bars and
    the exit price are halved before comparing."""
    exit_at = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)
    now = datetime(2026, 10, 13, 21, 0, tzinfo=UTC)
    clock = FakeClock(now)
    worker, market_data, _ = _worker(session_factory, clock)
    with session_factory() as session:
        p = _closed_position(session, closed=exit_at.replace(tzinfo=None))
        session.add(
            CorporateAction(
                symbol="AAA",
                action_type="split",
                date=datetime(2026, 10, 9),
                ratio_or_amount=Decimal("2"),
            )
        )
        session.commit()
        pid = p.id
    await market_data.refresh_bars(datetime(2026, 10, 5, tzinfo=UTC), now)
    report = await worker.fill(now)
    assert report.filled == 1
    with session_factory() as session:
        p = session.get(Position, pid)
        assert p.after_exit_status == "done"
        bars = session.execute(
            select(Bar5mRow).where(Bar5mRow.symbol == "AAA")
        ).scalars().all()
        by_start = {b.start: b for b in bars}
        # Expected: bars before Oct 9 divided by 2, later bars as stored.
        expected_highs = []
        for b in bars:
            day_ = b.start.astimezone(UTC).astimezone(NY_TZ).date()
            if b.start >= exit_at.replace(tzinfo=None):
                expected_highs.append(
                    b.high / 2 if day_ < date(2026, 10, 9) else b.high
                )
        assert p.after_exit_best_price == max(expected_highs)


def test_split_factor_after_helper() -> None:
    actions = [
        CorporateAction(
            symbol="AAA", action_type="split",
            date=datetime(2026, 10, 9), ratio_or_amount=Decimal("2"),
        ),
        CorporateAction(
            symbol="AAA", action_type="dividend",
            date=datetime(2026, 10, 8), ratio_or_amount=Decimal("0.5"),
        ),
    ]
    assert split_factor_after(actions, "AAA", date(2026, 10, 8)) == Decimal("2")
    assert split_factor_after(actions, "AAA", date(2026, 10, 9)) == Decimal("1")


# ----------------------------------------------------------------------
# API


def _api_seed() -> int:
    """One run with a done after-exit trade and a pending one."""
    with get_session_maker()() as session:
        for table in (Position, Fill, Order, Outcome, Decision, Cycle, Run):
            session.execute(delete(table))
        run = Run(
            started_at=datetime(2026, 10, 5, 13, 30),
            starting_equity=Decimal("10000"),
        )
        session.add(run)
        session.flush()
        session.add(
            Position(
                symbol="AAA",
                origin="agent",
                qty=10,
                avg_entry_price=Decimal("100"),
                initial_stop=Decimal("90"),
                opened_at=datetime(2026, 10, 5, 13, 30),
                closed_at=datetime(2026, 10, 6, 20, 0),
                realized_pnl_raw=Decimal("100"),
                realized_pnl_adjusted=Decimal("99"),
                exit_reason="target",
                after_exit_status="done",
                after_exit_best_price=Decimal("120"),
                after_exit_best_at=datetime(2026, 10, 8, 14, 0),
                after_exit_close_price=Decimal("115"),
                after_exit_done_at=datetime(2026, 10, 13, 21, 0),
            )
        )
        session.add(
            Position(
                symbol="BBB",
                origin="agent",
                qty=5,
                avg_entry_price=Decimal("200"),
                initial_stop=Decimal("190"),
                opened_at=datetime(2026, 10, 12, 13, 30),
                closed_at=datetime(2026, 10, 13, 20, 0),
                realized_pnl_raw=Decimal("-20"),
                realized_pnl_adjusted=Decimal("-21"),
                exit_reason="stop",
            )
        )
        session.commit()
        return run.id


def test_tails_endpoint(client: TestClient) -> None:
    run_id = _api_seed()
    body = client.get("/api/performance/tails").json()
    assert body["empty"] is False
    assert body["run"]["id"] == run_id
    assert body["trades"] == 2
    assert body["pending"] == 1
    assert body["min_trades"] == 20
    assert len(body["distribution"]) == 11
    rows = {r["symbol"]: r for r in body["rows"]}
    assert rows["AAA"]["result_r"] is not None
    assert rows["AAA"]["after_best_r"] is not None
    assert rows["BBB"]["after_exit_status"] == "pending"
    assert rows["BBB"]["after_best_r"] is None
    by = {g["reason"]: g for g in body["after_exit"]}
    assert "target" in by
    assert by["target"]["count"] == 1
    # AAA: result +0.99R... adjusted 99 / (10×10) = 0.99R → bucket [0.5,1).
    buckets = {
        (b["lower"], b["upper"]): b["count"] for b in body["distribution"]
    }
    assert buckets[("0.5", "1.0")] == 1
    # BBB: −21 / (10×5) = −0.42R → bucket [−0.5,0).
    assert buckets[("-0.5", "0.0")] == 1


def test_tails_endpoint_unknown_run(client: TestClient) -> None:
    _api_seed()
    assert client.get("/api/performance/tails?run=99999").status_code == 404
