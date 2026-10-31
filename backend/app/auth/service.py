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

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.guard import auth_required
from app.auth.passwords import (
    hash_password,
    hash_token,
    new_csrf_token,
    new_pending_token,
    new_recovery_codes,
    new_session_token,
    new_totp_secret,
    normalise_recovery_code,
    otpauth_uri,
    verify_password,
    verify_totp,
    verify_totp_step,
)
from app.config import AppConfig
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import AuthSession, SignInAttempt, User
from app.market.clock import Clock
from app.notifications.service import Notifier, NullNotifier

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


class AuthError(Exception):
    """One failed auth step → an HTTP response with the PR 25 error shape."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retry_after: int | None = None,
        fields: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after
        # 422 answers may carry per-field messages; the top-level
        # ``message`` is the registry sentence for the code (PR 25).
        self.fields = fields


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
        self._global_locked_until: datetime | None = None
        self._lockout_notified: set[str] = set()
        self._scrub_warned = False

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

    def _check_allowed(self, session: Session, ip: str) -> None:
        """Raise 429 when sign-in is paused globally or this address waits."""
        now = self._now()
        if self._global_locked_until is not None and now < self._global_locked_until:
            retry = int((self._global_locked_until - now).total_seconds())
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

    def _failed(self, session: Session, ip: str) -> AuthError:
        """Record a failed step and apply the lockout rules."""
        self._record_attempt(session, ip, "wrong")
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
            if (
                self._global_locked_until is None
                or self._global_locked_until <= self._now()
            ):
                self._notifier.notify(
                    "sign_in_lockout",
                    f"{total} failed sign-ins in an hour across all "
                    "addresses — sign-in is paused for an hour",
                    link="/settings",
                )
            self._global_locked_until = self._now() + timedelta(hours=1)
            raise AuthError(
                429,
                "rate_limited",
                "Too many failed sign-ins — sign-in is paused for now. "
                "You were notified by email.",
                retry_after=3600,
            )
        return AuthError(401, "invalid_credentials", _WRONG)

    # ------------------------------------------------------------------
    # the two sign-in steps

    def start_sign_in(self, password: str, remember: bool, ip: str, user_agent: str) -> str:
        """Password step → a pending token for the code step."""
        with self._factory() as session:
            self._check_allowed(session, ip)
            user = self._user(session)
            if user is None:
                logger.error("sign-in attempted but no user exists")
                raise AuthError(503, "unavailable", "Sign-in is not set up.")
            if not verify_password(user.password_hash, password):
                raise self._failed(session, ip)
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

    def finish_sign_in(self, pending_token: str, code: str, ip: str) -> SignedIn:
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
            self._check_allowed(session, ip)
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
                raise self._failed(session, ip)
            if matched_step >= 0:
                user.last_totp_step = matched_step
            self._record_attempt(session, ip, "ok")
            remember = bool(ticket["remember"])
            days = int(self._config.get("auth", "remember_days", default=30))
            ua = str(ticket["ua"])
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
            )
            session.add(row)
            session.commit()
            device = row.device_name
        self._drop_pending(key)
        self._notifier.notify(
            "new_sign_in",
            f"{device} from {mask_ip(ip)}",
            link="/settings",
        )
        return SignedIn(token, new_csrf_token(), remember)

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
            if not ok:
                ok = verify_totp(self._totp_secret(user), secret, now)
            if not ok:
                raise AuthError(401, "invalid_credentials", _WRONG)
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
            session.execute(
                delete(AuthSession).where(AuthSession.token_hash == hash_token(token))
            )
            session.commit()

    def sign_out_others(self, token: str) -> int:
        with self._factory() as session:
            result = session.execute(
                delete(AuthSession).where(AuthSession.token_hash != hash_token(token))
            )
            session.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    def sign_out_everywhere(self) -> int:
        with self._factory() as session:
            result = session.execute(delete(AuthSession))
            session.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    def sign_out_by_id(self, session_id: int) -> None:
        with self._factory() as session:
            session.execute(
                delete(AuthSession).where(AuthSession.id == session_id)
            )
            session.commit()

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
            if user is None or not verify_password(user.password_hash, current):
                raise user_error
            user.password_hash = hash_password(new)
            user.password_changed_at = _naive_utc(self._now())
            session.execute(
                delete(AuthSession).where(
                    AuthSession.token_hash != hash_token(token)
                )
            )
            session.commit()

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
            session.commit()
        return {
            "secret": secret,
            "otpauth_uri": otpauth_uri(
                secret, user.username, "Stock Trading Agent"
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

