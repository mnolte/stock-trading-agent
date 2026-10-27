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

from app.auth.guard import auth_required, client_ip, host_is_loopback
from app.auth.service import (
    CSRF_COOKIE,
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
    from fastapi.responses import JSONResponse

    response = JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message}},
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
    ``SameSite=Strict``. The CSRF cookie is readable so the SPA can echo
    it in the ``X-CSRF-Token`` header (double-submit).
    """
    secure = not host_is_loopback(request.url.hostname or "")
    days = int(get_config().get("auth", "remember_days", default=30))
    response.set_cookie(
        SESSION_COOKIE,
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


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/status")
def auth_status(request: Request) -> dict[str, object]:
    """Open endpoint: what the login screen and Settings need to know."""
    config = get_config()
    required = auth_required(config)
    auth = getattr(request.app.state, "auth", None)
    signed_in = False
    confirmed = False
    if required and auth is not None:
        row, reason = auth.authenticate(request.cookies.get(SESSION_COOKIE))
        signed_in = row is not None
        confirmed = signed_in and auth.confirmed(row)
    return {
        "required": required,
        "has_user": auth.has_user() if (required and auth is not None) else False,
        "signed_in": signed_in,
        "confirmed": confirmed,
        "idle_hours": int(config.get("auth", "idle_hours", default=12)),
        "confirm_minutes": int(config.get("auth", "confirm_minutes", default=5)),
        "instance_id": config.env.sa_instance_id,
    }


@router.post("/sign-in")
async def sign_in(request: Request) -> Response:
    """Step 1: password → pending token for the code step."""
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
    return JSONResponse({"step": "code", "pending": pending})


@router.post("/code")
async def sign_in_code(request: Request) -> Response:
    """Step 2: TOTP or recovery code → session + CSRF cookies."""
    from fastapi.responses import JSONResponse

    auth = _auth(request)
    body = await request.json()
    ip = client_ip(request, get_config())
    try:
        signed = auth.finish_sign_in(
            str(body.get("pending", "")), str(body.get("code", "")), ip
        )
    except AuthError as exc:
        return error_response(exc)
    response = JSONResponse({"ok": True})
    _set_session_cookies(request, response, signed)
    logger.info(
        "sign-in ok for %s", mask_log_ip(ip), extra={"event": "auth.sign_in"}
    )
    return response


def mask_log_ip(ip: str) -> str:
    from app.auth.service import mask_ip

    return mask_ip(ip)


def _session_row(request: Request) -> tuple[AuthService, AuthSession]:
    """The caller's session row or an AuthError (used inside endpoints)."""
    auth = _auth(request)
    row, reason = auth.authenticate(request.cookies.get(SESSION_COOKIE))
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
    token = request.cookies.get(SESSION_COOKIE, "")
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
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth.sign_out(token)
    response = JSONResponse({"ok": True})
    _clear_session_cookies(response)
    return response


@router.post("/sign-out-others")
def sign_out_others(request: Request) -> dict[str, object]:
    auth = _auth(request)
    count = auth.sign_out_others(request.cookies.get(SESSION_COOKIE, ""))
    return {"ok": True, "signed_out": count}


@router.post("/sign-out-everywhere")
def sign_out_everywhere(request: Request) -> Response:
    from fastapi.responses import JSONResponse

    auth = _auth(request)
    count = auth.sign_out_everywhere()
    response = JSONResponse({"ok": True, "signed_out": count})
    _clear_session_cookies(response)
    return response


@router.get("/sessions")
def sessions(request: Request) -> dict[str, object]:
    auth = _auth(request)
    return {
        "devices": auth.list_sessions(request.cookies.get(SESSION_COOKIE)),
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
            str(body.get("current_password", "")),
            str(body.get("new_password", "")),
        )
    except AuthError as exc:
        response = error_response(exc)
        if exc.status < 500:
            return response
        return response
    response = JSONResponse({"ok": True})
    _clear_session_cookies(response)
    return response


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
