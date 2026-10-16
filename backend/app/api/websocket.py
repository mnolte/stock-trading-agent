"""Live updates (docs/SPEC.md, GUI).

The engine publishes topic names whenever stored state changes
(positions filled, a decision stored, an approval created, the kill
switch flipped). Connected sockets get ``{"type": "update",
"topics": [...]}``; the GUI refetches only those REST endpoints,
debounced. A client message still gets a ``pong`` back.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


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


@router.websocket("/ws")
async def feed(websocket: WebSocket) -> None:
    await websocket.accept()
    bus: UpdateBus = websocket.app.state.update_bus
    queue = bus.subscribe()
    try:
        while True:
            topics_task = asyncio.ensure_future(queue.get())
            receive_task = asyncio.ensure_future(websocket.receive_text())
            done, pending = await asyncio.wait(
                {topics_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if receive_task in done:
                if receive_task.exception() is not None:
                    raise receive_task.exception()  # type: ignore[misc]
                await websocket.send_json({"type": "pong"})
            if topics_task in done:
                topics = topics_task.result()
                await websocket.send_json(
                    {"type": "update", "topics": sorted(topics)}
                )
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(queue)
