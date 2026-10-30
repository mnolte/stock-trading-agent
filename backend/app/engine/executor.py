"""The executor: turns stored decisions into broker orders and keeps the
database a mirror of the Alpaca paper account (docs/SPEC.md, Alpaca
integration and Risk management).

Everything here follows three hard rules: every position carries a live
broker-side stop, no order is ever resent automatically (after a write
error reconciliation decides what happened), and all money is Decimal.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.base import Broker
from app.broker.client_id import client_order_id, order_instance, order_kind
from app.broker.models import (
    OPEN_ORDER_STATUSES,
    BrokerAccount,
    BrokerOrder,
    BrokerPosition,
    TradeUpdateInfo,
)
from app.data.aggregate import session_bars
from app.data.cache import _naive_utc
from app.data.models import CalendarDay, Quote
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import (
    Approval,
    Decision,
    Event,
    Fill,
    Order,
    Position,
    Run,
)
from app.decide.output import decision_trade_prices
from app.engine.state import ExecutionState, record_event
from app.logging_config import ev
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, Clock
from app.notifications.service import Notifier, NullNotifier
from app.risk.gates import GateCheck, GateContext, evaluate_entry_gates, resolve_sectors
from app.risk.math import (
    WorkingEntry,
    drawdown_pct,
    session_pnl_pct,
    trade_risk,
)
from app.signals.indicators import atr
from app.signals.service import SignalService, SymbolEval, trading_days_until

if TYPE_CHECKING:
    from app.config import AppConfig

CENT = Decimal("0.01")

logger = logging.getLogger(__name__)
log_orders = logging.getLogger("app.broker.orders")
log_gates = logging.getLogger("app.risk")
log_reconcile = logging.getLogger("app.broker.reconcile")


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


def spy_session_moves(
    market_data: MarketDataService,
    sessions: list[CalendarDay],
    now: datetime,
    today: date,
) -> tuple[Decimal | None, Decimal | None]:
    """SPY's change on the last closed 1h bar and since today's open.

    These are the shock-brake display inputs, shared with the dashboard
    risk budget card: ``(last_1h_pct, since_open_pct)``, each None when
    today's bars are missing (market closed, no 1h bar finished yet).
    Only complete 1h bars of today's session count — a partial bar of the
    hour still running is not "the last 1h bar", and yesterday's bar is
    not today's move.
    """
    todays = [
        b
        for b in market_data.get_1h_bars_range("SPY", sessions)
        if b.complete
        and b.end <= now
        and b.start.astimezone(NY_TZ).date() == today
    ]
    if not todays:
        return None, None
    last = todays[-1]
    move_1h = (
        (last.close - last.open) / last.open * 100 if last.open > 0 else None
    )
    move_session = (
        (todays[-1].close - todays[0].open) / todays[0].open * 100
        if todays[0].open > 0
        else None
    )
    return move_1h, move_session


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
        notify: Callable[[set[str]], None] | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.broker = broker
        self.market_data = market_data
        self.signals = signals
        self.calendar = calendar
        self.config = config
        self.clock = clock
        self.state = state
        self._session_factory = session_factory or get_session_maker()
        # Pushes WebSocket update topics to the GUI; a no-op in tests.
        self._notify = notify or (lambda topics: None)
        # The notification service; a no-op until main wires it in.
        self._notifier = notifier or NullNotifier()
        self._orders_this_cycle = 0
        self._order_seq = 0
        # Fills reconciliation recorded while their stream update could
        # still be in flight (order id -> fill time); re-checked every
        # pass against the stream-health grace window.
        self._fills_awaiting_update: dict[str, datetime] = {}
        # Entry orders that reached a final state with fills since the
        # last drain — the "trade opened" notification fires once, on
        # the final state, never on the first partial fill (docs/SPEC.md,
        # Notifications — Trade opened or closed).
        self._entry_finals: list[str] = []
        self._opened_notified: set[str] = set()
        # Fills still settling (docs/SPEC.md, Alpaca integration): a
        # deferred quantity check is re-run ~fill_settle_seconds after
        # an entry order goes final.
        self._settle_recheck_needed = False
        self._settle_recheck_scheduled = False
        # Alpaca enforces client_order_id uniqueness account-wide
        # forever; the seq restarts each process, so a per-process
        # nonce keeps ids unique across restarts and DB rebuilds.
        self._order_nonce = secrets.token_hex(3)
        self._reconcile_failed = False
        # Symbols found flat at the broker during this reconcile pass;
        # later steps must not ask Alpaca for the same 404 again.
        self._flat_symbols: set[str] = set()
        self._reconcile_lock = asyncio.Lock()
        # Set by main: closes every shadow's virtual positions on flatten.
        self.on_flatten: Callable[[], object] = lambda: None

    # ------------------------------------------------------------------
    # account + brakes

    async def refresh_account(self) -> BrokerAccount | None:
        result = await self.broker.get_account()
        if result.error is not None or result.data is None:
            return None
        account = result.data
        self.state.account = account
        self.state.account_fetched_at = self.clock.now()
        self.state.reset_session_if_new_day()
        limit = Decimal(str(self.config.get("risk", "daily_loss_limit_pct", default=2)))
        pnl_pct = session_pnl_pct(account.equity, account.last_equity)
        if pnl_pct <= -limit and not self.state.daily_loss_hit:
            with self._session_factory() as session:
                record_event(
                    session, self.clock, level="warning", event_type="daily_loss_limit",
                    details={"session_date": str(self.state.session_date), "pnl_pct": str(pnl_pct)},
                )
                session.commit()
            self.state.daily_loss_hit = True
            self._notifier.notify(
                "daily_loss_limit",
                f"session P&L {pnl_pct:.2f}% ≤ −{limit}% — no new entries today",
                link="/",
            )
        if account.equity > self.state.peak_equity:
            self.state.peak_equity = account.equity
        return account

    async def update_session_brakes(self, *, include_1h: bool = False) -> None:
        """Shock brake, daily loss and drawdown kill from fresh data.

        The since-open leg of the shock brake uses the real-time SPY IEX
        quote — position checks and cycles both refresh it. The 1h leg
        reads the last *complete* session-aligned 1h bar and only runs on
        decision cycles (``include_1h``), right after the bar top-up.
        """
        self.state.reset_session_if_new_day()
        account = await self.refresh_account()
        if account is not None:
            kill_pct = Decimal(
                str(self.config.get("risk", "drawdown_kill_pct", default=8))
            )
            if (
                self.state.peak_equity > 0
                and drawdown_pct(account.equity, self.state.peak_equity)
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

        # Shock brake: the quote is the real-time price; today's open
        # comes from the session's first 5m bar, topped up here so the
        # early position checks work before the first cycle runs.
        now = self.clock.now()
        today = now.astimezone(NY_TZ).date()
        session = self.calendar.session_for(today)
        if session is None:
            return
        bars = await self.market_data.sources.bars.fetch_5m_bars(
            ["SPY"], session.open, now
        )
        if bars.error is None and bars.data:
            self.market_data.bars_cache.store(session_bars(bars.data, [session]))
        todays_5m = self.market_data.get_5m_bars(
            "SPY", session.open, session.close
        )
        session_open = todays_5m[0].open if todays_5m else None

        quotes = await self.market_data.sources.quotes.fetch_quotes(["SPY"])
        quote = (
            quotes.data.get("SPY")
            if quotes.error is None and quotes.data
            else None
        )
        move_session = (
            (quote.last - session_open) / session_open * 100
            if quote is not None
            and quote.last.is_finite()
            and quote.last > 0
            and session_open is not None
            and session_open > 0
            else None
        )
        move_1h: Decimal | None = None
        if include_1h:
            closed_1h = [
                b
                for b in self.market_data.get_1h_bars("SPY", session)
                if b.complete and b.end <= now
            ]
            if closed_1h and closed_1h[-1].open > 0:
                move_1h = (
                    (closed_1h[-1].close - closed_1h[-1].open)
                    / closed_1h[-1].open
                    * 100
                )

        if move_1h is None and move_session is None:
            return
        shock_1h = Decimal(
            str(self.config.get("risk", "shock_brake_1h_pct", default=1.5))
        )
        shock_session = Decimal(
            str(self.config.get("risk", "shock_brake_session_pct", default=2.5))
        )
        if move_1h is not None and move_1h <= -shock_1h:
            if not self.state.shock_brake_1h:
                self._notifier.notify(
                    "shock_brake",
                    f"SPY {move_1h:.2f}% in the last hour — "
                    "no new entries",
                    link="/",
                )
            self.state.shock_brake_1h = True
        if move_session is not None and move_session <= -shock_session:
            if not self.state.shock_brake_session:
                self._notifier.notify(
                    "shock_brake",
                    f"SPY {move_session:.2f}% since the open — "
                    "no new entries",
                    link="/",
                )
            self.state.shock_brake_session = True

    # ------------------------------------------------------------------
    # kill switch

    async def activate_kill_switch(
        self,
        *,
        reason: str,
        automatic: bool,
        flatten: bool = False,
        new_run_required: bool = False,
    ) -> None:
        """Turn the switch on and log it. ``flatten`` is stop & flatten."""
        self.state.kill_switch_on = True
        self.state.kill_switch_since = self.clock.now()
        self.state.kill_switch_reason = reason
        self.state.kill_switch_auto = automatic
        self.state.new_run_required = (
            self.state.new_run_required or new_run_required
        )
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
                    "new_run_required": new_run_required,
                },
            )
            session.commit()
        self._notify({"banners", "header", "approvals"})

    async def pause(self) -> None:
        """Pause new entries (docs/SPEC.md, Paused at startup).

        Positions keep being reviewed and their stops stay in place; only
        entries are blocked. Manual pauses clear the startup flag, so the
        blue "Started paused" banner only ever marks a fresh start.
        """
        if self.state.paused:
            return
        self.state.paused = True
        self.state.paused_at_startup = False
        self.state.paused_since = self.clock.now()
        with self._session_factory() as session:
            record_event(
                session,
                self.clock,
                level="info",
                event_type="pause",
                details={"on": True, "reason": "manual"},
            )
            session.commit()
        self._notify({"banners", "header"})

    async def resume_from_pause(self) -> None:
        """Resume entries after a pause: one click, no reason needed."""
        if self.state.new_run_required:
            raise ValueError("start a new run first")
        if not self.state.paused:
            return
        self.state.paused = False
        self.state.paused_at_startup = False
        self.state.paused_since = None
        with self._session_factory() as session:
            record_event(
                session,
                self.clock,
                level="info",
                event_type="pause",
                details={"on": False, "reason": "manual"},
            )
            session.commit()
        self._notify({"banners", "header", "approvals"})

    async def resume(self, reason: str) -> None:
        """Resume after a kill switch. The typed reason is stored."""
        if self.state.new_run_required:
            raise ValueError("start a new run first")
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
        self._notify({"banners", "header"})

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
        blocked: list[str] = []
        # Confirm the cancels landed before selling: a stop leg still live
        # at the broker could fill after the market sell and leave an
        # unintended short. A symbol with unconfirmed orders is not sold.
        confirm_failed = False
        still_open = await self.broker.get_open_orders()
        blocked_symbols: set[str] = set()
        if still_open.error is not None:
            confirm_failed = True
            errors.append(f"could not confirm cancels: {still_open.error}")
        else:
            blocked_symbols = {
                o.symbol
                for o in still_open.data
                if o.is_open or any(leg.is_open for leg in o.legs)
            }
        positions = await self.broker.get_positions()
        if positions.error is not None:
            errors.append(f"could not read positions: {positions.error}")
        else:
            for pos in positions.data:
                if confirm_failed or pos.symbol in blocked_symbols:
                    blocked.append(pos.symbol)
                    errors.append(
                        f"{pos.symbol}: sell held back, cancel not confirmed"
                    )
                    with self._session_factory() as session:
                        record_event(
                            session,
                            self.clock,
                            level="critical",
                            event_type="flatten_sell_blocked",
                            details={
                                "symbol": pos.symbol,
                                "qty": pos.qty,
                                "reason": "cancel not confirmed",
                            },
                            affects="order",
                        )
                        session.commit()
                    continue
                self._orders_this_cycle += 1
                result = await self.broker.submit_market_sell(
                    symbol=pos.symbol,
                    qty=pos.qty,
                    market_on_open=not regular_hours,
                    client_order_id=self._client_id("exit", 0, pos.symbol),
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
                            affects="order",
                        )
                        session.commit()
                else:
                    sold.append(pos.symbol)
                    with self._session_factory() as session:
                        self._store_broker_order(
                            session, result.data, None, leg="exit"
                        )
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
                    "blocked": blocked,
                    "market_on_open": not regular_hours,
                    "errors": errors,
                },
                # A clean flatten is a deliberate action; a failed leg is
                # an order error that counts for Readiness.
                affects="order" if errors or rejected or blocked else "none",
            )
            session.commit()
        await self.reconcile()
        # Shadows are flattened virtually too (docs/SPEC.md, Virtual
        # trading); the hook only touches shadow tables, never the broker.
        try:
            self.on_flatten()
        except Exception:  # noqa: BLE001
            logger.exception("shadow flatten failed")
        self._notify({"banners", "header", "positions", "orders"})
        return {
            "cancelled_orders": cancelled.data,
            "sold": sold,
            "blocked": blocked,
            "market_on_open": not regular_hours,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # supervised-mode approvals

    def pending_approvals(self, session: Session) -> list[Approval]:
        return list(
            session.execute(
                select(Approval)
                .where(Approval.status == "pending")
                .order_by(Approval.id)
            ).scalars().all()
        )

    def _supersede_pending(self, session: Session, symbol: str) -> None:
        """At most one pending approval per symbol: a newer one replaces it."""
        for old in self.pending_approvals(session):
            if old.symbol == symbol:
                old.status = "expired"
                old.reason = "replaced by a newer approval"
                old.resolved_at = _naive_utc(self.clock.now())

    def expire_pending_approvals(self) -> int:
        """Pending approvals die at the start of the next full cycle."""
        with self._session_factory() as session:
            pending = self.pending_approvals(session)
            now = _naive_utc(self.clock.now())
            for approval in pending:
                approval.status = "expired"
                approval.reason = "expired at the next cycle"
                approval.resolved_at = now
            session.commit()
            count = len(pending)
        if count:
            self._notify({"approvals", "decisions"})
        return count

    def reject_approval(self, approval_id: int, reason: str | None) -> bool:
        with self._session_factory() as session:
            approval = session.get(Approval, approval_id)
            if approval is None or approval.status != "pending":
                return False
            approval.status = "rejected"
            approval.reason = reason or "rejected"
            approval.resolved_at = _naive_utc(self.clock.now())
            session.commit()
        self._notify({"approvals", "decisions"})
        return True

    def _resolve_approval(
        self, approval: Approval, status: str, reason: str | None
    ) -> None:
        approval.status = status
        approval.reason = reason
        approval.resolved_at = _naive_utc(self.clock.now())

    def _code_check_failure(
        self,
        *,
        atr_multiple: Decimal | None,
        reward_to_risk: Decimal | None,
    ) -> str | None:
        """The stop-ATR and reward-to-risk checks from the decision validator."""
        atr_min = Decimal(
            str(self.config.get("decision", "stop_atr_min", default=1.5))
        )
        atr_max = Decimal(
            str(self.config.get("decision", "stop_atr_max", default=4.0))
        )
        min_rr = Decimal(
            str(self.config.get("decision", "min_reward_risk", default=1.5))
        )
        if atr_multiple is None:
            return "stop distance vs ATR unavailable"
        if atr_multiple < atr_min or atr_multiple > atr_max:
            return (
                f"stop {atr_multiple:.2f} × ATR outside "
                f"{atr_min}–{atr_max} × ATR"
            )
        if reward_to_risk is None or reward_to_risk < min_rr:
            return f"reward to risk {reward_to_risk or 0:.2f} below {min_rr}"
        return None

    def _quote_provenance(self, quote: Quote) -> dict[str, str | bool | None]:
        dev_pct = Decimal(
            str(self.config.get("execution", "quote_max_deviation_pct"))
        )
        return quote.provenance(quote.quote_plausible(dev_pct))

    async def _fresh_entry_check(
        self,
        *,
        symbol: str,
        stop: Decimal,
        target: Decimal,
        atr_1h: Decimal | None,
        room_levels: list[dict[str, Any]] | None = None,
    ) -> tuple[Quote | None, Decimal | None, str | None]:
        """The "Fresh quote" gate right before an entry order is sent.

        Fetches the latest IEX quote, rejects a missing or older last
        trade (``price_stale``) or one more than
        ``execution.quote_max_deviation_pct`` off the last closed bar
        (``price_suspect``), and on a usable market price re-runs the
        spread check on a plausible quote, recomputes the limit price
        and re-runs the code checks (stop distance, reward to risk) from
        the new entry. Returns ``(quote, limit, reason)`` — on failure
        ``limit`` is None and ``reason`` names the failing check.
        Automatic entries and Approve share this step; exits and stop
        moves never wait on it (docs/SPEC.md, Entries).
        """
        quotes = await self.market_data.sources.quotes.fetch_quotes([symbol])
        quote = (
            quotes.data.get(symbol)
            if quotes.error is None and quotes.data
            else None
        )
        if quote is None:
            return None, None, "price_stale"
        now = self.clock.now()
        bar = self.market_data.bars_cache.latest(symbol, now - timedelta(minutes=5))
        dev_pct = Decimal(
            str(self.config.get("execution", "quote_max_deviation_pct"))
        )
        reason = quote.price_reason(
            now=now,
            max_age_seconds=float(self.config.get("execution", "quote_max_age_seconds")),
            bar_close=bar.close if bar is not None else None,
            max_deviation_pct=dev_pct,
        )
        if reason is not None:
            return quote, None, reason
        reference = quote.stop_reference(
            bar.close if bar else None, atr_1h, entry=True,
            max_deviation_pct=dev_pct,
        )
        if reference is None:
            return quote, None, "reference_missing"
        if quote.quote_plausible(dev_pct):
            max_spread = Decimal(
                str(self.config.get("execution", "max_spread_pct", default=0.15))
            )
            mid = (quote.ask + quote.bid) / 2
            spread_pct = (quote.ask - quote.bid) / mid * 100
            if spread_pct > max_spread:
                return quote, None, f"spread {spread_pct:.3f}% > {max_spread}%"
        offset = Decimal(
            str(
                self.config.get("execution", "entry_limit_offset_pct", default=0.05)
            )
        )
        limit = _cents(quote.last * (1 + offset / 100))
        risk = trade_risk(limit, stop, target, atr_1h)
        fail = self._code_check_failure(
            atr_multiple=risk.atr_multiple,
            reward_to_risk=risk.reward_to_risk,
        )
        if fail is not None:
            return quote, None, fail
        market_risk = trade_risk(reference, stop, target, atr_1h)
        minimum = Decimal(str(self.config.get("decision", "stop_atr_min")))
        if market_risk.atr_multiple is None or market_risk.atr_multiple < minimum:
            return quote, None, "stop_market_distance"
        # decision.v3: room is checked again from the final limit — a
        # fresh limit that leaves less than min_room_atr fails the Fresh
        # quote gate with reason ``room`` (docs/SPEC.md, Decision v3).
        if room_levels is not None and atr_1h is not None and atr_1h > 0:
            from app.decide.v3 import room_recheck_failure, v3_config

            room_fail = room_recheck_failure(
                room_levels, limit, atr_1h, v3_config(self.config)
            )
            if room_fail is not None:
                return quote, None, room_fail
        return quote, limit, None

    async def approve(self, approval_id: int) -> dict[str, object]:
        """Re-check gates, code checks and spread on a fresh quote, then submit.

        Every check runs again in code at approve time; a failure rejects the
        approval with the reason stored and returned.
        """
        if self.state.kill_switch_on:
            return {"ok": False, "error": "kill switch is on"}
        with self._session_factory() as session:
            approval = session.get(Approval, approval_id)
            if approval is None or approval.status != "pending":
                return {"ok": False, "error": "approval is not pending"}
            if self.state.paused:
                return {"ok": False, "error": "new entries are paused"}

            decision = session.get(Decision, approval.decision_id)
            # All risk gates again with fresh equity + the fresh ask.
            account = await self.refresh_account()
            if account is None:
                self._resolve_approval(approval, "rejected", "account_unavailable")
                if decision is not None:
                    decision.final_action = "blocked"
                    decision.gates_result = {"allowed": False, "blocked_by": "account_unavailable"}
                session.commit()
                return {"ok": False, "error": "account_unavailable"}
            # Fresh earnings, not the decision-time snapshot: a report
            # announced since then must still block the entry.
            today = self.clock.now().astimezone(NY_TZ).date()
            earnings_result = await self.market_data.refresh_earnings(
                today, today + timedelta(days=90)
            )
            earnings_known = earnings_result.error is None
            next_date = self.signals.next_earnings(
                [approval.symbol], today
            ).get(approval.symbol)
            days_to_earnings = (
                trading_days_until(self.calendar, today, next_date)
                if next_date is not None
                else None
            )
            # The shared "Fresh quote" gate: latest IEX quote, freshness,
            # spread, limit recompute and code checks — the same function
            # automatic entries use (docs/SPEC.md, Entries).
            quote, limit, fail = await self._fresh_entry_check(
                symbol=approval.symbol,
                stop=approval.stop_price,
                target=approval.target_price,
                atr_1h=self._atr_1h(approval.symbol),
                **(
                    {
                        "room_levels": cast(
                            "list[dict[str, Any]] | None",
                            (decision.levels or {}).get("levels"),
                        )
                    }
                    if decision is not None
                    and decision.prompt_version == "decision.v3"
                    else {}
                ),
            )
            if fail is not None:
                self._resolve_approval(approval, "rejected", fail)
                if decision is not None:
                    decision.final_action = "blocked"
                    decision.gates_result = {
                        "allowed": False, "blocked_by": "fresh_quote",
                        "checks": [{"name": "fresh_quote", "passed": False, "detail": fail}],
                        "quote": self._quote_provenance(quote) if quote is not None else None,
                    }
                session.commit()
                self._notify({"approvals", "decisions"})
                return {"ok": False, "error": fail}
            assert quote is not None and limit is not None  # noqa: S101 - internal invariant
            ctx = self._gate_context(
                session,
                account,
                approval.symbol,
                days_to_earnings,
                earnings_known,
            )
            result = evaluate_entry_gates(
                ctx,
                entry=limit,
                stop=approval.stop_price,
                config=self.config,
            )
            ev(
                log_gates, logging.DEBUG, "gates.eval",
                f"{approval.symbol}: {'allowed' if result.allowed else 'blocked'}",
                symbol=approval.symbol,
                checks={
                    c.name: {"passed": c.passed, "detail": c.detail}
                    for c in result.checks
                },
                sizing=(
                    result.sizing.__dict__ if result.sizing else None
                ),
            )
            if not result.allowed:
                ev(
                    log_orders, logging.INFO, "gates.blocked",
                    f"{approval.symbol}: {result.blocked_by or 'gate'}",
                    symbol=approval.symbol,
                )
            if decision is not None:
                decision.gates_result = result.to_json() | {
                    "entry_limit": str(limit), "quote": self._quote_provenance(quote),
                }
            if not result.allowed:
                blocked = result.blocked_by or "gate"
                detail = next(
                    (c.detail for c in result.checks if not c.passed), ""
                )
                fail = f"{blocked}: {detail}".rstrip(": ")
                self._resolve_approval(approval, "rejected", fail)
                if decision is not None:
                    decision.final_action = "blocked"
                session.commit()
                if result.blocked_by == "drawdown_kill":
                    await self.activate_kill_switch(reason="drawdown kill gate", automatic=True)
                self._notify({"approvals", "decisions"})
                return {"ok": False, "error": fail}
            assert result.sizing is not None  # noqa: S101 - internal invariant
            qty = result.sizing.shares

            self._orders_this_cycle += 1
            client_id = self._client_id(
                "entry", approval.decision_id, approval.symbol
            )
            session.commit()
            # Guard the submit→store window: shutdown's grace wait holds
            # until this slot closes, so an accepted answer is never lost
            # between the broker call and its DB row (hard rule 16).
            import contextlib

            from app.shutdown import current as _shutdown_current

            _stack = contextlib.ExitStack()
            _mgr = _shutdown_current()
            if _mgr is not None:
                _stack.enter_context(_mgr.order_slot())
            submitted = await self.broker.submit_bracket_entry(
                symbol=approval.symbol,
                qty=qty,
                limit_price=limit,
                stop_price=approval.stop_price,
                target_price=approval.target_price,
                client_order_id=client_id,
            )
            if submitted.error is not None or submitted.data is None:
                ev(
                    log_orders, logging.ERROR, "orders.submit_failed",
                    f"{approval.symbol}: order submit failed: {submitted.error}",
                    symbol=approval.symbol, order=client_id,
                )
                # Never resent; reconciliation decides what Alpaca did.
                recovered = await self.broker.get_order_by_client_id(client_id)
                if recovered.error is None and recovered.data is not None:
                    self._store_broker_order(
                        session, recovered.data, approval.decision_id
                    )
                self._resolve_approval(
                    approval,
                    "rejected",
                    f"order submit failed: {submitted.error}",
                )
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type="order_submit_failed",
                    details={
                        "symbol": approval.symbol,
                        "client_order_id": client_id,
                        "error": submitted.error,
                    },
                )
                session.commit()
                _stack.close()
                self._notify({"approvals", "decisions", "banners"})
                return {
                    "ok": False,
                    "error": f"order submit failed: {submitted.error}",
                }
            self._store_broker_order(
                session, submitted.data, approval.decision_id
            )
            ev(
                log_orders, logging.INFO, "orders.submitted",
                f"{approval.symbol}: bracket entry x{qty} "
                f"limit {limit} stop {approval.stop_price} "
                f"target {approval.target_price} ({client_id})",
                symbol=approval.symbol, order=client_id,
            )
            approval.qty = qty
            approval.limit_price = limit
            self._resolve_approval(approval, "approved", None)
            session.commit()
            _stack.close()
        self._notify({"approvals", "decisions", "positions", "orders", "header"})
        return {"ok": True}

    # ------------------------------------------------------------------
    # paper account reset

    async def paper_reset(self) -> dict[str, object]:
        """Step 1 of the reset: kill switch on, close all, cancel all."""
        await self.activate_kill_switch(
            reason="paper reset", automatic=False, flatten=True
        )
        result = await self.broker.close_all_positions(cancel_orders=True)
        closed: list[str] = []
        errors: list[str] = []
        if result.error is not None:
            errors.append(str(result.error))
        else:
            closed = [o.symbol for o in (result.data or [])]
        with self._session_factory() as session:
            record_event(
                session,
                self.clock,
                level="critical",
                event_type="paper_reset",
                details={
                    "closed": closed,
                    "errors": errors,
                },
                # A clean reset is a deliberate action; a failed close is
                # an order error that counts for Readiness.
                affects="order" if errors else "none",
            )
            session.commit()
        await self.reconcile()
        self._notify(
            {"banners", "header", "positions", "orders", "watchlist", "decisions"}
        )
        starting = Decimal(
            str(self.config.get("performance", "starting_equity", default=10000))
        )
        return {
            "closed": closed,
            "errors": errors,
            "message": (
                f"Positions closed. Now reset the balance to "
                f"${starting:,.0f} in the Alpaca dashboard, "
                "then press Start new run."
            ),
        }

    async def start_new_run(self) -> dict[str, object]:
        """Step 2 of the reset: verify the account, then start a new run."""
        target = Decimal(
            str(
                self.config.get(
                    "performance", "starting_equity", default=10000
                )
            )
        )
        checks: list[str] = []
        positions = await self.broker.get_positions()
        if positions.error is not None:
            checks.append(f"could not read positions: {positions.error}")
        elif positions.data:
            checks.append(
                "open positions: "
                + ", ".join(p.symbol for p in positions.data)
            )
        open_orders = await self.broker.get_open_orders()
        if open_orders.error is not None:
            checks.append(f"could not read orders: {open_orders.error}")
        elif open_orders.data:
            checks.append(f"{len(open_orders.data)} open order(s) at Alpaca")
        account = await self.broker.get_account()
        equity = account.data.equity if account.data is not None else None
        if account.error is not None:
            checks.append(f"could not read the account: {account.error}")
        elif equity is not None:
            tolerance = target * Decimal("0.01")
            if abs(equity - target) > tolerance:
                checks.append(
                    f"equity {equity} is not {target} (±1%); "
                    "reset the balance in the Alpaca dashboard first"
                )
        if checks:
            return {"ok": False, "failed": checks}
        assert equity is not None  # noqa: S101 - internal invariant
        spy_close = self._latest_spy_close()
        with self._session_factory() as session:
            run = Run(
                started_at=_naive_utc(self.clock.now()),
                starting_equity=equity,
                spy_close=spy_close,
            )
            session.add(run)
            session.flush()
            run_id = run.id
            record_event(
                session,
                self.clock,
                level="info",
                event_type="run_started",
                details={
                    "run_id": run_id,
                    "starting_equity": str(equity),
                    "spy_close": str(spy_close) if spy_close else None,
                },
            )
            session.commit()
        # Restart peak equity, losing streak and the daily loss baseline.
        self.state.peak_equity = equity
        self.state.daily_loss_hit = False
        self.state.new_run_required = False
        self.state.run_started_at = _naive_utc(self.clock.now())
        self._notify({"header", "performance", "banners"})
        return {"ok": True, "run_id": run_id}

    def _latest_spy_close(self) -> Decimal | None:
        today = self.clock.now().astimezone(NY_TZ).date()
        sessions = self.market_data.recent_sessions(today, 2)
        bars = self.market_data.get_daily_bars("SPY", sessions)
        return bars[-1].close if bars else None

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
        return client_order_id(
            self.config.env.sa_instance_id,
            kind,
            ref_id,
            symbol,
            self._order_nonce,
            self._order_seq,
        )

    def _mismatch(
        self, session: Session, report: ReconcileReport, message: str,
        details: dict[str, object], position: Position | None = None,
        event_type: str = "reconciliation",
    ) -> None:
        report.mismatches.append(message)
        if position is not None:
            position.pending_ack = True
        record_event(session, self.clock, level="critical", event_type=event_type, details=details)
        ev(log_reconcile, logging.CRITICAL, "reconcile.mismatch", message)
        self._notify({"positions", "banners", "header"})

    def _check_foreign_instance(
        self, session: Session, order: BrokerOrder
    ) -> str | None:
        """Returns the foreign instance id the first time it is seen.

        An order or fill carrying another instance's client id means a
        second agent copy trades this account (docs/SPEC.md, Two copies on
        one account): critical event, kill switch on, banner. Ids without
        an ``sa-`` prefix and our own ids never trip it. Once per order.
        """
        other = order_instance(order.client_order_id)
        own = "".join(
            c for c in str(self.config.env.sa_instance_id) if c.isalnum()
        )
        if other is None or other == own:
            return None
        existing = session.execute(
            select(func.count(Event.id)).where(
                Event.event_type == "another_instance",
                Event.details["order"].as_string() == order.id,
            )
        ).scalar_one()
        if existing:
            return None
        message = (
            f"Another instance is trading this Alpaca account: order "
            f"{order.id[:8]}… carries instance id {other}"
            + (f", not {own}" if own else "")
            + ". Kill switch turned on. Stop the other copy, then acknowledge."
        )
        self._mismatch(
            session, ReconcileReport(), message,
            {"kind": "another_instance", "order": order.id,
             "client_order_id": order.client_order_id, "foreign_instance": other,
             "own_instance": own or None, "message": message},
            event_type="another_instance",
        )
        return other

    @staticmethod
    def _leg_for(order: BrokerOrder, parent: BrokerOrder | None) -> str:
        if order.side == "buy":
            return "entry"
        if order_kind(order.client_order_id) in ("exit", "breach"):
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
        new_fills: list[tuple[str, datetime | None]] | None = None,
    ) -> Order:
        """Insert or update the DB row for one broker order (idempotent)."""
        row = session.execute(
            select(Order).where(Order.alpaca_order_id == order.id)
        ).scalar_one_or_none()
        was_open = row is not None and row.status in OPEN_ORDER_STATUSES
        was_known = row is not None
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
            if row.parent_alpaca_order_id is None and parent is not None:
                # First seen flat (a released leg), now nested under its
                # parent — restore the link.
                row.parent_alpaca_order_id = parent.id
        if order.status not in OPEN_ORDER_STATUSES and row.active_until is None:
            row.active_until = _naive_utc(order.updated_at or self.clock.now())
        if order.filled_qty > 0 and order.filled_avg_price is not None:
            self._record_fills(session, row, order, new_fills=new_fills)
        if (
            row.leg == "entry"
            and order.status not in OPEN_ORDER_STATUSES
            and order.filled_qty > 0
            and (was_open or not was_known)
            and order.id not in self._opened_notified
        ):
            # Open → final (or first seen final) with fills: queue the
            # one "trade opened" notification; drained after positions
            # open so the quantity is settled.
            self._entry_finals.append(order.id)
            self._opened_notified.add(order.id)
            self._settle_recheck_needed = True
        for leg_order in order.legs:
            self._store_broker_order(
                session, leg_order, decision_id, parent=order,
                new_fills=new_fills,
            )
        return row

    def _record_fills(
        self,
        session: Session,
        row: Order,
        order: BrokerOrder,
        new_fills: list[tuple[str, datetime | None]] | None = None,
    ) -> None:
        """Record fill rows for any broker filled qty not yet stored.

        ``new_fills`` (reconciliation only) collects every fill first seen
        here — a fill the stream update never announced (docs/SPEC.md,
        Alpaca integration — Stream health).
        """
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
        recorded_value = session.execute(
            select(Fill).where(Fill.order_id == row.id)
        ).scalars().all()
        price = (order.filled_avg_price * order.filled_qty - sum(
            (fill.price * fill.qty for fill in recorded_value), Decimal(0)
        )) / delta
        session.add(
            Fill(
                order_id=row.id,
                qty=delta,
                price=price,
                filled_at=_naive_utc(order.filled_at or self.clock.now()),
                # Slippage vs the intended price; shown separately in the GUI.
                slippage=price - intended,
            )
        )
        if new_fills is not None:
            new_fills.append((order.id, order.filled_at))

    # ------------------------------------------------------------------
    # reconciliation

    def _market_in_session(self) -> bool:
        """Whether New York is inside today's session right now."""
        ny_now = self.clock.now().astimezone(NY_TZ)
        session = self.calendar.session_for(ny_now.date())
        return (
            session is not None and session.open <= ny_now < session.close
        )

    async def on_stream_failure(self, reason: str) -> None:
        """A stream health failure; entering ``degraded`` records the
        critical event (docs/SPEC.md, Alpaca integration — Stream
        health). A reconnect outage only degrades during a session —
        overnight drops are routine."""
        if (
            reason.startswith("stream reconnect outage")
            and not self._market_in_session()
        ):
            ev(
                logger, logging.INFO, "stream.outage_after_hours",
                f"{reason} outside session — stream stays healthy",
            )
            return
        with self._session_factory() as session:
            self._mark_stream_failure(session, reason)
            session.commit()

    def _mark_stream_failure(self, session: Session, reason: str) -> None:
        """One stream-health failure; entering ``degraded`` records the
        critical event (its notification goes through the event sink).
        Repeat failures extend the window without a new alert."""
        now = self.clock.now()
        already = self.state.stream_degraded
        self.state.stream_failures += 1
        self.state.stream_last_failure_at = now
        clear_min = int(
            self.config.get("execution", "stream_degraded_minutes", default=30)
        )
        self.state.stream_degraded_until = now + timedelta(minutes=clear_min)
        if already:
            return
        self.state.stream_degraded_since = now
        record_event(
            session,
            self.clock,
            level="critical",
            event_type="stream_degraded",
            details={
                "reason": reason,
                "failures": self.state.stream_failures,
                "message": "Trade-updates stream degraded",
            },
        )
        self._notify({"banners", "header"})

    def _stream_grace(self) -> timedelta:
        return timedelta(
            seconds=int(
                self.config.get(
                    "execution", "stream_update_grace_seconds", default=120
                )
            )
        )

    async def on_trade_update(self, update: TradeUpdateInfo) -> None:
        """A trade-updates event: mark the order, then reconcile the rest."""
        # An update arriving inside the grace window answers a fill
        # reconciliation had already recorded (stream health).
        pending_fill = self._fills_awaiting_update.get(update.order.id)
        if (
            pending_fill is not None
            and self.clock.now() - pending_fill <= self._stream_grace()
        ):
            del self._fills_awaiting_update[update.order.id]
        foreign: str | None = None
        with self._session_factory() as session:
            foreign = self._check_foreign_instance(session, update.order)
            session.commit()
            row = session.execute(
                select(Order).where(Order.alpaca_order_id == update.order.id)
            ).scalar_one_or_none()
            if row is not None:
                was_open = row.status in OPEN_ORDER_STATUSES
                row.status = update.order.status
                if update.order.filled_at is not None:
                    row.filled_at = _naive_utc(update.order.filled_at)
                if (
                    row.leg == "entry"
                    and was_open
                    and update.order.status not in OPEN_ORDER_STATUSES
                    and update.order.filled_qty > 0
                    and update.order.id not in self._opened_notified
                ):
                    # Entry order final with fills: the "opened"
                    # notification goes out once, from the drain below.
                    self._entry_finals.append(update.order.id)
                    self._opened_notified.add(update.order.id)
                    self._settle_recheck_needed = True
                if (
                    update.order.status not in OPEN_ORDER_STATUSES
                    and row.active_until is None
                ):
                    # A terminal update (filled, replaced, ...) ends the
                    # order's live window — a row left without
                    # active_until counts as live for every later bar.
                    row.active_until = _naive_utc(
                        update.order.updated_at
                        or update.timestamp
                        or self.clock.now()
                    )
                recorded = session.execute(
                    select(func.coalesce(func.sum(Fill.qty), 0)).where(Fill.order_id == row.id)
                ).scalar_one()
                self._record_fills(session, row, update.order)
                session.flush()
                delta = update.order.filled_qty - int(recorded)
                if row.side == "sell" and delta > 0 and self._own_exit(session, row):
                    position = session.execute(
                        select(Position).where(
                            Position.symbol == row.symbol, Position.closed_at.is_(None)
                        )
                    ).scalar_one_or_none()
                    if position is not None and _naive_utc(
                        update.order.filled_at or self.clock.now()
                    ) >= position.opened_at:
                        if delta >= position.qty:
                            self._close_position(
                                session, position, ReconcileReport(), exit_reason=row.leg,
                                explained=True,
                            )
                        else:
                            position.qty -= delta
                            position.exit_filled_qty += delta
                session.commit()
        if foreign is not None:
            await self.activate_kill_switch(
                reason=(
                    f"another instance ({foreign}) is trading "
                    "this Alpaca account"
                ),
                automatic=True,
            )
        self._notify({"positions", "orders", "header"})
        await self.reconcile()

    async def reconcile(self) -> ReconcileReport:
        """Compare broker state with the database; Alpaca wins on mismatch."""
        async with self._reconcile_lock:
            await self._verify_broker_guard()
            report = await self._reconcile()
        self.state.last_reconcile_at = self.clock.now()
        self.state.last_reconcile_ok = report.ok and not report.mismatches
        return report

    async def _verify_broker_guard(self) -> None:
        """Re-check the account guard when it was unconfirmed, once per
        reconcile (each position check and cycle). A confirm lifts the
        flag and acknowledges the warning banner (docs/SPEC.md,
        Protective stops)."""
        if self.state.broker_guard_confirmed:
            return
        read = await self.broker.get_account_config()
        cfg = read.data or {}
        if not (
            read.error is None
            and cfg.get("no_shorting") is True
            and str(cfg.get("max_margin_multiplier")) in ("1", "1.0")
        ):
            return
        self.state.broker_guard_confirmed = True
        with self._session_factory() as session:
            stale = session.execute(
                select(Event).where(
                    Event.event_type == "broker_guard_unconfirmed",
                    Event.acknowledged_at.is_(None),
                )
            ).scalars().all()
            for event in stale:
                event.acknowledged_at = _naive_utc(self.clock.now())
            session.commit()
        ev(
            logger, logging.INFO, "executor.broker_guard_confirmed",
            "broker-side guard confirmed (no_shorting, 1x margin)",
        )

    async def _reconcile(self) -> ReconcileReport:
        report = ReconcileReport()
        self._flat_symbols = set()
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
        # The GUI's stale fallback serves these when Alpaca stops
        # answering (docs/SPEC.md — Broker reads from the GUI).
        self.state.last_broker_positions = positions_r.data
        broker_open: dict[str, BrokerOrder] = {}
        for order in orders_r.data:
            broker_open[order.id] = order
            for leg in order.legs:
                broker_open[leg.id] = leg

        with self._session_factory() as session:
            # Fills first seen by this pass (their stream update never
            # arrived) feed the stream-health check after step 2.
            new_fills: list[tuple[str, datetime | None]] = []
            # 1. Store/update rows for every open order at the broker.
            foreign_instance: str | None = None
            for order in orders_r.data:
                seen = self._check_foreign_instance(session, order)
                foreign_instance = foreign_instance or seen
                for leg_order in order.legs:
                    seen = self._check_foreign_instance(session, leg_order)
                    foreign_instance = foreign_instance or seen
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
                    self._mismatch(
                        session, report,
                        f"unknown order {order.symbol} {order.side} {order.qty}",
                        details={
                            "kind": "unknown_order",
                            "symbol": order.symbol,
                            "order": order.id,
                            "client_order_id": order.client_order_id,
                        },
                    )
                self._store_broker_order(session, order, None, new_fills=new_fills)
                report.orders_synced += 1

            # 2. DB orders still open but no longer open at the broker.
            db_open = session.execute(
                select(Order).where(Order.status.in_(OPEN_ORDER_STATUSES))
            ).scalars().all()
            for row in db_open:
                if row.alpaca_order_id in broker_open:
                    continue
                # Hard rule 13: step 1's writes are durable before this call.
                session.commit()
                fetched = await self.broker.get_order(row.alpaca_order_id)
                if fetched.error is not None or fetched.data is None:
                    report.errors.append(
                        f"order {row.alpaca_order_id}: "
                        f"{fetched.error or 'not found'}"
                    )
                    continue
                self._store_broker_order(
                    session, fetched.data, row.decision_id, new_fills=new_fills
                )
                report.orders_synced += 1
                # Live orders the open list missed — Alpaca's open filter
                # never returns `held`, so a bracket's working stop leg
                # only surfaces here. They still count as coverage.
                if fetched.data.is_open:
                    broker_open[fetched.data.id] = fetched.data
                    for leg in fetched.data.legs:
                        if leg.is_open:
                            broker_open[leg.id] = leg
            session.flush()

            # Stream health (docs/SPEC.md, Alpaca integration): a fill
            # this pass recorded for the first time is one the stream
            # never delivered. It marks the stream degraded once the
            # update is provably late — past the grace window — while a
            # fresher fill waits for the next pass. Fills from before
            # this run started are offline fills, not stream failures.
            grace = self._stream_grace()
            now = self.clock.now()
            run_started = self.state.run_started_at
            if run_started is not None and run_started.tzinfo is None:
                run_started = run_started.replace(tzinfo=UTC)
            for order_id, fill_at in new_fills:
                fill_time = fill_at or now
                if run_started is not None and fill_time < run_started:
                    continue
                if fill_time + grace <= now:
                    self._mark_stream_failure(
                        session,
                        f"no trade update within "
                        f"{int(grace.total_seconds())}s of a fill "
                        f"(order {order_id})",
                    )
                else:
                    self._fills_awaiting_update[order_id] = fill_time
            for order_id, fill_time in list(self._fills_awaiting_update.items()):
                if fill_time + grace <= now:
                    del self._fills_awaiting_update[order_id]
                    self._mark_stream_failure(
                        session,
                        f"no trade update within "
                        f"{int(grace.total_seconds())}s of a fill "
                        f"(order {order_id})",
                    )

            # 3. An entry fill opens the DB position (qty/avg are then
            #    corrected from the broker position below).
            report.fills_recorded += self._open_positions_from_fills(session)
            self._notify_opened(session)

            # 4. Position-level sync: adopt unknown, adopt mismatched,
            #    close positions the broker no longer reports.
            db_positions = session.execute(
                select(Position).where(Position.closed_at.is_(None))
            ).scalars().all()
            known_symbols = {p.symbol for p in db_positions}
            for position in db_positions:
                bp = broker_positions.get(position.symbol)
                if bp is None:
                    self._flat_symbols.add(position.symbol)
                    session.commit()
                    history = await self.broker.get_filled_orders(
                        position.symbol, position.opened_at.replace(tzinfo=UTC)
                    )
                    if history.error is not None:
                        report.ok = False
                        report.errors.append(f"exit history {position.symbol}: {history.error}")
                        ev(log_reconcile, logging.WARNING, "reconcile.history_failed",
                           f"exit history unavailable for {position.symbol}: {history.error}")
                        continue
                    for order in history.data:
                        self._store_broker_order(session, order, None)
                    session.flush()
                    sold = session.execute(select(Fill, Order).join(
                        Order, Fill.order_id == Order.id
                    ).where(
                        Order.symbol == position.symbol, Order.side == "sell",
                        Fill.filled_at >= position.opened_at,
                    )).all()
                    explained = sum(
                        fill.qty for fill, order in sold if self._own_exit(session, order)
                    ) >= position.qty + position.exit_filled_qty
                    self._close_position(
                        session, position, report, exit_reason="broker_close",
                        explained=explained, offline=explained,
                    )
                    continue
                if bp.qty != position.qty or (
                    bp.avg_entry_price != position.avg_entry_price
                ):
                    if self._settling(session, position.symbol, now):
                        # Fills still settling: the broker position can
                        # lag the last prints by a second or two — log,
                        # don't compare (docs/SPEC.md, Fills still
                        # settling).
                        ev(
                            log_reconcile, logging.INFO,
                            "reconcile.settling",
                            f"{position.symbol}: quantity check deferred "
                            f"— fills still settling "
                            f"(broker {bp.qty}, db {position.qty})",
                            symbol=position.symbol,
                        )
                    else:
                        self._mismatch(
                            session, report,
                            f"{position.symbol}: broker {bp.qty} vs db {position.qty}",
                            {"kind": "qty_mismatch", "symbol": position.symbol,
                             "broker_qty": bp.qty, "db_qty": position.qty}, position,
                        )
                        position.qty = bp.qty
                        position.avg_entry_price = bp.avg_entry_price
            # Sell-side stops already open at the broker, per symbol.
            # Needed before adoption (a position may already be covered
            # by a stop placed before the database was rebuilt) and for
            # step 5's coverage check.
            open_stops: dict[str, Decimal] = {}
            broker_stops: dict[str, list[BrokerOrder]] = {}
            for order in broker_open.values():
                if (
                    order.is_open
                    and order.side == "sell"
                    and order.order_type in (
                        "stop",
                        "stop_limit",
                    )
                ):
                    open_stops[order.symbol] = open_stops.get(
                        order.symbol, Decimal(0)
                    ) + order.qty
                    broker_stops.setdefault(order.symbol, []).append(order)

            for symbol, bp in broker_positions.items():
                if symbol not in known_symbols:
                    # Hard rule 13.
                    session.commit()
                    placed = await self._adopt_position(
                        session, bp, report, broker_stops.get(symbol, [])
                    )
                    if placed is not None:
                        open_stops[symbol] = (
                            open_stops.get(symbol, Decimal(0)) + placed
                        )

            # 5. Every open position needs a live stop at the broker.
            for position in session.execute(
                select(Position).where(Position.closed_at.is_(None))
            ).scalars().all():
                covered = open_stops.get(position.symbol, Decimal(0))
                if covered >= position.qty:
                    continue
                # Hard rule 13.
                session.commit()
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
                # Hard rule 13: earlier steps' writes are durable before
                # this call.
                session.commit()
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
        if foreign_instance is not None:
            # Another agent copy is trading this account: stop everything.
            await self.activate_kill_switch(
                reason=(
                    f"another instance ({foreign_instance}) is trading "
                    "this Alpaca account"
                ),
                automatic=True,
            )
        ev(
            log_reconcile, logging.INFO, "reconcile.result",
            f"reconcile: {report.orders_synced} orders synced, "
            f"{report.fills_recorded} fills, "
            f"{len(report.mismatches)} mismatches, "
            f"{len(report.entries_cancelled)} entries cancelled"
            + ("" if report.ok else f" — errors: {'; '.join(report.errors)}"),
            orders=report.orders_synced,
            fills=report.fills_recorded,
            mismatches=len(report.mismatches),
        )
        if report.mismatches:
            ev(
                log_reconcile, logging.CRITICAL, "reconcile.mismatch",
                "; ".join(report.mismatches[:5]),
            )
        self._notify({"positions", "orders", "banners", "header"})
        if self._settle_recheck_needed:
            self._settle_recheck_needed = False
            try:
                self._schedule_settle_recheck()
            except RuntimeError:
                # No running loop (unit tests driving _reconcile pieces).
                self._settle_recheck_scheduled = False
        return report

    def _fill_settle_seconds(self) -> int:
        return int(
            self.config.get("execution", "fill_settle_seconds", default=10)
        )

    def _settling(
        self, session: Session, symbol: str, now: datetime
    ) -> bool:
        """Fills still settling (docs/SPEC.md, Alpaca integration): the
        quantity comparison defers while an entry order for the symbol
        is working, or a fill landed inside the settle window — the
        broker position can lag the last print by a second or two."""
        working = session.execute(
            select(Order.id).where(
                Order.symbol == symbol,
                Order.leg == "entry",
                Order.status.in_(OPEN_ORDER_STATUSES),
            ).limit(1)
        ).scalar_one_or_none()
        if working is not None:
            return True
        latest = session.execute(
            select(func.max(Fill.filled_at))
            .select_from(Fill)
            .join(Order, Fill.order_id == Order.id)
            .where(Order.symbol == symbol)
        ).scalar()
        if latest is None:
            return False
        cutoff = _naive_utc(
            now - timedelta(seconds=self._fill_settle_seconds())
        )
        return latest > cutoff

    def _schedule_settle_recheck(self) -> None:
        """Re-run reconciliation once, ~fill_settle_seconds after an
        entry order went final — the broker's position view can lag
        the last prints (docs/SPEC.md — Fills still settling)."""
        if self._settle_recheck_scheduled:
            return
        self._settle_recheck_scheduled = True
        from app.spawn import spawn

        async def recheck() -> None:
            try:
                await asyncio.sleep(self._fill_settle_seconds())
                await self.reconcile()
            finally:
                self._settle_recheck_scheduled = False

        spawn(recheck(), "reconcile-settle-recheck")

    def _notify_opened(self, session: Session) -> None:
        """One "trade opened" notification per final entry order.

        Fires when the order is final — filled, or cancelled/expired
        with a partial fill — never on the first partial print. The
        filled quantity comes from the recorded fill rows: "opened
        PG: 12 shares @ 162.85", or "8 of 12 shares" when the rest was
        cancelled (docs/SPEC.md, Notifications).
        """
        for alpaca_order_id in list(self._entry_finals):
            self._entry_finals.remove(alpaca_order_id)
            row = session.execute(
                select(Order).where(
                    Order.alpaca_order_id == alpaca_order_id
                )
            ).scalar_one_or_none()
            if row is None:
                continue
            fills = session.execute(
                select(Fill).where(Fill.order_id == row.id)
            ).scalars().all()
            if not fills:
                continue
            qty = sum(f.qty for f in fills)
            avg = sum((f.price * f.qty for f in fills), Decimal(0)) / qty
            partial = (
                f"{qty} of {row.qty} shares"
                if qty < row.qty
                else f"{qty} shares"
            )
            self._notifier.notify(
                "trade",
                f"opened {row.symbol}: {partial} @ "
                f"{avg.quantize(Decimal('0.01'))}",
                link="/positions",
            )

    def _open_positions_from_fills(self, session: Session) -> int:
        """Open a DB position for each entry fill that has none yet.

        A buy fill is "covered" once a position for the symbol is still
        open or closed at/after it — the fill's shares lived in that
        position. Without this guard every reconcile would re-open a
        position that already closed and re-close it, duplicating
        realized P&L. A fill older than every position's close (a round
        trip the DB never recorded) still gets its position once, which
        step 4 immediately closes; afterwards the fill is covered.
        Sell fills are handled by the broker-position sync: Alpaca is
        the source of truth for qty and whether a position still exists.
        """
        opened = 0
        filled_buys = session.execute(
            select(Order)
            .join(Fill, Fill.order_id == Order.id)
            .where(Order.side == "buy")
            .distinct()
        ).scalars().all()
        for order in filled_buys:
            fill_rows = session.execute(
                select(Fill)
                .where(Fill.order_id == order.id)
                .order_by(Fill.filled_at, Fill.id)
            ).scalars().all()
            if not fill_rows:
                continue
            filled_qty = sum(f.qty for f in fill_rows)
            avg_price = sum(
                (f.price * f.qty for f in fill_rows), Decimal(0)
            ) / filled_qty
            first_fill = fill_rows[0].filled_at
            open_pos = session.execute(
                select(Position).where(
                    Position.symbol == order.symbol,
                    Position.closed_at.is_(None),
                )
            ).scalar_one_or_none()
            if open_pos is not None:
                # Partial fills keep arriving after the position opened:
                # grow qty/avg from the fill rows (broker-derived) so step
                # 4 doesn't flag a false qty mismatch before Alpaca's
                # position view catches up.
                sold = session.execute(select(Fill, Order).join(
                    Order, Fill.order_id == Order.id
                ).where(
                    Order.symbol == order.symbol, Order.side == "sell",
                    Fill.filled_at >= open_pos.opened_at,
                )).all()
                remaining = filled_qty - sum(
                    f.qty for f, o in sold if self._own_exit(session, o)
                )
                if remaining > open_pos.qty:
                    open_pos.qty = remaining
                    open_pos.avg_entry_price = avg_price
                continue
            covered = session.execute(
                select(Position.id)
                .where(
                    Position.symbol == order.symbol,
                    Position.closed_at >= first_fill,
                )
                .limit(1)
            ).scalar_one_or_none()
            if covered is not None:
                # Already processed into a position that has since closed;
                # re-creating it would duplicate the realized P&L.
                continue
            legs = session.execute(
                select(Order).where(
                    Order.parent_alpaca_order_id == order.alpaca_order_id
                )
            ).scalars().all()
            stop = next((o.stop_price for o in legs if o.leg == "stop"), None)
            target = next(
                (o.limit_price for o in legs if o.leg == "target"), None
            )
            if stop is None or target is None:
                # Released legs carry no parent link: once the entry is
                # closed they arrive flat, so the position's stop and
                # target are found as the symbol's open sell legs.
                siblings = session.execute(
                    select(Order).where(
                        Order.symbol == order.symbol,
                        Order.side == "sell",
                        Order.leg.in_(("stop", "target")),
                        Order.status.in_(OPEN_ORDER_STATUSES),
                    )
                ).scalars().all()
                stop = stop or next(
                    (o.stop_price for o in siblings if o.leg == "stop"), None
                )
                target = target or next(
                    (o.limit_price for o in siblings if o.leg == "target"),
                    None,
                )
            session.add(
                Position(
                    symbol=order.symbol,
                    origin="agent",
                    pending_ack=False,
                    entry_decision_id=order.decision_id,
                    qty=filled_qty,
                    avg_entry_price=avg_price,
                    initial_stop=stop,
                    current_stop_price=stop,
                    target_price=target,
                    opened_at=first_fill,
                )
            )
            opened += 1
        session.flush()
        return opened

    def _own_exit(self, session: Session, order: Order) -> bool:
        client_id = order.client_order_id
        if order_kind(client_id) is None and order.parent_alpaca_order_id:
            parent = session.execute(select(Order).where(
                Order.alpaca_order_id == order.parent_alpaca_order_id
            )).scalar_one_or_none()
            client_id = parent.client_order_id if parent else None
        own = "".join(c for c in str(self.config.env.sa_instance_id) if c.isalnum())
        return order_kind(client_id) is not None and order_instance(client_id) == (own or None)

    def _close_position(
        self,
        session: Session,
        position: Position,
        report: ReconcileReport,
        *,
        exit_reason: str,
        explained: bool = False,
        offline: bool = False,
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
        if explained:
            fills = [
                row
                for row in fills
                if self._own_exit(session, row[1])
            ]
        qty_closed = sum(f.qty for f, _ in fills) if explained else position.qty
        exit_price: Decimal | None = None
        if fills:
            newest = fills[0][1]
            if order_kind(newest.client_order_id) == "breach":
                exit_reason = "stop_breached"
            else:
                exit_reason = newest.leg  # newest sell leg wins
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
        # Keep qty at the closed size: Performance shows the shares traded.
        if explained:
            position.qty = qty_closed
            position.exit_filled_qty = qty_closed
            position.closed_while_offline = offline
            ev(
                log_reconcile, logging.WARNING if offline else logging.INFO, "position.closed",
                f"closed {position.symbol}: {exit_reason} @ {exit_price}",
            )
        else:
            self._mismatch(
                session, report, f"{position.symbol}: closed at broker",
                {"kind": "flat_without_fill", "symbol": position.symbol}, position,
            )
        pnl = (
            f"P&L {raw:+.2f}" if raw is not None else "P&L unknown"
        )
        self._notifier.notify(
            "trade",
            f"closed {position.symbol}: {exit_reason}, {pnl}",
            link="/positions",
        )

    async def _adopt_position(
        self,
        session: Session,
        bp: BrokerPosition,
        report: ReconcileReport,
        existing_stops: list[BrokerOrder],
    ) -> Decimal | None:
        """A position at the broker the agent never opened: adopt it.

        It gets a standalone gtc stop at current − adopted_stop_atr × the
        1h ATR (or −protective_stop_fallback_pct without ATR), no target,
        and blocks new entries until acknowledged. Sell-side stops
        already open at the broker (e.g. one placed before the database
        was rebuilt) count as cover — no second stop is placed for the
        same shares. Returns the newly placed stop qty — 0 when an
        existing stop already covers the position, None on a failed
        write.
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
        covered = sum(o.qty for o in existing_stops) >= bp.qty
        if covered:
            prices = [o.stop_price for o in existing_stops]
            priced = [p for p in prices if p is not None]
            if priced:
                # The highest stop fires first: the effective protection.
                stop = max(priced)
            basis = "existing broker stop"
        position = Position(
            symbol=bp.symbol,
            origin="adopted",
            pending_ack=True,
            entry_decision_id=None,
            qty=bp.qty,
            avg_entry_price=bp.avg_entry_price,
            initial_stop=stop,
            current_stop_price=stop,
            target_price=None,
            opened_at=_naive_utc(self.clock.now()),
        )
        session.add(position)
        # Hard rule 13: the adopted row exists before the stop order call.
        session.commit()
        placed: Decimal | None = Decimal(0)
        if not covered:
            result = await self.broker.submit_stop_order(
                symbol=bp.symbol,
                qty=bp.qty,
                stop_price=stop,
                client_order_id=self._client_id(
                    "stop", position.id, bp.symbol
                ),
            )
            if result.error is not None or result.data is None:
                placed = None
                report.errors.append(
                    f"adopted stop {bp.symbol}: {result.error}"
                )
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type="protective_stop_failed",
                    details={"symbol": bp.symbol, "error": result.error},
                    affects="order",
                )
            else:
                self._store_broker_order(session, result.data, None, leg="stop")
                placed = Decimal(bp.qty)
        report.adopted.append(bp.symbol)
        self._mismatch(
            session, report, f"{bp.symbol}: adopted {bp.qty} shares",
            position=position,
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
        """A known position has no live stop: repair per spec and alert.

        Confirm first (fresh position and open orders for the symbol),
        then, in order: nothing when flat or already sold, a market sale
        when the stored stop is breached, the orphaned-leg OCO repair
        when a sibling order holds the shares, else a plain gtc stop —
        every sell capped at the confirmed qty (docs/SPEC.md, Protective
        stops). Returns the protected/sold qty, or None on a failed
        write.
        """
        # Confirm first: the symbol's position and open orders straight
        # from the broker, never the state this reconcile fetched. A
        # symbol this pass already found flat is skipped — asking again
        # only repeats the same 404.
        if position.symbol in self._flat_symbols:
            return Decimal(0)
        pos_r = await self.broker.get_position(position.symbol)
        orders_r = await self.broker.get_open_orders()
        if pos_r.error is not None:
            report.errors.append(
                f"protective check {position.symbol}: {pos_r.error}"
            )
            return None
        confirmed = pos_r.data
        if confirmed is None or confirmed.qty <= 0:
            # Flat at the broker — nothing to protect. The row closes on
            # the next pass; remember it so nothing asks again this pass.
            self._flat_symbols.add(position.symbol)
            return Decimal(0)
        live_sells: list[BrokerOrder] = []
        for order in orders_r.data or []:
            for leg in (order, *order.legs):
                if leg.is_open and leg.side == "sell" and leg.symbol == position.symbol:
                    live_sells.append(leg)
        if any(o.order_type in ("stop", "stop_limit") for o in live_sells):
            return Decimal(0)  # a live stop appeared on the fresh read
        # Never sell more than is held (and a position already sold in
        # full is exiting, not uncovered).
        qty = min(position.qty, confirmed.qty)
        sold = session.execute(
            select(func.coalesce(func.sum(Fill.qty), 0))
            .join(Order, Fill.order_id == Order.id)
            .where(
                Order.symbol == position.symbol,
                Order.side == "sell",
                Fill.filled_at >= position.opened_at,
            )
        ).scalar_one()
        if int(sold) >= qty:
            ev(
                log_reconcile, logging.INFO, "reconcile.protective_skip",
                f"{position.symbol}: sell fills already cover "
                f"{qty} — nothing sent",
                symbol=position.symbol,
            )
            return Decimal(0)
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
                confirmed.current_price
                if confirmed.current_price is not None
                else position.avg_entry_price
            )
            stop = _cents(mark * (1 - fallback / 100))
            position.current_stop_price = stop
        current = confirmed.current_price
        if current is not None and stop >= current:
            # The stored stop is at or above the market: the level was
            # already breached, so the repair is the market sale itself
            # (MOO outside the session) — the only sale a check may make.
            ny_now = self.clock.now().astimezone(NY_TZ)
            today = self.calendar.session_for(ny_now.date())
            moo = today is None or not (today.open <= ny_now <= today.close)
            session.commit()
            result = await self.broker.submit_market_sell(
                symbol=position.symbol,
                qty=qty,
                market_on_open=moo,
                client_order_id=self._client_id(
                    "breach", position.id, position.symbol
                ),
            )
            if result.error is not None or result.data is None:
                report.errors.append(
                    f"stop_breached {position.symbol}: {result.error}"
                )
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type="protective_stop_failed",
                    details={"symbol": position.symbol, "error": result.error},
                    affects="order",
                )
                return None
            self._store_broker_order(session, result.data, None, leg="exit")
            record_event(
                session,
                self.clock,
                level="critical",
                event_type="protective_stop",
                details={
                    "symbol": position.symbol,
                    "action": "stop_breached",
                    "qty": qty,
                    "stop": str(stop),
                    "market": str(current),
                },
                affects="order",
            )
            return Decimal(qty)
        if position.initial_stop is None:
            position.initial_stop = stop
        # Orphaned stop leg while a sibling sell order (the target) holds
        # the shares: a new stop would be rejected for insufficient qty.
        # Cancel the sibling, confirm, then an OCO exit — or the stop
        # alone when the OCO is refused. Protection before the target.
        holders = [
            o for o in live_sells if o.order_type not in ("stop", "stop_limit")
        ]
        if holders:
            unprotected_since = self.clock.now()
            for leg in holders:
                ok, final, error = await self._ensure_dead(leg.id)
                if not ok:
                    report.errors.append(
                        f"orphan repair {position.symbol}: cancel "
                        f"{leg.id}: {error or 'still live'}"
                    )
                    record_event(
                        session,
                        self.clock,
                        level="critical",
                        event_type="protective_stop_failed",
                        details={
                            "symbol": position.symbol,
                            "error": f"target cancel: {error or 'still live'}",
                        },
                        affects="order",
                    )
                    return None
                row = session.execute(
                    select(Order).where(Order.alpaca_order_id == leg.id)
                ).scalar_one_or_none()
                if row is not None and final is not None:
                    row.status = final.status
                    if row.active_until is None:
                        row.active_until = _naive_utc(
                            final.updated_at or self.clock.now()
                        )
            session.commit()
            target = position.target_price or stop
            result = await self.broker.submit_oco_exit(
                symbol=position.symbol,
                qty=qty,
                limit_price=target,
                stop_price=stop,
                client_order_id=self._client_id(
                    "stop", position.id, position.symbol
                ),
            )
            repaired_by = "oco"
            if result.error is not None or result.data is None:
                # Protection comes before the target: the stop alone.
                result = await self.broker.submit_stop_order(
                    symbol=position.symbol,
                    qty=qty,
                    stop_price=stop,
                    client_order_id=self._client_id(
                        "stop", position.id, position.symbol
                    ),
                )
                repaired_by = "stop"
            if result.error is not None or result.data is None:
                report.errors.append(
                    f"protective stop {position.symbol}: {result.error}"
                )
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type="protective_stop_failed",
                    details={"symbol": position.symbol, "error": result.error},
                    affects="order",
                )
                return None
            self._store_broker_order(session, result.data, None, leg="stop")
            gap = (self.clock.now() - unprotected_since).total_seconds()
            ev(
                log_reconcile, logging.CRITICAL,
                "reconcile.unprotected_gap",
                f"{position.symbol}: unprotected for {gap:.1f}s while "
                f"the orphaned leg was repaired ({repaired_by})",
                symbol=position.symbol,
                gap_seconds=round(gap, 3),
            )
            record_event(
                session,
                self.clock,
                level="critical",
                event_type="protective_stop",
                details={
                    "symbol": position.symbol,
                    "qty": qty,
                    "stop": str(stop),
                    "repaired_by": repaired_by,
                    "gap_seconds": round(gap, 3),
                },
                affects="order",
            )
            report.protective_stops.append(position.symbol)
            return Decimal(qty)
        # Hard rule 13: the chosen stop is durable before the order call.
        session.commit()
        result = await self.broker.submit_stop_order(
            symbol=position.symbol,
            qty=qty,
            stop_price=stop,
            client_order_id=self._client_id("stop", position.id, position.symbol),
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
                affects="order",
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
                "qty": qty,
                "stop": str(stop),
            },
        )
        return Decimal(qty)

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
        # Working entry orders count in the gates as if they were already
        # positions (docs/SPEC.md, Working entry orders): unfilled qty ×
        # (limit − stop) toward open risk, unfilled qty × limit toward
        # cash. Supervised approvals are not Order rows, so they never
        # land here; they are gated again on Approve.
        entry_rows = session.execute(
            select(Order).where(
                Order.leg == "entry",
                Order.status.in_(OPEN_ORDER_STATUSES),
            )
        ).scalars().all()
        working: list[WorkingEntry] = []
        for row in entry_rows:
            filled = session.execute(
                select(func.coalesce(func.sum(Fill.qty), 0)).where(
                    Fill.order_id == row.id
                )
            ).scalar_one()
            unfilled = row.qty - int(filled)
            if unfilled <= 0:
                continue  # filled qty is a position already
            stop_leg = session.execute(
                select(Order.stop_price).where(
                    Order.parent_alpaca_order_id == row.alpaca_order_id,
                    Order.leg == "stop",
                    Order.status.in_(OPEN_ORDER_STATUSES),
                )
            ).scalars().first()
            working.append(
                WorkingEntry(
                    symbol=row.symbol,
                    unfilled_qty=unfilled,
                    limit_price=row.limit_price,
                    stop_price=stop_leg,
                )
            )
        since = self.clock.now().astimezone(NY_TZ).date()
        recent_closed = session.execute(
            select(Position.closed_at).where(
                Position.symbol == symbol,
                Position.closed_at.is_not(None),
            )
        ).scalars().all()
        closed_today = any(
            closed_at is not None and _ny_date(closed_at) == since
            for closed_at in recent_closed
        )
        closed_query = (
            select(Position)
            .where(
                Position.closed_at.is_not(None),
                Position.realized_pnl_raw.is_not(None),
            )
            .order_by(Position.closed_at.desc())
        )
        if self.state.run_started_at is not None:
            # A paper reset restarts the losing streak.
            closed_query = closed_query.where(
                Position.closed_at >= self.state.run_started_at
            )
        closed = session.execute(closed_query).scalars().all()
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
            working_entries=tuple(working),
            closed_today=bool(closed_today),
            losing_streak=streak,
            shock_brake_1h=self.state.shock_brake_1h,
            shock_brake_session=self.state.shock_brake_session,
            days_to_earnings=days_to_earnings,
            earnings_known=earnings_known,
            kill_switch_on=self.state.kill_switch_on,
            paused=self.state.paused,
            pending_ack=self.state.pending_ack(session) or self._reconcile_failed,
            orders_this_cycle=self._orders_this_cycle,
            sectors=resolve_sectors(self.config, session),
            daily_loss_hit=self.state.daily_loss_hit,
        )

    def budget_context(self, session: Session) -> GateContext:
        """The gate inputs as they stand now — the dashboard risk budget
        card reads the same context the entry gates see, with no candidate
        symbol (``""`` never matches a held position or pending entry)."""
        return self._gate_context(session, None, "", None, True)

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
            new_stop, _ = decision_trade_prices(
                decision.model_output, decision.options, decision.chosen_option
            )
            raw_stop = new_stop if new_stop is not None else output.get("stop_price")
            await self._tighten(
                session,
                decision,
                position,
                Decimal(str(raw_stop)) if raw_stop is not None else None,
                atr_1h=item.snapshot.atr_1h,
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
        if stop is None or target is None:
            stop, target = decision_trade_prices(
                decision.model_output, decision.options, decision.chosen_option
            )
        ctx = self._gate_context(
            session,
            account,
            decision.symbol,
            item.snapshot.days_to_earnings,
            earnings_known,
        )
        if quote is None or stop is None or target is None:
            # No fresh quote → no spread or limit check → no entry.
            decision.gates_result = {
                "allowed": False,
                "blocked_by": "missing_data",
                "checks": [],
            }
            decision.final_action = "blocked"
            return
        stop = _cents(Decimal(str(stop)))
        target = _cents(Decimal(str(target)))
        entry = _cents(quote.last * (1 + Decimal(str(
            self.config.get("execution", "entry_limit_offset_pct", default=0.05)
        )) / 100))
        result = evaluate_entry_gates(
            ctx, entry=entry, stop=Decimal(str(stop)), config=self.config
        )
        decision.gates_result = result.to_json()
        if not result.allowed:
            decision.final_action = "blocked"
            if result.blocked_by == "drawdown_kill":
                session.commit()  # hard rule 13: gate result durable first
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
                    affects="order",
                )
            session.flush()
            return
        assert result.sizing is not None  # noqa: S101 - internal invariant
        qty = result.sizing.shares

        # Spread and entry limit from the decision-time IEX quote; the
        # automatic branch re-fetches a fresh quote before submitting.
        # The spread gate runs only on a plausible quote — a thin IEX
        # book is recorded as spread_unknown, never a block.
        dev_pct = Decimal(
            str(self.config.get("execution", "quote_max_deviation_pct"))
        )
        max_spread = Decimal(
            str(self.config.get("execution", "max_spread_pct", default=0.15))
        )
        offset = Decimal(
            str(
                self.config.get("execution", "entry_limit_offset_pct", default=0.05)
            )
        )
        if quote.quote_plausible(dev_pct):
            mid = (quote.ask + quote.bid) / 2
            spread_pct = (quote.ask - quote.bid) / mid * 100
            if spread_pct > max_spread:
                decision.gates_result = result.to_json() | {
                    "allowed": False,
                    "blocked_by": "spread",
                    "detail": f"spread {spread_pct:.3f}% > {max_spread}%",
                }
                decision.final_action = "blocked"
                session.flush()
                return
        limit = _cents(quote.last * (1 + offset / 100))

        if str(self.config.get("execution", "mode", default="automatic")) == (
            "supervised"
        ):
            # Supervised mode: the entry waits for Marc's approval instead
            # of going to Alpaca (docs/SPEC.md, Supervised mode). Approve
            # re-fetches a fresh quote and re-runs every check.
            self._supersede_pending(session, decision.symbol)
            approval = Approval(
                decision_id=decision.id,
                symbol=decision.symbol,
                qty=qty,
                limit_price=limit,
                stop_price=_cents(Decimal(str(stop))),
                target_price=_cents(Decimal(str(target))),
                status="pending",
                created_at=_naive_utc(self.clock.now()),
            )
            session.add(approval)
            session.flush()
            decision.gates_result = result.to_json() | {
                "pending_approval": True,
                "approval_id": approval.id,
            }
            session.flush()
            self._notifier.notify(
                "approval_waiting",
                f"{decision.symbol}: {qty} shares at {limit} — "
                "approve on the dashboard",
                link="/",
            )
            self._notify({"approvals", "decisions"})
            return

        # The "Fresh quote" gate right before the order is sent — the
        # shared pre-submit step Approve also uses (docs/SPEC.md,
        # Entries): fetch the latest IEX quote, reject missing or stale,
        # then re-run the spread check, recompute the limit and re-run
        # the code checks from the new entry. A failure is recorded on
        # the gates result; no order is sent, no retry this cycle.
        # Hard rule 13: the gate result is durable before the fresh-quote
        # fetch and the order call below.
        session.commit()
        account = await self.refresh_account()
        if account is None:
            decision.final_action = "blocked"
            decision.gates_result = {"allowed": False, "blocked_by": "account_unavailable"}
            session.flush()
            return
        fresh_quote, fresh_limit, fresh_fail = await self._fresh_entry_check(
            symbol=decision.symbol,
            stop=Decimal(str(stop)),
            target=Decimal(str(target)),
            atr_1h=item.snapshot.atr_1h,
            **(
                {
                    "room_levels": cast(
                        "list[dict[str, Any]] | None",
                        (decision.levels or {}).get("levels"),
                    )
                }
                if decision.prompt_version == "decision.v3"
                else {}
            ),
        )
        if fresh_fail is not None:
            failed = replace(
                result,
                allowed=False,
                blocked_by="fresh_quote",
                sizing=None,
                checks=[
                    *result.checks,
                    GateCheck("fresh_quote", False, fresh_fail),
                ],
            )
            decision.gates_result = failed.to_json() | {
                "quote": self._quote_provenance(fresh_quote)
                if fresh_quote is not None
                else None,
            }
            decision.final_action = "blocked"
            session.flush()
            return
        assert fresh_limit is not None and fresh_quote is not None  # noqa: S101 - internal invariant
        ctx = self._gate_context(
            session, account, decision.symbol, item.snapshot.days_to_earnings, earnings_known,
        )
        result = evaluate_entry_gates(
            ctx, entry=fresh_limit, stop=Decimal(str(stop)), config=self.config,
        )
        decision.gates_result = result.to_json() | {
            "entry_limit": str(fresh_limit),
            "quote": self._quote_provenance(fresh_quote),
        }
        if not result.allowed or result.sizing is None:
            decision.final_action = "blocked"
            if result.blocked_by == "drawdown_kill":
                session.commit()
                await self.activate_kill_switch(reason="drawdown kill gate", automatic=True)
            session.flush()
            return
        qty = result.sizing.shares

        self._orders_this_cycle += 1
        client_id = self._client_id("entry", decision.id, decision.symbol)
        session.commit()  # hard rule 13
        submitted = await self.broker.submit_bracket_entry(
            symbol=decision.symbol,
            qty=qty,
            limit_price=fresh_limit,
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
        self._notify({"positions", "orders", "decisions", "header"})

    async def _tighten(
        self,
        session: Session,
        decision: Decision,
        position: Position,
        new_stop: Decimal | None,
        *, atr_1h: Decimal | None,
    ) -> None:
        """Move the stop leg in place; stops only move in the position's favour."""
        if (
            new_stop is None
            or position.current_stop_price is None
            or new_stop <= position.current_stop_price
        ):
            # The model asked for a tighten that moves nothing — say so in
            # gates_result rather than letting "tighten_stop" imply a change.
            decision.gates_result = {
                "tighten_noop": True,
                "reason": "new stop is not above the current stop",
            }
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
            decision.gates_result = {
                "tighten_noop": True,
                "reason": "no open stop leg for this position",
            }
            return
        session.commit()
        quotes = await self.market_data.sources.quotes.fetch_quotes([position.symbol])
        quote = quotes.data.get(position.symbol) if quotes.error is None and quotes.data else None
        now = self.clock.now()
        bar = self.market_data.bars_cache.latest(
            position.symbol, now - timedelta(minutes=5),
        )
        reason = None
        reference = None
        if quote is None:
            reason = "price_stale"
        else:
            reason = quote.price_reason(
                now=now,
                # Held-position tighten decisions may accept an older
                # IEX last trade (docs/SPEC.md, tighten_stop).
                max_age_seconds=float(
                    self.config.get(
                        "execution", "tighten_quote_max_age_seconds",
                        default=300,
                    )
                ),
                bar_close=bar.close if bar is not None else None,
                max_deviation_pct=Decimal(
                    str(self.config.get("execution", "quote_max_deviation_pct"))
                ),
            )
            if reason is None:
                reference = quote.stop_reference(
                    bar.close if bar else None, atr_1h, entry=False,
                    max_deviation_pct=Decimal(
                        str(self.config.get("execution", "quote_max_deviation_pct"))
                    ),
                )
        margin = Decimal(str(self.config.get("decision", "tighten_min_atr_below_price")))
        if reason is None:
            reason = (
                "reference_missing" if reference is None or atr_1h is None
                else "tighten_market_distance"
                if reference - _cents(new_stop) < margin * atr_1h
                else None
            )
        if reason is not None:
            decision.final_action = "hold"
            decision.gates_result = {"tighten_noop": True, "reason": reason}
            if reason in ("price_stale", "price_suspect"):
                # Same display as the break-even guard: drawer, feed and
                # inspector show the code change with its reason
                # (docs/SPEC.md, tighten_stop).
                validation = dict(decision.validation_result or {})
                checks = list(cast("list[dict[str, object]]", validation.get("checks") or []))
                checks.append(
                    {
                        "name": "Market price",
                        "passed": False,
                        "detail": reason,
                        "value": None,
                    }
                )
                validation["final_action"] = "hold"
                validation["changed_by_code"] = True
                validation["reason"] = reason
                validation["note"] = (
                    f"code changed tighten_stop → hold ({reason})"
                )
                validation["checks"] = checks
                decision.validation_result = validation
            session.flush()
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
        stop_row.active_until = _naive_utc(self.clock.now())
        self._store_broker_order(session, result.data, stop_row.decision_id, leg="stop")
        position.current_stop_price = _cents(new_stop)
        session.flush()
        self._notify({"positions", "orders"})

    async def _ensure_dead(
        self, order_id: str
    ) -> tuple[bool, BrokerOrder | None, str | None]:
        """Cancel one broker order if still live; confirm it can't fill.

        ``(True, order, None)`` — terminal now; ``(True, None, None)`` —
        the broker has no such order, so nothing can fill; ``(False,
        None, error)`` — the cancel write failed (error carries the
        broker's message); ``(False, None, None)`` — a read failed or
        the order is still live, so its state stays unconfirmed. A leg a
        cancelled parent took down reads as already terminal and passes.
        The market sell must never go out before every leg is confirmed
        done (docs/SPEC.md, Exits).
        """
        checked = await self.broker.get_order(order_id)
        if checked.error is not None:
            return False, None, None
        if checked.data is not None and checked.data.is_open:
            cancelled = await self.broker.cancel_order(order_id)
            if cancelled.error is not None:
                return False, None, cancelled.error
            checked = await self.broker.get_order(order_id)
            if checked.error is not None:
                return False, None, None
        order = checked.data
        if order is None:
            return True, None, None
        if order.is_open:
            return False, None, None
        return True, order, None

    async def _exit(
        self,
        session: Session,
        decision: Decision,
        position: Position,
        *,
        reason: str,
    ) -> None:
        """Agent exit: cancel the open legs, confirm, then market sell.

        Same-session exits sell at once — the PDT deferral is retired
        (docs/SPEC.md, Day trades (rule retired)).
        """
        # Cancel every open order for the symbol and confirm each is dead
        # before selling — both sides: a still-working buy parent (a
        # partially filled entry, or an order placed outside the agent)
        # would keep adding shares after the exit, and its nested legs
        # could still fire.
        open_orders = session.execute(
            select(Order).where(
                Order.symbol == position.symbol,
                Order.status.in_(OPEN_ORDER_STATUSES),
            )
        ).scalars().all()
        for row in open_orders:
            ok, final, error = await self._ensure_dead(row.alpaca_order_id)
            if not ok:
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type=(
                        "order_submit_failed" if error
                        else "leg_cancel_unconfirmed"
                    ),
                    details={
                        "symbol": position.symbol,
                        "order": row.alpaca_order_id,
                        **({"error": error} if error else {}),
                    },
                )
                session.flush()
                return
            if final is not None:
                row.status = final.status
                if row.active_until is None:
                    row.active_until = _naive_utc(
                        final.updated_at or self.clock.now()
                    )

        # Broker-side orders the DB never recorded (e.g. a crash between
        # the broker's accept and our store) must be cancelled and
        # confirmed too — the market sell must never go past a live leg.
        broker_open = await self.broker.get_open_orders()
        if broker_open.error is not None:
            record_event(
                session,
                self.clock,
                level="critical",
                event_type="leg_cancel_unconfirmed",
                details={
                    "symbol": position.symbol,
                    "error": broker_open.error,
                },
                affects="order",
            )
            session.flush()
            return
        known_ids = {row.alpaca_order_id for row in open_orders}
        extra_legs: list[BrokerOrder] = []
        for order in broker_open.data:
            if order.symbol != position.symbol:
                continue
            if order.is_open and order.id not in known_ids:
                extra_legs.append(order)
            extra_legs.extend(
                leg_order
                for leg_order in order.legs
                if leg_order.is_open and leg_order.id not in known_ids
            )
        for order in extra_legs:
            ok, final, error = await self._ensure_dead(order.id)
            if not ok:
                record_event(
                    session,
                    self.clock,
                    level="critical",
                    event_type=(
                        "order_submit_failed" if error
                        else "leg_cancel_unconfirmed"
                    ),
                    details={
                        "symbol": position.symbol,
                        "order": order.id,
                        **({"error": error} if error else {}),
                    },
                )
                session.flush()
                return
            if final is not None:
                self._store_broker_order(session, final, None)
        # Hard rule 13: the order statuses are durable before the market sell.
        session.commit()

        now = self.clock.now()
        ny_now = now.astimezone(NY_TZ)
        today_session = self.calendar.session_for(ny_now.date())
        regular_hours = (
            today_session is not None
            and today_session.open <= ny_now <= today_session.close
        )
        self._orders_this_cycle += 1
        # Never sell more than is held: cap at the qty Alpaca confirms
        # right before the send. Falling back to the stored qty is only
        # allowed while the broker guard is confirmed — without it the
        # exit waits for a confirmed qty (retried at the next check).
        if position.symbol in self._flat_symbols:
            # Already found flat this pass — nothing left to sell.
            return
        confirmed = await self.broker.get_position(position.symbol)
        if confirmed.error is None and (
            confirmed.data is None or confirmed.data.qty <= 0
        ):
            self._flat_symbols.add(position.symbol)
        if confirmed.error is None and confirmed.data is not None:
            sell_qty = min(position.qty, confirmed.data.qty)
        elif self.state.broker_guard_confirmed:
            sell_qty = position.qty
        else:
            ev(
                logger, logging.WARNING, "executor.exit_unconfirmed_qty",
                f"{position.symbol}: exit held back — position qty "
                f"could not be confirmed ({confirmed.error}); retrying "
                "at the next position check",
                symbol=position.symbol,
            )
            session.flush()
            return
        if sell_qty <= 0:
            return
        sold = await self.broker.submit_market_sell(
            symbol=position.symbol,
            qty=sell_qty,
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
                affects="order",
            )
            session.flush()
            return
        self._store_broker_order(session, sold.data, decision.id, leg="exit")
        session.flush()
        if reason == "earnings_exit":
            self._notifier.notify(
                "exit_deferred_or_earnings_exit",
                f"Earnings exit tomorrow: {position.symbol}",
                link="/positions",
            )
        self._notify({"positions", "orders"})
