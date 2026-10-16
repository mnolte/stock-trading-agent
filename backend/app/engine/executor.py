"""The executor: turns stored decisions into broker orders and keeps the
database a mirror of the Alpaca paper account (docs/SPEC.md, Alpaca
integration and Risk management).

Everything here follows three hard rules: every position carries a live
broker-side stop, no order is ever resent automatically (after a write
error reconciliation decides what happened), and all money is Decimal.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.base import Broker
from app.broker.models import (
    OPEN_ORDER_STATUSES,
    BrokerAccount,
    BrokerOrder,
    BrokerPosition,
    TradeUpdateInfo,
)
from app.data.cache import _naive_utc
from app.data.models import Quote
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import DayTrade, Decision, Event, Fill, Order, Position
from app.engine.state import ExecutionState, record_event
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, Clock
from app.risk.gates import GateContext, evaluate_entry_gates
from app.signals.indicators import atr
from app.signals.service import SignalService, SymbolEval

if TYPE_CHECKING:
    from app.config import AppConfig

CENT = Decimal("0.01")


@dataclass
class ReconcileReport:
    """What one reconciliation pass changed; also used by tests."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    protective_stops: list[str] = field(default_factory=list)
    entries_cancelled: list[str] = field(default_factory=list)
    orders_synced: int = 0
    fills_recorded: int = 0


