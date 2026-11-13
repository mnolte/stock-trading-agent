"""Sign-in sessions, codes and rate limits (docs/SPEC.md, Sign-in).

One ``AuthService`` per app: ``app.state.auth``. With ``SA_AUTH=off``
``enabled`` is False and every check passes — the local app is unchanged.

Sign-in is two steps: the password step returns a short-lived in-memory
pending token; the code step accepts a 6-digit TOTP or a single-use
recovery code and only then creates the session row and cookie token.
Attempts land in ``sign_in_attempts`` (``ok`` / ``wrong`` / ``locked``),
which drives the per-address lockout (5 failures in 15 minutes → wait
15) and the global pause (20 failures in an hour → sign-in pauses for an
hour and a warning notification goes out). Attempt rows are pruned after
90 days on each write.

Nothing secret is logged or returned: error messages never say whether
the password or the code was wrong.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.auth.guard import auth_required
from app.auth.passwords import (
    hash_password,
    hash_token,
    new_csrf_token,
    new_device_token,
    new_pending_token,
    new_recovery_codes,
    new_session_token,
    new_totp_secret,
    normalise_recovery_code,
    otpauth_uri,
    verify_password,
    verify_totp_step,
)
from app.config import AppConfig
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import AuthSession, SignInAttempt, TrustedDevice, User
from app.market.clock import Clock
from app.notifications.service import Notifier, NullNotifier

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)

SESSION_COOKIE = "sa_session"
# Over HTTPS the session cookie is __Host-sa_session (Secure, Path=/,
# no Domain); the API picks the name per request.
SESSION_COOKIE_SECURE = "__Host-sa_session"
CSRF_COOKIE = "sa_csrf"
CSRF_HEADER = "x-csrf-token"
# The between-steps sign-in ticket, bound to the browser by cookie.
PENDING_COOKIE = "sa_pending"

# How long the between-steps pending token lives; it dies after this or
# PENDING_MAX_ATTEMPTS wrong codes, whichever comes first.
PENDING_TTL = timedelta(minutes=10)
PENDING_MAX_ATTEMPTS = 3
# A busy GUI makes many calls a minute; last_seen updates are throttled.
LAST_SEEN_WRITE_SECONDS = 60
# Trusted devices (item 083): at most this many browsers are remembered.
MAX_DEVICES = 10
# Close code for sockets ended by the session rules (item 083, S7).
WS_SESSION_END_CODE = 1008


class _Socket(Protocol):
    """The slice of WebSocket the registry needs (kept untyped so this
    module never imports the web layer)."""

    async def close(self, code: int = ..., reason: str | None = ...) -> None: ...


class AuthError(Exception):
    """One failed auth step → an HTTP response with the PR 25 error shape."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retry_after: int | None = None,
        fields: list[dict[str, str]] | None = None,
        clear_device_cookie: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after
        # 422 answers may carry per-field messages; the top-level
        # ``message`` is the registry sentence for the code (PR 25).
        self.fields = fields
        # The device cookie presented on this request was just revoked —
        # the endpoint deletes it so the browser stops presenting it.
        self.clear_device_cookie = clear_device_cookie


# The generic failure line — identical for a wrong password and a wrong
# code so the answer never reveals which step failed (spec).
_WRONG = "That didn't work — check your password or code and try again."


def describe_device(user_agent: str) -> str:
    """A short "Firefox · macOS" style name for the devices list."""
    ua = user_agent.lower()
    if "firefox" in ua:
        browser = "Firefox"
    elif "edg" in ua:
        browser = "Edge"
    elif "safari" in ua and "chrom" not in ua:
        browser = "Safari"
    elif "chrom" in ua:
        browser = "Chrome"
    else:
        browser = "Browser"
    if "iphone" in ua or "ipad" in ua:
        system = "iOS"
    elif "android" in ua:
        system = "Android"
    elif "mac os" in ua or "macintosh" in ua:
        system = "macOS"
    elif "windows" in ua:
        system = "Windows"
    elif "linux" in ua:
        system = "Linux"
    else:
        system = "device"
    return f"{browser} · {system}"


def mask_ip(ip: str) -> str:
    """``84.104.12.9`` → ``84.104.xx.xx`` for display."""
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.xx.xx"
    return ip[:12] + "…" if len(ip) > 12 else ip


