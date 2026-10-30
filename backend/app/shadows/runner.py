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
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from time import monotonic
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.data.cache import BarCache, _naive_utc
from app.data.models import Quote
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Position, Run, ShadowDecision, ShadowPosition, Strategy
from app.decide.client import DecisionClient, ModelResponse, token_cost
from app.decide.llm_cache import LLMCacheStore
from app.decide.output import (
    ModelOutputV3,
    decision_trade_prices,
    validate_model_output,
    validate_model_output_v2,
    validate_model_output_v3,
)
from app.decide.prompt import PromptSpec, cached_prompt, prompt_spec
from app.decide.service import (
    model_output_json,
    rules_result_json,
    validation_result_json,
)
from app.decide.snapshot import UNAVAILABLE, _position_block, entry_invalidation
from app.decide.v2inputs import HeldContext, V2Build, build_v2_fields, rebuild_bars
from app.decide.v3 import HeldV3, build_v3_fields, stop_basis_info, v3_send_snapshot
from app.engine.state import record_event
from app.inspector.record import build_llm_call, sync_llm_call
from app.market.calendar import CalendarProvider
from app.market.clock import NY_TZ, Clock, SessionClock
from app.notifications.service import Notifier, NullNotifier
from app.risk.earnings import earnings_exit_due, next_earnings
from app.risk.gates import evaluate_entry_gates, resolve_sectors
from app.risk.math import trade_risk
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


@dataclass(frozen=True)
class LiveInput:
    """One symbol of a live cycle as a shadow sees it: the stored snapshot
    and the live decision it came with. ``live_id`` is None for a symbol
    only a shadow holds — live built its snapshot but made no decision."""

    symbol: str
    snapshot: dict[str, Any]
    live_id: int | None
    order: int


def _spec(strategy: Strategy) -> PromptSpec:
    """The strategy's prompt contract; rules_only shadows use v1's shape."""
    return prompt_spec(str(strategy.prompt_version or "decision.v1"))


def _rank_key(item: LiveInput) -> tuple[int, int]:
    rank = item.snapshot.get("rank")
    return (rank if isinstance(rank, int) else 10**9, item.order)


