"""Break-glass kill switch (item 083, S6).

When sign-in is unavailable — a lost authenticator, a paused sign-in,
a dead session — the agent can still be stopped from the server:

    docker compose exec app python -m app.ops.kill_switch --reason "…"
    docker compose exec app python -m app.ops.kill_switch --reason "…" --flatten
    docker compose exec app python -m app.ops.kill_switch --status

The command writes ``SA_DATA_DIR/kill.request`` (JSON, mode 0600,
``O_EXCL``) and waits up to 30 s for ``kill.result``. The running app
checks for the file every 5 s inside its own event loop — the file is
read, never executed — runs the same code path as the kill-switch
route with event source ``cli``, writes ``kill.result`` and removes
the request. It never resumes trading.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import get_config
from app.errors import safe_error
from app.shutdown import RunLock

if TYPE_CHECKING:
    from app.engine.executor import Executor

logger = logging.getLogger(__name__)

REQUEST_NAME = "kill.request"
RESULT_NAME = "kill.result"
# The app polls for a request this often (spec, Audit of 11 November).
POLL_SECONDS = 5.0
# How long the CLI waits for kill.result.
WAIT_SECONDS = 30.0
# run.lock heartbeats older than this mean the app isn't running.
LOCK_STALE_SECONDS = 90.0


def _paths(data_dir: str | None = None) -> tuple[Path, Path, Path]:
    root = Path(data_dir or get_config().env.sa_data_dir)
    return root / REQUEST_NAME, root / RESULT_NAME, root / "run.lock"


def app_running(data_dir: str | None = None) -> bool:
    """True while run.lock shows a live pid with a fresh heartbeat."""
    _request, _result, lock_path = _paths(data_dir)
    check = RunLock(lock_path).check()
    if check.status != "live":
        return False
    try:
        data = json.loads(lock_path.read_text())
        heartbeat = max(
            float(data.get("heartbeat_at") or 0),
            float(data.get("started_at") or 0),
        )
    except (OSError, TypeError, ValueError):
        return False
    return time.time() - heartbeat <= LOCK_STALE_SECONDS


def _write_result(result_path: Path, payload: dict[str, Any]) -> None:
    fd = os.open(result_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(payload))


async def process_kill_request(
    executor: Executor, request_path: Path, result_path: Path
) -> bool:
    """Handle one ``kill.request`` file: read it, run the kill path,
    write ``kill.result``, remove the request. Returns True when a
    request was consumed. A malformed file is logged and dropped — its
    contents are never executed."""
    if not request_path.exists():
        return False
    try:
        raw = request_path.read_text()
    except OSError:
        logger.warning("kill.request unreadable — ignored")
        request_path.unlink(missing_ok=True)
        return True
    try:
        payload = json.loads(raw)
        reason = str(payload["reason"]).strip()
        flatten = bool(payload.get("flatten"))
        if not reason:
            raise ValueError("reason is empty")
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("malformed kill.request ignored: %s", safe_error(exc))
        request_path.unlink(missing_ok=True)
        try:
            _write_result(result_path, {"ok": False, "error": "malformed request"})
        except OSError:
            logger.warning("could not write kill.result")
        return True
    logger.warning("kill.request picked up: flatten=%s reason=%r", flatten, reason)
    try:
        if flatten:
            await executor.flatten(reason=reason, source="cli")
        else:
            await executor.activate_kill_switch(
                reason=reason, automatic=False, source="cli"
            )
        outcome: dict[str, Any] = {"ok": True}
    except Exception as exc:  # noqa: BLE001 - the result file reports it
        logger.exception("kill.request failed")
        outcome = {"ok": False, "error": safe_error(exc)}
    try:
        _write_result(result_path, outcome)
    except OSError:
        logger.warning("could not write kill.result")
    request_path.unlink(missing_ok=True)
    return True


async def watch_kill_requests(
    executor: Executor,
    data_dir: str,
    shutdown: Any,
) -> None:
    """The in-app half: poll ``kill.request`` every 5 s on the existing
    event loop (never a new thread), act once, write ``kill.result``."""
    request_path, result_path, _lock = _paths(data_dir)
    while not shutdown.shutting_down.is_set():
        await asyncio.sleep(POLL_SECONDS)
        if shutdown.shutting_down.is_set():
            break
        await process_kill_request(executor, request_path, result_path)


def _status(data_dir: str | None = None) -> int:
    """``--status``: is the app running, is the kill switch on, is a
    break-glass request waiting."""
    request_path, _result, _lock = _paths(data_dir)
    running = app_running(data_dir)
    kill_on: bool | None = None
    reason: str | None = None
    try:
        from sqlalchemy import select

        from app.db.base import get_session_maker, shutdown_engine
        from app.db.models import Event

        with get_session_maker()() as session:
            last = session.execute(
                select(Event)
                .where(Event.event_type.in_(("kill_switch", "kill_switch_resume")))
                .order_by(Event.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if last is not None:
                details = last.details or {}
                kill_on = last.event_type == "kill_switch"
                reason = str(details.get("reason") or "") or None
        shutdown_engine()
    except Exception as exc:  # noqa: BLE001 - status must still answer
        logger.warning("could not read kill-switch state: %s", safe_error(exc))
    print(
        json.dumps(
            {
                "app_running": running,
                "kill_switch": kill_on,
                "reason": reason,
                "pending_request": request_path.exists(),
            }
        )
    )
    return 0


def main(
    argv: list[str] | None = None, *, data_dir: str | None = None
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.kill_switch",
        description=(
            "Break-glass: turn the kill switch on when sign-in is "
            "unavailable. It never resumes trading."
        ),
    )
    parser.add_argument("--reason", help="why the kill switch is going on")
    parser.add_argument(
        "--flatten",
        action="store_true",
        help="also cancel all orders and sell every position",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="print whether the app runs and the kill-switch state",
    )
    args = parser.parse_args(argv)

    if args.status:
        return _status(data_dir)

    reason = (args.reason or "").strip()
    if not reason:
        print("a --reason is required", file=sys.stderr)
        return 2
    if not app_running(data_dir):
        print(
            "the app is not running (no fresh run.lock heartbeat) — "
            "start it first, then retry",
            file=sys.stderr,
        )
        return 1

    request_path, result_path, _lock = _paths(data_dir)
    result_path.unlink(missing_ok=True)
    payload = {
        "reason": reason,
        "flatten": bool(args.flatten),
        "requested_at": time.time(),
    }
    try:
        fd = os.open(request_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(
            "a kill request is already waiting — check --status",
            file=sys.stderr,
        )
        return 1
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(payload))

    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                outcome = json.loads(result_path.read_text())
            except (OSError, ValueError):
                outcome = {"ok": False, "error": "unreadable result"}
            if outcome.get("ok"):
                print(
                    "kill switch is on"
                    + (" (flatten ordered)" if args.flatten else "")
                )
                return 0
            print(
                f"the app refused: {outcome.get('error', 'unknown')}",
                file=sys.stderr,
            )
            return 1
        time.sleep(0.25)
    print(
        f"no answer within {WAIT_SECONDS:.0f}s — is the app still running?",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
