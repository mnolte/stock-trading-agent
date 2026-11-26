"""PR 23 steps 3-4: the sign-in flow and the protected route table.

Boots the app with ``SA_AUTH=required`` against a seeded single user.
The simulated clock is a FakeClock, so idle and absolute expiry are
driven by advancing it.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
from typing import Any

import app.config as config_module
import pytest
from app.auth.passwords import totp_code
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import AuthSession, SignInAttempt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from tests.helpers import (
    PASSWORD,
    auth_app,
    csrf_headers,
    public_resolver,
    sign_in,
)


@pytest.fixture
def app_auth(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    yield auth_app(monkeypatch)


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


def test_https_accepts_only_the_host_session_cookie(app_auth: Any) -> None:
    """Over HTTPS a planted/plain ``sa_session`` must not authenticate —
    only ``__Host-sa_session`` does (the __Host- guarantee is not diluted)."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        token = client.cookies.get("__Host-sa_session") or ""
        assert token
        client.cookies.clear()
        client.cookies.set("sa_session", token)
        assert client.get("/api/header").status_code == 401
        assert client.get("/api/auth/status").json()["signed_in"] is False


def test_http_accepts_only_the_plain_session_cookie(app_auth: Any) -> None:
    """The mirror of the HTTPS rule: on plain HTTP only ``sa_session``
    counts — the ``__Host-`` name is refused there too."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="http://testserver") as client:
        sign_in(client, secret, remember=True)
        token = client.cookies.get("sa_session") or ""
        assert token
        client.cookies.clear()
        client.cookies.set("__Host-sa_session", token)
        assert client.get("/api/header").status_code == 401
        client.cookies.clear()
        client.cookies.set("sa_session", token)
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
                client.post("/api/auth/sign-out", headers=csrf_headers(client))
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
        assert sign_in(client, secret, remember=True)


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
        # The pause lives in the database: a fresh AuthService (a restart)
        # still refuses, and it lifts once the hour is up.
        from app.auth.service import AuthError, AuthService

        restarted = AuthService(get_config(), app.state.clock)
        with pytest.raises(AuthError) as err:
            restarted.start_sign_in("nope", False, "10.1.2.3", "pytest")
        assert err.value.status == 429
        app.state.clock.advance(timedelta(minutes=61))
        assert restarted.start_sign_in(PASSWORD, False, "10.1.2.3", "pytest")


def test_idle_and_absolute_expiry(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        assert client.get("/api/header").status_code == 200
        # Idle: 12 hours without activity signs out.
        app.state.clock.advance(timedelta(hours=13))
        r = client.get("/api/header")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "session_expired"
        # Sign back in; the absolute cap (30 days) expires the cookie.
        sign_in(client, secret, remember=True)
        app.state.clock.advance(timedelta(days=31))
        r = client.get("/api/header")
        assert r.status_code == 401


def test_sign_out_and_sign_out_everywhere(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        first = client.cookies.get("__Host-sa_session") or ""
        sign_in(client, secret, remember=True)  # a second device
        with get_session_maker()() as session:
            assert len(session.execute(select(AuthSession)).scalars().all()) == 2
        r = client.post("/api/auth/sign-out-everywhere", headers=csrf_headers(client))
        assert r.status_code == 200
        assert client.get("/api/header").status_code == 401
        # The first device's token is dead too.
        client.cookies.set("__Host-sa_session", first or "")
        assert client.get("/api/header").status_code == 401


def test_sessions_list_and_sign_out_by_id(app_auth: Any) -> None:
    """Settings → Access devices: the list marks the current session and
    sign-out by id kills the other device's token."""
    app, secret, _codes = app_auth
    # A second cookie jar on the same booted app — a second TestClient
    # inside 'with' would boot the lifespan again and hit the run lock.
    with TestClient(app, base_url="https://testserver") as client:
        other_client = TestClient(app, base_url="https://testserver")
        sign_in(client, secret, remember=True)
        sign_in(other_client, secret, remember=True)  # a second device
        data = client.get("/api/auth/sessions").json()
        devices = data["devices"]
        assert len(devices) == 2
        assert [d["current"] for d in devices].count(True) == 1
        other = next(d for d in devices if not d["current"])
        r = client.post(
            f"/api/auth/sessions/{other['id']}/sign-out", headers=csrf_headers(client)
        )
        assert r.status_code == 200
        assert other_client.get("/api/header").status_code == 401
        assert client.get("/api/header").status_code == 200


