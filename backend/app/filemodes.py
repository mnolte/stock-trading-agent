"""Process umask and data-dir file modes (docs/SPEC.md, Security
hardening → Files on disk — the piece shared with PR 28, built here).

- The process umask is 077, set in ``app.serve`` before anything can
  create a file and again at the top of the lifespan for the ``make
  dev``/``make run`` uvicorn entry paths.
- ``SA_DATA_DIR`` is 0700; everything under it — the database, backups,
  run folders, logs, the log index and ``secret.key`` — is 0600
  (subdirectories 0700). Wrong modes found at startup are corrected,
  with one INFO line.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DIR_MODE = 0o700
FILE_MODE = 0o600


def apply_umask() -> None:
    """``umask 077``: everything the app creates is owner-only."""
    os.umask(0o077)


def enforce_file_modes(data_dir: Path) -> int:
    """Correct modes under ``data_dir``; returns how many were fixed."""
    if not data_dir.exists():
        return 0
    corrected = 0
    for path in [data_dir, *sorted(data_dir.rglob("*"))]:
        try:
            current = path.stat().st_mode & 0o777
        except OSError:
            continue
        want = DIR_MODE if path.is_dir() else FILE_MODE
        if current != want:
            try:
                path.chmod(want)
            except OSError:
                logger.warning(
                    "could not set mode %o on %s", want, path
                )
                continue
            corrected += 1
    return corrected
