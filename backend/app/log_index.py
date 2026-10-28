"""Log index (docs/SPEC.md, Logging — Data model).

A separate SQLite file ``data/logs/logs.db`` — never the trading
database — indexed by the QueueListener's own thread in ~1s batches.
If an index write fails, file logging keeps working; the failure goes
to the file and stderr only, never recursively through the index.

``log_lines`` mirrors the JSON line plus a ``fingerprint`` (logger +
event + message with numbers, symbols and ids folded to placeholders)
so recurring problems group together. FTS5 on the message powers the
Logs screen's search; pruning follows the log files (``keep_days``).

``python -m app.log_index`` rebuilds the index from the log files —
``make logs-reindex``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config import AppConfig

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_lines(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    logger TEXT NOT NULL,
    event TEXT,
    message TEXT NOT NULL,
    context TEXT,
    cycle TEXT,
    symbol TEXT,
    process TEXT,
    pid INTEGER,
    error_id TEXT,
    exception_type TEXT,
    exception_message TEXT,
    traceback TEXT,
    fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS ix_log_lines_ts ON log_lines(ts);
CREATE INDEX IF NOT EXISTS ix_log_lines_level_ts ON log_lines(level, ts);
CREATE INDEX IF NOT EXISTS ix_log_lines_logger_ts ON log_lines(logger, ts);
CREATE INDEX IF NOT EXISTS ix_log_lines_cycle_ts ON log_lines(cycle, ts);
CREATE INDEX IF NOT EXISTS ix_log_lines_symbol_ts ON log_lines(symbol, ts);
CREATE INDEX IF NOT EXISTS ix_log_lines_fingerprint ON log_lines(fingerprint);
CREATE VIRTUAL TABLE IF NOT EXISTS log_lines_fts USING fts5(
    message, content='log_lines', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS log_lines_ai AFTER INSERT ON log_lines BEGIN
    INSERT INTO log_lines_fts(rowid, message) VALUES (new.id, new.message);
END;
"""

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_ID = re.compile(r"\b[a-z]-[0-9a-f]{3,}\b")
_SYMBOL = re.compile(r"\b[A-Z]{1,5}(?:\.[A-Z])?\b")
_PLACEHOLDER_WORDS = {"INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL", "NULL",
                      "TRUE", "FALSE", "OK", "UTC", "NY", "AM", "PM", "GET",
                      "POST", "PUT", "DELETE", "HTTP", "API", "WAL", "SIGINT",
                      "SIGTERM", "PRAGMA", "SQL", "FTS", "PID", "DB", "CSV",
                      "JSON", "LLM", "SMTP", "ECB", "SPY"}


def fingerprint(logger: str, event: str | None, message: str) -> str:
    """logger + event + message with numbers, symbols and ids folded."""
    text = _ID.sub("{id}", message)
    text = _NUMBER.sub("{n}", text)
    text = _SYMBOL.sub(
        lambda m: m.group(0) if m.group(0) in _PLACEHOLDER_WORDS else "{sym}",
        text,
    )
    return f"{logger}|{event or ''}|{text[:200]}"


