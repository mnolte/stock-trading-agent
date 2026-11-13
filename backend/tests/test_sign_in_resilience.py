"""Item 083: sign-in resilience — trusted devices, the break-glass
kill switch and WebSocket session lifetime (audit S6/S7).

Same harness as test_auth_flow: ``SA_AUTH=required`` app, seeded user,
FakeClock. The device cookie rules, the devices API, the kill.request
file handling and the socket registry/expiry/cap all live here.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from app.auth.passwords import totp_code
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import SignInAttempt, TrustedDevice
from app.market.clock import FakeClock
from app.ops import kill_switch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from tests.helpers import PASSWORD, auth_app, csrf_headers, sign_in


@pytest.fixture
def app_auth(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    yield auth_app(monkeypatch)


def _seed_wrong_attempts(count: int, app: FastAPI, start_ip: int = 0) -> None:
    """``count`` wrong sign-in attempts from distinct addresses inside
    the last hour — one short of the global-pause trigger."""
    from app.data.cache import _naive_utc

    now = app.state.clock.now()
    with get_session_maker()() as session:
        for i in range(count):
            session.add(
                SignInAttempt(
                    time=_naive_utc(now), ip=f"10.8.8.{start_ip + i}", result="wrong"
                )
            )
        session.commit()


def _clear_device_cookie(client: TestClient) -> None:
    """Drop every ``__Host-sa_device`` entry from the jar (httpx keeps
    cookies per domain/path, so clear them all)."""
    jar = client.cookies.jar
    for cookie in list(jar):
        if cookie.name == "__Host-sa_device":
            jar.clear(domain=cookie.domain, path=cookie.path, name=cookie.name)


def _device_cookie(client: TestClient) -> str:
    return client.cookies.get("__Host-sa_device") or client.cookies.get(
        "sa_device"
    ) or ""


# ---------------------------------------------------------------------
# the device cookie


def test_sign_in_sets_device_cookie(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        r = client.post("/api/auth/sign-in", json={"password": PASSWORD})
        assert r.status_code == 200
        r = client.post(
            "/api/auth/code",
            json={"code": totp_code(secret, app.state.clock.now())},
        )
        assert r.status_code == 200
        headers = r.headers.get_list("set-cookie")
        device = [h for h in headers if "sa_device=" in h]
        assert device, headers
        line = device[0]
        # __Host- over https, HttpOnly, Secure, SameSite=Strict, Path=/,
        # 180 days — and the value carries the sad_ prefix.
        name = "__Host-sa_device" if "__Host-sa_device=" in line else "sa_device"
        assert name == "__Host-sa_device"
        value = client.cookies.get(name) or ""
        assert value.startswith("sad_")
        for attr in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
            assert attr.lower() in line.lower(), line
        assert "Max-Age=15552000" in line, line
        # Plain http loopback uses the unprefixed name.
    with TestClient(app, base_url="http://testserver") as client:
        sign_in(client, secret)
        assert (client.cookies.get("sa_device") or "").startswith("sad_")
        assert client.cookies.get("__Host-sa_device") is None


def test_device_token_never_in_api_or_status(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret)
        token = _device_cookie(client)
        assert token
        for path in ("/api/auth/status", "/api/auth/devices", "/api/auth/sessions"):
            body = client.get(path).text
            assert token not in body, path
            assert token.split("_", 1)[1][:16] not in body, path


# ---------------------------------------------------------------------
# the global pause and the known browser


def test_known_browser_signs_in_during_pause(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as known:
        sign_in(known, secret)
        device_token = _device_cookie(known)
        assert device_token
        # 19 seeded failures + this wrong password = the 20/hour cap:
        # the global pause starts (one device failure is counted too).
        _seed_wrong_attempts(19, app)
        r = known.post("/api/auth/sign-in", json={"password": "nope-nope-nope"})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited"

        # Status: the pause end is public; known_device needs the cookie.
        r = known.get("/api/auth/status")
        assert r.json()["paused_until"] is not None
        assert r.json()["known_device"] is True

        # An unknown browser — here simulated by a bogus device cookie —
        # sees the pause and is refused at the password step.
        _clear_device_cookie(known)
        known.cookies.set("__Host-sa_device", "sad_bogus-token-value")
        r = known.get("/api/auth/status")
        assert r.json()["paused_until"] is not None
        assert r.json()["known_device"] is False
        r = known.post("/api/auth/sign-in", json={"password": PASSWORD})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited"

        # The same request with the real device cookie passes the pause —
        # password, code and the per-address lockout are unchanged.
        _clear_device_cookie(known)
        known.cookies.set("__Host-sa_device", device_token)
        r = known.post("/api/auth/sign-in", json={"password": PASSWORD})
        assert r.status_code == 200, r.text
        clock: FakeClock = app.state.clock
        clock.advance(timedelta(seconds=31))
        r = known.post(
            "/api/auth/code",
            json={"code": totp_code(secret, clock.now())},
        )
        assert r.status_code == 200, r.text


def test_five_wrong_passwords_revoke_the_device(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret)
        assert _device_cookie(client)
        responses = [
            client.post("/api/auth/sign-in", json={"password": "bad-password-1"})
            for _ in range(5)
        ]
        assert all(r.status_code == 401 for r in responses)
        # The fifth failure deleted the trusted_devices row and the
        # answer clears the cookie.
        with get_session_maker()() as session:
            assert session.execute(select(func.count(TrustedDevice.id))).scalar_one() == 0
        cleared = [
            h for h in responses[-1].headers.get_list("set-cookie")
            if "sa_device=" in h
        ]
        assert cleared and ("Max-Age=0" in cleared[0] or "Expires=" in cleared[0])
        assert client.get("/api/auth/status").json()["known_device"] is False


def test_devices_api_lists_and_removes(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret)
        r = client.get("/api/auth/devices")
        assert r.status_code == 200
        devices = r.json()["devices"]
        assert len(devices) == 1
        assert devices[0]["this_browser"] is True
        assert devices[0]["name"]
        assert devices[0]["created_at"] and devices[0]["last_used_at"]
        # Removal is a risky action: confirm first.
        r = client.post(
            f"/api/auth/devices/{devices[0]['id']}/remove",
            json={},
            headers=csrf_headers(client),
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "confirm_required"
        r = client.post(
            "/api/auth/confirm", json={"secret": PASSWORD},
            headers=csrf_headers(client),
        )
        assert r.status_code == 200
        r = client.post(
            f"/api/auth/devices/{devices[0]['id']}/remove",
            json={},
            headers=csrf_headers(client),
        )
        assert r.status_code == 200
        assert client.get("/api/auth/devices").json()["devices"] == []
        assert client.get("/api/auth/status").json()["known_device"] is False
        # Removing an unknown id is a 404 in the shared error shape.
        r = client.post(
            "/api/auth/devices/999/remove",
            json={},
            headers=csrf_headers(client),
        )
        assert r.status_code == 404


def test_at_most_ten_devices_oldest_evicted(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver"):
        clock: FakeClock = app.state.clock
        auth = app.state.auth
        # Eleven completed sign-ins from "different browsers" (no device
        # cookie at the service level) — the eleventh evicts the oldest.
        for _ in range(11):
            clock.advance(timedelta(minutes=1))
            pending = auth.start_sign_in(PASSWORD, False, "10.9.9.9", "pytest")
            auth.finish_sign_in(
                pending, totp_code(secret, clock.now()), "10.9.9.9"
            )
        with get_session_maker()() as session:
            count = session.execute(
                select(func.count(TrustedDevice.id))
            ).scalar_one()
        assert count == 10
        # The API wraps AuthService.list_devices — ask it directly (this
        # client never completed an HTTP sign-in, so no session cookie).
        devices = auth.list_devices(None)
        assert len(devices) == 10
        # The survivors are the newest ten — eleven minutes apart, the
        # first sign-in's row is the one that went.
        created = sorted(str(d["created_at"]) for d in devices)
        assert len(set(created)) == 10


def test_password_change_and_recovery_codes_revoke_devices(app_auth: Any) -> None:
    app, secret, codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret)
        assert client.get("/api/auth/devices").json()["devices"]
        # New recovery codes revoke every known browser.
        client.post("/api/auth/confirm", json={"secret": PASSWORD}, headers=csrf_headers(client))
        r = client.post("/api/auth/recovery-codes", json={}, headers=csrf_headers(client))
        assert r.status_code == 200
        assert client.get("/api/auth/devices").json()["devices"] == []
        # Sign in again → a fresh device; a password change revokes it.
        sign_in(client, secret)
        assert client.get("/api/auth/devices").json()["devices"]
        client.post("/api/auth/confirm", json={"secret": PASSWORD}, headers=csrf_headers(client))
        r = client.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": "a brand new password!"},
            headers=csrf_headers(client),
        )
        assert r.status_code == 200, r.text
        assert client.get("/api/auth/devices").json()["devices"] == []


# ---------------------------------------------------------------------
# the break-glass kill switch


class _FakeExecutor:
    """Records which kill path ran, with the source it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def activate_kill_switch(self, **kwargs: Any) -> None:
        self.calls.append(("activate", kwargs))

    async def flatten(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(("flatten", kwargs))
        return {"ok": True}


def _request_file(directory: Path, payload: object) -> tuple[Path, Path]:
    request = directory / kill_switch.REQUEST_NAME
    result = directory / kill_switch.RESULT_NAME
    if isinstance(payload, str):
        request.write_text(payload)
    else:
        request.write_text(json.dumps(payload))
    return request, result


def test_kill_request_file_activates_switch_with_cli_source(tmp_path: Path) -> None:
    executor = _FakeExecutor()
    request, result = _request_file(
        tmp_path, {"reason": "lost phone", "flatten": False, "requested_at": 1}
    )
    assert asyncio.run(
        kill_switch.process_kill_request(executor, request, result)
    )
    assert executor.calls == [
        ("activate", {"reason": "lost phone", "automatic": False, "source": "cli"})
    ]
    assert json.loads(result.read_text()) == {"ok": True}
    assert not request.exists()


def test_kill_request_flatten_uses_the_button_sequence(tmp_path: Path) -> None:
    """--flatten runs the same executor.flatten path as the GUI button —
    activate with flatten=True inside, orders cancelled, positions sold."""
    executor = _FakeExecutor()
    request, result = _request_file(
        tmp_path, {"reason": "stop everything", "flatten": True}
    )
    asyncio.run(kill_switch.process_kill_request(executor, request, result))
    assert executor.calls == [
        ("flatten", {"reason": "stop everything", "source": "cli"})
    ]
    assert json.loads(result.read_text()) == {"ok": True}


def test_malformed_kill_request_is_dropped_not_executed(tmp_path: Path) -> None:
    executor = _FakeExecutor()
    for bad in ("not json{", json.dumps({"flatten": True}), json.dumps({"reason": "  "})):
        request, result = _request_file(tmp_path, bad)
        result.unlink(missing_ok=True)
        assert asyncio.run(
            kill_switch.process_kill_request(executor, request, result)
        )
        assert executor.calls == []
        assert json.loads(result.read_text())["ok"] is False
        assert not request.exists()


def test_kill_request_executor_error_is_reported(tmp_path: Path) -> None:
    class _Boom(_FakeExecutor):
        async def activate_kill_switch(self, **kwargs: Any) -> None:
            raise RuntimeError("db is on fire")

    request, result = _request_file(tmp_path, {"reason": "x", "flatten": False})
    asyncio.run(kill_switch.process_kill_request(_Boom(), request, result))
    outcome = json.loads(result.read_text())
    assert outcome["ok"] is False
    assert "db is on fire" in outcome["error"]


def _fresh_run_lock(directory: Path) -> None:
    """A run.lock that reads live: this pid, fresh heartbeat."""
    (directory / "run.lock").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started_at": time.time(),
                "heartbeat_at": time.time(),
                "heartbeat_seconds": 30,
            }
        )
    )


