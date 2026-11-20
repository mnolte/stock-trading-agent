"""Item 108 — dividends counted like for like.

Covers the corporate-action fetch/parse fixes, the stock-dividend
valuation in ``_dividends``, the shared SPY total-return benchmark and
the one-time repair that rewrites rows stored before stock dividends
were parsed.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from app.config import AppConfig
from app.data.alpaca import AlpacaDataClient, parse_corporate_actions
from app.data.corporate_repair import (
    REPAIR_EVENT,
    repair_corporate_actions_once,
)
from app.data.fake import (
    FakeBarSource,
    FakeCalendarSource,
    FakeCorporateActionSource,
    FakeEarningsSource,
    FakeFXSource,
    FakeNewsSource,
    FakeQuoteSource,
)
from app.data.models import CorporateActionEvent, DataResult
from app.data.service import MarketDataService
from app.db.models import (
    Bar1d,
    CorporateAction,
    EquitySnapshot,
    Event,
    Fill,
    Order,
    Position,
    Run,
)
from app.engine.benchmark import (
    spy_benchmark_with_dividends,
    spy_return,
)
from app.engine.trades import _dividends
from app.market.clock import UTC, FakeClock
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio


def _action(
    symbol: str,
    action_type: str,
    ex: date,
    amount: str | None,
) -> CorporateAction:
    return CorporateAction(
        symbol=symbol,
        action_type=action_type,
        date=datetime.combine(ex, datetime.min.time()),
        ratio_or_amount=Decimal(amount) if amount is not None else None,
    )


# ----------------------------------------------------------------------
# parse: cash and stock dividends on one ex-date are two rows


def test_parse_stock_and_cash_dividend_same_ex_date() -> None:
    raw = {
        "corporate_actions": {
            "cash_dividends": [
                {
                    "symbol": "AAPL",
                    "ex_date": "2026-11-10",
                    "rate": 0.25,
                }
            ],
            "stock_dividends": [
                {
                    "symbol": "AAPL",
                    "ex_date": "2026-11-10",
                    "rate": 0.05,
                }
            ],
        }
    }
    at = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
    actions = parse_corporate_actions(raw, at)
    types = sorted(a.action_type for a in actions)
    assert types == ["dividend", "stock_dividend"]
    stock = next(a for a in actions if a.action_type == "stock_dividend")
    assert stock.amount_or_ratio == Decimal("0.05")


# ----------------------------------------------------------------------
# fetch: limit=None pages inside one limiter call; truncation fails


async def test_fetch_corporate_actions_requests_all_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AlpacaDataClient("test-key", "test-secret")
    captured: dict[str, object] = {}

    async def fake_run_read(fn: object, *args: object, **kwargs: object):
        captured["request"] = args[0]
        return {
            "cash_dividends": [
                {"symbol": "AAPL", "ex_date": "2026-11-10", "rate": 0.25}
            ]
        }

    monkeypatch.setattr(client.limiter, "run_read", fake_run_read)
    result = await client.fetch_corporate_actions(
        ["AAPL"], date(2026, 11, 1), date(2026, 11, 30)
    )
    assert result.error is None
    request = captured["request"]
    assert request.limit is None  # type: ignore[union-attr]


async def test_fetch_corporate_actions_truncated_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response still carrying a page token is a failure, not a short
    list — refresh must not prune rows it never saw."""
    client = AlpacaDataClient("test-key", "test-secret")

    async def fake_run_read(fn: object, *args: object, **kwargs: object):
        return {
            "cash_dividends": [
                {"symbol": "AAPL", "ex_date": "2026-11-10", "rate": 0.25}
            ],
            "next_page_token": "still-more",
        }

    monkeypatch.setattr(client.limiter, "run_read", fake_run_read)
    result = await client.fetch_corporate_actions(
        ["AAPL"], date(2026, 11, 1), date(2026, 11, 30)
    )
    assert result.error is not None
    assert "corporate actions truncated" in result.error
    assert result.data == []


