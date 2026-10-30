"""The decision smoke run: one call, a printed report, nothing stored."""

import json
from datetime import date, datetime, timedelta
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
from app.db.models import Cycle, Decision, Outcome
from app.decide.client import FakeDecisionClient, ModelResponse
from app.market.calendar import WeekdayCalendarProvider
from app.market.clock import UTC, FakeClock
from app.smoke_decision import run as smoke_run
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)  # 13:00 New York


def _fakes() -> DataSources:
    calendar = FakeCalendarSource()
    return DataSources(
        bars=FakeBarSource(calendar),
        quotes=FakeQuoteSource(),
        news=FakeNewsSource(),
        earnings=FakeEarningsSource(),
        fx=FakeFXSource(),
        corporate_actions=FakeCorporateActionSource(),
        calendar=calendar,
        alpaca=None,
    )


class CountingFake(FakeDecisionClient):
    """Records the symbol of every call so the test proves exactly one."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        self.calls.append(json.loads(user)["symbol"])
        return await super().complete(
            system=system, user=user, schema=schema,
            model=model, temperature=temperature,
        )


async def test_smoke_decision_prints_call_details_and_stores_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["MSFT"]
    clock = FakeClock(NOW)

    # Fill the caches the way startup would — then the smoke only reads them.
    market_data = MarketDataService(
        _fakes(), WeekdayCalendarProvider(), config, session_factory=session_factory
    )
    await market_data.refresh_bars(NOW - timedelta(days=90), NOW)
    await market_data.refresh_daily(date(2026, 10, 15))

    lines: list[str] = []
    client = CountingFake()
    rc = await smoke_run(
        "MSFT",
        config,
        client,
        clock,
        session_factory=session_factory,
        out=lines.append,
    )
    assert rc == 0
    assert client.calls == ["MSFT"]
    text = "\n".join(lines)
    assert "decision smoke: MSFT (candidate)" in text
    assert "call:" in text and "tokens" in text and "ms" in text
    assert "result:" in text and "final action" in text

    with session_factory() as session:
        assert session.execute(select(Decision)).scalars().all() == []
        assert session.execute(select(Cycle)).scalars().all() == []
        assert session.execute(select(Outcome)).scalars().all() == []


async def test_smoke_decision_failed_call_exits_nonzero(
    session_factory: sessionmaker[Session],
) -> None:
    config = AppConfig()
    config.yaml["universe"]["symbols"] = ["MSFT"]
    clock = FakeClock(NOW)
    market_data = MarketDataService(
        _fakes(), WeekdayCalendarProvider(), config, session_factory=session_factory
    )
    await market_data.refresh_bars(NOW - timedelta(days=90), NOW)
    await market_data.refresh_daily(date(2026, 10, 15))

    lines: list[str] = []
    client = FakeDecisionClient(responses={"MSFT": {"error": "timeout after 30s"}})
    rc = await smoke_run(
        "MSFT",
        config,
        client,
        clock,
        session_factory=session_factory,
        out=lines.append,
    )
    assert rc == 1
    text = "\n".join(lines)
    assert "FAILED" in text
    assert "no_call (model_error)" in text
