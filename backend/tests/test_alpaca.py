from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alpaca.common.enums import BaseURL
from alpaca.data.requests import StockBarsRequest
from app.data.alpaca import (
    AlpacaDataClient,
    parse_bars,
    parse_calendar,
    parse_clock,
    parse_corporate_actions,
    parse_news,
    parse_quotes,
)
from app.market.clock import NY_TZ, UTC, FakeClock

pytestmark = pytest.mark.anyio


def test_trading_client_is_paper() -> None:
    client = AlpacaDataClient("test-key", "test-secret")
    assert client.trading._base_url == BaseURL.TRADING_PAPER


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


def test_parse_quotes_missing_both_uses_now() -> None:
    now = datetime(2026, 10, 15, 14, 0, 2, tzinfo=UTC)
    quotes = parse_quotes({}, {}, ["JPM"], now)
    assert quotes["JPM"].timestamp == now


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
    await client.fetch_5m_bars("AAPL", now - timedelta(hours=1), now)
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
    await client.fetch_5m_bars("AAPL", now - timedelta(hours=3), old_end)
    end = captured["request"].end
    assert end is not None
    assert end.replace(tzinfo=UTC) == old_end


async def test_sip_bar_request_empty_when_range_too_recent() -> None:
    # A range fully inside the last 16 minutes becomes empty without a call.
    now = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
    client = AlpacaDataClient("test-key", "test-secret", clock=FakeClock(now))

    def boom(request: StockBarsRequest) -> dict[str, Any]:
        raise AssertionError("should not be called")

    client.market_data.get_stock_bars = boom  # type: ignore[assignment]
    result = await client.fetch_5m_bars("AAPL", now - timedelta(minutes=5), now)
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
