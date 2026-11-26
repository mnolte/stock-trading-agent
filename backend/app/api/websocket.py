"""Live updates (docs/SPEC.md, GUI).

The engine publishes topic names whenever stored state changes
(positions filled, a decision stored, an approval created, the kill
switch flipped). Connected sockets get ``{"type": "update",
"topics": [...]}``; the GUI refetches only those REST endpoints,
debounced. A client message still gets a ``pong`` back.

Item 083 (S7): a socket lives only as long as its session. The feed
loop re-checks the session every 60 s on the app clock and closes with
1008 ``session_expired`` when it is gone; sign-out and friends close
their sockets at once through the AuthService registry. At most
``security.max_sockets`` sockets are open — a new one closes the
oldest; with ``SA_AUTH=off`` only the cap applies.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketDisconnected

router = APIRouter()
logger = logging.getLogger(__name__)

# The session re-check interval and the longest the loop sleeps between
# evaluations — short enough that a FakeClock advance is noticed within
# a couple of seconds in tests, cheap enough in production.
SESSION_RECHECK_SECONDS = 60.0
RECHECK_POLL_CAP_SECONDS = 5.0


class UpdateBus:
    """Fan-out of update topics to all connected GUI sockets."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[set[str]]] = set()

    def subscribe(self) -> asyncio.Queue[set[str]]:
        queue: asyncio.Queue[set[str]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[set[str]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, *topics: str) -> None:
        """Notify every socket; called from anywhere in the app."""
        if not self._subscribers:
            return
        payload = set(topics)
        for queue in list(self._subscribers):
            if queue.full():
                # A stale consumer keeps the newest state anyway.
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(payload)


async def _send_json(websocket: WebSocket, payload: dict[str, object]) -> bool:
    """Send one frame; False means the socket was already closed."""
    try:
        await websocket.send_json(payload)
    except WebSocketDisconnected:
        logger.debug("websocket send skipped: socket already closed")
        return False
    return True


@router.websocket("/ws")
async def feed(websocket: WebSocket) -> None:
    bus: UpdateBus = websocket.app.state.update_bus
    auth = getattr(websocket.app.state, "auth", None)
    clock = getattr(websocket.app.state, "clock", None)
    scope_state = websocket.scope.get("state") or {}
    session_row = scope_state.get("session")
    session_token = scope_state.get("session_token") or ""
    session_id = getattr(session_row, "id", None)
    # The re-check needs the session cookie token; without sign-in (or
    # a clock) there is nothing to re-check — the cap still applies.
    recheck = (
        auth is not None and clock is not None and bool(session_token)
    )
    last_check: datetime | None = None
    if recheck and clock is not None:
        last_check = clock.now()
    # Subscribe before accepting: a publish between accept() and
    # subscribe() would be dropped, leaving the client without the
    # update until the next event or reconnect.
    queue = bus.subscribe()
    close_event: asyncio.Event | None = None
    if auth is not None:
        close_event = auth.register_socket(websocket, session_id)
    close_task = (
        asyncio.ensure_future(close_event.wait()) if close_event is not None else None
    )
    try:
        await websocket.accept()
        while True:
            timeout: float | None = None
            if (
                recheck
                and auth is not None
                and clock is not None
                and last_check is not None
            ):
                elapsed = (clock.now() - last_check).total_seconds()
                if elapsed >= SESSION_RECHECK_SECONDS:
                    row, _reason = auth.authenticate(session_token)
                    last_check = clock.now()
                    if row is None:
                        await websocket.close(code=1008, reason="session_expired")
                        return
                    elapsed = 0.0
                timeout = min(
                    max(SESSION_RECHECK_SECONDS - elapsed, 0.1),
                    RECHECK_POLL_CAP_SECONDS,
                )
            elif not recheck:
                # No session to check: still wake periodically so a
                # socket can't outlive a clock that appears later —
                # and so tests see bounded behaviour.
                timeout = RECHECK_POLL_CAP_SECONDS
            topics_task = asyncio.ensure_future(queue.get())
            receive_task = asyncio.ensure_future(websocket.receive_text())
            waiters: set[asyncio.Task[object]] = {topics_task, receive_task}
            if close_task is not None:
                waiters.add(close_task)
            done, pending = await asyncio.wait(
                waiters,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                if task is not close_task:
                    task.cancel()
            if close_task is not None and close_task in done:
                return
            if not done:
                # Poll timeout: loop around and re-evaluate the session.
                continue
            if receive_task in done:
                if receive_task.exception() is not None:
                    raise receive_task.exception()  # type: ignore[misc]
                if len(receive_task.result()) > 64 * 1024:
                    await websocket.close(code=1009)  # message too big
                    return
                if not await _send_json(websocket, {"type": "pong"}):
                    return
            if topics_task in done:
                topics = topics_task.result()
                if not await _send_json(
                    websocket, {"type": "update", "topics": sorted(topics)}
                ):
                    return
    except WebSocketDisconnect:
        pass
    finally:
        if close_task is not None:
            close_task.cancel()
        bus.unsubscribe(queue)
        if auth is not None:
            auth.unregister_socket(websocket)
