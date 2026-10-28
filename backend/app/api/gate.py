"""The request gate (docs/SPEC.md → Security hardening → Request gate).

One middleware in front of every ``/api`` route and the WebSocket — and
in front of sign-in, so it also protects the local ``SA_AUTH=off`` app:
until sign-in exists, any web page Marc visits could otherwise send write
requests to the app on 127.0.0.1 (CORS does not stop simple
``text/plain`` POSTs).

- **Host** is checked on every request: loopback names with ``SA_PORT``
  (any port under ``make dev``/the simulated stack, where the Vite server
  proxies with the browser's Host), the ``SA_PUBLIC_URL`` host and
  ``security.allowed_hosts``; anything else is 400 ``bad_host``. This
  stops DNS rebinding, where a hostile site points its own name at
  127.0.0.1.
- **Writes** (POST/PUT/PATCH/DELETE, and every WebSocket upgrade) need a
  familiar face: a foreign ``Origin`` or a ``Sec-Fetch-Site`` other than
  ``same-origin``/``none`` gets 403 ``origin_refused``. A request without
  either header (curl, scripts) passes — browsers always send them on
  cross-site writes.
- **Content type.** A write with a body must be ``application/json``
  (415); the CSV import is the only endpoint allowed a multipart body.
- **Size.** Bodies are capped at ``security.max_body_kb`` — the import at
  ``security.max_upload_kb`` — counted while reading, not after: 413
  ``too_large`` the moment the cap is crossed (or up front from a too-big
  Content-Length).
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlparse

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import AppConfig, get_config

logger = logging.getLogger(__name__)

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_FETCH_SITES = frozenset({"same-origin", "none"})
# The CSV import is the one endpoint that may carry a multipart body and
# gets the larger upload cap (docs/SPEC.md → Request gate).
_UPLOAD_PATH = "/api/universe/import"


def _dev_server(config: AppConfig) -> bool:
    """True when a Vite server may sit in front of the app.

    ``make dev``/``make run-frontend`` serve the GUI from Vite on
    127.0.0.1:5173 (``SA_RELOAD=1`` marks those backend runs); the
    simulated demo and the e2e stack proxy it the same way on a free
    port. The Vite proxy forwards the browser's own Host and Origin, so
    loopback names on any port count as the app's own address here.
    """
    return os.environ.get("SA_RELOAD") == "1" or config.env.mode == "simulated"


def _split_host(header: str) -> tuple[str, int | None]:
    """"name", port from a Host header — ``[::1]:8000`` keeps its brackets."""
    text = header.strip().lower()
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return "", None
        name, rest = text[1:end], text[end + 1 :]
        port = rest[1:] if rest.startswith(":") else ""
        return name, int(port) if port.isdigit() else None
    name, sep, port = text.rpartition(":")
    if sep and name:
        return name, int(port) if port.isdigit() else None
    return text, None


def _extra_hostnames(config: AppConfig) -> set[str]:
    names = {
        str(n).strip().lower()
        for n in (config.get("security", "allowed_hosts", default=[]) or [])
        if str(n).strip()
    }
    public = (urlparse(config.env.sa_public_url).hostname or "").lower()
    if public:
        names.add(public)
    if os.environ.get("PYTEST_VERSION"):
        # Starlette's TestClient sends "testserver" as Host; it never
        # occurs outside pytest, so allow it there only.
        names.add("testserver")
    return names


def host_allowed(host_header: str, config: AppConfig) -> bool:
    """The Host allowlist from the spec's Request gate section."""
    name, port = _split_host(host_header)
    if not name:
        return False
    if name in _LOOPBACK_NAMES:
        return port is None or port == config.env.sa_port or _dev_server(config)
    return name in _extra_hostnames(config)


def _origin_allowed(origin: str, config: AppConfig) -> bool:
    """An Origin is ours when its hostname+port would pass the Host check."""
    parsed = urlparse(origin)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return host_allowed(
        f"{host}:{parsed.port}" if parsed.port is not None else host, config
    )


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key == name:
            return str(value.decode("latin-1"))
    return ""


def _int_header(scope: Scope, name: bytes) -> int | None:
    raw = _header(scope, name)
    try:
        return int(raw) if raw.strip() else None
    except ValueError:
        return None


async def _send_error(send: Send, status: int, code: str) -> None:
    from app.errors import error_body

    body = json.dumps(error_body(code)).encode()
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
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _refuse_ws(send: Send, code: int, reason: str) -> None:
    await send({"type": "websocket.close", "code": code, "reason": reason})


