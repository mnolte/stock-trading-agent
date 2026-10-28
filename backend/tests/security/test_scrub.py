"""PR 28 step 3 — exceptions, URLs and Authorization strings are scrubbed.

The spec's three cases across every handler's output: a fake key inside
an exception's arguments, a credential parameter inside a URL in an
exception message, and an Authorization header — checked against the
file handler's JSON line, the console line and the log-index row.
"""

from __future__ import annotations

import json
import logging
import sys
from zoneinfo import ZoneInfo

import pytest
from app.log_index import _record_to_row
from app.logging_config import (
    JsonFormatter,
    SecretScrubFilter,
    _ConsoleFormatter,
    _scrub_text,
)

FAKE_KEY = "sk-proj-FAKEKEY1234567890"


def _record(message: str, exc: BaseException | None) -> logging.LogRecord:
    exc_info = None
    if exc is not None:
        try:
            raise exc
        except Exception:  # noqa: BLE001 - intentionally catching the test raise
            exc_info = sys.exc_info()  # noqa: BLE001 - re-raising the test exception
    return logging.LogRecord(
        "app.test", logging.ERROR, __file__, 1, message, (), exc_info
    )


# ---------------------------------------------------------------------
# the raw scrubber: URL parameters and Authorization headers
# ---------------------------------------------------------------------


def test_url_credential_parameters_masked() -> None:
    text = _scrub_text(
        "GET https://feed.example/x?token=T0K3N&apikey=AA11&api_key=BB22"
        "&key=CC33&secret=DD44&page=2",
        [],
    )
    for name in ("token", "apikey", "api_key", "key", "secret"):
        assert f"{name}=[redacted]" in text
    for value in ("T0K3N", "AA11", "BB22", "CC33", "DD44"):
        assert value not in text
    assert "page=2" in text  # ordinary parameters survive


def test_authorization_header_masked() -> None:
    assert "[redacted]" in _scrub_text(
        "Authorization: Bearer abcdef123456", []
    )
    assert "abcdef123456" not in _scrub_text(
        "authorization=basic dXNlcjpwYXNz", []
    )


# ---------------------------------------------------------------------
# an exception's arguments and message, through all three handlers
# ---------------------------------------------------------------------


@pytest.fixture
def record() -> logging.LogRecord:
    """An exception carrying the fake key in its args plus a credential
    URL in its message."""
    return _record(
        "provider call failed",
        RuntimeError(
            "fetch https://api.example/stream?token=QUERYT0K3N "
            f"failed for {FAKE_KEY}",
            FAKE_KEY,
        ),
    )


def _outputs(record: logging.LogRecord) -> dict[str, str]:
    """What each handler writes for the record (filter first, like the
    real pipeline: every handler shares SecretScrubFilter)."""
    scrubbed = logging.makeLogRecord(record.__dict__)
    SecretScrubFilter().filter(scrubbed)
    file_line = JsonFormatter("test").format(scrubbed)
    # The console sees exc_text — set it like QueueHandler.prepare does.
    if scrubbed.exc_text is None and scrubbed.exc_info:
        scrubbed.exc_text = logging.Formatter().formatException(scrubbed.exc_info)
        SecretScrubFilter().filter(scrubbed)
    console_line = _ConsoleFormatter(ZoneInfo("UTC")).format(scrubbed)
    row = _record_to_row(scrubbed)
    index_line = json.dumps(row, default=str)
    return {"file": file_line, "console": console_line, "index": index_line}


def test_key_in_exception_args_scrubbed_everywhere(record: logging.LogRecord) -> None:
    outputs = _outputs(record)
    for name, text in outputs.items():
        assert FAKE_KEY not in text, name
        assert "[redacted]" in text, name


def test_url_in_exception_message_scrubbed_everywhere(
    record: logging.LogRecord,
) -> None:
    outputs = _outputs(record)
    for name, text in outputs.items():
        assert "QUERYT0K3N" not in text, name
        assert "token=[redacted]" in text, name


def test_authorization_in_message_scrubbed_everywhere() -> None:
    rec = _record(
        "request headers: Authorization: Bearer hunter2secret", None
    )
    outputs = _outputs(rec)
    for name, text in outputs.items():
        assert "hunter2secret" not in text, name
