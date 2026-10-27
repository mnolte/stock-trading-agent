"""PR 23 step 2: credential primitives and the create-user writer."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from app.auth.create_user import create_or_reset_user
from app.auth.passwords import (
    hash_password,
    hash_token,
    new_recovery_codes,
    normalise_recovery_code,
    otpauth_uri,
    totp_code,
    verify_password,
    verify_totp,
)
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import AuthSession
from app.market.clock import FakeClock
from sqlalchemy import select

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def test_password_roundtrip_and_reject() -> None:
    hashed = hash_password("a long enough password")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "a long enough password")
    assert not verify_password(hashed, "a long enough passw0rd")
    assert not verify_password("$argon2id$garbage", "a long enough password")


def test_totp_matches_rfc6238() -> None:
    """RFC 6238 SHA-1 test key, T=59s → 287082 for 6 digits."""
    secret = base64.b32encode(b"12345678901234567890").decode()
    at = datetime.fromtimestamp(59, UTC)
    assert totp_code(secret, at) == "287082"
    assert verify_totp(secret, "287082", at)
    # One step of drift is accepted, two is not.
    assert verify_totp(secret, "287082", at + timedelta(seconds=30))
    assert not verify_totp(secret, "287082", at + timedelta(seconds=60))
    # Sloppy input is cleaned, junk is rejected.
    assert verify_totp(secret, "287 082", at)
    assert not verify_totp(secret, "abcdef", at)
    assert not verify_totp(secret, "12345", at)


def test_recovery_codes_format_and_normalise() -> None:
    codes = new_recovery_codes(10)
    assert len(codes) == 10 and len(set(codes)) == 10
    for code in codes:
        groups = code.split("-")
        assert [len(g) for g in groups] == [4, 4, 4]
        # Lowercase with spaces hashes the same as the shown code.
        assert hash_token(normalise_recovery_code(code.lower().replace("-", " "))) == (
            hash_token(normalise_recovery_code(code))
        )


def test_otpauth_uri() -> None:
    uri = otpauth_uri("JBSWY3DPEHPK3PXP", "marc", "Stock Trading Agent")
    assert uri.startswith("otpauth://totp/Stock%20Trading%20Agent:marc")
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    assert "issuer=Stock%20Trading%20Agent" in uri


def test_create_or_reset_user(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Creates the row encrypted, and a reset wipes sessions and codes."""
    monkeypatch.setenv("SA_DATA_DIR", str(tmp_path / "data"))
    import app.config as config_module

    monkeypatch.setattr(config_module, "_config", None)
    config = get_config()
    clock = FakeClock(NOW)

    with get_session_maker()() as session:
        user, secret, codes = create_or_reset_user(
            session, config, clock, username="marc", password="fourteen chars ok"
        )
        assert user.username == "marc"
        assert len(codes) == 10
        assert user.totp_secret_encrypted != secret  # Fernet-encrypted
        assert verify_password(user.password_hash, "fourteen chars ok")
        assert len(user.recovery_code_hashes) == 10

        # A live session for the old credentials dies on reset.
        session.add(
            AuthSession(
                token_hash="x" * 64,
                device_name="Firefox",
                user_agent="",
                ip="127.0.0.1",
                created_at=NOW.replace(tzinfo=None),
                last_seen_at=NOW.replace(tzinfo=None),
                expires_at=(NOW + timedelta(days=30)).replace(tzinfo=None),
                keep_signed_in=True,
            )
        )
        session.commit()

        user2, secret2, codes2 = create_or_reset_user(
            session, config, clock, username="marc", password="new fourteen chars"
        )
        assert user2.id == user.id  # same single row
        assert secret2 != secret
        assert session.execute(select(AuthSession)).scalars().all() == []
        assert not verify_password(user2.password_hash, "fourteen chars ok")
        assert verify_password(user2.password_hash, "new fourteen chars")
        # The new TOTP secret actually verifies.
        assert verify_totp(secret2, totp_code(secret2, clock.now()), clock.now())

        with pytest.raises(ValueError, match="at least"):
            create_or_reset_user(
                session, config, clock, username="marc", password="short"
            )
