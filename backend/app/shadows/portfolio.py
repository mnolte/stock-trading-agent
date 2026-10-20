"""A shadow's virtual portfolio and its fills on 5-min bars.

docs/SPEC.md, Strategies and shadows — Virtual trading:

- An entry is a limit at the live limit price; it fills when a 5-min bar
  starting at or after the order time trades at or through the limit,
  before the live entry timeout. Fill price: the limit, or the bar's open
  when the bar opens below it.
- Stops and targets are checked bar by bar at every position check and
  cycle; when both are touched in one bar the stop counts. A gap through
  the stop fills at the open; a gap above the target fills at the open.
- Market exits (model exit, earnings exit, Stop & flatten) fill at the open
  of the first bar starting at or after the request.
- Slippage per side and dividends (ex-date inside the hold) are applied like
  live's adjusted P&L; ``r`` is the net P&L over the risk at entry.

Only cached bars are read — shadows make no market-data calls of their own.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.cache import _naive_utc
from app.data.models import Bar5m
from app.db.models import CorporateAction, Position, ShadowEquity, ShadowPosition
from app.engine.trades import _dividends
from app.market.clock import NY_TZ, UTC
from app.risk.gates import GateContext
from app.risk.math import WorkingEntry

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.market.calendar import CalendarProvider

BAR = timedelta(minutes=5)
CENT = Decimal("0.01")

BarReader = Callable[[str, datetime, datetime], list[Bar5m]]


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def ny_date(dt: datetime) -> date:
    return aware(dt).astimezone(NY_TZ).date()


def slippage_pct(config: AppConfig) -> Decimal:
    return Decimal(str(config.get("performance", "slippage_pct_per_side", default=0.05)))


def entry_cost(sp: ShadowPosition, slip: Decimal) -> Decimal:
    """Per-share cost of a filled entry, slippage included."""
    assert sp.entry is not None
    return sp.entry * (1 + slip / 100)


@dataclass(frozen=True)
class FillEvent:
    """One thing the bar replay did; returned for tests and logs."""

    position_id: int
    symbol: str
    kind: str  # filled | not_filled | stop | target | exit | earnings_exit | flatten
    price: Decimal | None
    at: datetime


def current_positions(
    session: Session, strategy_id: int, run_id: int | None, statuses: Sequence[str]
) -> list[ShadowPosition]:
    return list(
        session.execute(
            select(ShadowPosition)
            .where(
                ShadowPosition.strategy_id == strategy_id,
                ShadowPosition.run_id == run_id,
                ShadowPosition.status.in_(statuses),
            )
            .order_by(ShadowPosition.id)
        ).scalars()
    )


def _dividends_for(session: Session, sp: ShadowPosition, closed_at: datetime) -> Decimal:
    actions = (
        session.execute(
            select(CorporateAction).where(
                CorporateAction.symbol == sp.symbol,
                CorporateAction.action_type == "dividend",
            )
        )
        .scalars()
        .all()
    )
    assert sp.opened_at is not None
    return _dividends(actions, sp.symbol, ny_date(sp.opened_at), ny_date(closed_at), sp.qty)


def close_position(
    session: Session,
    sp: ShadowPosition,
    *,
    price: Decimal,
    at: datetime,
    reason: str,
    slip: Decimal,
) -> None:
    """Close at ``price``: P&L net of slippage both sides plus dividends."""
    cost = entry_cost(sp, slip)
    proceeds = price * (1 - slip / 100)
    pnl = (proceeds - cost) * sp.qty + _dividends_for(session, sp, at)
    risk = (sp.entry - sp.initial_stop) * sp.qty if sp.entry is not None else Decimal(0)
    sp.status = "closed"
    sp.exit_price = price
    sp.exit_reason = reason
    sp.closed_at = _naive_utc(at)
    sp.pnl = pnl
    sp.r = pnl / risk if risk > 0 else None
    sp.exit_pending = None
    sp.exit_requested_at = None


def replay_bars(
    session: Session,
    *,
    strategy_id: int,
    run_id: int | None,
    now: datetime,
    bars: BarReader,
    config: AppConfig,
) -> list[FillEvent]:
    """Walk every working entry and open position through the 5-min bars
    cached since it was last checked, in time order."""
    slip = slippage_pct(config)
    events: list[FillEvent] = []
    for sp in current_positions(session, strategy_id, run_id, ("pending", "open")):
        since = aware(sp.bars_through) + BAR if sp.bars_through else aware(sp.created_at)
        series = [b for b in bars(sp.symbol, since, now) if b.start + BAR <= now]
        for bar in series:
            if sp.status == "pending":
                if bar.start >= aware(sp.expires_at):
                    sp.status = "not_filled"
                    sp.closed_at = sp.expires_at
                    sp.exit_reason = "not_filled"
                    events.append(FillEvent(sp.id, sp.symbol, "not_filled", None, bar.start))
                    break
                assert sp.limit_price is not None
                sp.bars_through = _naive_utc(bar.start)
                if bar.low <= sp.limit_price:
                    sp.status = "open"
                    sp.entry = min(bar.open, sp.limit_price)
                    sp.opened_at = _naive_utc(bar.start)
                    events.append(FillEvent(sp.id, sp.symbol, "filled", sp.entry, bar.start))
                # The fill bar's own path is unknown; stops start next bar.
                continue
            sp.bars_through = _naive_utc(bar.start)
            if (
                sp.exit_pending
                and sp.exit_requested_at
                and bar.start >= aware(sp.exit_requested_at)
            ):
                reason = sp.exit_pending
                close_position(session, sp, price=bar.open, at=bar.start, reason=reason, slip=slip)
                events.append(FillEvent(sp.id, sp.symbol, reason, bar.open, bar.start))
                break
            if bar.low <= sp.stop:
                # Stop first when both are touched in one bar.
                price = min(bar.open, sp.stop)
                close_position(session, sp, price=price, at=bar.start, reason="stop", slip=slip)
                events.append(FillEvent(sp.id, sp.symbol, "stop", price, bar.start))
                break
            if sp.target is not None and bar.high >= sp.target:
                price = max(bar.open, sp.target)
                close_position(session, sp, price=price, at=bar.start, reason="target", slip=slip)
                events.append(FillEvent(sp.id, sp.symbol, "target", price, bar.start))
                break
        # A working entry with no bars at all (data gap) lapses once its
        # session has ended, so it never blocks the budget forever.
        if (
            sp.status == "pending"
            and now >= aware(sp.expires_at)
            and ny_date(now) > ny_date(sp.expires_at)
        ):
            sp.status = "not_filled"
            sp.closed_at = sp.expires_at
            sp.exit_reason = "not_filled"
            events.append(FillEvent(sp.id, sp.symbol, "not_filled", None, now))
    session.flush()
    return events


def last_price(bars: BarReader, symbol: str, now: datetime) -> Decimal | None:
    series = [b for b in bars(symbol, now - timedelta(days=7), now) if b.start + BAR <= now]
    return series[-1].close if series else None


@dataclass(frozen=True)
class Book:
    """The virtual portfolio as it stands now."""

    start_equity: Decimal
    cash: Decimal  # after open positions, before working entries
    equity: Decimal  # cash + open positions at the last cached close
    open: list[ShadowPosition]
    pending: list[ShadowPosition]
    closed: list[ShadowPosition]


def book(
    session: Session,
    *,
    strategy_id: int,
    run_id: int | None,
    start_equity: Decimal,
    now: datetime,
    bars: BarReader,
    config: AppConfig,
) -> Book:
    slip = slippage_pct(config)
    rows = current_positions(session, strategy_id, run_id, ("pending", "open", "closed"))
    opened = [p for p in rows if p.status == "open"]
    closed = [p for p in rows if p.status == "closed"]
    pending = [p for p in rows if p.status == "pending"]
    cash = start_equity + sum((p.pnl or Decimal(0) for p in closed), Decimal(0))
    value = Decimal(0)
    for p in opened:
        cash -= entry_cost(p, slip) * p.qty
        price = last_price(bars, p.symbol, now)
        value += (price if price is not None else p.entry or Decimal(0)) * p.qty
    return Book(start_equity, cash, cash + value, opened, pending, closed)


def write_equity(
    session: Session, *, strategy_id: int, run_id: int | None, at: datetime, b: Book
) -> None:
    session.add(
        ShadowEquity(
            strategy_id=strategy_id,
            run_id=run_id,
            time=_naive_utc(at),
            equity=cents(b.equity),
            cash=cents(b.cash),
        )
    )


def _recent_session_start(calendar: CalendarProvider, today: date, n: int) -> date:
    """First day of the last ``n`` trading days, today included."""
    days: list[date] = []
    day = today
    for _ in range(n * 3 + 10):
        if len(days) >= n:
            break
        if calendar.session_for(day) is not None:
            days.append(day)
        day -= timedelta(days=1)
    return days[-1] if days else today


def gate_context(
    session: Session,
    *,
    symbol: str,
    b: Book,
    strategy_id: int,
    run_id: int | None,
    now: datetime,
    calendar: CalendarProvider,
    live: LiveGateState,
    days_to_earnings: int | None,
    earnings_known: bool,
    orders_this_cycle: int,
) -> GateContext:
    """The live ``GateContext`` filled from the shadow's own portfolio.

    Pause, the kill switch, the reconciliation block and the shock brake
    come from live (docs/SPEC.md, Virtual trading); everything else —
    equity, cash, open risk, positions, sectors, daily loss, losing streak,
    drawdown and day trades — is the shadow's own.
    """
    today = ny_date(now)
    session_open = calendar.session_for(today)
    since_close = session_open.open if session_open is not None else now
    eq_rows = session.execute(
        select(ShadowEquity.time, ShadowEquity.equity)
        .where(ShadowEquity.strategy_id == strategy_id, ShadowEquity.run_id == run_id)
        .order_by(ShadowEquity.time)
    ).all()
    before_today = [e for t, e in eq_rows if aware(t) < since_close]
    last_equity = before_today[-1] if before_today else b.start_equity
    peak = max([b.start_equity, b.equity, *[e for _, e in eq_rows]])

    first_day = _recent_session_start(calendar, today, 5)
    day_trades = sum(
        1
        for p in b.closed
        if p.opened_at is not None
        and p.closed_at is not None
        and ny_date(p.opened_at) == ny_date(p.closed_at)
        and ny_date(p.closed_at) >= first_day
    )
    streak = 0
    for p in sorted(b.closed, key=lambda p: p.closed_at or datetime.min, reverse=True):
        if (p.pnl or Decimal(0)) < 0:
            streak += 1
        else:
            break
    open_positions = [
        Position(
            symbol=p.symbol,
            qty=p.qty,
            avg_entry_price=p.entry,
            current_stop_price=p.stop,
            target_price=p.target,
            opened_at=p.opened_at,
        )
        for p in b.open
    ]
    working = tuple(
        WorkingEntry(
            symbol=p.symbol, unfilled_qty=p.qty, limit_price=p.limit_price, stop_price=p.stop
        )
        for p in b.pending
    )
    return GateContext(
        symbol=symbol,
        equity=b.equity,
        cash=b.cash,
        last_equity=last_equity,
        peak_equity=peak,
        open_positions=open_positions,
        working_entries=working,
        day_trades_5d=day_trades,
        losing_streak=streak,
        shock_brake_1h=live.shock_brake_1h,
        shock_brake_session=live.shock_brake_session,
        days_to_earnings=days_to_earnings,
        earnings_known=earnings_known,
        kill_switch_on=live.kill_switch_on,
        paused=live.paused,
        pending_ack=live.pending_ack,
        orders_this_cycle=orders_this_cycle,
    )


@dataclass(frozen=True)
class LiveGateState:
    """Live's entry blockers and the market-wide shock brake, as plain
    values — the shadow code never sees the executor or the broker."""

    kill_switch_on: bool = False
    paused: bool = False
    pending_ack: bool = False
    shock_brake_1h: bool = False
    shock_brake_session: bool = False
