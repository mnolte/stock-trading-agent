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
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _aware_utc, _naive_utc
from app.data.factory import DataSources
from app.data.models import NewsItem, Quote
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Position
from app.decide.output import decision_trade_prices
from app.decide.service import DecisionService
from app.decide.snapshot import build_input_snapshot
from app.decide.v2inputs import HeldContext, V2Build, build_v2_fields
from app.decide.v3 import build_v3_fields, held_v3
from app.engine.executor import Executor
from app.engine.live_calls import LiveModelHealth
from app.engine.outcomes import OutcomeWorker
from app.engine.selection import ScoredEntry, SelectionBudget, choose, score
from app.engine.state import record_event
from app.inspector.record import sync_llm_call
from app.llm.base import ModelResponse
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, UTC, Clock, SessionClock
from app.notifications.service import Notifier, NullNotifier
from app.risk.earnings import earnings_exit_due, next_earnings
from app.risk.gates import evaluate_entry_gates
from app.risk.math import (
    committed_entries,
    open_risk,
    position_size,
    streak_risk_factor,
    trade_risk,
)
from app.rules.prefilter import STATUS_HELD, STATUS_PASS
from app.signals.service import SignalService, SymbolEval

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
    # Snapshots built for symbols only shadow strategies hold; live makes
    # no rules check, model call or decision for them.
    shadow_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)


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
        on_cycle_done: Callable[[CycleReport], None] | None = None,
        model_health: LiveModelHealth | None = None,
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
        # Shadow strategies start after a finished cycle (docs/SPEC.md,
        # Running shadows). Main schedules them as their own task, so this
        # call returns at once and never delays live.
        self.on_cycle_done = on_cycle_done or (lambda report: None)
        # Symbols held by active or paused shadows (set by main).
        self.shadow_symbols: Callable[[], list[str]] = lambda: []
        # "Live decisions failing" episode tracker (docs/SPEC.md,
        # Decision layer); only the live app sets it — shadows, reviews
        # and backtests never see one.
        self.model_health = model_health

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
        # "Last cycle done" is a display state: the market is still open
        # until the close, and at the last cycle's own minute the state
        # already reads it — the 15:46 cycle must still run.
        return state.state in ("open", "early_close", "last_cycle_done")

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

    def _price_reason(self, symbol: str, quote: Quote | None, now: datetime) -> str | None:
        if quote is None:
            return "price_stale"
        bar = self.market_data.bars_cache.latest(symbol, now - timedelta(minutes=5))
        return quote.price_reason(
            now=now,
            max_age_seconds=float(self.config.get("execution", "quote_max_age_seconds")),
            bar_close=bar.close if bar is not None else None,
            max_deviation_pct=Decimal(str(self.config.get("execution", "quote_max_deviation_pct"))),
        )

    async def _fetch_news(
        self, symbols: list[str], now: datetime, report: CycleReport
    ) -> dict[str, list[NewsItem]] | None:
        """Headlines for the symbols that get a model call; None on error."""
        self._news_status: dict[str, str] = {}
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
        self._news_status = news_result.statuses or {
            symbol: "complete" if news[symbol] else "empty" for symbol in symbols
        }
        incomplete = [
            s for s, status in self._news_status.items() if status in ("failed", "truncated")
        ]
        report.data_source_health["news"] = (
            f"incomplete: {', '.join(incomplete)}" if incomplete else "ok"
        )
        return news

    # ------------------------------------------------------------------
    # earnings exits

    def _is_last_cycle(self, now: datetime) -> bool:
        """True when ``now`` is at/past the session's last active cycle."""
        return SessionClock(self.clock, self.config, self.calendar).is_last_cycle(now)

    def _earnings_map(
        self, session: Session, symbols: list[str], now: datetime
    ) -> dict[str, tuple[date, str]]:
        """Next earnings (date, time_of_day) per symbol, today or later."""
        return next_earnings(session, symbols, now)

    def _earnings_exit_due(
        self,
        position: Position,
        earnings: dict[str, tuple[date, str]],
        now: datetime,
        last_cycle: bool,
    ) -> bool:
        """The shared earnings-exit rule (app.risk.earnings)."""
        return earnings_exit_due(
            position.symbol,
            earnings,
            now,
            last_cycle=last_cycle,
            calendar=self.calendar,
            config=self.config,
        )

    # ------------------------------------------------------------------
    # the cycle

    def record_missing_cycles(self, reason: str) -> int:
        now = self.clock.now()
        today = now.astimezone(NY_TZ).date()
        grace = timedelta(seconds=int(self.config.get("schedule", "misfire_grace_seconds")))
        schedule = SessionClock(self.clock, self.config, self.calendar)
        count = 0
        with self._session_factory() as session:
            latest = session.execute(
                select(func.max(Cycle.scheduled_time)).where(Cycle.kind == "cycle")
            ).scalar_one()
            day = latest.replace(tzinfo=UTC).astimezone(NY_TZ).date() if latest else today
            existing = set(session.execute(select(Cycle.scheduled_time).where(
                Cycle.kind == "cycle", Cycle.scheduled_time >= _naive_utc(
                    datetime.combine(day, datetime.min.time(), tzinfo=NY_TZ)
                ),
            )).scalars())
            while day <= today:
                trading = self.calendar.session_for(day)
                if trading is not None:
                    for at in schedule.active_cycles(trading):
                        if at < now - grace and _naive_utc(at) not in existing:
                            session.add(Cycle(
                                kind="cycle", scheduled_time=_naive_utc(at), status="skipped",
                                finished_at=_naive_utc(now), errors=[reason],
                            ))
                            count += 1
                day += timedelta(days=1)
            session.commit()
        return count

    async def run(self, scheduled_ny: datetime) -> CycleReport:
        """Run one cycle for the given New York scheduled time."""
        from app.logging_config import ev, log_context

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

            with log_context(cycle=f"c-{cycle.id:04d}"):
                ev(logger, logging.INFO, "cycle.start",
                   f"Cycle c-{cycle.id:04d} started ({scheduled_ny:%H:%M} NY)")
                try:
                    await self._run(session, cycle.id, scheduled_ny, now, report)
                except Exception as exc:  # noqa: BLE001
                    report.status = "error"
                    report.errors.append(str(exc))
                    logger.exception(
                        "Cycle %s failed at %s: %s",
                        report.cycle_id,
                        scheduled_ny,
                        exc,
                    )
                    session.rollback()
                    stored = session.get(Cycle, report.cycle_id)
                    assert stored is not None  # noqa: S101 - internal invariant
                    cycle = stored

                # Post-decision outcomes and the equity snapshot are part of
                # every cycle, whatever the decision stage produced.
                if self.outcomes is not None:
                    try:
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
                    except Exception as exc:
                        report.status = "error"
                        report.errors.append(str(exc))
                        logger.exception("cycle %s outcomes failed", report.cycle_id)
                        session.rollback()
                        stored = session.get(Cycle, report.cycle_id)
                        assert stored is not None  # noqa: S101 - internal invariant
                        cycle = stored

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
                    ev(logger, logging.WARNING, "cycle.slow",
                       f"Cycle c-{cycle.id:04d} took "
                       f"{cycle.duration_seconds:.0f}s (>{warn_minutes}m)")
                session.commit()
                ev(
                    logger, logging.INFO, "cycle.end",
                    f"Cycle c-{cycle.id:04d} {report.status} in "
                    f"{(cycle.duration_seconds or 0):.1f}s",
                    symbols=len(report.decisions),
                    calls=report.calls,
                    decisions=len(report.decisions),
                    status=report.status,
                )
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
        if report.status == "done" and report.cycle_id is not None:
            try:
                self.on_cycle_done(report)
            except Exception:  # noqa: BLE001
                logger.exception("shadow hook failed after cycle %s", report.cycle_id)
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
        from app.logging_config import ev

        if not getattr(self.calendar, "is_loaded", True):
            report.status = "skipped"
            report.errors.append("trading calendar unavailable")
            ev(logger, logging.INFO, "cycle.skipped",
               "trading calendar unavailable")
            return
        if not await self._market_open(report):
            report.status = "skipped"
            report.errors.append("market not open")
            ev(logger, logging.INFO, "cycle.skipped", "market not open")
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
        ev(logger, logging.DEBUG, "cycle.universe",
           f"universe: {len(symbols)} symbols", symbols=symbols)
        try:
            shadow_only = [s for s in self.shadow_symbols() if s not in symbols]
        except Exception:  # noqa: BLE001
            logger.exception("shadow-held symbols unavailable")
            shadow_only = []
        # One bar request covers shadow-held symbols too (docs/SPEC.md,
        # Running shadows); they get no rules check or decision.
        await self._update_bars(symbols + shadow_only, now, report)
        # The 1h shock-brake leg runs here, on the hourly cycle, so the
        # just-topped-up cache holds the completed bar (docs/SPEC.md,
        # Position check).
        if self.executor is not None:
            await self.executor.update_session_brakes(include_1h=True)
        evaluation = await self.signals.refresh_eval()
        if shadow_only:
            try:
                for symbol, (snap, bars_1h, _daily, _todays) in (
                    self.signals.snapshots_for(shadow_only, now).items()
                ):
                    report.shadow_snapshots[symbol] = build_input_snapshot(
                        symbol=symbol,
                        context="held",
                        cycle_time_ny=scheduled_ny,
                        price=snap.price,
                        regime=evaluation.regime,
                        snapshot=snap,
                        bars_1h=bars_1h,
                        position=None,
                        news=None,
                        last_bar=self.market_data.bars_cache.latest(
                            symbol, now - timedelta(minutes=5),
                        ),
                    )
            except Exception:  # noqa: BLE001
                # Never let shadow inputs affect the live cycle.
                logger.exception("shadow-held snapshots failed")

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

        # Quotes cover the review set — a candidate without a usable
        # market price can never enter and skips its model call. The
        # called set is the review order walked until the call cap fills;
        # news is fetched for exactly those symbols (docs/SPEC.md, Cycles
        # at scale).
        quotes = await self._fetch_quotes(
            [item.symbol for item in reviews], report
        )
        max_calls = int(
            self.config.get("decision", "max_calls_per_cycle", default=10)
        )
        ep_mode = (
            str(self.config.get("selection", "mode", default="rank"))
            == "expected_profit"
            and self.executor is not None
        )
        # The called set decides the news fetch. Rank order walks the
        # review list; expected_profit decides its call order inside the
        # selection path and fetches news for exactly those symbols.
        called: list[str] = []
        if not ep_mode:
            for item in reviews:
                if len(called) >= max_calls:
                    break
                # Held positions are always reviewed; a stale or suspect
                # market price only blocks a tighten_stop (docs/SPEC.md,
                # Entries — Market price).
                if (
                    item.rules.status == STATUS_HELD
                    or self._price_reason(item.symbol, quotes.get(item.symbol), now) is None
                ):
                    called.append(item.symbol)
            news = await self._fetch_news(called, now, report)
        else:
            news = None

        earnings = self._earnings_map(
            session, [item.symbol for item in reviews], now
        )
        last_cycle = self._is_last_cycle(now)
        earnings_result = self.market_data.latest_earnings
        # None means the calendar was never fetched — same unavailable as a
        # failed refresh; missing earnings data blocks entries (docs/SPEC.md,
        # Degradation rules). The signals service reads it identically.
        earnings_known = (
            earnings_result is not None and earnings_result.error is None
        )
        if not earnings_known and earnings_result is not None:
            self._notifier.notify(
                "source_failing",
                "earnings calendar unavailable "
                f"({earnings_result.error}) — "
                "entries stay blocked",
                link="/settings",
            )

        # Symbols the rules reject — and held positions skipped for a
        # pending acknowledgment — still get a stored decision: "no_call"
        # with reason "rules", so the inspector shows why they were skipped.
        rules_log = logging.getLogger("app.rules")
        review_symbols = {item.symbol for item in reviews}
        for item in evaluation.items:
            ev(
                rules_log, logging.DEBUG, "rules.eval",
                f"{item.symbol}: {item.rules.status}"
                + (f" — {item.rules.reason}" if item.rules.reason else ""),
                symbol=item.symbol,
                checks={
                    c.name: {"passed": c.passed, "detail": c.detail}
                    for c in item.rules.checks
                },
            )
            if item.symbol in review_symbols:
                continue
            position = positions.get(item.symbol)
            held = item.rules.status == STATUS_HELD
            context = "held" if held else "candidate"
            snapshot_json = build_input_snapshot(
                symbol=item.symbol,
                context=context,
                cycle_time_ny=scheduled_ny,
                price=item.price if not held else None,
                regime=evaluation.regime,
                snapshot=item.snapshot,
                bars_1h=item.bars_1h,
                position=position,
                news=None,
                rank=item.rank,
                last_bar=self.market_data.bars_cache.latest(
                    item.symbol, now - timedelta(minutes=5),
                ),
            )
            decision = self.decide.record_no_call(
                session,
                cycle_id=cycle_id,
                symbol=item.symbol,
                context=context,
                snapshot_json=snapshot_json,
                rules=item.rules,
                reason="rules",
                detail=(
                    "pending acknowledgment"
                    if held and position is not None and position.pending_ack
                    else f"rules: {item.rules.status}"
                ),
            )
            report.decisions.append(decision.id)
        # Hard rule 13: the recorded no_call decisions are durable before
        # the first model call awaits I/O.
        session.commit()
        capped_at: int | None = None
        # Real model calls this cycle — the live-decisions-failing check
        # reads them once every symbol settled (docs/SPEC.md, Decision
        # layer).
        call_responses: list[ModelResponse] = []
        if ep_mode:
            await self._run_expected_profit(
                session,
                cycle_id,
                reviews,
                quotes,
                positions,
                evaluation,
                earnings,
                last_cycle,
                earnings_known,
                scheduled_ny,
                now,
                report,
                call_responses,
                max_calls,
                account,
            )
        else:
            for i, item in enumerate(reviews):
                if report.calls >= max_calls:
                    capped_at = i
                    break
                quote = quotes.get(item.symbol)
                decision = await self._decide_item(
                    session,
                    cycle_id,
                    item,
                    quote,
                    position=positions.get(item.symbol),
                    news_map=news,
                    earnings=earnings,
                    last_cycle=last_cycle,
                    evaluation=evaluation,
                    scheduled_ny=scheduled_ny,
                    now=now,
                    report=report,
                    call_responses=call_responses,
                )
                if self.executor is not None:
                    await self._apply_item(
                        session,
                        item,
                        decision,
                        positions.get(item.symbol),
                        quote,
                        scheduled_ny,
                        now=now,
                        account=account,
                        earnings=earnings,
                        last_cycle=last_cycle,
                        earnings_known=earnings_known,
                    )
                session.commit()
                report.decisions.append(decision.id)
            # Review items past the cap are recorded without a call — the
            # tail starts where the loop stopped, not at ``max_calls``,
            # since quote-missing candidates never consumed a call slot.
            for item in reviews[
                capped_at if capped_at is not None else len(reviews):
            ]:
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
                    last_bar=self.market_data.bars_cache.latest(
                        item.symbol, now - timedelta(minutes=5),
                    ),
                    ),
                    rules=item.rules,
                    reason="call_cap",
                    detail="call cap reached",
                )
                report.decisions.append(decision.id)
            session.commit()
        if self.model_health is not None:
            self.model_health.settle_cycle(session, call_responses)
            session.commit()
        self._notify({"decisions", "watchlist", "header", "banners", "performance"})

    # ------------------------------------------------------------------
    # per-item pipeline (shared by rank and expected_profit paths)

    async def _decide_item(
        self,
        session: Session,
        cycle_id: int,
        item: Any,
        quote: Quote | None,
        *,
        position: Position | None,
        news_map: dict[str, list[NewsItem]] | None,
        earnings: dict[str, tuple[date, str]],
        last_cycle: bool,
        evaluation: Any,
        scheduled_ny: datetime,
        now: datetime,
        report: CycleReport,
        call_responses: list[ModelResponse],
    ) -> Decision:
        """Build the snapshot and options, then either record the code
        no_call or run the model call — one stored decision either way."""
        from app.logging_config import ev

        context = "held" if item.rules.status == STATUS_HELD else "candidate"
        price_reason = self._price_reason(item.symbol, quote, now)
        price = (
            quote.last
            if quote is not None and price_reason is None
            else None
        )
        dev_pct = Decimal(
            str(self.config.get("execution", "quote_max_deviation_pct"))
        )
        snapshot_json = build_input_snapshot(
            symbol=item.symbol,
            context=context,
            cycle_time_ny=scheduled_ny,
            price=price,
            regime=evaluation.regime,
            snapshot=item.snapshot,
            bars_1h=item.bars_1h,
            position=position,
            news=None if news_map is None else news_map.get(item.symbol),
            rank=item.rank,
            last_bar=self.market_data.bars_cache.latest(
                item.symbol, now - timedelta(minutes=5),
            ),
            quote=quote,
            quote_plausible=(
                quote.quote_plausible(dev_pct) if quote is not None else False
            ),
            news_status=self._news_status.get(item.symbol, "failed"),
        )
        bar = self.market_data.bars_cache.latest(item.symbol, now - timedelta(minutes=5))
        reference = quote.stop_reference(
            bar.close if bar else None, item.snapshot.atr_1h,
            entry=context == "candidate",
            max_deviation_pct=dev_pct,
        ) if quote else None
        snapshot_json["stop_reference_price"] = (
            str(reference) if reference is not None else None
        )
        snapshot_json["last_bar_close"] = str(bar.close) if bar else None
        snapshot_json["last_bar_time"] = bar.start.isoformat() if bar else None
        v2: V2Build | None = None
        if self.decide.spec.is_v3:
            entry_decision = (
                session.get(Decision, position.entry_decision_id)
                if context == "held"
                and position is not None
                and position.entry_decision_id is not None
                else None
            )
            v2 = build_v3_fields(
                context=context,
                base=snapshot_json,
                price=price if price is not None else item.price,
                reference=reference,
                atr_1h=item.snapshot.atr_1h,
                indicators=item.snapshot,
                regime=evaluation.regime,
                bars_1h=item.bars_1h,
                daily=item.daily,
                today_5m=item.today_5m,
                earnings=earnings.get(item.symbol),
                held=(
                    held_v3(position, entry_decision, earnings.get(item.symbol))
                    if context == "held" and position is not None
                    else None
                ),
                calendar=self.calendar,
                config=self.config,
                cycle_time_ny=scheduled_ny,
            )
            snapshot_json.update(v2.fields)
        elif self.decide.spec.uses_options:
            v2 = build_v2_fields(
                context=context,
                bars_1h=item.bars_1h,
                daily=item.daily,
                today_5m=item.today_5m,
                price=price if price is not None else item.price,
                reference=reference,
                atr_1h=item.snapshot.atr_1h,
                config=self.config,
                held=(
                    HeldContext(
                        entry=position.avg_entry_price,
                        current_stop=position.current_stop_price,
                        initial_stop=position.initial_stop,
                        opened_at=_aware_utc(position.opened_at),
                    )
                    if context == "held" and position is not None
                    else None
                ),
            )
            snapshot_json.update(v2.fields)
        if (
            context == "candidate"
            and v2 is not None
            and not v2.fields["options"]
        ):
            # No code-built option survived the band and R/R rules —
            # the model is never asked (docs/SPEC.md, Decision v2).
            ev(logger, logging.DEBUG, "cycle.no_options",
               f"{item.symbol}: {v2.reason}", symbol=item.symbol)
            decision = self.decide.record_no_call(
                session,
                cycle_id=cycle_id,
                symbol=item.symbol,
                context=context,
                snapshot_json=snapshot_json,
                rules=item.rules,
                reason="no_options",
                detail=v2.reason or "no option met the rules",
                v2=v2,
            )
        elif price_reason is not None and context == "candidate":
            # An unusable market price means no new entries this
            # cycle — and no model call, so the cap slot stays free
            # for the next one. Held positions are always reviewed.
            ev(logger, logging.WARNING, "cycle.price_blocked",
               f"{item.symbol}: {price_reason}", symbol=item.symbol)
            decision = self.decide.record_no_call(
                session,
                cycle_id=cycle_id,
                symbol=item.symbol,
                context=context,
                snapshot_json=snapshot_json,
                rules=item.rules,
                reason=price_reason,
                detail=price_reason,
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
                v2=v2,
                on_response=(
                    call_responses.append
                    if self.model_health is not None
                    else None
                ),
            )
        return decision

    async def _apply_item(
        self,
        session: Session,
        item: Any,
        decision: Decision,
        position: Position | None,
        quote: Quote | None,
        scheduled_ny: datetime,
        *,
        now: datetime,
        account: Any,
        earnings: dict[str, tuple[date, str]],
        last_cycle: bool,
        earnings_known: bool,
    ) -> None:
        """Gates, sizing and the order for one stored decision."""
        assert self.executor is not None  # noqa: S101 - caller checks
        # Hard rule 13: commit the stored decision before the
        # executor's quote and order calls await the broker.
        session.commit()
        force_exit = (
            "earnings_exit"
            if (
                position is not None
                and self._earnings_exit_due(position, earnings, now, last_cycle)
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
        # Fold gate changes to final_action into the call row — same
        # transaction as the decision's own update.
        sync_llm_call(session, decision)

    # ------------------------------------------------------------------
    # expected_profit selection (docs/SPEC.md, Entry selection)

    def _candidate_limit(self, quote: Quote) -> Decimal:
        offset = Decimal(
            str(self.config.get("execution", "entry_limit_offset_pct", default=0.05))
        )
        return (quote.last * (1 + offset / 100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def _sizing_state(
        self, session: Session, account: Any, earnings_known: bool
    ) -> tuple[Any, Decimal, Decimal, Decimal, dict[str, int], Decimal]:
        """(ctx, cash left, open risk in use, risk factor, sector counts,
        open-risk cap) after the held phase — the budget's starting
        state for dry runs and the set search."""
        assert self.executor is not None  # noqa: S101 - ep mode requires it
        ctx = self.executor._gate_context(  # noqa: SLF001
            session, account, "", None, earnings_known
        )
        spent = committed_entries(
            ctx.working_entries, (ctx.market_prices or {}).get
        )
        used = (
            open_risk(
                (p.qty, p.avg_entry_price, p.current_stop_price or Decimal(0))
                for p in ctx.open_positions
            )
            + spent.risk
        )
        cash = (
            max(Decimal(0), ctx.cash - spent.cash)
            if self.config.get("risk", "cash_only", default=True)
            else ctx.equity
        )
        sector_map = ctx.sectors or {}
        counts: dict[str, int] = {}
        for symbol in [p.symbol for p in ctx.open_positions] + [
            e.symbol for e in ctx.working_entries
        ]:
            sector = sector_map.get(symbol)
            if sector is not None:
                counts[sector] = counts.get(sector, 0) + 1
        cap = ctx.equity * Decimal(
            str(self.config.get("risk", "max_open_risk_pct", default=2.5))
        ) / 100
        factor = streak_risk_factor(ctx.losing_streak, self.config)
        return ctx, cash, used, factor, counts, cap

    async def _run_expected_profit(
        self,
        session: Session,
        cycle_id: int,
        reviews: list[Any],
        quotes: dict[str, Quote],
        positions: dict[str, Position],
        evaluation: Any,
        earnings: dict[str, tuple[date, str]],
        last_cycle: bool,
        earnings_known: bool,
        scheduled_ny: datetime,
        now: datetime,
        report: CycleReport,
        call_responses: list[ModelResponse],
        max_calls: int,
        account: Any,
    ) -> None:
        """Held first, then pre-score, collect, score, choose, submit.

        docs/SPEC.md, Entry selection: model calls go to candidates in
        pre-score order (dollars at risk of the first option's size);
        every candidate answer is collected before any order is sent;
        the winning set is submitted in expected-profit order through
        the unchanged gates and fresh-quote check.
        """
        held_items = [
            item for item in reviews if item.rules.status == STATUS_HELD
        ]
        cand_items = [
            item for item in reviews if item.rules.status != STATUS_HELD
        ]

        # 1. Held positions first, as in rank mode — with their news.
        news_held = await self._fetch_news(
            [it.symbol for it in held_items][:max_calls], now, report
        )
        capped_held: list[SymbolEval] = []
        for item in held_items:
            if report.calls >= max_calls:
                capped_held.append(item)
                continue
            quote = quotes.get(item.symbol)
            decision = await self._decide_item(
                session,
                cycle_id,
                item,
                quote,
                position=positions.get(item.symbol),
                news_map=news_held,
                earnings=earnings,
                last_cycle=last_cycle,
                evaluation=evaluation,
                scheduled_ny=scheduled_ny,
                now=now,
                report=report,
                call_responses=call_responses,
            )
            await self._apply_item(
                session,
                item,
                decision,
                positions.get(item.symbol),
                quote,
                scheduled_ny,
                now=now,
                account=account,
                earnings=earnings,
                last_cycle=last_cycle,
                earnings_known=earnings_known,
            )
            session.commit()
            report.decisions.append(decision.id)

        # 2. Pre-score: build the options and size option A — the
        # pre-score is the dollars at risk of that size; v1 candidates
        # have no options and keep rank order.
        ctx, cash_left, open_risk_used, risk_factor, sector_counts, _cap = (
            self._sizing_state(session, account, earnings_known)
        )
        assert self.executor is not None  # noqa: S101 - ep mode requires it
        prepared: list[dict[str, Any]] = []
        for item in cand_items:
            quote = quotes.get(item.symbol)
            if quote is None or self._price_reason(
                item.symbol, quote, now
            ) is not None:
                continue
            if not self.decide.spec.uses_options:
                prepared.append({"item": item, "quote": quote, "pre": None})
                continue
            # The options the call will offer, built on a news-less
            # snapshot: identical to what the call rebuilds (options do
            # not depend on news).
            snap = build_input_snapshot(
                symbol=item.symbol,
                context="candidate",
                cycle_time_ny=scheduled_ny,
                price=quote.last,
                regime=evaluation.regime,
                snapshot=item.snapshot,
                bars_1h=item.bars_1h,
                position=None,
                news=None,
                rank=item.rank,
                last_bar=self.market_data.bars_cache.latest(
                    item.symbol, now - timedelta(minutes=5),
                ),
            )
            bar = self.market_data.bars_cache.latest(
                item.symbol, now - timedelta(minutes=5)
            )
            dev_pct = Decimal(
                str(self.config.get("execution", "quote_max_deviation_pct"))
            )
            reference = quote.stop_reference(
                bar.close if bar else None, item.snapshot.atr_1h,
                entry=True,
                max_deviation_pct=dev_pct,
            )
            v2: V2Build | None
            if self.decide.spec.is_v3:
                v2 = build_v3_fields(
                    context="candidate",
                    base=snap,
                    price=quote.last,
                    reference=reference,
                    atr_1h=item.snapshot.atr_1h,
                    indicators=item.snapshot,
                    regime=evaluation.regime,
                    bars_1h=item.bars_1h,
                    daily=item.daily,
                    today_5m=item.today_5m,
                    earnings=earnings.get(item.symbol),
                    calendar=self.calendar,
                    config=self.config,
                    cycle_time_ny=scheduled_ny,
                )
            else:
                v2 = build_v2_fields(
                    context="candidate",
                    bars_1h=item.bars_1h,
                    daily=item.daily,
                    today_5m=item.today_5m,
                    price=quote.last,
                    reference=reference,
                    atr_1h=item.snapshot.atr_1h,
                    config=self.config,
                )
            options = v2.options_column.get("options") or []
            if not options:
                prepared.append(
                    {"item": item, "quote": quote, "pre": Decimal(0)}
                )
                continue
            first = options[0]
            sizing = position_size(
                ctx.equity,
                self._candidate_limit(quote),
                Decimal(str(first["stop"])),
                cash=cash_left,
                current_open_risk=open_risk_used,
                config=self.config,
                risk_factor=risk_factor,
            )
            pre = sizing.shares * (
                self._candidate_limit(quote) - Decimal(str(first["stop"]))
            )
            prepared.append({"item": item, "quote": quote, "pre": pre})

        prepared.sort(
            key=lambda p: (
                -(p["pre"] if p["pre"] is not None else Decimal(0)),
                p["item"].rank if p["item"].rank is not None else 10**9,
                p["item"].symbol,
            )
        )
        budget_left = max(0, max_calls - report.calls)
        called_pre = prepared[:budget_left]
        capped = prepared[budget_left:]

        # News for the candidate symbols actually called.
        news_map = await self._fetch_news(
            sorted({p["item"].symbol for p in called_pre}), now, report
        )

        # 3. Collect: every candidate answer first, nothing submitted.
        collected: list[dict[str, Any]] = []
        position_of: dict[int, int] = {}
        for i, p in enumerate(called_pre):
            item = p["item"]
            decision = await self._decide_item(
                session,
                cycle_id,
                item,
                p["quote"],
                position=None,
                news_map=news_map,
                earnings=earnings,
                last_cycle=last_cycle,
                evaluation=evaluation,
                scheduled_ny=scheduled_ny,
                now=now,
                report=report,
                call_responses=call_responses,
            )
            session.commit()
            report.decisions.append(decision.id)
            position_of[decision.id] = i + 1
            collected.append({**p, "decision": decision})
        for item in capped_held + [p["item"] for p in capped]:
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
                    last_bar=self.market_data.bars_cache.latest(
                        item.symbol, now - timedelta(minutes=5),
                    ),
                ),
                rules=item.rules,
                reason="call_cap",
                detail="call cap reached",
            )
            report.decisions.append(decision.id)
        # Price-blocked candidates get their stored no_call too — the
        # same rows the rank path records.
        for item in cand_items:
            if self._price_reason(item.symbol, quotes.get(item.symbol), now) is None:
                continue
            decision = await self._decide_item(
                session,
                cycle_id,
                item,
                quotes.get(item.symbol),
                position=None,
                news_map=news_map,
                earnings=earnings,
                last_cycle=last_cycle,
                evaluation=evaluation,
                scheduled_ny=scheduled_ny,
                now=now,
                report=report,
                call_responses=call_responses,
            )
            report.decisions.append(decision.id)
        session.commit()

        # 4. Score each enter: dry-run gates against the cycle's
        # starting state, then expected R x dollars at risk.
        scored: list[ScoredEntry] = []
        by_key: dict[str, dict[str, Any]] = {}
        weight = Decimal(
            str(self.config.get("selection", "confidence_weight", default=0.5))
        )
        max_positions = int(self.config.get("risk", "max_open_positions", default=4))
        max_orders = int(self.config.get("risk", "max_orders_per_cycle", default=6))
        sector_map = ctx.sectors or {}
        for c in collected:
            item = c["item"]
            cdec: Decision = c["decision"]
            if cdec.final_action != "enter":
                continue
            output = cdec.model_output or {}
            stop_t, target_t = decision_trade_prices(
                cdec.model_output, cdec.options, cdec.chosen_option
            )
            stop_raw = stop_t if stop_t is not None else output.get("stop_price")
            target_raw = (
                target_t if target_t is not None else output.get("target_price")
            )
            quote = c["quote"]
            if stop_raw is None or target_raw is None or quote is None:
                cdec.gates_result = {
                    "allowed": False,
                    "blocked_by": "missing_data",
                    "checks": [],
                }
                cdec.final_action = "blocked"
                sync_llm_call(session, cdec)
                continue
            stop_d = Decimal(str(stop_raw))
            target_d = Decimal(str(target_raw))
            limit = self._candidate_limit(quote)
            gctx = self.executor._gate_context(  # noqa: SLF001
                session,
                account,
                cdec.symbol,
                item.snapshot.days_to_earnings,
                earnings_known,
            )
            gate = evaluate_entry_gates(
                gctx, entry=limit, stop=stop_d, config=self.config
            )
            if not gate.allowed or gate.sizing is None:
                cdec.gates_result = gate.to_json()
                cdec.final_action = "blocked"
                sync_llm_call(session, cdec)
                continue
            risk = trade_risk(limit, stop_d, target_d)
            if risk.reward_to_risk is None or risk.reward_to_risk <= 0:
                cdec.gates_result = {
                    "allowed": False,
                    "blocked_by": "not_selected",
                    "detail": "no expected profit",
                    "checks": [],
                }
                cdec.final_action = "blocked"
                sync_llm_call(session, cdec)
                continue
            confidence = Decimal(str(output.get("confidence") or 0))
            dollars = gate.sizing.shares * (limit - stop_d)
            be, p_used, expected_r, expected_profit = score(
                reward_to_risk=risk.reward_to_risk,
                confidence=confidence,
                dollars_at_risk=dollars,
                confidence_weight=weight,
            )
            entry_scored = ScoredEntry(
                symbol=item.symbol,
                rank=item.rank,
                sector=sector_map.get(item.symbol),
                entry=limit,
                stop=stop_d,
                reward_to_risk=risk.reward_to_risk,
                breakeven=be,
                p_used=p_used,
                expected_r=expected_r,
                dollars_at_risk=dollars,
                expected_profit=expected_profit,
                decision_id=cdec.id,
                pre_score=c["pre"],
                call_position=position_of[cdec.id],
            )
            scored.append(entry_scored)
            by_key[entry_scored.key] = c

        # 5. Choose the set.
        free_slots = max_positions - (
            len(ctx.open_positions) + len(ctx.working_entries)
        )
        floor_raw = self.config.get("selection", "last_slot_min_ev_r")
        floor = (
            Decimal(str(floor_raw)) if floor_raw is not None else None
        )
        budget = SelectionBudget(
            free_slots=max(0, free_slots),
            sector_cap=int(self.config.get("risk", "max_per_sector", default=2)),
            sector_counts=sector_counts,
            open_risk_used=open_risk_used,
            open_risk_cap=_cap,
            cash=cash_left,
            orders_left=max(0, max_orders - ctx.orders_this_cycle),
            equity=ctx.equity,
            last_slot_min_ev_r=floor,
        )
        sel = choose(scored, budget, self.config, risk_factor=risk_factor)
        chosen_keys = {e.key for e in sel.chosen}
        cycle_row = session.get(Cycle, cycle_id)
        if cycle_row is not None:
            cycle_row.selection = {
                "mode": "expected_profit",
                "free_slots": budget.free_slots,
                "scored": len(scored),
                "chosen": [e.decision_id for e in sel.chosen],
                "total": str(sel.total),
                "rank_total": str(sel.rank_total),
            }
        for se in scored:
            decision = by_key[se.key]["decision"]
            decision.selection = {
                "mode": "expected_profit",
                "pre_score": str(se.pre_score) if se.pre_score is not None else None,
                "call_position": se.call_position,
                "be": str(se.breakeven),
                "p": str(se.p_used),
                "expected_r": str(se.expected_r),
                "dollars_at_risk": str(se.dollars_at_risk),
                "expected_profit": str(se.expected_profit),
                "rank": se.rank,
                "chosen": se.key in chosen_keys,
                "total": str(sel.total),
            }
            if se.key in chosen_keys:
                continue
            detail = (
                "lower expected profit" if se.expected_r > 0 else "no expected profit"
            )
            decision.gates_result = {
                "allowed": False,
                "blocked_by": "not_selected",
                "detail": detail,
                "checks": [],
            }
            decision.final_action = "blocked"
            sync_llm_call(session, decision)
        session.commit()

        # 6. Submit the chosen set in expected-profit order; on a
        # gate/fresh-quote failure the best unchosen entry that still
        # fits is tried, once.
        tried: set[str] = set()

        async def submit(se: ScoredEntry) -> bool:
            c = by_key[se.key]
            decision: Decision = c["decision"]
            decision.final_action = "enter"
            session.commit()
            await self._apply_item(
                session,
                c["item"],
                decision,
                None,
                c["quote"],
                scheduled_ny,
                now=now,
                account=account,
                earnings=earnings,
                last_cycle=last_cycle,
                earnings_known=earnings_known,
            )
            session.commit()
            return decision.final_action == "enter"

        async def fallback() -> None:
            for cand in sorted(
                (
                    s
                    for s in scored
                    if s.key not in chosen_keys and s.key not in tried
                ),
                key=lambda s: -s.expected_profit,
            ):
                tried.add(cand.key)
                c = by_key[cand.key]
                assert self.executor is not None  # noqa: S101 - ep mode
                gctx2 = self.executor._gate_context(  # noqa: SLF001
                    session,
                    account,
                    cand.symbol,
                    c["item"].snapshot.days_to_earnings,
                    earnings_known,
                )
                g = evaluate_entry_gates(
                    gctx2, entry=cand.entry, stop=cand.stop, config=self.config
                )
                if not g.allowed:
                    continue
                fdec: Decision = c["decision"]
                fdec.selection = {**(fdec.selection or {}), "chosen": True}
                await submit(cand)
                return

        for se in sel.chosen:
            if not await submit(se):
                await fallback()

    async def run_due(self) -> CycleReport | None:
        """Catch-up: run today's latest due cycle once, if it has not run.

        Used in simulated mode at startup so a frozen clock still produces a
        stored cycle; crash recovery for live runs is part of PR 8.
        """
        scheduled = self.due_cycle(self.clock.now())
        if scheduled is None or self.already_ran(scheduled):
            return None
        return await self.run(scheduled)
