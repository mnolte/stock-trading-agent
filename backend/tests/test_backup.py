"""Tests for the daily database copy (docs/SPEC.md, Data model)."""

from datetime import date, timedelta
from pathlib import Path

from app.config import get_config
from app.db.backup import create_backup


def _db_path() -> Path:
    return Path(get_config().env.sa_db_path)


def test_create_backup_creates_dated_copy() -> None:
    db_path = _db_path()
    db_path.write_text("test data")

    create_backup()

    backups = list((db_path.parent / "backups").glob(f"{db_path.name}.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "test data"
    today = date.today().isoformat()
    assert backups[0].name == f"{db_path.name}.{today}"


def test_create_backup_prunes_old_copies() -> None:
    db_path = _db_path()
    db_path.write_text("test data")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for days_ago in (15, 16, 20):
        old = date.today() - timedelta(days=days_ago)
        (backup_dir / f"{db_path.name}.{old.isoformat()}").write_text("old")

    create_backup()

    backups = list(backup_dir.glob(f"{db_path.name}.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "test data"


def test_create_backup_ignores_unrelated_files() -> None:
    db_path = _db_path()
    db_path.write_text("test data")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / f"{db_path.name}.not-a-date").write_text("keep")

    create_backup()

    assert (backup_dir / f"{db_path.name}.not-a-date").exists()
