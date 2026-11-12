"""Item 081 step 5 — end-to-end per data source, no network.

Each source's HTTP layer is stubbed to raise one httpx.HTTPStatusError
and one httpx.ConnectError whose text carries ``api_key=<fake>`` /
``token=<fake>`` URL parameters plus a stored (non-key-shaped) secret
value. The resulting ``DataResult.error`` must contain neither; the same
error then travels the surfaces a cycle would give it — the notification
row and fake mailer, a cycles row, the Ask ``get_cycle`` tool result and
GET /api/settings + /api/server — and the fake key is absent everywhere.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from app.ask.tools import AskDeps, CycleArg, get_cycle
from app.config import get_config
from app.data.alpaca import AlpacaDataClient
from app.data.ecb import EcbFXSource
from app.data.finnhub import FinnhubEarningsSource
from app.data.fred import FredVixSource
from app.data.models import DataResult
from app.db.base import Base
from app.db.models import Cycle, Notification
from app.errors import safe_error
from app.logging_config import (
    _secret_providers,
    _secrets_cache,
    register_secret_provider,
)
from app.market.clock import FakeClock
from app.notifications.mail import FakeMailer
from app.notifications.service import NotificationService
from app.secrets_service import SecretsStore
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

FAKE_KEY = "sk-proj-FAKEKEY1234567890"
STORED_SECRET = "stored-value-not-key-shaped-1"
NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)

SessionFactory = sessionmaker[Session]


def _fake_secrets() -> list[str]:
    return [STORED_SECRET]


@pytest.fixture(autouse=True)
def _provider() -> Any:
    register_secret_provider(_fake_secrets)
    _secrets_cache.clear()
    yield
    if _fake_secrets in _secret_providers:
        _secret_providers.remove(_fake_secrets)
    _secrets_cache.clear()


@pytest.fixture
def session_factory(tmp_path: Path) -> SessionFactory:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def _http_error() -> Exception:
    """What httpx raises for a 500 — message carries the request URL."""
    url = f"https://data.example/feed?api_key={FAKE_KEY}&page=2"
    request = httpx.Request("GET", url)
    response = httpx.Response(500, request=request)
    return httpx.HTTPStatusError(
        f"Server error '500' for url '{url}' — key {STORED_SECRET}",
        request=request,
        response=response,
    )


def _conn_error() -> Exception:
    """A connection failure whose text carries a token URL parameter."""
    return httpx.ConnectError(
        "connection refused for url "
        f"'https://data.example/feed?token={FAKE_KEY}' — {STORED_SECRET}"
    )


async def _surfaces(
    name: str,
    error: str,
    *,
    client: TestClient,
    session_factory: SessionFactory,
) -> None:
    """Push ``error`` through every surface a cycle error reaches."""
    assert FAKE_KEY not in error, f"{name}: key in DataResult.error"
    assert STORED_SECRET not in error, f"{name}: secret in DataResult.error"

    # Notification row + email body.
    clock = FakeClock(NOW)
    config = get_config()
    secrets = SecretsStore(config, clock, session_factory=session_factory)
    mailer = FakeMailer()
    service = NotificationService(
        config=config,
        clock=clock,
        secrets=secrets,
        mailer=mailer,
        session_factory=session_factory,
    )
    secrets.save("smtp_password", "smtp-secret-98765")
    result = await service.save_smtp(
        host="smtp.test",
        port=587,
        username="agent@test",
        from_addr="agent@test",
        to_addr="marc@test",
    )
    assert result["ok"] is True
    mailer.sent.clear()
    service.notify("source_failing", f"{name} failed: {error}")
    await service.drain()
    with session_factory() as session:
        (row,) = session.execute(select(Notification)).scalars().all()
        assert FAKE_KEY not in row.subject
        assert FAKE_KEY not in (row.body or "")
        assert STORED_SECRET not in (row.body or "")
    assert mailer.sent, f"{name}: expected an email"
    assert FAKE_KEY not in mailer.sent[-1].body
    assert STORED_SECRET not in mailer.sent[-1].body

    # The cycles row: health map and errors list as cycle.py writes them.
    with session_factory() as session:
        cycle = Cycle(
            kind="cycle",
            scheduled_time=datetime(2026, 10, 15, 18, 46),
            status="done",
            errors=[f"{name}: {error}"],
            data_source_health={name: f"failed: {error}"},
        )
        session.add(cycle)
        session.commit()
        cycle_id = cycle.id
        assert FAKE_KEY not in str(cycle.data_source_health)
        assert FAKE_KEY not in str(cycle.errors)

    # The Ask get_cycle tool result — what the model would see.
    deps = AskDeps(
        config=config, clock=clock, session_factory=session_factory
    )
    with session_factory() as session:
        out = get_cycle(session, deps, CycleArg(cycle_id=cycle_id))
    blob = json.dumps(out, default=str)
    assert FAKE_KEY not in blob, f"{name}: key in get_cycle result"
    assert STORED_SECRET not in blob

    # GET /api/settings (connections echo source errors) and /api/server.
    app = cast(Any, client.app)
    market_data = getattr(app.state, "market_data", None)
    if market_data is not None:
        market_data.latest_earnings = DataResult(
            data=None, error=error, source=name
        )
        market_data.latest_fx = DataResult(
            data=None, error=error, source=name
        )
    vix = getattr(app.state, "vix", None)
    if vix is not None:
        vix.latest = DataResult(data=None, error=error, source=name)
    for path in ("/api/settings", "/api/server"):
        response = await asyncio.to_thread(client.get, path)
        assert FAKE_KEY not in response.text, f"{name}: key in {path}"
        assert STORED_SECRET not in response.text, f"{name}: secret in {path}"


async def _alpaca_error(kind: str) -> str:
    client = AlpacaDataClient(
        "PKFAKEKEY1234567890AB", "fake-secret", clock=FakeClock(NOW)
    )

    async def boom(*_args: object, **_kwargs: object) -> object:
        raise _http_error() if kind == "http" else _conn_error()

    client.limiter.run_read = boom  # type: ignore[method-assign]
    result = await client.fetch_5m_bars(
        ["AAPL"], datetime(2026, 10, 14, 14, 0), datetime(2026, 10, 15, 14, 0)
    )
    assert result.error is not None
    return result.error


async def _finnhub_error(kind: str) -> str:
    source = FinnhubEarningsSource("fake-key", clock=FakeClock(NOW))

    def boom(*_args: object) -> object:
        raise _http_error() if kind == "http" else _conn_error()

    source._client = SimpleNamespace(earnings_calendar=boom)
    result = await source.fetch_earnings(
        ["SPY"], date(2026, 10, 1), date(2026, 10, 31)
    )
    assert result.error is not None
    return result.error


async def _fred_error(kind: str) -> str:
    source = FredVixSource(api_key="fake-fred-key", clock=FakeClock(NOW))

    def boom(*_args: object, **_kwargs: object) -> object:
        raise _http_error() if kind == "http" else _conn_error()

    import app.data.fred as fred_module

    original = fred_module.httpx.get
    fred_module.httpx.get = boom  # type: ignore[assignment]
    try:
        result = await source.fetch_vix_closes(
            date(2026, 10, 1), date(2026, 10, 15)
        )
    finally:
        fred_module.httpx.get = original  # type: ignore[assignment]
    assert result.error is not None
    return result.error


async def _ecb_error(kind: str) -> str:
    source = EcbFXSource(clock=FakeClock(NOW))

    def boom(*_args: object, **_kwargs: object) -> object:
        raise _http_error() if kind == "http" else _conn_error()

    import app.data.ecb as ecb_module

    original = ecb_module.httpx.get
    ecb_module.httpx.get = boom  # type: ignore[assignment]
    try:
        result = await source.fetch_fx(date(2026, 10, 15))
    finally:
        ecb_module.httpx.get = original  # type: ignore[assignment]
    assert result.error is not None
    return result.error


_SOURCE_FNS = {
    "alpaca": _alpaca_error,
    "finnhub": _finnhub_error,
    "fred": _fred_error,
    "ecb": _ecb_error,
}


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["http", "conn"])
@pytest.mark.parametrize("source_name", sorted(_SOURCE_FNS))
async def test_source_error_scrubbed_end_to_end(
    source_name: str,
    kind: str,
    client: TestClient,
    session_factory: SessionFactory,
) -> None:
    error = await _SOURCE_FNS[source_name](kind)
    await _surfaces(
        source_name, error, client=client, session_factory=session_factory
    )


def test_safe_error_shapes() -> None:
    """The unit-level guarantees the e2e relies on."""
    out = safe_error(_http_error(), extra=[STORED_SECRET])
    assert FAKE_KEY not in out
    assert STORED_SECRET not in out
    assert "page=2" not in out
    assert "https://data.example/feed" in out
    out = safe_error(_conn_error(), extra=[STORED_SECRET])
    assert FAKE_KEY not in out
    assert "token=" not in out
