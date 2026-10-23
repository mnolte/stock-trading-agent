import asyncio
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from alpaca.common.enums import BaseURL
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from app.data.alpaca import (
    AlpacaDataClient,
    parse_bars,
    parse_calendar,
    parse_clock,
    parse_corporate_actions,
    parse_daily_bars,
    parse_news,
    parse_quotes,
)
from app.market.clock import NY_TZ, UTC, FakeClock

pytestmark = pytest.mark.anyio


async def test_news_more_than_fifty_stories_keeps_per_symbol_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AlpacaDataClient("test-key", "test-secret")
    stories = [
        {"id": str(i), "headline": str(i), "source": "fake", "symbols": ["AAPL"],
         "created_at": "2026-10-22T14:00:00Z"} for i in range(60)
    ] + [{"id": "msft", "headline": "MSFT", "source": "fake", "symbols": ["MSFT"],
          "created_at": "2026-10-22T14:00:00Z"}]

    def page(path: str, data: dict[str, Any]) -> dict[str, Any]:
        rows = [s for s in stories if data["symbols"] in s["symbols"]]
        offset = int(data.get("page_token") or 0)
        return {"news": rows[offset:offset + 50],
                "next_page_token": str(offset + 50) if len(rows) > offset + 50 else None}

    monkeypatch.setattr(client.news, "get", page)
    monkeypatch.setattr(client.news, "get_news", lambda request: {"news": stories[:50]})
    at = datetime(2026, 10, 22, 15, tzinfo=UTC)
    result = await client.fetch_news(["AAPL", "MSFT", "JPM"], at - timedelta(hours=24), at)
    assert len(result.data) == 61
    assert result.statuses == {"AAPL": "complete", "MSFT": "complete", "JPM": "empty"}


async def test_news_status_distinguishes_failed_and_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AlpacaDataClient("test-key", "test-secret")
    story = {
        "id": "one", "headline": "One", "source": "fake",
        "symbols": ["TRUNC"], "created_at": "2026-10-22T14:00:00Z",
    }

    def page(path: str, data: dict[str, Any]) -> dict[str, Any]:
        symbol = str(data["symbols"])
        if symbol == "FAIL":
            raise RuntimeError("news unavailable")
        if symbol == "TRUNC":
            return {"news": [story], "next_page_token": "repeat"}
        return {"news": [], "next_page_token": None}

    monkeypatch.setattr(client.news, "get", page)
    at = datetime(2026, 10, 22, 15, tzinfo=UTC)
    result = await client.fetch_news(
        ["FAIL", "TRUNC", "DONE"], at - timedelta(hours=24), at
    )
    assert result.statuses == {
        "FAIL": "failed", "TRUNC": "truncated", "DONE": "empty",
    }


def test_trading_client_is_paper() -> None:
    client = AlpacaDataClient("test-key", "test-secret")
    assert client.trading._base_url == BaseURL.TRADING_PAPER


def test_parse_order_tolerates_fractional_qty_strings() -> None:
    """Alpaca reports qty as a string and can return fractional values
    on orders outside our whole-share flow — the parser must read them
    through Decimal instead of crashing on ``int("4.5")``."""
    from app.broker.alpaca import parse_order

    order = parse_order(
        {
            "id": "o-1",
            "client_order_id": "sa-x",
            "symbol": "MSFT",
            "side": "sell",
            "type": "market",
            "order_class": "",
            "status": "filled",
            "qty": "4.5",
            "filled_qty": "4.5",
        }
    )
    assert order.qty == 4
    assert order.filled_qty == 4


async def test_get_asset_reads_raw_class_field() -> None:
    """Alpaca's raw JSON calls the field "class", not "asset_class"
    (raw_data=True on the trading client)."""
    from app.broker.alpaca import AlpacaPaperBroker

    broker = AlpacaPaperBroker("test-key", "test-secret")

    def fake_asset(symbol_or_asset_id: str | UUID) -> dict[str, Any]:
        return {
            "symbol": str(symbol_or_asset_id),
            "class": "us_equity",
            "status": "active",
            "tradable": True,
            "exchange": "NASDAQ",
            "shortable": True,
        }

    broker.trading.get_asset = fake_asset  # type: ignore[method-assign]
    result = await broker.get_asset("TSLA")
    assert result.error is None
    assert result.data is not None
    assert result.data.asset_class == "us_equity"
    assert result.data.tradable is True


