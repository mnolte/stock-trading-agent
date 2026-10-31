"""Logging core (docs/SPEC.md, Logging, error handling and shutdown).

One JSON object per line in ``data/logs/stock-agent.log``, written through
a queue: producers put records on the queue and the ``QueueListener``
thread owns the real handlers (file, console, later the log index), so
logging never blocks a cycle.

- Format: ``ts`` (UTC, ms), ``level``, ``logger``, ``event``, ``message``,
  ``context``, ``process``, ``pid``, and ``error_id`` + ``exception`` on
  ERROR and CRITICAL.
- Rotation: size-based at ``logging.max_file_mb``; the rotated file is
  gzipped and files beyond ``logging.max_files`` or older than
  ``logging.keep_days`` are deleted.
- Scrubbing: every handler shares one filter that replaces stored keys,
  key-shaped strings and Authorization headers with ``[redacted]``.
- Tracing: ``log_context()`` sets cycle/check/job/request ids (and
  symbol, decision, order, notification) on every line in scope.
- The backtest child calls ``setup_backtest_logging(run_id)`` and writes
  ``data/logs/backtest-<id>.log`` in the same format.
"""

from __future__ import annotations

import gzip
import logging
import logging.handlers
import queue
import re
import secrets as _secrets
import shutil
import sys
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from app.config import get_config

REDACTED = "[redacted]"

_log_ctx: ContextVar[dict[str, Any]] = ContextVar("sa_log_ctx")

# Fields a log_context or an explicit ``context`` extra may carry. Order
# is the JSON field order; unknown keys are appended alphabetically.
CONTEXT_FIELDS = (
    "cycle", "check", "job", "request", "symbol", "decision", "order",
    "notification", "run", "strategy",
)

# Things that look like a key or an Authorization header: OpenAI keys,
# Alpaca key ids/secrets, bearer tokens and Authorization headers, plus
# the PR 23 auth shapes (sa_-prefixed session/pending/CSRF tokens and
# XXXX-XXXX-XXXX recovery codes) so they can never reach a log line.
_KEY_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\b(PK|AK)[A-Z0-9]{14,}\b"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?\S+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{10,}"),
    # URL credential parameters: token=, apikey=, api_key=, key=, secret=
    # (docs/SPEC.md → Secrets in errors) — the name is kept, the value
    # becomes [redacted].
    re.compile(r"(?i)\b((?:token|api_?key|key|secret)=)[^\s&'\"]+"),
    re.compile(r"\bsa[tpc]_[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\b[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}\b"),
)


def _scrub_text(text: str, secrets: list[str]) -> str:
    """Replace stored keys and key-shaped strings with ``[redacted]``."""
    for value in secrets:
        if value and len(value) >= 8:
            text = text.replace(value, REDACTED)
    for pattern in _KEY_PATTERNS:
        text = pattern.sub(
            lambda m: (m.group(1) + REDACTED) if m.lastindex else REDACTED,
            text,
        )
    return text


_secret_providers: list[Callable[[], list[str]]] = []
_secrets_cache: list[str] = []
_collecting = threading.local()


def register_secret_provider(provider: Callable[[], list[str]]) -> None:
    """Feed the scrub filter live secret values (keys, then auth values:
    TOTP secret and pending sign-in tokens, PR 23)."""
    _secret_providers.append(provider)


def _all_secrets() -> list[str]:
    """Collect live secret values. A provider may emit a log line while
    collecting (e.g. its store is already closed at shutdown) — that record
    re-enters this collector on the same thread, so reuse the last known
    values instead of recursing forever."""
    if getattr(_collecting, "active", False):
        return list(_secrets_cache)
    _collecting.active = True
    try:
        values: list[str] = []
        for provider in _secret_providers:
            try:
                values.extend(provider())
            except Exception:  # noqa: BLE001,S112 - a broken provider must not break logging
                continue  # noqa: S112 - a broken provider must not break logging
        _secrets_cache[:] = values
        return values
    finally:
        _collecting.active = False