@dataclass
class SignedIn:
    """Result of a completed code step: what the cookies carry."""

    session_token: str
    csrf_token: str
    keep_signed_in: bool
    # Set only when this sign-in minted a new trusted device — the
    # endpoint writes the cookie; a reused device needs no new value.
    device_token: str | None = None


class AuthService:
    def __init__(
        self,
        config: AppConfig,
        clock: Clock,
        *,
        notifier: Notifier | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._notifier = notifier or NullNotifier()
        self._factory = session_factory or get_session_maker()
        # Pending password-step tickets, keyed by token hash. In memory:
        # a restart just means typing the password again.
        self._pending: dict[str, dict[str, object]] = {}
        self._totp_cache: tuple[str, str] | None = None
        self._lockout_notified: set[str] = set()
        # Wrong "Confirm it is you"/current-password guesses per session
        # token hash (spec, Sign-in — Confirm and password change).
        self._confirm_failures: dict[str, list[datetime]] = {}
        self._scrub_warned = False
        # WebSocket registry (item 083, S7): every open feed socket with
        # its session id (None without sign-in) and the loop it runs on,
        # plus the per-session index used to close sockets at once.
        self._sockets: OrderedDict[
            _Socket, tuple[int | None, asyncio.AbstractEventLoop]
        ] = OrderedDict()
        self._sockets_by_session: dict[int, set[_Socket]] = {}

    # ------------------------------------------------------------------
    # basics

    @property
    def enabled(self) -> bool:
        return auth_required(self._config)

    def _now(self) -> datetime:
        return self._clock.now()

    def _user(self, session: Session) -> User | None:
        return session.execute(select(User)).scalars().first()

    def has_user(self) -> bool:
        with self._factory() as session:
            return self._user(session) is not None

    def _totp_secret(self, user: User) -> str:
        """Decrypt the stored TOTP blob; the scrub filter calls this on
        every log record, so the plaintext is cached on the ciphertext —
        a rotated blob simply misses once."""
        blob = user.totp_secret_encrypted
        if self._totp_cache is None or self._totp_cache[0] != blob:
            from app.secrets_service import _load_or_create_key

            fernet = _load_or_create_key(
                Path(self._config.env.sa_data_dir) / "secret.key"
            )
            self._totp_cache = (blob, fernet.decrypt(blob.encode()).decode())
        return self._totp_cache[1]

    # ------------------------------------------------------------------
    # trusted devices (item 083, S6)

    def _device_for_token(
        self, session: Session, token: str | None
    ) -> TrustedDevice | None:
        """The trusted-device row a presented cookie maps to, or None."""
        if not token:
            return None
        return session.execute(
            select(TrustedDevice).where(
                TrustedDevice.token_hash == hash_token(token)
            )
        ).scalar_one_or_none()

    def known_device(self, token: str | None) -> bool:
        """True only while the presented device cookie is a live row —
        the open status endpoint reports this and nothing more."""
        with self._factory() as session:
            return self._device_for_token(session, token) is not None

    def global_pause_until(self) -> datetime | None:
        """The persisted global pause end (aware UTC), for /auth/status."""
        with self._factory() as session:
            return self._global_pause_until(self._user(session))

    def paused_until_iso(self) -> str | None:
        """The pause end as ISO while the global pause is on, else None —
        what the open status endpoint may show the sign-in page."""
        pause = self.global_pause_until()
        if pause is None or pause <= self._now():
            return None
        return pause.isoformat()

    def _count_device_failure(
        self, session: Session, device: TrustedDevice | None
    ) -> bool:
        """A failed sign-in step with a valid device cookie also counts
        on the device: ``auth.lockout_failures`` inside
        ``auth.lockout_window_minutes`` revokes it. Returns True when the
        row was just deleted."""
        if device is None:
            return False
        window = timedelta(
            minutes=int(
                self._config.get("auth", "lockout_window_minutes", default=15)
            )
        )
        cutoff = (_naive_utc(self._now()) - window).isoformat()
        fails = [t for t in device.failures if t >= cutoff]
        fails.append(_naive_utc(self._now()).isoformat())
        device.failures = fails
        needed = int(self._config.get("auth", "lockout_failures", default=5))
        if len(fails) >= needed:
            # Sessions keep working — they are authenticated — but lose
            # the device link so the FK lets the row go.
            self._unlink_sessions(session, [device.id])
            session.delete(device)
            logger.info("trusted device revoked after %d failed sign-ins", needed)
            return True
        return False

    def _add_device(
        self, session: Session, user_agent: str, now: datetime
    ) -> tuple[TrustedDevice, str]:
        """A fresh trusted-device row + its cookie token; at most
        ``MAX_DEVICES`` rows — the oldest by ``last_used_at`` goes."""
        token = new_device_token()
        count = session.execute(
            select(func.count(TrustedDevice.id))
        ).scalar_one()
        if count >= MAX_DEVICES:
            oldest = session.execute(
                select(TrustedDevice)
                .order_by(TrustedDevice.last_used_at.asc())
                .limit(1)
            ).scalar_one_or_none()
            if oldest is not None:
                self._unlink_sessions(session, [oldest.id])
                session.delete(oldest)
        row = TrustedDevice(
            token_hash=hash_token(token),
            device_name=describe_device(user_agent),
            created_at=_naive_utc(now),
            last_used_at=_naive_utc(now),
            failures=[],
        )
        session.add(row)
        session.flush()
        return row, token

    def list_devices(self, device_token: str | None) -> list[dict[str, object]]:
        """Settings → Access → Known browsers; hashes never leave here."""
        current_hash = hash_token(device_token) if device_token else ""
        out: list[dict[str, object]] = []
        with self._factory() as session:
            rows = session.execute(
                select(TrustedDevice).order_by(TrustedDevice.last_used_at.desc())
            ).scalars().all()
            for row in rows:
                out.append(
                    {
                        "id": row.id,
                        "name": row.device_name,
                        "created_at": row.created_at.replace(tzinfo=UTC).isoformat(),
                        "last_used_at": row.last_used_at.replace(tzinfo=UTC).isoformat(),
                        "this_browser": row.token_hash == current_hash,
                    }
                )
        return out

    def _unlink_sessions(self, session: Session, device_ids: list[int]) -> None:
        """Sessions keep their row but lose the device link — SQLite
        enforces the FK, so a device row can only go once nothing points
        at it."""
        if device_ids:
            session.execute(
                update(AuthSession)
                .where(AuthSession.trusted_device_id.in_(device_ids))
                .values(trusted_device_id=None)
            )

    def _session_ids_for_devices(self, session: Session, device_ids: list[int]) -> set[int]:
        if not device_ids:
            return set()
        return set(
            session.execute(
                select(AuthSession.id).where(
                    AuthSession.trusted_device_id.in_(device_ids)
                )
            )
            .scalars()
            .all()
        )

    def remove_device(self, device_id: int) -> bool:
        """Revoke one known browser; sockets of its sessions close at once."""
        with self._factory() as session:
            row = session.get(TrustedDevice, device_id)
            if row is None:
                return False
            session_ids = self._session_ids_for_devices(session, [row.id])
            self._unlink_sessions(session, [row.id])
            session.delete(row)
            session.commit()
        self._close_session_sockets(session_ids, reason="device_removed")
        return True

    def revoke_all_devices(self) -> None:
        """Every trusted device goes (password change, sign out
        everywhere, new recovery codes — spec, Audit of 11 November)."""
        with self._factory() as session:
            rows = session.execute(select(TrustedDevice)).scalars().all()
            if not rows:
                return
            session_ids = self._session_ids_for_devices(
                session, [row.id for row in rows]
            )
            self._unlink_sessions(session, [row.id for row in rows])
            for row in rows:
                session.delete(row)
            session.commit()
        self._close_session_sockets(session_ids, reason="devices_revoked")

    # ------------------------------------------------------------------
    # WebSocket registry (item 083, S7)

    def register_socket(
        self, websocket: WebSocket, session_id: int | None
    ) -> None:
        """Track a feed socket; ``security.max_sockets`` caps the total —
        a new socket closes the oldest. With SA_AUTH=off only the cap
        applies (session_id stays None)."""
        cap = int(self._config.get("security", "max_sockets", default=20))
        while len(self._sockets) >= max(cap, 1):
            oldest, (old_session, old_loop) = self._sockets.popitem(last=False)
            if old_session is not None:
                self._sockets_by_session.get(old_session, set()).discard(oldest)
            self._close_socket(oldest, old_loop, "socket_limit")
        loop = asyncio.get_running_loop()
        self._sockets[websocket] = (session_id, loop)
        if session_id is not None:
            self._sockets_by_session.setdefault(session_id, set()).add(websocket)

    def unregister_socket(self, websocket: WebSocket) -> None:
        entry = self._sockets.pop(websocket, None)
        if entry is not None and entry[0] is not None:
            self._sockets_by_session.get(entry[0], set()).discard(websocket)

    def _close_socket(
        self, websocket: _Socket, loop: asyncio.AbstractEventLoop, reason: str
    ) -> None:
        async def _do() -> None:
            try:
                await websocket.close(code=WS_SESSION_END_CODE, reason=reason)
            except Exception:  # noqa: BLE001 - already closed is fine
                logger.debug("socket close failed (already closed)")

        try:
            asyncio.run_coroutine_threadsafe(_do(), loop)
        except RuntimeError:
            logger.debug("socket loop already closed")

    def _close_session_sockets(
        self, session_ids: set[int], *, reason: str
    ) -> None:
        """Close every socket bound to these sessions — sign-out,
        sign out everywhere, a password change and a device removal
        reach their sockets at once (spec, Audit of 11 November)."""
        for session_id in session_ids:
            for websocket in list(self._sockets_by_session.pop(session_id, set())):
                entry = self._sockets.pop(websocket, None)
                if entry is not None:
                    self._close_socket(websocket, entry[1], reason)

    # ------------------------------------------------------------------
    # attempts and rate limits

    def _record_attempt(self, session: Session, ip: str, result: str) -> None:
        now = _naive_utc(self._now())
        session.add(SignInAttempt(time=now, ip=ip, result=result))
        # Prune rows past 90 days on each write (spec, Data model).
        session.execute(
            delete(SignInAttempt).where(
                SignInAttempt.time < now - timedelta(days=90)
            )
        )

    def _recent_failures(self, session: Session, ip: str) -> list[datetime]:
        window = int(
            self._config.get("auth", "lockout_window_minutes", default=15)
        )
        since = _naive_utc(self._now()) - timedelta(minutes=window)
        return list(
            session.execute(
                select(SignInAttempt.time).where(
                    SignInAttempt.ip == ip,
                    SignInAttempt.result == "wrong",
                    SignInAttempt.time >= since,
                )
            )
            .scalars()
            .all()
        )

    def _address_locked_until(self, session: Session, ip: str) -> datetime | None:
        """The moment the per-address wait ends, or None."""
        failures = self._recent_failures(session, ip)
        needed = int(self._config.get("auth", "lockout_failures", default=5))
        if len(failures) < needed:
            return None
        wait = int(self._config.get("auth", "lockout_minutes", default=15))
        return max(failures).replace(tzinfo=UTC) + timedelta(minutes=wait)

    @staticmethod
    def _global_pause_until(user: User | None) -> datetime | None:
        """The persisted global pause end as an aware UTC datetime."""
        if user is None or user.global_locked_until is None:
            return None
        return user.global_locked_until.replace(tzinfo=UTC)

    def _check_allowed(
        self, session: Session, ip: str, device: TrustedDevice | None = None
    ) -> None:
        """Raise 429 when sign-in is paused globally or this address waits.

        A valid trusted-device cookie skips the global pause only — the
        per-address lockout is unchanged (item 083, S6)."""
        now = self._now()
        pause = self._global_pause_until(self._user(session))
        if pause is not None and now < pause and device is None:
            retry = int((pause - now).total_seconds())
            self._record_attempt(session, ip, "locked")
            session.commit()
            raise AuthError(
                429,
                "rate_limited",
                "Too many failed sign-ins — sign-in is paused for now. "
                "You were notified by email.",
                retry_after=max(retry, 1),
            )
        locked = self._address_locked_until(session, ip)
        if locked is not None and now < locked:
            retry = int((locked - now).total_seconds())
            self._record_attempt(session, ip, "locked")
            session.commit()
            raise AuthError(
                429,
                "locked_out",
                "Too many failed attempts from this address. "
                "You were notified by email.",
                retry_after=max(retry, 1),
            )

    def _failed(
        self,
        session: Session,
        ip: str,
        device: TrustedDevice | None = None,
    ) -> AuthError:
        """Record a failed step and apply the lockout rules."""
        self._record_attempt(session, ip, "wrong")
        device_revoked = self._count_device_failure(session, device)
        session.commit()
        failures = self._recent_failures(session, ip)
        needed = int(self._config.get("auth", "lockout_failures", default=5))
        wait = int(self._config.get("auth", "lockout_minutes", default=15))
        if len(failures) >= needed and ip not in self._lockout_notified:
            # The lockout starts with this failure; the wait applies to
            # the next attempt (checked in _check_allowed).
            self._lockout_notified.add(ip)
            self._notifier.notify(
                "sign_in_lockout",
                f"Sign-in locked for an address after {needed} failed "
                f"attempts — it waits {wait} minutes",
                link="/settings",
            )
        # The global hourly cap across all addresses.
        cap = int(
            self._config.get("auth", "global_failures_per_hour", default=20)
        )
        since = _naive_utc(self._now()) - timedelta(hours=1)
        total = session.execute(
            select(func.count(SignInAttempt.id)).where(
                SignInAttempt.result == "wrong",
                SignInAttempt.time >= since,
            )
        ).scalar_one()
        if total >= cap:
            user = self._user(session)
            pause = self._global_pause_until(user)
            if pause is None or pause <= self._now():
                if user is not None:
                    user.global_locked_until = _naive_utc(
                        self._now() + timedelta(hours=1)
                    )
                self._notifier.notify(
                    "sign_in_lockout",
                    f"{total} failed sign-ins in an hour across all "
                    "addresses — sign-in is paused for an hour",
                    link="/settings",
                )
            session.commit()
            raise AuthError(
                429,
                "rate_limited",
                "Too many failed sign-ins — sign-in is paused for now. "
                "You were notified by email.",
                retry_after=3600,
                clear_device_cookie=device_revoked,
            )
        return AuthError(
            401,
            "invalid_credentials",
            _WRONG,
            clear_device_cookie=device_revoked,
        )

    # ------------------------------------------------------------------
    # the two sign-in steps

    def start_sign_in(
        self,
        password: str,
        remember: bool,
        ip: str,
        user_agent: str,
        device_token: str | None = None,
    ) -> str:
        """Password step → a pending token for the code step."""
        with self._factory() as session:
            device = self._device_for_token(session, device_token)
            self._check_allowed(session, ip, device)
            user = self._user(session)
            if user is None:
                logger.error("sign-in attempted but no user exists")
                raise AuthError(503, "unavailable", "Sign-in is not set up.")
            if not verify_password(user.password_hash, password):
                raise self._failed(session, ip, device)
        # Drop expired tickets while we're here so _pending can't grow.
        now = self._now()
        for key, ticket in list(self._pending.items()):
            if ticket["expires"] <= now:  # type: ignore[operator]
                self._drop_pending(key)
        token = new_pending_token()
        self._pending[hash_token(token)] = {
            "expires": self._now() + PENDING_TTL,
            "remember": remember,
            "ip": ip,
            "ua": user_agent,
            "attempts": 0,
        }
        return token

    def _drop_pending(self, key: str) -> None:
        self._pending.pop(key, None)

    def finish_sign_in(
        self,
        pending_token: str,
        code: str,
        ip: str,
        device_token: str | None = None,
    ) -> SignedIn:
        """Code step → session + CSRF tokens. TOTP or a recovery code."""
        key = hash_token(pending_token)
        ticket = self._pending.get(key)
        now = self._now()
        if (
            ticket is None
            or ticket["expires"] < now  # type: ignore[operator]
            or ticket["ip"] != ip  # the ticket is bound to its browser
        ):
            self._drop_pending(key)
            raise AuthError(401, "invalid_credentials", _WRONG)
        with self._factory() as session:
            device = self._device_for_token(session, device_token)
            self._check_allowed(session, ip, device)
            user = self._user(session)
            if user is None:
                raise AuthError(503, "unavailable", "Sign-in is not set up.")
            secret = self._totp_secret(user)
            # A TOTP step can be used once: codes at or below the last
            # used step are refused (replay protection per spec).
            ok, matched_step = verify_totp_step(secret, code, now)
            if ok and matched_step <= (user.last_totp_step or -1):
                ok = False
            if not ok:
                # Recovery codes are single-use: remove the hash when spent.
                hashed = hash_token(normalise_recovery_code(code))
                hashes = list(user.recovery_code_hashes)
                if hashed in hashes:
                    hashes.remove(hashed)
                    user.recovery_code_hashes = hashes
                    ok = True
                    matched_step = -1
            if not ok:
                # A wrong code counts toward the lockout like a wrong
                # password; the ticket dies after 3 misses.
                attempts = cast(int, ticket["attempts"]) + 1
                ticket["attempts"] = attempts
                if attempts >= PENDING_MAX_ATTEMPTS:
                    self._drop_pending(key)
                raise self._failed(session, ip, device)
            if matched_step >= 0:
                user.last_totp_step = matched_step
            self._record_attempt(session, ip, "ok")
            remember = bool(ticket["remember"])
            days = int(self._config.get("auth", "remember_days", default=30))
            ua = str(ticket["ua"])
            # The browser becomes a known device: a live cookie is
            # reused (last_used moves), anything else mints a new row —
            # the cookie value only ever leaves here once, in SignedIn.
            new_device_token_value: str | None = None
            if device is not None:
                device.last_used_at = _naive_utc(now)
                device_row = device
            else:
                device_row, new_device_token_value = self._add_device(
                    session, ua, now
                )
            token = new_session_token()
            row = AuthSession(
                token_hash=hash_token(token),
                device_name=describe_device(ua),
                user_agent=ua[:512],
                ip=ip,
                created_at=_naive_utc(now),
                last_seen_at=_naive_utc(now),
                expires_at=_naive_utc(now + timedelta(days=days)),
                keep_signed_in=remember,
                trusted_device_id=device_row.id,
            )
            session.add(row)
            session.commit()
            device_label = row.device_name
        self._drop_pending(key)
        self._notifier.notify(
            "new_sign_in",
            f"{device_label} from {mask_ip(ip)}",
            link="/settings",
        )
        return SignedIn(token, new_csrf_token(), remember, new_device_token_value)

    # ------------------------------------------------------------------
    # sessions

    def authenticate(self, token: str | None) -> tuple[AuthSession | None, str]:
        """Cookie token → live session row, or None with a reason code."""
        if not token:
            return None, "not_signed_in"
        key = hash_token(token)
        now = self._now()
        with self._factory() as session:
            row = session.execute(
                select(AuthSession).where(AuthSession.token_hash == key)
            ).scalar_one_or_none()
            if row is None:
                return None, "not_signed_in"
            last_seen = row.last_seen_at.replace(tzinfo=UTC)
            expires = row.expires_at.replace(tzinfo=UTC)
            idle_hours = int(self._config.get("auth", "idle_hours", default=12))
            if expires <= now or last_seen + timedelta(hours=idle_hours) <= now:
                session.delete(row)
                session.commit()
                return None, "session_expired"
            if (now - last_seen).total_seconds() >= LAST_SEEN_WRITE_SECONDS:
                row.last_seen_at = _naive_utc(now)
                session.commit()
            session.expunge(row)
            return row, "ok"

    def confirmed(self, row: AuthSession) -> bool:
        """True while "Confirm it is you" is still fresh on this device."""
        if row.confirmed_until is None:
            return False
        until = row.confirmed_until.replace(tzinfo=UTC)
        return until > self._now()

    def _session_failed(
        self, session: Session, key: str, ip: str, session_id: int | None = None
    ) -> None:
        """A wrong secret for a live session — counts per session like a
        sign-in failure counts per address (spec: after 5 in 15 minutes
        the session is signed out and the "Failed sign-ins" warning
        goes out; a stolen cookie must not allow unlimited guessing)."""
        now = self._now()
        window = timedelta(
            minutes=int(
                self._config.get("auth", "lockout_window_minutes", default=15)
            )
        )
        fails = [
            t for t in self._confirm_failures.get(key, []) if now - t < window
        ]
        fails.append(now)
        self._record_attempt(session, ip, "wrong")
        needed = int(self._config.get("auth", "lockout_failures", default=5))
        if len(fails) >= needed:
            session.execute(
                delete(AuthSession).where(AuthSession.token_hash == key)
            )
            self._confirm_failures.pop(key, None)
            session.commit()
            if session_id is not None:
                self._close_session_sockets({session_id}, reason="session_ended")
            logger.warning("session signed out after %d failed confirm attempts", needed)
            self._notifier.notify(
                "sign_in_lockout",
                f"A session was signed out after {needed} failed "
                "confirm-it-is-you attempts",
                link="/settings",
            )
            return
        self._confirm_failures[key] = fails
        session.commit()

    def confirm(self, token: str, secret: str) -> None:
        """Password or authenticator code again → confirmed_until."""
        key = hash_token(token)
        now = self._now()
        with self._factory() as session:
            row = session.execute(
                select(AuthSession).where(AuthSession.token_hash == key)
            ).scalar_one_or_none()
            user = self._user(session)
            if row is None or user is None:
                raise AuthError(401, "not_signed_in", "Sign in first.")
            ok = verify_password(user.password_hash, secret)
            matched_step = -1
            if not ok:
                # Same once-per-step rule as sign-in: a code already used
                # (here or at sign-in) is refused on replay.
                ok, matched_step = verify_totp_step(
                    self._totp_secret(user), secret, now
                )
                if ok and matched_step <= (user.last_totp_step or -1):
                    ok = False
            if not ok:
                self._session_failed(session, key, row.ip, row.id)
                raise AuthError(401, "invalid_credentials", _WRONG)
            if matched_step >= 0:
                user.last_totp_step = matched_step
            self._confirm_failures.pop(key, None)
            minutes = int(self._config.get("auth", "confirm_minutes", default=5))
            row.confirmed_until = _naive_utc(now + timedelta(minutes=minutes))
            session.commit()

    def list_sessions(self, current_token: str | None) -> list[dict[str, object]]:
        """The Settings → Access devices list; hashes never leave here."""
        now = self._now()
        current_hash = hash_token(current_token) if current_token else ""
        out: list[dict[str, object]] = []
        with self._factory() as session:
            rows = session.execute(
                select(AuthSession).order_by(AuthSession.last_seen_at.desc())
            ).scalars().all()
            for row in rows:
                if row.expires_at.replace(tzinfo=UTC) <= now:
                    continue
                out.append(
                    {
                        "id": row.id,
                        "device": row.device_name,
                        "ip": mask_ip(row.ip),
                        "created_at": row.created_at.replace(tzinfo=UTC).isoformat(),
                        "last_seen_at": row.last_seen_at.replace(tzinfo=UTC).isoformat(),
                        "keep_signed_in": row.keep_signed_in,
                        "current": row.token_hash == current_hash,
                    }
                )
        return out

    def sign_out(self, token: str) -> None:
        with self._factory() as session:
            sid = session.execute(
                select(AuthSession.id).where(
                    AuthSession.token_hash == hash_token(token)
                )
            ).scalar_one_or_none()
            session.execute(
                delete(AuthSession).where(AuthSession.token_hash == hash_token(token))
            )
            session.commit()
        if sid is not None:
            self._close_session_sockets({sid}, reason="signed_out")

    def sign_out_others(self, token: str) -> int:
        with self._factory() as session:
            ids = set(
                session.execute(
                    select(AuthSession.id).where(
                        AuthSession.token_hash != hash_token(token)
                    )
                )
                .scalars()
                .all()
            )
            result = session.execute(
                delete(AuthSession).where(AuthSession.token_hash != hash_token(token))
            )
            session.commit()
        self._close_session_sockets(ids, reason="signed_out")
        return int(getattr(result, "rowcount", 0) or 0)

    def sign_out_everywhere(self) -> int:
        with self._factory() as session:
            ids = set(session.execute(select(AuthSession.id)).scalars().all())
            result = session.execute(delete(AuthSession))
            session.commit()
        self._close_session_sockets(ids, reason="signed_out")
        # "Sign out everywhere" also forgets every known browser (spec,
        # Audit of 11 November → Sign-in can't be paused).
        self.revoke_all_devices()
        return int(getattr(result, "rowcount", 0) or 0)

    def sign_out_by_id(self, session_id: int) -> None:
        with self._factory() as session:
            session.execute(
                delete(AuthSession).where(AuthSession.id == session_id)
            )
            session.commit()
        self._close_session_sockets({session_id}, reason="signed_out")

    def failed_attempts_summary(self) -> dict[str, object]:
        """"Failed sign-ins in the last 7 days" for Settings → Access."""
        since = _naive_utc(self._now()) - timedelta(days=7)
        with self._factory() as session:
            rows = session.execute(
                select(SignInAttempt).where(
                    SignInAttempt.result != "ok", SignInAttempt.time >= since
                )
            ).scalars().all()
        addresses = {row.ip for row in rows}
        last = max((row.time for row in rows), default=None)
        return {
            "count": len(rows),
            "addresses": len(addresses),
            "last_at": last.replace(tzinfo=UTC).isoformat() if last else None,
            "last_ip": mask_ip(
                max(rows, key=lambda r: r.time).ip if rows else ""
            )
            if rows
            else None,
        }

    # ------------------------------------------------------------------
    # changing credentials (each caller gates on a fresh confirm)

    def change_password(self, token: str, current: str, new: str) -> None:
        """Change the password; every OTHER session signs out — this
        device keeps working (spec, Settings → Access)."""
        user_error = AuthError(401, "invalid_credentials", _WRONG)
        minimum = int(self._config.get("auth", "min_password_length", default=14))
        if len(new) < minimum:
            raise AuthError(
                422,
                "invalid",
                f"Password must be at least {minimum} characters.",
                fields=[
                    {
                        "name": "new_password",
                        "message": f"Password must be at least {minimum} characters.",
                    }
                ],
            )
        with self._factory() as session:
            user = self._user(session)
            key = hash_token(token)
            row = session.execute(
                select(AuthSession).where(AuthSession.token_hash == key)
            ).scalar_one_or_none()
            if user is None or not verify_password(user.password_hash, current):
                if row is not None:
                    self._session_failed(session, key, row.ip, row.id)
                raise user_error
            user.password_hash = hash_password(new)
            user.password_changed_at = _naive_utc(self._now())
            other_ids = set(
                session.execute(
                    select(AuthSession.id).where(
                        AuthSession.token_hash != hash_token(token)
                    )
                )
                .scalars()
                .all()
            )
            session.execute(
                delete(AuthSession).where(
                    AuthSession.token_hash != hash_token(token)
                )
            )
            session.commit()
            self._close_session_sockets(other_ids, reason="signed_out")
            # A password change forgets every known browser too (spec,
            # Audit of 11 November → Sign-in can't be paused).
            self.revoke_all_devices()

    def reset_totp(self) -> dict[str, object]:
        """A new authenticator secret; returns what is shown once."""
        from app.secrets_service import _load_or_create_key

        with self._factory() as session:
            user = self._user(session)
            if user is None:
                raise AuthError(503, "unavailable", "Sign-in is not set up.")
            secret = new_totp_secret()
            fernet = _load_or_create_key(
                Path(self._config.env.sa_data_dir) / "secret.key"
            )
            user.totp_secret_encrypted = fernet.encrypt(secret.encode()).decode()
            username = user.username
            session.commit()
        return {
            "secret": secret,
            "otpauth_uri": otpauth_uri(
                secret, username, "Stock Trading Agent"
            ),
        }

    def new_codes(self) -> list[str]:
        """A fresh set of recovery codes; returns them once."""
        with self._factory() as session:
            user = self._user(session)
            if user is None:
                raise AuthError(503, "unavailable", "Sign-in is not set up.")
            codes = new_recovery_codes(
                int(self._config.get("auth", "recovery_codes", default=10))
            )
            user.recovery_code_hashes = [
                hash_token(normalise_recovery_code(c)) for c in codes
            ]
            session.commit()
            # New recovery codes revoke every known browser (spec,
            # Audit of 11 November → Sign-in can't be paused).
            self.revoke_all_devices()
            return codes

    def codes_left(self) -> int:
        with self._factory() as session:
            user = self._user(session)
            return len(user.recovery_code_hashes) if user else 0

    def scrub_values(self) -> list[str]:
        """Live values the log scrubber must never print (hard rule 9/15):
        the TOTP secret and any unexpired pending tokens."""
        values: list[str] = []
        now = self._now()
        for key, ticket in list(self._pending.items()):
            if ticket["expires"] <= now:  # type: ignore[operator]
                self._drop_pending(key)
        try:
            with self._factory() as session:
                user = self._user(session)
                if user is not None:
                    values.append(self._totp_secret(user))
        except Exception:  # noqa: BLE001 - the scrubber must never break
            # This provider runs inside the log scrubber's own filter: a
            # record logged here is filtered by it, so warn once per
            # failure episode — a record per call feeds itself forever.
            if not self._scrub_warned:
                self._scrub_warned = True
                logger.exception("auth scrub values failing")
        else:
            self._scrub_warned = False
        return values

