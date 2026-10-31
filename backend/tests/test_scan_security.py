"""Scan fixes of 31 October — security items.

- The four auth routes that used to call ``request.json()`` untyped now
  answer 422 for a non-object body or a wrong field type — never 500.
- Wrong secrets in "Confirm it is you" and Change password count per
  session; five in 15 minutes sign the session out and warn.
- Secrets shorter than 8 characters are refused at save; a stored one
  shows no characters.
- User-set URLs refuse multicast literal IPs too.
"""

from __future__ import annotations

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
from app.secrets_service import masked_hint
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

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
def app_auth(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SA_AUTH", "required")
    monkeypatch.setattr(config_module, "_config", None)
    secret, codes = _seed_user()
    app = create_app()
    yield app, secret, codes


def _sign_in(client: TestClient, secret: str) -> str:
    app = cast(FastAPI, client.app)
    app.state.clock.advance(timedelta(seconds=31))
    r = client.post("/api/auth/sign-in", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/auth/code", json={"code": totp_code(secret, app.state.clock.now())}
    )
    assert r.status_code == 200, r.text
    return client.cookies.get("sa_csrf") or ""


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("sa_csrf") or ""}


# ----------------------------------------------------------------------
# typed bodies on the four auth routes


AUTH_BODY_ROUTES = (
    "/api/auth/sign-in",
    "/api/auth/code",
    "/api/auth/confirm",
    "/api/auth/password",
)


def test_auth_routes_non_object_body_is_422(app_auth: Any) -> None:
    """A JSON body that is not an object is a client mistake — 422 with
    the shared error shape, never a 500."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        for path in AUTH_BODY_ROUTES:
            for body in ([1, 2], "text", 42):
                r = client.post(path, json=body, headers=_csrf(client))
                assert r.status_code == 422, f"{path} {body!r} -> {r.status_code}"
                assert r.json()["error"]["code"] == "invalid", r.text
            assert r.status_code < 500


def test_auth_routes_wrong_field_type_is_422(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        cases = (
            ("/api/auth/sign-in", {"password": [1, 2]}),
            ("/api/auth/sign-in", {"password": "x", "remember": [1]}),
            ("/api/auth/code", {"code": {"a": 1}}),
            ("/api/auth/confirm", {"secret": [1]}),
            ("/api/auth/password", {"current_password": 1, "new_password": "x" * 14}),
        )
        for path, body in cases:
            r = client.post(path, json=body, headers=_csrf(client))
            assert r.status_code == 422, f"{path} {body} -> {r.status_code}"
            assert r.json()["error"]["code"] == "invalid", r.text


# ----------------------------------------------------------------------
# Confirm / Change password attempt limits


def _wrong_attempts(ip: str) -> int:
    with get_session_maker()() as session:
        return len(
            session.execute(
                select(SignInAttempt).where(
                    SignInAttempt.ip == ip, SignInAttempt.result == "wrong"
                )
            )
            .scalars()
            .all()
        )


def test_confirm_failures_revoke_session_after_five(app_auth: Any) -> None:
    """Five wrong confirm secrets in 15 minutes sign the session out,
    record the attempts like sign-in failures and send the warning."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        for _ in range(4):
            r = client.post(
                "/api/auth/confirm", json={"secret": "nope"}, headers=_csrf(client)
            )
            assert r.status_code == 401
        # Still signed in at four.
        assert client.get("/api/header").status_code == 200
        r = client.post(
            "/api/auth/confirm", json={"secret": "nope"}, headers=_csrf(client)
        )
        assert r.status_code == 401
        # The fifth failure ends the session.
        r = client.get("/api/header")
        assert r.status_code == 401
        with get_session_maker()() as session:
            assert not session.execute(select(AuthSession)).scalars().all()
        # Recorded like sign-in failures.
        assert _wrong_attempts("testclient") >= 5


def test_change_password_failures_revoke_session(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        client.post(
            "/api/auth/confirm", json={"secret": PASSWORD}, headers=_csrf(client)
        )
        for _ in range(5):
            r = client.post(
                "/api/auth/password",
                json={
                    "current_password": "wrong-current",
                    "new_password": "new fourteen chars!!",
                },
                headers=_csrf(client),
            )
            assert r.status_code == 401
        assert client.get("/api/header").status_code == 401


def test_confirm_failures_outside_window_do_not_count(app_auth: Any) -> None:
    """Failures older than the 15-minute window fall off the count."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        for _ in range(4):
            client.post(
                "/api/auth/confirm", json={"secret": "nope"}, headers=_csrf(client)
            )
        app.state.clock.advance(timedelta(minutes=16))
        # Old failures expired: one more wrong secret is not a revoke.
        r = client.post(
            "/api/auth/confirm", json={"secret": "nope"}, headers=_csrf(client)
        )
        assert r.status_code == 401
        assert client.get("/api/header").status_code == 200


def test_confirm_failures_send_the_warning(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    notifications: list[tuple[str, str]] = []
    with TestClient(app, base_url="https://testserver") as client:
        auth = app.state.auth
        original = auth._notifier.notify
        auth._notifier.notify = lambda key, line, link=None: notifications.append(  # type: ignore[method-assign]
            (key, line)
        ) or original(key, line, link=link)
        _sign_in(client, secret)
        for _ in range(5):
            client.post(
                "/api/auth/confirm", json={"secret": "nope"}, headers=_csrf(client)
            )
    assert any(key == "sign_in_lockout" for key, _line in notifications)


def test_session_found_across_split_cookie_headers(app_auth: Any) -> None:
    """Multiple Cookie headers are joined with "; " — a session split
    across two headers must still authenticate."""
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        session_cookie = client.cookies.get("__Host-sa_session") or ""
        csrf = client.cookies.get("sa_csrf") or ""
        r = client.post(
            "/api/pause",
            headers=[
                ("cookie", "sa_csrf=" + csrf),
                ("cookie", "__Host-sa_session=" + session_cookie),
                ("x-csrf-token", csrf),
            ],
        )
        assert r.status_code == 200, r.text


# ----------------------------------------------------------------------
# secrets: minimum length


def test_save_secret_shorter_than_8_refused() -> None:
    from app.secrets_service import SecretError, SecretsStore

    config = get_config()
    store = SecretsStore(config, FakeClock(datetime.now(UTC)))
    with pytest.raises(SecretError, match="Too short to be a real key"):
        store.save("openai", "sk-1234")


def test_masked_hint_shows_no_chars_below_8() -> None:
    assert masked_hint("openai", "sk-1234") == "••••"
    assert masked_hint("finnhub", "ab") == "••••"
    assert masked_hint("openai", "sk-realkey12345") == "sk-••••2345"


def test_keys_route_refuses_short_secret(app_auth: Any) -> None:
    app, secret, _codes = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, secret)
        client.post(
            "/api/auth/confirm", json={"secret": PASSWORD}, headers=_csrf(client)
        )
        r = client.post(
            "/api/keys/openai", json={"value": "short1"}, headers=_csrf(client)
        )
        assert r.status_code == 422
        assert "Too short" in r.text


# ----------------------------------------------------------------------
# urls.py: multicast literal IP


def test_literal_multicast_ip_rejected() -> None:
    from app.ops.urls import UrlRejected, validate_public_https_url

    with pytest.raises(UrlRejected, match="private address"):
        validate_public_https_url("https://239.1.2.3/hook")
    with pytest.raises(UrlRejected, match="private address"):
        validate_public_https_url("https://[ff02::1]/hook")
