"""Read-only execution check against the real paper account.

Run from the repo root: ``MODE=paper make smoke-execution``. It exercises the
same calls startup and the 15-minute position check make — the paper-account
check, one reconciliation pass and one position check — and prints what each
found. It can never place an order: the broker is wrapped so every write
fails locally with "read-only check" before reaching Alpaca.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from app.api.main import (
    check_execution_mode,
    make_broker,
    run_migrations,
)
from app.broker.base import Broker, TradeUpdateHandler
from app.broker.models import (
    BrokerAccount,
    BrokerAsset,
    BrokerOrder,
    BrokerPosition,
)
from app.config import get_config
from app.data.factory import make_data_sources
from app.data.models import DataResult
from app.data.service import MarketDataService
from app.engine.executor import Executor
from app.engine.position_check import PositionChecker
from app.engine.state import load_execution_state
from app.market.calendar import CalendarStore
from app.market.clock import make_clock
from app.secrets_service import (
    SecretsStore,
    make_key_testers,
    read_env_keys,
)
from app.signals.service import SignalService

BLOCKED = "read-only check: write blocked"


class ReadOnlyBroker:
    """Wraps the real broker; reads pass through, writes never reach Alpaca."""

    def __init__(self, inner: Broker) -> None:
        self._inner = inner

    async def get_account(self) -> DataResult[BrokerAccount]:
        return await self._inner.get_account()

    async def get_positions(self) -> DataResult[list[BrokerPosition]]:
        return await self._inner.get_positions()

    async def get_position(
        self, symbol: str
    ) -> DataResult[BrokerPosition | None]:
        return await self._inner.get_position(symbol)

    async def get_account_config(
        self,
    ) -> DataResult[dict[str, str | bool | None]]:
        return await self._inner.get_account_config()

    async def set_account_config(
        self, **fields: object
    ) -> DataResult[dict[str, str | bool | None]]:
        return DataResult(data=None, error=BLOCKED, source="read-only")  # type: ignore[arg-type]

    async def get_open_orders(self) -> DataResult[list[BrokerOrder]]:
        return await self._inner.get_open_orders()

    async def get_order(self, order_id: str) -> DataResult[BrokerOrder | None]:
        return await self._inner.get_order(order_id)

    async def get_order_by_client_id(
        self, client_order_id: str
    ) -> DataResult[BrokerOrder | None]:
        return await self._inner.get_order_by_client_id(client_order_id)

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
        return DataResult(data=None, error=BLOCKED, source="read-only")  # type: ignore[arg-type]

    async def submit_stop_order(
        self,
        *,
        symbol: str,
        qty: int,
        stop_price: Decimal,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        return DataResult(data=None, error=BLOCKED, source="read-only")  # type: ignore[arg-type]

    async def submit_oco_exit(
        self,
        *,
        symbol: str,
        qty: int,
        limit_price: Decimal,
        stop_price: Decimal,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        return DataResult(data=None, error=BLOCKED, source="read-only")  # type: ignore[arg-type]

    async def submit_market_sell(
        self,
        *,
        symbol: str,
        qty: int,
        market_on_open: bool,
        client_order_id: str,
    ) -> DataResult[BrokerOrder]:
        return DataResult(data=None, error=BLOCKED, source="read-only")  # type: ignore[arg-type]

    async def replace_stop(
        self, order_id: str, *, stop_price: Decimal
    ) -> DataResult[BrokerOrder]:
        return DataResult(data=None, error=BLOCKED, source="read-only")  # type: ignore[arg-type]

    async def cancel_order(self, order_id: str) -> DataResult[None]:
        return DataResult(data=None, error=BLOCKED, source="read-only")

    async def cancel_all_orders(self) -> DataResult[int]:
        return DataResult(data=0, error=BLOCKED, source="read-only")

    async def close_all_positions(
        self, *, cancel_orders: bool = True
    ) -> DataResult[list[BrokerOrder] | None]:
        return DataResult(data=[], error=BLOCKED, source="read-only")

    async def get_asset(self, symbol: str) -> DataResult[BrokerAsset | None]:
        return await self._inner.get_asset(symbol)

    def stream_connected(self) -> bool:
        return self._inner.stream_connected()

    def subscribe_trade_updates(self, handler: TradeUpdateHandler) -> None:
        return None

    def subscribe_stream_failures(self, handler: object) -> None:
        return None

    async def start_stream(self) -> None:
        return None

    async def stop_stream(self) -> None:
        return None


async def _main() -> int:
    config = get_config()
    check_execution_mode(config)
    print(f"startup: execution.mode ok ({config.get('execution', 'mode')})")

    run_migrations()
    clock = make_clock(config)
    store = SecretsStore(config, clock)
    await store.import_env_keys(
        read_env_keys(), make_key_testers(config, clock)
    )
    sources = make_data_sources(config, store)
    if sources.alpaca is None:
        print("MODE is not paper — run with MODE=paper")
        return 1

    paper = await sources.alpaca.check_paper_account()
    if paper.error is not None:
        print(f"paper-account check: FAILED ({paper.error})")
        return 1
    print(f"paper-account check: ok ({paper.data})")

    broker = ReadOnlyBroker(make_broker(config, sources, clock, store))
    calendar_store = CalendarStore(sources.calendar)
    market_data = MarketDataService(sources, calendar_store, config)
    signal_service = SignalService(market_data, calendar_store, config, clock)
    state = load_execution_state(clock, config)
    executor = Executor(
        broker=broker,
        market_data=market_data,
        signals=signal_service,
        calendar=calendar_store,
        config=config,
        clock=clock,
        state=state,
    )

    account = await broker.get_account()
    if account.data is not None:
        print(
            "account: "
            f"equity {account.data.equity} "
            f"cash {account.data.cash} "
            f"last_equity {account.data.last_equity}"
        )

    report = await executor.reconcile()
    print(
        "reconciliation: "
        f"{'ok' if report.ok else 'FAILED'}; "
        f"orders synced {report.orders_synced}, "
        f"fills recorded {report.fills_recorded}, "
        f"entries cancelled {len(report.entries_cancelled)}, "
        f"adopted {report.adopted or 'none'}, "
        f"protective stops {report.protective_stops or 'none'}, "
        f"mismatches {report.mismatches or 'none'}"
    )
    for error in report.errors:
        print(f"  reconcile error: {error}")

    checker = PositionChecker(
        executor=executor, state=state, config=config, clock=clock
    )
    check = await checker.run()
    print(
        "position check: "
        f"{'ok' if check.ok else 'FAILED'}; "
        f"mismatches {check.mismatches}, "
        f"stops placed {check.stops_placed}, "
        f"entries cancelled {check.entries_cancelled}"
    )
    for error in check.errors:
        print(f"  check error: {error}")
    return 0 if report.ok and check.ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
