"""Shared test helpers (issue #120).

Builders that several test modules used to re-implement verbatim. Only
helpers whose bodies were identical (or differed by a flag) live here;
variants that build genuinely different fixtures stay in their module.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import app.config as config_module
import pytest
from app.api.main import create_app, run_migrations
from app.auth.create_user import create_or_reset_user
from app.auth.passwords import totp_code
from app.broker.models import BrokerAccount
from app.config import get_config
from app.data.models import Bar1h, Bar5m, DailyBar, Quote
from app.db.base import get_session_maker
from app.db.models import Cycle, Decision, Event, Position
from app.engine.state import load_execution_state
from app.market.clock import FakeClock, make_clock
from app.rules.prefilter import RulesResult
from app.secrets_service import FakeKeyTesters, SecretsStore
from app.signals.service import SymbolEval
from app.signals.snapshot import IndicatorSnapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# bar builders (decision v2 levels and options)


def bar1h(day: date, start_h: int, start_m: int, high: str, low: str,
        close: str | None = None, o: str | None = None) -> Bar1h:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(
        hour=start_h, minute=start_m
    )
    return Bar1h(
        symbol="X", start=start, end=start + timedelta(hours=1),
        open=Decimal(o or low), high=Decimal(high), low=Decimal(low),
        close=Decimal(close or low), volume=1000, feed="sip", complete=True,
    )


def dbar(day: date, o: str, h: str, lo: str, c: str) -> DailyBar:
    return DailyBar(
        symbol="X", date=day, open=Decimal(o), high=Decimal(h),
        low=Decimal(lo), close=Decimal(c), volume=100_000,
    )


def bar5m(day: date, h: int, m: int, high: str, low: str, close: str,
        vol: int = 1000) -> Bar5m:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(
        hour=h, minute=m
    )
    return Bar5m(
        symbol="X", start=start, open=Decimal(close), high=Decimal(high),
        low=Decimal(low), close=Decimal(close), volume=vol, feed="sip",
    )


def wave_bars(day: date, n: int, base: float = 100.0, step: float = 1.0,
              seed: int = 7) -> list[Bar1h]:
    """n 1h bars on a triangle wave with seeded wicks — strict swings.

    Two bars up, two down, … gives local extremes every other bar; the
    seeded wicks break the exact ties a shared peak price would cause.
    """
    rng = random.Random(seed)
    bars: list[Bar1h] = []
    price = base
    for i in range(n):
        d = step if (i // 2) % 2 == 0 else -step
        nxt = price + d
        hi = max(price, nxt) + rng.uniform(0.02, 0.2) * step
        lo = min(price, nxt) - rng.uniform(0.02, 0.2) * step
        start = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=i)
        bars.append(Bar1h(
            symbol="X", start=start, end=start + timedelta(hours=1),
            open=Decimal(str(round(price, 4))), high=Decimal(str(round(hi, 4))),
            low=Decimal(str(round(lo, 4))), close=Decimal(str(round(nxt, 4))),
            volume=1000, feed="sip", complete=True,
        ))
        price = nxt
    return bars


# ----------------------------------------------------------------------
# executor fixtures (test_executor, test_pr6)

NOW = datetime(2026, 10, 15, 17, 0, tzinfo=UTC)
YESTERDAY = datetime(2026, 10, 14, 15, 0)  # naive UTC, stored format


def make_account(
    equity: str = "10000", cash: str = "10000", last_equity: str = "10000"
) -> BrokerAccount:
    return BrokerAccount(
        account_number="PA-TEST",
        equity=Decimal(equity),
        cash=Decimal(cash),
        last_equity=Decimal(last_equity),
    )


def make_decision(
    session: Session,
    symbol: str,
    final_action: str,
    model_output: dict[str, object] | None = None,
) -> Decision:
    """A running cycle at NOW with one decision on it (flushed)."""
    cycle = Cycle(
        kind="cycle",
        scheduled_time=NOW.replace(tzinfo=None),
        started_at=NOW.replace(tzinfo=None),
        status="running",
    )
    session.add(cycle)
    session.flush()
    decision = Decision(
        cycle_id=cycle.id,
        symbol=symbol,
        context="candidate" if final_action == "enter" else "held",
        final_action=final_action,
        model_output=model_output,
        prompt_version="decision.v1",
        prompt_hash="0" * 64,
        model="fake",
    )
    session.add(decision)
    session.flush()
    return decision


def make_snapshot(symbol: str, days_to_earnings: int | None = 9) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        symbol=symbol,
        price=Decimal("420"),
        ema_fast=Decimal("416.9"),
        ema_slow=Decimal("414.3"),
        rsi=Decimal("57"),
        atr_1h=Decimal("3.10"),
        session_vwap=Decimal("417.4"),
        obv_trend="up",
        rs_vs_spy_pct=Decimal("0.9"),
        daily_close=Decimal("415.8"),
        daily_sma_fast=Decimal("402.1"),
        daily_sma_slow=Decimal("381.6"),
        daily_atr_percentile=Decimal("48"),
        opening_gap_pct=Decimal("0.3"),
        days_to_earnings=days_to_earnings,
        earnings_available=True,
    )


def make_item(symbol: str) -> SymbolEval:
    return SymbolEval(
        symbol=symbol,
        snapshot=make_snapshot(symbol),
        rules=RulesResult(symbol, "pass", None, []),
        bars_1h=[],
        daily=[],
        today_5m=[],
        price=Decimal("420"),
        day_change_pct=Decimal("0.5"),
    )


def make_quote(
    symbol: str,
    ask: str = "420.10",
    bid: str = "420.00",
    last: str | None = None,
) -> Quote:
    return Quote(
        symbol=symbol,
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=Decimal(last) if last is not None else Decimal(ask),
        timestamp=NOW,
        feed="iex",
        trade_timestamp=NOW,
        fetched_at=NOW,
    )


def open_position(
    session: Session,
    symbol: str = "MSFT",
    qty: int = 4,
    entry: str = "415",
    stop: str = "410",
    opened_at: datetime = YESTERDAY,
    origin: str = "agent",
) -> Position:
    position = Position(
        symbol=symbol,
        origin=origin,
        pending_ack=False,
        qty=qty,
        avg_entry_price=Decimal(entry),
        current_stop_price=Decimal(stop),
        target_price=Decimal("435"),
        opened_at=opened_at,
    )
    session.add(position)
    session.flush()
    return position


def events_of(session: Session, event_type: str) -> list[Event]:
    return list(
        session.execute(
            select(Event).where(Event.event_type == event_type)
        ).scalars()
    )


# ----------------------------------------------------------------------
# key routes without the lifespan (test_keys, test_pr29_keys)


class StubExecutor:
    def __init__(self) -> None:
        self.resumed = False

    async def resume_from_pause(self) -> None:
        self.resumed = True

    async def refresh_account(self) -> None:
        return None


def paper_keys_client(
    *, ask_model: str | None = None, stub_broker: bool = False
) -> tuple[TestClient, StubExecutor]:
    """A paper-mode app without the lifespan: only the state the routes
    need is installed, so no real client is ever constructed.

    TestClient without __enter__ never runs the lifespan, so the manual
    state is the whole world — no network-capable client exists.
    """
    run_migrations()
    config = get_config()
    config.env.mode = "paper"
    if ask_model is not None:
        config._active.setdefault("ask", {})["model"] = ask_model
    app = create_app()
    clock = make_clock(config)
    app.state.clock = clock
    app.state.exec_state = load_execution_state(clock, config)
    app.state.secrets_store = SecretsStore(config, clock)
    app.state.key_testers = FakeKeyTesters()
    executor = StubExecutor()
    app.state.executor = executor
    if stub_broker:
        # Settings validation only touches the broker for universe changes.
        app.state.broker = object()
    return TestClient(app), executor


# ----------------------------------------------------------------------
# sign-in (SA_AUTH=required apps)

PASSWORD = "a fourteen char password"


def seed_user(now: datetime | None = None) -> tuple[str, list[str]]:
    """Create (or reset) the single user; returns (TOTP secret, codes)."""
    with get_session_maker()() as session:
        _user, secret, codes = create_or_reset_user(
            session,
            get_config(),
            FakeClock(now or datetime.now(UTC)),
            username="marc",
            password=PASSWORD,
        )
    return secret, codes


def auth_app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, str, list[str]]:
    """SA_AUTH=required app with the single user seeded."""
    monkeypatch.setenv("SA_AUTH", "required")
    monkeypatch.setattr(config_module, "_config", None)
    secret, codes = seed_user()
    return create_app(), secret, codes


def sign_in(
    client: TestClient,
    secret: str,
    *,
    remember: bool = False,
    advance: bool = True,
) -> str:
    """Password then code; returns the CSRF token for write calls.

    ``advance`` moves the FakeClock past the current TOTP step first so
    a code burnt by an earlier sign-in in the same test is not replayed.
    """
    app = cast(FastAPI, client.app)
    clock: Any = app.state.clock
    if advance:
        clock.advance(timedelta(seconds=31))
    body: dict[str, object] = {"password": PASSWORD}
    if remember:
        body["remember"] = True
    r = client.post("/api/auth/sign-in", json=body)
    assert r.status_code == 200, r.text
    r = client.post("/api/auth/code", json={"code": totp_code(secret, clock.now())})
    assert r.status_code == 200, r.text
    return client.cookies.get("sa_csrf") or ""


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("sa_csrf") or ""}


def public_resolver(host: str, *_a: Any) -> list[Any]:
    """``socket.getaddrinfo``-shaped fake answering one public address —
    the outbound-address checks (item 085) never touch DNS in tests."""
    return [(0, 0, 0, "", ("93.184.216.34", 0))]
