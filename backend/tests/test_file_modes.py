"""PR 24: umask + data-dir file modes (SPEC → Security hardening → Files
on disk — the piece shared with PR 28, built here)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from app.filemodes import DIR_MODE, FILE_MODE, apply_umask, enforce_file_modes


def test_umask_is_owner_only() -> None:
    old = os.umask(0)
    try:
        apply_umask()
        assert os.umask(0) == 0o077
    finally:
        os.umask(old)


def test_data_dir_and_files_modes(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "backups").mkdir(parents=True)
    (data / "logs").mkdir()
    db = data / "stock-agent.db"
    db.write_text("x")
    backup = data / "backups" / "stock-agent.db.2026-10-28"
    backup.write_text("x")
    log = data / "logs" / "stock-agent.log"
    log.write_text("x")
    # Loose modes that startup must correct — set explicitly so the
    # count does not depend on the umask other tests left behind.
    data.chmod(0o755)
    db.chmod(0o644)
    backup.chmod(0o600)
    (data / "backups").chmod(0o750)
    (data / "logs").chmod(0o755)
    log.chmod(0o640)

    corrected = enforce_file_modes(data)

    # data, both subdirs and two files needed correcting.
    assert corrected == 5
    assert stat.S_IMODE(data.stat().st_mode) == DIR_MODE
    assert stat.S_IMODE((data / "backups").stat().st_mode) == DIR_MODE
    assert stat.S_IMODE((data / "logs").stat().st_mode) == DIR_MODE
    for f in (db, backup, log):
        assert stat.S_IMODE(f.stat().st_mode) == FILE_MODE


def test_enforce_on_missing_dir_is_noop(tmp_path: Path) -> None:
    assert enforce_file_modes(tmp_path / "nope") == 0


def test_new_files_are_owner_only(tmp_path: Path) -> None:
    apply_umask()
    f = tmp_path / "created.db"
    f.write_text("x")
    d = tmp_path / "newdir"
    d.mkdir()
    assert stat.S_IMODE(f.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(d.stat().st_mode) & 0o077 == 0
