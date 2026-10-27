"""PR 24: the Compose file carries the spec's container hardening, and
the auth layer takes "is this HTTPS" from X-Forwarded-Proto only where
the spec allows (trusted proxy or an https SA_PUBLIC_URL)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]

import app.config as config_module  # noqa: E402


def _compose() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def test_app_container_is_hardened() -> None:
    app = _compose()["services"]["app"]
    assert "ports" not in app  # internal network only, never published
    assert app["read_only"] is True
    assert app["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert app["restart"] == "unless-stopped"
    assert app["stop_grace_period"] == "90s"
    assert "mem_limit" in app
    assert any(t.startswith("/tmp:") for t in app["tmpfs"])
    env = app["environment"]
    assert env["SA_HOST"] == "0.0.0.0"
    assert env["SA_AUTH"] == "required"
    assert env["SA_INSTANCE_ID"] == "sa-vps-1"
    assert env["SA_TRUSTED_PROXY"] == "172.30.4.10"


def test_healthcheck_uses_localhost_healthz() -> None:
    hc = _compose()["services"]["app"]["healthcheck"]
    cmd = " ".join(str(p) for p in hc["test"])
    assert "localhost" in cmd and "/healthz" in cmd
    assert "SA_PORT" in cmd


def test_caddy_publishes_80_443_and_headers() -> None:
    caddy = _compose()["services"]["caddy"]
    ports = [str(p) for p in caddy["ports"]]
    assert any(p.endswith(":80") for p in ports)
    assert any(p.endswith(":443") for p in ports)
    text = (ROOT / "deploy" / "Caddyfile").read_text()
    for header in (
        "Strict-Transport-Security",
        "Permissions-Policy",
        "?Content-Security-Policy",
        "?X-Frame-Options",
        "?X-Content-Type-Options",
        "?Referrer-Policy",
        "?Cross-Origin-Opener-Policy",
    ):
        assert header in text
    assert "header_up Host {host}" in text
    assert "X-Forwarded-Proto" in text


def test_local_compose_uses_internal_ca_domain() -> None:
    local = yaml.safe_load((ROOT / "docker-compose.local.yml").read_text())
    assert local["services"]["caddy"]["environment"]["SA_DOMAIN"] == "localhost"
    assert (
        local["services"]["app"]["environment"]["SA_PUBLIC_URL"]
        == "https://localhost"
    )


# ---------------------------------------------------------------------
# X-Forwarded-Proto trust rules


PASSWORD = "a fourteen char password"


def _seed_user() -> str:
    from datetime import UTC, datetime

    from app.auth.create_user import create_or_reset_user
    from app.config import get_config
    from app.db.base import get_session_maker
    from app.market.clock import FakeClock

    with get_session_maker()() as session:
        _user, secret, _codes = create_or_reset_user(
            session,
            get_config(),
            FakeClock(datetime.now(UTC)),
            username="marc",
            password=PASSWORD,
        )
    return secret


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    def make(*, trusted: str = "", public_url: str = "http://localhost:8000"):
        monkeypatch.setenv("SA_AUTH", "required")
        monkeypatch.setenv("SA_TRUSTED_PROXY", trusted)
        monkeypatch.setenv("SA_PUBLIC_URL", public_url)
        monkeypatch.setattr(config_module, "_config", None)
        secret = _seed_user()
        from app.api.main import create_app

        return create_app(), secret

    return make


def _sign_in_cookies(client: TestClient, app: Any, secret: str, **headers: str) -> Any:
    """Sign in through the proxy path; returns the /code response.

    The Secure sa_pending cookie is never echoed over the plain-http
    test base_url (exactly like a browser), so it is replayed by hand —
    the set-cookie attributes on the /code response are what is tested.
    """
    from http.cookies import SimpleCookie

    from app.auth.passwords import totp_code

    app.state.clock.advance(timedelta(seconds=31))
    r1 = client.post(
        "/api/auth/sign-in", json={"password": PASSWORD}, headers=headers
    )
    assert r1.status_code == 200, r1.text
    jar = SimpleCookie()
    for line in r1.headers.get_list("set-cookie"):
        jar.load(line)
    pending = jar["sa_pending"].value
    r = client.post(
        "/api/auth/code",
        json={"code": totp_code(secret, app.state.clock.now())},
        headers={**headers, "Cookie": f"sa_pending={pending}"},
    )
    assert r.status_code == 200, r.text
    return r


def test_xfp_https_from_trusted_proxy_sets_secure_host_cookie(auth_env: Any) -> None:
    # TestClient's peer is "testclient" — trust it, send plain http + XFP.
    app, secret = auth_env(trusted="testclient")
    with TestClient(app, base_url="http://testserver") as client:
        r = _sign_in_cookies(
            client, app, secret, **{"X-Forwarded-Proto": "https"}
        )
        cookies = r.headers.get_list("set-cookie")
        host_cookie = [c for c in cookies if c.startswith("__Host-sa_session=")]
        assert host_cookie, cookies
        assert "Secure" in host_cookie[0]
        assert "HttpOnly" in host_cookie[0]
        assert "SameSite=strict" in host_cookie[0]
        assert "Path=/" in host_cookie[0]


def test_xfp_https_from_untrusted_peer_is_ignored(auth_env: Any) -> None:
    app, secret = auth_env(trusted="172.30.4.10")  # not our peer
    with TestClient(app, base_url="http://testserver") as client:
        r = _sign_in_cookies(
            client, app, secret, **{"X-Forwarded-Proto": "https"}
        )
        cookies = r.headers.get_list("set-cookie")
        assert not [c for c in cookies if c.startswith("__Host-sa_session=")]
        session = [c for c in cookies if c.startswith("sa_session=")]
        assert session and "Secure" not in session[0]


def test_https_public_url_means_secure_without_xfp(auth_env: Any) -> None:
    app, secret = auth_env(public_url="https://stock.example.nl")
    with TestClient(app, base_url="http://testserver") as client:
        r = _sign_in_cookies(client, app, secret)
        cookies = r.headers.get_list("set-cookie")
        host_cookie = [c for c in cookies if c.startswith("__Host-sa_session=")]
        assert host_cookie and "Secure" in host_cookie[0]
