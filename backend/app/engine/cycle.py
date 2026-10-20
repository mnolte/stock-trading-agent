"""The decision cycle (docs/SPEC.md, Decision layer, "One cycle").

Steps per spec: pre-checks → update data → compute indicators and the stored
snapshot → rules pre-filter → one model call per candidate or held symbol →
validate → risk gates → size and place orders → store the decision record.
Reconciliation against the broker runs at the start of every cycle.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.data.factory import DataSources
from app.data.models import NewsItem, Quote
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import Cycle, Earning, Position
from app.decide.service import DecisionService
from app.decide.snapshot import build_input_snapshot
from app.engine.executor import Executor
from app.engine.outcomes import OutcomeWorker
from app.engine.state import record_event
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, Clock, SessionClock
from app.notifications.service import Notifier, NullNotifier
from app.rules.prefilter import STATUS_HELD, STATUS_PASS
from app.signals.service import SignalService

NEWS_WINDOW = timedelta(hours=24)
# Top-up window for the 5-minute bar cache each cycle.
BAR_TOPUP = timedelta(days=3)

logger = logging.getLogger(__name__)


@dataclass
class CycleReport:
    """What happened in one cycle; also written to the cycles row."""

    cycle_id: int | None = None
    status: str = "done"  # done | skipped | error
    errors: list[str] = field(default_factory=list)
    data_source_health: dict[str, str] = field(default_factory=dict)
    calls: int = 0
    decisions: list[int] = field(default_factory=list)
    duration_seconds: float | None = None


class CycleRunner:
    """Runs one decision cycle against injected sources and clock."""

    def __init__(
        self,
        *,
        market_data: MarketDataService,
        signals: SignalService,
        decide: DecisionService,
        sources: DataSources,
        calendar: CalendarProvider,
        config: AppConfig,
        clock: Clock,
        executor: Executor | None = None,
        outcomes: OutcomeWorker | None = None,
        session_factory: sessionmaker[Session] | None = None,
        notify: Callable[[set[str]], None] | None = None,
        on_settings_promoted: Callable[[], None] | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.market_data = market_data
        self.signals = signals
        self.decide = decide
        self.sources = sources
        self.calendar = calendar
        self.config = config
        self.clock = clock
        self.executor = executor
        self.outcomes = outcomes
        self._session_factory = session_factory or get_session_maker()
        self._notify = notify or (lambda topics: None)
        self._notifier = notifier or NullNotifier()
        # Reschedules cron jobs when promoted settings move cycle times.
        self._on_settings_promoted = on_settings_promoted or (lambda: None)

    # ------------------------------------------------------------------
    # scheduling helpers

    def due_cycle(self, now: datetime) -> datetime | None:
        """Latest active cycle time today that is due at ``now`` (NY tz)."""
        today = now.astimezone(NY_TZ).date()
        session = self.calendar.session_for(today)
        if session is None:
            return None
        cycles = SessionClock(self.clock, self.config, self.calendar).active_cycles(
            session
        )
        due = [c for c in cycles if c <= now.astimezone(NY_TZ)]
        return due[-1] if due else None

    def already_ran(self, scheduled_ny: datetime) -> bool:
        with self._session_factory() as session:
            row = session.execute(
                select(Cycle.id).where(
                    Cycle.kind == "cycle",
                    Cycle.scheduled_time == _naive_utc(scheduled_ny),
                )
            ).first()
        return row is not None

    # ------------------------------------------------------------------
    # pre-checks

    async def _market_open(self, report: CycleReport) -> bool:
        """Market open per the Alpaca clock; simulated mode uses session state."""
        if self.sources.alpaca is not None:
            clock_result = await self.sources.alpaca.fetch_clock()
            if clock_result.error is not None or clock_result.data is None:
                report.errors.append(f"alpaca clock: {clock_result.error}")
                report.data_source_health["clock"] = "error"
                return False
            report.data_source_health["clock"] = "ok"
            return clock_result.data.is_open
        state = SessionClock(self.clock, self.config, self.calendar).state()
        report.data_source_health["clock"] = "ok"
        return state.state in ("open", "early_close")

    # ------------------------------------------------------------------
    # data updates
    #
    # Fetch scope at scale (docs/SPEC.md, Cycles at scale): bars cover the
    # whole cycle universe in one multi-symbol request, quotes cover the
    # review set (held + rules-pass), news only the symbols that get a
    # model call.

    async def _update_bars(
        self, symbols: list[str], now: datetime, report: CycleReport
    ) -> None:
        bar_results = await self.market_data.refresh_bars(
            now - BAR_TOPUP, now, symbols
        )
        errors = {
            r.error for r in bar_results.values() if r.error is not None
        }
        if not errors:
            report.data_source_health["bars"] = "ok"
        elif len(errors) == 1:
            # One shared request failed for the whole universe — report
            # the error itself, not a list of every symbol.
            report.data_source_health["bars"] = f"failed: {errors.pop()}"
        else:
            failed = sorted(
                s for s, r in bar_results.items() if r.error is not None
            )
            report.data_source_health["bars"] = f"failed: {', '.join(failed)}"

    async def _fetch_quotes(
        self, symbols: list[str], report: CycleReport
    ) -> dict[str, Quote]:
        if not symbols:
            return {}
        quotes_result = await self.sources.quotes.fetch_quotes(symbols)
        report.data_source_health["quotes"] = (
            "ok" if quotes_result.error is None else f"failed: {quotes_result.error}"
        )
        return quotes_result.data if quotes_result.error is None else {}

    async def _fetch_news(
        self, symbols: list[str], now: datetime, report: CycleReport
    ) -> dict[str, list[NewsItem]] | None:
        """Headlines for the symbols that get a model call; None on error."""
        if not symbols:
            return {}
        news_result = await self.sources.news.fetch_news(
            symbols, now - NEWS_WINDOW, now
        )
        if news_result.error is not None:
            report.data_source_health["news"] = f"failed: {news_result.error}"
            return None
        news: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}
        for item in news_result.data:
            news.setdefault(item.symbol, []).append(item)
        report.data_source_health["news"] = "ok"
        return news

    # ------------------------------------------------------------------
    # earnings exits

    def _is_last_cycle(self, now: datetime) -> bool:
        """True when ``now`` is at/past the session's last active cycle."""
        today = now.astimezone(NY_TZ).date()
        session = self.calendar.session_for(today)
        if session is None:
            return False
        cycles = SessionClock(self.clock, self.config, self.calendar).active_cycles(
            session
        )
        if not cycles:
            return True
        return now.astimezone(NY_TZ) >= cycles[-1]

    def _earnings_map(
        self, session: Session, symbols: list[str], now: datetime
    ) -> dict[str, tuple[date, str]]:
        """Next earnings (date, time_of_day) per symbol, today or later."""
        today = now.astimezone(NY_TZ).date()
        rows = session.execute(
            select(Earning.symbol, Earning.date, Earning.time_of_day)
            .where(
                Earning.symbol.in_(symbols),
                Earning.date >= datetime.combine(today, datetime.min.time()),
            )
            .order_by(Earning.date)
        ).all()
        result: dict[str, tuple[date, str]] = {}
        for symbol, day, tod in rows:
            result.setdefault(symbol, (day.date(), tod))
        return result

    def _earnings_exit_due(
        self,
        position: Position,
        earnings: dict[str, tuple[date, str]],
        now: datetime,
        last_cycle: bool,
    ) -> bool:
        """Held symbol reports before the next session's close → exit in the
        last cycle the day before. Unknown time of day counts as before open."""
        if not last_cycle:
            return False
        if self.config.get("risk", "hold_through_earnings", default=False):
            return False
        info = earnings.get(position.symbol)
        if info is None:
            return False
        event_date, time_of_day = info
        today = now.astimezone(NY_TZ).date()
        # A report after today's close still counts as "before the next
        # session's close": sell in this last cycle so the position is never
        # held through the release.
        if event_date == today:
            return time_of_day == "after_close"
        next_day = self.calendar.next_trading_day(today)
        if next_day is None or event_date != next_day.date:
            return False
        # Reports at next close (after_close) are handled at that session's
        # last cycle, not today's; an unknown time counts as before open.
        return time_of_day != "after_close"

    # ------------------------------------------------------------------
    # the cycle

    async def run(self, scheduled_ny: datetime) -> CycleReport:
        """Run one cycle for the given New York scheduled time."""
        now = self.clock.now()
        report = CycleReport()
        with self._session_factory() as session:
            cycle = Cycle(
                kind="cycle",
                scheduled_time=_naive_utc(scheduled_ny),
                started_at=_naive_utc(now),
                status="running",
            )
            session.add(cycle)
            session.commit()
            report.cycle_id = cycle.id
            # The dashboard's cycle counter switches to "running" on this
            # push, and back to the countdown on the one after commit.
            self._notify({"header", "decisions"})

            try:
                await self._run(session, cycle.id, scheduled_ny, now, report)
            except Exception as exc:  # noqa: BLE001
                report.status = "error"
                report.errors.append(str(exc))
                logger.error(
                    "Cycle %s failed at %s: %s",
                    cycle.id,
                    scheduled_ny,
                    exc,
                    exc_info=True,
                )

            # Post-decision outcomes and the equity snapshot are part of
            # every cycle, whatever the decision stage produced.
            if self.outcomes is not None:
                self.outcomes.fill(session, self.clock.now())
                account = (
                    self.executor.state.account
                    if self.executor is not None
                    else None
                )
                if account is not None:
                    self.outcomes.write_equity_snapshot(
                        session, equity=account.equity, cash=account.cash
                    )

            cycle.status = report.status
            cycle.finished_at = _naive_utc(self.clock.now())
            cycle.errors = report.errors or None
            cycle.data_source_health = report.data_source_health or None
            if cycle.started_at is not None and cycle.finished_at is not None:
                cycle.duration_seconds = (
                    cycle.finished_at - cycle.started_at
                ).total_seconds()
            report.duration_seconds = cycle.duration_seconds
            # A cycle slower than the configured limit raises a warning
            # banner — at scale the screen, fetches and model calls add up
            # (docs/SPEC.md, Cycles at scale).
            warn_minutes = float(
                self.config.get(
                    "schedule", "cycle_duration_warn_minutes", default=10
                )
            )
            if (
                cycle.duration_seconds is not None
                and cycle.duration_seconds > warn_minutes * 60
            ):
                record_event(
                    session,
                    self.clock,
                    level="warning",
                    event_type="cycle_slow",
                    details={
                        "cycle_id": cycle.id,
                        "scheduled_time": scheduled_ny.isoformat(),
                        "duration_seconds": round(cycle.duration_seconds, 1),
                        "warn_minutes": warn_minutes,
                    },
                )
            session.commit()
        self._notify({"header", "decisions", "banners"})
        failing = {
            name: status
            for name, status in report.data_source_health.items()
            if status != "ok"
        }
        if failing:
            self._notifier.notify(
                "source_failing",
                ", ".join(f"{name} ({status})" for name, status in failing.items()),
                link="/settings",
            )
        await self._count_cycle_outcome(report)
        return report

    async def _count_cycle_outcome(self, report: CycleReport) -> None:
        """Three failed cycles in a row turn the kill switch on."""
        if self.executor is None:
            return
        state = self.executor.state
        if report.status == "error":
            state.failed_cycles += 1
            kill_after = int(
                self.config.get("risk", "failed_cycles_kill", default=3)
            )
            if (
                state.failed_cycles >= kill_after
                and not state.kill_switch_on
            ):
                await self.executor.activate_kill_switch(
                    reason=(
                        f"{state.failed_cycles} cycles failed in a row: "
                        f"{report.errors[0] if report.errors else 'unknown'}"
                    ),
                    automatic=True,
                )
        elif report.status == "done":
            state.failed_cycles = 0

    async def _run(
        self,
        session: Session,
        cycle_id: int,
        scheduled_ny: datetime,
        now: datetime,
        report: CycleReport,
    ) -> None:
        # Pre-checks: calendar loaded, market open, then reconcile with the
        # broker. A failed reconciliation blocks entries through the gates.
        if not getattr(self.calendar, "is_loaded", True):
            report.status = "skipped"
            report.errors.append("trading calendar unavailable")
            return
        if not await self._market_open(report):
            report.status = "skipped"
            report.errors.append("market not open")
            return

        # A full cycle starts here: pending approvals expire and saved
        # settings promote to active (docs/SPEC.md, Settings, Supervised).
        if self.executor is not None:
            self.executor.expire_pending_approvals()
        promoted = self.config.promote_pending()
        if promoted is not None:
            self.decide.reload()
            if self.config.reschedule_cycles:
                self.config.reschedule_cycles = False
                self._on_settings_promoted()
            self._notify({"watchlist", "header", "decisions"})

        account = None
        if self.executor is not None:
            self.executor.begin_cycle()
            rec = await self.executor.reconcile()
            report.data_source_health["broker"] = (
                "ok" if rec.ok else f"failed: {'; '.join(rec.errors)}"
            )
            account = await self.executor.refresh_account()
            if account is None:
                report.data_source_health["account"] = "error"

        symbols = self.market_data.universe()
        await self._update_bars(symbols, now, report)
        # The 1h shock-brake leg runs here, on the hourly cycle, so the
        # just-topped-up cache holds the completed bar (docs/SPEC.md,
        # Position check).
        if self.executor is not None:
            await self.executor.update_session_brakes(include_1h=True)
        evaluation = await self.signals.refresh_eval()

        positions = {
            row.symbol: row
            for row in session.execute(
                select(Position).where(Position.closed_at.is_(None))
            ).scalars()
        }

        # Held positions are always reviewed — except adopted/mismatched
        # ones awaiting acknowledgment, which get no model call and no
        # exits. Candidates passed the rules. Held symbols come first so
        # they take call slots before candidates when the cap binds.
        reviews = [
            item
            for item in evaluation.items
            if item.rules.status == STATUS_HELD
            and not (
                positions.get(item.symbol) is not None
                and positions[item.symbol].pending_ack
            )
        ] + [
            item
            for item in evaluation.items
            if item.rules.status == STATUS_PASS
        ]

        # Quotes cover the review set — a candidate without a quote can
        # never enter and skips its model call. The called set is the
        # review order walked until the call cap fills; news is fetched
        # for exactly those symbols (docs/SPEC.md, Cycles at scale).
        quotes = await self._fetch_quotes(
            [item.symbol for item in reviews], report
        )
        max_calls = int(
            self.config.get("decision", "max_calls_per_cycle", default=10)
        )
        called: list[str] = []
        for item in reviews:
            if len(called) >= max_calls:
                break
            if item.rules.status == STATUS_HELD or item.symbol in quotes:
                called.append(item.symbol)
        news = await self._fetch_news(called, now, report)

        earnings = self._earnings_map(
            session, [item.symbol for item in reviews], now
        )
        last_cycle = self._is_last_cycle(now)
        earnings_result = self.market_data.latest_earnings
        earnings_known = (
            earnings_result is None or earnings_result.error is None
        )
        if not earnings_known and earnings_result is not None:
            self._notifier.notify(
                "source_failing",
                "earnings calendar unavailable "
                f"({earnings_result.error}) — "
                "entries stay blocked",
                link="/settings",
            )

        # Symbols the rules reject still get a stored decision — "no_call"
        # with reason "rules" — so the inspector shows why they were skipped.
        review_symbols = {item.symbol for item in reviews}
        for item in evaluation.items:
            if item.symbol in review_symbols:
                continue
            snapshot_json = build_input_snapshot(
                symbol=item.symbol,
                context="candidate",
                cycle_time_ny=scheduled_ny,
                price=item.price,
                regime=evaluation.regime,
                snapshot=item.snapshot,
                bars_1h=item.bars_1h,
                position=None,
                news=None,
                rank=item.rank,
            )
            decision = self.decide.record_no_call(
                session,
                cycle_id=cycle_id,
                symbol=item.symbol,
                context="candidate",
                snapshot_json=snapshot_json,
                rules=item.rules,
                reason="rules",
                detail=f"rules: {item.rules.status}",
            )
            report.decisions.append(decision.id)
        capped_at: int | None = None
        for i, item in enumerate(reviews):
            if report.calls >= max_calls:
                capped_at = i
                break
            quote = quotes.get(item.symbol)
            price = quote.ask if quote is not None else None
            position = positions.get(item.symbol)
            context = "held" if item.rules.status == STATUS_HELD else "candidate"
            snapshot_json = build_input_snapshot(
                symbol=item.symbol,
                context=context,
                cycle_time_ny=scheduled_ny,
                price=price,
                regime=evaluation.regime,
                snapshot=item.snapshot,
                bars_1h=item.bars_1h,
                position=position,
                news=None if news is None else news.get(item.symbol),
                rank=item.rank,
            )
            if context == "candidate" and price is None:
                # Missing quotes mean no new entries this cycle — and no
                # model call, so the cap slot stays free for the next one.
                decision = self.decide.record_no_call(
                    session,
                    cycle_id=cycle_id,
                    symbol=item.symbol,
                    context=context,
                    snapshot_json=snapshot_json,
                    rules=item.rules,
                    reason="quote_missing",
                    detail="quote unavailable",
                )
            else:
                report.calls += 1
                decision = await self.decide.decide(
                    session,
                    cycle_id=cycle_id,
                    symbol=item.symbol,
                    context=context,
                    snapshot_json=snapshot_json,
                    rules=item.rules,
                    price=price,
                    atr_1h=item.snapshot.atr_1h,
                    current_stop=(
                        position.current_stop_price if position else None
                    ),
                )
            if self.executor is not None:
                force_exit = (
                    "earnings_exit"
                    if (
                        position is not None
                        and self._earnings_exit_due(
                            position, earnings, now, last_cycle
                        )
                    )
                    else None
                )
                await self.executor.apply_decision(
                    session,
                    decision,
                    item,
                    position,
                    quote,
                    scheduled_ny,
                    account=account,
                    earnings_known=earnings_known,
                    force_exit_reason=force_exit,
                )
            session.commit()
            report.decisions.append(decision.id)
        # Review items past the cap are recorded without a call — the tail
        # starts where the loop stopped, not at ``max_calls``, since
        # quote-missing candidates never consumed a call slot.
        for item in reviews[capped_at if capped_at is not None else len(reviews):]:
            decision = self.decide.record_no_call(
                session,
                cycle_id=cycle_id,
                symbol=item.symbol,
                context="held" if item.rules.status == STATUS_HELD else "candidate",
                snapshot_json=build_input_snapshot(
                    symbol=item.symbol,
                    context=(
                        "held" if item.rules.status == STATUS_HELD else "candidate"
                    ),
                    cycle_time_ny=scheduled_ny,
                    price=None,
                    regime=evaluation.regime,
                    snapshot=item.snapshot,
                    bars_1h=item.bars_1h,
                    position=positions.get(item.symbol),
                    news=None,
                    rank=item.rank,
                ),
                rules=item.rules,
                reason="call_cap",
                detail="call cap reached",
            )
            report.decisions.append(decision.id)
        session.commit()
        self._notify({"decisions", "watchlist", "header", "banners", "performance"})

    async def run_due(self) -> CycleReport | None:
        """Catch-up: run today's latest due cycle once, if it has not run.

        Used in simulated mode at startup so a frozen clock still produces a
        stored cycle; crash recovery for live runs is part of PR 8.
        """
        scheduled = self.due_cycle(self.clock.now())
        if scheduled is None or self.already_ran(scheduled):
            return None
        return await self.run(scheduled)