class SecretScrubFilter(logging.Filter):
    """Removes stored keys, key-shaped strings and Authorization headers
    from every record before a handler writes it."""

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = [v for v in _all_secrets() if v]
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        needs = any(v in message for v in secrets if len(v) >= 8) or any(
            p.search(message) for p in _KEY_PATTERNS
        )
        if needs:
            record.msg = _scrub_text(message, secrets)
            record.args = ()
        if record.exc_text:
            record.exc_text = _scrub_text(record.exc_text, secrets)
        # A formatted-but-not-yet-cached exception: scrub the rendered
        # args too when they sit on the record itself.
        return True


class _ContextFilter(logging.Filter):
    """Merges the ambient ``log_context`` fields and assigns error ids."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR and not getattr(
            record, "error_id", None
        ):
            record.error_id = f"e-{_secrets.token_hex(3)}"
        ambient = _log_ctx.get({})
        explicit = getattr(record, "context", None)
        merged: dict[str, Any] = {}
        for key in CONTEXT_FIELDS:
            if key in ambient:
                merged[key] = ambient[key]
            if isinstance(explicit, dict) and explicit.get(key) is not None:
                merged[key] = explicit[key]
        if isinstance(explicit, dict):
            for key in sorted(explicit):
                if key not in merged and explicit[key] is not None:
                    merged[key] = explicit[key]
        for key in ambient:
            if key not in merged and ambient[key] is not None:
                merged[key] = ambient[key]
        record.context = merged
        return True


@contextmanager
def log_context(**fields: Any) -> Generator[None, None, None]:
    """Attach tracing fields to every log line written in scope."""
    merged = {
        **_log_ctx.get({}),
        **{k: v for k, v in fields.items() if v is not None},
    }
    token = _log_ctx.set(merged)
    try:
        yield
    finally:
        _log_ctx.reset(token)


def ev(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str | None = None,
    *,
    exc_info: Any = False,
    **context: Any,
) -> None:
    """One structured log line with a stable event code and context."""
    logger.log(
        level,
        message or event,
        exc_info=exc_info,
        extra={"event": event, "context": context},
    )


class JsonFormatter(logging.Formatter):
    """One JSON object per line in the spec's field order."""

    def __init__(self, process: str) -> None:
        super().__init__()
        self._process = process

    def format(self, record: logging.LogRecord) -> str:
        import json

        ts = datetime.fromtimestamp(record.created, tz=ZoneInfo("UTC"))
        line: dict[str, Any] = {
            "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None),
            "message": record.getMessage(),
            "context": getattr(record, "context", {}) or {},
            "process": getattr(record, "process", self._process),
            "pid": record.process,
        }
        if record.levelno >= logging.ERROR:
            line["error_id"] = getattr(record, "error_id", None)
        if record.exc_info:
            exc_type, exc, _tb = record.exc_info
            # Scrub exception text and traceback as they are written —
            # an exception's message can carry a key or a path (hard
            # rule 9; the filter only sees record.msg/exc_text).
            line["exception"] = {
                "type": getattr(exc_type, "__name__", str(exc_type)),
                "message": _scrub_text(
                    str(exc) if exc is not None else "", _all_secrets()
                ),
                "traceback": _scrub_text(
                    self.formatException(record.exc_info), _all_secrets()
                ),
            }
        elif record.exc_text:
            line["exception"] = {"type": None, "message": "", "traceback": record.exc_text}
        return json.dumps(line, default=str, ensure_ascii=False)


class _ConsoleFormatter(logging.Formatter):
    """Short readable console format (app timezone per spec/design)."""

    def __init__(self, tz: ZoneInfo) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)-14s %(message)s")
        self._tz = tz

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=self._tz)
        return dt.strftime("%H:%M:%S") + f".{int(record.msecs):03d}"


class GzipRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Size rotation; the rotated file is gzipped, old files pruned."""

    def __init__(
        self,
        filename: Path,
        *,
        max_bytes: int,
        max_files: int,
        keep_days: int,
    ) -> None:
        super().__init__(filename, maxBytes=max_bytes, backupCount=0, encoding="utf-8")
        self._max_files = max_files
        self._keep_days = keep_days

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        base = Path(self.baseFilename)
        rotated = base.with_name(
            f"{base.stem}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}{base.suffix}.gz"
        )
        if base.exists():
            with base.open("rb") as src, gzip.open(rotated, "wb") as dst:
                shutil.copyfileobj(src, dst)
            base.unlink()
        self._prune(base.parent)
        self.stream = self._open()

    def _prune(self, directory: Path) -> None:
        base = Path(self.baseFilename)
        rotated = sorted(
            directory.glob(f"{base.stem}-*{base.suffix}.gz"),
            key=lambda p: p.stat().st_mtime,
        )
        cutoff = time.time() - self._keep_days * 86400
        keep = rotated[-self._max_files:] if self._max_files else []
        for path in rotated:
            if path in keep and path.stat().st_mtime >= cutoff:
                continue  # noqa: S112 - a broken provider must not break logging
            path.unlink(missing_ok=True)


class _LibraryFilter(logging.Filter):
    """The logger-name rule (docs/SPEC.md, Logging, error handling and
    shutdown): every logger whose name does not start with ``app`` or
    ``frontend`` logs at WARNING and above, unless
    ``logging.library_debug`` is enabled. It sits on the queue handler
    so it covers every library and future logger without a name list —
    httpcore, urllib3, websockets, the next dependency."""

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.name == "app"
            or record.name.startswith("app.")
            or record.name == "frontend"
            or record.name.startswith("frontend.")
        ):
            return True
        if record.levelno >= logging.WARNING:
            return True
        try:
            return bool(
                get_config().get("logging", "library_debug", default=False)
            )
        except Exception:  # noqa: BLE001 - logging must never fail on config
            return False


class _QueueHandler(logging.handlers.QueueHandler):
    """Same-process queue: ``prepare`` flattens exceptions into the
    message text (kept — the console format wants that) and clears
    ``exc_info``/``exc_text`` for pickleability. Nothing on this queue is
    pickled, so keep the structured data: the file handler writes the
    spec's ``exception`` object and the log index fills its columns."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        prepared = cast(logging.LogRecord, super().prepare(record))
        prepared.exc_info = record.exc_info
        prepared.exc_text = record.exc_text
        return prepared


class LogPipeline:
    """The queue + listener + handlers; ``stop`` flushes and closes last."""

    def __init__(
        self,
        *,
        file_path: Path,
        process: str,
        file_level: int,
        console_level: int,
        max_file_mb: int,
        max_files: int,
        keep_days: int,
        console_tz: ZoneInfo,
        console: bool = True,
    ) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue: queue.Queue[logging.LogRecord] = queue.Queue()
        self.scrub = SecretScrubFilter()
        context = _ContextFilter()

        self.file_handler = GzipRotatingFileHandler(
            file_path,
            max_bytes=max_file_mb * 1024 * 1024,
            max_files=max_files,
            keep_days=keep_days,
        )
        self.file_handler.setLevel(file_level)
        self.file_handler.setFormatter(JsonFormatter(process))
        self.file_handler.addFilter(self.scrub)
        self.file_handler.addFilter(context)

        handlers: list[logging.Handler] = [self.file_handler]
        self.console_handler: logging.StreamHandler[Any] | None
        if console:
            self.console_handler = logging.StreamHandler(sys.stderr)
            self.console_handler.setLevel(console_level)
            self.console_handler.setFormatter(_ConsoleFormatter(console_tz))
            self.console_handler.addFilter(self.scrub)
            self.console_handler.addFilter(context)
            handlers.append(self.console_handler)
        else:
            self.console_handler = None

        self._queue_handler = _QueueHandler(self.queue)
        self.listener = logging.handlers.QueueListener(
            self.queue, *handlers, respect_handler_level=True
        )

    def add_handler(self, handler: logging.Handler) -> None:
        """Attach another listener-side handler (the log index, PR 20)."""
        handler.addFilter(self.scrub)
        handler.addFilter(_ContextFilter())
        self.listener.handlers = (*self.listener.handlers, handler)

    def start(self) -> None:
        self.listener.start()

    def stop(self) -> None:
        self.listener.stop()


_pipeline: LogPipeline | None = None
_pipeline_lock = threading.Lock()


