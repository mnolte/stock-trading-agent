"""The shadow runner (docs/SPEC.md, Strategies and shadows — Running shadows).

After each live cycle has finished and its orders are sent, every active
shadow decides on that cycle's stored input snapshots: the market part is
reused as it is, the position part is rebuilt from the shadow's virtual
portfolio. Calls go to the shadow's held positions first, then to
candidates that pass its rules in rank order, up to the live call cap.

Shadow work never delays live. It runs as its own task after the cycle;
before every step it checks the next scheduled live cycle or position
check, and work left when that is due is recorded as ``missed``. A model
call is also bounded by the time left, so it can never run into live.

The runner reads the database and the bar cache, calls its own model
clients and nothing else: no broker client, no market-data calls, no
share of live's call budget or rate limits.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import BarCache, _naive_utc
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Position, Run, ShadowDecision, ShadowPosition, Strategy
from app.decide.client import DecisionClient, token_cost
from app.decide.output import validate_model_output
from app.decide.prompt import cached_prompt
from app.decide.service import model_output_json, rules_result_json, validation_result_json
from app.decide.snapshot import UNAVAILABLE, _position_block
from app.market.calendar import CalendarProvider
from app.market.clock import Clock, SessionClock
from app.notifications.service import Notifier, NullNotifier
from app.risk.earnings import earnings_exit_due, exit_blocked_same_day, next_earnings
from app.risk.gates import evaluate_entry_gates
from app.rules.prefilter import STATUS_PASS, RulesResult, evaluate_entry_rules
from app.shadows.portfolio import (
    BarReader,
    Book,
    FillEvent,
    LiveGateState,
    aware,
    book,
    cents,
    current_positions,
    gate_context,
    replay_bars,
    write_equity,
)
from app.shadows.strategy import NON_ARCHIVED, strategy_config, strategy_start
from app.signals.regime import MarketRegime
from app.signals.snapshot import IndicatorSnapshot

logger = logging.getLogger(__name__)

ClientFactory = Callable[[Strategy], DecisionClient]


# ----------------------------------------------------------------------
# stored snapshot → the inputs the live rule functions take


def _dec(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    if isinstance(value, str) and value == UNAVAILABLE:
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def indicators_from_snapshot(symbol: str, snap: dict[str, Any]) -> IndicatorSnapshot:
    """Rebuild the indicator values from a stored input snapshot."""
    h = snap.get("indicators_1h") or {}
    d = snap.get("indicators_daily") or {}
    dte = snap.get("days_to_earnings")
    obv = h.get("obv_trend")
    return IndicatorSnapshot(
        symbol=symbol,
        price=_dec(snap.get("price")),
        ema_fast=_dec(h.get("ema20")),
        ema_slow=_dec(h.get("ema50")),
        rsi=_dec(h.get("rsi14")),
        atr_1h=_dec(h.get("atr14")),
        session_vwap=_dec(h.get("vwap")),
        obv_trend=obv if isinstance(obv, str) and obv != UNAVAILABLE else None,
        rs_vs_spy_pct=_dec(h.get("rs_vs_spy_pct")),
        daily_close=_dec(d.get("close")),
        daily_sma_fast=_dec(d.get("sma50")),
        daily_sma_slow=_dec(d.get("sma200")),
        daily_atr_percentile=_dec(d.get("atr_percentile")),
        opening_gap_pct=_dec(d.get("gap_pct")),
        days_to_earnings=dte if isinstance(dte, int) and not isinstance(dte, bool) else None,
        earnings_available=dte != UNAVAILABLE,
    )


def regime_from_snapshot(snap: dict[str, Any], config: AppConfig) -> MarketRegime:
    raw = snap.get("regime") or {}
    trend = str(raw.get("trend") or "sideways")
    vol = str(raw.get("volatility") or "unknown")
    key = f"{trend}_{vol}"
    blocked = set(config.get("regime", "block_entries_in", default=["down_high"]))
    return MarketRegime(
        trend=trend,
        volatility=vol,
        key=key,
        label=f"{trend.capitalize()} · {vol} vol",
        blocks_entries=key in blocked,
        atr_percentile=None,
    )


def _rank_key(decision: Decision) -> tuple[int, int]:
    rank = (decision.input_snapshot or {}).get("rank")
    return (rank if isinstance(rank, int) else 10**9, decision.id)


def _virtual_position(sp: ShadowPosition) -> Position:
    """A transient live ``Position`` so the live position block is reused."""
    return Position(
        symbol=sp.symbol,
        qty=sp.qty,
        avg_entry_price=sp.entry,
        current_stop_price=sp.stop,
        target_price=sp.target,
        opened_at=sp.opened_at,
        deferred_exit=sp.deferred_exit,
    )


# ----------------------------------------------------------------------


@dataclass
class StrategyReport:
    strategy_id: int
    decisions: int = 0
    calls: int = 0
    missed: int = 0
    entries: int = 0
    paused_by_cap: bool = False
    fills: list[FillEvent] = field(default_factory=list)


@dataclass
class ShadowReport:
    cycle_id: int | None
    strategies: list[StrategyReport] = field(default_factory=list)


class _OutOfTime(Exception):
    """The next live cycle or position check is due."""


class ShadowRunner:
    """Runs every non-archived shadow after live cycles and position checks."""

    def __init__(
        self,
        *,
        config: AppConfig,
        clock: Clock,
        calendar: CalendarProvider,
        client_factory: ClientFactory,
        session_factory: sessionmaker[Session] | None = None,
        live_state: Callable[[], LiveGateState] | None = None,
        bars: BarReader | None = None,
        notifier: Notifier | None = None,
        notify: Callable[[set[str]], None] | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.calendar = calendar
        self._client_factory = client_factory
        self._sf = session_factory or get_session_maker()
        self._live_state = live_state or LiveGateState
        self._bars: BarReader = bars or BarCache(self._sf).get
        self._notifier = notifier or NullNotifier()
        self._notify = notify or (lambda topics: None)
        self._clients: dict[int, DecisionClient] = {}
        self._cap_notified: str | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # helpers

    def _session_clock(self) -> SessionClock:
        return SessionClock(self.clock, self.config, self.calendar)

    def reset_clients(self) -> None:
        """Drop cached model clients (after an OpenAI key change)."""
        self._clients.clear()

    def _client(self, strategy: Strategy) -> DecisionClient:
        client = self._clients.get(strategy.id)
        if client is None:
            client = self._client_factory(strategy)
            self._clients[strategy.id] = client
        return client

    @staticmethod
    def _run(session: Session) -> Run | None:
        return session.execute(select(Run).order_by(Run.id.desc()).limit(1)).scalar_one_or_none()

    @staticmethod
    def _strategies(session: Session) -> list[Strategy]:
        return list(
            session.execute(
                select(Strategy).where(Strategy.status.in_(NON_ARCHIVED)).order_by(Strategy.id)
            ).scalars()
        )

    def _book(self, session: Session, strategy: Strategy, run: Run, now: datetime) -> Book:
        return book(
            session,
            strategy_id=strategy.id,
            run_id=run.id,
            start_equity=run.starting_equity,
            now=now,
            bars=self._bars,
            config=self.config,
        )

    # ------------------------------------------------------------------
    # cost cap (docs/SPEC.md, Strategies and shadows — Cost)

    def month_start(self) -> datetime:
        tz = ZoneInfo(str(self.config.get("app", "timezone", default="Europe/Amsterdam")))
        first: date = self.clock.now().astimezone(tz).date().replace(day=1)
        return _naive_utc(datetime.combine(first, time.min, tzinfo=tz))

    def monthly_spend(self, session: Session) -> Decimal:
        total = session.execute(
            select(func.sum(ShadowDecision.cost)).where(
                ShadowDecision.created_at >= self.month_start()
            )
        ).scalar_one()
        return Decimal(total or 0)

    def cost_cap(self) -> Decimal:
        return Decimal(str(self.config.get("shadows", "monthly_cost_cap_usd", default=5)))

    def cap_reached(self, session: Session) -> bool:
        return self.monthly_spend(session) >= self.cost_cap()

    def _notify_cap(self, session: Session) -> None:
        month = self.month_start().isoformat()
        if self._cap_notified == month:
            return
        self._cap_notified = month
        self._notifier.notify(
            "model_failing",
            f"Shadow cost cap reached: ${self.monthly_spend(session):.2f} of "
            f"${self.cost_cap():g} — LLM shadows paused until next month",
            link="/strategies",
        )

    # ------------------------------------------------------------------
    # entry points

    async def after_cycle(self, cycle_id: int) -> ShadowReport:
        """Run every shadow on the stored snapshots of one finished cycle."""
        report = ShadowReport(cycle_id=cycle_id)
        async with self._lock:
            with self._sf() as session:
                cycle = session.get(Cycle, cycle_id)
                run = self._run(session)
                if cycle is None or run is None:
                    return report
                scheduled = aware(cycle.scheduled_time)
                deadline = self._session_clock().next_live_event(self.clock.now())
                live = self._live_state()
                for strategy in self._strategies(session):
                    if scheduled < aware(strategy_start(strategy, run.started_at)):
                        continue
                    done = session.execute(
                        select(func.count(ShadowDecision.id)).where(
                            ShadowDecision.strategy_id == strategy.id,
                            ShadowDecision.cycle_id == cycle_id,
                        )
                    ).scalar_one()
                    if done:
                        continue
                    sr = StrategyReport(strategy_id=strategy.id)
                    report.strategies.append(sr)
                    sr.fills = replay_bars(
                        session,
                        strategy_id=strategy.id,
                        run_id=run.id,
                        now=self.clock.now(),
                        bars=self._bars,
                        config=self.config,
                    )
                    await self._decide(session, strategy, cycle, run, scheduled, live, deadline, sr)
                    write_equity(
                        session,
                        strategy_id=strategy.id,
                        run_id=run.id,
                        at=scheduled,
                        b=self._book(session, strategy, run, self.clock.now()),
                    )
                    session.commit()
        self._notify({"strategies"})
        return report

    async def after_check(self) -> ShadowReport:
        """Position check: replay new bars for stops, targets and fills."""
        report = ShadowReport(cycle_id=None)
        async with self._lock:
            with self._sf() as session:
                run = self._run(session)
                if run is None:
                    return report
                now = self.clock.now()
                for strategy in self._strategies(session):
                    if now < aware(strategy_start(strategy, run.started_at)):
                        continue
                    sr = StrategyReport(strategy_id=strategy.id)
                    report.strategies.append(sr)
                    sr.fills = replay_bars(
                        session,
                        strategy_id=strategy.id,
                        run_id=run.id,
                        now=now,
                        bars=self._bars,
                        config=self.config,
                    )
                    write_equity(
                        session,
                        strategy_id=strategy.id,
                        run_id=run.id,
                        at=now,
                        b=self._book(session, strategy, run, now),
                    )
                    session.commit()
        self._notify({"strategies"})
        return report

    def flatten(self) -> int:
        """Stop & flatten: every shadow's open positions close at the next
        5-min bar's open plus slippage; working entries are cancelled."""
        now = self.clock.now()
        count = 0
        with self._sf() as session:
            run = self._run(session)
            if run is None:
                return 0
            for strategy in self._strategies(session):
                for sp in current_positions(session, strategy.id, run.id, ("pending", "open")):
                    if sp.status == "pending":
                        sp.status = "cancelled"
                        sp.closed_at = _naive_utc(now)
                        sp.exit_reason = "flatten"
                    else:
                        sp.exit_pending = "flatten"
                        sp.exit_requested_at = _naive_utc(now)
                    count += 1
            session.commit()
        self._notify({"strategies"})
        return count

    # ------------------------------------------------------------------
    # one strategy, one cycle

    async def _decide(
        self,
        session: Session,
        strategy: Strategy,
        cycle: Cycle,
        run: Run,
        scheduled: datetime,
        live: LiveGateState,
        deadline: datetime | None,
        sr: StrategyReport,
    ) -> None:
        cfg = strategy_config(self.config, strategy)
        llm = strategy.kind == "llm"
        now = self.clock.now()
        b = self._book(session, strategy, run, now)
        held = {p.symbol: p for p in b.open}
        pending_symbols = {p.symbol for p in b.pending}
        last_cycle = self._session_clock().is_last_cycle(scheduled)
        earnings = next_earnings(session, list(held), scheduled)
        max_calls = int(self.config.get("decision", "max_calls_per_cycle", default=10))
        timeout = float(self.config.get("decision", "call_timeout_seconds", default=30))

        # Earnings exits are position management — they run even while the
        # shadow is paused, out of budget or out of time.
        for symbol, sp in held.items():
            if earnings_exit_due(
                symbol,
                earnings,
                scheduled,
                last_cycle=last_cycle,
                calendar=self.calendar,
                config=self.config,
            ) and not exit_blocked_same_day(aware(sp.opened_at or sp.created_at), scheduled):
                sp.exit_pending = "earnings_exit"
                sp.exit_requested_at = _naive_utc(scheduled)

        if strategy.status != "active":
            return
        if llm and self.cap_reached(session):
            sr.paused_by_cap = True
            self._notify_cap(session)
            return

        decisions = list(
            session.execute(
                select(Decision).where(Decision.cycle_id == cycle.id).order_by(Decision.id)
            ).scalars()
        )
        held_items: list[Decision] = []
        passing: list[tuple[Decision, RulesResult]] = []
        failing: list[tuple[Decision, RulesResult]] = []
        for d in decisions:
            snap = d.input_snapshot or {}
            if d.symbol in held:
                held_items.append(d)
                continue
            rules = evaluate_entry_rules(
                d.symbol,
                snapshot=indicators_from_snapshot(d.symbol, snap),
                regime=regime_from_snapshot(snap, cfg),
                held=False,
                open_entry_order=d.symbol in pending_symbols,
                is_last_cycle=last_cycle,
                config=cfg,
            )
            (passing if rules.status == STATUS_PASS else failing).append((d, rules))
        passing.sort(key=lambda pair: _rank_key(pair[0]))

        state = {"calls": 0, "orders": 0, "out": False}

        def out_of_time() -> bool:
            if state["out"]:
                return True
            if deadline is not None:
                margin = timedelta(seconds=timeout) if llm else timedelta(0)
                if self.clock.now() + margin >= deadline:
                    state["out"] = True
            if llm and not state["out"] and self.cap_reached(session):
                # The cap hit mid-cycle: the rest is skipped like missed work.
                sr.paused_by_cap = True
                self._notify_cap(session)
                state["out"] = True
            return bool(state["out"])

        async def call(
            d: Decision,
            context: str,
            snap_json: dict[str, Any],
            price: Decimal | None,
            current_stop: Decimal | None,
            rules: RulesResult | None,
        ) -> ShadowDecision:
            assert deadline is None or deadline > self.clock.now()
            state["calls"] = int(state["calls"]) + 1
            sr.calls += 1
            prompt = cached_prompt(str(strategy.prompt_version))
            budget = (deadline - self.clock.now()).total_seconds() if deadline is not None else None
            try:
                response = await asyncio.wait_for(
                    self._client(strategy).complete(
                        system=prompt.text, user=json.dumps(snap_json, indent=2)
                    ),
                    timeout=budget,
                )
            except TimeoutError as exc:
                state["out"] = True
                raise _OutOfTime from exc
            ind = indicators_from_snapshot(d.symbol, d.input_snapshot or {})
            validation = validate_model_output(
                response.raw,
                symbol=d.symbol,
                context=context,
                price=price,
                atr_1h=ind.atr_1h,
                current_stop=current_stop,
                call_error=response.error,
                config=cfg,
            )
            row = self._row(
                strategy,
                cycle,
                d,
                context,
                final_action="no_call" if response.error else validation.final_action,
                no_call_reason="model_error" if response.error else None,
                rules=rules,
                position=snap_json.get("position"),
            )
            row.model_output = model_output_json(validation.output) if validation.output else None
            row.validation_result = validation_result_json(validation)
            row.prompt_version = prompt.version
            row.prompt_hash = prompt.sha256
            row.model = strategy.model
            row.tokens = response.tokens
            row.tokens_in = response.tokens_in
            row.tokens_out = response.tokens_out
            row.cost = token_cost(response, self.config, model=strategy.model)
            row.latency_ms = response.latency_ms
            row.raw_response = response.raw
            return row

        # ---- held positions first
        for d in held_items:
            sp = held[d.symbol]
            snap = d.input_snapshot or {}
            price = _dec(snap.get("price"))
            position_json = _position_block(_virtual_position(sp), price)
            snap_json: dict[str, Any] = copy.deepcopy(dict(snap))
            snap_json["context"] = "held"
            snap_json["position"] = position_json
            if out_of_time():
                self._missed(session, strategy, cycle, d, "held", sr)
                continue
            if llm and int(state["calls"]) >= max_calls:
                row = self._row(
                    strategy,
                    cycle,
                    d,
                    "held",
                    final_action="no_call",
                    no_call_reason="call_cap",
                    position=position_json,
                )
            elif llm:
                try:
                    row = await call(d, "held", snap_json, price, sp.stop, None)
                except _OutOfTime:
                    self._missed(session, strategy, cycle, d, "held", sr)
                    continue
            else:
                # rules_only exits only through its stop, target or earnings.
                row = self._row(
                    strategy, cycle, d, "held", final_action="hold", position=position_json
                )
            session.add(row)
            session.flush()
            self._apply_held(row, sp, scheduled)
            sr.decisions += 1

        # ---- candidates that pass this strategy's rules, in rank order
        for d, rules in passing:
            if out_of_time():
                self._missed(session, strategy, cycle, d, "candidate", sr)
                continue
            snap = d.input_snapshot or {}
            price = _dec(snap.get("price"))
            stop: Decimal | None = None
            target: Decimal | None = None
            if llm:
                if price is None:
                    row = self._row(
                        strategy,
                        cycle,
                        d,
                        "candidate",
                        final_action="no_call",
                        no_call_reason="quote_missing",
                        rules=rules,
                    )
                elif int(state["calls"]) >= max_calls:
                    row = self._row(
                        strategy,
                        cycle,
                        d,
                        "candidate",
                        final_action="no_call",
                        no_call_reason="call_cap",
                        rules=rules,
                    )
                else:
                    snap_json = copy.deepcopy(dict(snap))
                    snap_json["context"] = "candidate"
                    snap_json["position"] = None
                    try:
                        row = await call(d, "candidate", snap_json, price, None, rules)
                    except _OutOfTime:
                        self._missed(session, strategy, cycle, d, "candidate", sr)
                        continue
                    output = row.model_output or {}
                    stop = _dec(output.get("stop_price"))
                    target = _dec(output.get("target_price"))
            else:
                atr = indicators_from_snapshot(d.symbol, snap).atr_1h
                row = self._row(strategy, cycle, d, "candidate", final_action="skip", rules=rules)
                if price is None or atr is None or atr <= 0:
                    row.gates_result = {
                        "allowed": False,
                        "blocked_by": "missing_data",
                        "checks": [],
                    }
                else:
                    k = Decimal(
                        str(self.config.get("shadows", "rules_only", "stop_atr", default=2.0))
                    )
                    r_mult = Decimal(
                        str(self.config.get("shadows", "rules_only", "target_r", default=2.0))
                    )
                    stop = price - k * atr
                    target = price + r_mult * (price - stop)
                    row.final_action = "enter"
            session.add(row)
            session.flush()
            if row.final_action == "enter" and price is not None and stop is not None:
                self._enter(
                    session, row, b, price, stop, target, strategy, run, scheduled, live, d, state
                )
                if row.final_action == "enter":
                    sr.entries += 1
            sr.decisions += 1

        # ---- everything the rules reject: stored, never called
        for d, rules in failing:
            if state["out"]:
                self._missed(session, strategy, cycle, d, "candidate", sr)
                continue
            session.add(
                self._row(
                    strategy,
                    cycle,
                    d,
                    "candidate",
                    final_action="no_call",
                    no_call_reason="rules",
                    rules=rules,
                )
            )
            sr.decisions += 1
        session.flush()

    # ------------------------------------------------------------------

    def _row(
        self,
        strategy: Strategy,
        cycle: Cycle,
        d: Decision,
        context: str,
        *,
        final_action: str,
        no_call_reason: str | None = None,
        rules: RulesResult | None = None,
        position: dict[str, Any] | None = None,
    ) -> ShadowDecision:
        return ShadowDecision(
            strategy_id=strategy.id,
            cycle_id=cycle.id,
            symbol=d.symbol,
            context=context,
            live_decision_id=d.id,
            position=position,
            rules_result=rules_result_json(rules),
            final_action=final_action,
            no_call_reason=no_call_reason,
            model=strategy.model if strategy.kind == "llm" else None,
            prompt_version=strategy.prompt_version,
            missed=False,
            created_at=_naive_utc(self.clock.now()),
        )

    def _missed(
        self,
        session: Session,
        strategy: Strategy,
        cycle: Cycle,
        d: Decision,
        context: str,
        sr: StrategyReport,
    ) -> None:
        row = self._row(strategy, cycle, d, context, final_action="no_call")
        row.missed = True
        session.add(row)
        sr.missed += 1

    def _apply_held(self, row: ShadowDecision, sp: ShadowPosition, at: datetime) -> None:
        output = row.model_output or {}
        if sp.exit_pending:
            return  # an earnings exit is already on its way
        if row.final_action == "tighten_stop":
            new_stop = _dec(output.get("stop_price"))
            if new_stop is not None and new_stop > sp.stop:
                sp.stop = cents(new_stop)  # stops only move up
            else:
                row.gates_result = {"tighten_noop": True}
        elif row.final_action == "exit":
            opened = aware(sp.opened_at or sp.created_at)
            if exit_blocked_same_day(opened, at):
                sp.deferred_exit = {"at": at.isoformat(), "reason": "day_trade_rule"}
                row.final_action = "deferred"
            else:
                sp.exit_pending = "exit"
                sp.exit_requested_at = _naive_utc(at)

    def _enter(
        self,
        session: Session,
        row: ShadowDecision,
        b: Book,
        price: Decimal,
        stop: Decimal,
        target: Decimal | None,
        strategy: Strategy,
        run: Run,
        at: datetime,
        live: LiveGateState,
        d: Decision,
        state: dict[str, Any],
    ) -> None:
        """Live gates and sizing on the virtual portfolio, then a working
        limit entry at the live limit price and timeout."""
        ind = indicators_from_snapshot(d.symbol, d.input_snapshot or {})
        ctx = gate_context(
            session,
            symbol=d.symbol,
            b=b,
            strategy_id=strategy.id,
            run_id=run.id,
            now=at,
            calendar=self.calendar,
            live=live,
            days_to_earnings=ind.days_to_earnings,
            earnings_known=ind.earnings_available,
            orders_this_cycle=int(state["orders"]),
        )
        # Gates always run on live's config: they are never overridable.
        result = evaluate_entry_gates(ctx, entry=price, stop=stop, config=self.config)
        row.gates_result = result.to_json()
        if not result.allowed or result.sizing is None:
            row.final_action = "blocked"
            return
        offset = Decimal(str(self.config.get("execution", "entry_limit_offset_pct", default=0.05)))
        timeout = int(self.config.get("execution", "entry_timeout_minutes", default=10))
        sp = ShadowPosition(
            strategy_id=strategy.id,
            run_id=run.id,
            symbol=d.symbol,
            status="pending",
            entry_decision_id=row.id,
            qty=result.sizing.shares,
            limit_price=cents(price * (1 + offset / 100)),
            initial_stop=cents(stop),
            stop=cents(stop),
            target=cents(target) if target is not None else None,
            created_at=_naive_utc(at),
            expires_at=_naive_utc(at + timedelta(minutes=timeout)),
        )
        session.add(sp)
        session.flush()
        b.pending.append(sp)
        state["orders"] = int(state["orders"]) + 1
