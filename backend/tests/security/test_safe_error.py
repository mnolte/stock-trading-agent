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
# Traceback renderers: their output is multi-line exception text — fine
# inside a scrub/logging call, a leak anywhere else (item 105).
_TRACEBACK_ATTRS = {
    "format_exception",
    "format_exc",
    "format_tb",
    "print_exception",
    "print_exc",
}
_NESTED_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
)

# (path suffix, statement substring) -> why raw text is acceptable there.
ALLOWLIST = {
    (
        "log_index.py",
        "log index write failed",
    ): "index-failure fallback: reports an already-scrubbed record; "
    "the exception is a sqlite error, never a provider response",
    (
        "log_index.py",
        "unformattable record",
    ): "only the exception's class name is stored, never its text",
    (
        "data/service.py",
        "type(exc).__name__",
    ): "only the exception's class name is stored, never its text",
    (
        "decide/output.py",
        "exc.msg",
    ): "pydantic/JSONDecodeError .msg is a fixed validation sentence, "
    "never exception text",
    (
        "decide/output.py",
        "exc.errors()",
    ): "pydantic ValidationError .msg entries are fixed validation "
    "sentences, never exception text",
    (
        "ask/tools.py",
        "exc.errors()",
    ): "pydantic ValidationError .msg entries are fixed validation "
    "sentences, never exception text",
}


def _is_exc_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and _EXC_NAME.fullmatch(node.id) is not None


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_scrub_call(node: ast.AST) -> bool:
    """A call whose result is scrubbed or logged — safe to wrap raw
    exception text (``safe_error(exc)``, ``scrub_text(...)``,
    ``logger.warning(...)``)."""
    if not isinstance(node, ast.Call):
        return False
    name = _call_name(node.func)
    return (
        name in _SCRUB_CALLS
        or name in _LOG_NAMES
        or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in _LOG_ATTRS
        )
    )


def _inside_allowed_call(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True when the node sits inside a logging call, the structured
    ``ev`` helper, or a scrub call — the places raw exception text is
    allowed to exist."""
    current = node
    while current in parents:
        current = parents[current]
        if _is_scrub_call(current):
            return True
    return False


def _names_in(node: ast.AST) -> set[str]:
    """Every ``Name`` id inside an expression subtree."""
    return {
        child.id for child in ast.walk(node) if isinstance(child, ast.Name)
    }


def _except_bound_names(scope: ast.AST) -> set[str]:
    """Names bound by ``except ... as X`` in ``scope``.

    Nested functions, lambdas and classes bind their own names — they
    get their own pass, so this walk does not descend into them. The
    usage scan still covers the whole scope, so a nested function that
    closes over an outer ``except`` name is checked against the outer
    set too.
    """
    names: set[str] = set()
    stack = [scope]
    while stack:
        node = stack.pop()
        for child in ast.iter_child_nodes(node):
            if child is not scope and isinstance(child, _NESTED_SCOPES):
                continue
            if isinstance(child, ast.ExceptHandler) and child.name:
                names.add(child.name)
            stack.append(child)
    return names


def _raw_exc_nodes(
    tree: ast.AST, bound: set[str]
) -> dict[int, tuple[ast.AST, str]]:
    """Nodes that can carry raw exception text outside logging/scrub.

    Two families (item 105 widened the second):

    - bare ``*exc`` names anywhere — ``str(exc)``, ``repr(exc)``,
      ``{exc}`` — the original gate (``legacy``);
    - anything built on an ``except``-bound name — ``str(exc.args)``,
      ``{exc.__cause__}``, ``"…".format(exc)``, ``"… %s" % exc`` — plus
      traceback renderers, which are flagged in every scope.

    The value is ``(node, kind)``: ``bound`` candidates get one more
    check — every bound name inside a scrub/logging call (``{safe_error(exc)}``)
    is covered — while ``traceback`` candidates are always reported.
    """
    found: dict[int, tuple[ast.AST, str]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"str", "repr"}
            and node.args
            and not node.keywords
        ):
            if _is_exc_name(node.args[0]):
                found[id(node)] = (node, "legacy")
            elif bound and any(_names_in(a) & bound for a in node.args):
                found[id(node)] = (node, "bound")
        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if not isinstance(value, ast.FormattedValue):
                    continue
                if id(value) in found:
                    continue
                if _is_exc_name(value.value):
                    found[id(value)] = (value, "legacy")
                elif bound and _names_in(value.value) & bound:
                    found[id(value)] = (value, "bound")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and bound
            and any(_names_in(a) & bound for a in node.args)
        ) or (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and bound
            and (
                isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
                or isinstance(node.left, ast.JoinedStr)
            )
            and _names_in(node.right) & bound
        ):
            found[id(node)] = (node, "bound")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _TRACEBACK_ATTRS
        ):
            found[id(node)] = (node, "traceback")
    return found


def _bound_covered(
    node: ast.AST, bound: set[str], parents: dict[ast.AST, ast.AST]
) -> bool:
    """True when every ``except``-bound name inside ``node`` sits inside
    a scrub or logging call — ``{safe_error(exc)}`` is covered, ``{exc}``
    and ``{exc.msg}`` are not."""
    for child in ast.walk(node):
        if not (isinstance(child, ast.Name) and child.id in bound):
            continue
        current: ast.AST = child
        covered = False
        while current is not node and current in parents:
            current = parents[current]
            if _is_scrub_call(current):
                covered = True
                break
        if not covered:
            return False
    return True


def _scopes(tree: ast.AST) -> list[ast.AST]:
    """The module plus every function/lambda — each gets its own
    ``except``-bound names (a nested function also sees the outer set
    through the outer scope's usage scan)."""
    scopes: list[ast.AST] = [tree]
    scopes.extend(
        node
        for node in ast.walk(tree)
        if isinstance(node, _NESTED_SCOPES)
    )
    return scopes


def test_no_raw_exception_text_outside_logging_and_scrub() -> None:
    """Exception-derived text in ``app/`` must sit inside a logging
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
        candidates: dict[int, tuple[ast.AST, str]] = {}
        file_bound: set[str] = set()
        for scope in _scopes(tree):
            bound = _except_bound_names(scope)
            file_bound |= bound
            for node_id, found in _raw_exc_nodes(scope, bound).items():
                # A node found by several scopes keeps the strictest
                # kind: legacy < bound < traceback.
                rank = {"legacy": 0, "bound": 1, "traceback": 2}
                if node_id not in candidates or rank[found[1]] > rank[
                    candidates[node_id][1]
                ]:
                    candidates[node_id] = found
        for node, kind in candidates.values():
            if _inside_allowed_call(node, parents):
                continue
            if kind == "bound" and _bound_covered(node, file_bound, parents):
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