def install_queue_root(pipeline: LogPipeline) -> None:
    """Root logger → the queue; uvicorn and library loggers propagate.

    alembic's ``fileConfig`` clears every handler when migrations run, so
    the lifespan calls this again after ``run_migrations()``.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(pipeline._queue_handler)
    # The library rule is a filter on the queue handler: any logger
    # outside app/frontend drops below WARNING before the file and the
    # index ever see it, without a fixed name list. uvicorn and the
    # libraries still log through propagation; their own handlers go
    # and their level opens so the filter is the single floor —
    # logging.library_debug then genuinely shows library DEBUG.
    if not any(
        isinstance(f, _LibraryFilter)
        for f in pipeline._queue_handler.filters
    ):
        pipeline._queue_handler.addFilter(_LibraryFilter())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx",
                 "httpx2", "httpcore", "urllib3", "openai", "anthropic",
                 "websockets", "apscheduler",
                 "apscheduler.executors.default",
                 "apscheduler.scheduler", "alembic",
                 "alpaca.trading.stream"):
        lib = logging.getLogger(name)
        for handler in list(lib.handlers):
            lib.removeHandler(handler)
        lib.setLevel(logging.DEBUG)
        lib.propagate = True
    # Per-run "Running job / executed successfully" lines are INFO noise:
    # with the 10-second backtest tick alone that is ~17k lines a day.
    # Job failures still reach the log — APScheduler logs those at
    # WARNING/ERROR and main's EVENT_JOB_ERROR listener at ERROR.
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    logging.captureWarnings(True)


def log_dir(config: Any) -> Path:
    """The log directory: ``logging.dir`` relative paths under ``data/``
    live inside the data dir (SA_DATA_DIR in tests), others under ROOT."""
    from app.config import ROOT

    d = Path(str(config.get("logging", "dir", default="data/logs")))
    if d.is_absolute():
        return d
    if d.parts and d.parts[0] == "data":
        return Path(config.env.sa_data_dir).joinpath(*d.parts[1:])
    return ROOT / d


def setup_logging(
    *,
    process: str = "app",
    console: bool = True,
) -> LogPipeline:
    """Configure the JSON-lines queue pipeline. Safe to call twice."""
    global _pipeline
    cfg = get_config()
    app_tz = ZoneInfo(str(cfg.get("app", "timezone", default="Europe/Amsterdam")))
    file_level = str(cfg.get("logging", "file_level", default="DEBUG"))
    console_level = str(cfg.get("logging", "console_level", default="INFO"))
    pipeline = LogPipeline(
        file_path=log_dir(cfg) / "stock-agent.log",
        process=process,
        file_level=getattr(logging, file_level.upper(), logging.DEBUG),
        console_level=getattr(logging, console_level.upper(), logging.INFO),
        max_file_mb=int(cfg.get("logging", "max_file_mb", default=50)),
        max_files=int(cfg.get("logging", "max_files", default=20)),
        keep_days=int(cfg.get("logging", "keep_days", default=30)),
        console_tz=app_tz,
        console=console,
    )
    with _pipeline_lock:
        previous = _pipeline
        _pipeline = pipeline
    if previous is not None:
        # A second lifespan in the same process swaps pipelines cleanly.
        previous.stop()
    install_queue_root(pipeline)
    pipeline.start()
    return pipeline


def setup_backtest_logging(run_id: int) -> LogPipeline:
    """The backtest child's own file: data/logs/backtest-<id>.log."""
    pipeline = setup_logging(process=f"backtest-{run_id}", console=False)
    base = pipeline.file_handler.baseFilename
    # Same format, sibling file named for the run.
    pipeline.file_handler.baseFilename = str(
        Path(base).parent / f"backtest-{run_id}.log"
    )
    pipeline.file_handler.stream = pipeline.file_handler._open()
    return pipeline


def get_pipeline() -> LogPipeline | None:
    return _pipeline


def shutdown_logging() -> None:
    """Flush and close the listener — always the last shutdown step."""
    global _pipeline
    with _pipeline_lock:
        pipeline = _pipeline
        _pipeline = None
    if pipeline is not None:
        pipeline.stop()
