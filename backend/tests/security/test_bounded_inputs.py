"""PR 28 step 5 — bounded inputs never 500.

Every list parameter is clamped or answered 422: ``limit`` (1–200),
``window`` (1–100), typed ids like ``strategy:<id>``, the FTS search
quoted as a phrase, exports capped at ``security.export_max_rows``.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

_PARAMS = ("limit", "window", "after_id", "cursor", "line_id")
_VALUES = ("-1", "0", "1000000000000", "abc")


def _get_routes(app: Any) -> list[str]:
    """All GET /api paths — this FastAPI version keeps included routers
    lazy (``_IncludedRouter``), so expand them via ``original_router``."""
    paths = []
    stack = [(route, "") for route in app.routes]
    while stack:
        route, prefix = stack.pop()
        original = getattr(route, "original_router", None)
        if original is not None:
            ctx = getattr(route, "include_context", None)
            stack += [
                (child, prefix + getattr(ctx, "prefix", ""))
                for child in original.routes
            ]
            continue
        methods = getattr(route, "methods", None) or set()
        path = prefix + getattr(route, "path", "")
        if "GET" in methods and path.startswith("/api"):
            paths.append(path)
    return paths


def test_every_get_route_survives_bad_params(client: TestClient) -> None:
    """Send -1, 0, 10^12 and abc to every plausible query parameter of
    every GET route: nothing may answer 500 (spec → Bounded inputs)."""
    routes = _get_routes(client.app)
    assert len(routes) > 30
    failures: list[str] = []
    for path in routes:
        # Path parameters get an int-friendly value so the route resolves.
        url = path
        for placeholder in (
            "{decision_id}", "{review_id}", "{run_id}", "{line_id}",
            "{call_id}", "{strategy_id}", "{symbol}",
        ):
            url = url.replace(placeholder, "1")
        url = url.replace("{slug}", "live")
        for param in _PARAMS:
            for value in _VALUES:
                r = client.get(url, params={param: value})
                if r.status_code >= 500:
                    failures.append(f"{path}?{param}={value} -> {r.status_code}")
    assert not failures, "; ".join(failures)


def test_limit_clamped(client: TestClient) -> None:
    assert client.get("/api/logs", params={"limit": "1000000000000"}).status_code != 500
    assert client.get("/api/logs", params={"limit": "abc"}).status_code == 422
    assert client.get("/api/notifications", params={"limit": "abc"}).status_code == 422
    assert client.get("/api/notifications", params={"limit": "10^12"}).status_code != 500


def test_strategy_typed_id_is_422(client: TestClient) -> None:
    r = client.post(
        "/api/backtests/estimate",
        json={
            "start": "2026-09-01",
            "end": "2026-09-10",
            "strategies": ["strategy:abc"],
        },
    )
    assert r.status_code == 422


def test_log_search_is_a_quoted_phrase(client: TestClient) -> None:
    """FTS operators inside the search text are data, not syntax."""
    for search in ('"unclosed', "NEAR(", "OR NOT"):
        r = client.get("/api/logs", params={"search": search})
        assert r.status_code != 500, search
