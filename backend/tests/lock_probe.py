"""Hard-rule-13 probe: fails a call made while a SQLite write lock is held.

Wrap any broker, data source or model client. Before every awaited call it
tries ``BEGIN IMMEDIATE`` on the database with no busy-wait; if the lock
can't be taken, some other code is holding a write transaction open across
this I/O call — the exact failure a live write would hit while shadow or
backtest work is in progress.
"""

from __future__ import annotations

import sqlite3
from typing import Any


class LockProbe:
    """Proxy that asserts no write transaction is open during each call."""

    def __init__(self, name: str, inner: Any, db_path: str) -> None:
        self._name = name
        self._inner = inner
        self._db_path = db_path
        self.violations: list[str] = []

    def _check(self, method: str) -> None:
        conn = sqlite3.connect(self._db_path, timeout=0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            self.violations.append(f"{self._name}.{method}")
        finally:
            conn.close()

    def __getattr__(self, method: str) -> Any:
        attr = getattr(self._inner, method)
        if not callable(attr):
            return attr

        async def guarded(*args: Any, **kwargs: Any) -> Any:
            self._check(method)
            return await attr(*args, **kwargs)

        return guarded
