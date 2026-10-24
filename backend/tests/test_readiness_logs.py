"""PR 20: the two log-index Operations criteria (docs/SPEC.md, Readiness)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

import app.readiness.service as readiness
from app.config import get_config
from app.db.base import get_session_maker
from app.db.models import LogReview
from app.log_index import LogIndexHandler
from app.market.calendar import WeekdayCalendarProvider


def _emit(handler: LogIndexHandler, msg: str, level: int) -> str:
    r = logging.LogRecord("test", level, __file__, 1, msg, (), None)
    r.context = {"cycle": "c-1"}
    r.event = "test.err"
    handler.emit(r)
    handler.flush()
    conn = sqlite3.connect(handler.db_path)
    fp = conn.execute(
        "SELECT fingerprint FROM log_lines WHERE message LIKE ? "
        "ORDER BY id DESC LIMIT 1",
        (f"%{msg}%",),
    ).fetchone()[0]
    conn.close()
    return fp


def _criteria(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> list[dict]:
    """A fresh index in tmp_path, patched into readiness."""
    db = tmp_path / "logs.db"
    monkeypatch.setattr(readiness, "index_path", lambda _cfg: db)
    return readiness._log_criteria(
        session, WeekdayCalendarProvider(), date.today(), get_config()
    )


def _by_key(criteria: list[dict]) -> dict[str, dict]:
    return {c["key"]: c for c in criteria}


def _index(tmp_path: Path) -> LogIndexHandler:
    return LogIndexHandler(tmp_path / "logs.db")


def test_index_unavailable_never_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        readiness, "index_path", lambda _cfg: tmp_path / "missing.db"
    )
    with get_session_maker()() as session:
        criteria = readiness._log_criteria(
            session, WeekdayCalendarProvider(), date.today(), get_config()
        )
    assert _by_key(criteria)["log_errors"]["status"] == "not_yet"
    assert _by_key(criteria)["log_critical"]["status"] == "not_yet"


def test_unreviewed_error_keeps_not_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _index(tmp_path)
    _emit(handler, "boom error", logging.ERROR)
    with get_session_maker()() as session:
        criteria = _criteria(tmp_path, monkeypatch, session)
    assert _by_key(criteria)["log_errors"]["status"] == "not_yet"
    assert _by_key(criteria)["log_errors"]["value"] == "1"


def test_expected_review_passes_on_recurrence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _index(tmp_path)
    fp = _emit(handler, "boom error", logging.ERROR)
    with get_session_maker()() as session:
        session.add(LogReview(
            fingerprint=fp, level="ERROR", sample="boom error",
            review="expected", note="known",
            reviewed_at=datetime(2020, 1, 1), app_version="0.2.0",
        ))
        session.commit()
        criteria = _criteria(tmp_path, monkeypatch, session)
    assert _by_key(criteria)["log_errors"]["status"] == "pass"


def test_fixed_review_reopens_on_recurrence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _index(tmp_path)
    fp = _emit(handler, "boom error", logging.ERROR)
    with get_session_maker()() as session:
        # Review written before the error recurred → unreviewed again.
        session.add(LogReview(
            fingerprint=fp, level="ERROR", sample="boom error",
            review="fixed", note="patched",
            reviewed_at=datetime(2020, 1, 1), app_version="0.2.0",
        ))
        session.commit()
        criteria = _criteria(tmp_path, monkeypatch, session)
    assert _by_key(criteria)["log_errors"]["status"] == "not_yet"


def test_critical_line_fails_cannot_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _index(tmp_path)
    _emit(handler, "critical boom", logging.CRITICAL)
    with get_session_maker()() as session:
        criteria = _criteria(tmp_path, monkeypatch, session)
    c = _by_key(criteria)["log_critical"]
    assert c["status"] == "not_yet" and c["value"] == "1"


def test_clean_log_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _index(tmp_path)
    _emit(handler, "all good", logging.INFO)
    with get_session_maker()() as session:
        criteria = _criteria(tmp_path, monkeypatch, session)
    assert _by_key(criteria)["log_errors"]["status"] == "pass"
    assert _by_key(criteria)["log_critical"]["status"] == "pass"
