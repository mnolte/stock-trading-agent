"""PR 24: offsite backups, heartbeat and server checks — all offline."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.config import get_config
from app.market.clock import FakeClock
from app.ops import offsite
from app.ops.service import OpsService
from app.ops.urls import (
    UrlRejected,
    resolve_public,
    validate_public_https_url,
)

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


# --- resolve_public: the check that is also the use (item 085) ---------


def test_resolve_public_returns_the_checked_addresses() -> None:
    assert resolve_public("example.com", resolver=_public_resolver) == [
        "93.184.216.34"
    ]


def test_resolve_public_refuses_private_and_loopback() -> None:
    with pytest.raises(UrlRejected, match="private address"):
        resolve_public("internal.example", resolver=_private_resolver)
    with pytest.raises(UrlRejected, match="private address"):
        resolve_public("127.0.0.1")
    with pytest.raises(UrlRejected):
        resolve_public("does-not-exist.invalid", resolver=_failing_resolver)


def _failing_resolver(host: str, *_a: Any) -> list[Any]:
    raise OSError("NXDOMAIN")


def test_resolve_public_allow_private_still_refuses_loopback() -> None:
    """security.allow_private_smtp lets a private relay through — but
    never loopback or an unnamed address."""
    assert resolve_public(
        "smtp.lan", resolver=_private_resolver, allow_private=True
    ) == ["10.0.0.4"]
    assert resolve_public("10.0.0.2", allow_private=True) == ["10.0.0.2"]
    with pytest.raises(UrlRejected, match="private address"):
        resolve_public("127.0.0.1", allow_private=True)
    with pytest.raises(UrlRejected, match="private address"):
        resolve_public("::1", allow_private=True)
    with pytest.raises(UrlRejected, match="private address"):
        resolve_public("169.254.169.254", allow_private=True)


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

    async def pinger(url: str) -> bool:
        calls.append(url)
        return True

    ops = _ops(
        tmp_path,
        values={"heartbeat_url": "https://hc-ping.com/abc123f9a"},
        pinger=pinger,
    )

    async def run() -> None:
        assert await ops.ping_heartbeat()
        assert await ops.ping_heartbeat()

    asyncio.run(run())
    assert calls == ["https://hc-ping.com/abc123f9a"] * 2
    assert ops.last_heartbeat_at is not None


def test_heartbeat_masked_shows_host_and_last4(tmp_path: Path) -> None:
    ops = _ops(
        tmp_path, values={"heartbeat_url": "https://hc-ping.com/abc123f9a"}
    )
    assert ops.heartbeat_masked() == "hc-ping.com/••••3f9a"
    assert ops.heartbeat_url() == "https://hc-ping.com/abc123f9a"


def test_heartbeat_config_value_is_ignored(tmp_path: Path) -> None:
    """ops.heartbeat_url in config no longer feeds the ping (item 081)."""
    ops = _ops(tmp_path)
    get_config()._active.setdefault("ops", {})["heartbeat_url"] = (
        "https://hc-ping.com/from-config"
    )
    try:
        assert ops.heartbeat_url() is None
        assert ops.heartbeat_masked() is None
    finally:
        get_config()._active["ops"]["heartbeat_url"] = ""


def test_heartbeat_not_called_without_url(tmp_path: Path) -> None:
    import asyncio

    calls: list[str] = []

    async def pinger(url: str) -> bool:
        calls.append(url)
        return True

    ops = _ops(tmp_path, pinger=pinger)
    assert not asyncio.run(ops.ping_heartbeat())
    assert calls == []


def test_heartbeat_connects_to_the_checked_address(tmp_path: Path) -> None:
    """The ping resolves at each use and connects to that address with
    the original Host and SNI — the checked lookup is the used lookup
    (item 085). No real socket: a MockTransport records the request."""
    import asyncio

    import httpx

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    ops = _ops(
        tmp_path,
        values={"heartbeat_url": "https://hc-ping.com:8443/abc123?x=1"},
        resolver=_public_resolver,
        ping_transport=httpx.MockTransport(handler),
    )
    assert asyncio.run(ops.ping_heartbeat())
    (request,) = seen
    assert request.url.host == "93.184.216.34"
    assert request.url.port == 8443
    assert request.url.path == "/abc123"
    assert request.url.query == b"x=1"
    assert request.headers["host"] == "hc-ping.com:8443"
    assert request.extensions["sni_hostname"] == "hc-ping.com"


def test_heartbeat_refused_when_name_turns_private(tmp_path: Path) -> None:
    """Public when saved, private at ping: the ping fails before any
    connection is attempted."""
    import asyncio

    import httpx

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    ops = _ops(
        tmp_path,
        values={"heartbeat_url": "https://hc-ping.com/abc123"},
        resolver=_private_resolver,
        ping_transport=transport,
    )
    assert not asyncio.run(ops.ping_heartbeat())
    assert calls == []
    assert ops.last_heartbeat_at is None


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


def test_backup_check_warns_on_unparseable_restore_test_at(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored restore_test.at that is not a timestamp still logs the
    warning — only the 'not set up' override skips the parse (item 054)."""
    ops = _ops(
        tmp_path,
        values={"backup_password": "pw", "backup_credentials": "{}"},
    )
    ops._status["restore_test"] = {"at": "not-a-date", "ok": True}
    with caplog.at_level("WARNING", logger="app.ops.service"):
        ops.check_backups()
    assert any(
        "unparseable restore_test.at" in r.getMessage() for r in caplog.records
    )


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


