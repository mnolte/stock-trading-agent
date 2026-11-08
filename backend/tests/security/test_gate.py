"""PR 28 step 1 — the request gate (docs/SPEC.md → Security hardening).

The gate runs in front of sign-in, so every check also holds with
``SA_AUTH=off`` — that is the mode under test in the shared ``client``
fixture (simulated mode, which also counts as a dev server so the Vite
proxy's forwarded Host/Origin pass).
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator, MutableMapping
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

EVIL = "http://evil.example"


def _foreign(headers: dict[str, str] | None = None) -> dict[str, str]:
    return {"Origin": EVIL, "Sec-Fetch-Site": "cross-site", **(headers or {})}


# ---------------------------------------------------------------------
# writes from another site are refused; the app's own origin passes
# ---------------------------------------------------------------------


def test_cross_origin_text_plain_post_refused_and_inert(
    client: TestClient,
) -> None:
    """The audit's main hole: a web page's simple text/plain POST must do
    nothing — 403, and the kill switch stays off."""
    r = client.post(
        "/api/kill-switch",
        content='{"action": "entries"}',
        headers={**_foreign(), "Content-Type": "text/plain"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "origin_refused"
    assert client.get("/api/header").json()["kill_switch"] is False


def test_bodiless_cross_origin_post_refused(client: TestClient) -> None:
    r = client.post("/api/pause", headers=_foreign())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "origin_refused"


def test_fetch_metadata_alone_refuses(client: TestClient) -> None:
    """Sec-Fetch-Site same-site without an Origin header is still a
    cross-site write (only same-origin/none pass)."""
    r = client.post("/api/pause", headers={"Sec-Fetch-Site": "same-site"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "origin_refused"


def test_fetch_metadata_same_origin_passes(client: TestClient) -> None:
    r = client.post(
        "/api/kill-switch",
        json={"action": "entries"},
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert r.status_code == 200


def test_own_origin_write_passes(client: TestClient) -> None:
    r = client.post("/api/pause", headers={"Origin": "http://testserver"})
    assert r.status_code == 200
    assert r.json() == {"paused": True}


@pytest.mark.parametrize(
    "origin", ["http://localhost", "http://127.0.0.1", "http://[::1]"]
)
def test_portless_loopback_origin_refused(client: TestClient, origin: str) -> None:
    """Port-less loopback Origins mean :80/:443 — a page served by another
    local process must not pass as the app's own origin."""
    r = client.post("/api/pause", headers={"Origin": origin})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "origin_refused"


def test_loopback_origin_with_explicit_port_passes(client: TestClient) -> None:
    # Simulated mode counts any loopback port as a dev server; the port is
    # what makes the Origin attributable to a specific local server.
    r = client.post("/api/pause", headers={"Origin": "http://localhost:8000"})
    assert r.status_code == 200


def test_no_headers_write_passes(client: TestClient) -> None:
    """curl-style requests carry no Origin or fetch metadata — allowed."""
    r = client.post("/api/pause")
    assert r.status_code == 200


# ---------------------------------------------------------------------
# Host allowlist (DNS rebinding)
# ---------------------------------------------------------------------