# ----------------------------------------------------------------------
# refresh: two pages of 600 store 1,200 rows


async def test_refresh_stores_two_pages_of_actions(
    session_factory: sessionmaker[Session],
) -> None:
    symbols = [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META",
        "JPM", "XOM",
    ]
    events: list[CorporateActionEvent] = []
    # 10 symbols × 2 types × 60 ex-dates = 1,200 unique rows — the shape
    # of the two SDK pages the fetch now merges.
    for symbol in symbols:
        for action_type in ("dividend", "stock_dividend"):
            for day in range(60):
                events.append(
                    CorporateActionEvent(
                        symbol=symbol,
                        action_type=action_type,
                        ex_date=date(2026, 3, 2) + timedelta(days=day),
                        amount_or_ratio=Decimal("0.10"),
                        fetched_at=datetime(2026, 10, 15, tzinfo=UTC),
                    )
                )
    calendar = FakeCalendarSource()
    sources = _sources(FakeCorporateActionSource(events))
    service = MarketDataService(
        sources, calendar, AppConfig(), session_factory=session_factory
    )
    result = await service.refresh_corporate_actions(
        date(2026, 1, 1), date(2026, 12, 31)
    )
    assert result.error is None
    assert result.data == 1200
    with session_factory() as session:
        count = len(
            session.execute(select(CorporateAction)).scalars().all()
        )
    assert count == 1200


def _sources(corporate_actions: FakeCorporateActionSource):
    from app.data.factory import DataSources

    calendar = FakeCalendarSource()
    return DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=corporate_actions,
        calendar=calendar,
        alpaca=None,
    )


# ----------------------------------------------------------------------
# _dividends entitlement and stock-dividend valuation


def _dividends_for(
    actions: list[CorporateAction],
    *,
    start: date,
    end: date,
    qty: int = 100,
    closes: dict[date, Decimal] | None = None,
) -> Decimal:
    close_map = closes or {}

    def lookup(_symbol: str, day: date) -> Decimal | None:
        # daily_close_before: the last stored bar strictly before ``day``.
        prior = [d for d in close_map if d < day]
        return close_map[max(prior)] if prior else None

    return _dividends(
        actions,
        "TEST",
        start,
        end,
        qty,
        close_before=lookup,
    )


def test_cash_dividend_entitlement_ex_date_inside_hold() -> None:
    div = _action("TEST", "dividend", date(2026, 11, 10), "0.50")
    # Bought the day before the ex-date: entitled.
    assert _dividends_for([div], start=date(2026, 11, 9), end=date(2026, 11, 20)) == Decimal("50")
    # Bought on the ex-date: not entitled (must hold the prior close).
    assert _dividends_for([div], start=date(2026, 11, 10), end=date(2026, 11, 20)) == Decimal("0")
    # Sold on the ex-date: still entitled.
    assert _dividends_for([div], start=date(2026, 11, 9), end=date(2026, 11, 10)) == Decimal("50")


def test_stock_dividend_valued_at_prior_close() -> None:
    """100 shares, 5% stock dividend, prior close 210 → $1,000."""
    stock = _action("TEST", "stock_dividend", date(2026, 11, 10), "0.05")
    total = _dividends_for(
        [stock],
        start=date(2026, 11, 9),
        end=date(2026, 11, 20),
        closes={date(2026, 11, 9): Decimal("210")},
    )
    assert total == Decimal("1000.00")


def test_stock_dividend_missing_close_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stock = _action("TEST", "stock_dividend", date(2026, 11, 10), "0.05")
    with caplog.at_level("WARNING"):
        total = _dividends_for(
            [stock], start=date(2026, 11, 9), end=date(2026, 11, 20)
        )
    assert total == Decimal("0")
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "TEST" in message
    assert "2026-11-10" in message


# ----------------------------------------------------------------------
# benchmark.spy_return


