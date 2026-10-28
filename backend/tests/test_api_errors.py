"""The one JSON error shape for every /api answer (PR 25).

docs/SPEC.md → Error pages and API errors: ``{"error": {"code",
"message", "error_id"?, "fields"?}}`` — never a traceback, SQL, a file
path, a secret or HTML, and the message is always the registry sentence
for the code, never the exception's own text.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

import pytest
from app.api.main import create_app
from app.errors import MESSAGES, ErrorCode
from fastapi import FastAPI
from fastapi.testclient import TestClient

# A fake key in the shape the scrubber masks (sk-…): a response or a log
# line may never carry it, inside a traceback or an exception message.
FAKE_KEY = "sk-FAKEtestKEY0123456789"


def _shape(response: Any) -> dict[str, Any]:
    """Assert the shared shape and return the error object."""
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    error: dict[str, Any] = body["error"]
    assert set(error) <= {"code", "message", "error_id", "fields"}
    assert error["code"] in ErrorCode._value2member_map_
    assert error["message"] == MESSAGES[ErrorCode(error["code"])]
    # Never a traceback, path or HTML in the body.
    text = response.text
    assert "Traceback" not in text and "<!doctype" not in text.lower()
    return error


@pytest.fixture
def boom_client() -> Generator[TestClient, None, None]:
    """A client whose app has a test-only failing route, and which keeps
    the 500 response instead of re-raising the handled exception."""
    from fastapi.routing import APIRoute

    app = create_app()

    def boom() -> None:
        raise RuntimeError(f"broker rejected key {FAKE_KEY} at /etc/secret/path")

    # The SPA mount at "/" matches everything, so the test route must go
    # first in match order.
    app.router.routes.insert(0, APIRoute("/api/_test_boom", boom))

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_unknown_api_path_is_404_json(client: TestClient) -> None:
    for method in ("get", "post"):
        resp = getattr(client, method)("/api/no-such-route")
        assert resp.status_code == 404
        assert _shape(resp)["code"] == "not_found"


def test_non_api_get_serves_the_frontend(client: TestClient) -> None:
    resp = client.get("/decisions/123")
    # The SPA mount serves every non-/api GET where dist exists (the
    # test image builds no frontend); a served page is HTML, never the
    # JSON error shape.
    if resp.status_code == 200:
        assert "<html" in resp.text.lower()


def test_route_404_keeps_the_shape(client: TestClient) -> None:
    resp = client.get("/api/decisions/99999999")
    assert resp.status_code == 404
    error = _shape(resp)
    assert error["code"] == "not_found"
    assert "99999999" not in resp.text  # no echoed request data


def test_validation_error_is_422_with_fields(client: TestClient) -> None:
    # A path parameter that can't parse → RequestValidationError →
    # 422 invalid with the field named.
    resp = client.get("/api/decisions/not-a-number")
    assert resp.status_code == 422
    error = _shape(resp)
    assert error["code"] == "invalid"
    assert error["fields"]


def test_endpoint_422_keeps_the_shape(client: TestClient) -> None:
    # An HTTPException(422) from an endpoint: fixed message, no detail echo.
    resp = client.get("/api/decisions", params={"cycle": "not-a-time"})
    assert resp.status_code == 422
    assert _shape(resp)["code"] == "invalid"


def test_500_has_error_id_and_no_secret(boom_client: TestClient) -> None:
    resp = boom_client.get("/api/_test_boom")
    assert resp.status_code == 500
    error = _shape(resp)
    assert error["code"] == "internal"
    assert error["error_id"].startswith("e-")
    assert FAKE_KEY not in resp.text
    assert "/etc/secret/path" not in resp.text


def test_500_log_line_scrubs_the_secret(
    boom_client: TestClient, capfd: pytest.CaptureFixture[str]
) -> None:
    resp = boom_client.get("/api/_test_boom")
    error_id = resp.json()["error"]["error_id"]
    # The same error_id is on the ERROR log line; the fake key inside the
    # exception text and traceback is scrubbed before the file (rule 9).
    log_path = (
        Path(os.environ["SA_DATA_DIR"]) / "logs" / "stock-agent.log"
    )
    text = ""
    for _ in range(50):
        if log_path.exists() and error_id in log_path.read_text():
            text = log_path.read_text()
            break
        time.sleep(0.1)
    line = next(
        json.loads(raw) for raw in text.splitlines() if error_id in raw
    )
    assert line["error_id"] == error_id
    assert "exception" in line
    assert FAKE_KEY not in text
    # The console handler scrubs the same way — the key is nowhere on
    # stderr either.
    captured = capfd.readouterr()
    assert error_id not in captured.err or FAKE_KEY not in captured.err
    assert FAKE_KEY not in captured.err


def test_malformed_json_body_is_400_not_500(client: TestClient) -> None:
    """A body the app can't parse is a client mistake: 400 bad_request,
    no error_id, and logged at WARNING — an unreviewed ERROR would hold
    Readiness at "not yet" for nothing (PR 25 review)."""
    log_path = (
        Path(os.environ["SA_DATA_DIR"]) / "logs" / "stock-agent.log"
    )
    resp = client.post("/api/auth/sign-in", content="not json")
    assert resp.status_code == 400
    error = _shape(resp)
    assert error["code"] == "bad_request"
    assert "error_id" not in error
    text = ""
    for _ in range(50):
        if log_path.exists() and "unreadable JSON body" in log_path.read_text():
            text = log_path.read_text()
            break
        time.sleep(0.1)
    lines = [
        json.loads(raw) for raw in text.splitlines() if raw.strip()
    ]
    warnings = [
        line for line in lines if "unreadable JSON body" in line["message"]
    ]
    assert warnings and warnings[-1]["level"] == "WARNING"
    assert not any(
        line["level"] == "ERROR" and "sign-in" in line["message"]
        for line in lines
    )


def test_shutting_down_and_starting_up(client: TestClient) -> None:
    app = cast(FastAPI, client.app)
    app.state.shutdown.begin()
    try:
        resp = client.post("/api/pause")
        assert resp.status_code == 503
        assert _shape(resp)["code"] == "shutting_down"
        # Reads keep working during the graceful shutdown (spec).
        assert client.get("/api/health").status_code == 200
    finally:
        app.state.shutdown.shutting_down.clear()
    ready = app.state.ready
    app.state.ready = False
    try:
        resp = client.get("/api/header")
        assert resp.status_code == 503
        assert _shape(resp)["code"] == "starting_up"
        # /healthz stays outside the maintenance answer.
        assert client.get("/healthz").status_code == 200
    finally:
        app.state.ready = ready


def test_registry_is_complete() -> None:
    """Every code has exactly one fixed sentence; PR 28's codes exist."""
    assert set(MESSAGES) == set(ErrorCode)
    for code in (
        "bad_host",
        "origin_refused",
        "too_large",
        "unsupported_media_type",
        "password_required",
    ):
        assert code in MESSAGES


def test_static_pages_are_self_contained(repo_root: Path) -> None:
    """deploy/errors/*.html: no external requests, no version, no host —
    anyone can see these without signing in (spec → Served by Caddy)."""
    import re

    errors_dir = repo_root / "deploy" / "errors"
    pages = sorted(errors_dir.glob("*.html"))
    assert {p.name for p in pages} == {"502.html", "503.html"}
    for page in pages:
        text = page.read_text()
        assert "Stock Trading Agent" in text
        # No external URLs or asset references at all.
        assert not re.search(r'(?:src|href)\s*=\s*["\'](?:https?:)?//', text)
        assert "http://" not in text and "https://" not in text
        assert "fonts.googleapis" not in text
        # No version, host or command leakage.
        for leaked in ("0.2.0", "localhost", "deploy.sh", "127.0.0.1"):
            assert leaked not in text, f"{leaked} in {page.name}"
        # The positions note is on every static page (spec).
        assert "stops and targets at Alpaca" in text
