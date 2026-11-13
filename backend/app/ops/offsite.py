"""Offsite backups with restic (docs/SPEC.md → VPS deployment → Backups).

The daily 03:00 local copy stays; the offsite job encrypts it and the
``secret.key`` into a restic repository on S3-compatible storage at
another provider. 30 daily and 12 weekly snapshots are kept.

Credentials come from the encrypted ``secrets`` table — never files or
logs: ``backup_password`` (the restic repository password) and
``backup_credentials`` (JSON: ``endpoint``, ``bucket``,
``access_key_id``, ``secret_access_key``). The endpoint is validated as
https and non-private on save, and again here before any use.

``python -m app.ops.offsite_restore`` restores the latest snapshot into
``SA_DATA_DIR/restore/latest/`` — what ``deploy/restore.sh offsite``
calls.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.db.backup import dated_backups
from app.errors import safe_error
from app.ops.urls import UrlRejected, validate_public_https_url

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.secrets_service import SecretsStore

logger = logging.getLogger(__name__)


class OffsiteError(Exception):
    """The offsite backup could not run; the message is shown to Marc."""


@dataclass(frozen=True)
class OffsiteCredentials:
    endpoint: str  # https URL of the S3-compatible storage
    bucket: str
    access_key_id: str
    secret_access_key: str
    password: str  # restic repository password


def load_credentials(secrets: SecretsStore, resolver: object = None) -> OffsiteCredentials:
    """Read + validate the stored backup credentials."""
    password = secrets.get("backup_password")
    raw = secrets.get("backup_credentials")
    if not password or not raw:
        raise OffsiteError("no backup password or storage credentials stored")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise OffsiteError("stored backup credentials are not valid JSON") from exc
    endpoint = str(data.get("endpoint") or "")
    bucket = str(data.get("bucket") or "")
    try:
        endpoint = validate_public_https_url(endpoint, resolver=resolver)
    except UrlRejected as exc:
        raise OffsiteError(f"backup endpoint refused: {safe_error(exc)}") from exc
    if not bucket:
        raise OffsiteError("backup credentials have no bucket")
    for key in ("access_key_id", "secret_access_key"):
        if not str(data.get(key) or ""):
            raise OffsiteError(f"backup credentials missing {key}")
    return OffsiteCredentials(
        endpoint=endpoint,
        bucket=bucket,
        access_key_id=str(data["access_key_id"]),
        secret_access_key=str(data["secret_access_key"]),
        password=password,
    )


def _restic_env(creds: OffsiteCredentials) -> dict[str, str]:
    env = dict(os.environ)
    env["RESTIC_PASSWORD"] = creds.password
    env["AWS_ACCESS_KEY_ID"] = creds.access_key_id
    env["AWS_SECRET_ACCESS_KEY"] = creds.secret_access_key
    return env


_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")


def _repo(creds: OffsiteCredentials) -> str:
    # The bucket name becomes part of the repo URL — anything outside
    # the S3 name shape could reshape where credentials are sent.
    if not _BUCKET_RE.fullmatch(creds.bucket):
        raise ValueError(f"invalid backup bucket name {creds.bucket!r}")
    return f"s3:{creds.endpoint}/{creds.bucket}"


def _run(
    args: list[str], creds: OffsiteCredentials, timeout: int = 600
) -> subprocess.CompletedProcess[bytes]:
    """One restic call. stderr goes to the caller for the event details —
    it never contains the password or keys (restic only reads env)."""
    if shutil.which("restic") is None:
        raise OffsiteError("restic is not installed")
    proc = subprocess.run(  # noqa: S603 - fixed restic argv, no untrusted input
        ["restic", *args],  # noqa: S607 - restic resolved from PATH by design
        env=_restic_env(creds),
        capture_output=True,
        timeout=timeout,
    )
    return proc


def _check(proc: subprocess.CompletedProcess[bytes], what: str) -> None:
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise OffsiteError(f"{what} failed: {detail[-1] if detail else 'error'}")


def offsite_backup(config: AppConfig, secrets: SecretsStore) -> None:
    """Encrypt the database and secret.key into the offsite repository,
    then prune to 30 daily + 12 weekly snapshots."""
    creds = load_credentials(secrets)
    repo = _repo(creds)
    data = Path(config.env.sa_data_dir)
    db = Path(config.env.sa_db_path)
    keep_daily = config.get_int("ops", "offsite_backup", "keep_daily", default=30)
    keep_weekly = int(
        config.get("ops", "offsite_backup", "keep_weekly", default=12)
    )
    # init is idempotent: "already initialized" is not an error.
    init = _run(["init", "-r", repo], creds)
    if init.returncode != 0 and b"already initialized" not in init.stderr:
        last = init.stderr.decode("utf-8", "replace").strip().splitlines()
        raise OffsiteError(f"restic init failed: {last[-1] if last else 'error'}")
    # Back up the verified daily copy, not the live WAL database — a
    # plain copy of the live file can be torn and misses WAL contents.
    # Fall back to the live path only before the first backup exists.
    backups_dir = db.parent / "backups"
    copies = sorted(dated_backups(backups_dir, db.name))
    paths = [str(copies[-1] if copies else db)]
    secret_key = data / "secret.key"
    if secret_key.exists():
        paths.append(str(secret_key))
    _check(_run(["backup", "-r", repo, *paths], creds), "restic backup")
    _check(
        _run(
            [
                "forget", "-r", repo, "--prune",
                "--keep-daily", str(keep_daily),
                "--keep-weekly", str(keep_weekly),
            ],
            creds,
        ),
        "restic forget",
    )
    logger.info("offsite backup ok (restic, %d files)", len(paths))


# --- delete protection (item 084, S8) -------------------------------------
#
# The monthly restore test asks the bucket whether versioning is on, so
# Settings → Server can show "Delete protection". One signed S3 request
# (GetBucketVersioning) — restic can't ask, and the app's key needs no
# delete permission for it. Any failure answers "unknown", never an
# exception: the check must not block a backup or a restore.

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _sigv4_authorization(
    *,
    method: str,
    canonical_uri: str,
    canonical_query: str,
    host: str,
    region: str,
    now: datetime,
    creds: OffsiteCredentials,
) -> tuple[str, str]:
    """AWS Signature Version 4 for one request; returns (Authorization,
    x-amz-date). Only the headers we send are signed."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{_EMPTY_SHA256}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_query, canonical_headers, signed_headers, _EMPTY_SHA256]
    )
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    def _h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = _h(f"AWS4{creds.secret_access_key}".encode(), date_stamp)
    k_region = _h(k_date, region)
    k_service = _h(k_region, "s3")
    k_signing = _h(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={creds.access_key_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return authorization, amz_date


def _versioning_request(
    creds: OffsiteCredentials, now: datetime
) -> tuple[str, dict[str, str]]:
    """``GET {endpoint}/{bucket}?versioning`` path-style, SigV4-signed —
    the same layout restic's ``s3:endpoint/bucket`` repo uses."""
    if not _BUCKET_RE.fullmatch(creds.bucket):
        raise ValueError(f"invalid backup bucket name {creds.bucket!r}")
    parts = urlsplit(creds.endpoint)
    host = parts.netloc
    if host.endswith(":443"):
        host = host[: -len(":443")]
    base_path = parts.path.rstrip("/")
    canonical_uri = f"{base_path}/{creds.bucket}"
    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    authorization, amz_date = _sigv4_authorization(
        method="GET",
        canonical_uri=canonical_uri,
        canonical_query="versioning=",
        host=host,
        region=region,
        now=now,
        creds=creds,
    )
    url = f"{creds.endpoint}{canonical_uri}?versioning"
    return url, {
        "Authorization": authorization,
        "x-amz-content-sha256": _EMPTY_SHA256,
        "x-amz-date": amz_date,
    }


def _versioning_status(body: str) -> str:
    """Map the GetBucketVersioning XML to on / off / suspended."""
    match = re.search(r"<Status>([^<]+)</Status>", body)
    status = match.group(1).strip() if match else ""
    return {"Enabled": "on", "Suspended": "off"}.get(status, "unknown")


def bucket_delete_protection(
    creds: OffsiteCredentials,
    now: datetime,
    *,
    get: Callable[[str, dict[str, str]], Any] | None = None,
) -> str:
    """"on" / "off" / "unknown" — one signed GetBucketVersioning call.

    ``get`` is injectable for tests: ``get(url, headers) -> response``
    with ``.status_code`` and ``.text``. Never raises — a failed check
    is "unknown", shown amber as "not confirmed".
    """
    try:
        url, headers = _versioning_request(creds, now)
        if get is not None:
            response = get(url, headers)
        else:
            import httpx

            with httpx.Client(timeout=15) as client:
                response = client.get(url, headers=headers)
        if int(getattr(response, "status_code", 0)) != 200:
            logger.warning(
                "delete-protection check answered HTTP %s",
                getattr(response, "status_code", "?"),
            )
            return "unknown"
        return _versioning_status(str(getattr(response, "text", "")))
    except Exception:  # noqa: BLE001 — the check must never break a backup
        logger.warning("delete-protection check failed", exc_info=True)
        return "unknown"


def restore_latest(
    config: AppConfig, secrets: SecretsStore, target: Path
) -> Path:
    """Restore the newest snapshot into ``target``; returns the db path."""
    creds = load_credentials(secrets)
    target.mkdir(parents=True, exist_ok=True)
    _check(
        _run(
            ["restore", "latest", "-r", _repo(creds), "--target", str(target)],
            creds,
            timeout=1800,
        ),
        "restic restore",
    )
    name = Path(config.env.sa_db_path).name
    # New snapshots hold the dated daily copy (name.YYYY-MM-DD); older
    # ones hold the live path itself.
    candidates = sorted(target.rglob(f"{name}.*"))
    if not candidates:
        candidates = sorted(target.rglob(name))
    if not candidates:
        raise OffsiteError(f"restored snapshot has no {name}")
    return candidates[-1]


def main() -> None:
    """``python -m app.ops.offsite_restore`` — for deploy/restore.sh."""
    from app.config import get_config
    from app.market.clock import RealClock
    from app.secrets_service import SecretsStore

    config = get_config()
    target = Path(config.env.sa_data_dir) / "restore" / "latest"
    path = restore_latest(
        config, SecretsStore(config, RealClock()), target
    )
    print(path)


if __name__ == "__main__":
    main()
