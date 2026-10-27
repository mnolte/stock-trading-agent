"""Fake broker for simulated mode and tests (no network, deterministic).

Orders sit in memory as ``new`` until ``tick`` moves a price through the
limit/stop/target: a limit buy fills at or below its limit, a stop sells
at or below its trigger, a target sells at or above its limit. Market
sells fill immediately at the current mark.

``fail_writes`` makes every write return an error without placing
anything; ``place_then_fail`` places the order but still reports an
error — the timeout-after-accept case that reconciliation must recover
without a resend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.broker.base import TradeUpdateHandler
from app.broker.models import (
    OPEN_ORDER_STATUSES,
    BrokerAccount,
    BrokerAsset,
    BrokerOrder,
    BrokerPosition,
    TradeUpdateInfo,
)
from app.data.models import DataResult
from app.market.clock import Clock


@dataclass
class _OrderRec:
    id: str
    client_order_id: str | None
    symbol: str
    side: str
    order_type: str
    order_class: str
    status: str
    qty: int
    filled_qty: int
    filled_avg_price: Decimal | None
    limit_price: Decimal | None
    stop_price: Decimal | None
    created_at: object
    updated_at: object
    filled_at: object
    legs: list[str] = field(default_factory=list)  # child order ids
    parent_id: str | None = None
    time_in_force: str = "gtc"


@dataclass
class _PosRec:
    qty: int
    avg_entry_price: Decimal


class FakeBroker:
    """Implements the Broker protocol in memory."""

    def __init__(
        self,
        clock: Clock,
        *,
        cash: Decimal = Decimal("10000"),
        prices: dict[str, Decimal] | None = None,
    ) -> None:
        self.clock = clock
        self.cash = cash
        self.start_cash = cash
        self.prices: dict[str, Decimal] = dict(prices or {})
        self.positions: dict[str, _PosRec] = {}
        self.reject_oco: set[str] = set()
        self.account_config: dict[str, str | bool | None] = {}
        self.orders: dict[str, _OrderRec] = {}
        self._seq = 0
        self._handlers: list[TradeUpdateHandler] = []
        # Test/simulation knobs.
        self.fail_writes = False
        self.place_then_fail = False
        self.fail_reads = False
        self.submit_calls = 0  # counts write attempts (never resent)
        # symbol -> shares that fill on the next buy tick (partial fill).
        self.partial_fill: dict[str, int] = {}
        # symbols whose market sells are rejected (e.g. broker PDT block).
        self.reject_sells: set[str] = set()
        # cancel_order reports success but the order stays open at the broker.
        self.cancel_stuck = False
        # get_asset reports these symbols as unknown to the broker.
        self.untradable: set[str] = set()
        # get_asset finds these but reports tradable=False / wrong class.
        self.non_tradable: set[str] = set()
        self._stream_started = False

    # ------------------------------------------------------------------
    # helpers

    def _now(self) -> datetime:
        return self.clock.now()

    def _next_id(self) -> str:
        self._seq += 1
        return f"fake-{self._seq:06d}"

    def _to_broker_order(self, rec: _OrderRec) -> BrokerOrder:
        return BrokerOrder(
            id=rec.id,
            client_order_id=rec.client_order_id,
            symbol=rec.symbol,
            side=rec.side,
            order_type=rec.order_type,
            order_class=rec.order_class,
            status=rec.status,
            qty=rec.qty,
            filled_qty=rec.filled_qty,
            filled_avg_price=rec.filled_avg_price,
            limit_price=rec.limit_price,
            stop_price=rec.stop_price,
            created_at=rec.created_at,  # type: ignore[arg-type]
            updated_at=rec.updated_at,  # type: ignore[arg-type]
            filled_at=rec.filled_at,  # type: ignore[arg-type]
            legs=[self._to_broker_order(self.orders[i]) for i in rec.legs],
        )

    def _equity(self) -> Decimal:
        value = self.cash
        for symbol, pos in self.positions.items():
            mark = self.prices.get(symbol, pos.avg_entry_price)
            value += mark * pos.qty
        return value

    def _duplicate_client_id(self, client_order_id: str) -> bool:
        # Alpaca enforces client_order_id uniqueness account-wide,
        # including on canceled/expired orders.
        return any(
            rec.client_order_id == client_order_id
            for rec in self.orders.values()
        )

    async def _emit(self, rec: _OrderRec, event: str) -> None:
        update = TradeUpdateInfo(
            event=event,
            order=self._to_broker_order(rec),
            qty=rec.filled_qty or None,
            price=rec.filled_avg_price,
            timestamp=rec.filled_at,  # type: ignore[arg-type]
            position_qty=(
                self.positions[rec.symbol].qty if rec.symbol in self.positions else 0
            ),
        )
        for handler in self._handlers:
            await handler(update)

    def _fill(self, rec: _OrderRec, price: Decimal) -> None:
        fill_qty = rec.qty
        if rec.side == "buy" and rec.symbol in self.partial_fill:
            fill_qty = min(rec.qty - rec.filled_qty, self.partial_fill[rec.symbol])
            if rec.filled_qty + fill_qty >= rec.qty:
                del self.partial_fill[rec.symbol]
        rec.filled_qty += fill_qty
        rec.filled_avg_price = price
        rec.filled_at = self._now()
        rec.updated_at = self._now()
        rec.status = "filled" if rec.filled_qty >= rec.qty else "partially_filled"
        if rec.side == "buy":
            pos = self.positions.get(rec.symbol)
            if pos is None:
                self.positions[rec.symbol] = _PosRec(fill_qty, price)
            else:
                total = pos.qty + fill_qty
                pos.avg_entry_price = (
                    pos.avg_entry_price * pos.qty + price * fill_qty
                ) / total
                pos.qty = total
            self.cash -= price * fill_qty
            # The bracket's protective legs go live once the parent fills;
            # they cover only the filled qty, like Alpaca's bracket legs.
            for leg_id in rec.legs:
                leg = self.orders[leg_id]
                if leg.status in ("held", "accepted"):
                    leg.status = "new"
                leg.qty = rec.filled_qty
        else:
            pos = self.positions.get(rec.symbol)
            if pos is not None:
                pos.qty -= rec.qty
                if pos.qty <= 0:
                    del self.positions[rec.symbol]
            self.cash += price * rec.qty
            # A fill of one leg cancels the bracket siblings.
            if rec.parent_id is not None:
                parent = self.orders.get(rec.parent_id)
                if parent is not None:
                    parent.status = "filled"
                    for leg_id in parent.legs:
                        leg = self.orders[leg_id]
                        if leg.id != rec.id and leg.status in OPEN_ORDER_STATUSES:
                            leg.status = "canceled"
                            leg.updated_at = self._now()

    # ------------------------------------------------------------------
    # reads

    async def get_account(self) -> DataResult[BrokerAccount]:
        if self.fail_reads:
            return DataResult(
                data=None,  # type: ignore[arg-type]
                error="fake read failure",
                source="fake",
            )
        return DataResult(
            data=BrokerAccount(
                account_number="PA-FAKE",
                equity=self._equity(),
                cash=self.cash,
                last_equity=self.start_cash,
            ),
            source="fake",
        )

    async def get_positions(self) -> DataResult[list[BrokerPosition]]:
        if self.fail_reads:
            return DataResult(data=[], error="fake read failure", source="fake")
        return DataResult(
            data=[
                BrokerPosition(
                    symbol=symbol,
                    qty=pos.qty,
                    avg_entry_price=pos.avg_entry_price,
                    current_price=self.prices.get(symbol),
                    market_value=(
                        self.prices.get(symbol, pos.avg_entry_price) * pos.qty
                    ),
                )
                for symbol, pos in self.positions.items()
            ],
            source="fake",
        )

    async def get_position(
        self, symbol: str
    ) -> DataResult[BrokerPosition | None]:
        pos = self.positions.get(symbol)
        if pos is None or self.fail_reads:
            return DataResult(
                data=None,
                error="fake read failure" if self.fail_reads else None,
                source="fake",
            )
        return DataResult(
            data=BrokerPosition(
                symbol=symbol,
                qty=pos.qty,
                avg_entry_price=pos.avg_entry_price,
                current_price=self.prices.get(symbol),
                market_value=self.prices.get(symbol, pos.avg_entry_price) * pos.qty,
            ),
            source="fake",
        )

    async def get_account_config(
        self,
    ) -> DataResult[dict[str, str | bool | None]]:
        return DataResult(data=dict(self.account_config), source="fake")

    async def set_account_config(
        self, **fields: object
    ) -> DataResult[dict[str, str | bool | None]]:
        self.account_config.update(
            {k: v for k, v in fields.items() if isinstance(v, (str, bool)) or v is None}
        )
        return DataResult(data=dict(self.account_config), source="fake")

    async def get_open_orders(self) -> DataResult[list[BrokerOrder]]:
        if self.fail_reads:
            return DataResult(data=[], error="fake read failure", source="fake")
        return DataResult(
            data=[
                self._to_broker_order(rec)
                for rec in self.orders.values()
                # Alpaca's open filter never returns `held` orders; a
                # bracket stop leg stays `held` under its filled parent
                # and surfaces only via get_order or a closed-orders query.
                if rec.status in OPEN_ORDER_STATUSES and rec.status != "held"
            ],
            source="fake",
        )

    async def get_order(self, order_id: str) -> DataResult[BrokerOrder | None]:
        rec = self.orders.get(order_id)
        return DataResult(
            data=self._to_broker_order(rec) if rec else None, source="fake"
        )

    async def get_order_by_client_id(
        self, client_order_id: str
    ) -> DataResult[BrokerOrder | None]:
        for rec in self.orders.values():
            if rec.client_order_id == client_order_id:
                return DataResult(data=self._to_broker_order(rec), source="fake")
        return DataResult(data=None, source="fake")

    # ------------------------------------------------------------------
    # writes

    def _write_error(self) -> DataResult[BrokerOrder]:
        return DataResult(
            data=None,  # type: ignore[arg-type]
            error="fake write failure",
            source="fake",
        )

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
        self.submit_calls += 1
        if self.fail_writes:
            return self._write_error()
        if self._duplicate_client_id(client_order_id):
            return DataResult(
                data=None,  # type: ignore[arg-type]
                error="client_order_id must be unique",
                source="fake",
            )
        now = self._now()
        parent = _OrderRec(
            id=self._next_id(),
            client_order_id=client_order_id,
            symbol=symbol,
            side="buy",
            order_type="limit",
            order_class="bracket",
            status="new",
            qty=qty,
            filled_qty=0,
            filled_avg_price=None,
            limit_price=limit_price,
            stop_price=None,
            created_at=now,
            updated_at=now,
            filled_at=None,
        )
        for leg_type, order_type in (("target", "limit"), ("stop", "stop")):
            leg = _OrderRec(
                id=self._next_id(),
                client_order_id=None,
                symbol=symbol,
                side="sell",
                order_type=order_type,
                order_class="",
                status="held",  # legs activate when the parent fills
                qty=qty,
                filled_qty=0,
                filled_avg_price=None,
                limit_price=target_price if leg_type == "target" else None,
                stop_price=stop_price if leg_type == "stop" else None,
                created_at=now,
                updated_at=now,
                filled_at=None,
                parent_id=parent.id,
            )
            self.orders[leg.id] = leg
            parent.legs.append(leg.id)
        self.orders[parent.id] = parent
        if self.place_then_fail:
            return self._write_error()
        return DataResult(data=self._to_broker_order(parent), source="fake")

    async def submit_stop_order(
        self,
        *,
        symbol: str,
        qty: int,
        stop_price: Decimal,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        self.submit_calls += 1
        if self.fail_writes:
            return self._write_error()
        if self._duplicate_client_id(client_order_id):
            return DataResult(
                data=None,  # type: ignore[arg-type]
                error="client_order_id must be unique",
                source="fake",
            )
        now = self._now()
        rec = _OrderRec(
            id=self._next_id(),
            client_order_id=client_order_id,
            symbol=symbol,
            side="sell",
            order_type="stop",
            order_class="",
            status="new",
            qty=qty,
            filled_qty=0,
            filled_avg_price=None,
            limit_price=None,
            stop_price=stop_price,
            created_at=now,
            updated_at=now,
            filled_at=None,
        )
        self.orders[rec.id] = rec
        if self.place_then_fail:
            return self._write_error()
        return DataResult(data=self._to_broker_order(rec), source="fake")

    async def submit_oco_exit(
        self,
        *,
        symbol: str,
        qty: int,
        limit_price: Decimal,
        stop_price: Decimal,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        self.submit_calls += 1
        if self.fail_writes or symbol in self.reject_oco:
            return self._write_error()
        if self._duplicate_client_id(client_order_id):
            return DataResult(
                data=None,  # type: ignore[arg-type]
                error="client_order_id must be unique",
                source="fake",
            )
        now = self._now()
        parent = _OrderRec(
            id=self._next_id(),
            client_order_id=client_order_id,
            symbol=symbol,
            side="sell",
            order_type="limit",
            order_class="oco",
            status="new",
            qty=qty,
            filled_qty=0,
            filled_avg_price=None,
            limit_price=limit_price,
            stop_price=None,
            created_at=now,
            updated_at=now,
            filled_at=None,
        )
        for order_type, price in (("limit", limit_price), ("stop", stop_price)):
            leg = _OrderRec(
                id=self._next_id(),
                client_order_id=None,
                symbol=symbol,
                side="sell",
                order_type=order_type,
                order_class="",
                status="new",
                qty=qty,
                filled_qty=0,
                filled_avg_price=None,
                limit_price=price if order_type == "limit" else None,
                stop_price=price if order_type == "stop" else None,
                created_at=now,
                updated_at=now,
                filled_at=None,
                parent_id=parent.id,
            )
            self.orders[leg.id] = leg
            parent.legs.append(leg.id)
        self.orders[parent.id] = parent
        if self.place_then_fail:
            return self._write_error()
        return DataResult(data=self._to_broker_order(parent), source="fake")

    async def submit_market_sell(
        self,
        *,
        symbol: str,
        qty: int,
        market_on_open: bool,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        self.submit_calls += 1
        if self.fail_writes:
            return self._write_error()
        if self._duplicate_client_id(client_order_id):
            return DataResult(
                data=None,  # type: ignore[arg-type]
                error="client_order_id must be unique",
                source="fake",
            )
        now = self._now()
        rec = _OrderRec(
            id=self._next_id(),
            client_order_id=client_order_id,
            symbol=symbol,
            side="sell",
            order_type="market",
            order_class="",
            status="new",
            qty=qty,
            filled_qty=0,
            filled_avg_price=None,
            limit_price=None,
            stop_price=None,
            created_at=now,
            updated_at=now,
            filled_at=None,
            time_in_force="opg" if market_on_open else "day",
        )
        if symbol in self.reject_sells:
            return DataResult(
                data=None,  # type: ignore[arg-type]
                error="sale rejected (e.g. broker day-trade protection)",
                source="fake",
            )
        self.orders[rec.id] = rec
        if self.place_then_fail:
            return self._write_error()
        if market_on_open:
            # Queued for the open like Alpaca's MOO orders: it stays
            # "accepted" until the next tick opens the session.
            rec.status = "accepted"
            return DataResult(data=self._to_broker_order(rec), source="fake")
        price = self.prices.get(symbol, Decimal("0"))
        self._fill(rec, price)
        await self._emit(rec, "fill")
        return DataResult(data=self._to_broker_order(rec), source="fake")

    async def replace_stop(
        self, order_id: str, *, stop_price: Decimal
    ) -> DataResult[BrokerOrder]:
        self.submit_calls += 1
        if self.fail_writes:
            return self._write_error()
        rec = self.orders.get(order_id)
        if rec is None:
            return DataResult(
                data=None,  # type: ignore[arg-type]
                error="unknown order",
                source="fake",
            )
        rec.stop_price = stop_price
        rec.updated_at = self._now()
        rec.status = "replaced"
        new_rec = _OrderRec(
            id=self._next_id(),
            client_order_id=rec.client_order_id,
            symbol=rec.symbol,
            side=rec.side,
            order_type=rec.order_type,
            order_class=rec.order_class,
            status="new",
            qty=rec.qty,
            filled_qty=0,
            filled_avg_price=None,
            limit_price=rec.limit_price,
            stop_price=stop_price,
            created_at=self._now(),
            updated_at=self._now(),
            filled_at=None,
            parent_id=rec.parent_id,
        )
        if rec.parent_id is not None:
            parent = self.orders[rec.parent_id]
            parent.legs = [new_rec.id if i == rec.id else i for i in parent.legs]
        self.orders[new_rec.id] = new_rec
        if self.place_then_fail:
            return self._write_error()
        return DataResult(data=self._to_broker_order(new_rec), source="fake")

    async def cancel_order(self, order_id: str) -> DataResult[None]:
        self.submit_calls += 1
        if self.fail_writes:
            return DataResult(data=None, error="fake write failure", source="fake")
        rec = self.orders.get(order_id)
        if rec is None:
            return DataResult(data=None, error="unknown order", source="fake")
        if not self.cancel_stuck:
            rec.status = "canceled"
            rec.updated_at = self._now()
            await self._emit(rec, "canceled")
        return DataResult(data=None, source="fake")

    async def cancel_all_orders(self) -> DataResult[int]:
        self.submit_calls += 1
        if self.fail_writes:
            return DataResult(data=0, error="fake write failure", source="fake")
        cancelled = 0
        for rec in self.orders.values():
            if rec.status in OPEN_ORDER_STATUSES:
                if self.cancel_stuck:
                    # Like a stuck pending_cancel at Alpaca: the call
                    # succeeds but the order stays live.
                    continue
                rec.status = "canceled"
                rec.updated_at = self._now()
                cancelled += 1
                await self._emit(rec, "canceled")
        return DataResult(data=cancelled, source="fake")

    async def close_all_positions(
        self, *, cancel_orders: bool = True
    ) -> DataResult[list[BrokerOrder] | None]:
        self.submit_calls += 1
        if self.fail_writes:
            return DataResult(data=None, error="fake write failure", source="fake")
        if cancel_orders:
            for rec in self.orders.values():
                if rec.status in OPEN_ORDER_STATUSES:
                    rec.status = "canceled"
                    rec.updated_at = self._now()
        sold: list[BrokerOrder] = []
        for symbol, pos in list(self.positions.items()):
            if pos.qty <= 0:
                continue
            price = self.prices.get(symbol, pos.avg_entry_price)
            self.cash += price * pos.qty
            del self.positions[symbol]
            self._seq += 1
            sold.append(
                BrokerOrder(
                    id=f"fake-liquidate-{self._seq}",
                    client_order_id=None,
                    symbol=symbol,
                    side="sell",
                    order_type="market",
                    order_class="simple",
                    status="filled",
                    qty=pos.qty,
                    filled_qty=pos.qty,
                    filled_avg_price=price,
                    limit_price=None,
                    stop_price=None,
                    created_at=self._now(),
                    updated_at=self._now(),
                    filled_at=self._now(),
                )
            )
        return DataResult(data=sold, source="fake")

    async def get_asset(self, symbol: str) -> DataResult[BrokerAsset | None]:
        if self.fail_reads:
            return DataResult(data=None, error="fake read failure", source="fake")
        if symbol in self.untradable:
            return DataResult(data=None, error="asset not found", source="fake")
        if symbol in self.non_tradable:
            return DataResult(
                data=BrokerAsset(
                    symbol=symbol,
                    status="inactive",
                    tradable=False,
                    asset_class="us_equity",
                    exchange="NASDAQ",
                    shortable=False,
                ),
                source="fake",
            )
        return DataResult(
            data=BrokerAsset(
                symbol=symbol,
                status="active",
                tradable=True,
                asset_class="us_equity",
                exchange="NASDAQ",
                shortable=False,
            ),
            source="fake",
        )

    # ------------------------------------------------------------------
    # stream + simulation

    def subscribe_trade_updates(self, handler: TradeUpdateHandler) -> None:
        self._handlers.append(handler)

    def stream_connected(self) -> bool:
        return self._stream_started

    async def start_stream(self) -> None:
        self._stream_started = True

    async def stop_stream(self) -> None:
        self._stream_started = False

    async def tick(self, symbol: str, price: Decimal) -> None:
        """Move a mark; fills any order whose price is now crossed."""
        self.prices[symbol] = price
        for rec in list(self.orders.values()):
            # A partially filled order keeps working until done, like the
            # real broker — it is not a terminal state.
            if rec.symbol != symbol or rec.status not in (
                "new",
                "accepted",
                "partially_filled",
            ):
                continue
            if rec.order_type == "market" and rec.side == "sell":
                # A queued market(-on-open) sell fills at the next tick.
                self._fill(rec, price)
                await self._emit(rec, "fill")
                continue
            if rec.side == "buy" and rec.order_type == "limit":
                if rec.limit_price is not None and price <= rec.limit_price:
                    self._fill(rec, price)
                    await self._emit(rec, "fill")
            elif rec.side == "sell":
                if (
                    rec.order_type == "stop"
                    and rec.stop_price is not None
                    and price <= rec.stop_price
                ):
                    self._fill(rec, rec.stop_price)
                    await self._emit(rec, "fill")
                elif (
                    rec.order_type == "limit"
                    and rec.limit_price is not None
                    and price >= rec.limit_price
                ):
                    self._fill(rec, rec.limit_price)
                    await self._emit(rec, "fill")