def test_sign_out_others_keeps_current(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        other_client = TestClient(app, base_url="https://testserver")
        sign_in(client, secret, remember=True)
        sign_in(other_client, secret, remember=True)  # a second device
        r = client.post("/api/auth/sign-out-others", headers=csrf_headers(client))
        assert r.status_code == 200
        assert r.json()["signed_out"] == 1
        assert client.get("/api/header").status_code == 200
        assert other_client.get("/api/header").status_code == 401


def test_totp_reset_requires_confirm_and_replaces_secret(app_auth: Any) -> None:
    """POST /api/auth/totp resets the second factor — behind 'Confirm it
    is you'; the old secret stops working immediately."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        r = client.post("/api/auth/totp", headers=csrf_headers(client))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "confirm_required"
        client.post(
            "/api/auth/confirm", json={"secret": PASSWORD}, headers=csrf_headers(client)
        )
        r = client.post("/api/auth/totp", headers=csrf_headers(client))
        assert r.status_code == 200
        new_secret = r.json()["secret"]
        assert new_secret != secret
        # The old code no longer signs in; the new one does — past the
        # already-spent TOTP step (codes at or below last_totp_step are
        # refused as replays).
        client.post("/api/auth/sign-out", headers=csrf_headers(client))
        app.state.clock.advance(timedelta(seconds=31))
        client.post("/api/auth/sign-in", json={"password": PASSWORD})
        old_code = totp_code(secret, app.state.clock.now())
        assert client.post("/api/auth/code", json={"code": old_code}).status_code == 401
        new_code = totp_code(new_secret, app.state.clock.now())
        assert client.post("/api/auth/code", json={"code": new_code}).status_code == 200


def test_recovery_codes_regenerate_requires_confirm(app_auth: Any) -> None:
    """New recovery codes replace the old set — behind 'Confirm it is
    you'; a regenerated code signs in, an old one is dead."""
    app, _secret, codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, _secret, remember=True)
        r = client.post("/api/auth/recovery-codes", headers=csrf_headers(client))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "confirm_required"
        client.post(
            "/api/auth/confirm", json={"secret": PASSWORD}, headers=csrf_headers(client)
        )
        r = client.post("/api/auth/recovery-codes", headers=csrf_headers(client))
        assert r.status_code == 200
        new_codes = r.json()["codes"]
        assert new_codes and new_codes != codes
        client.post("/api/auth/sign-out", headers=csrf_headers(client))
        client.post("/api/auth/sign-in", json={"password": PASSWORD})
        assert (
            client.post("/api/auth/code", json={"code": codes[0]}).status_code == 401
        )
        assert (
            client.post("/api/auth/code", json={"code": new_codes[0]}).status_code
            == 200
        )


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/paper-reset", {}),
        ("/api/runs", {}),
        ("/api/keys/openai", {"value": "sk-fake-0123456789abcdef"}),
        ("/api/keys/openai/remove", {}),
    ],
)
def test_risky_writes_require_confirm(
    app_auth: Any, path: str, body: dict[str, object]
) -> None:
    """Every write guarded by 'Confirm it is you' answers 403
    confirm_required without a fresh confirmation."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        r = client.post(path, json=body, headers=csrf_headers(client))
        assert r.status_code == 403, f"{path} -> {r.status_code}"
        assert r.json()["error"]["code"] == "confirm_required"


def test_first_smtp_save_requires_confirm(app_auth: Any) -> None:
    """Configuring SMTP for the first time redirects every future alert —
    it needs 'Confirm it is you' just like changing a saved destination."""
    app, secret, _codes = app_auth
    body = {
        "host": "smtp.test",
        "port": 587,
        "username": "agent@test",
        "from_addr": "agent@test",
        "to_addr": "marc@test",
        "password": "smtp-secret-123",
    }
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        r = client.post("/api/notifications/smtp", json=body, headers=csrf_headers(client))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "confirm_required"
        client.post(
            "/api/auth/confirm", json={"secret": PASSWORD}, headers=csrf_headers(client)
        )
        # The SMTP host must resolve public (item 085) — fake the lookup.
        app.state.notification_service._resolver = public_resolver
        r = client.post("/api/notifications/smtp", json=body, headers=csrf_headers(client))
        assert r.status_code == 200, r.text


def test_settings_risk_loosening_requires_confirm(app_auth: Any) -> None:
    """Loosening a numeric risk limit needs 'Confirm it is you';
    tightening never asks (design, Settings → Access)."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        for body, expect in (
            ({"settings": {"risk": {"daily_loss_limit_pct": 3}}, "reason": "t"}, 403),
            ({"settings": {"risk": {"daily_loss_limit_pct": 1}}, "reason": "t"}, 200),
        ):
            r = client.post("/api/settings", json=body, headers=csrf_headers(client))
            assert r.status_code == expect, f"{body} -> {r.status_code} {r.text}"
            if expect == 403:
                assert r.json()["error"]["code"] == "confirm_required"


