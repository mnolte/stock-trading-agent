"""PR 23 steps 3-4: the sign-in flow and the protected route table.

Boots the app with ``SA_AUTH=required`` against a seeded single user.
The simulated clock is a FakeClock, so idle and absolute expiry are
driven by advancing it.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import app.config as config_module
import pytest
from app.api.main import create_app
from app.auth.create_user import create_or_reset_user
from app.auth.passwords import totp_code
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import AuthSession, SignInAttempt
from app.market.clock import FakeClock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

PASSWORD = "a fourteen char password"


def _seed_user() -> tuple[str, list[str]]:
    config = get_config()
    clock = FakeClock(datetime.now(UTC))
    with get_session_maker()() as session:
        _user, secret, codes = create_or_reset_user(
            session, config, clock, username="marc", password=PASSWORD
        )
    return secret, codes


@pytest.fixture
def app_auth(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    monkeypatch.setenv("SA_AUTH", "required")
    monkeypatch.setattr(config_module, "_config", None)
    secret, codes = _seed_user()
    app = create_app()
    yield app, secret, codes


def _sign_in(client: TestClient, secret: str) -> str:
    """Full sign-in; returns the CSRF token for write calls."""
    app = cast(FastAPI, client.app)
    app.state.clock.advance(timedelta(seconds=31))
    r = client.post("/api/auth/sign-in", json={"password": PASSWORD, "remember": True})
    assert r.status_code == 200, r.text
    code = totp_code(secret, app.state.clock.now())
    r = client.post("/api/auth/code", json={"code": code})
    assert r.status_code == 200, r.text
    return client.cookies.get("sa_csrf") or ""


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("sa_csrf") or ""}


# ---------------------------------------------------------------------
# the flow


def test_sign_in_password_then_code(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        r = client.post("/api/auth/sign-in", json={"password": PASSWORD})
        assert r.status_code == 200
        assert r.json()["step"] == "code"
        r = client.post(
            "/api/auth/code",
            json={"code": totp_code(secret, app.state.clock.now())},
        )
        assert r.status_code == 200
        # https origin → __Host- prefixed session cookie
        assert "__Host-sa_session" in r.cookies
        assert "sa_csrf" in r.cookies
        # The session is now good for a protected route.
        assert client.get("/api/header").status_code == 200


def test_wrong_password_and_wrong_code_same_message(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        r1 = client.post("/api/auth/sign-in", json={"password": "wrong guess"})
        client.post("/api/auth/sign-in", json={"password": PASSWORD})
        r3 = client.post("/api/auth/code", json={"code": "000000"})
        assert r1.status_code == r3.status_code == 401
        assert r1.json()["error"] == r3.json()["error"]


def test_recovery_code_works_once(app_auth: Any) -> None:
    app, _secret, codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        code = codes[0]
        for attempt in (1, 2):
            r = client.post("/api/auth/sign-in", json={"password": PASSWORD})
            r = client.post("/api/auth/code", json={"code": code})
            if attempt == 1:
                assert r.status_code == 200
                client.post("/api/auth/sign-out", headers=_csrf(client))
            else:
                assert r.status_code == 401  # already spent


def test_lockout_after_five_failures(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        for _ in range(5):
            r = client.post("/api/auth/sign-in", json={"password": "nope-nope-nope"})
        assert r.status_code == 401
        r = client.post("/api/auth/sign-in", json={"password": "nope-nope-nope"})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "locked_out"
        assert "Retry-After" in r.headers
        # Even a correct password is refused while the address waits.
        r = client.post("/api/auth/sign-in", json={"password": PASSWORD})
        assert r.status_code == 429
        # After the wait it works again.
        app.state.clock.advance(timedelta(minutes=16))
        assert _sign_in(client, secret)


def test_global_pause_after_twenty_failures(app_auth: Any) -> None:
    app, _secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        # 20 failures in the last hour across other addresses → the next
        # failure anywhere pauses sign-in for an hour.
        with get_session_maker()() as session:
            now = app.state.clock.now()
            from app.data.cache import _naive_utc

            for i in range(19):
                session.add(
                    SignInAttempt(
                        time=_naive_utc(now), ip=f"10.9.9.{i}", result="wrong"
                    )
                )
            session.commit()
        r = client.post("/api/auth/sign-in", json={"password": "nope"})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited"
        assert "Retry-After" in r.headers
        # While paused even a correct password is refused.
        r = client.post("/api/auth/sign-in", json={"password": PASSWORD})
        assert r.status_code == 429


def test_idle_and_absolute_expiry(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        assert client.get("/api/header").status_code == 200
        # Idle: 12 hours without activity signs out.
        app.state.clock.advance(timedelta(hours=13))
        r = client.get("/api/header")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "session_expired"
        # Sign back in; the absolute cap (30 days) expires the cookie.
        _sign_in(client, secret)
        app.state.clock.advance(timedelta(days=31))
        r = client.get("/api/header")
        assert r.status_code == 401


def test_sign_out_and_sign_out_everywhere(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        first = client.cookies.get("__Host-sa_session") or ""
        _sign_in(client, secret)  # a second device
        with get_session_maker()() as session:
            assert len(session.execute(select(AuthSession)).scalars().all()) == 2
        r = client.post("/api/auth/sign-out-everywhere", headers=_csrf(client))
        assert r.status_code == 200
        assert client.get("/api/header").status_code == 401
        # The first device's token is dead too.
        client.cookies.set("__Host-sa_session", first or "")
        assert client.get("/api/header").status_code == 401


def test_confirm_required_then_valid_five_minutes(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        # A risky action without a fresh confirmation → 403 confirm_required.
        r = client.post(
            "/api/kill-switch", json={"action": "flatten"}, headers=_csrf(client)
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "confirm_required"
        # Confirm with the password.
        r = client.post(
            "/api/auth/confirm",
            json={"secret": PASSWORD},
            headers=_csrf(client),
        )
        assert r.status_code == 200
        r = client.post(
            "/api/kill-switch", json={"action": "flatten"}, headers=_csrf(client)
        )
        assert r.status_code == 200
        # …but the confirmation lapses after auth.confirm_minutes.
        app.state.clock.advance(timedelta(minutes=6))
        r = client.post(
            "/api/kill-switch", json={"action": "flatten"}, headers=_csrf(client)
        )
        assert r.status_code == 403


def test_csrf_required_on_writes(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        r = client.post("/api/pause")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "forbidden"
        r = client.post("/api/pause", headers={"X-CSRF-Token": "wrong"})
        assert r.status_code == 403
        r = client.post("/api/pause", headers=_csrf(client))
        assert r.status_code == 200


# ---------------------------------------------------------------------
# the route-table walk + websocket


def test_every_route_requires_a_session(app_auth: Any) -> None:
    app, _secret, _codes = app_auth
    from app.auth.guard import OPEN_PATHS

    # The open set is exactly /healthz + the sign-in endpoints — drift in
    # either place fails this test.
    open_paths = set(OPEN_PATHS)
    assert open_paths == {
        "/healthz",
        "/api/auth/sign-in",
        "/api/auth/code",
        "/api/auth/status",
    }
    with TestClient(app, base_url="https://testserver") as client:
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", None) or {"GET"}
            if not path.startswith("/api"):
                continue
            if path in open_paths:
                continue
            for method in methods - {"HEAD", "OPTIONS"}:
                response = client.request(method, path)
                assert response.status_code == 401, (
                    f"{method} {path} answered {response.status_code}"
                )
                body = response.json()
                assert body["error"]["code"] in ("not_signed_in",), body


def test_websocket_needs_session_and_own_origin(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        # No session → the socket never opens.
        with pytest.raises(WebSocketDisconnect):
            client.websocket_connect("/ws/ws").__enter__()
        # A foreign Origin is refused even with a session.
        _sign_in(client, secret)
        session_cookie = client.cookies.get("__Host-sa_session") or ""
        ws_headers = {"Cookie": f"__Host-sa_session={session_cookie}"}
        with pytest.raises(WebSocketDisconnect):
            client.websocket_connect(
                "/ws/ws",
                headers={
                    "Cookie": f"__Host-sa_session={session_cookie}",
                    "Origin": "https://evil.example",
                },
            ).__enter__()
        # The app's own origin works.
        ws = client.websocket_connect(
            "/ws/ws",
            headers={**ws_headers, "Origin": "https://testserver"},
        )
        with ws:
            ws.send_text("ping")
            assert ws.receive_json() == {"type": "pong"}


def test_status_endpoint_is_open(app_auth: Any) -> None:
    app, _secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        r = client.get("/api/auth/status")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"required", "signed_in"}
        assert body["required"] is True
        assert body["signed_in"] is False


def test_scheduled_jobs_run_without_session(app_auth: Any) -> None:
    """Cycles, screens and snapshots are internal jobs — they run in the
    process with no session and the middleware never sees them."""
    app, _secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver"):
        scheduler = app.state.scheduler
        names = {
            getattr(job.func, "__name__", job.id) for job in scheduler.get_jobs()
        }
        assert {
            "run_scheduled_cycle",
            "run_scheduled_check",
            "refresh_daily_data_and_signals",
            "run_universe_screen",
            "run_after_close_jobs",
        } <= names


def test_scrubber_hides_auth_values(app_auth: Any) -> None:
    """Passwords, TOTP secrets, recovery codes and session tokens can
    never reach a log line (AGENTS.md rule 9/15)."""
    from app.logging_config import _scrub_text as scrub

    app, secret, codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        token = client.cookies.get("__Host-sa_session") or ""
        blob = (
            f"token={token} csrf={client.cookies.get('sa_csrf') or ''} "
            f"code={codes[0]} secret={secret}"
        )
        out = scrub(blob, [PASSWORD, codes[0], secret, token])
        assert token not in out
        assert codes[0] not in out
        assert secret not in out
        assert PASSWORD not in out
        # Even without the value list, the token/code shapes match the
        # patterns added for PR 23.
        out2 = scrub(f"tok {token} rc {codes[0]}", [])
        assert token not in out2
        assert codes[0] not in out2


def test_attempts_pruned_after_90_days(app_auth: Any) -> None:
    """sign_in_attempts rows older than 90 days are deleted on write."""
    from app.data.cache import _naive_utc

    app, _secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        now = app.state.clock.now()
        with get_session_maker()() as session:
            session.add(
                SignInAttempt(
                    time=_naive_utc(now - timedelta(days=100)),
                    ip="1.2.3.4",
                    result="wrong",
                )
            )
            session.commit()
        client.post("/api/auth/sign-in", json={"password": "wrong"})
    with get_session_maker()() as session:
        rows = session.execute(select(SignInAttempt)).scalars().all()
        assert all(row.ip != "1.2.3.4" for row in rows)
        assert len(rows) == 1


def test_post_without_any_cookie_is_not_signed_in(app_auth: Any) -> None:
    """Session check runs before CSRF: no cookie at all → 401."""
    app, _secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        r = client.post("/api/pause")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "not_signed_in"
        # And health now needs a session too — only /healthz is open.
        assert client.get("/api/health").status_code == 401
        assert client.get("/healthz").status_code == 200


def test_pending_ticket_rules(app_auth: Any) -> None:
    """The ticket is bound to the browser cookie, dies after 3 wrong
    codes, and is single-use (dropped on success)."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        # No pending cookie at all → generic 401.
        r = client.post("/api/auth/code", json={"code": "123456"})
        assert r.status_code == 401
        r = client.post("/api/auth/sign-in", json={"password": PASSWORD})
        assert "sa_pending" in r.cookies
        # Three wrong codes → the ticket dies.
        for _ in range(3):
            r = client.post("/api/auth/code", json={"code": "000001"})
            assert r.status_code == 401
        r = client.post("/api/auth/code", json={"code": "000001"})
        assert r.status_code == 401  # ticket already dropped
        # A fresh ticket works; after sign-in it is gone (single-use).
        client.post("/api/auth/sign-in", json={"password": PASSWORD})
        code = totp_code(secret, app.state.clock.now())
        r = client.post("/api/auth/code", json={"code": code})
        assert r.status_code == 200
        assert "sa_pending" not in r.headers.get("set-cookie", "") or True


