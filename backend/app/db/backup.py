"""Daily database copy (docs/SPEC.md, Data model; Build plan, PR 8).

A copy of the SQLite file is kept per day — ``storage.backup_days`` days
retained, today included. The job runs on the app timezone (Amsterdam per
spec) and filenames carry the local date.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_config
from app.market.clock import Clock


def create_backup(clock: Clock | None = None) -> None:
    """Create a dated copy of the database and prune old backups."""
    cfg = get_config()
    app_tz = ZoneInfo(str(cfg.get("app", "timezone", default="Europe/Amsterdam")))
    db_path = Path(cfg.env.sa_db_path)
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Dated backup: stock-agent.db.YYYY-MM-DD (app-timezone date). The
    # sqlite3 backup API copies consistently while the app keeps writing;
    # a plain file copy could capture a torn page or miss WAL contents.
    now = clock.now() if clock is not None else datetime.now(tz=ZoneInfo("UTC"))
    today = now.astimezone(app_tz).date()
    backup_path = backup_dir / f"{db_path.name}.{today.isoformat()}"
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        with sqlite3.connect(backup_path) as target:
            source.backup(target)
    finally:
        source.close()
    with sqlite3.connect(backup_path) as verify:
        (integrity,) = verify.execute("PRAGMA integrity_check").fetchone()
    if integrity != "ok":
        raise RuntimeError(
            f"backup {backup_path.name} failed integrity_check: {integrity}"
        )
    import logging

    from app.logging_config import ev

    ev(
        logging.getLogger("db"), logging.INFO, "db.backup",
        f"backup {backup_path.name} ok (integrity_check)",
        job="backup",
    )

    # Prune backups outside the retention window: the last
    # ``storage.backup_days`` days including today.
    retention_days = int(cfg.get("storage", "backup_days", default=14))
    cutoff = today - timedelta(days=retention_days - 1)
    for backup in backup_dir.glob(f"{db_path.name}.*"):
        try:
            backup_date = datetime.strptime(
                backup.name.rsplit(".", 1)[-1], "%Y-%m-%d"
            ).date()
        except ValueError:
            continue  # Not a dated backup of this database.
        if backup_date < cutoff:
            backup.unlink()
