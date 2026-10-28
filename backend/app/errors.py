"""One JSON error shape for every ``/api`` error (docs/SPEC.md →
Error pages and API errors).

Every error response is ``{"error": {"code", "message", "error_id"?,
"fields"?}}``:

- ``code`` is a stable value from :class:`ErrorCode` — the frontend maps
  statuses and codes to behaviour, never the text.
- ``message`` is always the fixed registry sentence for the code —
  never the exception's own text, which can carry keys or paths
  (hard rule 9).
- ``error_id`` is added on 500 and matches the ERROR log line.
- ``fields`` is added on 422: a list of ``{"name", "message"}`` pairs.
- Nothing else: no tracebacks, SQL, file paths or secret values.

Endpoints raise ``ApiError`` (or plain ``HTTPException``, which maps to
the status's default code) or let a specific detail string name a code:
``HTTPException(422, detail="password_required")`` answers with the
``password_required`` code and its registry sentence.
"""

from __future__ import annotations

import json
import logging
import secrets as _secrets
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """Stable wire codes — adding is fine, renaming never is."""

    # 400
    BAD_REQUEST = "bad_request"
    BAD_HOST = "bad_host"  # PR 28 raises it; registered here.
    # 401
    NOT_SIGNED_IN = "not_signed_in"
    SESSION_EXPIRED = "session_expired"
    INVALID_CREDENTIALS = "invalid_credentials"
    # 403
    FORBIDDEN = "forbidden"
    CONFIRM_REQUIRED = "confirm_required"
    ORIGIN_REFUSED = "origin_refused"  # PR 28.
    SIMULATED_ONLY = "simulated_only"
    # 404
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    # 409
    CONFLICT = "conflict"
    SETTINGS_CHANGED = "settings_changed"
    # 413 / 415 — PR 28's request gate.
    TOO_LARGE = "too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    # 422
    INVALID = "invalid"
    PASSWORD_REQUIRED = "password_required"  # PR 28.
    # 429
    RATE_LIMITED = "rate_limited"
    LOCKED_OUT = "locked_out"
    # 500
    INTERNAL = "internal"
    # 502 / 503
    APP_UNAVAILABLE = "app_unavailable"  # Caddy answers this on 502/504.
    UPDATING = "updating"  # Caddy answers this on 503 maintenance.
    UNAVAILABLE = "unavailable"
    STARTING_UP = "starting_up"
    SHUTTING_DOWN = "shutting_down"


MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.BAD_REQUEST: "The request couldn't be understood.",
    ErrorCode.BAD_HOST: "Requests must come to the app's own address.",
    ErrorCode.NOT_SIGNED_IN: "Sign in to continue.",
    ErrorCode.SESSION_EXPIRED: "Your session expired — sign in again.",
    ErrorCode.INVALID_CREDENTIALS: (
        "That didn't work — check your password or code and try again."
    ),
    ErrorCode.FORBIDDEN: "That request isn't allowed.",
    ErrorCode.CONFIRM_REQUIRED: "Confirm it is you before this action.",
    ErrorCode.ORIGIN_REFUSED: "This request didn't come from the app.",
    ErrorCode.SIMULATED_ONLY: "Only available in simulated mode.",
    ErrorCode.NOT_FOUND: "That doesn't exist.",
    ErrorCode.METHOD_NOT_ALLOWED: "That method isn't allowed here.",
    ErrorCode.CONFLICT: (
        "Something changed while you were working — reload to see the latest."
    ),
    ErrorCode.SETTINGS_CHANGED: "Settings changed — reload before saving again.",
    ErrorCode.TOO_LARGE: "That request was too large.",
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: "The app only accepts JSON here.",
    ErrorCode.INVALID: "Check the highlighted fields.",
    ErrorCode.PASSWORD_REQUIRED: "Enter the password again for this change.",
    ErrorCode.RATE_LIMITED: (
        "Too many failed sign-ins — sign-in is paused for now. "
        "You were notified by email."
    ),
    ErrorCode.LOCKED_OUT: (
        "Too many failed attempts from this address. "
        "You were notified by email."
    ),
    ErrorCode.INTERNAL: "Something went wrong on the server.",
    ErrorCode.APP_UNAVAILABLE: "The app isn't answering.",
    ErrorCode.UPDATING: "A new version is being installed.",
    ErrorCode.UNAVAILABLE: "That part of the app isn't available right now.",
    ErrorCode.STARTING_UP: "The app is starting up.",
    ErrorCode.SHUTTING_DOWN: "The app is shutting down.",
}

assert set(MESSAGES) == set(ErrorCode), "every code needs one fixed sentence"

