"""The broker interface (docs/SPEC.md, Alpaca integration).

Every order call returns a ``DataResult`` and is never retried by the
caller or the broker: after a timeout or error, reconciliation decides
what actually happened at Alpaca. Reads may retry with backoff inside
the client.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.broker.models import (
    BrokerAccount,
    BrokerAsset,
    BrokerOrder,
    BrokerPosition,
    TradeUpdateInfo,
)
from app.data.models import DataResult

TradeUpdateHandler = Callable[[TradeUpdateInfo], Awaitable[None]]

# Called with a short reason when the stream delivers something the
# parser cannot read (docs/SPEC.md, Alpaca integration — Stream health).
StreamFailureHandler = Callable[[str], Awaitable[None]]


class Broker(Protocol):
    """Trading account operations. Implementations: AlpacaPaperBroker, FakeBroker."""

    async def get_account(self) -> DataResult[BrokerAccount]:
        """Current account equity, cash and previous-session equity."""
        ...

    async def get_positions(self) -> DataResult[list[BrokerPosition]]:
        """All open positions at the broker."""
        ...

    async def get_position(
        self, symbol: str
    ) -> DataResult[BrokerPosition | None]:
        """One open position by symbol; ``data`` is None when flat."""
        ...

    async def get_account_config(self) -> DataResult[dict[str, str | bool | None]]:
        """Account safety configuration (no_shorting, margin multiplier)."""
        ...

    async def set_account_config(
        self, **fields: object
    ) -> DataResult[dict[str, str | bool | None]]:
        """Set account configuration fields; returns the effective values."""
        ...

    async def get_open_orders(self) -> DataResult[list[BrokerOrder]]:
        """All orders still live at the broker (legs included)."""
        ...

    async def get_filled_orders(
        self, symbol: str, since: datetime
    ) -> DataResult[list[BrokerOrder]]:
        ...

    async def get_order(self, order_id: str) -> DataResult[BrokerOrder | None]:
        """One order by broker id; ``data`` is None when unknown."""
        ...

    async def get_order_by_client_id(
        self, client_order_id: str
    ) -> DataResult[BrokerOrder | None]:
        """One order by our client id; used to recover after a timed-out write."""
        ...

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
        """Bracket order: limit buy + stop-loss leg + take-profit leg, gtc."""
        ...

    async def submit_stop_order(
        self,
        *,
        symbol: str,
        qty: int,
        stop_price: Decimal,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        """Standalone gtc stop sell (protective stop for adopted positions)."""
        ...

    async def submit_oco_exit(
        self,
        *,
        symbol: str,
        qty: int,
        limit_price: Decimal,
        stop_price: Decimal,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        """OCO exit order: take-profit limit + stop-loss leg, gtc."""
        ...

    async def submit_market_sell(
        self,
        *,
        symbol: str,
        qty: int,
        market_on_open: bool,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        """Market sell now, or queued as market-on-open outside hours."""
        ...

    async def replace_stop(
        self, order_id: str, *, stop_price: Decimal
    ) -> DataResult[BrokerOrder]:
        """Move a stop leg in place; returns the replacement order."""
        ...

    async def cancel_order(self, order_id: str) -> DataResult[None]:
        """Cancel one open order."""
        ...

    async def cancel_all_orders(self) -> DataResult[int]:
        """Cancel every open order; returns how many were cancelled."""
        ...

    async def close_all_positions(
        self, *, cancel_orders: bool = True
    ) -> DataResult[list[BrokerOrder] | None]:
        """Liquidate every position (paper reset); optionally cancel orders."""
        ...

    async def get_asset(self, symbol: str) -> DataResult[BrokerAsset | None]:
        """Tradability info for one symbol; None when unknown at the broker."""
        ...

    def stream_connected(self) -> bool:
        """Whether the trade-updates stream is live (Settings → Connections)."""
        ...

    def subscribe_trade_updates(self, handler: TradeUpdateHandler) -> None:
        """Register a handler for fill/cancel/reject events."""
        ...

    def subscribe_stream_failures(self, handler: StreamFailureHandler) -> None:
        """Register a handler for unparseable stream messages."""
        ...

    async def start_stream(self) -> None:
        """Start the trade-updates stream (no-op for the fake)."""
        ...

    async def stop_stream(self) -> None:
        """Stop the trade-updates stream (no-op for the fake)."""
        ...
