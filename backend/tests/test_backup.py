"""Tests for the daily database copy (docs/SPEC.md, Data model)."""

import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_config
from app.db.backup import create_backup

APP_TZ = ZoneInfo("Europe/Amsterdam")


def _db_path() -> Path:
    return Path(get_config().env.sa_db_path)


def _today() -> date:
    """The date the backup code stamps: today in the app timezone."""
    return datetime.now(tz=APP_TZ).date()


def _is_date_suffix(name: str) -> bool:
    """Same rule the pruner uses: the part after the last dot is a date."""
    try:
        datetime.strptime(name.rsplit(".", 1)[-1], "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _write_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('test data')")


def test_create_backup_creates_dated_copy() -> None:
    db_path = _db_path()
    _write_db(db_path)

    create_backup()

    backup_path = db_path.parent / "backups" / f"{db_path.name}.{_today().isoformat()}"
    with sqlite3.connect(backup_path) as conn:
        row = conn.execute("SELECT v FROM t").fetchone()
    assert row == ("test data",)


def test_create_backup_prunes_old_copies() -> None:
    db_path = _db_path()
    _write_db(db_path)
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for days_ago in (14, 15, 20):
        old = _today() - timedelta(days=days_ago)
        (backup_dir / f"{db_path.name}.{old.isoformat()}").write_text("old")

    create_backup()

    # WAL-mode databases leave -shm/-wal sidecars next to the copy; the
    # pruner (and this check) only counts files with a date suffix.
    backups = [
        b.name
        for b in backup_dir.glob(f"{db_path.name}.*")
        if _is_date_suffix(b.name)
    ]
    assert backups == [f"{db_path.name}.{_today().isoformat()}"]


def test_create_backup_keeps_the_retention_boundary() -> None:
    """backup_days days are kept including today; the day before is pruned."""
    db_path = _db_path()
    _write_db(db_path)
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    boundary = _today() - timedelta(days=13)  # oldest kept copy (14 days incl. today)
    outside = _today() - timedelta(days=14)
    kept = backup_dir / f"{db_path.name}.{boundary.isoformat()}"
    pruned = backup_dir / f"{db_path.name}.{outside.isoformat()}"
    kept.write_text("keep")
    pruned.write_text("prune")

    create_backup()

    assert kept.exists()
    assert not pruned.exists()


def test_backup_copy_restores_and_reads() -> None:
    """A backup file restores to a working database (integrity_check ok)."""
    db_path = _db_path()
    _write_db(db_path)

    create_backup()

    backup = db_path.parent / "backups" / f"{db_path.name}.{_today().isoformat()}"
    restored = db_path.parent / "restored.db"
    shutil.copy2(backup, restored)
    with sqlite3.connect(restored) as conn:
        (integrity,) = conn.execute("PRAGMA integrity_check").fetchone()
        row = conn.execute("SELECT v FROM t").fetchone()
    assert integrity == "ok"
    assert row == ("test data",)


def test_create_backup_ignores_unrelated_files() -> None:
    db_path = _db_path()
    _write_db(db_path)
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / f"{db_path.name}.not-a-date").write_text("keep")

    create_backup()

    assert (backup_dir / f"{db_path.name}.not-a-date").exists()
