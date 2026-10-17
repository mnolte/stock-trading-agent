"""Alpaca paper broker (docs/SPEC.md, Alpaca integration).

The trading client is always created with ``paper=True``; the paper
account check happens at startup in ``AlpacaDataClient``. Every write
goes through the shared rate limiter's ``run_write`` — order calls are
never retried. The trade-updates stream runs in its own thread (the SDK
spins a private event loop) and hands parsed updates to the engine.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass,
    OrderSide,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    ReplaceOrderRequest,
    StopLossRequest,
    StopOrderRequest,
    TakeProfitRequest,
)
from alpaca.trading.stream import TradingStream

from app.broker.base import TradeUpdateHandler
from app.broker.models import (
    BrokerAccount,
    BrokerAsset,
    BrokerOrder,
    BrokerPosition,
    TradeUpdateInfo,
)
from app.data.alpaca import _parse_timestamp
from app.data.models import DataResult
from app.data.rate_limiter import RateLimiter


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _ts(value: Any) -> datetime | None:
    if value is None:
        return None
    return _parse_timestamp(value)


def _raw(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    # alpaca-py model -> plain dict (we create clients with raw_data=True,
    # but stream callbacks may still deliver models depending on version).
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")  # type: ignore[no-any-return]
    return dict(vars(item))


def parse_order(item: Any) -> BrokerOrder:
    """Alpaca order dict -> BrokerOrder (legs parsed recursively)."""
    raw = _raw(item)
    legs = [parse_order(leg) for leg in raw.get("legs") or []]
    qty_raw = raw.get("qty")
    filled_qty_raw = raw.get("filled_qty")
    return BrokerOrder(
        id=str(raw["id"]),
        client_order_id=(
            str(raw["client_order_id"]) if raw.get("client_order_id") else None
        ),
        symbol=str(raw.get("symbol", "")),
        side=str(raw.get("side", "")),
        order_type=str(raw.get("type", raw.get("order_type", ""))),
        order_class=str(raw.get("order_class", "") or ""),
        status=str(raw.get("status", "")),
        qty=int(qty_raw) if qty_raw is not None else 0,
        filled_qty=int(Decimal(str(filled_qty_raw))) if filled_qty_raw else 0,
        filled_avg_price=_dec(raw.get("filled_avg_price")),
        limit_price=_dec(raw.get("limit_price")),
        stop_price=_dec(raw.get("stop_price")),
        created_at=_ts(raw.get("created_at")),
        updated_at=_ts(raw.get("updated_at")),
        filled_at=_ts(raw.get("filled_at")),
        legs=legs,
    )


def parse_position(item: Any) -> BrokerPosition:
    raw = _raw(item)
    qty_raw = raw.get("qty")
    return BrokerPosition(
        symbol=str(raw.get("symbol", "")),
        qty=int(Decimal(str(qty_raw))) if qty_raw is not None else 0,
        avg_entry_price=Decimal(str(raw.get("avg_entry_price", "0"))),
        current_price=_dec(raw.get("current_price")),
        market_value=_dec(raw.get("market_value")),
    )


def parse_account(item: Any) -> BrokerAccount:
    raw = _raw(item)
    return BrokerAccount(
        account_number=str(raw.get("account_number", "")),
        equity=Decimal(str(raw.get("equity", "0"))),
        cash=Decimal(str(raw.get("cash", "0"))),
        last_equity=Decimal(str(raw.get("last_equity", "0"))),
    )


def parse_trade_update(item: Any) -> TradeUpdateInfo:
    raw = _raw(item)
    return TradeUpdateInfo(
        event=str(raw.get("event", "")),
        order=parse_order(raw.get("order", {})),
        qty=int(Decimal(str(raw["qty"]))) if raw.get("qty") is not None else None,
        price=_dec(raw.get("price")),
        timestamp=_ts(raw.get("timestamp")),
        position_qty=(
            int(Decimal(str(raw["position_qty"])))
            if raw.get("position_qty") is not None
            else None
        ),
    )


class AlpacaPaperBroker:
    """Trading calls against the Alpaca paper account. Never retries writes."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.limiter = rate_limiter or RateLimiter()
        self.trading = TradingClient(api_key, secret_key, paper=True, raw_data=True)
        self._stream = TradingStream(api_key, secret_key, paper=True, raw_data=True)
        self._handlers: list[TradeUpdateHandler] = []
        self._stream_started = False

    # ------------------------------------------------------------------
    # reads (retried with backoff inside run_read)

    async def get_account(self) -> DataResult[BrokerAccount]:
        try:
            raw = await self.limiter.run_read(self.trading.get_account)
            return DataResult(data=parse_account(raw), source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(
                data=None, error=str(exc), source="alpaca"  # type: ignore[arg-type]
            )

    async def get_positions(self) -> DataResult[list[BrokerPosition]]:
        try:
            raw = await self.limiter.run_read(self.trading.get_all_positions)
            rows = [parse_position(p) for p in (list(raw) if raw else [])]
            return DataResult(data=rows, source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=[], error=str(exc), source="alpaca")

    async def get_open_orders(self) -> DataResult[list[BrokerOrder]]:
        try:
            raw = await self.limiter.run_read(
                self.trading.get_orders,
                GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True),
            )
            orders = [parse_order(o) for o in (list(raw) if raw else [])]
            return DataResult(data=orders, source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=[], error=str(exc), source="alpaca")

    async def get_order(self, order_id: str) -> DataResult[BrokerOrder | None]:
        try:
            raw = await self.limiter.run_read(self.trading.get_order_by_id, order_id)
            return DataResult(data=parse_order(raw), source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=None, error=str(exc), source="alpaca")

    async def get_order_by_client_id(
        self, client_order_id: str
    ) -> DataResult[BrokerOrder | None]:
        try:
            raw = await self.limiter.run_read(
                self.trading.get_order_by_client_id, client_order_id
            )
            return DataResult(data=parse_order(raw), source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=None, error=str(exc), source="alpaca")

    # ------------------------------------------------------------------
    # writes (one attempt each; reconciliation recovers after a failure)

    async def submit_bracket_entry(
        self,
        *,
        symbol: str,
        qty: int,
        limit_price: Decimal,
        stop_price: Decimal,
        target_price: Decimal,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        request = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            type="limit",
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            limit_price=float(limit_price),
            take_profit=TakeProfitRequest(limit_price=float(target_price)),
            stop_loss=StopLossRequest(stop_price=float(stop_price)),
            client_order_id=client_order_id,
        )
        try:
            raw = await self.limiter.run_write(self.trading.submit_order, request)
            return DataResult(data=parse_order(raw), source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(
                data=None, error=str(exc), source="alpaca"  # type: ignore[arg-type]
            )

    async def submit_stop_order(
        self,
        *,
        symbol: str,
        qty: int,
        stop_price: Decimal,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        request = StopOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            type="stop",
            time_in_force=TimeInForce.GTC,
            stop_price=float(stop_price),
            client_order_id=client_order_id,
        )
        try:
            raw = await self.limiter.run_write(self.trading.submit_order, request)
            return DataResult(data=parse_order(raw), source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(
                data=None, error=str(exc), source="alpaca"  # type: ignore[arg-type]
            )

    async def submit_market_sell(
        self,
        *,
        symbol: str,
        qty: int,
        market_on_open: bool,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            type="market",
            # Outside regular hours the sell is queued as a market-on-open
            # order (spec: stop & flatten queues sells as MOO).
            time_in_force=TimeInForce.OPG if market_on_open else TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        try:
            raw = await self.limiter.run_write(self.trading.submit_order, request)
            return DataResult(data=parse_order(raw), source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(
                data=None, error=str(exc), source="alpaca"  # type: ignore[arg-type]
            )

    async def replace_stop(
        self, order_id: str, *, stop_price: Decimal
    ) -> DataResult[BrokerOrder]:
        request = ReplaceOrderRequest(stop_price=float(stop_price))
        try:
            raw = await self.limiter.run_write(
                self.trading.replace_order_by_id, order_id, request
            )
            return DataResult(data=parse_order(raw), source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(
                data=None, error=str(exc), source="alpaca"  # type: ignore[arg-type]
            )

    async def cancel_order(self, order_id: str) -> DataResult[None]:
        try:
            await self.limiter.run_write(self.trading.cancel_order_by_id, order_id)
            return DataResult(data=None, source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=None, error=str(exc), source="alpaca")

    async def cancel_all_orders(self) -> DataResult[int]:
        try:
            raw = await self.limiter.run_write(self.trading.cancel_orders)
            cancelled = len(list(raw)) if raw else 0
            return DataResult(data=cancelled, source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=0, error=str(exc), source="alpaca")

    async def close_all_positions(
        self, *, cancel_orders: bool = True
    ) -> DataResult[list[BrokerOrder] | None]:
        try:
            raw = await self.limiter.run_write(
                self.trading.close_all_positions, cancel_orders
            )
            orders = [parse_order(o) for o in (list(raw) if raw else [])]
            return DataResult(data=orders, source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=None, error=str(exc), source="alpaca")

    async def get_asset(self, symbol: str) -> DataResult[BrokerAsset | None]:
        try:
            raw = await self.limiter.run_read(self.trading.get_asset, symbol)
            item = _raw(raw)
            asset = BrokerAsset(
                symbol=str(item.get("symbol", symbol)),
                status=str(item.get("status", "")),
                tradable=bool(item.get("tradable", False)),
                asset_class=str(
                    # Raw JSON calls the field "class"; the SDK model
                    # renames it "asset_class" (alias of class).
                    item.get("class") or item.get("asset_class") or ""
                ),
                exchange=(
                    str(item.get("exchange")) if item.get("exchange") else None
                ),
                shortable=bool(item.get("shortable", False)),
            )
            return DataResult(data=asset, source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=None, error=str(exc), source="alpaca")

    # ------------------------------------------------------------------
    # trade-updates stream

    def stream_connected(self) -> bool:
        return self._stream_started

    def subscribe_trade_updates(self, handler: TradeUpdateHandler) -> None:
        self._handlers.append(handler)

    async def _on_trade_update(self, item: Any) -> None:
        update = parse_trade_update(item)
        for handler in self._handlers:
            await handler(update)

    async def start_stream(self) -> None:
        """Run the SDK websocket in its own thread (it owns an event loop)."""
        if self._stream_started:
            return
        self._stream_started = True
        self._stream.subscribe_trade_updates(self._on_trade_update)
        await asyncio.to_thread(self._run_stream_blocking)

    def _run_stream_blocking(self) -> None:
        # TradingStream.run() calls asyncio.run(); it must live on a
        # dedicated thread, not the API event loop.
        import threading

        thread = threading.Thread(
            target=self._stream.run, name="alpaca-trade-stream", daemon=True
        )
        thread.start()

    async def stop_stream(self) -> None:
        if not self._stream_started:
            return
        self._stream_started = False
        await asyncio.to_thread(self._stream.stop)


__all__ = [
    "AlpacaPaperBroker",
    "parse_account",
    "parse_order",
    "parse_position",
    "parse_trade_update",
]