def test_confirm_required_then_valid_five_minutes(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        # A risky action without a fresh confirmation → 403 confirm_required.
        r = client.post(
            "/api/kill-switch", json={"action": "flatten"}, headers=csrf_headers(client)
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "confirm_required"
        # Confirm with the password.
        r = client.post(
            "/api/auth/confirm",
            json={"secret": PASSWORD},
            headers=csrf_headers(client),
        )
        assert r.status_code == 200
        r = client.post(
            "/api/kill-switch", json={"action": "flatten"}, headers=csrf_headers(client)
        )
        assert r.status_code == 200
        # …but the confirmation lapses after auth.confirm_minutes.
        app.state.clock.advance(timedelta(minutes=6))
        r = client.post(
            "/api/kill-switch", json={"action": "flatten"}, headers=csrf_headers(client)
        )
        assert r.status_code == 403


def test_csrf_required_on_writes(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        r = client.post("/api/pause")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "forbidden"
        r = client.post("/api/pause", headers={"X-CSRF-Token": "wrong"})
        assert r.status_code == 403
        r = client.post("/api/pause", headers=csrf_headers(client))
        assert r.status_code == 200


def test_csrf_check_uses_constant_time_compare(
    app_auth: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for #156: the double-submit check must go through
    hmac.compare_digest — a plain != passes every behavioural test."""
    import hmac

    app, secret, _codes = app_auth
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        calls.clear()  # sign-in itself compares TOTP/recovery hashes
        token = client.cookies.get("sa_csrf") or ""
        r = client.post("/api/pause", headers=csrf_headers(client))
        assert r.status_code == 200
    assert (token.encode(), token.encode()) in calls


# ---------------------------------------------------------------------
# the route-table walk + websocket


def test_every_route_requires_a_session(app_auth: Any) -> None:
    app, _secret, _codes = app_auth
    from app.auth.guard import OPEN_PATHS, iter_flat_routes

    # The open set is exactly /healthz + the sign-in endpoints — drift in
    # either place fails this test.
    open_paths = set(OPEN_PATHS)
    assert open_paths == {
        "/healthz",
        "/api/auth/sign-in",
        "/api/auth/code",
        "/api/auth/status",
    }
    checked = 0
    with TestClient(app, base_url="https://testserver") as client:
        for path, methods, _route in iter_flat_routes(app):
            if not path.startswith("/api"):
                continue
            if path in open_paths:
                continue
            for method in (methods or {"GET"}) - {"HEAD", "OPTIONS"}:
                response = client.request(method, path)
                checked += 1
                assert response.status_code == 401, (
                    f"{method} {path} answered {response.status_code}"
                )
                body = response.json()
                assert body["error"]["code"] in ("not_signed_in",), body
    # The walk must actually reach the API surface — lazy included
    # routers without a .path made it a no-op once before.
    assert checked > 30


def test_websocket_needs_session_and_own_origin(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        # No session → the socket never opens.
        with pytest.raises(WebSocketDisconnect):
            client.websocket_connect("/ws/ws").__enter__()
        # A foreign Origin is refused even with a session.
        sign_in(client, secret, remember=True)
        token = client.cookies.get("__Host-sa_session") or ""
        # websocket_connect always dials ws:// — a plain scheme, so the
        # plain sa_session name is the one that counts here (the __Host-
        # name is for https/wss scopes, covered by the http tests).
        with pytest.raises(WebSocketDisconnect):
            client.websocket_connect(
                "/ws/ws",
                headers={
                    "Cookie": f"__Host-sa_session={token}",
                    "Origin": "https://testserver",
                },
            ).__enter__()
        with pytest.raises(WebSocketDisconnect):
            client.websocket_connect(
                "/ws/ws",
                headers={
                    "Cookie": f"sa_session={token}",
                    "Origin": "https://evil.example",
                },
            ).__enter__()
        # The app's own origin works.
        ws = client.websocket_connect(
            "/ws/ws",
            headers={
                "Cookie": f"sa_session={token}",
                "Origin": "https://testserver",
            },
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
        assert set(body) == {
            "required",
            "signed_in",
            "paused_until",
            "known_device",
        }
        assert body["required"] is True
        assert body["signed_in"] is False
        assert body["paused_until"] is None
        assert body["known_device"] is False


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
        sign_in(client, secret, remember=True)
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
        # The single-use ticket is dropped: the response clears the
        # pending cookie and the jar no longer holds it.
        set_cookies = r.headers.get_list("set-cookie")
        assert any(
            "sa_pending" in h and ("Max-Age=0" in h or 'sa_pending=""' in h)
            for h in set_cookies
        ), set_cookies
        assert client.cookies.get("sa_pending") is None


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
        client.post("/api/auth/sign-out", headers=csrf_headers(client))
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
        sign_in(client, secret, remember=True)
        client.post("/api/auth/confirm", json={"secret": PASSWORD}, headers=csrf_headers(client))
        r = client.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": "new fourteen chars!!"},
            headers=csrf_headers(client),
        )
        assert r.status_code == 200
        # This device is still signed in.
        assert client.get("/api/auth/status").json()["signed_in"] is True
        with get_session_maker()() as session:
            assert len(session.execute(select(AuthSession)).scalars().all()) == 1


def test_new_session_token_every_sign_in(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client, secret, remember=True)
        first = client.cookies.get("__Host-sa_session")
        client.post("/api/auth/sign-out", headers=csrf_headers(client))
        sign_in(client, secret, remember=True)
        second = client.cookies.get("__Host-sa_session")
        assert first and second and first != second


def test_startup_route_walk_refuses_unguarded_route() -> None:
    """With SA_AUTH=required a route outside the guard refuses startup."""
    from app.auth.guard import check_route_coverage

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.get("/healthz")(lambda: {"ok": True})
    app.get("/api/anything")(lambda: {})
    check_route_coverage(app)  # /api is guarded — passes

    bad = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    bad.get("/healthz")(lambda: {"ok": True})
    bad.get("/internal/debug")(lambda: {})
    with pytest.raises(RuntimeError, match="not behind the session check"):
        check_route_coverage(bad)


def test_startup_route_walk_reaches_lazy_included_routers() -> None:
    """Routers added via include_router stay lazy in this FastAPI
    version — the coverage walk must expand them or it sees nothing."""
    from app.auth.guard import check_route_coverage
    from fastapi import APIRouter

    ok = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    ok.get("/healthz")(lambda: {"ok": True})
    router = APIRouter()
    router.get("/anything")(lambda: {})
    ok.include_router(router, prefix="/api")
    check_route_coverage(ok)  # /api router is guarded — passes

    bad = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    bad.get("/healthz")(lambda: {"ok": True})
    internal = APIRouter()
    internal.get("/debug")(lambda: {})
    internal.include_router(APIRouter())
    bad.include_router(internal, prefix="/internal")
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