def test_credentials_private_at_run_refused(tmp_path: Path) -> None:
    """Public when saved, private when the backup runs: load_credentials
    resolves again and the run fails with OffsiteError (item 085)."""
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
    # Fine at save time …
    assert offsite.load_credentials(
        secrets, resolver=_public_resolver  # type: ignore[arg-type]
    )
    # … refused at use.
    with pytest.raises(offsite.OffsiteError, match="private address"):
        offsite.load_credentials(secrets, resolver=_private_resolver)  # type: ignore[arg-type]


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


# --- clock check -----------------------------------------------------------

def test_clock_check_reads_data_client(tmp_path: Path) -> None:
    """The check reads the data client's fetch_clock — the broker
    interface has no clock — and stores the measured offset."""
    import asyncio
    from datetime import timedelta

    from app.data.models import DataResult, MarketClock

    class _Data:
        async def fetch_clock(self) -> DataResult[MarketClock | None]:
            return DataResult(
                data=MarketClock(
                    is_open=True,
                    timestamp=NOW + timedelta(milliseconds=250),
                    next_open=NOW,
                    next_close=NOW,
                ),
                source="fake",
            )

    ops = _ops(tmp_path, data_client=_Data())
    result = asyncio.run(ops.check_clock())
    assert result["offset_ms"] is not None
    assert result["level"] == "ok"
    assert ops.status["clock"]["offset_ms"] == result["offset_ms"]


def test_clock_check_failing_notifies_once_per_session(
    tmp_path: Path,
) -> None:
    """A failing check sends "Clock check failing" once per New York
    session, not every hourly run."""
    import asyncio

    from app.data.models import DataResult

    class _Data:
        async def fetch_clock(self) -> DataResult[None]:
            return DataResult(data=None, error="unreachable", source="fake")

    class _Notifier:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def notify(self, event: str, line: str, **_kw: Any) -> None:
            self.calls.append(event)

    notifier = _Notifier()
    ops = _ops(tmp_path, data_client=_Data(), notifier=notifier)
    asyncio.run(ops.check_clock())
    asyncio.run(ops.check_clock())
    assert notifier.calls == ["clock_check_failing"]


def test_clock_check_failure_marks_offset_unknown(tmp_path: Path) -> None:
    """The check itself failing leaves offset_ms null — the GUI must not
    show a stale good value."""
    import asyncio

    from app.data.models import DataResult

    class _Data:
        async def fetch_clock(self) -> DataResult[None]:
            return DataResult(data=None, error="unreachable", source="fake")

    ops = _ops(tmp_path, data_client=_Data())
    result = asyncio.run(ops.check_clock())
    assert result["offset_ms"] is None
    assert result["level"] == "unknown"