def _spy_dividend(session: Session, ex: date, amount: str) -> None:
    session.add(_action("SPY", "dividend", ex, amount))


def test_spy_return_no_dividends_is_price_only(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        pct = spy_return(
            session,
            date(2026, 10, 1),
            date(2026, 10, 31),
            Decimal("500"),
            Decimal("510"),
        )
    assert pct == Decimal("2")


def test_spy_return_counts_dividend_on_last_day(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _spy_dividend(session, date(2026, 10, 31), "1.00")
        session.commit()
        pct = spy_return(
            session,
            date(2026, 10, 1),
            date(2026, 10, 31),
            Decimal("500"),
            Decimal("510"),
        )
    # (510 + 1 − 500) / 500 = 2.2%
    assert pct == Decimal("2.2")


def test_spy_return_ignores_dividend_on_first_day(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _spy_dividend(session, date(2026, 10, 1), "1.00")
        session.commit()
        pct = spy_return(
            session,
            date(2026, 10, 1),
            date(2026, 10, 31),
            Decimal("500"),
            Decimal("510"),
        )
    assert pct == Decimal("2")


def test_spy_benchmark_with_dividends_grows_stored_value(
    session_factory: sessionmaker[Session],
) -> None:
    """stored + starting_equity × Σdivs ÷ run.spy_close."""
    with session_factory() as session:
        run = Run(
            started_at=datetime(2026, 10, 1, 13, 30),
            starting_equity=Decimal("10000"),
            spy_close=Decimal("500"),
        )
        session.add(run)
        session.flush()
        snap = EquitySnapshot(
            time=datetime(2026, 10, 20, 20, 0),
            equity=Decimal("10100"),
            cash=Decimal("9000"),
            spy_benchmark_value=Decimal("10200"),
        )
        session.add(snap)
        _spy_dividend(session, date(2026, 10, 15), "1.00")
        _spy_dividend(session, date(2026, 10, 25), "1.00")  # after snap day
        session.commit()
        got = spy_benchmark_with_dividends(session, run, snap)
    # Only the Oct 15 dividend is inside (run start, snap day]:
    # 10200 + 10000 × 1.00 / 500 = 10220
    assert got == Decimal("10220")


# ----------------------------------------------------------------------
# one-time repair


def _market_data(
    session_factory: sessionmaker[Session],
    actions: list[CorporateActionEvent],
) -> MarketDataService:
    calendar = FakeCalendarSource()
    sources = _sources(FakeCorporateActionSource(actions))
    return MarketDataService(
        sources, calendar, AppConfig(), session_factory=session_factory
    )


async def test_repair_rewrites_stock_dividend_and_runs_once(
    session_factory: sessionmaker[Session],
) -> None:
    clock = FakeClock(datetime(2026, 10, 15, 14, 0, tzinfo=UTC))
    with session_factory() as session:
        session.add(
            Run(
                started_at=datetime(2026, 10, 1, 13, 30),
                starting_equity=Decimal("10000"),
                spy_close=Decimal("500"),
            )
        )
        # The pre-108 bug: a stock dividend stored as a cash dividend.
        session.add(
            _action("AAPL", "dividend", date(2026, 10, 10), "0.05")
        )
        session.commit()

    # The fixed fetch now returns it as a stock_dividend.
    market_data = _market_data(
        session_factory,
        [
            CorporateActionEvent(
                symbol="AAPL",
                action_type="stock_dividend",
                ex_date=date(2026, 10, 10),
                amount_or_ratio=Decimal("0.05"),
                fetched_at=datetime(2026, 10, 15, tzinfo=UTC),
            )
        ],
    )
    counts = await repair_corporate_actions_once(
        session_factory, market_data, clock, AppConfig()
    )
    assert counts is not None
    with session_factory() as session:
        rows = session.execute(select(CorporateAction)).scalars().all()
        types = sorted(r.action_type for r in rows)
        assert types == ["stock_dividend"]
        flag = session.execute(
            select(Event).where(Event.event_type == REPAIR_EVENT)
        ).scalars().all()
        assert len(flag) == 1

    # Second call: the flag means it never runs again.
    again = await repair_corporate_actions_once(
        session_factory, market_data, clock, AppConfig()
    )
    assert again is None


async def test_repair_keeps_flag_unset_on_fetch_error(
    session_factory: sessionmaker[Session],
) -> None:
    """A failed fetch must not burn the one-time flag."""
    clock = FakeClock(datetime(2026, 10, 15, 14, 0, tzinfo=UTC))
    with session_factory() as session:
        session.add(
            Run(
                started_at=datetime(2026, 10, 1, 13, 30),
                starting_equity=Decimal("10000"),
                spy_close=Decimal("500"),
            )
        )
        session.commit()

    class FailingSource(FakeCorporateActionSource):
        async def fetch_corporate_actions(self, symbols, start, end):
            return DataResult(data=[], error="boom", source="fake")

    market_data = _market_data(session_factory, [])
    market_data.sources.corporate_actions = FailingSource()
    counts = await repair_corporate_actions_once(
        session_factory, market_data, clock, AppConfig()
    )
    assert counts == {"error": 1}
    with session_factory() as session:
        flag = session.execute(
            select(Event).where(Event.event_type == REPAIR_EVENT)
        ).scalars().all()
        assert flag == []


# ----------------------------------------------------------------------
# Bar1d close lookup used by stock-dividend valuation


def test_daily_close_before_reads_last_bar(
    session_factory: sessionmaker[Session],
) -> None:
    from app.engine.trades import daily_close_before

    with session_factory() as session:
        session.add_all(
            [
                Bar1d(
                    symbol="TEST",
                    date=date(2026, 11, 6),
                    open=Decimal("1"),
                    high=Decimal("1"),
                    low=Decimal("1"),
                    close=Decimal("205"),
                    volume=1,
                ),
                Bar1d(
                    symbol="TEST",
                    date=date(2026, 11, 9),
                    open=Decimal("1"),
                    high=Decimal("1"),
                    low=Decimal("1"),
                    close=Decimal("210"),
                    volume=1,
                ),
            ]
        )
        session.commit()
        assert daily_close_before(session, "TEST", date(2026, 11, 10)) == Decimal(
            "210"
        )
        assert daily_close_before(session, "TEST", date(2026, 11, 9)) == Decimal(
            "205"
        )


# ----------------------------------------------------------------------
# like-for-like agreement: Performance and backtest results on the same
# seeded run (Readiness and the Strategies screen read the same
# Performance summary / benchmark function, so they agree by
# construction).


async def test_performance_and_backtest_agree_on_dividends(
    session_factory: sessionmaker[Session],
) -> None:
    """One SPY dividend and one agent dividend move every surface the
    same way: adjusted return vs SPY total return, and the difference
    from the price-only figures is exactly the dividend share."""
    from app.backtests.results import summarise
    from app.engine.performance import compute_performance

    with session_factory() as session:
        run = Run(
            started_at=datetime(2026, 10, 1, 13, 30),
            starting_equity=Decimal("10000"),
            spy_close=Decimal("500"),
        )
        session.add(run)
        session.flush()
        # SPY closes: 500 on Oct 1, 505 on Oct 10, 510 on Oct 20.
        for d, close in (
            (date(2026, 10, 1), "500"),
            (date(2026, 10, 10), "505"),
            (date(2026, 10, 20), "510"),
        ):
            session.add(
                Bar1d(
                    symbol="SPY",
                    date=d,
                    open=Decimal(close),
                    high=Decimal(close),
                    low=Decimal(close),
                    close=Decimal(close),
                    volume=1,
                )
            )
        # Stored benchmark stays price-only: starting_equity × spy ÷
        # run.spy_close.
        for t, equity, spy_price in (
            (datetime(2026, 10, 1, 20, 0), "10000", "500"),
            (datetime(2026, 10, 20, 20, 0), "10100", "510"),
        ):
            session.add(
                EquitySnapshot(
                    time=t,
                    equity=equity,
                    cash="9000",
                    spy_benchmark_value=(
                        Decimal("10000") * Decimal(spy_price) / Decimal("500")
                    ),
                )
            )
        # SPY dividend inside the span; agent cash dividend inside a
        # closed position's hold.
        session.add(_action("SPY", "dividend", date(2026, 10, 15), "1.00"))
        session.add(_action("TEST", "dividend", date(2026, 10, 12), "0.50"))
        order = Order(
            alpaca_order_id="ord-1",
            symbol="TEST",
            side="buy",
            order_type="market",
            leg="entry",
            qty=10,
            status="filled",
            created_at=datetime(2026, 10, 5, 14, 30),
            filled_at=datetime(2026, 10, 5, 14, 30),
        )
        session.add(order)
        session.flush()
        session.add(
            Fill(
                order_id=order.id,
                qty=10,
                price=Decimal("100"),
                filled_at=datetime(2026, 10, 5, 14, 30),
            )
        )
        session.add(
            Position(
                symbol="TEST",
                origin="agent",
                qty=10,
                avg_entry_price=Decimal("100"),
                opened_at=datetime(2026, 10, 5, 14, 30),
                closed_at=datetime(2026, 10, 18, 18, 0),
                realized_pnl_raw=Decimal("50"),
                realized_pnl_adjusted=Decimal("49.50"),
                exit_reason="target",
            )
        )
        session.commit()

        clock = FakeClock(datetime(2026, 10, 21, 14, 0, tzinfo=UTC))
        perf = compute_performance(
            session,
            config=AppConfig(),
            market_data=None,
            calendar=FakeCalendarSource(),
            clock=clock,
        )
        summary = perf["summary"]  # type: ignore[index]
        curve = perf["curve"]["points"]  # type: ignore[index]

        bt = summarise(
            session_factory,
            config=AppConfig(),
            equity=Decimal("10000"),
            slippage_pct=Decimal("0.05"),
        )

    # SPY total return Oct 1 → Oct 20: (510 + 1 − 500) / 500 = 2.2%.
    # Both surfaces report it; the price-only figure would be 2.0% —
    # the difference is exactly the dividend's share.
    assert float(summary["spy_return_pct"]) == pytest.approx(2.2)  # type: ignore[index]
    assert bt["spy_return_pct"] == pytest.approx(2.2)
    assert float(summary["spy_return_pct"]) - 2.0 == pytest.approx(0.2)  # type: ignore[index]

    # The agent side: adjusted = raw − slippage + dividends.
    # return 100 − slip 0.5 (10×100×0.05%) + div 5 (10×0.50) = 104.5 → 1.045%.
    assert Decimal(summary["adjusted_usd"]) == Decimal("104.50")  # type: ignore[index]
    assert bt["return_pct"] == pytest.approx(1.045)
    assert float(summary["adjusted_pct"]) == pytest.approx(1.045, abs=0.01)  # type: ignore[index]
    # vs SPY uses the same adjusted return against the same total return.
    assert float(summary["vs_spy_pct"]) == pytest.approx(
        float(summary["adjusted_pct"]) - 2.2  # type: ignore[index]
    )
    assert bt["vs_spy_pts"] == pytest.approx(
        bt["return_pct"] - bt["spy_return_pct"]
    )

    # The curve's last point carries the dividend-aware benchmark:
    # stored 10200 + 10000 × 1.00 ÷ 500 = 10220, and an adjusted equity.
    last = curve[-1]
    assert Decimal(last["benchmark"]) == Decimal("10220")
    assert Decimal(last["adjusted"]) > Decimal(last["equity"]) - Decimal("1")
