"""PR 28 step 5 — bounded inputs never 500.

Every list parameter is clamped or answered 422: ``limit`` (1–200),
``window`` (1–100), typed ids like ``strategy:<id>``, the FTS search
quoted as a phrase, exports capped at ``security.export_max_rows``.
"""

from __future__ import annotations

import inspect
import re
import typing
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.auth.guard import iter_flat_routes
from fastapi.params import Depends, Param, Query
from fastapi.testclient import TestClient

_VALUES = ("-1", "0", "1000000000000", "abc")
# Path placeholders resolve to these values; anything else defaults to "1".
_PATH_VALUES = {"slug": "live"}
# Sent to every route on top of its declared parameters — unknown query
# params must be ignored, never crash.
_JUNK_PARAM = "bogus"


def _get_routes(app: Any) -> list[tuple[str, Any]]:
    """All GET /api (path, endpoint) pairs — expands lazy included
    routers via the shared walker so no route is silently skipped."""
    routes = []
    for path, methods, route in iter_flat_routes(app):
        if "GET" in (methods or set()) and path.startswith("/api"):
            endpoint = getattr(route, "endpoint", None)
            routes.append((path, endpoint))
    return routes


_SCALARS = (str, int, float, bool, Decimal, date, datetime)


def _scalar_annotation(annotation: Any) -> bool:
    if annotation in _SCALARS:
        return True
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    return bool(args) and all(a in _SCALARS for a in args)


def _declared_params(endpoint: Any) -> list[str]:
    """Query parameters the endpoint actually declares — skips injected
    dependencies (``Depends``), ``Request``/``Session`` objects and
    header/path/body params."""
    if endpoint is None:
        return []
    try:
        sig = inspect.signature(endpoint)
    except (TypeError, ValueError):
        return []
    out = []
    for name, p in sig.parameters.items():
        default = p.default
        if isinstance(default, Depends):
            continue
        if isinstance(default, Param):
            if isinstance(default, Query):
                out.append(name)
            continue
        if default is inspect.Parameter.empty:
            if _scalar_annotation(p.annotation):
                out.append(name)
        elif isinstance(default, _SCALARS) or default is None:
            out.append(name)
    return out


def test_every_get_route_survives_bad_params(client: TestClient) -> None:
    """Send -1, 0, 10^12 and abc to every declared query parameter of
    every GET route, plus one undeclared junk param: nothing may answer
    500 (spec → Bounded inputs)."""
    routes = _get_routes(client.app)
    assert len(routes) > 30
    failures: list[str] = []
    for path, endpoint in routes:
        url = re.sub(
            r"{(\w+)}", lambda m: _PATH_VALUES.get(m.group(1), "1"), path
        )
        assert "{" not in url, f"unresolved path param in {path}"
        params = set(_declared_params(endpoint)) | {_JUNK_PARAM}
        for param in sorted(params):
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
