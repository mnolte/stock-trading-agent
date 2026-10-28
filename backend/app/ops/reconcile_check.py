"""``python -m app.ops.reconcile_check`` — deploy.sh's read-only probe.

Exit 0 when the last startup reconciliation recorded in
``server-status.json`` was clean; 1 otherwise (including a missing or
unparseable status file — unknown is never clean).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.config import get_config


def startup_reconcile_ok(data_dir: str | None = None) -> bool | None:
    """True/False from the status file; None when nothing was recorded."""
    root = Path(data_dir or get_config().env.sa_data_dir)
    try:
        status = json.loads((root / "server-status.json").read_text())
    except (OSError, ValueError):
        return None
    entry = status.get("startup_reconcile")
    if not isinstance(entry, dict):
        return None
    return bool(entry.get("ok"))


def main() -> None:
    ok = startup_reconcile_ok()
    print(json.dumps({"startup_reconcile_ok": ok}))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
