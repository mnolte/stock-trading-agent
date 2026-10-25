"""One backtest strategy: a full engine stack on its own live-schema DB.

Each strategy in a run gets ``run-<id>/<slug>.db`` created from the same
migrations, its own broker (the bar-replaying ``BacktestBroker``) and its
own MarketDataService/SignalService/CycleRunner/PositionChecker reading
the shared bar store and the run's market.db (docs/SPEC.md, Backtesting,
Engine).
"""

from __future__ import annotations

import os
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
from app.decide.client import DecisionClient
from app.decide.service import DecisionService
from app.engine.cycle import CycleRunner
from app.engine.executor import Executor
from app.engine.outcomes import OutcomeWorker
from app.engine.position_check import PositionChecker
from app.engine.state import ExecutionState
from app.market.calendar import CalendarStore
from app.market.clock import Clock
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
) -> StrategyBundle:
    """Build one strategy's engine on its own database file."""
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
        assert decision_client is not None
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

    async def decide(
        self,
        session: Session,
        *,
        cycle_id: int,
        symbol: str,
        context: str,
        snapshot_json: dict[str, Any],
        rules: Any,
        price: Decimal | None,
        atr_1h: Decimal | None,
        current_stop: Decimal | None,
        v2: Any = None,
    ) -> Any:
        from app.decide.output import ModelOutput, ValidationResult

        if context == "held" or price is None or atr_1h is None or atr_1h <= 0:
            return self._store(
                session,
                cycle_id=cycle_id,
                symbol=symbol,
                context=context,
                snapshot_json=snapshot_json,
                rules=rules,
                response=None,
                validation=ValidationResult(
                    valid=True,
                    output=ModelOutput(
                        symbol=symbol, action="hold", confidence=1.0
                    ),
                    model_action="hold",
                    final_action="hold",
                    note="rules only — hold",
                ),
                final_action="hold",
            )
        k = Decimal(
            str(self.config.get("shadows", "rules_only", "stop_atr", default=2.0))
        )
        r_mult = Decimal(
            str(self.config.get("shadows", "rules_only", "target_r", default=2.0))
        )
        stop = price - k * atr_1h
        target = price + r_mult * (price - stop)
        output = ModelOutput(
            symbol=symbol,
            action="enter",
            confidence=1.0,
            stop_price=stop,
            target_price=target,
            thesis="rules-only baseline",
        )
        return self._store(
            session,
            cycle_id=cycle_id,
            symbol=symbol,
            context=context,
            snapshot_json=snapshot_json,
            rules=rules,
            response=None,
            validation=ValidationResult(
                valid=True,
                output=output,
                model_action="enter",
                final_action="enter",
                note="rules only — enter",
            ),
            final_action="enter",
        )
