"""PR 20: log index (logs.db) and the logs API (docs/SPEC.md, Logging)."""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import time
from pathlib import Path
from typing import cast

import pytest
from app.log_index import (
    LogIndexHandler,
    fingerprint,
    index_available,
    prune_index,
    reindex,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.anyio


def _rec(
    msg: str,
    level: int = logging.INFO,
    logger: str = "test",
    **context: object,
) -> logging.LogRecord:
    record = logging.LogRecord(logger, level, __file__, 1, msg, (), None)
    record.context = context
    return record


def test_index_writes_and_queries(tmp_path: Path) -> None:
    db = tmp_path / "logs.db"
    handler = LogIndexHandler(db)
    handler.emit(_rec("hello world", logging.WARNING, cycle="c-0001"))
    handler.flush()
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT ts, level, logger, message, cycle, fingerprint "
            "FROM log_lines"
        ).fetchone()
        assert row[1] == "WARNING" and row[4] == "c-0001"
        assert row[3] == "hello world"
        fts = conn.execute(
            "SELECT rowid FROM log_lines_fts WHERE message MATCH 'hello'"
        ).fetchone()
        assert fts is not None


def test_index_failure_keeps_file_logging(tmp_path: Path) -> None:
    """A broken index reports to the fallback handler + stderr only."""
    db = tmp_path / "logs.db"
    db.write_bytes(b"not a database")
    fallback_lines: list[logging.LogRecord] = []
    fallback = logging.StreamHandler()
    fallback.emit = fallback_lines.append  # type: ignore[method-assign, assignment]
    handler = LogIndexHandler(db, file_fallback=fallback)
    handler.emit(_rec("still logged"))
    handler.flush()
    assert handler._conn is None or handler._buffer == []
    assert fallback_lines, "index failure must reach the file handler"


def test_fingerprint_groups_recurring() -> None:
    """Same problem, different ids/numbers → one fingerprint."""
    a = fingerprint("broker.orders", "orders.submit_failed",
                    "NVDA: order submit failed: timeout 5.2s")
    b = fingerprint("broker.orders", "orders.submit_failed",
                    "AAPL: order submit failed: timeout 8.9s")
    assert a == b
    c = fingerprint("cycle", "cycle.failed", "cycle failed: boom")
    assert a != c