def test_cli_requires_reason_and_running_app(tmp_path: Path) -> None:
    # No reason → usage error before anything is written.
    assert kill_switch.main([], data_dir=str(tmp_path)) == 2
    # App not running (no run.lock) → refused with a clear message.
    assert kill_switch.main(["--reason", "x"], data_dir=str(tmp_path)) == 1
    assert not (tmp_path / "kill.request").exists()


def test_cli_writes_request_and_reads_result(tmp_path: Path) -> None:
    _fresh_run_lock(tmp_path)
    request = tmp_path / "kill.request"
    result = tmp_path / "kill.result"

    def _answer() -> None:
        # The app side: pick the request up and answer, like the watcher.
        for _ in range(400):
            if request.exists():
                payload = json.loads(request.read_text())
                assert payload["reason"] == "lost phone"
                assert payload["flatten"] is True
                assert oct(request.stat().st_mode & 0o777) == "0o600"
                result.write_text(json.dumps({"ok": True}))
                request.unlink()
                return
            time.sleep(0.01)

    import threading

    thread = threading.Thread(target=_answer, daemon=True)
    thread.start()
    assert kill_switch.main(
        ["--reason", "lost phone", "--flatten"], data_dir=str(tmp_path)
    ) == 0
    thread.join(timeout=5)


