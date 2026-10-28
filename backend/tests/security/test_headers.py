"""PR 28 step 2 — the app's own security headers.

Every response carries CSP, nosniff, X-Frame-Options, Referrer-Policy
and COOP; ``/api`` also gets ``Cache-Control: no-store``. The CSP is the
spec's strict value — the through-Caddy check that each header appears
exactly once with the app's value lives in deploy/test-compose.sh.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

_EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
    "cross-origin-opener-policy": "same-origin",
}


def _check(response: Any, *, api: bool) -> None:
    for name, value in _EXPECTED.items():
        assert response.headers[name] == value, name
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "object-src 'none'" in csp
    assert "form-action 'self'" in csp
    if api:
        assert "no-store" in response.headers["cache-control"]


def test_headers_on_api(client: TestClient) -> None:
    _check(client.get("/api/header"), api=True)


def test_headers_on_gate_refusal(client: TestClient) -> None:
    """Gate refusals carry the headers too — the middleware wraps the gate."""
    _check(client.get("/api/header", headers={"Host": "evil.example"}), api=True)


def test_headers_on_healthz(client: TestClient) -> None:
    _check(client.get("/healthz"), api=False)


def test_headers_on_html(client: TestClient) -> None:
    """The built frontend (or its 404 stand-in) gets the headers."""
    response = client.get("/")
    _check(response, api=False)
    # no-store is /api-only: a page must not be forced to revalidate.
    assert "no-store" not in response.headers.get("cache-control", "")
