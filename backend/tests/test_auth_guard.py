"""Startup guard and trusted-proxy address resolution (PR 23, step 1)."""

from __future__ import annotations

import app.config as config_module
import pytest
from app.api.main import create_app
from app.auth.guard import check_auth_guard, client_ip, request_is_https
from app.config import AppConfig, get_config
from fastapi.testclient import TestClient
from starlette.requests import Request


def _config(monkeypatch: pytest.MonkeyPatch, **env: str) -> AppConfig:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(config_module, "_config", None)
    return get_config()


def test_refuses_auth_off_on_public_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(monkeypatch, SA_AUTH="off", SA_HOST="0.0.0.0")
    with pytest.raises(RuntimeError, match="SA_AUTH=off"):
        check_auth_guard(config)


def test_refuses_unknown_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    # SA_AUTH is parsed once as an enum in EnvSettings — anything else
    # fails closed at settings load, before any startup work.
    with pytest.raises(Exception, match="SA_AUTH"):
        _config(monkeypatch, SA_AUTH="maybe", SA_HOST="127.0.0.1")


def test_auth_mode_normalises_case_and_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(monkeypatch, SA_AUTH=" Required ", SA_HOST="0.0.0.0")
    assert config.env.sa_auth == "required"
    check_auth_guard(config)  # required on any bind is fine


def test_auth_off_on_loopback_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    for host in ("127.0.0.1", "::1"):
        config = _config(monkeypatch, SA_AUTH="off", SA_HOST=host)
        check_auth_guard(config)


def test_auth_required_on_public_bind_is_fine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(monkeypatch, SA_AUTH="required", SA_HOST="0.0.0.0")
    check_auth_guard(config)


def test_boot_refuses_auth_off_on_public_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole app refuses to come up, not just a log line."""
    monkeypatch.setenv("SA_AUTH", "off")
    monkeypatch.setenv("SA_HOST", "0.0.0.0")
    monkeypatch.setattr(config_module, "_config", None)
    app = create_app()
    with pytest.raises(RuntimeError, match="SA_AUTH=off"), TestClient(app):
        pass


def test_normal_start_auth_unset_on_loopback(client: TestClient) -> None:
    """The default local setup (loopback, SA_AUTH unset) behaves as today."""
    response = client.get("/api/health")
    assert response.status_code == 200


def _request(peer: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "client": (peer, 5000),
            "headers": headers or [],
        }
    )


def test_forwarded_for_only_from_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(monkeypatch, SA_TRUSTED_PROXY="10.0.0.2")
    forwarded = [(b"x-forwarded-for", b"84.104.1.2, 10.0.0.2")]
    # From the proxy the left-most X-Forwarded-For entry wins.
    assert (
        client_ip(_request("10.0.0.2", forwarded), config) == "84.104.1.2"
    )
    # From anyone else the header is spoofable and ignored.
    assert client_ip(_request("9.9.9.9", forwarded), config) == "9.9.9.9"
    # No header at all: the peer is the address either way.
    assert client_ip(_request("10.0.0.2"), config) == "10.0.0.2"


def test_request_is_https_decides_secure_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Secure/__Host- cookie decision: direct https, an https
    SA_PUBLIC_URL, or X-Forwarded-Proto from a trusted proxy only."""
    config = _config(monkeypatch, SA_TRUSTED_PROXY="10.0.0.2")
    https = [(b"x-forwarded-proto", b"https")]
    plain = [(b"x-forwarded-proto", b"http")]
    # XFP counts only through the configured proxy.
    assert request_is_https(_request("10.0.0.2", https), config) is True
    assert request_is_https(_request("9.9.9.9", https), config) is False
    assert request_is_https(_request("10.0.0.2", plain), config) is False
    # An https public URL means the browser is on https by definition.
    config = _config(monkeypatch, SA_PUBLIC_URL="https://stocks.example.com")
    assert request_is_https(_request("9.9.9.9", plain), config) is True
    # A direct https request always counts.
    req = _request("9.9.9.9")
    req.scope["scheme"] = "https"
    assert request_is_https(req, config) is True
