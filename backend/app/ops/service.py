"""Server checks and the heartbeat (docs/SPEC.md → VPS deployment →
Monitoring).

- After every cycle and position check the app pings
  ``ops.heartbeat_url`` — the dead-man's-switch service that still
  alerts Marc when the whole server is down.
- Checks run hourly: clock offset (against the broker's clock), disk use
  on the data volume (warning 80%, critical 90%), HTTPS certificate
  expiry (14 days), local backup age, the monthly restore test, and OS
  updates (written by the host into ``/data/host-status.json`` when
  provision.sh's cron drops it there).
- Latest results persist in ``data/server-status.json`` so the Server
  section survives a restart; failures also raise notifications with the
  spec's severities.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import ssl
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse

import httpx

from app.db.backup import dated_backups
from app.errors import safe_error
from app.market.clock import NY_TZ
from app.ops.offsite import (
    OffsiteError,
    bucket_delete_protection,
    load_credentials,
    restore_latest,
)
from app.ops.urls import UrlRejected, validate_public_https_url

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.data.models import DataResult, MarketClock
    from app.market.clock import Clock
    from app.notifications.service import NotificationService
    from app.secrets_service import SecretsStore


class ClockSource(Protocol):
    """What ``check_clock`` needs: Alpaca's ``/v2/clock`` through the data
    client (the broker interface has no clock)."""

    async def fetch_clock(self) -> DataResult[MarketClock | None]: ...

logger = logging.getLogger(__name__)

_STATUS_NAME = "server-status.json"


class OpsService:
    def __init__(
        self,
        config: AppConfig,
        clock: Clock,
        *,
        notifier: NotificationService | None = None,
        secrets: SecretsStore | None = None,
        data_client: ClockSource | None = None,
        pinger: Any = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._notifier = notifier
        self._secrets = secrets
        self._data_client = data_client
        self._pinger = pinger or self._http_ping
        self._status_path = Path(config.env.sa_data_dir) / _STATUS_NAME
        self._status: dict[str, Any] = self._load()
        self.last_heartbeat_at: str | None = self._status.get("heartbeat_at")

    # ------------------------------------------------------------------
    # durable status

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._status_path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self._status_path.write_text(json.dumps(self._status))
            self._status_path.chmod(0o600)
        except OSError:
            logger.warning("could not write %s", self._status_path)

    @property
    def status(self) -> dict[str, Any]:
        data = dict(self._status)
        if self._creds_missing():
            # "Not set up" is the current state, not an old run's stored
            # result: an earlier failure stays in ``_status`` as the
            # history but is never shown as current (item 058).
            backups = dict(data.get("backups") or {})
            backups["offsite"] = {"not_set_up": True}
            backups["restore_test"] = {"not_set_up": True}
            data["backups"] = backups
            data["offsite_backup"] = {"not_set_up": True}
            data["restore_test"] = {"not_set_up": True}
        return data

    def _set(self, key: str, value: Any) -> None:
        self._status[key] = value
        self._save()

    def _notify(self, event: str, line: str, *, severity: str | None = None) -> None:
        if self._notifier is not None:
            self._notifier.notify(
                event, line, link="/settings", severity=severity
            )

    # ------------------------------------------------------------------
    # heartbeat

    def heartbeat_url(self) -> str | None:
        """The dead-man's-switch URL — a write-only secret (item 081).

        ``ops.heartbeat_url`` in config is imported into secrets once at
        startup and ignored afterwards; nothing else may read it.
        """
        if self._secrets is None:
            return None
        return self._secrets.get("heartbeat_url")

    def heartbeat_masked(self) -> str | None:
        """The only form of the URL the API may show: host, mask, last 4."""
        url = self.heartbeat_url()
        if not url:
            return None
        host = urlparse(url).netloc or urlparse(url).path
        return f"{host}/••••{url[-4:]}"

    def validate_heartbeat_url(self, url: str, resolver: object = None) -> str:
        """https + public — the rule from Stored credentials."""
        try:
            return validate_public_https_url(url, resolver=resolver)
        except UrlRejected as exc:
            raise ValueError(f"heartbeat_url {safe_error(exc)}") from exc

    async def _http_ping(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url)
            # 4xx is a miss too — a wrong URL returning 404 must not
            # read as a live heartbeat.
            return response.status_code < 400
        except httpx.HTTPError:
            return False

    async def ping_heartbeat(self) -> bool:
        """One ping after a cycle or check; failures are logged, not raised."""
        url = self.heartbeat_url()
        if not url:
            return False
        ok = await self._pinger(url)
        if ok:
            self.last_heartbeat_at = self._clock.now().isoformat()
            self._set("heartbeat_at", self.last_heartbeat_at)
            logger.debug("heartbeat ping sent")
        else:
            logger.warning("heartbeat ping failed (url configured)")
        return ok

    # ------------------------------------------------------------------
    # checks

    def check_disk(self) -> dict[str, Any]:
        usage = shutil.disk_usage(self._config.env.sa_data_dir)
        pct = round(usage.used * 100 / usage.total, 1) if usage.total else 0.0
        warn = int(self._config.get("ops", "disk_warn_pct", default=80))
        crit = int(self._config.get("ops", "disk_critical_pct", default=90))
        level = "ok"
        if pct >= crit:
            level = "critical"
            self._notify(
                "disk_space",
                f"data volume {pct:.0f}% full — {usage.free / 1e9:.1f} GB free",
                severity="critical",
            )
        elif pct >= warn:
            level = "warning"
            self._notify(
                "disk_space",
                f"data volume {pct:.0f}% full — {usage.free / 1e9:.1f} GB free",
            )
        result = {
            "pct": pct,
            "free_gb": round(usage.free / 1e9, 1),
            "total_gb": round(usage.total / 1e9, 1),
            "level": level,
        }
        self._set("disk", result)
        return result

    async def check_clock(self) -> dict[str, Any]:
        """Offset vs the broker clock, read through the data client's
        ``fetch_clock`` (chrony keeps the host; the offset is what matters
        to cycles). Simulated mode: no reference. A failing check notifies
        "Clock check failing" once per New York session."""
        max_off = float(
            self._config.get("ops", "clock_max_offset_seconds", default=1)
        )
        offset_ms: float | None = None
        check_failed = False
        if self._data_client is not None:
            remote: datetime | None = None
            try:
                started = time.monotonic()
                clock_result = await self._data_client.fetch_clock()
                rtt = time.monotonic() - started
                if clock_result.error is not None:
                    logger.warning(
                        "clock check failed: %s", clock_result.error
                    )
                elif clock_result.data is not None:
                    remote = clock_result.data.timestamp
            except Exception as exc:  # noqa: BLE001
                logger.warning("clock check failed: %s", exc)
            if remote is None:
                check_failed = True
            else:
                # subtract half the round-trip: the timestamp is one-way stale
                offset_ms = abs(
                    (remote - self._clock.now()).total_seconds() * 1000
                    - (rtt * 1000) / 2
                )
        if check_failed:
            self._notify_clock_failure()
        # Half the round-trip is the one-way error budget on the broker
        # timestamp; warn only after 3 consecutive checks over the
        # threshold so one skewed response does not page Marc. A failed
        # check proves nothing either way — keep the strike count.
        strikes = int(self._status.get("clock_strikes", 0))
        over = offset_ms is not None and offset_ms / 1000 > max_off
        if check_failed:
            level = "unknown"
        elif over:
            strikes += 1
            level = "warning" if strikes >= 3 else "ok"
        else:
            strikes = 0
            level = "ok"
        self._status["clock_strikes"] = strikes
        if over and strikes >= 3 and offset_ms is not None:
            self._notify(
                "clock_offset",
                f"clock off by {offset_ms / 1000:.2f}s for {strikes} checks — "
                "cycles depend on the exact time",
            )
        result = {
            "offset_ms": offset_ms,
            "level": level,
            "strikes": strikes,
        }
        self._set("clock", result)
        return result

    def _notify_clock_failure(self) -> None:
        """"Clock check failing" fires once per New York session, not
        every hourly check (docs/SPEC.md, Notifications)."""
        today = self._clock.now().astimezone(NY_TZ).date().isoformat()
        if self._status.get("clock_fail_notified") == today:
            return
        self._status["clock_fail_notified"] = today
        self._save()
        self._notify(
            "clock_check_failing",
            "the hourly clock check could not read Alpaca's clock",
        )

    def check_cert(self) -> dict[str, Any]:
        """Days until the public certificate expires (14-day warning)."""
        warn_days = int(self._config.get("ops", "cert_warn_days", default=14))
        public = self._config.env.sa_public_url
        parsed = urlparse(public)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host:
            unknown2: dict[str, Any] = {"expires": None, "days_left": None, "level": "unknown"}
            self._set("cert", unknown2)
            return unknown2
        try:
            pem = ssl.get_server_certificate(
                (host, parsed.port or 443), timeout=10
            )
            from cryptography import x509

            cert = x509.load_pem_x509_certificate(pem.encode())
            expires = cert.not_valid_after_utc
            days = (expires - self._clock.now()).days
        except (ssl.SSLError, OSError, ValueError, KeyError) as exc:
            logger.warning("certificate check failed: %s", exc)
            unknown3: dict[str, Any] = {"expires": None, "days_left": None, "level": "unknown"}
            self._set("cert", unknown3)
            return unknown3
        level = "ok"
        if days <= warn_days:
            level = "warning"
            self._notify(
                "cert_expiring",
                f"HTTPS certificate expires in {days} days ({expires.date()})",
            )
        result: dict[str, Any] = {
            "expires": expires.date().isoformat(),
            "days_left": days,
            "level": level,
        }
        self._set("cert", result)
        return result

    def check_backups(self) -> dict[str, Any]:
        """Local copy age + offsite/restore-test staleness."""
        stale_hours = int(self._config.get("ops", "backup_stale_hours", default=36))
        db = Path(self._config.env.sa_db_path)
        backups = sorted(
            dated_backups(db.parent / "backups", db.name),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
        )
        latest = backups[-1] if backups else None
        age_hours = (
            (self._clock.now().timestamp() - latest.stat().st_mtime) / 3600
            if latest is not None
            else None
        )
        level = "ok"
        if latest is None or (age_hours is not None and age_hours > stale_hours):
            level = "warning"
            self._notify(
                "backup_failed",
                "no fresh local backup — the 03:00 copy may be failing",
            )
        offsite = self._status.get("offsite_backup")
        restore_test = self._status.get("restore_test")
        if self._creds_missing():
            # "not set up" — never a failure notification (item 050).
            offsite = {"not_set_up": True}
            restore_test = {"not_set_up": True}
        stale_days = int(
            self._config.get("ops", "restore_test_stale_days", default=45)
        )
        if restore_test and not restore_test.get("not_set_up"):
            ran = restore_test.get("at")
            try:
                ran_at = datetime.fromisoformat(str(ran))
                if (self._clock.now() - ran_at).days > stale_days:
                    restore_test = {**restore_test, "level": "warning"}
            except (TypeError, ValueError):
                logger.warning(
                    "unparseable restore_test.at %r — ignored", ran
                )
        result = {
            "local": {
                "latest": latest.name if latest else None,
                "age_hours": round(age_hours, 1) if age_hours is not None else None,
                "level": level,
            },
            "offsite": offsite,
            "restore_test": restore_test,
        }
        self._set("backups", result)
        return result

    def host_status(self) -> dict[str, Any]:
        """OS update status written by the host cron (provision.sh)."""
        path = Path(self._config.env.sa_data_dir) / "host-status.json"
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def record_startup_reconcile(self, ok: bool, mismatches: int) -> None:
        """Persist the startup reconciliation outcome so deploy.sh can
        read it back read-only via ``python -m app.ops.reconcile_check``."""
        self._set(
            "startup_reconcile",
            {
                "ok": ok,
                "mismatches": mismatches,
                "at": self._clock.now().isoformat(),
            },
        )

    def _creds_missing(self) -> bool:
        """Offsite backup needs both stored secrets (docs/SPEC.md,
        Backups). Without them the state is 'not set up' — no failure,
        no notification."""
        if self._secrets is None:
            return True
        return not (
            self._secrets.get("backup_password")
            and self._secrets.get("backup_credentials")
        )

    # ------------------------------------------------------------------
    # offsite jobs

    def run_offsite_backup_sync(self) -> None:
        """The restic upload — sync so the 03:00 job can chain it."""
        from app.ops.offsite import offsite_backup

        if self._secrets is None:
            return
        if self._creds_missing():
            # Not set up — nothing ran, so the stored last-run result
            # stays as the history (item 058).
            return
        try:
            offsite_backup(self._config, self._secrets)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, OffsiteError):
                # Expected failure — its message is written for Marc;
                # a traceback would bury the reason.
                logger.warning("offsite backup failed: %s", exc)
            else:
                logger.exception("offsite backup failed")
            self._set(
                "offsite_backup",
                {"at": self._clock.now().isoformat(), "ok": False, "error": safe_error(exc)},
            )
            raise  # the caller records the backup_failed event + alert
        self._set(
            "offsite_backup", {"at": self._clock.now().isoformat(), "ok": True}
        )

    async def run_offsite_backup(self) -> None:
        try:
            await asyncio.to_thread(self.run_offsite_backup_sync)
        except Exception as exc:  # noqa: BLE001
            self._notify("backup_failed", f"offsite backup failed ({safe_error(exc)})")

    async def run_restore_test(self) -> bool:
        """Monthly: restore the newest offsite snapshot into a temp folder
        and run PRAGMA integrity_check on it."""
        if self._secrets is None:
            return False
        if self._creds_missing():
            # Not set up — nothing ran, so the stored last-run result
            # stays as the history (item 058).
            return False
        import sqlite3
        import tempfile

        # Item 084: ask the bucket whether versioning protects the
        # backups (one signed request) — stored with the result, shown
        # as "Delete protection" in Settings → Server. Never blocks.
        delete_protection = "unknown"
        try:
            creds = load_credentials(self._secrets)
            delete_protection = await asyncio.to_thread(
                bucket_delete_protection, creds, self._clock.now()
            )
        except OffsiteError:
            # Unreadable credentials — the restore reports that itself.
            logger.debug("delete-protection check skipped: no usable credentials")

        target_root: Path | None = None
        try:
            target_root = Path(tempfile.mkdtemp(prefix="sa-restore-test-"))
            db = await asyncio.to_thread(
                restore_latest, self._config, self._secrets, target_root
            )
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                (integrity,) = conn.execute("PRAGMA integrity_check").fetchone()
            ok = integrity == "ok"
            self._set(
                "restore_test",
                {
                    "at": self._clock.now().isoformat(),
                    "ok": ok,
                    "delete_protection": delete_protection,
                },
            )
            if not ok:
                self._notify("backup_failed", "restore test failed integrity_check")
            else:
                logger.info("monthly restore test ok")
            return bool(ok)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, OffsiteError):
                # Expected failure — its message is written for Marc;
                # a traceback would bury the reason.
                logger.warning("monthly restore test failed: %s", exc)
            else:
                logger.exception("restore test failed")
            self._notify("backup_failed", f"restore test failed ({safe_error(exc)})")
            self._set(
                "restore_test",
                {
                    "at": self._clock.now().isoformat(),
                    "ok": False,
                    "error": safe_error(exc),
                    "delete_protection": delete_protection,
                },
            )
            return False
        finally:
            if target_root is not None:
                shutil.rmtree(target_root, ignore_errors=True)