def _record_to_row(record: logging.LogRecord) -> dict[str, Any] | None:
    """Map the JSON formatter's fields back onto a row (or None)."""
    message = record.getMessage()
    context = getattr(record, "context", {}) or {}
    exc_type = exc_msg = tb = None
    if record.exc_info:
        # Scrub like the file handler does — an exception's text can
        # carry keys or paths (hard rule 9).
        from app.logging_config import _all_secrets, _scrub_text

        t, e, _ = record.exc_info
        exc_type = getattr(t, "__name__", str(t))
        exc_msg = _scrub_text(str(e) if e is not None else "", _all_secrets())
        import traceback as _tb

        tb = _scrub_text(
            "".join(_tb.format_exception(*record.exc_info)), _all_secrets()
        )
    elif record.exc_text:
        tb = record.exc_text
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return {
        "ts": datetime.fromtimestamp(
            record.created, tz=ZoneInfo("UTC")
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": record.levelname,
        "logger": record.name,
        "event": getattr(record, "event", None),
        "message": message,
        "context": json.dumps(context, default=str),
        "cycle": context.get("cycle"),
        "symbol": context.get("symbol"),
        "process": getattr(record, "process", "app"),
        "pid": record.process,
        "error_id": getattr(record, "error_id", None),
        "exception_type": exc_type,
        "exception_message": exc_msg,
        "traceback": tb,
        "fingerprint": fingerprint(
            record.name, getattr(record, "event", None), message
        ),
    }


class LogIndexHandler(logging.Handler):
    """Listener-side handler: buffers rows, inserts ~every second."""

    def __init__(
        self,
        db_path: Path,
        *,
        file_fallback: logging.Handler | None = None,
        on_warning_or_above: Any = None,
    ) -> None:
        super().__init__(level=logging.DEBUG)
        self.db_path = db_path
        self._fallback = file_fallback
        self._on_warning = on_warning_or_above
        self._buffer: list[dict[str, Any]] = []
        self._last_flush = time.monotonic()
        self._conn: sqlite3.Connection | None = None
        # flush() also runs outside the listener thread (tests, close on
        # shutdown): serialize buffer swaps and the shared connection.
        self._write_lock = threading.Lock()
        self._broken = False

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False: writes only happen on the listener
            # thread, but logging.shutdown() may close us from another.
            self._conn = sqlite3.connect(
                self.db_path, check_same_thread=False
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
        return self._conn

    def _report_failure(self, record: logging.LogRecord, exc: Exception) -> None:
        """Index failure → file + stderr only, never through the index."""
        line = (
            f"log index write failed: {exc} "
            f"(line: {record.getMessage()[:200]})"
        )
        print(line, file=sys.stderr)
        if self._fallback is not None:
            try:
                fallback_record = logging.LogRecord(
                    "log_index", logging.ERROR, record.pathname or "",
                    0, line, (), None,
                )
                fallback_record.event = "log_index.failed"
                self._fallback.emit(fallback_record)
            except Exception:  # noqa: BLE001,S110 - never break the listener
                pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            row = _record_to_row(record)
        except Exception:  # noqa: BLE001 - a bad record must not break logging
            return
        if row is not None:
            # Under the same lock as flush(): an append that lands on a
            # just-swapped-out buffer is never seen by the next flush.
            with self._write_lock:
                self._buffer.append(row)
        if record.levelno >= logging.WARNING:
            # The "logs" ping must land after the row is committed: a
            # refetch that raced the batch would miss the very line it
            # announces. Warning-and-up therefore commits right away;
            # lower levels keep the ~1 s batch.
            self.flush()
            if self._on_warning is not None:
                # a broken callback must never break the listener thread
                with contextlib.suppress(Exception):
                    self._on_warning(record)
        elif time.monotonic() - self._last_flush >= 1:
            self.flush()

    def flush(self) -> None:
        with self._write_lock:
            if not self._buffer:
                return
            batch, self._buffer = self._buffer, []
            self._last_flush = time.monotonic()
            try:
                conn = self._connect()
                conn.executemany(
                    "INSERT INTO log_lines(ts,level,logger,event,message,"
                    "context,cycle,symbol,process,pid,error_id,exception_type,"
                    "exception_message,traceback,fingerprint) VALUES ("
                    ":ts,:level,:logger,:event,:message,:context,:cycle,"
                    ":symbol,:process,:pid,:error_id,:exception_type,"
                    ":exception_message,:traceback,:fingerprint)",
                    batch,
                )
                conn.commit()
            except sqlite3.Error as exc:
                self._report_failure(
                    logging.LogRecord("log_index", logging.ERROR, "", 0,
                                      f"{len(batch)} rows", (), None),
                    exc,
                )
                # The connection may be half-open; reopen next time.
                with contextlib.suppress(sqlite3.Error):
                    if self._conn is not None:
                        self._conn.close()
                self._conn = None

    def close(self) -> None:
        try:
            with contextlib.suppress(sqlite3.Error):
                self.flush()
            with self._write_lock, contextlib.suppress(sqlite3.Error):
                if self._conn is not None:
                    self._conn.close()
                self._conn = None
        finally:
            super().close()


def prune_index(db_path: Path, keep_days: int) -> int:
    """Drop index rows older than the files' retention window."""
    if not db_path.exists():
        return 0
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    cutoff = (
        datetime.now(tz=ZoneInfo("UTC")) - timedelta(days=keep_days)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute("DELETE FROM log_lines WHERE ts < ?", (cutoff,))
        conn.commit()
        return cur.rowcount


def index_available(db_path: Path) -> bool:
    """Readiness: a missing or incomplete index means 'not yet'."""
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.execute("SELECT 1 FROM log_lines LIMIT 1").fetchone()
            conn.execute("SELECT 1 FROM log_lines_fts LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def index_path(config: AppConfig) -> Path:
    """data/logs/logs.db — beside the log files."""
    from app.logging_config import log_dir

    return log_dir(config) / "logs.db"


def make_index_handler(
    config: AppConfig,
    *,
    file_fallback: logging.Handler | None = None,
    on_warning_or_above: Any = None,
) -> LogIndexHandler | None:
    """Attach the index to the listener, or None when disabled by config."""
    if not bool(config.get("logging", "index", default=True)):
        return None
    return LogIndexHandler(
        index_path(config),
        file_fallback=file_fallback,
        on_warning_or_above=on_warning_or_above,
    )


def reindex(log_dir: Path, db_path: Path) -> int:
    """Rebuild logs.db from the JSON-lines files (make logs-reindex)."""
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    files = sorted(
        list(log_dir.glob("*.log")) + list(log_dir.glob("*.log.gz")),
        key=lambda p: p.stat().st_mtime,
    )
    rows = 0
    for path in files:
        import gzip

        opener = gzip.open if path.suffix == ".gz" else open  # noqa: SIM115
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            batch = []
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ctx = obj.get("context") or {}
                exc = obj.get("exception") or {}
                message = str(obj.get("message", ""))
                batch.append(
                    {
                        "ts": obj.get("ts", ""),
                        "level": obj.get("level", "INFO"),
                        "logger": obj.get("logger", "app"),
                        "event": obj.get("event"),
                        "message": message,
                        "context": json.dumps(ctx),
                        "cycle": ctx.get("cycle"),
                        "symbol": ctx.get("symbol"),
                        "process": obj.get("process", "app"),
                        "pid": obj.get("pid"),
                        "error_id": obj.get("error_id"),
                        "exception_type": exc.get("type"),
                        "exception_message": exc.get("message"),
                        "traceback": exc.get("traceback"),
                        "fingerprint": fingerprint(
                            str(obj.get("logger", "app")),
                            obj.get("event"),
                            message,
                        ),
                    }
                )
                if len(batch) >= 1000:
                    conn.executemany(
                        "INSERT INTO log_lines VALUES (NULL,:ts,:level,:logger,"
                        ":event,:message,:context,:cycle,:symbol,:process,:pid,"
                        ":error_id,:exception_type,:exception_message,"
                        ":traceback,:fingerprint)",
                        batch,
                    )
                    conn.commit()
                    rows += len(batch)
                    batch = []
            if batch:
                conn.executemany(
                    "INSERT INTO log_lines VALUES (NULL,:ts,:level,:logger,"
                    ":event,:message,:context,:cycle,:symbol,:process,:pid,"
                    ":error_id,:exception_type,:exception_message,:traceback,"
                    ":fingerprint)",
                    batch,
                )
                conn.commit()
                rows += len(batch)
    conn.close()
    return rows


def main() -> None:
    from app.config import get_config

    config = get_config()
    log_dir = index_path(config).parent
    rows = reindex(log_dir, index_path(config))
    print(f"reindexed {rows} log lines into {index_path(config)}")


if __name__ == "__main__":
    main()
