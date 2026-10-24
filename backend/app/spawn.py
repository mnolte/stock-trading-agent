"""One helper for every background task (docs/SPEC.md, Error handling).

A task started anywhere in the app goes through ``spawn()``: it is named,
tracked in a registry and a failure is logged at ERROR with a traceback —
never silently dropped. A test fails on bare ``asyncio.create_task``
outside this module (and the websocket fan-out, which manages its own
wait pair).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import Coroutine
from typing import Any

from app.logging_config import ev

logger = logging.getLogger("app")

_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
    """Start ``coro`` as a named, tracked background task."""
    task: asyncio.Task[Any] = asyncio.get_running_loop().create_task(
        coro, name=name
    )
    _tasks.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            ev(
                logger,
                logging.ERROR,
                "task.failed",
                f"background task {name} failed: {exc}",
                exc_info=exc,
                task=name,
            )

    task.add_done_callback(_done)
    return task


def active_tasks() -> list[str]:
    """Names of tasks still running — used by shutdown diagnostics."""
    return sorted(t.get_name() for t in _tasks if not t.done())


def install_global_hooks(loop: asyncio.AbstractEventLoop) -> None:
    """Send every escaping exception to the structured log (spec:
    Global hooks). Called once from the app lifespan."""

    def excepthook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        ev(
            logger,
            logging.CRITICAL,
            "app.unhandled",
            f"unhandled exception: {exc}",
            exc_info=(exc_type, exc, tb),
        )

    def threading_hook(args: threading.ExceptHookArgs) -> None:
        ev(
            logger,
            logging.ERROR,
            "thread.failed",
            f"thread {args.thread.name if args.thread else '?'} failed: {args.exc_value}",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    def asyncio_hook(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        ev(
            logger,
            logging.ERROR,
            "asyncio.unhandled",
            f"asyncio: {context.get('message', exc)}",
            exc_info=exc if isinstance(exc, BaseException) else False,
            task=str(context.get("task", "")),
        )

    sys.excepthook = excepthook
    threading.excepthook = threading_hook
    loop.set_exception_handler(asyncio_hook)
