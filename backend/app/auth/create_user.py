"""``make create-user`` — create or reset the one sign-in account.

Runs on the server only (docs/SPEC.md, VPS deployment → Sign-in): asks
for the username and password (at least ``auth.min_password_length``),
sets up TOTP — the otpauth URI and a terminal QR are shown — and prints
the recovery codes once. The TOTP secret is Fernet-encrypted with the
same ``data/secret.key`` as the API keys; codes are stored as hashes.
A reset signs every existing session out.
"""

from __future__ import annotations

import getpass
import logging
import sys
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.passwords import (
    hash_password,
    hash_token,
    new_recovery_codes,
    new_totp_secret,
    normalise_recovery_code,
    otpauth_uri,
)
from app.config import AppConfig, get_config
from app.data.cache import _naive_utc
from app.db.models import AuthSession, User
from app.market.clock import Clock, make_clock

logger = logging.getLogger(__name__)

ISSUER = "Stock Trading Agent"


def create_or_reset_user(
    session: Session,
    config: AppConfig,
    clock: Clock,
    *,
    username: str,
    password: str,
) -> tuple[User, str, list[str]]:
    """Write the single user row; returns (user, totp secret, codes).

    The plaintext TOTP secret and recovery codes exist only in this
    return value — callers print them once and drop them.
    """
    minimum = int(config.get("auth", "min_password_length", default=14))
    if len(password) < minimum:
        raise ValueError(f"password must be at least {minimum} characters")

    from app.secrets_service import _load_or_create_key

    fernet = _load_or_create_key(Path(config.env.sa_data_dir) / "secret.key")
    secret = new_totp_secret()
    codes = new_recovery_codes(
        int(config.get("auth", "recovery_codes", default=10))
    )
    now = _naive_utc(clock.now())

    user = session.execute(select(User)).scalars().first()
    if user is None:
        user = User(username=username, created_at=now)
        session.add(user)
    else:
        user.username = username
    user.password_hash = hash_password(password)
    user.totp_secret_encrypted = fernet.encrypt(secret.encode()).decode()
    user.recovery_code_hashes = [
        hash_token(normalise_recovery_code(c)) for c in codes
    ]
    user.password_changed_at = now
    # A reset signs every device out: old sessions die with the old codes.
    session.execute(delete(AuthSession))
    session.commit()
    return user, secret, codes


def _print_qr(uri: str) -> None:
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.print_ascii(invert=True)
    except ImportError:  # pragma: no cover - qrcode is a real dependency
        print("(install the qrcode package to see the QR; the URI above works)")


def main() -> int:
    from app.api.main import run_migrations
    from app.db.base import get_session_maker

    config = get_config()
    clock = make_clock(config)
    run_migrations()  # a fresh server has no tables yet

    username = input("Username [marc]: ").strip() or "marc"
    minimum = int(config.get("auth", "min_password_length", default=14))
    while True:
        password = getpass.getpass(
            f"Password (at least {minimum} characters): "
        )
        if len(password) < minimum:
            print(f"Too short — at least {minimum} characters.")
            continue
        if password != getpass.getpass("Repeat the password: "):
            print("The passwords did not match.")
            continue
        break

    with get_session_maker()() as session:
        user, secret, codes = create_or_reset_user(
            session, config, clock, username=username, password=password
        )

    uri = otpauth_uri(secret, user.username, ISSUER)
    print()
    print("Authenticator — scan the QR or paste the URI into your app:")
    _print_qr(uri)
    print(f"  {uri}")
    print()
    print(f"Recovery codes for {user.username} — shown once, keep them safe:")
    for code in codes:
        print(f"  {code}")
    print()
    print("Done. Sign in with the password and a code from the app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