def _virtual_position(sp: ShadowPosition) -> Position:
    """A transient live ``Position`` so the live position block is reused."""
    return Position(
        symbol=sp.symbol,
        qty=sp.qty,
        avg_entry_price=sp.entry,
        initial_stop=sp.initial_stop,
        current_stop_price=sp.stop,
        target_price=sp.target,
        opened_at=sp.opened_at,
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
    """The next live cycle or position check is due.

    When a model request was already sent but the wait-for deadline cut
    it off, ``sent_at``/``latency_ms`` carry the send so the timeout is
    still recorded as a sent call (docs/SPEC.md, LLM call history)."""

    def __init__(
        self, sent_at: datetime | None = None, latency_ms: int | None = None
    ) -> None:
        super().__init__()
        self.sent_at = sent_at
        self.latency_ms = latency_ms


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
        llm_cache: LLMCacheStore | None = None,
        secrets: Callable[[], list[str]] | None = None,
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
        # Write-through only: shadow answers land in the shared cache for
        # backtest replays; a shadow decision is always a fresh model call.
        self._llm_cache = llm_cache
        # Scrubbed stored keys for call-history error messages.
        self._secrets = secrets

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

    def held_symbols(self) -> list[str]:
        """Symbols held by any active or paused shadow in the current run —
        live's cycle builds snapshots for them too."""
        with self._sf() as session:
            run = self._run(session)
            if run is None:
                return []
            rows = session.execute(
                select(ShadowPosition.symbol)
                .join(Strategy, ShadowPosition.strategy_id == Strategy.id)
                .where(
                    Strategy.status.in_(NON_ARCHIVED),
                    ShadowPosition.run_id == run.id,
                    ShadowPosition.status == "open",
                )
            ).all()
        return sorted({r[0] for r in rows})

    async def after_cycle(
        self, cycle_id: int, extra_snapshots: dict[str, dict[str, Any]] | None = None
    ) -> ShadowReport:
        """Run every shadow on the stored snapshots of one finished cycle.

        ``extra_snapshots`` are the snapshots live built for symbols only
        shadows hold (no live decision exists for them)."""
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
                    await self._decide(
                        session,
                        strategy,
                        cycle,
                        run,
                        scheduled,
                        live,
                        deadline,
                        sr,
                        extra_snapshots or {},
                    )
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
        extra_snapshots: dict[str, dict[str, Any]],
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
            ):
                sp.exit_pending = "earnings_exit"
                sp.exit_requested_at = _naive_utc(scheduled)

        if strategy.status != "active":
            return
        if llm and self.cap_reached(session):
            sr.paused_by_cap = True
            self._notify_cap(session)
            return

        decisions = [
            LiveInput(d.symbol, dict(d.input_snapshot or {}), d.id, d.id)
            for d in session.execute(
                select(Decision).where(Decision.cycle_id == cycle.id).order_by(Decision.id)
            ).scalars()
        ]
        # Symbols only this shadow holds: live built their snapshots in the
        # same cycle but made no decision (docs/SPEC.md, Running shadows).
        seen = {d.symbol for d in decisions}
        decisions += [
            LiveInput(symbol, snap, None, 10**9 + i)
            for i, (symbol, snap) in enumerate(sorted(extra_snapshots.items()))
            if symbol in held and symbol not in seen
        ]
        if _spec(strategy).is_v3:
            # v3 checks.earnings needs every reviewed symbol's next report.
            earnings = next_earnings(
                session, [d.symbol for d in decisions], scheduled
            )
        held_items: list[LiveInput] = []
        passing: list[tuple[LiveInput, RulesResult]] = []
        failing: list[tuple[LiveInput, RulesResult]] = []
        for d in decisions:
            snap = d.snapshot
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
                cycle_time=scheduled.astimezone(NY_TZ).strftime("%H:%M"),
            )
            (passing if rules.status == STATUS_PASS else failing).append((d, rules))
        passing.sort(key=lambda pair: _rank_key(pair[0]))

        # "out" is False, or why the rest of the cycle is missed: time | cost_cap.
        state: dict[str, Any] = {"calls": 0, "orders": 0, "out": False}

        def out_of_time() -> bool:
            if state["out"]:
                return True
            if deadline is not None:
                margin = timedelta(seconds=timeout) if llm else timedelta(0)
                if self.clock.now() + margin >= deadline:
                    state["out"] = "time"
            if llm and not state["out"] and self.cap_reached(session):
                # The cap hit mid-cycle: the rest is skipped like missed work.
                sr.paused_by_cap = True
                self._notify_cap(session)
                state["out"] = "cost_cap"
            return bool(state["out"])

        async def call(
            d: LiveInput,
            context: str,
            snap_json: dict[str, Any],
            price: Decimal | None,
            current_stop: Decimal | None,
            rules: RulesResult | None,
            v2: V2Build | None = None,
        ) -> ShadowDecision:
            assert deadline is None or deadline > self.clock.now()  # noqa: S101 - shadow runner invariant
            state["calls"] = int(state["calls"]) + 1
            sr.calls += 1
            prompt = cached_prompt(str(strategy.prompt_version))
            spec = _spec(strategy)
            budget = (deadline - self.clock.now()).total_seconds() if deadline is not None else None
            sent_at = _naive_utc(self.clock.now())
            # Never hold a database write transaction across an await: live
            # jobs run on the same event loop and must always get the lock.
            session.commit()
            sent = json.dumps(
                v3_send_snapshot(snap_json) if spec.is_v3 else snap_json,
                indent=2,
            )
            started = monotonic()
            try:
                response = await asyncio.wait_for(
                    self._client(strategy).complete(
                        system=prompt.text,
                        user=sent,
                        schema=spec.json_schema,
                    ),
                    timeout=budget,
                )
            except TimeoutError as exc:
                state["out"] = "time"
                # The request was sent and got cut off by the deadline:
                # still a call (docs/SPEC.md, What counts as a call).
                raise _OutOfTime(
                    sent_at, int((monotonic() - started) * 1000)
                ) from exc
            if (
                self._llm_cache is not None
                and response.error is None
                and response.raw is not None
            ):
                self._llm_cache.store(
                    model=str(strategy.model),
                    temperature=float(
                        cfg.get("decision", "temperature", default=0.2)
                    ),
                    prompt_version=prompt.version,
                    prompt_hash=prompt.sha256,
                    user=sent,
                    cycle_time=scheduled,
                    response=response.raw,
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                )
            ind = indicators_from_snapshot(d.symbol, d.snapshot)
            if spec.is_v3:
                validation = validate_model_output_v3(
                    response.raw,
                    symbol=d.symbol,
                    context=context,
                    snapshot=snap_json,
                    atr_1h=ind.atr_1h,
                    current_stop=current_stop,
                    call_error=response.error,
                    config=cfg,
                )
            elif spec.uses_options:
                validation = validate_model_output_v2(
                    response.raw,
                    symbol=d.symbol,
                    context=context,
                    snapshot=snap_json,
                    atr_1h=ind.atr_1h,
                    current_stop=current_stop,
                    call_error=response.error,
                    config=cfg,
                )
            else:
                validation = validate_model_output(
                    response.raw,
                    symbol=d.symbol,
                    context=context,
                    price=(
                        _dec(snap_json.get("stop_reference_price"))
                        if context == "held"
                        else price
                    ),
                    atr_1h=ind.atr_1h,
                    current_stop=current_stop,
                    call_error=response.error,
                    config=cfg,
                    position=(
                        snap_json.get("position")
                        if isinstance(snap_json.get("position"), dict)
                        else None
                    ),
                )
            # v3 data flags: WARNING + event in every context
            # (docs/SPEC.md, Decision v3 — Data flags).
            if isinstance(validation.output, ModelOutputV3):
                for flag in validation.output.data_flags:
                    logger.warning(
                        "model.data_flag %s: model flagged a data conflict (%s)",
                        d.symbol, flag,
                    )
                    record_event(
                        session,
                        self.clock,
                        level="warning",
                        event_type="data_flag",
                        details={
                            "symbol": d.symbol,
                            "flag": flag,
                            "message": f"Model flagged a data conflict: {d.symbol} {flag}",
                        },
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
            if (snap_json.get("quote") or {}).get("synthetic"):
                row.input_snapshot = snap_json
            row.model_output = model_output_json(validation.output) if validation.output else None
            row.validation_result = validation_result_json(validation)
            if v2 is not None:
                row.levels = {"levels": v2.fields.get("levels")}
                row.options = v2.options_column
                row.options_version = v2.options_column.get("options_version")
            row.chosen_option = (
                validation.chosen_option.get("id")
                if validation.chosen_option is not None
                else None
            )
            row.explanation_check = (
                (validation.explanation.to_json()
                 if validation.explanation is not None else {})
                | ({"claims": validation.claims.to_json()}
                   if validation.claims is not None else {})
                or None
            )
            row.prompt_version = prompt.version
            row.prompt_hash = prompt.sha256
            row.model = strategy.model
            row.tokens = response.tokens
            row.tokens_in = response.tokens_in
            row.tokens_out = response.tokens_out
            row.cost = token_cost(response, self.config, model=strategy.model)
            row.latency_ms = response.latency_ms
            row.raw_response = response.raw
            session.add(row)
            session.flush()
            # One llm_calls row per request actually sent, committed with
            # the shadow decision it produced (docs/SPEC.md, LLM call
            # history — Storage).
            session.add(
                build_llm_call(
                    source="shadow",
                    kind="decision",
                    sent_at=sent_at,
                    response=response,
                    input_snapshot=snap_json,
                    model_action=validation.model_action,
                    final_action=row.final_action,
                    output_invalid=not validation.valid
                    and response.error is None
                    and not response.refused,
                    error_detail=validation.error,
                    cycle_id=cycle.id,
                    shadow_decision_id=row.id,
                    strategy_id=strategy.id,
                    symbol=d.symbol,
                    context=context,
                    model=str(strategy.model),
                    temperature=float(
                        cfg.get("decision", "temperature", default=0.2)
                    ),
                    prompt_version=prompt.version,
                    prompt_hash=prompt.sha256,
                    cost=row.cost,
                    slow_ms=int(
                        float(
                            self.config.get(
                                "inspector", "slow_call_seconds", default=15
                            )
                        )
                        * 1000
                    ),
                    scrub_values=self._secrets,
                )
            )
            return row

        # ---- held positions first
        for d in held_items:
            sp = held[d.symbol]
            snap = d.snapshot
            price = _dec(snap.get("price"))
            position_json = _position_block(_virtual_position(sp), price)
            snap_json: dict[str, Any] = copy.deepcopy(dict(snap))
            snap_json["context"] = "held"
            snap_json["position"] = position_json
            entry = (
                session.get(ShadowDecision, sp.entry_decision_id) if sp.entry_decision_id else None
            )
            if position_json is not None and entry is not None:
                reasoning = entry.model_output or {}
                position_json["original_thesis"] = reasoning.get("thesis", UNAVAILABLE)
                position_json["original_invalidation"] = entry_invalidation(
                    sp.initial_stop, entry
                )
            quote, synthetic, price_failure = self._market_quote(
                d.symbol, snap_json, scheduled
            )
            dev_pct = Decimal(
                str(self.config.get("execution", "quote_max_deviation_pct"))
            )
            reference = quote.stop_reference(
                _dec(snap_json.get("last_bar_close")),
                indicators_from_snapshot(d.symbol, snap_json).atr_1h, entry=False,
                max_deviation_pct=dev_pct,
            ) if quote is not None and price_failure is None else None
            snap_json["stop_reference_price"] = str(reference) if reference is not None else None
            if quote is not None:
                snap_json["quote"] = (
                    quote.provenance(quote.quote_plausible(dev_pct))
                    | {"synthetic": synthetic}
                )
            v2_held: V2Build | None = None
            is_v3 = _spec(strategy).is_v3
            if llm and is_v3:
                h_bars, h_daily, h_todays = rebuild_bars(
                    d.symbol, scheduled, calendar=self.calendar,
                    bars_reader=self._bars,
                )
                v2_held = build_v3_fields(
                    context="held",
                    base=snap_json,
                    price=price,
                    reference=reference,
                    atr_1h=indicators_from_snapshot(d.symbol, snap_json).atr_1h,
                    indicators=indicators_from_snapshot(d.symbol, snap_json),
                    regime=regime_from_snapshot(snap_json, cfg),
                    bars_1h=h_bars,
                    daily=h_daily,
                    today_5m=h_todays,
                    earnings=earnings.get(d.symbol),
                    held=HeldV3(
                        entry=sp.entry,
                        qty=sp.qty,
                        initial_stop=sp.initial_stop,
                        current_stop=sp.stop,
                        target=sp.target,
                        opened_at=aware(sp.opened_at) if sp.opened_at else None,
                        entry_thesis=(
                            (entry.model_output or {}).get("thesis")
                            if entry is not None
                            else None
                        ),
                        stop_basis=stop_basis_info(entry),
                        earnings=earnings.get(d.symbol),
                        entry_levels=tuple(
                            (entry.levels or {}).get("levels") or ()
                        )
                        if entry is not None
                        else (),
                    ),
                    calendar=self.calendar,
                    config=cfg,
                    cycle_time_ny=scheduled,
                )
                snap_json.update(v2_held.fields)
            elif llm and _spec(strategy).uses_options:
                h_bars, h_daily, h_todays = rebuild_bars(
                    d.symbol, scheduled, calendar=self.calendar,
                    bars_reader=self._bars,
                )
                v2_held = build_v2_fields(
                    context="held",
                    bars_1h=h_bars,
                    daily=h_daily,
                    today_5m=h_todays,
                    price=price,
                    reference=reference,
                    atr_1h=indicators_from_snapshot(d.symbol, snap_json).atr_1h,
                    config=cfg,
                    held=HeldContext(
                        entry=sp.entry,
                        current_stop=sp.stop,
                        initial_stop=sp.initial_stop,
                        opened_at=aware(sp.opened_at) if sp.opened_at else None,
                    ),
                )
                snap_json.update(v2_held.fields)
            # Held positions are always reviewed; a stale or suspect
            # market price only blocks a tighten_stop (docs/SPEC.md,
            # Entries — Market price).
            if out_of_time():
                self._missed(session, strategy, cycle, d, "held", sr, str(state["out"]))
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
                    row = await call(
                        d, "held", snap_json, price, sp.stop, None, v2=v2_held
                    )
                except _OutOfTime as oot:
                    missed = self._missed(
                        session, strategy, cycle, d, "held", sr, str(state["out"])
                    )
                    session.flush()
                    self._record_timeout(
                        session, strategy, cycle, d, "held", snap_json, oot,
                        missed.id,
                    )
                    continue
            else:
                # rules_only exits only through its stop, target or earnings.
                row = self._row(
                    strategy, cycle, d, "held", final_action="hold", position=position_json
                )
            session.add(row)
            session.flush()
            self._apply_held(
                row, sp, scheduled, reference,
                indicators_from_snapshot(d.symbol, snap_json).atr_1h,
                price_failure=price_failure,
            )
            sync_llm_call(session, row)
            sr.decisions += 1

        # ---- candidates that pass this strategy's rules, in rank order
        for d, rules in passing:
            if out_of_time():
                self._missed(session, strategy, cycle, d, "candidate", sr, str(state["out"]))
                continue
            snap = copy.deepcopy(d.snapshot)
            quote, synthetic, quote_failure = self._market_quote(d.symbol, snap, scheduled)
            dev_pct = Decimal(
                str(self.config.get("execution", "quote_max_deviation_pct"))
            )
            provenance = (
                quote.provenance(quote.quote_plausible(dev_pct))
                | {"synthetic": synthetic}
            ) if quote else None
            if quote_failure is not None:
                row = self._row(
                    strategy, cycle, d, "candidate", final_action="blocked", rules=rules,
                )
                row.gates_result = {
                    "allowed": False, "blocked_by": quote_failure, "quote": provenance,
                }
                session.add(row)
                sr.decisions += 1
                continue
            assert quote is not None  # noqa: S101 - shadow runner invariant
            price = quote.last
            snap["price"] = float(price)
            snap["quote"] = provenance
            d = replace(d, snapshot=snap)
            stop: Decimal | None = None
            target: Decimal | None = None
            if llm:
                if int(state["calls"]) >= max_calls:
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
                    v2_cand: V2Build | None = None
                    if _spec(strategy).uses_options:
                        atr_v2 = indicators_from_snapshot(
                            d.symbol, snap_json
                        ).atr_1h
                        ref_v2 = quote.stop_reference(
                            _dec(snap_json.get("last_bar_close")),
                            atr_v2,
                            entry=True,
                            max_deviation_pct=dev_pct,
                        )
                        snap_json["stop_reference_price"] = (
                            str(ref_v2) if ref_v2 is not None else None
                        )
                        c_bars, c_daily, c_todays = rebuild_bars(
                            d.symbol, scheduled, calendar=self.calendar,
                            bars_reader=self._bars,
                        )
                        if _spec(strategy).is_v3:
                            v2_cand = build_v3_fields(
                                context="candidate",
                                base=snap_json,
                                price=price,
                                reference=ref_v2,
                                atr_1h=atr_v2,
                                indicators=indicators_from_snapshot(
                                    d.symbol, snap_json
                                ),
                                regime=regime_from_snapshot(snap_json, cfg),
                                bars_1h=c_bars,
                                daily=c_daily,
                                today_5m=c_todays,
                                earnings=earnings.get(d.symbol),
                                calendar=self.calendar,
                                config=cfg,
                                cycle_time_ny=scheduled,
                            )
                        else:
                            v2_cand = build_v2_fields(
                                context="candidate",
                                bars_1h=c_bars,
                                daily=c_daily,
                                today_5m=c_todays,
                                price=price,
                                reference=ref_v2,
                                atr_1h=atr_v2,
                                config=cfg,
                            )
                        snap_json.update(v2_cand.fields)
                    if v2_cand is not None and not v2_cand.fields["options"]:
                        row = self._row(
                            strategy, cycle, d, "candidate",
                            final_action="no_call",
                            no_call_reason="no_options",
                            rules=rules,
                        )
                        row.levels = {"levels": v2_cand.fields.get("levels")}
                        row.options = v2_cand.options_column
                        row.options_version = v2_cand.options_column.get(
                            "options_version"
                        )
                        if _spec(strategy).is_v3:
                            # v3: the no_options reason (room, ...) is
                            # stored with the row for the drawer and feed.
                            row.validation_result = {
                                "error": v2_cand.reason
                                or "no option met the rules",
                                "final_action": "no_call",
                                "note": (
                                    f"No call — {v2_cand.reason or 'no option met the rules'}"
                                ),
                            }
                        session.add(row)
                        session.flush()
                        sr.decisions += 1
                        continue
                    try:
                        row = await call(
                            d, "candidate", snap_json, price, None, rules,
                            v2=v2_cand,
                        )
                    except _OutOfTime as oot:
                        missed = self._missed(
                            session, strategy, cycle, d, "candidate", sr,
                            str(state["out"]),
                        )
                        session.flush()
                        self._record_timeout(
                            session, strategy, cycle, d, "candidate", snap_json,
                            oot, missed.id,
                        )
                        continue
                    output = row.model_output or {}
                    rstop, rtarget = decision_trade_prices(
                        row.model_output, row.options, row.chosen_option
                    )
                    stop = rstop if rstop is not None else _dec(output.get("stop_price"))
                    target = (
                        rtarget
                        if rtarget is not None
                        else _dec(output.get("target_price"))
                    )
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
            sync_llm_call(session, row)
            sr.decisions += 1

        # ---- everything the rules reject: stored, never called
        for d, rules in failing:
            if state["out"]:
                self._missed(session, strategy, cycle, d, "candidate", sr, str(state["out"]))
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
        d: LiveInput,
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
            live_decision_id=d.live_id,
            input_snapshot=None if d.live_id is not None else d.snapshot,
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
        d: LiveInput,
        context: str,
        sr: StrategyReport,
        reason: str,
    ) -> ShadowDecision:
        row = self._row(strategy, cycle, d, context, final_action="no_call")
        row.missed = True
        row.missed_reason = reason
        session.add(row)
        sr.missed += 1
        return row

    def _record_timeout(
        self,
        session: Session,
        strategy: Strategy,
        cycle: Cycle,
        d: LiveInput,
        context: str,
        snap_json: dict[str, Any],
        oot: _OutOfTime,
        missed_id: int | None,
    ) -> None:
        """The llm_calls row for a request the deadline cut off mid-call.

        The request was sent, so it gets a row — a plain "missed" call
        (never sent) does not (docs/SPEC.md, What counts as a call)."""
        if oot.sent_at is None:
            return
        prompt = cached_prompt(str(strategy.prompt_version))
        cfg = strategy_config(self.config, strategy)
        session.add(
            build_llm_call(
                source="shadow",
                kind="decision",
                sent_at=oot.sent_at,
                response=ModelResponse(
                    raw=None,
                    model=str(strategy.model),
                    tokens_in=None,
                    tokens_out=None,
                    latency_ms=oot.latency_ms or 0,
                    error="call cut off at the live-work deadline",
                    error_type="timeout",
                ),
                input_snapshot=snap_json,
                final_action="no_call",
                cycle_id=cycle.id,
                shadow_decision_id=missed_id,
                strategy_id=strategy.id,
                symbol=d.symbol,
                context=context,
                model=str(strategy.model),
                temperature=float(cfg.get("decision", "temperature", default=0.2)),
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
                scrub_values=self._secrets,
            )
        )

    def _apply_held(
        self, row: ShadowDecision, sp: ShadowPosition, at: datetime,
        reference: Decimal | None, atr: Decimal | None,
        price_failure: str | None = None,
    ) -> None:
        output = row.model_output or {}
        if sp.exit_pending:
            return  # an earnings exit is already on its way
        if row.final_action == "tighten_stop":
            rstop, _ = decision_trade_prices(
                row.model_output, row.options, row.chosen_option
            )
            new_stop = (
                rstop if rstop is not None else _dec(output.get("stop_price"))
            )
            margin = Decimal(str(self.config.get("decision", "tighten_min_atr_below_price")))
            if price_failure is not None:
                row.final_action = "hold"
                row.gates_result = {"tighten_noop": True, "reason": price_failure}
            elif reference is None or atr is None:
                row.final_action = "hold"
                row.gates_result = {"tighten_noop": True, "reason": "reference_missing"}
            elif new_stop is not None and reference - cents(new_stop) < margin * atr:
                row.final_action = "hold"
                row.gates_result = {"tighten_noop": True, "reason": "tighten_market_distance"}
            elif new_stop is not None and new_stop > sp.stop:
                sp.stop = cents(new_stop)  # stops only move up
            else:
                row.gates_result = {"tighten_noop": True}
        elif row.final_action == "exit":
            sp.exit_pending = "exit"
            sp.exit_requested_at = _naive_utc(at)

    def _market_quote(
        self, symbol: str, snapshot: dict[str, Any], at: datetime,
    ) -> tuple[Quote | None, bool, str | None]:
        """Live's fetched quote rebuilt from the stored snapshot, or a
        synthetic market price from the last closed bar when live fetched
        none (docs/SPEC.md, Virtual trading). Returns ``(quote,
        synthetic, failure)`` where failure is the market-price check —
        a stale or suspect price blocks the shadow the way it blocked
        live; order-time checks (spread, references) run in ``_enter``.
        A synthetic quote is never freshness-checked: the bar close is
        the price."""
        def timestamp(value: object) -> datetime | None:
            try:
                return aware(datetime.fromisoformat(str(value))) if value else None
            except ValueError:
                return None

        bar = _dec(snapshot.get("last_bar_close"))
        bar_time = timestamp(snapshot.get("last_bar_time"))
        raw = snapshot.get("quote")
        synthetic = not isinstance(raw, dict) or raw.get("synthetic") is True
        if synthetic:
            if bar is None or not bar.is_finite() or bar <= 0 or bar_time is None:
                return None, True, "reference_missing"
            close_time = bar_time + timedelta(minutes=5)
            return (
                Quote(
                    symbol, bar, bar, bar, close_time, "synthetic_bar",
                    close_time, close_time,
                ),
                True,
                None,
            )
        raw_quote = cast(dict[str, Any], raw)
        quote = Quote(
            symbol,
            _dec(raw_quote.get("bid")) or Decimal(0),
            _dec(raw_quote.get("ask")) or Decimal(0),
            _dec(raw_quote.get("last")) or Decimal(0),
            timestamp(raw_quote.get("quote_timestamp")),
            str(raw_quote.get("feed", "iex")),
            timestamp(raw_quote.get("trade_timestamp")),
            timestamp(raw_quote.get("fetched_at")),
        )
        reason = quote.price_reason(
            now=quote.fetched_at or at,
            max_age_seconds=float(self.config.get("execution", "quote_max_age_seconds")),
            bar_close=bar,
            max_deviation_pct=Decimal(str(
                self.config.get("execution", "quote_max_deviation_pct")
            )),
        )
        if reason is not None:
            return quote, False, reason
        return quote, False, None

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
        d: LiveInput,
        state: dict[str, Any],
    ) -> None:
        """Live gates and sizing on the virtual portfolio, then a working
        limit entry at the live limit price and timeout."""
        ind = indicators_from_snapshot(d.symbol, d.snapshot)
        stop = cents(stop)
        target = cents(target) if target is not None else None
        quote, synthetic, failure = self._market_quote(d.symbol, d.snapshot, at)
        dev_pct = Decimal(
            str(self.config.get("execution", "quote_max_deviation_pct"))
        )
        provenance = (
            quote.provenance(quote.quote_plausible(dev_pct))
            | {"synthetic": synthetic}
        ) if quote else None
        if failure is not None or quote is None:
            row.final_action = "blocked"
            row.gates_result = {"allowed": False, "blocked_by": failure, "quote": provenance}
            return
        # The spread gate mirrors live's order-time check: it runs only
        # on a plausible quote; a thin book is recorded, never blocking.
        if quote.quote_plausible(dev_pct):
            max_spread = Decimal(
                str(self.config.get("execution", "max_spread_pct", default=0.15))
            )
            mid = (quote.ask + quote.bid) / 2
            if (quote.ask - quote.bid) / mid * 100 > max_spread:
                row.final_action = "blocked"
                row.gates_result = {
                    "allowed": False, "blocked_by": "spread", "quote": provenance,
                }
                return
        bar_time_str = d.snapshot.get("last_bar_time")
        bar_time = None
        try:
            bar_time = (
                aware(datetime.fromisoformat(str(bar_time_str)))
                if bar_time_str else None
            )
        except ValueError:
            bar_time = None
        price = quote.last
        reference = quote.stop_reference(
            _dec(d.snapshot.get("last_bar_close")), ind.atr_1h, entry=True,
            max_deviation_pct=dev_pct,
        )
        if bar_time is None or bar_time + timedelta(minutes=5) > at or reference is None:
            row.final_action = "blocked"
            row.gates_result = {
                "allowed": False, "blocked_by": "reference_missing", "quote": provenance,
            }
            return
        minimum = Decimal(str(self.config.get("decision", "stop_atr_min")))
        market_risk = trade_risk(reference, stop, target, ind.atr_1h) if (
            reference is not None and target is not None
        ) else None
        if (
            market_risk is None or market_risk.atr_multiple is None
            or market_risk.atr_multiple < minimum
        ):
            row.final_action = "blocked"
            row.gates_result = {
                "allowed": False, "blocked_by": "stop_market_distance", "quote": provenance,
            }
            return
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
            config=self.config,
        )
        ctx = replace(ctx, sectors=resolve_sectors(self.config, session))
        # Gates always run on live's config: they are never overridable.
        offset = Decimal(str(self.config.get("execution", "entry_limit_offset_pct", default=0.05)))
        limit = cents(price * (1 + offset / 100))
        cfg = strategy_config(self.config, strategy)
        risk = trade_risk(limit, stop, target, ind.atr_1h) if target is not None else None
        if (
            risk is None or not risk.valid or risk.atr_multiple is None
            or risk.atr_multiple < Decimal(str(cfg.get("decision", "stop_atr_min")))
            or risk.atr_multiple > Decimal(str(cfg.get("decision", "stop_atr_max")))
            or risk.reward_to_risk is None
            or risk.reward_to_risk < Decimal(str(cfg.get("decision", "min_reward_risk")))
        ):
            row.final_action = "blocked"
            row.gates_result = {
                "allowed": False, "blocked_by": "code_checks",
                "entry_limit": str(limit), "quote": provenance,
            }
            return
        result = evaluate_entry_gates(ctx, entry=limit, stop=stop, config=self.config)
        row.gates_result = result.to_json() | {"entry_limit": str(limit), "quote": provenance}
        if not result.allowed or result.sizing is None:
            row.final_action = "blocked"
            return
        timeout = int(self.config.get("execution", "entry_timeout_minutes", default=10))
        sp = ShadowPosition(
            strategy_id=strategy.id,
            run_id=run.id,
            symbol=d.symbol,
            status="pending",
            entry_decision_id=row.id,
            qty=result.sizing.shares,
            limit_price=limit,
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
