"""The startup sign-in guard (docs/SPEC.md, VPS deployment → Sign-in).

``SA_AUTH`` is ``off`` or ``required`` (default ``off``). Sign-in off is
only safe on loopback: with ``SA_AUTH=off`` on any other bind address the
app refuses to start (AGENTS.md hard rule 10). ``SA_TRUSTED_PROXY`` lists
the reverse-proxy addresses whose ``X-Forwarded-For`` header is believed —
with Caddy in front that is the proxy's own address, never the internet's.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.websockets import WebSocket

if TYPE_CHECKING:
    from app.config import AppConfig

AUTH_OFF = "off"
AUTH_REQUIRED = "required"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


def auth_required(config: AppConfig) -> bool:
    return config.env.sa_auth == AUTH_REQUIRED


def check_auth_guard(config: AppConfig) -> None:
    """Refuse to start with sign-in off on a non-loopback bind address.

    Runs first in the lifespan, before the database or any client is
    touched. With the default local setup (loopback, ``SA_AUTH`` unset)
    nothing changes.
    """
    mode = config.env.sa_auth
    host = config.env.sa_host.strip()
    if mode == AUTH_OFF and host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            "Refusing to start: SA_AUTH=off but SA_HOST is "
            f"{host or '(empty)'!r} — sign-in is required on any address "
            "other than 127.0.0.1 or ::1 (hard rule 10). "
            "Set SA_AUTH=required or bind to loopback."
        )


def _is_trusted_proxy(address: str, config: AppConfig) -> bool:
    trusted = {
        part.strip()
        for part in config.env.sa_trusted_proxy.split(",")
        if part.strip()
    }
    return address in trusted


def client_ip(request: Request | WebSocket, config: AppConfig) -> str:
    """The address rate limits and sessions are attributed to.

    ``X-Forwarded-For`` is honoured only when the direct peer is a
    configured trusted proxy (Caddy on the VPS); anything else is spoofable
    and the peer address is used.
    """
    peer = request.client.host if request.client is not None else ""
    if _is_trusted_proxy(peer, config):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded.strip():
            # Left-most entry is the client as the proxy saw it.
            return forwarded.split(",")[0].strip()
    return peer


def host_is_loopback(host: str) -> bool:
    """True for literal loopback addresses (127.0.0.0/8, ::1)."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# The exact set of endpoints open without a session when SA_AUTH=required:
# /healthz for Caddy and uptime probes, plus the sign-in calls themselves.
# Everything else — /api/health included — needs a session.
OPEN_PATHS = frozenset(
    {
        "/healthz",
        "/api/auth/sign-in",
        "/api/auth/code",
        "/api/auth/status",
    }
)

# Prefixes the session middleware guards.
_GUARDED_PREFIXES = ("/api", "/ws")


def check_route_coverage(app: object) -> None:
    """Refuse to start when a route exists that no session check covers.

    Runs in the lifespan when ``SA_AUTH=required``: walks the FastAPI
    route table and fails closed if any route is neither in OPEN_PATHS
    nor under a guarded prefix — a route added outside /api would
    otherwise ship reachable without sign-in.
    """
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "")
        if not path or path in OPEN_PATHS:
            continue
        # Mounts (static files) and docs/redoc never carry API data.
        if getattr(route, "methods", None) is None and getattr(
            route, "routes", None
        ) is not None:
            continue
        if not path.startswith(_GUARDED_PREFIXES):
            raise RuntimeError(
                f"Refusing to start with SA_AUTH=required: route {path} "
                "is not behind the session check. Move it under /api, add "
                "it to the guarded prefixes, or list it in OPEN_PATHS."
            )
