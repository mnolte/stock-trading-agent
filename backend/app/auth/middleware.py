"""Session enforcement for every API route and the WebSocket (PR 23).

One middleware covers the whole surface so no route added later can
ship unprotected: anything under ``/api`` or ``/ws`` needs a valid
session cookie when ``SA_AUTH=required``, except the open endpoints —
``/healthz``, ``/api/health``, and the sign-in calls
(``/api/auth/sign-in``, ``/api/auth/code``, ``/api/auth/status``).

On top of the session check:
- unsafe methods with a session must echo the CSRF cookie in
  ``X-CSRF-Token`` (double-submit; ``SameSite=Strict`` already blocks
  cross-site sends, this is the second line per spec);
- failures answer in the PR 25 JSON shape with codes
  ``not_signed_in`` / ``session_expired`` / ``forbidden``, and the
  socket closes with 1008 instead of accepting.

With ``SA_AUTH=off`` the middleware is a pure passthrough — the local
app behaves exactly as before.
"""

from __future__ import annotations

import json
import logging
from http.cookies import SimpleCookie

from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.guard import OPEN_PATHS, auth_required
from app.auth.service import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from app.config import get_config

logger = logging.getLogger(__name__)

# The only open endpoints when SA_AUTH=required; shared with the startup
# route-table walk so the two can never disagree (review: /api/health is
# NOT open — Caddy and uptime checks use /healthz).
_OPEN_PATHS = OPEN_PATHS

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _cookies(scope: Scope) -> dict[str, str]:
    # Multiple Cookie headers are legal; they join with "; " (RFC 6265).
    raw = b"; ".join(
        value for name, value in scope.get("headers", []) if name == b"cookie"
    )
    jar = SimpleCookie()
    jar.load(raw.decode("latin-1"))
    return {key: morsel.value for key, morsel in jar.items()}


def _header(scope: Scope, name: bytes) -> str:
    headers = scope.get("headers") or []
    for key, value in headers:
        if key == name:
            return str(value.decode("latin-1"))
    return ""


def _error_body(code: str) -> bytes:
    # The shared shape; the sentence comes from the code registry (PR 25).
    from app.errors import error_body

    return json.dumps(error_body(code)).encode()


async def _send_error(send: Send, status: int, code: str) -> None:
    body = _error_body(code)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send(
        {"type": "http.response.body", "body": body, "more_body": False}
    )


class AuthMiddleware:
    """Pure ASGI: guards ``/api`` + ``/ws`` when sign-in is required."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        config = get_config()
        if not auth_required(config):
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        guarded = path.startswith("/api/") or path == "/api" or path.startswith("/ws")
        if not guarded or path in _OPEN_PATHS:
            await self.app(scope, receive, send)
            return

        asgi_app = scope.get("app")
        auth = getattr(getattr(asgi_app, "state", None), "auth", None)
        # app.state is populated by the lifespan; during the brief window
        # before it, treat everything as not signed in.
        cookies = _cookies(scope)
        # __Host-sa_session over HTTPS, sa_session on loopback.
        token = cookies.get("__Host-sa_session") or cookies.get(SESSION_COOKIE)
        row, reason = (
            auth.authenticate(token)
            if auth is not None
            else (None, "not_signed_in")
        )
        if row is None:
            if scope["type"] == "websocket":
                await _close_ws(send, reason)
                return
            await _send_error(send, 401, reason)
            return

        if scope["type"] == "websocket":
            # Host and Origin are the request gate's job — it runs in
            # front of this middleware on every scope, WebSocket included,
            # and its _split_host handles IPv6 brackets.
            scope["state"] = {**scope.get("state", {}), "session": row}
            await self.app(scope, receive, send)
            return

        # CSRF double-submit on writes.
        if scope.get("method", "GET") not in _SAFE_METHODS:
            presented = _header(scope, CSRF_HEADER.encode())
            expected = cookies.get(CSRF_COOKIE, "")
            if not expected or presented != expected:
                await _send_error(send, 403, "forbidden")
                return
        scope["state"] = {**scope.get("state", {}), "session": row}
        await self.app(scope, receive, send)


async def _close_ws(send: Send, reason: str) -> None:
    """Deny the handshake before accept: 403 on HTTP, then close."""
    await send(
        {
            "type": "websocket.close",
            "code": 1008,
            "reason": reason,
        }
    )
