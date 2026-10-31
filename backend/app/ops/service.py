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

import httpx

from app.market.clock import NY_TZ
from app.ops.offsite import restore_latest
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
        return dict(self._status)

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

    def heartbeat_url(self) -> str:
        return str(self._config.get("ops", "heartbeat_url", default="") or "")

    def validate_heartbeat_url(self, url: str, resolver: object = None) -> str:
        """https + public — the rule from Stored credentials."""
        try:
            return validate_public_https_url(url, resolver=resolver)
        except UrlRejected as exc:
            raise ValueError(f"heartbeat_url {exc}") from exc

    async def _http_ping(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url)
            return response.status_code < 500
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
        pct = round(usage.used * 100 / usage.total, 1)
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
        # threshold so one skewed response does not page Marc.
        strikes = int(self._status.get("clock_strikes", 0))
        over = offset_ms is not None and offset_ms / 1000 > max_off
        strikes = strikes + 1 if over else 0
        self._status["clock_strikes"] = strikes
        level = "ok" if offset_ms is not None and not over else "unknown"
        if over and strikes >= 3 and offset_ms is not None:
            level = "warning"
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
        host = (public.split("://", 1)[-1]).split("/")[0].split(":")[0]
        if not public.startswith("https://") or not host:
            unknown2: dict[str, Any] = {"expires": None, "days_left": None, "level": "unknown"}
            self._set("cert", unknown2)
            return unknown2
        try:
            pem = ssl.get_server_certificate((host, 443), timeout=10)
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
            (db.parent / "backups").glob(f"{db.name}.*"),
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
        stale_days = int(
            self._config.get("ops", "restore_test_stale_days", default=45)
        )
        if restore_test:
            ran = restore_test.get("at")
            try:
                ran_at = datetime.fromisoformat(str(ran))
                if (self._clock.now() - ran_at).days > stale_days:
                    restore_test = {**restore_test, "level": "warning"}
            except (TypeError, ValueError):
                pass
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

    # ------------------------------------------------------------------
    # offsite jobs

    def run_offsite_backup_sync(self) -> None:
        """The restic upload — sync so the 03:00 job can chain it."""
        from app.ops.offsite import offsite_backup

        if self._secrets is None:
            return
        try:
            offsite_backup(self._config, self._secrets)
        except Exception as exc:  # noqa: BLE001
            logger.exception("offsite backup failed")
            self._set(
                "offsite_backup",
                {"at": self._clock.now().isoformat(), "ok": False, "error": str(exc)[:200]},
            )
            raise  # the caller records the backup_failed event + alert
        self._set(
            "offsite_backup", {"at": self._clock.now().isoformat(), "ok": True}
        )

    async def run_offsite_backup(self) -> None:
        try:
            await asyncio.to_thread(self.run_offsite_backup_sync)
        except Exception as exc:  # noqa: BLE001
            self._notify("backup_failed", f"offsite backup failed ({exc})")

    async def run_restore_test(self) -> bool:
        """Monthly: restore the newest offsite snapshot into a temp folder
        and run PRAGMA integrity_check on it."""
        if self._secrets is None:
            return False
        import sqlite3
        import tempfile

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
                {"at": self._clock.now().isoformat(), "ok": ok},
            )
            if not ok:
                self._notify("backup_failed", "restore test failed integrity_check")
            else:
                logger.info("monthly restore test ok")
            return bool(ok)
        except Exception as exc:  # noqa: BLE001
            logger.exception("restore test failed")
            self._notify("backup_failed", f"restore test failed ({exc})")
            self._set(
                "restore_test",
                {"at": self._clock.now().isoformat(), "ok": False, "error": str(exc)[:200]},
            )
            return False
        finally:
            if target_root is not None:
                shutil.rmtree(target_root, ignore_errors=True)
