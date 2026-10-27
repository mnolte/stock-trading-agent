"""Passwords, TOTP and recovery codes (docs/SPEC.md, Sign-in).

- Passwords: Argon2id via argon2-cffi.
- Authenticator codes: RFC 6238 TOTP on the stdlib (HMAC-SHA1, 30 s
  step, 6 digits, ±1 step of clock drift).
- Recovery codes: 12-char groups of unambiguous characters, shown once
  and stored as SHA-256 hashes.
- Session, pending and CSRF tokens: ``secrets.token_urlsafe`` with a
  ``sat_`` / ``sap_`` / ``sac_`` prefix so the log scrubber can recognise
  them by shape even when a value was never registered.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerificationError, VerifyMismatchError

# argon2-cffi defaults are already Argon2id at the OWASP profile.
_hasher = PasswordHasher()

# Unambiguous alphabet: no 0/O, 1/I/L.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
RECOVERY_CODE_GROUPS = (4, 4, 4)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-work verification; wrong and malformed hashes both fail."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, Argon2Error):
        return False


def hash_token(token: str) -> str:
    """SHA-256 of a high-entropy token or recovery code — what the DB stores."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_session_token() -> str:
    return "sat_" + secrets.token_urlsafe(32)


def new_pending_token() -> str:
    return "sap_" + secrets.token_urlsafe(24)


def new_csrf_token() -> str:
    return "sac_" + secrets.token_urlsafe(24)


def new_totp_secret() -> str:
    """A base32 TOTP secret for authenticator apps (no padding)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_counter(secret_b32: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def totp_code(secret_b32: str, at: datetime, step: int = 30) -> str:
    """The 6-digit code for one instant (tests drive ``at``)."""
    return _totp_counter(secret_b32, int(at.timestamp()) // step)


def verify_totp(secret_b32: str, code: str, at: datetime, step: int = 30) -> bool:
    """Accept the current step and one step each side (clock drift)."""
    ok, _ = verify_totp_step(secret_b32, code, at, step)
    return ok


def verify_totp_step(
    secret_b32: str, code: str, at: datetime, step: int = 30
) -> tuple[bool, int]:
    """Like verify_totp but also returns the step that matched — sign-in
    stores it as ``last_totp_step`` so a code can never be replayed."""
    cleaned = code.replace(" ", "").strip()
    if not cleaned.isdigit() or len(cleaned) != 6:
        return False, -1
    counter = int(at.timestamp()) // step
    for drift in (-1, 0, 1):
        if hmac.compare_digest(
            _totp_counter(secret_b32, counter + drift), cleaned
        ):
            return True, counter + drift
    return False, counter


def new_recovery_codes(count: int) -> list[str]:
    """``count`` fresh single-use codes like ``K7F2-Q9MX-4T8P``."""
    codes: list[str] = []
    for _ in range(count):
        groups = [
            "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))
            for n in RECOVERY_CODE_GROUPS
        ]
        codes.append("-".join(groups))
    return codes


def normalise_recovery_code(code: str) -> str:
    """Case/separator-insensitive canonical form for hashing: the codes
    are stored and looked up by ``hash_token(normalise(code))`` so
    "k7f2 q9mx-4t8p" matches the printed "K7F2-Q9MX-4T8P"."""
    import re

    return re.sub(r"[^A-Z0-9]", "", code.upper())


def otpauth_uri(secret_b32: str, username: str, issuer: str) -> str:
    """The otpauth:// URI authenticator apps import (QR or paste)."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{username}", safe=":")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}"
        f"&issuer={quote(issuer)}&digits=6"
    )
