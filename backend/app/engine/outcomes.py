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
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, load_only, sessionmaker

from app.concurrency import run_batch
from app.config import AppConfig
from app.data.aggregate import session_bars
from app.data.alpaca import AlpacaDataClient
from app.data.cache import _naive_utc
from app.data.models import Bar5m, CalendarDay, DataResult
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
    ShadowDecision,
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
# Option touches could not be measured — the window's bars never
# arrived (docs/SPEC.md, Option outcomes — Missing bars).
_TOUCH_UNAVAILABLE = "unavailable"

# ``_fill_option_touches`` / ``_fill_shadow_touches`` results.
_TOUCHES_FILLED = "filled"
_TOUCHES_MISSING = "missing"  # no bars for the window — fetch or give up
_TOUCHES_WAITING = "waiting"  # bars there; the traded touch is pending

logger = logging.getLogger(__name__)


@dataclass
class TouchFetchReport:
    """Rows whose missing window bars were fetched in one pass."""

    decisions: int = 0
    shadows: int = 0


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
        # A decision taken outside a session (weekend, holiday, a gap in
        # the calendar) still gets session targets — counted from the
        # trading days strictly after its date, else the fields would
        # stay null and the row would rescan forever.
        base_day = session.date if session is not None else decision_ny.date()
        for n, fields in (
            (1, ("price_1session", "price_1session_at")),
            (5, ("price_5sessions", "price_5sessions_at")),
        ):
            later = self._session_n_after(base_day, n)
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

    def _retention_cutoff(self, now: datetime) -> date | None:
        """The oldest trading day the 5m cache can still hold.

        ``schedule.bars_5m_keep_sessions`` prunes older bars, so a row
        whose targets predate this day can never fill — scanning it
        again every pass is wasted work, and an option_touches row would
        hold its verdict back forever.
        """
        keep = int(
            self.config.get("schedule", "bars_5m_keep_sessions", default=120)
        )
        if keep <= 0 or self.calendar is None:
            return None
        day = now.astimezone(NY_TZ).date()
        for _ in range(keep):
            prev = self.calendar.previous_trading_day(day)
            if prev is None:
                break
            day = prev.date
        return day

    def fill(
        self,
        session: Session,
        now: datetime | None = None,
        *,
        fetch_report: TouchFetchReport | None = None,
    ) -> OutcomeReport:
        """One pass over every unfilled outcome target and open trade."""
        now = now or self.clock.now()
        fetched = fetch_report or TouchFetchReport()
        report = OutcomeReport()
        if self.market_data is None or self.calendar is None:
            return report
        cutoff = self._retention_cutoff(now)

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
            .options(load_only(Decision.symbol, Decision.options, Decision.chosen_option))
        ).all()
        for decision, scheduled, outcome in rows:
            decision_time = _aware_utc(scheduled)
            changed = False
            for field, at_field, target in self._targets(decision_time):
                if getattr(outcome, field) is not None:
                    continue
                if now < target + self._delay:
                    continue
                if (
                    cutoff is not None
                    and target.astimezone(NY_TZ).date() < cutoff
                ):
                    # The bars for this target were pruned — it can
                    # never fill; stop scanning it.
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
        filled_n = waiting_n = unavailable_n = 0
        for decision, scheduled, outcome in stale:
            basis = _aware_utc(
                outcome.price_5sessions_at or scheduled
            ).astimezone(NY_TZ).date()
            if cutoff is not None and basis < cutoff:
                # The window's bars were pruned — the touches can never
                # arrive; mark the row done so its verdict can draft.
                outcome.option_touches = {}
                filled_n += 1
                continue
            result = self._fill_option_touches(
                session, decision, outcome, _aware_utc(scheduled)
            )
            if result == _TOUCHES_FILLED:
                filled_n += 1
            elif result == _TOUCHES_MISSING and self._touches_give_up(
                outcome.price_5sessions_at or scheduled, now
            ):
                # Still no bars past the give-up window: every option
                # without a result is "unavailable" and the row is done
                # (docs/SPEC.md, Option outcomes — Missing bars).
                outcome.option_touches = self._unavailable_touches(
                    decision.options or {}
                )
                unavailable_n += 1
            else:
                waiting_n += 1
        if stale:
            logger.info(
                "option-touches backfill: %d filled, %d fetched, "
                "%d waiting, %d unavailable",
                filled_n, fetched.decisions, waiting_n, unavailable_n,
            )
        session.flush()

        # Shadow decisions: the same counterfactual touches on their own
        # column (docs/SPEC.md, Decision v5). A row fills once its
        # 5-session window passed the SIP delay; missing bars stay null.
        shadow_stale = session.execute(
            select(ShadowDecision, Cycle.scheduled_time)
            .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
            .where(
                ShadowDecision.option_touches.is_(None),
                ShadowDecision.options.is_not(None),
            )
        ).all()
        sf_filled = sf_waiting = sf_unavailable = 0
        for srow, scheduled in shadow_stale:
            decision_time = _aware_utc(scheduled)
            targets = self._targets(decision_time)
            five = next(
                (t for f, _a, t in targets if f == "price_5sessions"), None
            )
            if five is None or now < five + self._delay:
                continue
            if cutoff is not None and five.astimezone(NY_TZ).date() < cutoff:
                srow.option_touches = {}
                sf_filled += 1
                continue
            result = self._fill_shadow_touches(srow, decision_time, five)
            if result == _TOUCHES_FILLED:
                sf_filled += 1
            elif result == _TOUCHES_MISSING and self._touches_give_up(
                five, now
            ):
                srow.option_touches = self._unavailable_touches(
                    srow.options or {}
                )
                sf_unavailable += 1
            else:
                sf_waiting += 1
        if shadow_stale:
            logger.info(
                "shadow option-touches backfill: %d filled, %d fetched, "
                "%d waiting, %d unavailable",
                sf_filled, fetched.shadows, sf_waiting, sf_unavailable,
            )
        session.flush()

        for position in session.execute(
            select(Position)
            .join(Outcome, Outcome.decision_id == Position.entry_decision_id)
            .where(or_(
                Position.closed_at.is_(None),
                Position.closed_at >= _naive_utc(now - self._delay),
                Outcome.best_price.is_(None),
                Outcome.worst_price.is_(None),
                Outcome.first_touch.is_(None),
            ))
        ).scalars():
            if self._update_best_worst(session, position, now):
                report.best_worst += 1
        session.flush()
        return report

    def _option_touches(
        self,
        options_raw: list[dict[str, Any]],
        *,
        symbol: str,
        decision_time: datetime,
        end: datetime,
        shared_limit: Any,
        traded_id: str | None = None,
        traded_touch: str | None = None,
        row_label: str = "",
    ) -> dict[str, object] | None:
        """First stop/target touch per offered option; the one scan both
        the live and the shadow fill use (docs/SPEC.md, Option outcomes).

        Rules: the entry_limit must be reached inside a 10-minute window
        from the decision ("not_filled" otherwise); bars after the fill bar
        decide, and a bar touching both counts as a stop touch. The traded
        option uses the real fill and the stored ``first_touch`` when one
        is passed.

        The limit is one value per decision: stored options carry no
        per-option entry, so it comes from the option, then the snapshot's
        constraints, then an option's ``stop + risk`` (the stored risk is
        limit − stop). Returns None when the bars are missing — the
        caller must leave the column NULL so the row is retried, never
        write a tombstone that drops it from the backfill scan.
        """
        assert self.market_data is not None  # noqa: S101 - internal invariant
        bars = self.market_data.get_5m_bars(symbol, decision_time, end)
        if not bars:
            return None
        # ``get_5m_bars`` returns bars in start order already.
        touches: dict[str, object] = {}
        for option in options_raw:
            raw_id = option.get("id") or option.get("option_id")
            if raw_id is None:
                continue
            oid = str(raw_id)
            if oid == traded_id:
                # The traded option's touch is real, not a bar replay —
                # while the position is still open the whole row waits:
                # returning None keeps the column NULL and retries next
                # pass instead of freezing a guess.
                if traded_touch is None:
                    return None
                touches[oid] = traded_touch
                continue
            entry = option.get("entry_limit", option.get("entry"))
            stop = option.get("stop")
            target = option.get("target")
            try:
                if entry is None:
                    entry = shared_limit
                if (
                    entry is None
                    and stop is not None
                    and option.get("risk") is not None
                ):
                    entry = Decimal(str(stop)) + Decimal(str(option["risk"]))
                limit = Decimal(str(entry)) if entry is not None else None
                stop_d = Decimal(str(stop)) if stop is not None else None
                target_d = (
                    Decimal(str(target)) if target is not None else None
                )
            except (InvalidOperation, ValueError):
                logger.warning(
                    "option %s touch left null: unparseable price (%s)",
                    oid,
                    row_label,
                )
                limit = stop_d = target_d = None
            if (
                limit is None
                or stop_d is None
                or target_d is None
                or not limit.is_finite()
                or not stop_d.is_finite()
                or not target_d.is_finite()
            ):
                touches[oid] = None
                continue
            fill_index: int | None = None
            for index, bar in enumerate(bars):
                # The entry order dies at 10 minutes — a bar starting
                # exactly then can no longer fill it.
                if bar.start - decision_time >= timedelta(minutes=10):
                    break
                if bar.low <= limit:
                    fill_index = index
                    break
            if fill_index is None:
                touches[oid] = "not_filled"
                continue
            touch: str = _TOUCH_NEITHER
            # Only bars after the fill bar decide — a fill-bar touch of
            # the stop or target does not count (docs/SPEC.md, Option
            # outcomes: "After the fill bar, the first bar…").
            for bar in bars[fill_index + 1 :]:
                # A bar touching both counts as a stop touch.
                if bar.low <= stop_d:
                    touch = _TOUCH_STOP
                    break
                if bar.high >= target_d:
                    touch = _TOUCH_TARGET
                    break
            touches[oid] = touch
        return touches

    @staticmethod
    def _touch_options(column: dict[str, Any]) -> list[dict[str, Any]]:
        """Offered and removed options — both get a verdict (the
        counterfactual touches, docs/SPEC.md, Decision v5)."""
        options_raw: list[dict[str, Any]] = []
        for key in ("options", "removed"):
            entries = column.get(key)
            if isinstance(entries, list):
                options_raw += [o for o in entries if isinstance(o, dict)]
        return options_raw

    @classmethod
    def _unavailable_touches(cls, column: dict[str, Any]) -> dict[str, object]:
        """Every option without a result gets "unavailable" — the row is
        done and its verdict can draft (docs/SPEC.md, Missing bars)."""
        touches: dict[str, object] = {}
        for option in cls._touch_options(column):
            raw_id = option.get("id") or option.get("option_id")
            if raw_id is not None:
                touches[str(raw_id)] = _TOUCH_UNAVAILABLE
        return touches

    def _touches_give_up(
        self, five_session_at: datetime, now: datetime
    ) -> bool:
        """True when the 5-session time is more than
        ``outcomes.touches_give_up_sessions`` trading sessions ago."""
        give_up = int(
            self.config.get(
                "outcomes", "touches_give_up_sessions", default=2
            )
        )
        if self.calendar is None:
            return False
        day = _aware_utc(five_session_at).astimezone(NY_TZ).date()
        today = now.astimezone(NY_TZ).date()
        sessions = 0
        cursor = day
        while sessions <= give_up:
            nxt = self.calendar.next_trading_day(cursor)
            if nxt is None or nxt.date > today:
                break
            sessions += 1
            cursor = nxt.date
        return sessions > give_up

    def _window_sessions(
        self, start: datetime, end: datetime
    ) -> list[CalendarDay]:
        """Trading sessions overlapping [start, end], time-ordered."""
        assert self.calendar is not None  # noqa: S101 - internal invariant
        start_u = _aware_utc(start)
        end_u = _aware_utc(end)
        sessions: list[CalendarDay] = []
        session = self.calendar.session_for(start_u.astimezone(NY_TZ).date())
        if session is None:
            session = self.calendar.next_trading_day(
                start_u.astimezone(NY_TZ).date()
            )
        while session is not None:
            if session.open.astimezone(UTC) >= end_u:
                break
            if session.close.astimezone(UTC) > start_u:
                sessions.append(session)
            nxt = self.calendar.next_trading_day(session.date)
            if nxt is None:
                break
            session = nxt
        return sessions

    async def prefetch_touches(
        self, now: datetime | None = None
    ) -> TouchFetchReport:
        """Fetch once per session the 5m bars a touches row is missing.

        For every stale touches row whose window isn't covered by stored
        bars, fetch that symbol's bars for the window through the data
        limiter and store them, so ``fill`` can retry the touches
        (docs/SPEC.md, Option outcomes — Missing bars). A row is tried at
        most once per session: ``option_touches_tried_at`` is committed
        before any network call (hard rule 13).
        """
        report = TouchFetchReport()
        now = now or self.clock.now()
        if self.market_data is None or self.calendar is None:
            return report
        bar_source = getattr(
            getattr(self.market_data, "sources", None), "bars", None
        )
        if bar_source is None:
            return report
        cutoff = self._retention_cutoff(now)
        today = now.astimezone(NY_TZ).date()

        # (kind, symbol, start, end, sessions) for rows due a fetch.
        wanted: list[
            tuple[str, str, datetime, datetime, list[CalendarDay]]
        ] = []
        with self._session_factory() as session:
            rows = session.execute(
                select(Decision, Cycle.scheduled_time, Outcome)
                .join(Cycle, Decision.cycle_id == Cycle.id)
                .join(Outcome, Outcome.decision_id == Decision.id)
                .where(
                    Outcome.option_touches.is_(None),
                    Outcome.price_5sessions.is_not(None),
                    Decision.options.is_not(None),
                )
            ).all()
            for decision, scheduled, outcome in rows:
                if not self._touch_options(decision.options or {}):
                    continue
                if outcome.price_5sessions_at is None:
                    continue
                decision_time = _aware_utc(scheduled)
                end = _aware_utc(outcome.price_5sessions_at)
                basis = end.astimezone(NY_TZ).date()
                if cutoff is not None and basis < cutoff:
                    continue
                tried = outcome.option_touches_tried_at
                if (
                    tried is not None
                    and _aware_utc(tried).astimezone(NY_TZ).date() == today
                ):
                    continue
                sessions = self._window_sessions(decision_time, end)
                if not sessions:
                    continue
                covered = self.market_data.bars_cache.covered_session_dates(
                    decision.symbol, sessions
                )
                if (
                    covered >= {s.date for s in sessions}
                    and self.market_data.get_5m_bars(
                        decision.symbol, decision_time, end
                    )
                ):
                    continue
                outcome.option_touches_tried_at = _naive_utc(now)
                wanted.append(
                    ("decisions", decision.symbol, decision_time, end, sessions)
                )

            shadow_rows = session.execute(
                select(ShadowDecision, Cycle.scheduled_time)
                .join(Cycle, ShadowDecision.cycle_id == Cycle.id)
                .where(
                    ShadowDecision.option_touches.is_(None),
                    ShadowDecision.options.is_not(None),
                )
            ).all()
            for srow, scheduled in shadow_rows:
                if not self._touch_options(srow.options or {}):
                    continue
                decision_time = _aware_utc(scheduled)
                five = next(
                    (
                        t
                        for f, _a, t in self._targets(decision_time)
                        if f == "price_5sessions"
                    ),
                    None,
                )
                if five is None or now < five + self._delay:
                    continue
                if cutoff is not None and five.astimezone(NY_TZ).date() < cutoff:
                    continue
                tried = srow.option_touches_tried_at
                if (
                    tried is not None
                    and _aware_utc(tried).astimezone(NY_TZ).date() == today
                ):
                    continue
                sessions = self._window_sessions(decision_time, five)
                if not sessions:
                    continue
                covered = self.market_data.bars_cache.covered_session_dates(
                    srow.symbol, sessions
                )
                if (
                    covered >= {s.date for s in sessions}
                    and self.market_data.get_5m_bars(
                        srow.symbol, decision_time, five
                    )
                ):
                    continue
                srow.option_touches_tried_at = _naive_utc(now)
                wanted.append(
                    ("shadows", srow.symbol, decision_time, five, sessions)
                )
            session.commit()  # tried marks are durable before any fetch

        # Item 078: the window fetches are independent — one request per
        # row, at most concurrency.max_requests in flight; the bars still
        # land in the cache one row at a time, in row order.
        async def fetch(
            item: tuple[str, str, datetime, datetime, list[CalendarDay]],
        ) -> DataResult[list[Bar5m]] | None:
            _kind, symbol, start, end, _sessions = item
            try:
                result: DataResult[list[Bar5m]] = (
                    await bar_source.fetch_5m_bars([symbol], start, end)
                )
                return result
            except Exception:  # noqa: BLE001 - one bad row must not stop the rest
                logger.exception(
                    "option-touches fetch raised for %s", symbol
                )
                return None

        max_requests = max(
            1,
            int(self.config.get("concurrency", "max_requests", default=20)),
        )
        fetched_rows = await run_batch(
            wanted, send=fetch, limit=max_requests, kind="sync.option_touches"
        )
        for (kind, symbol, _start, _end, sessions), fetched in zip(
            wanted, fetched_rows, strict=True
        ):
            if fetched is None or isinstance(fetched, BaseException):
                continue
            if fetched.error is not None:
                logger.warning(
                    "option-touches fetch failed for %s: %s",
                    symbol,
                    fetched.error,
                )
                continue
            in_session = session_bars(fetched.data or [], sessions)
            if in_session and self.market_data.bars_cache.store(in_session):
                if kind == "decisions":
                    report.decisions += 1
                else:
                    report.shadows += 1
        return report

    def _fill_option_touches(
        self,
        session: Session,
        decision: Decision,
        outcome: Outcome,
        decision_time: datetime,
    ) -> str:
        """First stop/target touch per offered option; one of the
        ``_TOUCHES_*`` statuses."""
        assert self.market_data is not None  # noqa: S101 - internal invariant
        if outcome.option_touches is not None:
            return _TOUCHES_FILLED
        column = decision.options or {}
        # Old-format removed entries carry only {id, reason} and stay
        # null; entries without an id are skipped inside the scan.
        options_raw = self._touch_options(column)
        if not options_raw:
            # No offered options → nothing to record; marking it done
            # keeps the row out of the backfill scan.
            outcome.option_touches = {}
            return _TOUCHES_FILLED
        if outcome.price_5sessions_at is None:
            return _TOUCHES_WAITING
        # Inclusive end bound: the bar that supplied the 5-session price
        # is the last one in the window; +5m would admit the next bar.
        end = _aware_utc(outcome.price_5sessions_at)
        sessions = self._window_sessions(decision_time, end)
        wanted_dates = {s.date for s in sessions}
        if sessions and (
            self.market_data.bars_cache.covered_session_dates(
                decision.symbol, sessions
            )
            < wanted_dates
        ):
            # The window isn't covered by stored bars — the fetch-once /
            # give-up path in ``fill`` decides what happens next.
            return _TOUCHES_MISSING
        if not self.market_data.get_5m_bars(decision.symbol, decision_time, end):
            return _TOUCHES_MISSING
        snapshot = decision.input_snapshot
        constraints = (
            snapshot.get("constraints") if isinstance(snapshot, dict) else None
        )
        shared_limit = (
            constraints.get("entry_limit")
            if isinstance(constraints, dict)
            else None
        )
        # The chosen option replays like the rest unless the decision
        # really opened a position: a "blocked" enter never traded, so
        # waiting on a first_touch that cannot arrive would hold the row
        # forever (item 058).
        traded = (
            session.execute(
                select(Position.id)
                .where(Position.entry_decision_id == decision.id)
                .limit(1)
            ).first()
            is not None
        )
        touches = self._option_touches(
            options_raw,
            symbol=decision.symbol,
            decision_time=decision_time,
            end=end,
            shared_limit=shared_limit,
            traded_id=decision.chosen_option if traded else None,
            traded_touch=outcome.first_touch if traded else None,
            row_label=f"decision {decision.id}",
        )
        if touches is None:
            # The traded option's real touch still unknown — stay NULL
            # so a later pass retries.
            return _TOUCHES_WAITING
        # A null touch inside the dict is a terminal verdict (no limit
        # to test, unparseable price) — persist it, never a guessed fill.
        outcome.option_touches = touches
        return _TOUCHES_FILLED

    def _fill_shadow_touches(
        self,
        row: ShadowDecision,
        decision_time: datetime,
        five_target: datetime,
    ) -> str:
        """Counterfactual touches for one shadow decision — the same
        bar scan as the live fill (docs/SPEC.md, Decision v5)."""
        assert self.market_data is not None  # noqa: S101 - internal invariant
        column = row.options or {}
        raw = self._touch_options(column)
        if not raw:
            row.option_touches = {}
            return _TOUCHES_FILLED
        # Same window as the live fill: through the close of the bar
        # that supplied the 5-session price, not the nominal target —
        # with a data gap the two can be sessions apart.
        price_bar = self._price_at(row.symbol, five_target)
        if price_bar is None:
            return _TOUCHES_MISSING  # bars missing — fetch or give up
        end = _aware_utc(price_bar.start)
        sessions = self._window_sessions(decision_time, end)
        wanted_dates = {s.date for s in sessions}
        if sessions and (
            self.market_data.bars_cache.covered_session_dates(
                row.symbol, sessions
            )
            < wanted_dates
        ):
            return _TOUCHES_MISSING
        if not self.market_data.get_5m_bars(row.symbol, decision_time, end):
            return _TOUCHES_MISSING
        snapshot = row.input_snapshot or {}
        constraints = (
            snapshot.get("constraints") if isinstance(snapshot, dict) else None
        )
        shared_limit = (
            constraints.get("entry_limit")
            if isinstance(constraints, dict)
            else None
        )
        touches = self._option_touches(
            raw,
            symbol=row.symbol,
            decision_time=decision_time,
            end=end,
            shared_limit=shared_limit,
            # A shadow never really traded — the chosen option gets the
            # same counterfactual scan as the rest.
            row_label=f"shadow {row.id}",
        )
        if touches is None:
            return _TOUCHES_MISSING
        row.option_touches = touches
        return _TOUCHES_FILLED

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
        outcome = session.execute(
            select(Outcome).where(Outcome.decision_id == position.entry_decision_id)
        ).scalar_one_or_none()
        if outcome is None:
            return False
        # The window is fixed after the close; give late bars one delay
        # to arrive, then stop rewriting the row.
        if (
            closed is not None
            and closed + self._delay < now
            and outcome.best_price is not None
            and outcome.worst_price is not None
            and outcome.first_touch is not None
        ):
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