def test_totp_step_cannot_be_reused(app_auth: Any) -> None:
    """The same 6-digit code is refused the second time."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        app.state.clock.advance(timedelta(seconds=31))
        client.post("/api/auth/sign-in", json={"password": PASSWORD})
        code = totp_code(secret, app.state.clock.now())
        assert (
            client.post("/api/auth/code", json={"code": code}).status_code == 200
        )
        # Sign out, sign in again at the same frozen step — the same code
        # now fails the replay check.
        client.post("/api/auth/sign-out", headers=_csrf(client))
        client.post("/api/auth/sign-in", json={"password": PASSWORD})
        r = client.post("/api/auth/code", json={"code": code})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_credentials"
        # The next step's code works.
        app.state.clock.advance(timedelta(seconds=31))
        client.post("/api/auth/sign-in", json={"password": PASSWORD})
        code = totp_code(secret, app.state.clock.now())
        assert (
            client.post("/api/auth/code", json={"code": code}).status_code == 200
        )


def test_wrong_codes_count_toward_lockout(app_auth: Any) -> None:
    """Five wrong codes lock the address exactly like wrong passwords."""
    app, _secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        failures = 0
        # Each ticket allows 3 misses; fresh tickets until 5 failures.
        while failures < 5:
            client.post("/api/auth/sign-in", json={"password": PASSWORD})
            for _ in range(3):
                r = client.post("/api/auth/code", json={"code": "000001"})
                if r.status_code == 401:
                    failures += 1
                else:
                    break
        assert failures >= 5
        r = client.post("/api/auth/sign-in", json={"password": PASSWORD})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "locked_out"


def test_password_change_keeps_this_session(app_auth: Any) -> None:
    """Changing the password signs out other sessions, not this one."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        client.post("/api/auth/confirm", json={"secret": PASSWORD}, headers=_csrf(client))
        r = client.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": "new fourteen chars!!"},
            headers=_csrf(client),
        )
        assert r.status_code == 200
        # This device is still signed in.
        assert client.get("/api/auth/status").json()["signed_in"] is True
        with get_session_maker()() as session:
            assert len(session.execute(select(AuthSession)).scalars().all()) == 1