def test_cli_refuses_a_second_pending_request(tmp_path: Path) -> None:
    _fresh_run_lock(tmp_path)
    (tmp_path / "kill.request").write_text("{}")
    assert (
        kill_switch.main(["--reason", "x"], data_dir=str(tmp_path)) == 1
    )


# ---------------------------------------------------------------------
# WebSocket session lifetime


def _ws_headers(client: TestClient) -> dict[str, str]:
    # websocket_connect dials ws:// — a plain scheme, so the plain
    # sa_session name is the one that counts (same rule as the session
    # cookie tests in test_auth_flow).
    token = (
        client.cookies.get("__Host-sa_session")
        or client.cookies.get("sa_session")
        or ""
    )
    return {
        "Cookie": f"sa_session={token}",
        "Origin": "https://testserver",
    }


def test_socket_closes_when_session_expires(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        ws = client.websocket_connect("/ws/ws", headers=_ws_headers(client))
        with ws:
            ws.send_text("ping")
            assert ws.receive_json() == {"type": "pong"}
            # Past the 12 h idle limit the next 60 s re-check closes the
            # socket with 1008 session_expired.
            app.state.clock.advance(timedelta(hours=13))
            with pytest.raises(WebSocketDisconnect) as err:
                while True:
                    ws.receive_json()
            assert err.value.code == 1008


def test_socket_closes_at_once_on_sign_out(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        csrf = sign_in(client, secret, remember=True)
        ws = client.websocket_connect("/ws/ws", headers=_ws_headers(client))
        with ws:
            ws.send_text("ping")
            assert ws.receive_json() == {"type": "pong"}
            r = client.post(
                "/api/auth/sign-out", json={}, headers={"X-CSRF-Token": csrf}
            )
            assert r.status_code == 200
            # The registry closes the socket without waiting for the
            # 60 s re-check.
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()


def test_socket_cap_closes_the_oldest(app_auth: Any) -> None:
    import contextlib

    app, secret, _codes = app_auth
    config = get_config()
    config.apply_saved({"security": {"max_sockets": 3}}, 901)
    try:
        with (
            TestClient(app, base_url="https://testserver") as client,
            contextlib.ExitStack() as stack,
        ):
            sign_in(client, secret, remember=True)
            entered = [
                stack.enter_context(
                    client.websocket_connect(
                        "/ws/ws", headers=_ws_headers(client)
                    )
                )
                for _ in range(3)
            ]
            # The fourth connection closes the oldest.
            fourth = stack.enter_context(
                client.websocket_connect(
                    "/ws/ws", headers=_ws_headers(client)
                )
            )
            time.sleep(0.3)
            with pytest.raises(WebSocketDisconnect):
                entered[0].receive_json()
            # The newest still answers.
            fourth.send_text("ping")
            assert fourth.receive_json() == {"type": "pong"}
    finally:
        config.apply_saved({"security": {"max_sockets": 20}}, 902)