def test_clock_check_failure_keeps_strikes(tmp_path: Path) -> None:
    """A failed check proves nothing either way — it must not reset the
    strike count, or alternating failure/overshoot never warns."""
    import asyncio
    from datetime import timedelta

    from app.data.models import DataResult, MarketClock

    clock_time = NOW + timedelta(seconds=30)

    class _Data:
        fail = False

        async def fetch_clock(self) -> DataResult[MarketClock | None]:
            if self.fail:
                return DataResult(data=None, error="unreachable", source="fake")
            return DataResult(
                data=MarketClock(
                    is_open=True,
                    timestamp=clock_time,
                    next_open=NOW,
                    next_close=NOW,
                ),
                source="fake",
            )

    data = _Data()
    ops = _ops(tmp_path, data_client=data)
    assert asyncio.run(ops.check_clock())["strikes"] == 1
    data.fail = True
    failed = asyncio.run(ops.check_clock())
    assert failed["level"] == "unknown"
    assert failed["strikes"] == 1  # kept, not reset
    data.fail = False
    assert asyncio.run(ops.check_clock())["strikes"] == 2
    assert asyncio.run(ops.check_clock())["level"] == "warning"


def test_clock_check_single_over_reports_ok(tmp_path: Path) -> None:
    """One check over the threshold is a strike, not 'unknown' — the
    offset was measured fine."""
    import asyncio
    from datetime import timedelta

    from app.data.models import DataResult, MarketClock

    class _Data:
        async def fetch_clock(self) -> DataResult[MarketClock | None]:
            return DataResult(
                data=MarketClock(
                    is_open=True,
                    timestamp=NOW + timedelta(seconds=30),
                    next_open=NOW,
                    next_close=NOW,
                ),
                source="fake",
            )

    ops = _ops(tmp_path, data_client=_Data())
    result = asyncio.run(ops.check_clock())
    assert result["strikes"] == 1
    assert result["level"] == "ok"


def test_backup_check_ignores_non_dated_siblings(tmp_path: Path) -> None:
    """Only `<db>.YYYY-MM-DD` files count — a fresher `.tmp` sibling must
    not fake freshness."""
    ops = _ops(tmp_path)
    get_config().env.sa_db_path = str(tmp_path / "stock-agent.db")
    backups = tmp_path / "backups"
    backups.mkdir(exist_ok=True)
    (backups / "stock-agent.db.tmp").write_text("x")
    result = ops.check_backups()
    assert result["local"]["latest"] is None
    assert result["local"]["level"] == "warning"


