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

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, or_, select
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
    Order,
    Outcome,
    Position,
    Run,
)
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, Clock

# A price target needs this much new data either side of the target.
_PRICE_WINDOW = timedelta(days=2)

# ``_price_at`` checks the newest few bars first; a bar not yet closed at
# the target can only start inside its final minutes, so more candidates
# than this are never closed-at-target anyway.
_PRICE_CANDIDATES = 8

# First-touch results recorded on a trade's outcome row.
_TOUCH_STOP = "stop"
_TOUCH_TARGET = "target"
_TOUCH_NEITHER = "neither"

logger = logging.getLogger(__name__)


@dataclass
class OutcomeReport:
    """One fill pass over the outcomes table."""

    filled: int = 0  # outcome rows that got at least one new price
    pending: int = 0  # targets due but still missing bar data
    best_worst: int = 0  # trade outcomes updated


def _aware_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _active_order(
    orders: list[Order], start: datetime
) -> Order | None:
    """The latest-created order live at ``start`` (created before the bar,
    not yet ended by ``active_until``)."""
    live = [
        o
        for o in orders
        if _aware_utc(o.created_at) <= start
        and (o.active_until is None or start < _aware_utc(o.active_until))
    ]
    return max(live, key=lambda o: (o.created_at, o.id)) if live else None