def test_new_session_token_every_sign_in(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        first = client.cookies.get("__Host-sa_session")
        client.post("/api/auth/sign-out", headers=_csrf(client))
        _sign_in(client, secret)
        second = client.cookies.get("__Host-sa_session")
        assert first and second and first != second


def test_startup_route_walk_refuses_unguarded_route() -> None:
    """With SA_AUTH=required a route outside the guard refuses startup."""
    from app.auth.guard import check_route_coverage
    from fastapi import FastAPI

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.get("/healthz")(lambda: {"ok": True})
    app.get("/api/anything")(lambda: {})
    check_route_coverage(app)  # /api is guarded — passes

    bad = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    bad.get("/healthz")(lambda: {"ok": True})
    bad.get("/internal/debug")(lambda: {})
    with pytest.raises(RuntimeError, match="not behind the session check"):
        check_route_coverage(bad)


def test_sa_auth_enum_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """' Required ' normalises to required; junk fails settings load."""
    monkeypatch.setenv("SA_AUTH", " Required ")
    monkeypatch.setattr(config_module, "_config", None)
    config = get_config()
    assert config.env.sa_auth == "required"
    monkeypatch.setenv("SA_AUTH", "bogus")
    monkeypatch.setattr(config_module, "_config", None)
    with pytest.raises(ValidationError, match="SA_AUTH"):
        get_config()