def test_foreign_host_refused(client: TestClient) -> None:
    r = client.get("/api/header", headers={"Host": "evil.example"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_host"


def test_foreign_host_on_gui_refused(client: TestClient) -> None:
    """The static app isn't served to a rebound name either."""
    r = client.get("/", headers={"Host": "evil.example"})
    assert r.status_code == 400


@pytest.mark.parametrize(
    "host",
    ["localhost:8000", "127.0.0.1:8000", "[::1]:8000", "testserver"],
)
def test_allowed_hosts(client: TestClient, host: str) -> None:
    r = client.get("/api/health", headers={"Host": host})
    assert r.status_code == 200


def test_healthz_passes_the_gate(client: TestClient) -> None:
    """The compose healthcheck hits localhost:SA_PORT — must keep working."""
    r = client.get("/healthz", headers={"Host": "localhost:8000"})
    assert r.status_code == 200


# ---------------------------------------------------------------------
# content type and body size
# ---------------------------------------------------------------------


def test_form_encoded_write_refused(client: TestClient) -> None:
    r = client.post("/api/pause", data={"on": "true"})
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"


@pytest.mark.parametrize("encoding", ["Chunked", "gzip, chunked", "gzip,Chunked"])
def test_chunked_transfer_encoding_counts_as_a_body(encoding: str) -> None:
    """A TE token list with no Content-Type/Length still has a body: the
    gate must apply the JSON-only rule instead of letting it through."""
    from app.api.gate import RequestGateMiddleware

    called = False

    async def app(
        scope: MutableMapping[str, Any], receive: Any, send: Any
    ) -> None:
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    messages: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/pause",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 123),
        "headers": [
            (b"host", b"testserver"),
            (b"transfer-encoding", encoding.encode()),
        ],
    }
    asyncio.run(RequestGateMiddleware(app)(scope, receive, send))

    assert called is False
    assert messages[0]["status"] == 415


def test_json_write_accepted(client: TestClient) -> None:
    r = client.post("/api/kill-switch", json={"action": "entries"})
    assert r.status_code == 200


def test_oversized_body_refused(client: TestClient) -> None:
    """max_body_kb is 1024 KiB — the cap is enforced while reading."""
    body = b'{"pad": "' + b"x" * (1024 * 1024) + b'"}'
    r = client.post(
        "/api/logs/review",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "too_large"


def test_import_gets_the_larger_upload_cap(client: TestClient) -> None:
    """security.max_upload_kb (5 MiB) applies only to the CSV import —
    the same body on a normal route hits max_body_kb (1 MiB) → 413."""
    body = b'{"pad": "' + b"x" * (2 * 1024 * 1024) + b'"}'
    r = client.post(
        "/api/universe/import",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code != 413
    r = client.post(
        "/api/logs/review",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "too_large"


# ---------------------------------------------------------------------
# websocket upgrade goes through the same checks
# ---------------------------------------------------------------------


def test_websocket_foreign_origin_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        client.websocket_connect(
            "/ws/ws", headers={"Origin": EVIL}
        ).__enter__()


def test_websocket_foreign_host_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        client.websocket_connect(
            "/ws/ws", headers={"Host": "evil.example"}
        ).__enter__()


def test_websocket_cross_site_fetch_metadata_refused(client: TestClient) -> None:
    """The upgrade counts as a write — foreign Sec-Fetch-Site refuses."""
    with pytest.raises(WebSocketDisconnect):
        client.websocket_connect(
            "/ws/ws", headers={"Sec-Fetch-Site": "cross-site"}
        ).__enter__()


def test_websocket_own_origin_works(client: TestClient) -> None:
    with client.websocket_connect(
        "/ws/ws", headers={"Origin": "http://testserver"}
    ) as ws:
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong"}


# ---------------------------------------------------------------------
# the gate sits in front of sign-in
# ---------------------------------------------------------------------


@pytest.fixture
def app_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[Any, str], None, None]:
    """SA_AUTH=required app with the single user seeded (like PR 23's)."""
    from tests.helpers import auth_app

    app, secret, _codes = auth_app(monkeypatch)
    yield app, secret


def test_gate_runs_before_sign_in(app_auth: Any) -> None:
    """With SA_AUTH=required a foreign Origin is 403 origin_refused even
    with a valid session — the gate never reaches the session check."""
    from app.auth.passwords import totp_code

    app, secret = app_auth
    with TestClient(app, base_url="https://testserver") as client:
        r = client.post(
            "/api/auth/sign-in", json={"password": "a fourteen char password"}
        )
        assert r.status_code == 200
        r = client.post(
            "/api/auth/code", json={"code": totp_code(secret, app.state.clock.now())}
        )
        assert r.status_code == 200
        r = client.post("/api/pause", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "origin_refused"
        # The same write from the app's own origin reaches the session
        # check and succeeds.
        r = client.post(
            "/api/pause",
            headers={
                "Origin": "https://testserver",
                "X-CSRF-Token": client.cookies.get("sa_csrf") or "",
            },
        )
        assert r.status_code == 200
