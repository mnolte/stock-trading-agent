"""Broker-facing value types (docs/SPEC.md, Alpaca integration).

Everything crossing the broker boundary is a plain dataclass here so the
Alpaca client, the fake and reconciliation share one shape. Money and
prices are Decimal; times are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

# Alpaca order statuses that mean the order is still live at the broker.
OPEN_ORDER_STATUSES = frozenset(
    {
        "new",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "partially_filled",
        "held",
        "calculated",
        "pending_cancel",
        "pending_replace",
        "pending_review",
    }
)

# Statuses where the order can no longer fill.
CLOSED_ORDER_STATUSES = frozenset(
    {"filled", "canceled", "expired", "replaced", "rejected", "stopped", "suspended"}
)


@dataclass(frozen=True)
class BrokerAccount:
    """The Alpaca account state relevant to risk gates."""

    account_number: str
    equity: Decimal
    cash: Decimal
    last_equity: Decimal  # equity at the previous session close


@dataclass(frozen=True)
class BrokerPosition:
    """One open position at the broker."""

    symbol: str
    qty: int
    avg_entry_price: Decimal
    current_price: Decimal | None
    market_value: Decimal | None


@dataclass(frozen=True)
class BrokerOrder:
    """One order (parent or leg) at the broker."""

    id: str
    client_order_id: str | None
    symbol: str
    side: str  # buy | sell
    order_type: str  # limit | market | stop | stop_limit | ...
    order_class: str  # simple | bracket | oco | oto
    status: str
    qty: int
    filled_qty: int
    filled_avg_price: Decimal | None
    limit_price: Decimal | None
    stop_price: Decimal | None
    created_at: datetime | None
    updated_at: datetime | None
    filled_at: datetime | None
    legs: list[BrokerOrder] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_ORDER_STATUSES


@dataclass(frozen=True)
class TradeUpdateInfo:
    """One trade-updates stream event (fill, cancel, reject, ...)."""

    event: str  # fill | partial_fill | canceled | rejected | expired | replaced | ...
    order: BrokerOrder
    qty: int | None
    price: Decimal | None
    timestamp: datetime | None
    position_qty: int | None
