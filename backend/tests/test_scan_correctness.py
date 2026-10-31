"""Scan fixes of 31 October — correctness items."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from app.config import AppConfig
from app.data.factory import DataSources
from app.data.fake import (
    FakeBarSource,
    FakeCalendarSource,
    FakeCorporateActionSource,
    FakeEarningsSource,
    FakeFXSource,
    FakeNewsSource,
    FakeQuoteSource,
)
from app.data.service import MarketDataService
from app.db.models import UniversePoolRow
from app.market.calendar import CalendarStore
from app.market.clock import FakeClock
from app.risk.math import WorkingEntry, committed_entries, position_size
from app.universe.service import UniverseService
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio


def _config(tmp_path: Path) -> AppConfig:
    config = AppConfig()
    config.yaml["universe"]["allowed_sectors"] = ["Technology", "Financials"]
    config.yaml["universe"]["symbols"] = ["AAPL"]
    config.yaml["universe"]["sectors"] = {"AAPL": "Technology"}
    return config


def _service(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> tuple[UniverseService, Any]:
    clock = FakeClock(datetime.now(UTC))
    config = _config(tmp_path)
    calendar_source = FakeCalendarSource()
    sources = DataSources(
        bars=FakeBarSource(calendar_source),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar_source,
        alpaca=None,
    )
    market_data = MarketDataService(
        sources, CalendarStore(calendar_source), config,
        session_factory=session_factory,
    )
    from app.broker.fake import FakeBroker

    broker = FakeBroker(clock)
    service = UniverseService(
        market_data,
        sources,
        CalendarStore(calendar_source),
        broker,
        config,
        clock,
        session_factory=session_factory,
    )
    return service, broker


async def test_import_csv_no_write_transaction_during_broker_call(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    """Every symbol is checked at Alpaca before the write transaction
    opens (hard rule 13): no broker call while a write is pending."""
    import sqlalchemy

    service, broker = _service(session_factory, tmp_path)
    open_writes = [0]

    engine = session_factory.kw["bind"]
    @sqlalchemy.event.listens_for(engine, "begin")
    def _begin(conn: Any) -> None:
        open_writes[0] += 1

    @sqlalchemy.event.listens_for(engine, "commit")
    def _commit(conn: Any) -> None:
        open_writes[0] -= 1

    @sqlalchemy.event.listens_for(engine, "rollback")
    def _rollback(conn: Any) -> None:
        open_writes[0] -= 1

    called_during_write = []
    real_get_asset = broker.get_asset

    async def spy(symbol: str) -> Any:
        called_during_write.append(open_writes[0] > 0)
        return await real_get_asset(symbol)

    broker.get_asset = spy  # type: ignore[method-assign]
    added, skipped = await service.import_csv("PANW,Technology\nJPM,Financials\n")
    assert added == ["PANW", "JPM"] and skipped == 0
    assert called_during_write == [False, False]
    with session_factory() as session:
        rows = session.execute(
            select(UniversePoolRow.symbol).where(
                UniversePoolRow.removed_at.is_(None)
            )
        ).scalars().all()
    assert {"PANW", "JPM"} <= set(rows)


# ----------------------------------------------------------------------
# committed_entries: working entries without a limit price


def test_committed_entries_uses_market_price_without_limit() -> None:
    """A working entry without a limit counts at the market price
    passed in — never zero."""
    result = committed_entries(
        [WorkingEntry(symbol="X", unfilled_qty=10, limit_price=None, stop_price=Decimal(95))],
        market_price=lambda _s: Decimal(100),
    )
    assert result.risk == Decimal(50) and result.cash == Decimal(1000)


def test_committed_entries_reports_unpriced() -> None:
    """No stored/broker limit and no market price → reported unpriced
    (the entry gates then fail closed)."""
    entries = [
        WorkingEntry(symbol="X", unfilled_qty=10, limit_price=None, stop_price=Decimal(95)),
        WorkingEntry(symbol="Y", unfilled_qty=5, limit_price=Decimal(20), stop_price=Decimal(18)),
    ]
    result = committed_entries(entries)
    assert result.unpriced == ("X",)
    assert result.cash == Decimal(100)
    assert result.risk == Decimal(10)


# ----------------------------------------------------------------------
# position_size: limited_by names the real binder in spec gate order


def test_limited_by_names_spec_order_not_alphabetical() -> None:
    """When two limits tie, blocked_by names the constraint in the
    spec's gate order (risk, position, open risk, cash), not the first
    alphabetically."""
    config = AppConfig()
    # Equity 1000, entry 100, stop 90 → per-share risk 10.
    # risk budget: 1000*0.5% = 5 → 0 shares by risk_per_trade... build a
    # tie between open_risk and cash instead:
    sizing = position_size(
        Decimal(10000),
        Decimal(100),
        Decimal(90),
        cash=Decimal(50),           # cash → 0 shares
        current_open_risk=Decimal(249.0),  # open_risk room 1 → 0 shares
        config=config,
    )
    assert sizing.shares == 0
    # open_risk precedes cash in the spec's gate order, so it names the
    # blocker even though "cash" sorts first alphabetically.
    assert sizing.limited_by[0] == "open_risk"
    assert "cash" in sizing.limited_by
