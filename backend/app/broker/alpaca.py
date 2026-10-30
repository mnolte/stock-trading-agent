"""Alpaca paper broker (docs/SPEC.md, Alpaca integration).

The trading client is always created with ``paper=True``; the paper
account check happens at startup in ``AlpacaDataClient``. Every write
goes through the shared rate limiter's ``run_write`` — order calls are
never retried. The trade-updates stream runs in its own thread (the SDK
spins a private event loop) and hands parsed updates to the engine.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
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

from app.broker.base import StreamFailureHandler, TradeUpdateHandler
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

logger = logging.getLogger(__name__)


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _ts(value: Any) -> datetime | None:
    if value is None:
        return None
    return _parse_timestamp(value)


async def _await_handler(
    handler: TradeUpdateHandler, update: TradeUpdateInfo
) -> None:
    """Wrap an Awaitable handler in a coroutine for run_coroutine_threadsafe."""
    await handler(update)


async def _await_failure_handler(
    handler: StreamFailureHandler, reason: str
) -> None:
    """Same wrap for the stream-failure handlers."""
    await handler(reason)


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
        qty=int(Decimal(str(qty_raw))) if qty_raw is not None else 0,
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
    # The stream is created with raw_data=True, so the SDK delivers the
    # websocket envelope {"stream": "trade_updates", "data": {...}} —
    # unwrap it. The bare payload and SDK models still parse directly.
    if raw.get("stream") == "trade_updates":
        raw = _raw(raw.get("data"))
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
        keyed: bool = True,
    ) -> None:
        self.limiter = rate_limiter or RateLimiter()
        # False when no Alpaca keys are stored: the clients still exist so
        # calls fail as ordinary errors, but the trade stream stays off.
        self._keyed = keyed
        self._handlers: list[TradeUpdateHandler] = []
        self._failure_handlers: list[StreamFailureHandler] = []
        self._stream_started = False
        self._stream_thread: threading.Thread | None = None
        # The app event loop the stream hands updates to; captured in
        # start_stream (the SDK stream runs its own loop on its thread).
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._set_credentials(api_key, secret_key)

    def _set_credentials(self, api_key: str, secret_key: str) -> None:
        self.trading = TradingClient(api_key, secret_key, paper=True, raw_data=True)
        self._stream = TradingStream(api_key, secret_key, paper=True, raw_data=True)

    async def update_credentials(
        self, api_key: str, secret_key: str, keyed: bool = True
    ) -> None:
        """Swap keys after a Settings save; restarts the trade stream so
        updates flow for the new account."""
        was_started = self._stream_started
        if was_started:
            await self.stop_stream()
        self._keyed = keyed
        self._set_credentials(api_key, secret_key)
        if was_started:
            await self.start_stream()

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

    async def get_position(
        self, symbol: str
    ) -> DataResult[BrokerPosition | None]:
        try:
            raw = await self.limiter.run_read(
                self.trading.get_open_position, symbol
            )
            return DataResult(data=parse_position(raw), source="alpaca")
        except Exception as exc:  # noqa: BLE001
            # Alpaca answers a missing position with a 404 — that is a
            # flat answer, not an error. Other failures stay errors.
            text = str(exc)
            if "404" in text or "position does not exist" in text.lower():
                return DataResult(data=None, source="alpaca")
            return DataResult(data=None, error=text, source="alpaca")

    async def get_account_config(
        self,
    ) -> DataResult[dict[str, str | bool | None]]:
        try:
            raw = await self.limiter.run_read(
                self.trading.get_account_configurations
            )
            item = _raw(raw)
            return DataResult(
                data={
                    "no_shorting": bool(item.get("no_shorting")),
                    "max_margin_multiplier": (
                        str(item.get("max_margin_multiplier"))
                        if item.get("max_margin_multiplier") is not None
                        else None
                    ),
                },
                source="alpaca",
            )
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=None, error=str(exc), source="alpaca")  # type: ignore[arg-type]

    async def set_account_config(
        self, **fields: object
    ) -> DataResult[dict[str, str | bool | None]]:
        try:
            current = _raw(
                await self.limiter.run_read(
                    self.trading.get_account_configurations
                )
            )
            merged = dict(current)
            merged.update(fields)
            from alpaca.trading.models import AccountConfiguration

            config = AccountConfiguration.model_validate(
                {
                    k: v
                    for k, v in merged.items()
                    if k in AccountConfiguration.model_fields
                }
            )
            await self.limiter.run_write(
                self.trading.set_account_configurations, config
            )
            return await self.get_account_config()
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=None, error=str(exc), source="alpaca")  # type: ignore[arg-type]

    async def get_open_orders(self) -> DataResult[list[BrokerOrder]]:
        try:
            raw = await self.limiter.run_read(
                self.trading.get_orders,
                GetOrdersRequest(
                    status=QueryOrderStatus.OPEN, nested=True, limit=500
                ),
            )
            orders = [parse_order(o) for o in (list(raw) if raw else [])]
            # Alpaca's open filter never returns `held` orders, but a
            # bracket's stop leg stays `held` — and fully working — under
            # its filled parent. Recover live legs still riding under
            # closed parents so the stop-coverage check sees them.
            closed = await self.limiter.run_read(
                self.trading.get_orders,
                GetOrdersRequest(
                    status=QueryOrderStatus.CLOSED, nested=True, limit=500
                ),
            )
            seen = {order.id for order in orders}
            seen.update(leg.id for order in orders for leg in order.legs)
            for parent in [parse_order(o) for o in (list(closed) if closed else [])]:
                for leg in parent.legs:
                    if leg.is_open and leg.id not in seen:
                        seen.add(leg.id)
                        orders.append(leg)
            return DataResult(data=orders, source="alpaca")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=[], error=str(exc), source="alpaca")

    async def get_filled_orders(
        self, symbol: str, since: datetime
    ) -> DataResult[list[BrokerOrder]]:
        try:
            raw = await self.limiter.run_read(
                self.trading.get_orders,
                GetOrdersRequest(
                    status=QueryOrderStatus.CLOSED,
                    symbols=[symbol],
                    nested=True,
                    limit=500,
                ),
            )
            parents = [parse_order(order) for order in (list(raw) if raw else [])]
            if len(parents) >= 500:
                return DataResult(
                    data=[],
                    error="filled order history exceeded page limit",
                    source="alpaca",
                )
            orders = {
                order.id: order
                for parent in parents
                for order in (parent, *parent.legs)
            }
            return DataResult(
                data=[
                    order
                    for order in orders.values()
                    if order.filled_qty > 0
                    and order.filled_at is not None
                    and order.filled_at >= since
                ],
                source="alpaca",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("filled order history failed for %s", symbol)
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

    async def submit_oco_exit(
        self,
        *,
        symbol: str,
        qty: int,
        limit_price: Decimal,
        stop_price: Decimal,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        request = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            type="limit",
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.OCO,
            limit_price=float(limit_price),
            take_profit=TakeProfitRequest(limit_price=float(limit_price)),
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
        """Whether the trade-updates stream is live right now.

        The SDK reconnects inside ``run()``: while it waits between
        attempts ``_running`` is False and fills are genuinely being
        missed, so readiness should say "down". A dead runner thread
        (``run()`` returned or raised) is down until the app restarts
        it. If a future SDK drops ``_running`` we fall back to thread
        liveness rather than a permanent false alarm.
        """
        thread = self._stream_thread
        if (
            not self._stream_started
            or thread is None
            or not thread.is_alive()
        ):
            return False
        running = getattr(self._stream, "_running", None)
        return True if running is None else bool(running)

    def subscribe_trade_updates(self, handler: TradeUpdateHandler) -> None:
        self._handlers.append(handler)

    def subscribe_stream_failures(self, handler: StreamFailureHandler) -> None:
        self._failure_handlers.append(handler)

    async def _on_trade_update(self, item: Any) -> None:
        """Runs on the stream thread's private loop: hand work to the app loop.

        Handlers touch the database, the reconcile lock and the update
        bus — all bound to the application event loop. Awaiting them here
        would crash on the loop-bound ``asyncio.Lock``, so each handler is
        scheduled on the main loop with ``run_coroutine_threadsafe``.
        One bad message is logged and counted, never fatal to the next
        (docs/SPEC.md, Alpaca integration — Stream health).
        """
        loop = self._main_loop
        if loop is None or loop.is_closed():
            return
        try:
            update = parse_trade_update(item)
        except Exception as exc:  # noqa: BLE001 - carry on to the next message
            # The log scrubber strips any stored keys from the raw dump.
            logger.error(
                "unparseable trade update dropped: %s — raw: %s",
                exc,
                repr(item)[:2000],
                exc_info=True,
            )
            for failure_handler in self._failure_handlers:
                failed: Future[None] = asyncio.run_coroutine_threadsafe(
                    _await_failure_handler(failure_handler, str(exc)), loop
                )
                failed.add_done_callback(self._log_handler_result)
            return
        for handler in self._handlers:
            future: Future[None] = asyncio.run_coroutine_threadsafe(
                _await_handler(handler, update), loop
            )
            future.add_done_callback(self._log_handler_result)

    def _on_stream_outage(self) -> None:
        """The SDK logger's outage crossed the degrade window — hand the
        failure handlers a reason on the app loop (docs/SPEC.md —
        Stream health). Runs on the SDK thread; the session check is
        the executor's."""
        loop = self._main_loop
        if loop is None or loop.is_closed():
            return
        for failure_handler in self._failure_handlers:
            failed: Future[None] = asyncio.run_coroutine_threadsafe(
                _await_failure_handler(
                    failure_handler, "stream reconnect outage"
                ),
                loop,
            )
            failed.add_done_callback(self._log_handler_result)

    @staticmethod
    def _log_handler_result(future: Future[None]) -> None:
        """Surface handler failures: the scheduled future is never awaited."""
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            logger.error("trade-update handler failed: %s", exc, exc_info=exc)

    async def start_stream(self) -> None:
        """Run the SDK websocket in its own thread (it owns an event loop)."""
        if self._stream_started or not self._keyed:
            return
        self._stream_started = True
        self._main_loop = asyncio.get_running_loop()
        self._stream.subscribe_trade_updates(self._on_trade_update)
        # Outage-aware dedup on the SDK's stream logger: first failure
        # at ERROR, repeats at most once per 10 minutes, reconnect at
        # INFO with the outage length, long outages handed to the
        # failure handlers (docs/SPEC.md — Stream health).
        stream_logger = logging.getLogger("alpaca.trading.stream")
        if not any(
            isinstance(f, StreamOutageFilter) for f in stream_logger.filters
        ):
            stream_logger.addFilter(
                StreamOutageFilter(degraded=self._on_stream_outage)
            )
        await asyncio.to_thread(self._run_stream_blocking)

    def _run_stream_blocking(self) -> None:
        # TradingStream.run() calls asyncio.run(); it must live on a
        # dedicated thread, not the API event loop.
        self._stream_thread = threading.Thread(
            target=self._stream_thread_main,
            name="alpaca-trade-stream",
            daemon=True,
        )
        self._stream_thread.start()

    def _stream_thread_main(self) -> None:
        """Run the SDK loop; a death it didn't plan reaches the JSON log.

        Without this wrapper an exception in the thread surfaces only on
        stderr through ``threading.excepthook`` — invisible to the log
        index — while ``stream_connected`` keeps claiming "connected".
        """
        try:
            self._stream.run()
        except Exception:
            if self._stream_started:
                logger.error(
                    "trade-updates stream stopped unexpectedly", exc_info=True
                )

    async def stop_stream(self) -> None:
        if not self._stream_started:
            return
        self._stream_started = False
        await asyncio.to_thread(self._stream.stop)
        self._stream_thread = None