def test_cert_check_uses_url_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A public URL on a non-443 port must check that port, not 443."""
    import ssl

    seen: list[tuple[str, int]] = []

    def fake_get_server_certificate(addr: tuple[str, int], timeout: int) -> str:
        seen.append(addr)
        raise ssl.SSLError("stop here")

    monkeypatch.setattr(
        ssl, "get_server_certificate", fake_get_server_certificate
    )
    ops = _ops(tmp_path)
    get_config().env.sa_public_url = "https://stock.example.nl:8443"
    assert ops.check_cert()["level"] == "unknown"
    assert seen == [("stock.example.nl", 8443)]


def test_restore_test_old_failure_masked_when_not_set_up(
    tmp_path: Path,
) -> None:
    """Without backup credentials an earlier restore-test failure stays
    in the stored history but never shows as the current status —
    Settings reads 'not set up' (item 058)."""
    ops = _ops(tmp_path)
    ops._status["restore_test"] = {
        "at": "2026-11-01T00:00:00+00:00",
        "ok": False,
    }
    ops._status["backups"] = {
        "restore_test": {"at": "2026-11-01T00:00:00+00:00", "ok": False}
    }
    assert ops.status["restore_test"]["not_set_up"] is True
    backups = ops.check_backups()
    assert backups["restore_test"] == {"not_set_up": True}
    assert backups["offsite"] == {"not_set_up": True}
    # The stored failure is kept as the history, not deleted.
    assert ops._status["restore_test"]["ok"] is False


# --- delete protection (item 084) ----------------------------------------


def _creds() -> offsite.OffsiteCredentials:
    return offsite.OffsiteCredentials(
        endpoint="https://s3.example.com",
        bucket="backups-b1",
        access_key_id="AKIDEXAMPLE",
        secret_access_key="secret",
        password="pw",
    )


class _Resp:
    def __init__(self, code: int, text: str = "") -> None:
        self.status_code = code
        self.text = text


_VERSIONING_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<VersioningConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    "<Status>{}</Status></VersioningConfiguration>"
)


def test_delete_protection_on_off_and_unknown() -> None:
    seen: dict[str, Any] = {}

    def get(url: str, headers: dict[str, str]) -> _Resp:
        seen["url"] = url
        seen["headers"] = headers
        return _Resp(200, _VERSIONING_XML.format("Enabled"))

    assert offsite.bucket_delete_protection(_creds(), NOW, get=get) == "on"
    # One signed path-style request: endpoint/bucket?versioning, SigV4.
    assert seen["url"] == "https://s3.example.com/backups-b1?versioning"
    auth = seen["headers"]["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in auth
    assert seen["headers"]["x-amz-date"] == "20261028T120000Z"

    def suspended(url: str, headers: dict[str, str]) -> _Resp:
        return _Resp(200, _VERSIONING_XML.format("Suspended"))

    def forbidden(url: str, headers: dict[str, str]) -> _Resp:
        return _Resp(403, "<Error/>")

    def garbage(url: str, headers: dict[str, str]) -> _Resp:
        return _Resp(200, "not xml")

    assert offsite.bucket_delete_protection(_creds(), NOW, get=suspended) == "off"
    assert offsite.bucket_delete_protection(_creds(), NOW, get=forbidden) == "unknown"
    assert offsite.bucket_delete_protection(_creds(), NOW, get=garbage) == "unknown"

    def boom(url: str, headers: dict[str, str]) -> _Resp:
        raise OSError("network down")

    assert offsite.bucket_delete_protection(_creds(), NOW, get=boom) == "unknown"


def test_delete_protection_bad_bucket_is_unknown() -> None:
    creds = offsite.OffsiteCredentials(
        endpoint="https://s3.example.com",
        bucket="../escape",
        access_key_id="a",
        secret_access_key="s",
        password="p",
    )
    def get_ok(url: str, headers: dict[str, str]) -> _Resp:
        return _Resp(200)

    assert offsite.bucket_delete_protection(creds, NOW, get=get_ok) == "unknown"


def test_restore_test_stores_delete_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The monthly check records on/off/unknown with the result."""
    import asyncio
    import sqlite3

    values = {
        "backup_password": "pw",
        "backup_credentials": json.dumps(
            {
                "endpoint": "https://s3.example.com",
                "bucket": "backups-b1",
                "access_key_id": "a",
                "secret_access_key": "s",
            }
        ),
    }
    ops = _ops(tmp_path, values=values)
    monkeypatch.setattr(
        "app.ops.service.load_credentials", lambda secrets, resolver=None: _creds()
    )
    monkeypatch.setattr(
        "app.ops.service.bucket_delete_protection",
        lambda creds, now: "on",
    )

    def fake_restore(config: Any, secrets: Any, target: Path) -> Path:
        target.mkdir(parents=True, exist_ok=True)
        db = target / "stock-agent.db.2026-10-28"
        with sqlite3.connect(db) as conn:
            conn.execute("create table t (x int)")
        return db

    monkeypatch.setattr("app.ops.service.restore_latest", fake_restore)
    assert asyncio.run(ops.run_restore_test()) is True
    stored = ops.status["restore_test"]
    assert stored["ok"] is True
    assert stored["delete_protection"] == "on"
