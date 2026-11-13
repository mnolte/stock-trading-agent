"""One backtest strategy: a full engine stack on its own live-schema DB.

Each strategy in a run gets ``run-<id>/<slug>.db`` created from the same
migrations, its own broker (the bar-replaying ``BacktestBroker``) and its
own MarketDataService/SignalService/CycleRunner/PositionChecker reading
the shared bar store and the run's market.db (docs/SPEC.md, Backtesting,
Engine).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.backtests.broker import BacktestBroker
from app.backtests.store import engine_for, run_dir_for
from app.config import AppConfig
from app.data.cache import BarCache
from app.data.factory import DataSources
from app.data.service import MarketDataService
from app.db.models import Decision
from app.decide.client import DecisionClient, ModelResponse
from app.decide.service import DecisionService, PreparedCall
from app.decide.v2inputs import V2Build
from app.engine.cycle import CycleRunner
from app.engine.executor import Executor
from app.engine.outcomes import OutcomeWorker
from app.engine.position_check import PositionChecker
from app.engine.state import ExecutionState
from app.market.calendar import CalendarStore
from app.market.clock import Clock
from app.rules.prefilter import RulesResult
from app.signals.service import SignalService
from app.universe.service import UniverseService


def migrate_db(path: Path) -> None:
    """Create a database file from the same migrations as the live one."""
    from alembic import command
    from alembic.config import Config

    path.parent.mkdir(parents=True, exist_ok=True)
    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini))
    previous = os.environ.get("SA_ALEMBIC_DB")
    os.environ["SA_ALEMBIC_DB"] = f"sqlite:///{path}"
    try:
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("SA_ALEMBIC_DB", None)
        else:
            os.environ["SA_ALEMBIC_DB"] = previous


def init_strategy_db(path: Path) -> None:
    """A strategy DB by the live schema — via the same migrations."""
    migrate_db(path)


@dataclass
class StrategyBundle:
    """One strategy's engine stack inside a run."""

    slug: str
    name: str
    kind: str  # "llm" | "rules_only" — live is an llm row by definition
    config: AppConfig
    session_factory: sessionmaker[Session]
    sources: DataSources
    calendar: CalendarStore
    market_data: MarketDataService
    signals: SignalService
    state: ExecutionState
    broker: BacktestBroker
    executor: Executor
    decide: DecisionService
    outcomes: OutcomeWorker
    checker: PositionChecker
    cycle: CycleRunner
    universe_service: UniverseService
    engine: Any  # kept for dispose()