def test_index_available_and_prune(tmp_path: Path) -> None:
    db = tmp_path / "logs.db"
    assert not index_available(db)
    handler = LogIndexHandler(db)
    old = _rec("ancient")
    # Backdate: insert directly with an old ts.
    handler.emit(old)
    handler.flush()
    assert index_available(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE log_lines SET ts = '2000-01-01T00:00:00.000Z'")
        conn.commit()
    assert prune_index(db, keep_days=30) == 1
    assert index_available(db)


def test_reindex_rebuilds_from_files(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    line = {
        "ts": "2026-10-24T10:00:00.000Z",
        "level": "ERROR",
        "logger": "cycle",
        "event": "cycle.failed",
        "message": "cycle failed: boom",
        "context": {"cycle": "c-0001"},
        "process": "app",
        "pid": 1,
        "error_id": "e-abc123",
        "exception": {"type": "RuntimeError", "message": "boom",
                      "traceback": "tb"},
    }
    (log_dir / "stock-agent.log").write_text(json.dumps(line) + "\n")
    db = log_dir / "logs.db"
    assert reindex(log_dir, db) == 1
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT level, error_id, cycle FROM log_lines"
        ).fetchone()
        assert row == ("ERROR", "e-abc123", "c-0001")


def test_logs_api_paging_and_filters(client: TestClient) -> None:
    """/api/logs: filters, newest-first keyset paging (limit ≤ 200)."""
    handler = cast(FastAPI, client.app).state.log_index_handler
    assert handler is not None
    for i in range(5):
        handler.emit(_rec(f"line {i}", logging.WARNING,
                          symbol="NVDA", cycle="c-0001"))
    handler.emit(_rec("quiet", logging.DEBUG))
    handler.flush()

    data = client.get("/api/logs?levels=WARNING").json()
    assert data["index_available"] is True
    assert len(data["lines"]) == 5
    # Newest first.
    assert data["lines"][0]["message"] == "line 4"
    # Symbol + cycle filters.
    filtered = client.get("/api/logs?levels=WARNING&symbol=NVDA&cycle=c-0001")
    assert len(filtered.json()["lines"]) == 5
    empty = client.get("/api/logs?levels=WARNING&symbol=AAPL")
    assert empty.json()["lines"] == []
    # Search (FTS).
    found = client.get("/api/logs?levels=WARNING&search=line")
    assert len(found.json()["lines"]) == 5


def test_logs_api_summary_and_around(client: TestClient) -> None:
    handler = cast(FastAPI, client.app).state.log_index_handler
    handler.emit(_rec("a warning", logging.WARNING))
    err = _rec("an error", logging.ERROR, cycle="c-0001")
    err.error_id = "e-test01"
    handler.emit(err)
    handler.flush()

    summary = client.get("/api/logs/summary").json()
    assert summary["index_available"] is True
    assert any(p["level"] == "ERROR" for p in summary["problems"])
    assert any(d["count"] >= 1 for d in summary["days"])

    lines = client.get("/api/logs").json()["lines"]
    err_id = next(line["id"] for line in lines if line["level"] == "ERROR")
    around = client.get(f"/api/logs/{err_id}/around?window=2").json()
    assert around["center"] == err_id
    assert any(line["id"] == err_id for line in around["lines"])


def test_logs_api_export(client: TestClient) -> None:
    handler = cast(FastAPI, client.app).state.log_index_handler
    handler.emit(_rec("exported", logging.WARNING))
    handler.flush()
    resp = client.get("/api/logs/export.jsonl")
    assert resp.status_code == 200
    assert "exported" in resp.text


def test_client_log_endpoint(client: TestClient) -> None:
    """Frontend errors land in the index at ERROR with logger 'frontend'."""
    resp = client.post(
        "/api/logs/client",
        json={"message": "react boundary: boom", "error_id": "e-front1",
              "tab_id": "t-test", "url": "http://localhost/"},
    )
    assert resp.status_code == 200
    handler = cast(FastAPI, client.app).state.log_index_handler
    # The listener thread dequeues asynchronously; under parallel test
    # load it can starve for seconds, so drain the queue ourselves —
    # whichever path wins, the row lands on the same handler.
    from app.logging_config import get_pipeline

    pipeline = get_pipeline()
    deadline = time.monotonic() + 10
    while True:
        if pipeline is not None:
            while True:
                try:
                    record = pipeline.queue.get_nowait()
                except queue.Empty:
                    break
                pipeline.listener.handle(record)
        handler.flush()
        data = client.get("/api/logs?logger=frontend").json()
        found = any("boom" in line["message"] for line in data["lines"])
        if found or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    assert found


def test_client_log_rate_limit(client: TestClient) -> None:
    for _ in range(12):
        resp = client.post(
            "/api/logs/client",
            json={"message": "spam", "tab_id": "t-spam"},
        )
    assert resp.json()["logged"] is False or resp.json()["rate_limited"]


async def test_new_error_notification_once_per_fingerprint(
    client: TestClient,
) -> None:
    """The first ERROR fingerprint of the session sends one "New error"
    notification; the repeat and the next fingerprint behave per spec."""
    from typing import Any

    from app.db.base import get_session_maker
    from app.db.models import Notification
    from fastapi import FastAPI
    from sqlalchemy import select

    handler = cast(FastAPI, client.app).state.log_index_handler
    service = cast(Any, client.app).state.notification_service
    # Numbers fold in the fingerprint: lines 1 and 2 are one problem.
    handler.emit(_rec("broker call failed after 1 retries", logging.ERROR))
    handler.emit(_rec("broker call failed after 2 retries", logging.ERROR))
    handler.emit(_rec("a different failure", logging.ERROR))
    await service.drain()

    with get_session_maker()() as session:
        rows = (
            session.execute(
                select(Notification).where(Notification.event == "new_error")
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    assert all(row.severity == "warning" for row in rows)
    assert all(row.link == "/logs" for row in rows)
    assert rows[0].body is not None
    assert "broker call failed" in rows[0].body


def test_library_loggers_warning_unless_library_debug(
    client: TestClient,
) -> None:
    """websockets/uvicorn/httpx/apscheduler log at WARNING unless
    logging.library_debug is on (29 Oct: 52 MB in 2.5 days)."""
    from app.config import get_config
    from app.logging_config import get_pipeline, install_queue_root

    for name in ("uvicorn.access", "httpx", "websockets", "apscheduler.scheduler"):
        assert logging.getLogger(name).level == logging.WARNING

    get_config().yaml.setdefault("logging", {})["library_debug"] = True
    try:
        pipeline = get_pipeline()
        assert pipeline is not None
        install_queue_root(pipeline)
        for name in ("uvicorn.access", "httpx", "websockets"):
            assert logging.getLogger(name).level == logging.DEBUG
    finally:
        get_config().yaml["logging"]["library_debug"] = False
        pipeline = get_pipeline()
        assert pipeline is not None
        install_queue_root(pipeline)
    assert logging.getLogger("httpx").level == logging.WARNING
