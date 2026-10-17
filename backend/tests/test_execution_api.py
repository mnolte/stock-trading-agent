"""Startup rules and the execution API surface (kill switch, banners)."""

from datetime import datetime
from decimal import Decimal
from typing import cast

import pytest
from app.api.main import check_execution_mode
from app.config import AppConfig
from app.db.models import Position
from app.decide.snapshot import build_input_snapshot
from app.signals.regime import MarketRegime
from app.signals.snapshot import IndicatorSnapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.anyio


def test_supervised_mode_starts() -> None:
    """PR 6: supervised mode is a real mode — startup allows it."""
    config = AppConfig()
    config.yaml["execution"]["mode"] = "supervised"
    check_execution_mode(config)  # no raise


def test_unknown_execution_mode_refuses_to_start() -> None:
    config = AppConfig()
    config.yaml["execution"]["mode"] = "copilot"
    with pytest.raises(RuntimeError, match="unknown execution.mode"):
        check_execution_mode(config)


def test_automatic_mode_starts() -> None:
    config = AppConfig()
    config.yaml["execution"]["mode"] = "automatic"
    check_execution_mode(config)  # no raise


def test_deferred_exit_is_included_in_next_snapshot() -> None:
    """A deferred same-day exit shows up in the next session's snapshot."""
    position = Position(
        symbol="MSFT",
        qty=4,
        avg_entry_price=Decimal("415"),
        current_stop_price=Decimal("410"),
        target_price=Decimal("435"),
        opened_at=datetime(2026, 10, 14, 15, 0),
        deferred_exit={
            "at": "2026-10-15T17:42:00+00:00",
            "reason": "day_trade_rule",
            "detail": "model exit",
        },
    )
    snapshot = build_input_snapshot(
        symbol="MSFT",
        context="held",
        cycle_time_ny=datetime(2026, 10, 16, 10, 46),
        price=Decimal("421"),
        regime=MarketRegime(
            trend="up",
            volatility="normal",
            key="up_normal",
            label="Up · normal vol",
            blocks_entries=False,
            atr_percentile=Decimal("48"),
        ),
        snapshot=IndicatorSnapshot(
            symbol="MSFT",
            price=Decimal("421"),
            ema_fast=Decimal("416.9"),
            ema_slow=Decimal("414.3"),
            rsi=Decimal("57"),
            atr_1h=Decimal("3.1"),
            session_vwap=Decimal("417.4"),
            obv_trend="up",
            rs_vs_spy_pct=Decimal("0.9"),
            daily_close=Decimal("415.8"),
            daily_sma_fast=Decimal("402.1"),
            daily_sma_slow=Decimal("381.6"),
            daily_atr_percentile=Decimal("48"),
            opening_gap_pct=Decimal("0.3"),
            days_to_earnings=9,
            earnings_available=True,
        ),
        bars_1h=[],
        position=position,
        news=None,
    )
    block = snapshot["position"]
    assert isinstance(block, dict)
    assert block["deferred_exit"] == position.deferred_exit


def test_kill_switch_api_flow(client: TestClient) -> None:
    """Kill switch on → banner → resume with reason → off."""
    header = client.get("/api/header").json()
    assert header["paper"] is True
    assert header["kill_switch"] is False

    # Resume without a reason is rejected.
    assert client.post("/api/kill-switch/resume", json={}).status_code == 422

    on = client.post("/api/kill-switch", json={"reason": "testing"}).json()
    assert on["kill_switch"] is True
    banners = client.get("/api/banners").json()["items"]
    kill = next(b for b in banners if b["kind"] == "kill_switch")
    assert kill["automatic"] is False
    assert "resume" in kill["actions"] and "flatten" in kill["actions"]

    off = client.post(
        "/api/kill-switch/resume", json={"reason": "verified by test"}
    ).json()
    assert off["kill_switch"] is False
    assert client.get("/api/header").json()["kill_switch"] is False


def test_app_starts_paused_and_resume_needs_no_reason(
    client: TestClient,
) -> None:
    """Paused at startup: header + blue banner; resume is one click."""
    header = client.get("/api/header").json()
    assert header["paused"] is True
    assert header["paused_since"] is not None

    banners = client.get("/api/banners").json()["items"]
    paused = next(b for b in banners if b["kind"] == "paused")
    assert paused["level"] == "info"
    assert "Started paused" in paused["message"]
    assert paused["actions"] == ["resume_pause"]

    # Resume from pause takes no body and no typed reason.
    off = client.post("/api/pause/resume", json={}).json()
    assert off["paused"] is False
    assert client.get("/api/header").json()["paused"] is False
    items = client.get("/api/banners").json()["items"]
    assert not any(b["kind"] == "paused" for b in items)

    # Pause can be used any time — also one click.
    assert client.post("/api/pause", json={}).json()["paused"] is True
    assert client.get("/api/header").json()["paused"] is True
    # A manual pause shows no "Started paused" banner.
    items = client.get("/api/banners").json()["items"]
    assert not any(b["kind"] == "paused" for b in items)
    assert client.post("/api/pause/resume", json={}).json()["paused"] is False


def test_paused_banner_claims_reconcile_only_after_a_good_pass(
    client: TestClient,
) -> None:
    """A failed startup reconcile must not produce "Reconciled with Alpaca"."""
    state = cast(FastAPI, client.app).state.exec_state
    assert state.last_reconcile_at is not None  # lifespan ran reconcile
    assert state.last_reconcile_ok is True

    banners = client.get("/api/banners").json()["items"]
    paused = next(b for b in banners if b["kind"] == "paused")
    assert "Reconciled with Alpaca" in paused["message"]

    state.last_reconcile_ok = False
    banners = client.get("/api/banners").json()["items"]
    paused = next(b for b in banners if b["kind"] == "paused")
    assert "Started paused" in paused["message"]
    assert "Reconciled" not in paused["message"]


def test_approve_unknown_id_reports_not_pending_while_paused(
    client: TestClient,
) -> None:
    """Paused startup: a bad id gets "not pending", not the pause error."""
    assert client.get("/api/header").json()["paused"] is True
    response = client.post("/api/approvals/99999/approve")
    assert response.status_code == 422
    assert response.json()["detail"] == "approval is not pending"


def test_positions_and_orders_endpoints(client: TestClient) -> None:
    positions = client.get("/api/positions").json()
    assert {
        "open",
        "closed_today",
        "day_trades_5d",
        "day_trades",
        "account",
        "reconciliation",
    } <= set(positions)
    assert positions["day_trades_5d"] == 0
    orders = client.get("/api/orders").json()
    assert "orders" in orders


def test_acknowledge_unknown_position_404s(client: TestClient) -> None:
    response = client.post("/api/positions/99999/acknowledge")
    assert response.status_code == 404
    assert client.post("/api/events/99999/acknowledge").status_code == 404


def test_flatten_sell_rejected_shows_critical_banner(client: TestClient) -> None:
    """A rejected flatten sale is a critical, ack-able banner."""
    from app.db.base import get_session_maker
    from app.db.models import Event

    with get_session_maker()() as session:
        session.add(
            Event(
                time=datetime(2026, 10, 15, 17, 0),
                level="critical",
                event_type="flatten_sell_rejected",
                details={"symbol": "MSFT", "error": "403: rejected"},
            )
        )
        session.commit()

    banners = client.get("/api/banners").json()["items"]
    item = next(b for b in banners if b["kind"] == "flatten_sell_rejected")
    assert item["level"] == "critical"
    assert "MSFT" in item["message"] and "stop" in item["message"]
    assert item["actions"] == ["acknowledge"]
