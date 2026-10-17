"""Daily database copy (docs/SPEC.md, Data model; Build plan, PR 8).

A copy of the SQLite file is kept per day — 14 days retained. The job runs
on the app timezone (Amsterdam per spec) and filenames carry the local date.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_config


def create_backup() -> None:
    """Create a dated copy of the database and prune old backups."""
    cfg = get_config()
    app_tz = ZoneInfo(str(cfg.get("app", "timezone", default="Europe/Amsterdam")))
    db_path = Path(cfg.env.sa_db_path)
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Dated backup: stock-agent.db.YYYY-MM-DD (app-timezone date).
    today = datetime.now(tz=app_tz).date()
    backup_path = backup_dir / f"{db_path.name}.{today.isoformat()}"

    shutil.copy2(db_path, backup_path)

    # Prune backups older than the retention window.
    retention_days = int(cfg.get("storage", "backup_days", default=14))
    cutoff = today - timedelta(days=retention_days)
    for backup in backup_dir.glob(f"{db_path.name}.*"):
        try:
            backup_date = datetime.strptime(backup.name.rsplit(".", 1)[-1], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            continue  # Not a dated backup of this database.
        if backup_date < cutoff:
            backup.unlink()
