"""The decision cycle (docs/SPEC.md, Decision layer, "One cycle").

Steps per spec: pre-checks → update data → compute indicators and the stored
snapshot → rules pre-filter → one model call per candidate or held symbol →
validate → store the decision record. Risk gates, sizing and order placement
land in PR 5; reconciliation against the broker lands with it. What remains
here is everything the spec needs so simulated cycles produce stored
decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import _naive_utc
from app.data.factory import DataSources
from app.data.models import NewsItem, Quote
from app.data.service import MarketDataService
from app.db.base import get_session_maker
from app.db.models import Cycle, Position
from app.decide.service import DecisionService
from app.decide.snapshot import build_input_snapshot
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, Clock, SessionClock
from app.rules.prefilter import STATUS_HELD, STATUS_PASS
from app.signals.service import SignalService

NEWS_WINDOW = timedelta(hours=24)
# Top-up window for the 5-minute bar cache each cycle.
BAR_TOPUP = timedelta(days=3)


@dataclass
class CycleReport:
    """What happened in one cycle; also written to the cycles row."""

    cycle_id: int | None = None
    status: str = "done"  # done | skipped | error
    errors: list[str] = field(default_factory=list)
    data_source_health: dict[str, str] = field(default_factory=dict)
    calls: int = 0
    decisions: list[int] = field(default_factory=list)


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
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.market_data = market_data
        self.signals = signals
        self.decide = decide
        self.sources = sources
        self.calendar = calendar
        self.config = config
        self.clock = clock
        self._session_factory = session_factory or get_session_maker()

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

    async def _update_data(
        self, symbols: list[str], now: datetime, report: CycleReport
    ) -> tuple[dict[str, Quote], dict[str, list[NewsItem]] | None]:
        bar_results = await self.market_data.refresh_bars(now - BAR_TOPUP, now)
        failed = [s for s, r in bar_results.items() if r.error is not None]
        report.data_source_health["bars"] = (
            "ok" if not failed else f"failed: {', '.join(failed)}"
        )

        quotes_result = await self.sources.quotes.fetch_quotes(symbols)
        report.data_source_health["quotes"] = (
            "ok" if quotes_result.error is None else f"failed: {quotes_result.error}"
        )
        quotes = quotes_result.data if quotes_result.error is None else {}

        news_result = await self.sources.news.fetch_news(
            symbols, now - NEWS_WINDOW, now
        )
        news: dict[str, list[NewsItem]] | None = None
        if news_result.error is None:
            news = {symbol: [] for symbol in symbols}
            for item in news_result.data:
                news.setdefault(item.symbol, []).append(item)
            report.data_source_health["news"] = "ok"
        else:
            report.data_source_health["news"] = f"failed: {news_result.error}"
        return quotes, news

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

            try:
                await self._run(session, cycle.id, scheduled_ny, now, report)
            except Exception as exc:  # noqa: BLE001
                report.status = "error"
                report.errors.append(str(exc))

            cycle.status = report.status
            cycle.finished_at = _naive_utc(self.clock.now())
            cycle.errors = report.errors or None
            cycle.data_source_health = report.data_source_health or None
            session.commit()
        return report

    async def _run(
        self,
        session: Session,
        cycle_id: int,
        scheduled_ny: datetime,
        now: datetime,
        report: CycleReport,
    ) -> None:
        # Pre-checks: calendar loaded, market open. Kill-switch and
        # reconciliation checks arrive with the broker in PR 5.
        if not getattr(self.calendar, "is_loaded", True):
            report.status = "skipped"
            report.errors.append("trading calendar unavailable")
            return
        if not await self._market_open(report):
            report.status = "skipped"
            report.errors.append("market not open")
            return

        symbols = self.market_data.universe()
        quotes, news = await self._update_data(symbols, now, report)
        evaluation = await self.signals.refresh_eval()

        positions = {
            row.symbol: row
            for row in session.execute(
                select(Position).where(Position.closed_at.is_(None))
            ).scalars()
        }

        # Held positions are always reviewed; candidates passed the rules.
        reviews = [
            item
            for item in evaluation.items
            if item.rules.status == STATUS_HELD
        ] + [
            item
            for item in evaluation.items
            if item.rules.status == STATUS_PASS
        ]
        max_calls = int(
            self.config.get("decision", "max_calls_per_cycle", default=10)
        )
        for item in reviews:
            if report.calls >= max_calls:
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
                news=None if news is None else news.get(item.symbol, []),
            )
            report.calls += 1
            if context == "candidate" and price is None:
                # Missing quotes mean no new entries this cycle.
                decision = self.decide.record_no_call(
                    session,
                    cycle_id=cycle_id,
                    symbol=item.symbol,
                    context=context,
                    snapshot_json=snapshot_json,
                    rules=item.rules,
                    reason="quote unavailable",
                )
            else:
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
            session.commit()
            report.decisions.append(decision.id)
        # Candidates beyond the call cap are recorded without a call.
        for item in reviews[max_calls:]:
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
                ),
                rules=item.rules,
                reason="call cap reached",
            )
            report.decisions.append(decision.id)
        session.commit()

    async def run_due(self) -> CycleReport | None:
        """Catch-up: run today's latest due cycle once, if it has not run.

        Used in simulated mode at startup so a frozen clock still produces a
        stored cycle; crash recovery for live runs is part of PR 8.
        """
        scheduled = self.due_cycle(self.clock.now())
        if scheduled is None or self.already_ran(scheduled):
            return None
        return await self.run(scheduled)
