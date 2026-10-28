"""Sign-in HTTP endpoints (docs/SPEC.md, Sign-in).

Everything under ``/api/auth`` answers in the PR 25 error shape —
``{"error": {"code": …, "message": …}}`` with ``Retry-After`` on 429 —
so PR 25 only has to generalise it. ``/api/auth/status``, ``sign-in``
and ``code`` are the only API endpoints reachable without a session
(plus ``/healthz``); the middleware in ``app.auth.middleware`` guards
the rest.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from app.auth.guard import auth_required, client_ip, request_is_https
from app.auth.service import (
    CSRF_COOKIE,
    PENDING_COOKIE,
    PENDING_TTL,
    SESSION_COOKIE,
    AuthError,
    AuthService,
    SignedIn,
)
from app.config import get_config
from app.db.models import AuthSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")


def error_response(exc: AuthError) -> Response:
    """The shared shape via the code registry (docs/SPEC.md → API errors):
    the message is the registry sentence for the code, Retry-After on 429."""
    from fastapi.responses import JSONResponse

    from app.errors import error_body

    response = JSONResponse(
        status_code=exc.status,
        content=error_body(exc.code, fields=getattr(exc, "fields", None)),
    )
    if exc.retry_after is not None:
        response.headers["Retry-After"] = str(exc.retry_after)
    return response


def _auth(request: Request) -> AuthService:
    auth = getattr(request.app.state, "auth", None)
    if not isinstance(auth, AuthService):  # pragma: no cover
        raise AuthError(503, "unavailable", "Auth is not initialised.")
    return auth


def _set_session_cookies(request: Request, response: Response, signed: SignedIn) -> None:
    """HttpOnly session cookie + readable CSRF cookie.

    ``Secure`` everywhere except plain-http loopback (local dev), and
    ``SameSite=Strict`` — over HTTPS the session cookie is
    ``__Host-sa_session`` (Secure, Path=/, no Domain). The CSRF cookie is
    readable so the SPA can echo it in the ``X-CSRF-Token`` header.
    """
    secure = _secure_cookies(request)
    name = session_cookie_name(request)
    days = int(get_config().get("auth", "remember_days", default=30))
    response.set_cookie(
        name,
        signed.session_token,
        max_age=days * 86400 if signed.keep_signed_in else None,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        signed.csrf_token,
        max_age=days * 86400 if signed.keep_signed_in else None,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )


def _set_pending_cookie(request: Request, response: Response, token: str) -> None:
    """The between-steps ticket is bound to the browser that entered the
    password — an HttpOnly cookie, matching the session cookie policy."""
    response.set_cookie(
        PENDING_COOKIE,
        token,
        max_age=int(PENDING_TTL.total_seconds()),
        httponly=True,
        secure=_secure_cookies(request),
        samesite="strict",
        path="/",
    )


def _clear_session_cookies(request: Request, response: Response) -> None:
    response.delete_cookie(session_cookie_name(request), path="/")
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def session_cookie_name(request: Request) -> str:
    """``__Host-sa_session`` over HTTPS, ``sa_session`` on loopback
    (docs/SPEC.md, Sign-in). The ``__Host-`` prefix requires Secure,
    Path=/ and no Domain — all set in ``_set_session_cookies``."""
    return "__Host-sa_session" if _secure_cookies(request) else SESSION_COOKIE


def _secure_cookies(request: Request) -> bool:
    """Secure cookies on HTTPS — the real scheme, or the trusted
    ``X-Forwarded-Proto`` behind Caddy on the VPS."""
    return request_is_https(request, get_config())


@router.get("/status")
def auth_status(request: Request) -> dict[str, object]:
    """Open endpoint: only what the sign-in gate itself needs to know."""
    required = auth_required(get_config())
    auth = getattr(request.app.state, "auth", None)
    signed_in = False
    if required and auth is not None:
        row, _ = auth.authenticate(_session_token(request) or None)
        signed_in = row is not None
    return {"required": required, "signed_in": signed_in}


@router.post("/sign-in")
async def sign_in(request: Request) -> Response:
    """Step 1: password → a pending ticket in an HttpOnly cookie, bound
    to this browser (docs/SPEC.md, Sign-in: two steps)."""
    from fastapi.responses import JSONResponse

    auth = _auth(request)
    body = await request.json()
    ip = client_ip(request, get_config())
    ua = request.headers.get("user-agent", "")
    try:
        pending = auth.start_sign_in(
            str(body.get("password", "")),
            bool(body.get("remember")),
            ip,
            ua,
        )
    except AuthError as exc:
        return error_response(exc)
    response = JSONResponse({"step": "code"})
    _set_pending_cookie(request, response, pending)
    return response


@router.post("/code")
async def sign_in_code(request: Request) -> Response:
    """Step 2: TOTP or recovery code → session + CSRF cookies. The
    pending ticket comes from the ``sa_pending`` cookie, not the body."""
    from fastapi.responses import JSONResponse

    auth = _auth(request)
    body = await request.json()
    ip = client_ip(request, get_config())
    pending = request.cookies.get(PENDING_COOKIE, "")
    try:
        signed = auth.finish_sign_in(pending, str(body.get("code", "")), ip)
    except AuthError as exc:
        return error_response(exc)
    response = JSONResponse({"ok": True})
    response.delete_cookie(PENDING_COOKIE, path="/")
    _set_session_cookies(request, response, signed)
    logger.info(
        "sign-in ok for %s", mask_log_ip(ip), extra={"event": "auth.sign_in"}
    )
    return response


def mask_log_ip(ip: str) -> str:
    from app.auth.service import mask_ip

    return mask_ip(ip)


def _session_token(request: Request) -> str:
    """The session cookie — ``__Host-sa_session`` over HTTPS, else
    ``sa_session``; both are accepted on read."""
    return request.cookies.get(session_cookie_name(request)) or request.cookies.get(
        SESSION_COOKIE
    ) or ""


def _session_row(request: Request) -> tuple[AuthService, AuthSession]:
    """The caller's session row or an AuthError (used inside endpoints)."""
    auth = _auth(request)
    row, reason = auth.authenticate(_session_token(request) or None)
    if row is None:
        raise AuthError(401, reason, "Sign in first.")
    return auth, row


