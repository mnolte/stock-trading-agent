"""Startup guard and trusted-proxy address resolution (PR 23, step 1)."""

from __future__ import annotations

import app.config as config_module
import pytest
from app.api.main import create_app
from app.auth.guard import check_auth_guard, client_ip
from app.config import get_config
from fastapi.testclient import TestClient
from starlette.requests import Request


def _config(monkeypatch: pytest.MonkeyPatch, **env: str):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(config_module, "_config", None)
    return get_config()


def test_refuses_auth_off_on_public_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(monkeypatch, SA_AUTH="off", SA_HOST="0.0.0.0")
    with pytest.raises(RuntimeError, match="SA_AUTH=off"):
        check_auth_guard(config)


def test_refuses_unknown_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(monkeypatch, SA_AUTH="maybe", SA_HOST="127.0.0.1")
    with pytest.raises(RuntimeError, match="unknown SA_AUTH"):
        check_auth_guard(config)


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