def _first_touch(
    bars: list[Bar5m], position: Position, history: list[Order] | None = None,
) -> str:
    """Which of the position's stop or target the 5m bars touched first.

    ``bars`` must be ordered by start — the first touch is defined by bar
    order. Both sides follow the order live at each bar's timestamp: the
    stop-leg price and the target leg's limit apply only between the
    order's creation and its ``active_until``, so a cancelled or replaced
    leg no longer counts retroactively. Without stop history the initial
    stop is the only known level; without a recorded target leg the
    position's stored target price is used. A bar that touches both
    counts as a stop touch — intrabar order is unknowable, so the
    pessimistic read wins.
    """
    stops = [o for o in history or [] if o.leg == "stop"]
    # Only legs of this position's own entry decision count as its
    # take-profit: an unrelated sell-limit stored with the "target" label
    # is not the bracket's target.
    targets = [
        o
        for o in history or []
        if o.leg == "target"
        and position.entry_decision_id is not None
        and o.decision_id == position.entry_decision_id
    ]
    for bar in bars:
        if stops:
            current = _active_order(stops, bar.start)
            stop = current.stop_price if current is not None else None
        else:
            stop = position.initial_stop
        if targets:
            current_t = _active_order(targets, bar.start)
            target = (
                current_t.limit_price
                if current_t is not None
                else None
            )
        else:
            target = position.target_price
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
        assert self.calendar is not None  # noqa: S101 - internal invariant
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
        assert self.market_data is not None  # noqa: S101 - internal invariant
        assert self.calendar is not None  # noqa: S101 - internal invariant
        # The answer is almost always among the newest few bars: a bar
        # whose close is still after ``target`` can only start inside its
        # last 5 minutes, so only a handful of candidates need fetching.
        candidates = self.market_data.bars_cache.get_latest(
            symbol, target - _PRICE_WINDOW, target, _PRICE_CANDIDATES
        )
        if not candidates:
            return None
        sessions: dict[date, CalendarDay] = {}
        for bar in candidates:
            day = bar.start.astimezone(NY_TZ).date()
            if day not in sessions:
                found = self.calendar.session_for(day)
                if found is not None:
                    sessions[day] = found
        for bar in candidates:
            if self._bar_close_time(bar, sessions) <= target:
                return bar
        if len(candidates) < _PRICE_CANDIDATES:
            return None
        # More than a handful of consecutive unclosed bars — only possible
        # with misaligned data; the full window scan settles it.
        bars = self.market_data.get_5m_bars(
            symbol, target - _PRICE_WINDOW, target
        )
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
                if outcome.price_5sessions is not None:
                    self._fill_option_touches(
                        session, decision, outcome, decision_time
                    )
        session.flush()

        # Backfill: v2/v3 decisions whose 5-session outcome landed before
        # this code existed (docs/SPEC.md, Outcome tracking — Option
        # outcomes). From stored bars only; missing bars stay null.
        stale = session.execute(
            select(Decision, Cycle.scheduled_time, Outcome)
            .join(Cycle, Decision.cycle_id == Cycle.id)
            .join(Outcome, Outcome.decision_id == Decision.id)
            .where(
                Outcome.option_touches.is_(None),
                Outcome.price_5sessions.is_not(None),
                Decision.options.is_not(None),
            )
        ).all()
        filled_n = missing_n = 0
        for decision, scheduled, outcome in stale:
            if self._fill_option_touches(
                session, decision, outcome, _aware_utc(scheduled)
            ):
                filled_n += 1
            else:
                missing_n += 1
        if stale:
            logger.info(
                "option-touches backfill: %d rows filled, %d missing bars",
                filled_n, missing_n,
            )
        session.flush()

        for position in session.execute(
            select(Position).where(Position.entry_decision_id.is_not(None))
        ).scalars():
            if self._update_best_worst(session, position, now):
                report.best_worst += 1
        session.flush()
        return report

    def _fill_option_touches(
        self,
        session: Session,
        decision: Decision,
        outcome: Outcome,
        decision_time: datetime,
    ) -> bool:
        """First stop/target touch per offered option (docs/SPEC.md, Option
        outcomes). True when every option got a non-null verdict.

        Rules: the entry_limit must be reached inside a 10-minute window
        from the decision ("not_filled" otherwise); bars after the fill bar
        decide, and a bar touching both counts as a stop touch. The traded
        option uses the real fill and the stored ``first_touch``.
        """
        if outcome.option_touches is not None:
            return True
        options_raw = (decision.options or {}).get("options", [])
        if not isinstance(options_raw, list) or not options_raw:
            return True
        assert self.market_data is not None  # noqa: S101 - internal invariant
        if outcome.price_5sessions_at is None:
            return False
        end = _aware_utc(outcome.price_5sessions_at) + timedelta(minutes=5)
        bars = self.market_data.get_5m_bars(decision.symbol, decision_time, end)
        if not bars:
            outcome.option_touches = {
                str(o.get("id")): None
                for o in options_raw
                if isinstance(o, dict)
            }
            return False
        # ``get_5m_bars`` returns bars in start order already.
        traded_id = decision.chosen_option
        touches: dict[str, object] = {}
        all_filled = True
        for option in options_raw:
            if not isinstance(option, dict):
                continue
            oid = str(option.get("id"))
            if oid == traded_id and outcome.first_touch is not None:
                touches[oid] = outcome.first_touch
                continue
            entry = option.get("entry_limit", option.get("entry"))
            stop = option.get("stop")
            target = option.get("target")
            if entry is None or stop is None or target is None:
                touches[oid] = None
                all_filled = False
                continue
            limit = Decimal(str(entry))
            fill_index: int | None = None
            for index, bar in enumerate(bars):
                if bar.start - decision_time > timedelta(minutes=10):
                    break
                if bar.low <= limit <= bar.high or bar.low <= limit:
                    fill_index = index
                    break
            if fill_index is None:
                touches[oid] = "not_filled"
                continue
            touch: str = _TOUCH_NEITHER
            for bar in bars[fill_index:]:
                # A bar touching both counts as a stop touch.
                if bar.low <= Decimal(str(stop)):
                    touch = _TOUCH_STOP
                    break
                if bar.high >= Decimal(str(target)):
                    touch = _TOUCH_TARGET
                    break
            touches[oid] = touch
        outcome.option_touches = touches
        return all_filled

    def _update_best_worst(
        self, session: Session, position: Position, now: datetime
    ) -> bool:
        """Track the best/worst bar extremes while the trade was open."""
        assert self.market_data is not None  # noqa: S101 - internal invariant
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
        outcome = session.execute(
            select(Outcome).where(Outcome.decision_id == position.entry_decision_id)
        ).scalar_one_or_none()
        if outcome is None:
            return False
        extremes = self.market_data.bars_cache.get_extremes(
            position.symbol, opened, end
        )
        if extremes is None:
            return False
        (best_start, best_high), (worst_start, worst_low) = extremes
        changed = False
        if outcome.best_price is None or best_high > outcome.best_price:
            outcome.best_price = best_high
            outcome.best_price_at = _naive_utc(best_start)
            changed = True
        if outcome.worst_price is None or worst_low < outcome.worst_price:
            outcome.worst_price = worst_low
            outcome.worst_price_at = _naive_utc(worst_start)
            changed = True
        if outcome.first_touch in (_TOUCH_STOP, _TOUCH_TARGET):
            return changed
        # Legs of this position: everything its entry decision produced,
        # plus decision-less orders (protective stops, adopted orders the
        # broker already had) created while it was open. Another entry
        # decision's legs for the same symbol never leak in.
        history = list(session.execute(select(Order).where(
            Order.symbol == position.symbol,
            Order.leg.in_(("stop", "target")),
            Order.created_at <= _naive_utc(end),
            or_(
                Order.decision_id == position.entry_decision_id,
                and_(
                    Order.decision_id.is_(None),
                    Order.created_at >= _naive_utc(opened),
                ),
            ),
        )).scalars())
        # A bar can only touch if it dips below the loosest stop in play
        # or reaches the tightest target — fetch just those candidates and
        # let ``_first_touch`` settle each one exactly.
        stops = [o for o in history if o.leg == "stop"]
        targets = [
            o
            for o in history
            if o.leg == "target" and o.decision_id == position.entry_decision_id
        ]
        if stops:
            stop_levels = [
                o.stop_price for o in stops if o.stop_price is not None
            ]
        elif position.initial_stop is not None:
            stop_levels = [position.initial_stop]
        else:
            stop_levels = []
        if targets:
            target_levels = [
                o.limit_price for o in targets if o.limit_price is not None
            ]
        elif position.target_price is not None:
            target_levels = [position.target_price]
        else:
            target_levels = []
        bars = self.market_data.bars_cache.get_touching(
            position.symbol,
            opened,
            end,
            max(stop_levels) if stop_levels else None,
            min(target_levels) if target_levels else None,
        )
        touch = _first_touch(bars, position, history)
        if outcome.first_touch != touch:
            outcome.first_touch = touch
            changed = True
        return changed

    # ------------------------------------------------------------------
    # equity snapshots

    def _spy_price(self, now: datetime) -> Decimal | None:
        """Latest SPY price: last closed 5m bar, else the last daily close."""
        assert self.market_data is not None  # noqa: S101 - internal invariant
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