# A plain HTTPException's code when the detail doesn't name one.
STATUS_DEFAULT: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.NOT_SIGNED_IN,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: ErrorCode.INVALID,
    429: ErrorCode.RATE_LIMITED,
    500: ErrorCode.INTERNAL,
    502: ErrorCode.APP_UNAVAILABLE,
    503: ErrorCode.UNAVAILABLE,
    504: ErrorCode.APP_UNAVAILABLE,
}


class ApiError(StarletteHTTPException):
    """An API error with a registered code; ``detail`` stays the code name
    so the generic HTTPException path resolves it identically."""

    def __init__(
        self,
        status_code: int,
        code: ErrorCode | str,
        *,
        fields: list[dict[str, str]] | None = None,
        retry_after: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=str(code))
        self.code = ErrorCode(code)
        self.fields = fields
        self.retry_after = retry_after
        if retry_after is not None:
            self.headers = {**(headers or {}), "Retry-After": str(retry_after)}


def error_body(
    code: ErrorCode | str,
    *,
    fields: list[dict[str, str]] | None = None,
    error_id: str | None = None,
) -> dict[str, Any]:
    """The shared ``{"error": …}`` body — middleware and handlers both."""
    resolved = ErrorCode(code)
    error: dict[str, Any] = {"code": resolved.value, "message": MESSAGES[resolved]}
    if error_id is not None:
        error["error_id"] = error_id
    if fields:
        error["fields"] = fields
    return {"error": error}


def _field(name: object, message: object) -> dict[str, str]:
    return {"name": str(name or ""), "message": str(message)}


def fields_from_detail(detail: object) -> list[dict[str, str]] | None:
    """Endpoint-authored detail lists become ``fields``.

    Only deliberate response payloads qualify — validation-style dicts
    (``path``/``loc``/``name`` + ``message``/``msg``) and plain strings.
    Anything else is dropped: arbitrary exception text never reaches the
    body (hard rule 9).
    """
    if not isinstance(detail, list):
        return None
    fields: list[dict[str, str]] = []
    for item in detail:
        if isinstance(item, str):
            fields.append(_field("", item))
        elif isinstance(item, dict):
            name = (
                item.get("name")
                or item.get("field")
                or item.get("path")
                or item.get("loc")
            )
            if isinstance(name, (list, tuple)):
                name = ".".join(str(p) for p in name if p != "body")
            message = item.get("message") or item.get("msg")
            if message is None:
                fields.append(_field("", item))
            else:
                fields.append(_field(name, message))
        else:
            fields.append(_field("", item))
    return fields or None


def fields_from_validation(exc: RequestValidationError) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    for error in exc.errors():
        loc = [str(p) for p in error.get("loc", ()) if p not in ("body", "query", "path")]
        fields.append(_field(".".join(loc), error.get("msg", "invalid")))
    return fields


def code_for_http(exc: StarletteHTTPException) -> ErrorCode:
    """The code for an HTTPException: an ``ApiError`` keeps its own code;
    a detail string that names a registered code wins; otherwise the
    status's default."""
    if isinstance(exc, ApiError):
        return exc.code
    if isinstance(exc.detail, str):
        try:
            return ErrorCode(exc.detail)
        except ValueError:
            pass
    return STATUS_DEFAULT.get(exc.status_code, ErrorCode.BAD_REQUEST)


def install_exception_handlers(app: FastAPI) -> None:
    """One handler set for every ``/api`` failure (spec → API error format)."""

    from app.auth.service import AuthError

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, fields=exc.fields),
            headers=exc.headers,
        )

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError) -> JSONResponse:
        fields = getattr(exc, "fields", None)
        return JSONResponse(
            status_code=exc.status,
            content=error_body(exc.code, fields=fields),
            headers=(
                {"Retry-After": str(exc.retry_after)}
                if exc.retry_after is not None
                else None
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = code_for_http(exc)
        fields = (
            fields_from_detail(exc.detail)
            if exc.status_code == 422
            else None
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, fields=fields),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                ErrorCode.INVALID, fields=fields_from_validation(exc)
            ),
        )

    @app.exception_handler(json.JSONDecodeError)
    async def bad_json(
        request: Request, exc: json.JSONDecodeError
    ) -> JSONResponse:
        # A client mistake, not a server fault — 400 bad_request, logged
        # at WARNING so it never lands in the Readiness "unreviewed
        # errors" bucket (PR 25 review).
        logger.warning(
            "unreadable JSON body on %s %s: %s",
            request.method,
            request.url.path,
            exc,
        )
        return JSONResponse(
            status_code=400, content=error_body(ErrorCode.BAD_REQUEST)
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """500 with an error_id; the same id is on the log line (spec).
        The body names nothing from the exception itself."""
        error_id = f"e-{_secrets.token_hex(3)}"
        logger.error(
            "unhandled API error on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=exc,
            extra={"error_id": error_id},
        )
        return JSONResponse(
            status_code=500,
            content=error_body(ErrorCode.INTERNAL, error_id=error_id),
        )
