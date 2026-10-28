"""PR 24 review: the resume rules exercised over two real app boots.

First boot ends cleanly (TestClient context exit runs the graceful
shutdown); the second boot then decides. Five cases: clean + running →
resumes; unclean stop, kill switch, reconciliation difference and
unconfirmed broker guard → stays paused. Banner and notification are
checked each time.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import app.config as config_module
import pytest
from app.api.main import create_app
from app.auth.create_user import create_or_reset_user
from app.auth.passwords import totp_code
from app.config import get_config
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import Event, Notification
from app.market.clock import FakeClock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

PASSWORD = "a fourteen char password"
NOW = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)


def _insert_event(event_type: str, details: dict[str, object] | None = None) -> None:
    with get_session_maker()() as session:
        session.add(
            Event(
                time=_naive_utc(NOW),
                level="info",
                event_type=event_type,
                details=details,
            )
        )
        session.commit()


def _seed_user() -> str:
    with get_session_maker()() as session:
        _u, secret, _codes = create_or_reset_user(
            session,
            get_config(),
            FakeClock(NOW),
            username="marc",
            password=PASSWORD,
        )
    return secret


def _sign_in(client: TestClient, secret: str) -> None:
    r1 = client.post("/api/auth/sign-in", json={"password": PASSWORD})
    assert r1.status_code == 200, r1.text
    clock: Any = cast(FastAPI, client.app).state.clock
    r2 = client.post(
        "/api/auth/code",
        json={"code": totp_code(secret, clock.now())},
    )
    assert r2.status_code == 200, r2.text


def _events(event_type: str) -> list[Event]:
    with get_session_maker()() as session:
        return list(
            session.execute(
                select(Event)
                .where(Event.event_type == event_type)
                .order_by(Event.id.desc())
            )
            .scalars()
            .all()
        )


def _notifications(app: FastAPI) -> list[Notification]:
    asyncio.run(app.state.notification_service.drain())
    with get_session_maker()() as session:
        return list(
            session.execute(select(Notification).order_by(Notification.id.desc()))
            .scalars()
            .all()
        )


def _banners(client: TestClient) -> list[dict[str, Any]]:
    r = client.get("/api/banners")
    assert r.status_code == 200, r.text
    items: list[dict[str, Any]] = r.json()["items"]
    return items


@pytest.fixture
def auth_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """SA_AUTH=required — resume_after_clean_restart auto = on (the VPS)."""
    monkeypatch.setenv("SA_AUTH", "required")
    monkeypatch.setenv("SA_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setattr(config_module, "_config", None)


def _boot() -> TestClient:
    app = create_app()
    return TestClient(app)


def _first_boot_then_resume_marker() -> None:
    """Boot once (clean stop on exit) and leave a pause-resume marker so
    the second boot sees 'trading was running before'."""
    with _boot():
        pass  # clean shutdown on __exit__
    _insert_event("pause", {"on": False})  # manual resume = was running


def test_clean_running_boot_resumes(auth_boot: None, tmp_path: Path) -> None:
    _seed_user()
    _first_boot_then_resume_marker()
    # 30 s for the pending-ticket step: use a recovery-code-free sign-in
    with _boot() as client:
        app = cast(FastAPI, client.app)
        assert app.state.exec_state.paused is False
        resumed = _events("restart_resumed")
        assert resumed, "restart_resumed event missing"
        # banner
        secret = _seed_user()
        _sign_in(client, secret)
        kinds = {b["kind"] for b in _banners(client)}
        assert "restart_resumed" in kinds
        # notification
        events = {n.event for n in _notifications(app)}
        assert "restarted_resumed" in events


def _paused_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, saboteur: str
) -> None:
    _seed_user()
    _first_boot_then_resume_marker()
    if saboteur == "unclean":
        # a lock from a different boot = the previous run died
        lock = tmp_path / "data" / "run.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps({"pid": 999999999, "boot_id": "other-boot", "heartbeat": 0})
        )
    elif saboteur == "kill_switch":
        _insert_event("kill_switch", {"on": True, "reason": "test"})
    elif saboteur == "mismatch":
        from app.engine.executor import Executor, ReconcileReport

        async def bad_reconcile(self: Any) -> ReconcileReport:
            report = ReconcileReport()
            report.mismatches.append("AAPL: broker says 10, db says 5")
            return report

        monkeypatch.setattr(Executor, "reconcile", bad_reconcile)
    elif saboteur == "guard":
        import app.api.main as main_module
        from app.engine.state import load_execution_state as real_load

        def unconfirmed(*args: Any, **kwargs: Any) -> Any:
            state = real_load(*args, **kwargs)
            state.broker_guard_confirmed = False
            return state

        monkeypatch.setattr(main_module, "load_execution_state", unconfirmed)
    else:  # pragma: no cover
        raise AssertionError(saboteur)

    with _boot() as client:
        app = cast(FastAPI, client.app)
        assert app.state.exec_state.paused is True, saboteur
        paused_rows = _events("restart_paused")
        assert paused_rows, f"restart_paused event missing ({saboteur})"
        secret = _seed_user()
        _sign_in(client, secret)
        kinds = {b["kind"] for b in _banners(client)}
        assert "restart_paused" in kinds
        events = {n.event for n in _notifications(app)}
        assert "restarted_paused" in events


def test_unclean_stop_stays_paused(
    auth_boot: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _paused_boot(tmp_path, monkeypatch, saboteur="unclean")


def test_kill_switch_stays_paused(
    auth_boot: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _paused_boot(tmp_path, monkeypatch, saboteur="kill_switch")


def test_reconcile_mismatch_stays_paused(
    auth_boot: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _paused_boot(tmp_path, monkeypatch, saboteur="mismatch")


def test_unconfirmed_guard_stays_paused(
    auth_boot: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _paused_boot(tmp_path, monkeypatch, saboteur="guard")