def slugify(name: str) -> str:
    out = []
    for ch in name.lower():
        out.append(ch if ch.isalnum() else "-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "strategy"


def build_bundle(
    *,
    config: AppConfig,
    clock: Clock,
    run_id: int,
    slug: str,
    name: str,
    kind: str,
    sources: DataSources,
    store: BarCache,
    decision_client: DecisionClient | None,
    llm_cache: Any,
    before_call: Any,
    starting_equity: Decimal,
    slippage_pct: Decimal,
    market_sessions: sessionmaker[Session] | None = None,
) -> StrategyBundle:
    """Build one strategy's engine on its own database file.

    ``market_sessions`` is the run's market.db — the VixService reads
    the downloaded VIX closes from it (item 060)."""
    db_path = run_dir_for(config, run_id) / f"{slug}.db"
    if not db_path.exists():
        init_strategy_db(db_path)
    engine = engine_for(db_path, config)
    sf = sessionmaker(bind=engine)
    bundle_clock = clock

    calendar = CalendarStore(sources.calendar)
    market_data = MarketDataService(
        sources,
        calendar,
        config,
        session_factory=sf,
        bar_cache=BarCache(sf),
    )
    signals = SignalService(
        market_data, calendar, config, bundle_clock, session_factory=sf
    )
    state = ExecutionState(bundle_clock)
    broker = BacktestBroker(
        bundle_clock,
        store.get,
        cash=starting_equity,
        slippage_pct=slippage_pct,
        same_bar_target_first=(
            str(config.get("backtest", "same_bar_stop_and_target", default="stop"))
            == "target"
        ),
    )
    executor = Executor(
        broker=broker,
        market_data=market_data,
        signals=signals,
        calendar=calendar,
        config=config,
        clock=bundle_clock,
        state=state,
        session_factory=sf,
    )
    decide: DecisionService
    if kind == "rules_only":
        decide = RulesOnlyDecisionService(config, session_factory=sf)
    else:
        assert decision_client is not None  # noqa: S101 - decision client invariant
        decide = DecisionService(
            decision_client,
            config,
            session_factory=sf,
            llm_cache=llm_cache,
            cache_reads=True,
            before_call=before_call,
        )
    outcomes = OutcomeWorker(
        market_data=market_data,
        calendar=calendar,
        config=config,
        clock=bundle_clock,
        session_factory=sf,
    )
    checker = PositionChecker(
        executor=executor,
        state=state,
        config=config,
        clock=bundle_clock,
        outcomes=outcomes,
        session_factory=sf,
    )
    from app.data.vix import VixService
    from app.signals.breadth import BreadthService

    vix_service = VixService(
        sources.vix,
        market_data,
        config,
        bundle_clock,
        session_factory=market_sessions or sf,
    )
    breadth_service = BreadthService(market_data, config)
    cycle = CycleRunner(
        market_data=market_data,
        signals=signals,
        decide=decide,
        sources=sources,
        calendar=calendar,
        config=config,
        clock=bundle_clock,
        executor=executor,
        outcomes=outcomes,
        session_factory=sf,
        vix=vix_service,
        breadth=breadth_service,
    )
    universe_service = UniverseService(
        market_data=market_data,
        sources=sources,
        calendar=calendar,
        broker=broker,
        config=config,
        clock=bundle_clock,
        session_factory=sf,
    )
    return StrategyBundle(
        slug=slug,
        name=name,
        kind=kind,
        config=config,
        session_factory=sf,
        sources=sources,
        calendar=calendar,
        market_data=market_data,
        signals=signals,
        state=state,
        broker=broker,
        executor=executor,
        decide=decide,
        outcomes=outcomes,
        checker=checker,
        cycle=cycle,
        universe_service=universe_service,
        engine=engine,
    )


class RulesOnlyDecisionService(DecisionService):
    """``rules_only`` strategies: the deterministic baseline — no model.

    Entries use stop = entry − stop_atr × ATR(1h) and target = target_r ×
    (entry − stop), like the shadow baseline; held positions always hold
    (exits come only from the stop, the target or the earnings rule).
    """

    def __init__(
        self,
        config: AppConfig,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        from app.decide.client import FakeDecisionClient

        super().__init__(FakeDecisionClient(), config, session_factory=session_factory)

    def prepare_call(
        self,
        *,
        cycle_id: int,
        symbol: str,
        context: str,
        snapshot_json: dict[str, Any],
        rules: RulesResult | None,
        price: Decimal | None,
        atr_1h: Decimal | None,
        current_stop: Decimal | None,
        v2: V2Build | None = None,
        on_response: Callable[[ModelResponse], None] | None = None,
        market_context: dict[str, Any] | None = None,
    ) -> PreparedCall:
        # Item 078's wave API calls prepare/send/finish, never decide();
        # the baseline prepares like any call but never sends one.
        return super().prepare_call(
            cycle_id=cycle_id,
            symbol=symbol,
            context=context,
            snapshot_json=snapshot_json,
            rules=rules,
            price=price,
            atr_1h=atr_1h,
            current_stop=current_stop,
            v2=v2,
            on_response=on_response,
            market_context=market_context,
        )

    async def send_call(self, prepared: PreparedCall) -> None:
        """No request leaves the process — finish_call answers from the rules."""

    def finish_call(self, session: Session, prepared: PreparedCall) -> Decision:
        from app.decide.output import ModelOutput, ValidationResult

        symbol = prepared.symbol
        if (
            prepared.context == "held"
            or prepared.price is None
            or prepared.atr_1h is None
            or prepared.atr_1h <= 0
        ):
            action = "hold"
            output = ModelOutput(symbol=symbol, action="hold", confidence=1.0)
            note = "rules only — hold"
        else:
            action = "enter"
            k = Decimal(
                str(
                    self.config.get(
                        "shadows", "rules_only", "stop_atr", default=2.0
                    )
                )
            )
            r_mult = Decimal(
                str(
                    self.config.get(
                        "shadows", "rules_only", "target_r", default=2.0
                    )
                )
            )
            stop = prepared.price - k * prepared.atr_1h
            target = prepared.price + r_mult * (prepared.price - stop)
            output = ModelOutput(
                symbol=symbol,
                action="enter",
                confidence=1.0,
                stop_price=stop,
                target_price=target,
                thesis="rules-only baseline",
            )
            note = "rules only — enter"
        return self._store(
            session,
            cycle_id=prepared.cycle_id,
            symbol=symbol,
            context=prepared.context,
            snapshot_json=prepared.snapshot_json,
            rules=prepared.rules,
            response=None,
            validation=ValidationResult(
                valid=True,
                output=output,
                model_action=action,
                final_action=action,
                note=note,
            ),
            final_action=action,
            market_context=prepared.market_context,
        )
