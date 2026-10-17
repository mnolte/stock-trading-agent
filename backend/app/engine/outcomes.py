"""Outcome worker and equity snapshots (docs/SPEC.md, Outcome tracking).

For every decision — skips and no-calls included — the worker stores the
price 1 hour, 1 session and 5 sessions after the decision. A session is a
trading day: "1 session" is the same New York time on the next trading
session, "5 sessions" the 5th; a target after the session's close falls
back to the close. Each price is the close of the last 5-minute bar closed
at or before the target, stored with that bar's timestamp, and only once
the SIP delay has passed. For trades (decisions that opened a position)
the best and worst price reached while the position was open are tracked
too, updated until shortly after the close.

It also writes the equity snapshots that feed the Performance screen:
equity, cash, the same-day SPY buy-and-hold value and the ECB EUR/USD
rate for display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.alpaca import AlpacaDataClient
from app.data.cache import _naive_utc
from app.data.models import Bar5m, CalendarDay
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import (
    Cycle,
    Decision,
    EquitySnapshot,
    Outcome,
    Position,
    Run,
)
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, Clock

# A price target needs this much new data either side of the target.
_PRICE_WINDOW = timedelta(days=2)

# First-touch results recorded on a trade's outcome row.
_TOUCH_STOP = "stop"
_TOUCH_TARGET = "target"
_TOUCH_NEITHER = "neither"


@dataclass
class OutcomeReport:
    """One fill pass over the outcomes table."""

    filled: int = 0  # outcome rows that got at least one new price
    pending: int = 0  # targets due but still missing bar data
    best_worst: int = 0  # trade outcomes updated


def _aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _first_touch(bars: list[Bar5m], position: Position) -> str:
    """Which of the position's stop or target the 5m bars touched first.

    Compared against the position's current levels: a tightened stop is the
    level that would actually fire. A bar that touches both counts as a stop
    touch — intrabar order is unknowable, so the pessimistic read wins.
    """
    stop = position.current_stop_price
    target = position.target_price
    for bar in sorted(bars, key=lambda b: b.start):
        stop_hit = stop is not None and bar.low <= stop
        target_hit = target is not None and bar.high >= target
        if stop_hit:
            return _TOUCH_STOP
        if target_hit:
            return _TOUCH_TARGET
    return _TOUCH_NEITHER


class OutcomeWorker:
    """Fills outcome rows from cached bars; never touches the network."""

    def __init__(
        self,
        *,
        market_data: MarketDataService | None,
        calendar: CalendarProvider | None,
        config: AppConfig,
        clock: Clock,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.market_data = market_data
        self.calendar = calendar
        self.config = config
        self.clock = clock
        self._session_factory = session_factory or get_session_maker()
        # A target fills once the SIP delay has passed — the same delay the
        # data client applies to every bar request.
        self._delay = AlpacaDataClient.SIP_MIN_AGE

    # ------------------------------------------------------------------
    # targets

    def _session_n_after(self, day: date, n: int) -> CalendarDay | None:
        """The nth trading session strictly after ``day``."""
        assert self.calendar is not None
        session = None
        cursor = day
        for _ in range(n):
            session = self.calendar.next_trading_day(cursor)
            if session is None:
                return None
            cursor = session.date
        return session

    def _targets(self, decision_time: datetime) -> list[tuple[str, str, datetime]]:
        """(field, timestamp field, UTC target) for one decision."""
        targets: list[tuple[str, str, datetime]] = [
            ("price_1h", "price_1h_at", decision_time + timedelta(hours=1))
        ]
        decision_ny = decision_time.astimezone(NY_TZ)
        session = (
            self.calendar.session_for(decision_ny.date())
            if self.calendar is not None
            else None
        )
        if session is None:
            return targets
        for n, fields in (
            (1, ("price_1session", "price_1session_at")),
            (5, ("price_5sessions", "price_5sessions_at")),
        ):
            later = self._session_n_after(session.date, n)
            if later is None:
                continue
            target_ny = datetime.combine(
                later.date, decision_ny.timetz().replace(tzinfo=None), NY_TZ
            )
            # Same New York time; after the close on early-close days the
            # close is used instead.
            targets.append(
                (fields[0], fields[1], min(target_ny, later.close).astimezone(UTC))
            )
        return targets

    def next_due(
        self, decision_time: datetime, outcome: Outcome
    ) -> datetime | None:
        """Earliest unfilled price target (aware UTC); None when all filled."""
        pending = [
            target
            for field, _at, target in self._targets(_aware_utc(decision_time))
            if getattr(outcome, field) is None
        ]
        return min(pending) if pending else None

    # ------------------------------------------------------------------
    # prices

    def _bar_close_time(
        self, bar: Bar5m, sessions: dict[date, CalendarDay]
    ) -> datetime:
        """When the bar is closed: start + 5m, never past the session close."""
        end = bar.start + timedelta(minutes=5)
        session = sessions.get(bar.start.astimezone(NY_TZ).date())
        if session is not None and session.close.astimezone(UTC) < end:
            end = session.close.astimezone(UTC)
        return end

    def _price_at(self, symbol: str, target: datetime) -> Bar5m | None:
        """Last 5-minute bar closed at or before ``target`` (aware UTC)."""
        assert self.market_data is not None
        bars = self.market_data.get_5m_bars(symbol, target - _PRICE_WINDOW, target)
        if not bars:
            return None
        sessions: dict[date, CalendarDay] = {}
        assert self.calendar is not None
        for bar in bars:
            day = bar.start.astimezone(NY_TZ).date()
            if day not in sessions:
                found = self.calendar.session_for(day)
                if found is not None:
                    sessions[day] = found
        closed = [
            bar for bar in bars if self._bar_close_time(bar, sessions) <= target
        ]
        return closed[-1] if closed else None

    # ------------------------------------------------------------------
    # fills

    def fill(self, session: Session, now: datetime | None = None) -> OutcomeReport:
        """One pass over every unfilled outcome target and open trade."""
        now = now or self.clock.now()
        report = OutcomeReport()
        if self.market_data is None or self.calendar is None:
            return report

        rows = session.execute(
            select(Decision, Cycle.scheduled_time, Outcome)
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .join(Outcome, Outcome.decision_id == Decision.id)
            .where(
                or_(
                    Outcome.price_1h.is_(None),
                    Outcome.price_1session.is_(None),
                    Outcome.price_5sessions.is_(None),
                )
            )
        ).all()
        for decision, scheduled, outcome in rows:
            decision_time = _aware_utc(scheduled)
            changed = False
            for field, at_field, target in self._targets(decision_time):
                if getattr(outcome, field) is not None:
                    continue
                if now < target + self._delay:
                    continue
                bar = self._price_at(decision.symbol, target)
                if bar is None:
                    report.pending += 1
                    continue
                setattr(outcome, field, bar.close)
                setattr(outcome, at_field, _naive_utc(bar.start))
                changed = True
            if changed:
                report.filled += 1
        session.flush()

        for position in session.execute(
            select(Position).where(Position.entry_decision_id.is_not(None))
        ).scalars():
            if self._update_best_worst(session, position, now):
                report.best_worst += 1
        session.flush()
        return report

    def _update_best_worst(
        self, session: Session, position: Position, now: datetime
    ) -> bool:
        """Track the best/worst bar extremes while the trade was open."""
        assert self.market_data is not None
        opened = _aware_utc(position.opened_at)
        closed = _aware_utc(position.closed_at) if position.closed_at else None
        end = min(closed, now) if closed else now
        if end <= opened:
            return False
        if closed is not None and closed + self._delay < now:
            # The window is fixed after the close; give late bars one delay
            # to arrive, then stop rewriting the row.
            outcome = session.execute(
                select(Outcome).where(
                    Outcome.decision_id == position.entry_decision_id
                )
            ).scalar_one_or_none()
            if (
                outcome is not None
                and outcome.best_price is not None
                and outcome.worst_price is not None
                and outcome.first_touch is not None
            ):
                return False
        bars = self.market_data.get_5m_bars(position.symbol, opened, end)
        if not bars:
            return False
        outcome = session.execute(
            select(Outcome).where(Outcome.decision_id == position.entry_decision_id)
        ).scalar_one_or_none()
        if outcome is None:
            return False
        best = max(bars, key=lambda b: b.high)
        worst = min(bars, key=lambda b: b.low)
        changed = False
        if outcome.best_price is None or best.high > outcome.best_price:
            outcome.best_price = best.high
            outcome.best_price_at = _naive_utc(best.start)
            changed = True
        if outcome.worst_price is None or worst.low < outcome.worst_price:
            outcome.worst_price = worst.low
            outcome.worst_price_at = _naive_utc(worst.start)
            changed = True
        touch = _first_touch(bars, position)
        if (
            outcome.first_touch not in (_TOUCH_STOP, _TOUCH_TARGET)
            and outcome.first_touch != touch
        ):
            outcome.first_touch = touch
            changed = True
        return changed

    # ------------------------------------------------------------------
    # equity snapshots

    def _spy_price(self, now: datetime) -> Decimal | None:
        """Latest SPY price: last closed 5m bar, else the last daily close."""
        assert self.market_data is not None
        benchmark = str(
            self.config.get("performance", "benchmark", default="SPY")
        )
        bar = self._price_at(benchmark, now)
        if bar is not None:
            return bar.close
        today = now.astimezone(NY_TZ).date()
        daily = self.market_data.get_daily_bars(
            benchmark, self.market_data.recent_sessions(today, 2)
        )
        return daily[-1].close if daily else None

    def write_equity_snapshot(
        self,
        session: Session,
        *,
        equity: Decimal,
        cash: Decimal,
    ) -> EquitySnapshot:
        """Append one equity snapshot with the SPY benchmark and EUR/USD."""
        now = self.clock.now()
        run = session.execute(
            select(Run).order_by(Run.id.desc()).limit(1)
        ).scalar_one_or_none()
        spy = self._spy_price(now) if self.market_data is not None else None
        if run is not None and run.spy_close is None and spy is not None:
            run.spy_close = spy
        # Buy-and-hold: the run's starting equity invested in SPY at the
        # run's stored baseline close.
        benchmark = (
            run.starting_equity * spy / run.spy_close
            if run is not None and run.spy_close is not None and spy is not None
            else None
        )
        rate = None
        fx = (
            getattr(self.market_data, "latest_fx", None)
            if self.market_data is not None
            else None
        )
        if fx is not None and fx.data is not None:
            rate = fx.data.usd_per_eur
        snapshot = EquitySnapshot(
            time=_naive_utc(now),
            equity=equity,
            cash=cash,
            spy_benchmark_value=benchmark,
            eur_usd_rate=rate,
        )
        session.add(snapshot)
        session.flush()
        return snapshot
