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

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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
        raise OffsiteError(f"backup endpoint refused: {exc}") from exc
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


def _repo(creds: OffsiteCredentials) -> str:
    return f"s3:{creds.endpoint}/{creds.bucket}"


def _run(
    args: list[str], creds: OffsiteCredentials, timeout: int = 600
) -> subprocess.CompletedProcess[bytes]:
    """One restic call. stderr goes to the caller for the event details —
    it never contains the password or keys (restic only reads env)."""
    if shutil.which("restic") is None:
        raise OffsiteError("restic is not installed")
    proc = subprocess.run(
        ["restic", *args],
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
    keep_daily = int(config.get("ops", "offsite_backup", "keep_daily", default=30))
    keep_weekly = int(
        config.get("ops", "offsite_backup", "keep_weekly", default=12)
    )
    # init is idempotent: "already initialized" is not an error.
    init = _run(["init", "-r", repo], creds)
    if init.returncode != 0 and b"already initialized" not in init.stderr:
        last = init.stderr.decode("utf-8", "replace").strip().splitlines()
        raise OffsiteError(f"restic init failed: {last[-1] if last else 'error'}")
    paths = [str(db)]
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
    candidates = list(target.rglob("stock-agent.db"))
    if not candidates:
        raise OffsiteError("restored snapshot has no stock-agent.db")
    return candidates[0]


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