async def _drain_body(receive: Receive, cap: int) -> bytes | None:
    """Read the request body counting bytes; None once the cap is crossed.

    The cap is enforced while reading, never after — the caller answers
    413 without routing the request.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        kind = message["type"]
        if kind == "http.disconnect":
            return None
        if kind != "http.request":
            continue
        chunk = message.get("body", b"")
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            return None
        if not message.get("more_body", False):
            return b"".join(chunks)


class RequestGateMiddleware:
    """Pure ASGI so it covers the WebSocket upgrade exactly like HTTP."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        config = get_config()

        # Host: every request, every path — the GUI must not load for a
        # rebound name either.
        if not host_allowed(_header(scope, b"host"), config):
            if scope["type"] == "websocket":
                await _refuse_ws(send, 1008, "bad_host")
                return
            logger.debug("request gate: bad host %r", _header(scope, b"host"))
            await _send_error(send, 400, "bad_host")
            return

        is_write = scope["type"] == "websocket" or scope.get(
            "method", "GET"
        ) in _WRITE_METHODS
        if is_write:
            # A foreign Origin or fetch metadata from another site is
            # refused; no headers at all is curl and passes.
            origin = _header(scope, b"origin")
            fetch_site = _header(scope, b"sec-fetch-site").lower()
            refused = bool(origin) and not _origin_allowed(origin, config)
            refused = refused or bool(
                fetch_site and fetch_site not in _SAFE_FETCH_SITES
            )
            if refused:
                if scope["type"] == "websocket":
                    await _refuse_ws(send, 1008, "origin_refused")
                    return
                await _send_error(send, 403, "origin_refused")
                return

        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        is_write_http = scope.get("method", "GET") in _WRITE_METHODS
        if is_write_http:
            content_type = _header(scope, b"content-type").split(";")[0].strip()
            declared = _int_header(scope, b"content-length")
            chunked = _header(scope, b"transfer-encoding") == "chunked"
            cap = int(
                config.get(
                    "security",
                    "max_upload_kb" if path == _UPLOAD_PATH else "max_body_kb",
                    default=1024,
                )
            ) * 1024
            has_body = bool(content_type) or (declared or 0) > 0 or chunked
            if has_body:
                # Writes are JSON; only the CSV import takes multipart.
                allowed_types = (
                    ("application/json", "multipart/form-data")
                    if path == _UPLOAD_PATH
                    else ("application/json",)
                )
                if content_type not in allowed_types:
                    await _send_error(send, 415, "unsupported_media_type")
                    return
                if declared is not None and declared > cap:
                    await _send_error(send, 413, "too_large")
                    return
                body = await _drain_body(receive, cap)
                if body is None:
                    await _send_error(send, 413, "too_large")
                    return
                # Replay the counted body to the app below.
                delivered = False

                async def replay() -> Message:
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {
                            "type": "http.request",
                            "body": body,
                            "more_body": False,
                        }
                    return await receive()

                await self.app(scope, replay, send)
                return

        await self.app(scope, receive, send)


_CSP_STRICT = (
    "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "object-src 'none'; form-action 'self'"
)

# Under `make dev` the Vite dev server serves modules, its HMR socket and
# the react-refresh inline preamble — allow exactly those origins.
_CSP_DEV = (
    "default-src 'self' http://127.0.0.1:5173 http://localhost:5173; "
    "script-src 'self' 'unsafe-inline' http://127.0.0.1:5173 "
    "http://localhost:5173; "
    "style-src 'self' 'unsafe-inline' http://127.0.0.1:5173 "
    "http://localhost:5173; "
    "connect-src 'self' http://127.0.0.1:5173 http://localhost:5173 "
    "ws://127.0.0.1:5173 ws://localhost:5173; "
    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; "
    "form-action 'self'"
)


def _security_headers(config: AppConfig) -> list[tuple[bytes, bytes]]:
    """The app's own security headers (docs/SPEC.md → Request gate).

    Caddy sets the same names only as defaults (``header ?Name``), so
    these values win on the VPS; the strict CSP relaxes for the Vite dev
    server only under ``make dev``.
    """
    dev = os.environ.get("SA_RELOAD") == "1"
    return [
        (b"content-security-policy", (_CSP_DEV if dev else _CSP_STRICT).encode()),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"same-origin"),
        (b"cross-origin-opener-policy", b"same-origin"),
    ]


class SecurityHeadersMiddleware:
    """Adds the app's security headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = _security_headers(get_config())
        api = str(scope.get("path", "")).startswith("/api")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k for k, _v in message.get("headers") or []}
                extra = list(headers)
                if api and b"cache-control" not in existing:
                    extra.append((b"cache-control", b"no-store"))
                message = {
                    **message,
                    "headers": [*message.get("headers", []), *extra],
                }
            await send(message)

        await self.app(scope, receive, send_with_headers)
