"""PR 24: offsite backups, heartbeat and server checks — all offline."""

from __future__ import annotations

import json
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.config import get_config
from app.market.clock import FakeClock
from app.ops.service import OpsService
from app.ops.urls import UrlRejected, validate_public_https_url
from app.ops import offsite
from datetime import UTC, datetime

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


class _Secrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._v = values

    def get(self, name: str) -> str | None:
        return self._v.get(name)


def _ops(tmp_path: Path, values: dict[str, str] | None = None, **kw: Any) -> OpsService:
    config = get_config()
    config.env.sa_data_dir = str(tmp_path)
    (tmp_path).mkdir(parents=True, exist_ok=True)
    return OpsService(
        config,
        FakeClock(NOW),
        secrets=_Secrets(values or {}),  # type: ignore[arg-type]
        **kw,
    )


def _public_resolver(host: str, *_a: Any) -> list[Any]:
    return [(0, 0, 0, "", ("93.184.216.34", 0))]


def _private_resolver(host: str, *_a: Any) -> list[Any]:
    return [(0, 0, 0, "", ("10.0.0.4", 0))]


# --- URL validation ----------------------------------------------------

def test_https_public_url_accepted() -> None:
    assert (
        validate_public_https_url("https://hc-ping.com/abc", resolver=_public_resolver)
        == "https://hc-ping.com/abc"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://hc-ping.com/abc",          # not https
        "https://localhost/abc",           # loopback name
        "https://10.0.0.4/abc",            # private ip
        "https://127.0.0.1/abc",
        "https://192.168.1.9/x",
        "https://host.internal/x",
        "https://user:pw@x.example.com/y", # embedded credentials
    ],
)
def test_bad_urls_rejected(url: str) -> None:
    with pytest.raises(UrlRejected):
        validate_public_https_url(url, resolver=_public_resolver)


def test_private_resolution_rejected() -> None:
    with pytest.raises(UrlRejected):
        validate_public_https_url("https://evil.example/x", resolver=_private_resolver)


def test_heartbeat_url_validated(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    assert ops.validate_heartbeat_url(
        "https://hc-ping.com/x", resolver=_public_resolver
    )
    with pytest.raises(ValueError):
        ops.validate_heartbeat_url("http://hc-ping.com/x", resolver=_public_resolver)


# --- heartbeat -----------------------------------------------------------

async def _true() -> bool:
    return True


def test_heartbeat_called_once_per_unit(tmp_path: Path) -> None:
    import asyncio

    calls: list[str] = []
    ops = _ops(tmp_path, pinger=lambda url: calls.append(url) or _true())
    get_config()._active.setdefault("ops", {})["heartbeat_url"] = "https://hc-ping.com/x"

    async def run() -> None:
        assert await ops.ping_heartbeat()
        assert await ops.ping_heartbeat()

    asyncio.run(run())
    assert calls == ["https://hc-ping.com/x"] * 2
    assert ops.last_heartbeat_at is not None


def test_heartbeat_not_called_without_url(tmp_path: Path) -> None:
    import asyncio

    calls: list[str] = []
    ops = _ops(tmp_path, pinger=lambda url: calls.append(url))
    get_config()._active.setdefault("ops", {})["heartbeat_url"] = ""
    assert not asyncio.run(ops.ping_heartbeat())
    assert calls == []


# --- server checks -------------------------------------------------------

def test_disk_check_levels(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    result = ops.check_disk()
    assert result["level"] in {"ok", "warning", "critical"}
    assert 0 < result["pct"] <= 100
    assert ops.status["disk"]["pct"] == result["pct"]


def test_backup_check_warns_without_fresh_copy(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    get_config().env.sa_db_path = str(tmp_path / "stock-agent.db")
    result = ops.check_backups()
    assert result["local"]["level"] == "warning"
    fresh = tmp_path / "backups"
    fresh.mkdir(exist_ok=True)
    (fresh / "stock-agent.db.2026-10-28").write_text("x")
    result = ops.check_backups()
    assert result["local"]["level"] == "ok"


def test_status_persists_to_disk(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    ops.check_disk()
    again = OpsService(get_config(), FakeClock(NOW))
    again._status_path = tmp_path / "server-status.json"
    again._status = again._load()
    assert "disk" in again.status
    assert stat.S_IMODE((tmp_path / "server-status.json").stat().st_mode) == 0o600


# --- offsite credentials ---------------------------------------------------

def test_credentials_require_both_secrets(tmp_path: Path) -> None:
    with pytest.raises(offsite.OffsiteError):
        offsite.load_credentials(_Secrets({}))  # type: ignore[arg-type]


def test_credentials_endpoint_revalidated(tmp_path: Path) -> None:
    secrets = _Secrets(
        {
            "backup_password": "pw",
            "backup_credentials": json.dumps(
                {
                    "endpoint": "http://insecure.example",
                    "bucket": "b",
                    "access_key_id": "a",
                    "secret_access_key": "s",
                }
            ),
        }
    )
    with pytest.raises(offsite.OffsiteError):
        offsite.load_credentials(secrets, resolver=_public_resolver)  # type: ignore[arg-type]


def test_credentials_roundtrip(tmp_path: Path) -> None:
    secrets = _Secrets(
        {
            "backup_password": "pw",
            "backup_credentials": json.dumps(
                {
                    "endpoint": "https://s3.example.com",
                    "bucket": "b",
                    "access_key_id": "a",
                    "secret_access_key": "s",
                }
            ),
        }
    )
    creds = offsite.load_credentials(secrets, resolver=_public_resolver)  # type: ignore[arg-type]
    assert creds.endpoint == "https://s3.example.com"
    env = offsite._restic_env(creds)
    assert env["RESTIC_PASSWORD"] == "pw"
    assert env["AWS_ACCESS_KEY_ID"] == "a"
