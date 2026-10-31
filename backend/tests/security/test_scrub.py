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
from datetime import UTC, datetime
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from app.log_index import _record_to_row
from app.logging_config import (
    JsonFormatter,
    SecretScrubFilter,
    _ConsoleFormatter,
    _scrub_text,
)
from sqlalchemy.orm import Session, sessionmaker

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


# ---------------------------------------------------------------------
# a provider that emits a record while collecting
# ---------------------------------------------------------------------


def test_provider_log_during_collection_does_not_recurse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A provider may emit a record while collecting (auth's
    ``scrub_values`` warns once when its store is already closed at
    shutdown). That record re-enters the filter on the same thread; the
    collector falls back to the last known values instead of recursing —
    and the provider's own record is still scrubbed."""
    import threading

    import app.logging_config as lc

    emit = False
    calls = 0
    # Re-entry depth per thread: a record can pass several scrub filters
    # on its way out (filters accumulate on shared handlers), so extra
    # sequential calls are fine — a call nested inside an active
    # collection on the same thread is the bug this guards.
    local = threading.local()
    max_depth = 0

    def provider() -> list[str]:
        nonlocal calls, max_depth
        calls += 1
        depth = getattr(local, "depth", 0) + 1
        local.depth = depth
        max_depth = max(max_depth, depth)
        try:
            if emit:
                logging.getLogger("app.test.scrub").error(
                    "provider failed near abc123secret00"
                )
            return ["abc123secret00"]
        finally:
            local.depth = depth - 1

    handler = logging.StreamHandler()
    handler.addFilter(SecretScrubFilter())
    test_log = logging.getLogger("app.test.scrub")
    test_log.addHandler(handler)
    lc.register_secret_provider(provider)
    try:
        lc._all_secrets()  # a clean collection seeds the fallback
        emit = True
        with caplog.at_level(logging.ERROR):
            test_log.error("outer record")
    finally:
        lc._secret_providers.remove(provider)
        lc._secrets_cache.clear()
        test_log.removeHandler(handler)

    # The outer record's filter collected; the provider's own record saw
    # the cached values instead of re-entering the collection.
    assert calls >= 2
    assert max_depth == 1
    leaked = [r for r in caplog.records if "abc123secret00" in r.getMessage()]
    assert leaked == []
    assert any(
        "provider failed near [redacted]" in r.getMessage()
        for r in caplog.records
    )


def test_auth_scrub_values_warns_once_per_failure_episode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``AuthService.scrub_values`` runs inside the scrub filter, so a
    record logged on every failure feeds itself forever. It warns once
    per failure episode and resets on the first success."""
    from app.auth.service import AuthService
    from app.config import get_config
    from app.market.clock import FakeClock

    class _EmptySession:
        def __enter__(self) -> _EmptySession:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, _stmt: object) -> object:
            class _Result:
                def scalars(self) -> _Result:
                    return self

                def first(self) -> None:
                    return None

            return _Result()

    class _Factory:
        def __init__(self) -> None:
            self.dead = True

        def __call__(self) -> Session:
            if self.dead:
                raise RuntimeError("engine disposed")
            return cast(Session, _EmptySession())

    factory = _Factory()
    service = AuthService(
        get_config(),
        FakeClock(datetime(2026, 10, 15, 13, 30, tzinfo=UTC)),
        session_factory=cast(sessionmaker[Session], factory),
    )

    with caplog.at_level(logging.ERROR, logger="app.auth.service"):
        assert service.scrub_values() == []
        assert service.scrub_values() == []
    warnings = [
        r for r in caplog.records if "auth scrub values" in r.getMessage()
    ]
    assert len(warnings) == 1

    factory.dead = False
    assert service.scrub_values() == []
    factory.dead = True
    with caplog.at_level(logging.ERROR, logger="app.auth.service"):
        service.scrub_values()
    warnings = [
        r for r in caplog.records if "auth scrub values" in r.getMessage()
    ]
    assert len(warnings) == 2


def test_unregistered_provider_is_not_consulted() -> None:
    """A provider removed at app shutdown must not run on later records:
    left registered, a stale one fails once per failure episode and that
    warn is a stray "logs" publish on whatever app is still live."""
    import app.logging_config as lc

    calls = 0

    def provider() -> list[str]:
        nonlocal calls
        calls += 1
        return ["abc123secret00"]

    lc.register_secret_provider(provider)
    try:
        lc._all_secrets()
        lc.unregister_secret_provider(provider)
        values = lc._all_secrets()
    finally:
        if provider in lc._secret_providers:
            lc._secret_providers.remove(provider)
        lc._secrets_cache.clear()

    assert calls == 1
    assert "abc123secret00" not in values