def require_confirmed(request: Request) -> None:
    """The "Confirm it is you" gate for risky actions (PR 23 step 4).

    With SA_AUTH=off this is a no-op. Otherwise the session must have a
    confirmation younger than ``auth.confirm_minutes``.
    """
    if not auth_required(get_config()):
        return
    auth, row = _session_row(request)
    if not auth.confirmed(row):
        raise AuthError(
            403,
            "confirm_required",
            "Confirm it is you before this action.",
        )


@router.post("/confirm")
async def confirm(request: Request) -> Response:
    from fastapi.responses import JSONResponse

    auth = _auth(request)
    token = _session_token(request)
    body = await request.json()
    try:
        auth.confirm(token, str(body.get("secret", "")))
    except AuthError as exc:
        return error_response(exc)
    return JSONResponse({"ok": True})


@router.post("/sign-out")
def sign_out(request: Request) -> Response:
    from fastapi.responses import JSONResponse

    auth = _auth(request)
    token = _session_token(request)
    if token:
        auth.sign_out(token)
    response = JSONResponse({"ok": True})
    _clear_session_cookies(request, response)
    return response


@router.post("/sign-out-others")
def sign_out_others(request: Request) -> dict[str, object]:
    auth = _auth(request)
    count = auth.sign_out_others(_session_token(request))
    return {"ok": True, "signed_out": count}


@router.post("/sign-out-everywhere")
def sign_out_everywhere(request: Request) -> Response:
    from fastapi.responses import JSONResponse

    auth = _auth(request)
    count = auth.sign_out_everywhere()
    response = JSONResponse({"ok": True, "signed_out": count})
    _clear_session_cookies(request, response)
    return response


@router.get("/sessions")
def sessions(request: Request) -> dict[str, object]:
    auth = _auth(request)
    return {
        "devices": auth.list_sessions(_session_token(request) or None),
        "failed": auth.failed_attempts_summary(),
        "recovery_codes_left": auth.codes_left(),
    }


@router.post("/sessions/{session_id}/sign-out")
def sign_out_session(request: Request, session_id: int) -> dict[str, object]:
    auth = _auth(request)
    auth.sign_out_by_id(session_id)
    return {"ok": True}


@router.post("/password")
async def change_password(request: Request) -> Response:
    from fastapi.responses import JSONResponse

    try:
        require_confirmed(request)
        auth = _auth(request)
        body = await request.json()
        auth.change_password(
            _session_token(request),
            str(body.get("current_password", "")),
            str(body.get("new_password", "")),
        )
    except AuthError as exc:
        return error_response(exc)
    # The other sessions are gone; this device's cookie stays.
    return JSONResponse({"ok": True})


@router.post("/totp")
def reset_totp(request: Request) -> Response:
    from fastapi.responses import JSONResponse

    try:
        require_confirmed(request)
        auth = _auth(request)
        result = auth.reset_totp()
    except AuthError as exc:
        return error_response(exc)
    return JSONResponse(result)


@router.post("/recovery-codes")
def new_recovery_codes_endpoint(request: Request) -> Response:
    from fastapi.responses import JSONResponse

    try:
        require_confirmed(request)
        auth = _auth(request)
        codes = auth.new_codes()
    except AuthError as exc:
        return error_response(exc)
    return JSONResponse({"codes": codes})
