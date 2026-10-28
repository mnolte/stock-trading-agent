"""PR 24 review: backup download (db only, event + info email) and
write-only backup credentials with the destination-change rule."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import app.config as config_module
import pytest
from app.api.main import create_app
from app.auth.create_user import create_or_reset_user
from app.auth.passwords import totp_code
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import Event, Notification, Secret
from app.market.clock import FakeClock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

PASSWORD = "a fourteen char password"
NOW = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)


def _seed() -> str:
    with get_session_maker()() as session:
        _u, secret, _codes = create_or_reset_user(
            session,
            get_config(),
            FakeClock(NOW),
            username="marc",
            password=PASSWORD,
        )
    return secret


def _sign_in(client: TestClient, secret: str, confirm: bool = True) -> None:
    r1 = client.post("/api/auth/sign-in", json={"password": PASSWORD})
    assert r1.status_code == 200, r1.text
    clock: Any = cast(FastAPI, client.app).state.clock
    r2 = client.post(
        "/api/auth/code",
        json={"code": totp_code(secret, clock.now())},
    )
    assert r2.status_code == 200, r2.text
    if confirm:
        r3 = client.post(
            "/api/auth/confirm",
            json={"secret": totp_code(secret, clock.now())},
            headers={
                "X-CSRF-Token": client.cookies.get("sa_csrf") or ""
            },
        )
        assert r3.status_code == 200, r3.text


@pytest.fixture
def vps_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("SA_AUTH", "required")
    # https+public validation must not hit real DNS in tests
    monkeypatch.setattr(
        "app.ops.urls.socket.getaddrinfo",
        lambda host, *_a: [(0, 0, 0, "", ("93.184.216.34", 0))],
    )
    monkeypatch.setenv("SA_PUBLIC_URL", "https://stock.example.nl")
    monkeypatch.setattr(config_module, "_config", None)
    app = create_app()
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def _store_creds(app: FastAPI, endpoint: str = "https://s3.example.com") -> None:
    secrets = app.state.secrets_store
    secrets.save(
        "backup_credentials",
        json.dumps(
            {
                "endpoint": endpoint,
                "bucket": "sa-backups",
                "access_key_id": "AKIA-VERY-SECRET-KEY-ID",
                "secret_access_key": "THE-SECRET-ACCESS-KEY",
            }
        ),
    )
    secrets.save("backup_password", "repo-password-123")


def test_backup_download_records_event_and_email(vps_app: TestClient, tmp_path: Path) -> None:
    app = cast(FastAPI, vps_app.app)
    db = Path(get_config().env.sa_db_path)
    backups = db.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    payload = b"SQLite format 3\x00fake-backup"
    (backups / f"{db.name}.2026-10-15").write_bytes(payload)
    # a secret.key next to it must never be part of the download
    secret_bytes = b"SECRET-KEY-BYTES"
    (db.parent / "secret.key").write_bytes(secret_bytes)

    _sign_in(vps_app, _seed())
    r = vps_app.get("/api/server/backup-download")
    assert r.status_code == 200
    assert r.content == payload
    assert secret_bytes not in r.content

    events = list(get_session_maker()()
        .execute(select(Event).where(Event.event_type == "backup_downloaded"))
        .scalars()
        .all())
    assert events and events[0].level == "info"
    asyncio.run(app.state.notification_service.drain())
    rows = (
        get_session_maker()()
        .execute(
            select(Notification).where(Notification.event == "backup_downloaded")
        )
        .scalars()
        .all()
    )
    assert rows and "Backup downloaded" in rows[0].subject
    assert rows[0].severity == "info"


def test_backup_download_needs_confirmation(vps_app: TestClient, tmp_path: Path) -> None:
    db = Path(get_config().env.sa_db_path)
    backups = db.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    (backups / f"{db.name}.2026-10-15").write_bytes(b"x")
    _sign_in(vps_app, _seed(), confirm=False)
    r = vps_app.get("/api/server/backup-download")
    assert r.status_code == 403


def test_credentials_read_is_write_only(vps_app: TestClient) -> None:
    _store_creds(cast(FastAPI, vps_app.app))
    _sign_in(vps_app, _seed())
    r = vps_app.get("/api/server/backup-credentials")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["endpoint"] == "https://s3.example.com"
    assert body["bucket"] == "sa-backups"
    assert body["key_id_last4"] == "Y-ID"
    text = json.dumps(body)
    assert "VERY-SECRET-KEY-ID" not in text
    assert "SECRET-ACCESS-KEY" not in text
    assert "repo-password-123" not in text


def test_credentials_destination_change_needs_all_secrets(
    vps_app: TestClient,
) -> None:
    _store_creds(cast(FastAPI, vps_app.app))
    _sign_in(vps_app, _seed())
    # endpoint changed, no credentials in the request → 422 password_required
    r = vps_app.post(
        "/api/server/backup-credentials",
        json={
            "endpoint": "https://other.example.com",
            "bucket": "sa-backups",
        },
        headers={"X-CSRF-Token": vps_app.cookies.get("sa_csrf") or ""},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "password_required"
    # nothing was saved over the old credentials
    with get_session_maker()() as session:
        row = session.execute(
            select(Secret).where(Secret.name == "backup_credentials")
        ).scalar_one()
    assert "s3.example.com" not in row.last4  # value untouched
    assert cast(FastAPI, vps_app.app).state.secrets_store.get("backup_credentials") is not None
    creds = json.loads(
        cast(FastAPI, vps_app.app).state.secrets_store.get("backup_credentials") or "{}"
    )
    assert creds["endpoint"] == "https://s3.example.com"


def test_credentials_same_destination_updates(vps_app: TestClient) -> None:
    _store_creds(cast(FastAPI, vps_app.app))
    _sign_in(vps_app, _seed())
    r = vps_app.post(
        "/api/server/backup-credentials",
        json={
            "endpoint": "https://s3.example.com",
            "bucket": "sa-backups",
            "access_key_id": "AKIA-NEW-KEY-ID",
            "secret_access_key": "NEW-SECRET",
            "password": "new-repo-password",
        },
        headers={"X-CSRF-Token": vps_app.cookies.get("sa_csrf") or ""},
    )
    assert r.status_code == 200, r.text
    creds = json.loads(
        cast(FastAPI, vps_app.app).state.secrets_store.get("backup_credentials") or "{}"
    )
    assert creds["access_key_id"] == "AKIA-NEW-KEY-ID"


def test_credentials_save_needs_confirmation(vps_app: TestClient) -> None:
    _sign_in(vps_app, _seed(), confirm=False)
    r = vps_app.post(
        "/api/server/backup-credentials",
        json={
            "endpoint": "https://s3.example.com",
            "bucket": "b",
            "access_key_id": "a",
            "secret_access_key": "s",
            "password": "p",
        },
        headers={"X-CSRF-Token": vps_app.cookies.get("sa_csrf") or ""},
    )
    assert r.status_code == 403
