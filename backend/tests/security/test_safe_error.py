"""Item 081 — ``safe_error`` and the second-line scrub.

``safe_error`` is the one way exception-derived text leaves the process
boundary (hard rule 9): URL queries dropped, the log scrubber's key
patterns and every registered secret value replaced, 300 characters
max. The AST test proves no ``str(exc)``/``{exc}`` reaches a stored,
shown, sent or model-visible string outside logging and scrub calls.
The service tests prove the second line: notification rows, email
bodies and event details are scrubbed again at persistence time.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.config import get_config
from app.db.base import Base
from app.db.models import Event, Notification
from app.engine.state import record_event
from app.errors import safe_error
from app.logging_config import (
    _secrets_cache,
    register_secret_provider,
    unregister_secret_provider,
)
from app.market.clock import FakeClock
from app.notifications.mail import FakeMailer
from app.notifications.service import NotificationService
from app.secrets_service import SecretsStore
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import public_resolver

FAKE_KEY = "sk-proj-FAKEKEY1234567890"
STORED_SECRET = "abc123secret00"

APP_DIR = Path(__file__).resolve().parents[2] / "app"


# ---------------------------------------------------------------------
# safe_error
# ---------------------------------------------------------------------


def test_safe_error_strips_key_from_exception_args() -> None:
    exc = RuntimeError("provider call failed", FAKE_KEY)
    out = safe_error(exc)
    assert FAKE_KEY not in out
    assert "[redacted]" in out
    assert "provider call failed" in out


def test_safe_error_accepts_plain_text() -> None:
    assert safe_error("plain failure") == "plain failure"


def test_safe_error_drops_url_query_string() -> None:
    out = safe_error(
        "GET https://api.example/feed/x?token=T0K3N&apikey=AA11&page=2 "
        "failed"
    )
    assert "T0K3N" not in out
    assert "AA11" not in out
    assert "page=2" not in out  # the whole query goes, not just secrets
    assert "https://api.example/feed/x" in out


def test_safe_error_keeps_url_without_query() -> None:
    out = safe_error("see https://api.example/feed/x for details")
    assert "https://api.example/feed/x" in out


def test_safe_error_scrubs_registered_secret_values() -> None:
    provider = lambda: [STORED_SECRET]  # noqa: E731
    register_secret_provider(provider)
    _secrets_cache.clear()
    try:
        out = safe_error(f"call failed near {STORED_SECRET} end")
    finally:
        unregister_secret_provider(provider)
        _secrets_cache.clear()
    assert STORED_SECRET not in out
    assert "[redacted]" in out


def test_safe_error_scrubs_extra_values_without_a_provider() -> None:
    out = safe_error(f"call failed near {STORED_SECRET} end", extra=[STORED_SECRET])
    assert STORED_SECRET not in out


def test_safe_error_truncates_to_300_chars() -> None:
    out = safe_error("x" * 1000)
    assert len(out) == 300


# ---------------------------------------------------------------------
# the AST gate: no raw exception text outside logging and scrub calls
# ---------------------------------------------------------------------

_EXC_NAME = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*_)?exc$")
_SCRUB_CALLS = {
    "safe_error",
    "scrub",
    "scrub_text",
    "scrub_value",
    "_scrub_text",
}
_LOG_ATTRS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
_LOG_NAMES = {"ev"}

# (path suffix, statement substring) -> why raw text is acceptable there.
ALLOWLIST = {
    (
        "log_index.py",
        "log index write failed",
    ): "index-failure fallback: reports an already-scrubbed record; "
    "the exception is a sqlite error, never a provider response",
}


def _is_exc_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and _EXC_NAME.fullmatch(node.id) is not None


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _inside_allowed_call(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True when the node sits inside a logging call, the structured
    ``ev`` helper, or a scrub call — the places raw exception text is
    allowed to exist."""
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Call):
            name = _call_name(current.func)
            if name in _SCRUB_CALLS or name in _LOG_NAMES:
                return True
            if (
                isinstance(current.func, ast.Attribute)
                and current.func.attr in _LOG_ATTRS
            ):
                return True
    return False