async def test_open_orders_recovers_held_leg_under_closed_parent() -> None:
    """Alpaca's open filter never returns `held` orders — a bracket's
    working stop leg stays `held` under its filled parent. The closed
    list carries the parent with its legs nested; the live leg is
    recovered from there."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    from app.broker.alpaca import AlpacaPaperBroker

    broker = AlpacaPaperBroker("test-key", "test-secret")
    open_raw = [
        {  # the released take-profit leg, returned top-level
            "id": "leg-target",
            "symbol": "WMT",
            "side": "sell",
            "type": "limit",
            "order_class": "bracket",
            "status": "new",
            "qty": "16",
            "limit_price": "123",
        }
    ]
    closed_raw = [
        {  # the filled parent still carries both legs
            "id": "parent-entry",
            "symbol": "WMT",
            "side": "buy",
            "type": "limit",
            "order_class": "bracket",
            "status": "filled",
            "qty": "16",
            "filled_qty": "16",
            "filled_avg_price": "120.1",
            "legs": [
                {
                    "id": "leg-target",
                    "symbol": "WMT",
                    "side": "sell",
                    "type": "limit",
                    "status": "new",
                    "qty": "16",
                    "limit_price": "123",
                },
                {
                    "id": "leg-stop",
                    "symbol": "WMT",
                    "side": "sell",
                    "type": "stop",
                    "status": "held",
                    "qty": "16",
                    "stop_price": "118.31",
                },
            ],
        }
    ]

    def fake_get_orders(
        request: GetOrdersRequest | None = None,
    ) -> Any:  # raw_data=True returns plain dicts
        status = request.status if request is not None else None
        return open_raw if status == QueryOrderStatus.OPEN else closed_raw

    broker.trading.get_orders = fake_get_orders  # type: ignore[assignment]
    result = await broker.get_open_orders()
    assert result.error is None
    by_id = {o.id: o for o in result.data}
    assert set(by_id) == {"leg-target", "leg-stop"}
    assert by_id["leg-stop"].status == "held"
    assert by_id["leg-stop"].is_open


async def test_trade_update_handlers_run_on_the_app_loop() -> None:
    """The SDK stream owns a private loop on its thread; handlers touch
    the DB, the reconcile lock and the update bus, so they must be
    scheduled on the application loop, not awaited on the stream loop."""
    from app.broker.alpaca import AlpacaPaperBroker
    from app.broker.models import TradeUpdateInfo

    broker = AlpacaPaperBroker("test-key", "test-secret")
    main_loop = asyncio.get_running_loop()
    broker._main_loop = main_loop  # set by start_stream in production

    done = asyncio.Event()
    seen: dict[str, object] = {}

    async def handler(update: TradeUpdateInfo) -> None:
        seen["loop"] = asyncio.get_running_loop()
        seen["symbol"] = update.order.symbol
        done.set()

    broker.subscribe_trade_updates(handler)

    raw = {
        "event": "fill",
        "order": {
            "id": "o1",
            "symbol": "MSFT",
            "side": "buy",
            "type": "limit",
            "status": "filled",
            "qty": "4",
            "filled_qty": "4",
            "filled_avg_price": "420.10",
        },
        "qty": "4",
        "price": "420.10",
        "timestamp": "2026-10-15T17:00:00Z",
        "position_qty": "4",
    }

    # TradingStream invokes the subscribed coroutine on its own thread's
    # private event loop — reproduce that exactly.
    thread = threading.Thread(
        target=lambda: asyncio.run(broker._on_trade_update(raw)),
        name="fake-stream",
        daemon=True,
    )
    thread.start()
    assert await asyncio.wait_for(done.wait(), timeout=5)
    thread.join()

    # The handler ran on the app loop — the foreign thread's loop is gone.
    assert seen["loop"] is main_loop
    assert seen["symbol"] == "MSFT"


async def test_trade_update_without_main_loop_is_dropped() -> None:
    """Before start_stream (or after the app loop closes) updates are
    dropped rather than run on the wrong loop."""
    from app.broker.alpaca import AlpacaPaperBroker

    broker = AlpacaPaperBroker("test-key", "test-secret")
    called: list[object] = []

    async def handler(update: object) -> None:
        called.append(update)

    broker.subscribe_trade_updates(handler)
    await broker._on_trade_update({"event": "fill", "order": {}})
    assert called == []


async def test_check_paper_account_accepts_pa_account() -> None:
    client = AlpacaDataClient("test-key", "test-secret")

    def fake_account() -> dict[str, Any]:
        return {"account_number": "PA3TEST123", "status": "ACTIVE"}

    client.trading.get_account = fake_account  # type: ignore[method-assign]
    result = await client.check_paper_account()
    assert result.error is None
    assert result.data["account_number"] == "PA3TEST123"


async def test_check_paper_account_rejects_live_account() -> None:
    client = AlpacaDataClient("test-key", "test-secret")

    def fake_account() -> dict[str, Any]:
        return {"account_number": "123456789", "status": "ACTIVE"}

    client.trading.get_account = fake_account  # type: ignore[method-assign]
    result = await client.check_paper_account()
    assert result.error is not None
    assert "not a paper account" in result.error


async def test_check_paper_account_surfaces_errors() -> None:
    client = AlpacaDataClient("test-key", "test-secret")

    def boom() -> dict[str, Any]:
        raise ConnectionError("unreachable")

    client.trading.get_account = boom  # type: ignore[method-assign]
    result = await client.check_paper_account()
    assert result.error == "unreachable"


def test_parse_calendar_regular_and_early_close() -> None:
    raw = [
        {"date": "2026-10-15", "open": "09:30", "close": "16:00"},
        {"date": "2026-11-27", "open": "09:30", "close": "13:00"},
    ]
    days = parse_calendar(raw)
    assert days[0].open == datetime(2026, 10, 15, 9, 30, tzinfo=NY_TZ)
    assert days[0].close == datetime(2026, 10, 15, 16, 0, tzinfo=NY_TZ)
    assert days[1].close == datetime(2026, 11, 27, 13, 0, tzinfo=NY_TZ)


def test_parse_bars() -> None:
    raw = {
        "AAPL": [
            {
                "t": "2026-10-15T13:30:00Z",
                "o": 220.10,
                "h": 220.55,
                "l": 219.90,
                "c": 220.40,
                "v": 12345,
                "n": 200,
                "vw": 220.30,
            }
        ]
    }
    bars = parse_bars(raw, "AAPL")
    assert len(bars) == 1
    bar = bars[0]
    assert bar.start == datetime(2026, 10, 15, 13, 30, tzinfo=UTC)
    assert bar.open == Decimal("220.1")
    assert bar.volume == 12345
    assert bar.feed == "sip"


def test_parse_quotes_combines_quote_and_trade() -> None:
    raw_quotes = {"AAPL": {"t": "2026-10-15T14:00:00Z", "bp": 220.10, "ap": 220.12}}
    raw_trades = {"AAPL": {"t": "2026-10-15T14:00:01Z", "p": 220.11}}
    now = datetime(2026, 10, 15, 14, 0, 2, tzinfo=UTC)
    quotes = parse_quotes(raw_quotes, raw_trades, ["AAPL"], now)
    quote = quotes["AAPL"]
    assert quote.bid == Decimal("220.1")
    assert quote.ask == Decimal("220.12")
    assert quote.last == Decimal("220.11")
    assert quote.feed == "iex"


def test_parse_quotes_missing_trade_uses_quote_time() -> None:
    raw_quotes = {"MSFT": {"t": "2026-10-15T14:00:00Z", "bp": 400.0, "ap": 400.05}}
    now = datetime(2026, 10, 15, 14, 0, 2, tzinfo=UTC)
    quotes = parse_quotes(raw_quotes, {}, ["MSFT"], now)
    quote = quotes["MSFT"]
    assert quote.last == Decimal("0")
    assert quote.timestamp == datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


def test_parse_quotes_missing_both_has_no_market_timestamp() -> None:
    now = datetime(2026, 10, 15, 14, 0, 2, tzinfo=UTC)
    quotes = parse_quotes({}, {}, ["JPM"], now)
    assert quotes["JPM"].timestamp is None


def test_parse_quotes_recent_trade_does_not_refresh_two_hour_old_quote() -> None:
    now = datetime(2026, 10, 22, 15, 46, tzinfo=UTC)
    quotes = parse_quotes(
        {"COP": {"t": "2026-10-22T13:46:00Z", "bp": "149.55", "ap": "149.57"}},
        {"COP": {"t": "2026-10-22T15:46:00Z", "p": "144.34"}},
        ["COP"],
        now,
    )
    quote = quotes["COP"]
    assert quote.timestamp == datetime(2026, 10, 22, 13, 46, tzinfo=UTC)
    assert quote.trade_timestamp == now
    assert quote.fetched_at == now


def test_parse_news_maps_symbols() -> None:
    raw = {
        "news": [
            {
                "id": 123,
                "headline": "Headline",
                "source": "benzinga",
                "created_at": "2026-10-15T13:00:00Z",
                "symbols": ["AAPL", "MSFT"],
            }
        ]
    }
    now = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
    items = parse_news(raw, ["AAPL", "MSFT", "JPM"], now)
    assert len(items) == 2
    assert {i.symbol for i in items} == {"AAPL", "MSFT"}


def test_parse_news_no_items() -> None:
    now = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
    assert parse_news({"news": []}, ["AAPL"], now) == []


def test_parse_corporate_actions_dividend_and_split() -> None:
    raw = {
        "corporate_actions": {
            "cash_dividends": [
                {
                    "symbol": "XOM",
                    "ex_date": "2026-11-12",
                    "payable_date": "2026-12-10",
                    "rate": 1.03,
                }
            ],
            "forward_splits": [
                {
                    "symbol": "NVDA",
                    "ex_date": "2026-11-20",
                    "ratio": 4.0,
                }
            ],
            "name_changes": [{"symbol": "META", "ex_date": "2026-11-01"}],
        }
    }
    at = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
    actions = parse_corporate_actions(raw, at)
    assert len(actions) == 2
    dividend = next(a for a in actions if a.action_type == "dividend")
    split = next(a for a in actions if a.action_type == "split")
    assert dividend.symbol == "XOM"
    assert dividend.amount_or_ratio == Decimal("1.03")
    assert split.symbol == "NVDA"
    assert split.amount_or_ratio == Decimal("4.0")


async def test_sip_bar_request_ends_at_least_16_minutes_ago() -> None:
    # The free plan rejects recent SIP data: every request end must be at
    # least 16 minutes in the past, even when the caller asks for "now".
    now = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
    client = AlpacaDataClient("test-key", "test-secret", clock=FakeClock(now))
    captured: dict[str, StockBarsRequest] = {}

    def fake_get_stock_bars(request: StockBarsRequest) -> dict[str, Any]:
        captured["request"] = request
        return {"AAPL": []}

    client.market_data.get_stock_bars = fake_get_stock_bars  # type: ignore[assignment]
    await client.fetch_5m_bars(["AAPL"], now - timedelta(hours=1), now)
    # The SDK request model stores naive datetimes; compare in UTC.
    end = captured["request"].end
    assert end is not None
    assert end.replace(tzinfo=UTC) == now - AlpacaDataClient.SIP_MIN_AGE


async def test_sip_bar_request_keeps_older_end() -> None:
    # An end that is already old enough is sent unchanged.
    now = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
    client = AlpacaDataClient("test-key", "test-secret", clock=FakeClock(now))
    captured: dict[str, StockBarsRequest] = {}
    old_end = now - timedelta(hours=2)

    def fake_get_stock_bars(request: StockBarsRequest) -> dict[str, Any]:
        captured["request"] = request
        return {"AAPL": []}

    client.market_data.get_stock_bars = fake_get_stock_bars  # type: ignore[assignment]
    await client.fetch_5m_bars(["AAPL"], now - timedelta(hours=3), old_end)
    end = captured["request"].end
    assert end is not None
    assert end.replace(tzinfo=UTC) == old_end


async def test_5m_bar_request_is_split_adjusted() -> None:
    # Spec: every bar request uses adjustment=split.
    now = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
    client = AlpacaDataClient("test-key", "test-secret", clock=FakeClock(now))
    captured: dict[str, StockBarsRequest] = {}

    def fake_get_stock_bars(request: StockBarsRequest) -> dict[str, Any]:
        captured["request"] = request
        return {"AAPL": []}

    client.market_data.get_stock_bars = fake_get_stock_bars  # type: ignore[assignment]
    await client.fetch_5m_bars(["AAPL"], now - timedelta(hours=3), now)
    request = captured["request"]
    assert request.adjustment == Adjustment.SPLIT
    assert request.feed == DataFeed.SIP
    assert request.timeframe.amount == 5
    assert request.timeframe.unit == TimeFrameUnit.Minute


async def test_daily_bar_request_params() -> None:
    # Daily bars: 1Day timeframe, SIP feed, split adjustment, and the same
    # 16-minute SIP end cap as 5-minute requests.
    now = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
    client = AlpacaDataClient("test-key", "test-secret", clock=FakeClock(now))
    captured: dict[str, StockBarsRequest] = {}

    def fake_get_stock_bars(request: StockBarsRequest) -> dict[str, Any]:
        captured["request"] = request
        return {"AAPL": []}

    client.market_data.get_stock_bars = fake_get_stock_bars  # type: ignore[assignment]
    await client.fetch_daily_bars("AAPL", date(2025, 10, 1), date(2026, 10, 15))
    request = captured["request"]
    assert request.timeframe.amount == TimeFrame.Day.amount
    assert request.timeframe.unit == TimeFrame.Day.unit
    assert request.feed == DataFeed.SIP
    assert request.adjustment == Adjustment.SPLIT
    end = request.end
    assert end is not None
    assert end.replace(tzinfo=UTC) == now - AlpacaDataClient.SIP_MIN_AGE


def test_parse_daily_bars() -> None:
    raw = {
        "AAPL": [
            {"t": "2026-10-14T04:00:00Z", "o": 218.0, "h": 221.0,
             "l": 217.5, "c": 220.4, "v": 5_000_000},
            {"t": "2026-10-15T04:00:00Z", "o": 220.5, "h": 222.0,
             "l": 219.8, "c": 221.1, "v": 4_800_000},
        ]
    }
    bars = parse_daily_bars(raw, "AAPL")
    assert len(bars) == 2
    # Daily bar timestamps map to the New York session date.
    assert bars[0].date == date(2026, 10, 14)
    assert bars[1].date == date(2026, 10, 15)
    assert bars[0].close == Decimal("220.4")


async def test_sip_bar_request_empty_when_range_too_recent() -> None:
    # A range fully inside the last 16 minutes becomes empty without a call.
    now = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
    client = AlpacaDataClient("test-key", "test-secret", clock=FakeClock(now))

    def boom(request: StockBarsRequest) -> dict[str, Any]:
        raise AssertionError("should not be called")

    client.market_data.get_stock_bars = boom  # type: ignore[assignment]
    result = await client.fetch_5m_bars(["AAPL"], now - timedelta(minutes=5), now)
    assert result.error is None
    assert result.data == []


def test_parse_clock() -> None:
    raw = {
        "is_open": True,
        "timestamp": "2026-10-15T14:00:00Z",
        "next_open": "2026-10-16T13:30:00Z",
        "next_close": "2026-10-15T20:00:00Z",
    }
    clock = parse_clock(raw)
    assert clock.is_open is True
    assert clock.next_open == datetime(2026, 10, 16, 13, 30, tzinfo=UTC)
