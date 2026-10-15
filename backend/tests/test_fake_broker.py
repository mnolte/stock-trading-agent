"""FakeBroker: the deterministic in-memory broker behind tests and sim mode."""

from datetime import datetime
from decimal import Decimal

import pytest
from app.broker.fake import FakeBroker
from app.broker.models import TradeUpdateInfo
from app.market.clock import UTC, FakeClock

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)  # 13:00 New York


def _broker(cash: str = "10000") -> FakeBroker:
    return FakeBroker(FakeClock(NOW), cash=Decimal(cash), prices={"MSFT": Decimal("420")})


async def test_bracket_entry_creates_three_orders() -> None:
    broker = _broker()
    result = await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="sa-entry-1-MSFT-1",
    )
    assert result.error is None
    parent = result.data
    assert parent.order_class == "bracket"
    assert parent.side == "buy"
    assert parent.limit_price == Decimal("421")
    assert len(parent.legs) == 2
    stop = next(leg for leg in parent.legs if leg.order_type == "stop")
    target = next(leg for leg in parent.legs if leg.order_type == "limit")
    assert stop.stop_price == Decimal("410")
    assert target.limit_price == Decimal("435")
    # Open-order reads return parents only, with legs nested inside.
    open_orders = await broker.get_open_orders()
    assert [o.id for o in open_orders.data] == [parent.id]


async def test_limit_buy_fills_when_price_crosses() -> None:
    broker = _broker()
    submitted = await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="c1",
    )
    await broker.tick("MSFT", Decimal("421.50"))  # above the limit: no fill
    positions = await broker.get_positions()
    assert positions.data == []
    await broker.tick("MSFT", Decimal("421"))
    positions = await broker.get_positions()
    assert positions.data[0].qty == 4
    assert positions.data[0].avg_entry_price == Decimal("421")
    # The bracket legs went live when the parent filled.
    parent = await broker.get_order(submitted.data.id)
    assert parent.data is not None
    assert all(leg.status == "new" for leg in parent.data.legs)


async def test_stop_leg_fill_closes_position_and_cancels_target() -> None:
    broker = _broker()
    submitted = await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="c1",
    )
    await broker.tick("MSFT", Decimal("420"))
    await broker.tick("MSFT", Decimal("409"))
    positions = await broker.get_positions()
    assert positions.data == []
    parent = await broker.get_order(submitted.data.id)
    assert parent.data is not None
    legs = {leg.order_type: leg for leg in parent.data.legs}
    assert legs["stop"].status == "filled"
    assert legs["stop"].filled_avg_price == Decimal("410")
    assert legs["limit"].status == "canceled"


async def test_target_leg_fill_closes_position_and_cancels_stop() -> None:
    broker = _broker()
    submitted = await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="c1",
    )
    await broker.tick("MSFT", Decimal("420"))
    await broker.tick("MSFT", Decimal("436"))
    parent = await broker.get_order(submitted.data.id)
    assert parent.data is not None
    legs = {leg.order_type: leg for leg in parent.data.legs}
    assert legs["limit"].status == "filled"
    assert legs["limit"].filled_avg_price == Decimal("435")
    assert legs["stop"].status == "canceled"


async def test_market_sell_fills_immediately() -> None:
    broker = _broker()
    await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="c1",
    )
    await broker.tick("MSFT", Decimal("420"))
    sold = await broker.submit_market_sell(
        symbol="MSFT", qty=4, market_on_open=False, client_order_id="exit-1"
    )
    assert sold.error is None
    assert sold.data.status == "filled"
    assert sold.data.filled_avg_price == Decimal("420")
    assert (await broker.get_positions()).data == []


async def test_replace_stop_moves_only_the_price() -> None:
    broker = _broker()
    submitted = await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="c1",
    )
    await broker.tick("MSFT", Decimal("420"))
    parent = await broker.get_order(submitted.data.id)
    assert parent.data is not None
    stop_leg = next(leg for leg in parent.data.legs if leg.order_type == "stop")
    replaced = await broker.replace_stop(stop_leg.id, stop_price=Decimal("415"))
    assert replaced.error is None
    assert replaced.data.stop_price == Decimal("415")
    assert replaced.data.id != stop_leg.id
    old = await broker.get_order(stop_leg.id)
    assert old.data is not None
    assert old.data.status == "replaced"


async def test_cancel_order_marks_it_canceled() -> None:
    broker = _broker()
    submitted = await broker.submit_stop_order(
        symbol="MSFT", qty=4, stop_price=Decimal("410"), client_order_id="s1"
    )
    result = await broker.cancel_order(submitted.data.id)
    assert result.error is None
    order = await broker.get_order(submitted.data.id)
    assert order.data is not None
    assert order.data.status == "canceled"


async def test_cancel_all_orders_returns_count() -> None:
    broker = _broker()
    await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="c1",
    )
    await broker.submit_stop_order(
        symbol="AAPL", qty=2, stop_price=Decimal("200"), client_order_id="s1"
    )
    result = await broker.cancel_all_orders()
    assert result.error is None
    # Bracket parent + both held legs + the standalone stop.
    assert result.data == 4
    assert (await broker.get_open_orders()).data == []


async def test_write_failure_places_nothing() -> None:
    broker = _broker()
    broker.fail_writes = True
    result = await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="c1",
    )
    assert result.error is not None
    assert broker.submit_calls == 1
    assert broker.orders == {}


async def test_timed_out_write_is_recoverable_by_client_id() -> None:
    """The write "fails" after Alpaca accepted it: never resent, found by id."""
    broker = _broker()
    broker.place_then_fail = True
    result = await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="sa-entry-7-MSFT-3",
    )
    assert result.error is not None
    assert broker.submit_calls == 1  # one attempt, no resend
    recovered = await broker.get_order_by_client_id("sa-entry-7-MSFT-3")
    assert recovered.error is None
    assert recovered.data is not None
    assert recovered.data.qty == 4


async def test_trade_update_handler_sees_fills() -> None:
    broker = _broker()
    events: list[str] = []

    async def handler(update: TradeUpdateInfo) -> None:
        events.append(f"{update.event}:{update.order.symbol}")

    broker.subscribe_trade_updates(handler)
    await broker.submit_bracket_entry(
        symbol="MSFT",
        qty=4,
        limit_price=Decimal("421"),
        stop_price=Decimal("410"),
        target_price=Decimal("435"),
        client_order_id="c1",
    )
    await broker.tick("MSFT", Decimal("420"))
    assert events == ["fill:MSFT"]


async def test_read_failure_returns_error_not_exception() -> None:
    broker = _broker()
    broker.fail_reads = True
    account = await broker.get_account()
    assert account.error is not None
    assert not account.data
    positions = await broker.get_positions()
    assert positions.error is not None
    assert positions.data == []