def _raw_exc_nodes(tree: ast.AST) -> list[ast.AST]:
    """``str(exc)``/``repr(exc)`` calls and f-string ``{exc}`` values."""
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"str", "repr"}
            and len(node.args) == 1
            and not node.keywords
            and _is_exc_name(node.args[0])
        ):
            found.append(node)
        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if (
                    isinstance(value, ast.FormattedValue)
                    and _is_exc_name(value.value)
                ):
                    found.append(value)
    return found


def test_no_raw_exception_text_outside_logging_and_scrub() -> None:
    """Every ``str(exc)``/``{exc}`` in ``app/`` must sit inside a logging
    call, ``ev`` or a scrub call — anything else can reach a stored row,
    an API answer, an email or a model (hard rule 9)."""
    violations: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        rel = f"app/{path.relative_to(APP_DIR).as_posix()}"
        for node in _raw_exc_nodes(tree):
            if _inside_allowed_call(node, parents):
                continue
            # The allowlist matches on the enclosing statement's text.
            stmt = node
            while stmt in parents and not isinstance(parents[stmt], ast.stmt):
                stmt = parents[stmt]
            if isinstance(parents.get(stmt), ast.stmt):
                stmt = parents[stmt]
            segment = ast.get_source_segment(source, stmt) or ""
            if any(
                rel.endswith(suffix) and marker in segment
                for suffix, marker in ALLOWLIST
            ):
                continue
            violations.append(
                f"{rel}:{getattr(node, 'lineno', '?')} "
                f"{ast.get_source_segment(source, node) or node!r}"
            )
    assert violations == [], (
        "raw exception text outside logging/scrub calls — route it "
        "through safe_error:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------
# second line: notifications and events are scrubbed at persistence
# ---------------------------------------------------------------------

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)

SessionFactory = sessionmaker[Session]


@pytest.fixture
def session_factory(tmp_path: Path) -> SessionFactory:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def secrets(session_factory: SessionFactory) -> SecretsStore:
    return SecretsStore(get_config(), FakeClock(NOW), session_factory=session_factory)


@pytest.mark.anyio
async def test_notification_row_and_email_are_scrubbed(
    session_factory: SessionFactory,
    secrets: SecretsStore,
    clock: FakeClock,
) -> None:
    """A key-shaped string smuggled into a notification line lands in
    neither the stored row nor the email body."""
    mailer = FakeMailer()
    service = NotificationService(
        config=get_config(),
        clock=clock,
        secrets=secrets,
        mailer=mailer,
        session_factory=session_factory,
        resolver=public_resolver,
    )
    secrets.save("smtp_password", "smtp-secret-98765")
    result = await service.save_smtp(
        host="smtp.test",
        port=587,
        username="agent@test",
        from_addr="agent@test",
        to_addr="marc@test",
    )
    assert result["ok"] is True
    mailer.sent.clear()

    service.notify(
        "source_failing",
        f"fred failed: {FAKE_KEY} and token=QUERYT0K3N",
        details={"nested": {"error": f"see {FAKE_KEY}"}},
        body=f"body mentions {FAKE_KEY}",
    )
    await service.drain()

    with session_factory() as session:
        (row,) = session.execute(select(Notification)).scalars().all()
        for text in (row.subject, row.body or ""):
            assert FAKE_KEY not in text
            assert "QUERYT0K3N" not in text
    assert mailer.sent, "expected one email"
    assert FAKE_KEY not in mailer.sent[-1].body
    assert "QUERYT0K3N" not in mailer.sent[-1].body


def test_record_event_details_are_scrubbed(
    session_factory: SessionFactory, clock: FakeClock
) -> None:
    provider = lambda: [STORED_SECRET]  # noqa: E731
    register_secret_provider(provider)
    _secrets_cache.clear()
    try:
        with session_factory() as session:
            record_event(
                session,
                clock,
                level="warning",
                event_type="test_event",
                details={
                    "error": f"failed near {STORED_SECRET}",
                    "nested": [f"also {FAKE_KEY}"],
                },
            )
            session.commit()
            (event,) = session.execute(select(Event)).scalars().all()
    finally:
        unregister_secret_provider(provider)
        _secrets_cache.clear()
    assert STORED_SECRET not in str(event.details)
    assert FAKE_KEY not in str(event.details)
    assert "[redacted]" in str(event.details)