def _ny_date(dt: datetime) -> date:
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(NY_TZ).date()


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class Executor:
    """Order placement, reconciliation and kill-switch actions."""

    def __init__(
        self,
        *,
        broker: Broker,
        market_data: MarketDataService,
        signals: SignalService,
        calendar: CalendarProvider,
        config: AppConfig,
        clock: Clock,
        state: ExecutionState,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.broker = broker
        self.market_data = market_data
        self.signals = signals
        self.calendar = calendar
        self.config = config
        self.clock = clock
        self.state = state
        self._session_factory = session_factory or get_session_maker()
        self._orders_this_cycle = 0
        self._order_seq = 0
        self._reconcile_failed = False
        self._reconcile_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # account + brakes

    async def refresh_account(self) -> BrokerAccount | None:
        result = await self.broker.get_account()
        if result.error is not None or result.data is None:
            return None
        account = result.data
        self.state.account = account
        if account.equity > self.state.peak_equity:
            self.state.peak_equity = account.equity
        return account

    async def update_session_brakes(self) -> None:
        """Shock brake, daily loss and drawdown kill from fresh data."""
        self.state.reset_session_if_new_day()
        account = await self.refresh_account()
        if account is not None:
            limit = Decimal(
                str(self.config.get("risk", "daily_loss_limit_pct", default=2))
            )
            if account.last_equity > 0:
                pnl_pct = (
                    (account.equity - account.last_equity)
                    / account.last_equity
                    * 100
                )
                if pnl_pct <= -limit:
                    self.state.daily_loss_hit = True
            kill_pct = Decimal(
                str(self.config.get("risk", "drawdown_kill_pct", default=8))
            )
            if (
                self.state.peak_equity > 0
                and (self.state.peak_equity - account.equity)
                / self.state.peak_equity
                * 100
                >= kill_pct
                and not self.state.kill_switch_on
            ):
                await self.activate_kill_switch(
                    reason=(
                        f"drawdown kill: equity {account.equity} is "
                        f"{kill_pct}% below peak {self.state.peak_equity}"
                    ),
                    automatic=True,
                )

        # Shock brake from SPY 1h bars.
        now = self.clock.now()
        today = now.astimezone(NY_TZ).date()
        session = self.calendar.session_for(today)
        if session is None:
            return
        sessions = self.market_data.recent_sessions(today, 3)
        bars_1h = [
            b
            for b in self.market_data.get_1h_bars_range("SPY", sessions)
            if b.end <= now
        ]
        if not bars_1h:
            return
        shock_1h = Decimal(
            str(self.config.get("risk", "shock_brake_1h_pct", default=1.5))
        )
        shock_session = Decimal(
            str(self.config.get("risk", "shock_brake_session_pct", default=2.5))
        )
        last = bars_1h[-1]
        if last.open > 0:
            change = (last.close - last.open) / last.open * 100
            if change <= -shock_1h:
                self.state.shock_brake_1h = True
        todays = [
            b for b in bars_1h if b.start.astimezone(NY_TZ).date() == today
        ]
        if todays and todays[0].open > 0:
            change = (todays[-1].close - todays[0].open) / todays[0].open * 100
            if change <= -shock_session:
                self.state.shock_brake_session = True

    # ------------------------------------------------------------------
    # kill switch

    async def activate_kill_switch(
        self, *, reason: str, automatic: bool, flatten: bool = False
    ) -> None:
        """Turn the switch on and log it. ``flatten`` is stop & flatten."""
        self.state.kill_switch_on = True
        self.state.kill_switch_since = self.clock.now()
        self.state.kill_switch_reason = reason
        self.state.kill_switch_auto = automatic
        with self._session_factory() as session:
            record_event(
                session,
                self.clock,
                level="critical" if automatic else "warning",
                event_type="kill_switch",
                details={
                    "on": True,
                    "reason": reason,
                    "automatic": automatic,
                    "flatten": flatten,
                },
            )
            session.commit()

    async def resume(self, reason: str) -> None:
        """Resume after a kill switch. The typed reason is stored."""
        if not reason.strip():
            raise ValueError("a reason is required to resume")
        self.state.kill_switch_on = False
        self.state.kill_switch_reason = None
        self.state.kill_switch_auto = False
        self.state.failed_cycles = 0
        with self._session_factory() as session:
            record_event(
                session,
                self.clock,
                level="info",
                event_type="kill_switch_resume",
                details={"on": False, "reason": reason.strip()},
            )
            session.commit()

    async def flatten(self) -> dict[str, object]:
        """Stop & flatten: kill switch on, cancel all orders, sell everything.

        Outside regular hours the sells are queued as market-on-open.
        """
        await self.activate_kill_switch(
            reason="stop & flatten", automatic=False, flatten=True
        )
        cancelled = await self.broker.cancel_all_orders()
        now = self.clock.now()
        ny_now = now.astimezone(NY_TZ)
        today_session = self.calendar.session_for(ny_now.date())
        regular_hours = (
            today_session is not None
            and today_session.open <= ny_now <= today_session.close
        )
        sold: list[str] = []
        errors: list[str] = []
        rejected: list[str] = []
        day_trade_warnings: list[str] = []
        positions = await self.broker.get_positions()
        if positions.error is None:
            for pos in positions.data:
                self._orders_this_cycle += 1
                result = await self.broker.submit_market_sell(
                    symbol=pos.symbol,
                    qty=pos.qty,
                    market_on_open=not regular_hours,
                    client_order_id=self._client_id("flat", 0, pos.symbol),
                )
                if result.error is not None or result.data is None:
                    errors.append(f"{pos.symbol}: {result.error}")
                    rejected.append(pos.symbol)
                    # Never refused, never retried: the position keeps its
                    # stop (re-placed by the reconcile below) and the
                    # rejection shows as a critical banner.
                    with self._session_factory() as session:
                        record_event(
                            session,
                            self.clock,
                            level="critical",
                            event_type="flatten_sell_rejected",
                            details={
                                "symbol": pos.symbol,
                                "qty": pos.qty,
                                "error": result.error,
                                "market_on_open": not regular_hours,
                            },
                        )
                        session.commit()
                else:
                    sold.append(pos.symbol)
                    with self._session_factory() as session:
                        self._store_broker_order(
                            session, result.data, None, leg="exit"
                        )
                        # Flatten never refuses on the day-trade rule, but a
                        # same-day sale still counts toward the PDT limit —
                        # warn about it in the flatten event.
                        opened_today = session.execute(
                            select(Position).where(
                                Position.symbol == pos.symbol,
                                Position.closed_at.is_(None),
                            )
                        ).scalar_one_or_none()
                        if (
                            opened_today is not None
                            and _ny_date(opened_today.opened_at) == _ny_date(now)
                        ):
                            day_trade_warnings.append(pos.symbol)
                        session.commit()
        with self._session_factory() as session:
            record_event(
                session,
                self.clock,
                level="critical",
                event_type="flatten",
                details={
                    "cancelled_orders": cancelled.data,
                    "sold": sold,
                    "rejected": rejected,
                    "day_trade_warnings": day_trade_warnings,
                    "market_on_open": not regular_hours,
                    "errors": errors,
                },
            )
            session.commit()
        await self.reconcile()
        return {
            "cancelled_orders": cancelled.data,
            "sold": sold,
            "market_on_open": not regular_hours,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # acknowledgment

    def acknowledge_position(self, position_id: int) -> bool:
        """Acknowledge an adopted/mismatched position; it trades normally."""
        with self._session_factory() as session:
            position = session.get(Position, position_id)
            if position is None:
                return False
            position.pending_ack = False
            blocking = session.execute(
                select(Event).where(
                    Event.event_type == "reconciliation",
                    Event.acknowledged_at.is_(None),
                    Event.details["symbol"].as_string() == position.symbol,
                )
            ).scalars()
            now = _naive_utc(self.clock.now())
            for event in blocking:
                event.acknowledged_at = now
            session.commit()
        return True

    def acknowledge_event(self, event_id: int) -> bool:
        with self._session_factory() as session:
            event = session.get(Event, event_id)
            if event is None:
                return False
            event.acknowledged_at = _naive_utc(self.clock.now())
            session.commit()
        return True

    # ------------------------------------------------------------------
    # order rows

    def _client_id(self, kind: str, ref_id: int, symbol: str) -> str:
        self._order_seq += 1
        return f"sa-{kind}-{ref_id}-{symbol}-{self._order_seq}"

    @staticmethod
    def _leg_for(order: BrokerOrder, parent: BrokerOrder | None) -> str:
        if order.side == "buy":
            return "entry"
        if order.client_order_id and order.client_order_id.startswith(
            ("sa-exit", "sa-flat")
        ):
            return "exit"
        if order.order_type in ("stop", "stop_limit"):
            return "stop"
        if order.order_type == "limit" and parent is not None:
            return "target"
        return "exit" if order.order_type == "market" else "target"

    def _store_broker_order(
        self,
        session: Session,
        order: BrokerOrder,
        decision_id: int | None,
        *,
        leg: str | None = None,
        parent: BrokerOrder | None = None,
    ) -> Order:
        """Insert or update the DB row for one broker order (idempotent)."""
        row = session.execute(
            select(Order).where(Order.alpaca_order_id == order.id)
        ).scalar_one_or_none()
        if row is None and order.client_order_id:
            row = session.execute(
                select(Order).where(Order.client_order_id == order.client_order_id)
            ).scalar_one_or_none()
        if row is None:
            row = Order(
                alpaca_order_id=order.id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                parent_alpaca_order_id=parent.id if parent else None,
                decision_id=decision_id,
                side=order.side,
                order_type=order.order_type,
                leg=leg or self._leg_for(order, parent),
                qty=order.qty,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                status=order.status,
                created_at=_naive_utc(order.created_at or self.clock.now()),
            )
            session.add(row)
            session.flush()
        else:
            row.status = order.status
            if order.filled_at is not None:
                row.filled_at = _naive_utc(order.filled_at)
        if order.filled_qty > 0 and order.filled_avg_price is not None:
            self._record_fills(session, row, order)
        for leg_order in order.legs:
            self._store_broker_order(
                session, leg_order, decision_id, parent=order
            )
        return row

    def _record_fills(
        self, session: Session, row: Order, order: BrokerOrder
    ) -> None:
        """Record fill rows for any broker filled qty not yet stored."""
        recorded = session.execute(
            select(func.coalesce(func.sum(Fill.qty), 0)).where(
                Fill.order_id == row.id
            )
        ).scalar_one()
        delta = order.filled_qty - int(recorded)
        if delta <= 0 or order.filled_avg_price is None:
            return
        intended = (
            row.stop_price if row.leg == "stop" else row.limit_price
        ) or order.filled_avg_price
        session.add(
            Fill(
                order_id=row.id,
                qty=delta,
                price=order.filled_avg_price,
                filled_at=_naive_utc(order.filled_at or self.clock.now()),
                # Slippage vs the intended price; shown separately in the GUI.
                slippage=order.filled_avg_price - intended,
            )
        )

    # ------------------------------------------------------------------
    # reconciliation

    async def on_trade_update(self, update: TradeUpdateInfo) -> None:
        """A trade-updates event: mark the order, then reconcile the rest."""
        with self._session_factory() as session:
            row = session.execute(
                select(Order).where(Order.alpaca_order_id == update.order.id)
            ).scalar_one_or_none()
            if row is not None:
                row.status = update.order.status
                if update.order.filled_at is not None:
                    row.filled_at = _naive_utc(update.order.filled_at)
                self._record_fills(session, row, update.order)
                session.commit()
        await self.reconcile()

    async def reconcile(self) -> ReconcileReport:
        """Compare broker state with the database; Alpaca wins on mismatch."""
        async with self._reconcile_lock:
            return await self._reconcile()

    async def _reconcile(self) -> ReconcileReport:
        report = ReconcileReport()
        positions_r = await self.broker.get_positions()
        orders_r = await self.broker.get_open_orders()
        if positions_r.error is not None:
            report.ok = False
            report.errors.append(f"positions: {positions_r.error}")
        if orders_r.error is not None:
            report.ok = False
            report.errors.append(f"orders: {orders_r.error}")
        self._reconcile_failed = not report.ok
        if not report.ok:
            return report

        broker_positions = {p.symbol: p for p in positions_r.data}
        broker_open: dict[str, BrokerOrder] = {}
        for order in orders_r.data:
            broker_open[order.id] = order
            for leg in order.legs:
                broker_open[leg.id] = leg

        with self._session_factory() as session:
            # 1. Store/update rows for every open order at the broker.
            for order in orders_r.data:
                known = session.execute(
                    select(Order.id).where(Order.alpaca_order_id == order.id)
                ).scalar_one_or_none()
                matched_client = (
                    session.execute(
                        select(Order.id).where(
                            Order.client_order_id == order.client_order_id
                        )
                    ).scalar_one_or_none()
                    if order.client_order_id
                    else None
                )
                if known is None and matched_client is None:
                    # An order we never recorded: a timed-out write that
                    # Alpaca accepted, or an order placed outside the agent.
                    report.mismatches.append(
                        f"unknown order {order.symbol} {order.side} {order.qty}"
                    )
                    record_event(
                        session,
                        self.clock,
                        level="critical",
                        event_type="reconciliation",
                        details={
                            "kind": "unknown_order",
                            "symbol": order.symbol,
                            "order": order.id,
                            "client_order_id": order.client_order_id,
                        },
                    )
                self._store_broker_order(session, order, None)
                report.orders_synced += 1

            # 2. DB orders still open but no longer open at the broker.
            db_open = session.execute(
                select(Order).where(Order.status.in_(OPEN_ORDER_STATUSES))
            ).scalars().all()
            for row in db_open:
                if row.alpaca_order_id in broker_open:
                    continue
                fetched = await self.broker.get_order(row.alpaca_order_id)
                if fetched.error is not None or fetched.data is None:
                    report.errors.append(
                        f"order {row.alpaca_order_id}: "
                        f"{fetched.error or 'not found'}"
                    )
                    continue
                self._store_broker_order(session, fetched.data, row.decision_id)
                report.orders_synced += 1
            session.flush()

            # 3. An entry fill opens the DB position (qty/avg are then
            #    corrected from the broker position below).
            report.fills_recorded += self._open_positions_from_fills(session)

            # 4. Position-level sync: adopt unknown, adopt mismatched,
            #    close positions the broker no longer reports.
            db_positions = session.execute(
                select(Position).where(Position.closed_at.is_(None))
            ).scalars().all()
            known_symbols = {p.symbol for p in db_positions}
            for position in db_positions:
                bp = broker_positions.get(position.symbol)
                if bp is None:
                    self._close_position(
                        session, position, report, exit_reason="broker_close"
                    )
                    continue
                if bp.qty != position.qty or (
                    bp.avg_entry_price != position.avg_entry_price
                ):
                    report.mismatches.append(
                        f"{position.symbol}: broker {bp.qty} vs db {position.qty}"
                    )
                    position.qty = bp.qty
                    position.avg_entry_price = bp.avg_entry_price
                    position.pending_ack = True
                    record_event(
                        session,
                        self.clock,
                        level="critical",
                        event_type="reconciliation",
                        details={
                            "kind": "qty_mismatch",
                            "symbol": position.symbol,
                            "broker_qty": bp.qty,
                            "db_qty": position.qty,
                        },
                    )
            new_stops: dict[str, Decimal] = {}
            for symbol, bp in broker_positions.items():
                if symbol not in known_symbols:
                    placed = await self._adopt_position(session, bp, report)
                    if placed is not None:
                        new_stops[symbol] = placed

            # 5. Every open position needs a live stop at the broker.
            open_stops: dict[str, Decimal] = {}
            for order in broker_open.values():
                if order.side == "sell" and order.order_type in (
                    "stop",
                    "stop_limit",
                ):
                    open_stops[order.symbol] = open_stops.get(
                        order.symbol, Decimal(0)
                    ) + order.qty
            for symbol, qty in new_stops.items():
                open_stops[symbol] = open_stops.get(symbol, Decimal(0)) + qty
            for position in session.execute(
                select(Position).where(Position.closed_at.is_(None))
            ).scalars().all():
                covered = open_stops.get(position.symbol, Decimal(0))
                if covered >= position.qty:
                    continue
                placed = await self._place_protective_stop(
                    session, position, broker_positions.get(position.symbol), report
                )
                if placed is not None:
                    open_stops[position.symbol] = (
                        open_stops.get(position.symbol, Decimal(0)) + placed
                    )

            # 6. Entries that never filled within the configured timeout.
            timeout = int(
                self.config.get("execution", "entry_timeout_minutes", default=10)
            )
            now = self.clock.now()
            stale = session.execute(
                select(Order).where(
                    Order.leg == "entry",
                    Order.status.in_(OPEN_ORDER_STATUSES),
                )
            ).scalars().all()
            for row in stale:
                created = row.created_at.replace(tzinfo=UTC)
                if (now - created).total_seconds() < timeout * 60:
                    continue
                result = await self.broker.cancel_order(row.alpaca_order_id)
                if result.error is None:
                    row.status = "canceled"
                    report.entries_cancelled.append(row.symbol)
                    record_event(
                        session,
                        self.clock,
                        level="info",
                        event_type="entry_timeout",
                        details={
                            "symbol": row.symbol,
                            "order": row.alpaca_order_id,
                            "message": "entry not filled",
                        },
                    )
                else:
                    report.errors.append(
                        f"cancel {row.alpaca_order_id}: {result.error}"
                    )
            session.commit()
        return report

    def _open_positions_from_fills(self, session: Session) -> int:
        """Open a DB position for each entry fill that has none yet.

        Sell fills are handled by the broker-position sync: Alpaca is the
        source of truth for qty and whether a position still exists.
        """
        opened = 0
        filled_buys = session.execute(
            select(Order).where(
                Order.side == "buy",
                Order.status.in_(("filled", "partially_filled")),
            )
        ).scalars().all()
        for order in filled_buys:
            fill_rows = session.execute(
                select(Fill).where(Fill.order_id == order.id)
            ).scalars().all()
            if not fill_rows:
                continue
            open_pos = session.execute(
                select(Position).where(
                    Position.symbol == order.symbol,
                    Position.closed_at.is_(None),
                )
            ).scalar_one_or_none()
            if open_pos is not None:
                continue
            latest = fill_rows[-1]
            legs = session.execute(
                select(Order).where(
                    Order.parent_alpaca_order_id == order.alpaca_order_id
                )
            ).scalars().all()
            stop = next((o.stop_price for o in legs if o.leg == "stop"), None)
            target = next(
                (o.limit_price for o in legs if o.leg == "target"), None
            )
            session.add(
                Position(
                    symbol=order.symbol,
                    origin="agent",
                    pending_ack=False,
                    entry_decision_id=order.decision_id,
                    qty=sum(f.qty for f in fill_rows),
                    avg_entry_price=latest.price,
                    current_stop_price=stop,
                    target_price=target,
                    opened_at=latest.filled_at,
                )
            )
            opened += 1
        session.flush()
        return opened

    def _record_day_trade(
        self, session: Session, position: Position, closed_at: datetime
    ) -> None:
        """A round trip on the same NY session is a day trade (PDT)."""
        opened_date = _ny_date(position.opened_at)
        closed_date = _ny_date(closed_at)
        if opened_date != closed_date:
            return
        existing = session.execute(
            select(DayTrade).where(
                DayTrade.date == _naive_utc(closed_at),
                DayTrade.symbol == position.symbol,
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                DayTrade(
                    date=_naive_utc(closed_at),
                    symbol=position.symbol,
                    order_ids=[],
                )
            )

    def _close_position(
        self,
        session: Session,
        position: Position,
        report: ReconcileReport,
        *,
        exit_reason: str,
    ) -> None:
        """The broker no longer reports this position; find its exit fill."""
        fills = session.execute(
            select(Fill, Order)
            .join(Order, Fill.order_id == Order.id)
            .where(
                Order.symbol == position.symbol,
                Order.side == "sell",
                Fill.filled_at >= position.opened_at,
            )
            .order_by(Fill.filled_at.desc())
        ).all()
        qty_closed = position.qty
        exit_price: Decimal | None = None
        if fills:
            exit_reason = fills[0][1].leg  # newest sell leg wins
            total_qty = sum(f.qty for f, _ in fills)
            if total_qty > 0:
                exit_price = sum(
                    (f.price * f.qty for f, _ in fills), Decimal(0)
                ) / total_qty
        raw = (
            (exit_price - position.avg_entry_price) * qty_closed
            if exit_price is not None
            else None
        )
        slip_pct = Decimal(
            str(
                self.config.get(
                    "performance", "slippage_pct_per_side", default=0.05
                )
            )
        )
        adjusted = None
        if raw is not None and exit_price is not None:
            notional = (
                position.avg_entry_price * qty_closed
                + exit_price * qty_closed
            )
            adjusted = raw - notional * slip_pct / 100
        position.realized_pnl_raw = raw
        position.realized_pnl_adjusted = adjusted
        position.exit_reason = exit_reason
        position.closed_at = _naive_utc(self.clock.now())
        position.qty = 0
        position.deferred_exit = None
        report.mismatches.append(f"{position.symbol}: closed at broker")
        self._record_day_trade(session, position, self.clock.now())

    async def _adopt_position(
        self,
        session: Session,
        bp: BrokerPosition,
        report: ReconcileReport,
    ) -> Decimal | None:
        """A position at the broker the agent never opened: adopt it.

        It gets a standalone gtc stop at current − adopted_stop_atr × the
        1h ATR (or −protective_stop_fallback_pct without ATR), no target,
        and blocks new entries until acknowledged. Returns the stop qty.
        """
        price = bp.current_price or bp.avg_entry_price
        atr_1h = self._atr_1h(bp.symbol)
        atr_mult = Decimal(
            str(self.config.get("risk", "adopted_stop_atr", default=3))
        )
        fallback_pct = Decimal(
            str(self.config.get("risk", "protective_stop_fallback_pct", default=3))
        )
        if atr_1h is not None and atr_1h > 0:
            stop = _cents(price - atr_mult * atr_1h)
            basis = f"{atr_mult} × 1h ATR {atr_1h}"
        else:
            stop = _cents(price * (1 - fallback_pct / 100))
            basis = f"{fallback_pct}% below price (ATR unavailable)"
        position = Position(
            symbol=bp.symbol,
            origin="adopted",
            pending_ack=True,
            entry_decision_id=None,
            qty=bp.qty,
            avg_entry_price=bp.avg_entry_price,
            current_stop_price=stop,
            target_price=None,
            opened_at=_naive_utc(self.clock.now()),
        )
        session.add(position)
        session.flush()
        result = await self.broker.submit_stop_order(
            symbol=bp.symbol,
            qty=bp.qty,
            stop_price=stop,
            client_order_id=self._client_id("protect", position.id, bp.symbol),
        )
        placed: Decimal | None = None
        if result.error is not None or result.data is None:
            report.errors.append(f"adopted stop {bp.symbol}: {result.error}")
            record_event(
                session,
                self.clock,
                level="critical",
                event_type="protective_stop_failed",
                details={"symbol": bp.symbol, "error": result.error},
            )
        else:
            self._store_broker_order(session, result.data, None, leg="stop")
            placed = Decimal(bp.qty)
        report.adopted.append(bp.symbol)
        report.mismatches.append(f"{bp.symbol}: adopted {bp.qty} shares")
        record_event(
            session,
            self.clock,
            level="critical",
            event_type="reconciliation",
            details={
                "kind": "adopted_position",
                "symbol": bp.symbol,
                "qty": bp.qty,
                "avg_entry": str(bp.avg_entry_price),
                "stop": str(stop),
                "stop_basis": basis,
            },
        )
        return placed

    async def _place_protective_stop(
        self,
        session: Session,
        position: Position,
        bp: BrokerPosition | None,
        report: ReconcileReport,
    ) -> Decimal | None:
        """A known position has no live stop: place one, then alert.

        Returns the protected qty, or None when the write failed.
        """
        stop = position.current_stop_price
        if stop is None:
            fallback = Decimal(
                str(
                    self.config.get(
                        "risk", "protective_stop_fallback_pct", default=3
                    )
                )
            )
            mark = (
                bp.current_price
                if bp is not None and bp.current_price is not None
                else position.avg_entry_price
            )
            stop = _cents(mark * (1 - fallback / 100))
            position.current_stop_price = stop
        result = await self.broker.submit_stop_order(
            symbol=position.symbol,
            qty=position.qty,
            stop_price=stop,
            client_order_id=self._client_id("protect", position.id, position.symbol),
        )
        if result.error is not None or result.data is None:
            report.errors.append(
                f"protective stop {position.symbol}: {result.error}"
            )
            record_event(
                session,
                self.clock,
                level="critical",
                event_type="protective_stop_failed",
                details={
                    "symbol": position.symbol,
                    "error": result.error,
                },
            )
            return None
        self._store_broker_order(session, result.data, None, leg="stop")
        report.protective_stops.append(position.symbol)
        record_event(
            session,
            self.clock,
            level="critical",
            event_type="protective_stop",
            details={
                "symbol": position.symbol,
                "qty": position.qty,
                "stop": str(stop),
            },
        )
        return Decimal(position.qty)

    def _atr_1h(self, symbol: str) -> Decimal | None:
        now = self.clock.now()
        today = now.astimezone(NY_TZ).date()
        sessions = self.market_data.recent_sessions(today, 5)
        bars = [
            b
            for b in self.market_data.get_1h_bars_range(symbol, sessions)
            if b.end <= now
        ]
        if len(bars) < 15:
            return None
        period = int(self.config.get("signals", "atr_period", default=14))
        return atr(bars, period)

    # ------------------------------------------------------------------
    # decision execution

    def begin_cycle(self) -> None:
        self._orders_this_cycle = 0

    def _gate_context(
        self,
        session: Session,
        account: BrokerAccount | None,
        symbol: str,
        days_to_earnings: int | None,
        earnings_known: bool,
    ) -> GateContext:
        open_positions = session.execute(
            select(Position).where(Position.closed_at.is_(None))
        ).scalars().all()
        open_entries = session.execute(
            select(Order.symbol).where(
                Order.leg == "entry",
                Order.status.in_(OPEN_ORDER_STATUSES),
            )
        ).scalars().all()
        since = self.clock.now().astimezone(NY_TZ).date()
        # Day trades over the last 5 trading days, inclusive of today.
        days = self.market_data.recent_sessions(since, 5)
        if days:
            day_trades = session.execute(
                select(func.count(DayTrade.id)).where(
                    DayTrade.date >= _naive_utc(days[0].open)
                )
            ).scalar_one()
        else:
            day_trades = 0
        closed = session.execute(
            select(Position)
            .where(
                Position.closed_at.is_not(None),
                Position.realized_pnl_raw.is_not(None),
            )
            .order_by(Position.closed_at.desc())
        ).scalars().all()
        streak = 0
        for p in closed:
            if (p.realized_pnl_raw or Decimal(0)) < 0:
                streak += 1
            else:
                break
        account = account or self.state.account
        equity = account.equity if account else Decimal(0)
        return GateContext(
            symbol=symbol,
            equity=equity,
            cash=account.cash if account else Decimal(0),
            last_equity=account.last_equity if account else Decimal(0),
            peak_equity=max(self.state.peak_equity, equity),
            open_positions=list(open_positions),
            open_entry_symbols=frozenset(open_entries),
            day_trades_5d=int(day_trades),
            losing_streak=streak,
            shock_brake_1h=self.state.shock_brake_1h,
            shock_brake_session=self.state.shock_brake_session,
            days_to_earnings=days_to_earnings,
            earnings_known=earnings_known,
            kill_switch_on=self.state.kill_switch_on,
            pending_ack=self.state.pending_ack(session) or self._reconcile_failed,
            orders_this_cycle=self._orders_this_cycle,
        )

    async def apply_decision(
        self,
        session: Session,
        decision: Decision,
        item: SymbolEval,
        position: Position | None,
        quote: Quote | None,
        scheduled_ny: datetime,
        *,
        account: BrokerAccount | None,
        earnings_known: bool,
        force_exit_reason: str | None = None,
    ) -> None:
        """Apply one stored decision: gates, sizing, then the order."""
        action = decision.final_action
        output = decision.model_output or {}
        if force_exit_reason is not None and position is not None:
            action = "exit"

        if action == "enter" and position is None:
            await self._enter(
                session, decision, item, quote, account, earnings_known
            )
        elif action == "tighten_stop" and position is not None:
            new_stop = output.get("stop_price")
            await self._tighten(
                session, decision, position, Decimal(str(new_stop))
                if new_stop is not None
                else None
            )
        elif action == "exit" and position is not None:
            await self._exit(
                session,
                decision,
                position,
                reason=force_exit_reason or "model exit",
            )
        elif force_exit_reason is not None:
            decision.gates_result = {"forced": force_exit_reason, "sent": False}

    async def _enter(
        self,
        session: Session,
        decision: Decision,
        item: SymbolEval,
        quote: Quote | None,
        account: BrokerAccount | None,
        earnings_known: bool,
    ) -> None:
        output = decision.model_output or {}
        stop = output.get("stop_price")
        target = output.get("target_price")
        ctx = self._gate_context(
            session,
            account,
            decision.symbol,
            item.snapshot.days_to_earnings,
            earnings_known,
        )
        price = quote.ask if quote else item.price
        if price is None or stop is None or target is None:
            decision.gates_result = {
                "allowed": False,
                "blocked_by": "missing_data",
                "checks": [],
            }
            decision.final_action = "blocked"
            return
        entry = Decimal(str(price))
        result = evaluate_entry_gates(
            ctx, entry=entry, stop=Decimal(str(stop)), config=self.config
        )
        decision.gates_result = result.to_json()
        if not result.allowed:
            decision.final_action = "blocked"
            if result.blocked_by == "drawdown_kill":
                await self.activate_kill_switch(
                    reason="drawdown kill gate", automatic=True
                )
            if result.blocked_by == "order_sanity":
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type="order_sanity",
                    details=result.to_json(),
                )
            session.flush()
            return
        assert result.sizing is not None
        qty = result.sizing.shares

        # Spread and entry limit from the IEX quote.
        max_spread = Decimal(
            str(self.config.get("execution", "max_spread_pct", default=0.15))
        )
        offset = Decimal(
            str(
                self.config.get("execution", "entry_limit_offset_pct", default=0.05)
            )
        )
        mid = (quote.ask + quote.bid) / 2  # type: ignore[union-attr]
        spread_pct = (quote.ask - quote.bid) / mid * 100  # type: ignore[union-attr]
        if spread_pct > max_spread:
            decision.gates_result = result.to_json() | {
                "allowed": False,
                "blocked_by": "spread",
                "detail": f"spread {spread_pct:.3f}% > {max_spread}%",
            }
            decision.final_action = "blocked"
            session.flush()
            return
        limit = _cents(quote.ask * (1 + offset / 100))  # type: ignore[union-attr]

        self._orders_this_cycle += 1
        client_id = self._client_id("entry", decision.id, decision.symbol)
        submitted = await self.broker.submit_bracket_entry(
            symbol=decision.symbol,
            qty=qty,
            limit_price=limit,
            stop_price=_cents(Decimal(str(stop))),
            target_price=_cents(Decimal(str(target))),
            client_order_id=client_id,
        )
        if submitted.error is not None or submitted.data is None:
            # Never resent: reconciliation decides what Alpaca did.
            recovered = await self.broker.get_order_by_client_id(client_id)
            if recovered.error is None and recovered.data is not None:
                self._store_broker_order(
                    session, recovered.data, decision.id
                )
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type="order_recovered",
                    details={
                        "symbol": decision.symbol,
                        "client_order_id": client_id,
                    },
                )
            else:
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type="order_submit_failed",
                    details={
                        "symbol": decision.symbol,
                        "client_order_id": client_id,
                        "error": submitted.error,
                    },
                )
            session.flush()
            return
        self._store_broker_order(session, submitted.data, decision.id)

    async def _tighten(
        self,
        session: Session,
        decision: Decision,
        position: Position,
        new_stop: Decimal | None,
    ) -> None:
        """Move the stop leg in place; stops only move in the position's favour."""
        if (
            new_stop is None
            or position.current_stop_price is None
            or new_stop <= position.current_stop_price
        ):
            return
        stop_row = session.execute(
            select(Order).where(
                Order.symbol == position.symbol,
                Order.side == "sell",
                Order.leg == "stop",
                Order.status.in_(OPEN_ORDER_STATUSES),
            )
        ).scalar_one_or_none()
        if stop_row is None:
            return
        result = await self.broker.replace_stop(
            stop_row.alpaca_order_id, stop_price=_cents(new_stop)
        )
        if result.error is not None or result.data is None:
            # Never resent; reconciliation will repair or alert.
            record_event(
                session,
                self.clock,
                level="critical",
                event_type="order_submit_failed",
                details={
                    "symbol": position.symbol,
                    "order": stop_row.alpaca_order_id,
                    "error": result.error,
                },
            )
            session.flush()
            return
        stop_row.status = "replaced"
        self._store_broker_order(session, result.data, stop_row.decision_id, leg="stop")
        position.current_stop_price = _cents(new_stop)
        session.flush()

    async def _exit(
        self,
        session: Session,
        decision: Decision,
        position: Position,
        *,
        reason: str,
    ) -> None:
        """Agent exit: cancel the open legs, confirm, then market sell.

        A position is never closed on the day it opened (PDT): the exit is
        recorded as deferred and the next session's first cycle re-asks.
        """
        now = self.clock.now()
        if _ny_date(position.opened_at) == _ny_date(now):
            position.deferred_exit = {
                "at": now.isoformat(),
                "reason": "day_trade_rule",
                "detail": reason,
            }
            decision.final_action = "deferred"
            record_event(
                session,
                self.clock,
                level="info",
                event_type="exit_deferred",
                details={
                    "symbol": position.symbol,
                    "position_id": position.id,
                    "decision_id": decision.id,
                    "reason": "day_trade_rule",
                },
            )
            session.flush()
            return

        # Cancel every open leg for the symbol and confirm before selling.
        legs = session.execute(
            select(Order).where(
                Order.symbol == position.symbol,
                Order.side == "sell",
                Order.status.in_(OPEN_ORDER_STATUSES),
            )
        ).scalars().all()
        for leg in legs:
            cancelled = await self.broker.cancel_order(leg.alpaca_order_id)
            if cancelled.error is not None:
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type="order_submit_failed",
                    details={
                        "symbol": position.symbol,
                        "order": leg.alpaca_order_id,
                        "error": cancelled.error,
                    },
                )
                session.flush()
                return
            confirmed = await self.broker.get_order(leg.alpaca_order_id)
            if confirmed.data is not None:
                leg.status = confirmed.data.status
            if confirmed.data is not None and confirmed.data.is_open:
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type="leg_cancel_unconfirmed",
                    details={
                        "symbol": position.symbol,
                        "order": leg.alpaca_order_id,
                    },
                )
                session.flush()
                return
        session.flush()

        ny_now = now.astimezone(NY_TZ)
        today_session = self.calendar.session_for(ny_now.date())
        regular_hours = (
            today_session is not None
            and today_session.open <= ny_now <= today_session.close
        )
        self._orders_this_cycle += 1
        sold = await self.broker.submit_market_sell(
            symbol=position.symbol,
            qty=position.qty,
            market_on_open=not regular_hours,
            client_order_id=self._client_id("exit", position.id, position.symbol),
        )
        if sold.error is not None or sold.data is None:
            record_event(
                session,
                self.clock,
                level="critical",
                event_type="order_submit_failed",
                details={
                    "symbol": position.symbol,
                    "error": sold.error,
                },
            )
            session.flush()
            return
        self._store_broker_order(session, sold.data, decision.id, leg="exit")
        session.flush()