class StreamOutageFilter(logging.Filter):
    """Reconnect logging for the SDK's ``alpaca.trading.stream`` logger
    (docs/SPEC.md, Alpaca integration — Stream health).

    An outage opens at the first WARNING/ERROR line and ends when the
    SDK reports "connected to:". While it runs, the first failure is
    kept at ERROR, the same error repeats at most once per
    REPORT_EVERY_SECONDS with a count, the SDK's own INFO is left to
    the library rule, and the reconnect is announced at INFO by the
    app logger with the outage length. An outage that crosses
    DEGRADE_AFTER_SECONDS calls ``degraded`` once — the executor
    decides whether a session is open.
    """

    REPORT_EVERY_SECONDS = 600.0
    DEGRADE_AFTER_SECONDS = 300.0

    def __init__(
        self,
        *,
        degraded: Callable[[], None] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._degraded = degraded
        self._now = now
        self._outage_start: float | None = None
        # error key → (count, last reported monotonic)
        self._errors: dict[str, list[Any]] = {}
        self._degraded_notified = False

    def _check_degraded(self, at: float) -> None:
        if (
            self._degraded is not None
            and not self._degraded_notified
            and self._outage_start is not None
            and at - self._outage_start >= self.DEGRADE_AFTER_SECONDS
        ):
            self._degraded_notified = True
            try:
                self._degraded()
            except Exception:
                # A filter must never break logging; the callback's own
                # failure is still reported (hard rule 15).
                logger.exception("stream-outage degraded callback failed")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        at = self._now()
        if record.levelno >= logging.WARNING:
            key = message.splitlines()[0][:160]
            if self._outage_start is None:
                # First failure of an outage — at ERROR (spec).
                self._outage_start = at
                self._errors = {key: [1, at]}
                self._degraded_notified = False
                if record.levelno < logging.ERROR:
                    record.levelno = logging.ERROR
                    record.levelname = "ERROR"
                return True
            entry = self._errors.get(key)
            if entry is None:
                # A different failure during the same outage: logged.
                self._errors[key] = [1, at]
                self._check_degraded(at)
                return True
            entry[0] += 1
            if at - entry[1] < self.REPORT_EVERY_SECONDS:
                self._check_degraded(at)
                return False
            entry[1] = at
            record.msg = (
                f"{message} (repeated x{entry[0]} since the outage began)"
            )
            record.args = ()
            self._check_degraded(at)
            return True
        if (
            message.startswith("connected to:")
            and self._outage_start is not None
        ):
            duration = at - self._outage_start
            self._outage_start = None
            self._errors = {}
            self._degraded_notified = False
            logging.getLogger("app.broker.stream").info(
                "trade-updates stream reconnected after %.0f s down",
                duration,
            )
        return True


__all__ = [
    "AlpacaPaperBroker",
    "parse_account",
    "parse_order",
    "parse_position",
    "parse_trade_update",
]
