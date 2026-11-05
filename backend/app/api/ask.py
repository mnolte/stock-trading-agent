"""Ask the agent API (docs/SPEC.md → Ask the agent → API).

Threads, SSE answers, Stop and notes — all behind the request gate and
sign-in like every route. ``POST …/messages`` answers a
``text/event-stream`` with ``step``, ``delta``, ``refs``, ``done`` and
``error`` events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from app.ask import service as ask_service
from app.ask.service import answer, monthly_questions, monthly_spend
from app.ask.tools import AskDeps
from app.config import get_config
from app.data.cache import _naive_utc
from app.db.base import get_session_maker
from app.db.models import AskMessage, AskNote, AskThread
from app.errors import ApiError, ErrorCode
from app.llm.clients import make_model_clients
from app.market.clock import make_clock

logger = logging.getLogger(__name__)

router = APIRouter()

_QUESTION_MAX = 2000
_NOTE_MAX = 1000
_rate: dict[str, deque[float]] = defaultdict(deque)


def _clock(request: Request) -> Any:
    return getattr(request.app.state, "clock", None) or make_clock()


def _deps(request: Request) -> AskDeps:
    return AskDeps(
        config=get_config(),
        clock=_clock(request),
        session_factory=get_session_maker(),
        calendar=getattr(request.app.state, "calendar_store", None),
        exec_state=getattr(request.app.state, "exec_state", None),
        market_data=getattr(request.app.state, "market_data", None),
        signals=getattr(request.app.state, "signal_service", None),
        secrets=getattr(request.app.state, "secrets_store", None),
        notification_service=getattr(request.app.state, "notification_service", None),
        ops=getattr(request.app.state, "ops", None),
    )


def _enabled(request: Request) -> dict[str, Any]:
    """(enabled, reason) for the composer: off, no key, or monthly cap."""
    config = get_config()
    if not bool(config.get("ask", "enabled", default=True)):
        return {"enabled": False, "reason": "Ask the agent is off in Settings."}
    model = str(config.get("ask", "model", default="claude-sonnet-5-5"))
    if config.env.mode != "simulated":
        from app.llm.models import PROVIDER_KEY, model_provider

        provider = model_provider(config, model)
        store = getattr(request.app.state, "secrets_store", None)
        key_name = PROVIDER_KEY.get(provider, provider)
        if store is not None and not store.provider_ok(key_name):
            return {
                "enabled": False,
                "reason": f"{model} needs the {provider} key — set it in Settings.",
            }
    with get_session_maker()() as session:
        spent = monthly_spend(session, config, _clock(request))
        cap = float(config.get("ask", "monthly_cost_cap_usd", default=15))
        n, qspent = monthly_questions(session, config, _clock(request))
    return {
        "enabled": float(spent) < cap,
        "reason": (
            None
            if float(spent) < cap
            else (
                f"Ask has used its ${cap:.2f} for this month. "
                "It starts again on the 1st, or raise the cap in Settings."
            )
        ),
        "model": model,
        "spent_usd": str(spent),
        "cap_usd": cap,
        "questions": n,
        "questions_cost_usd": str(qspent),
        "avg_cost_usd": (str(qspent / n) if n else None),
    }


@router.get("/ask/status")
def ask_status(request: Request) -> dict[str, Any]:
    return _enabled(request)


def _rate_check(request: Request, config: Any) -> None:
    limit = config.get_int("ask", "max_questions_per_minute", default=10)
    from app.auth.guard import client_ip

    key = client_ip(request, config)
    now = time.monotonic()
    q = _rate[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        raise ApiError(429, ErrorCode.RATE_LIMITED, retry_after=int(60 - (now - q[0])) or 1)
    q.append(now)
    # Keep the map bounded: sweep emptied buckets when it grows.
    if len(_rate) > 1000:
        for k in [k for k, d in _rate.items() if not d or now - d[-1] > 60]:
            del _rate[k]


def _thread_json(row: AskThread, count: int | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "context": row.context,
        "created_at": row.created_at.isoformat() + "Z",
        "updated_at": row.updated_at.isoformat() + "Z",
        "questions": count,
    }


@router.get("/ask/threads")
def list_threads(
    request: Request, search: str | None = Query(default=None, max_length=200)
) -> dict[str, Any]:
    with get_session_maker()() as session:
        q = select(AskThread).order_by(AskThread.updated_at.desc()).limit(200)
        rows = session.execute(q).scalars().all()
        counts = dict(
            session.execute(
                select(AskMessage.thread_id, func.count(AskMessage.id))
                .where(AskMessage.role == "user")
                .group_by(AskMessage.thread_id)
            ).all()
        )
        if search:
            needle = search.strip().lower()
            rows = [r for r in rows if needle in r.title.lower()]
    return {
        "threads": [_thread_json(r, int(counts.get(r.id, 0))) for r in rows],
        "keep_days": int(get_config().get("ask", "keep_days", default=90)),
    }


class ThreadBody(BaseModel):
    context: dict[str, Any] | None = None


@router.post("/ask/threads")
def create_thread(request: Request, body: ThreadBody) -> dict[str, Any]:
    clock = _clock(request)
    with get_session_maker()() as session:
        row = AskThread(
            title="New chat",
            context=body.context,
            created_at=_naive_utc(clock.now()),
            updated_at=_naive_utc(clock.now()),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _thread_json(row, 0)


@router.get("/ask/threads/{thread_id}")
def get_thread(request: Request, thread_id: int) -> dict[str, Any]:
    with get_session_maker()() as session:
        row = session.get(AskThread, thread_id)
        if row is None:
            raise HTTPException(404)
        messages = (
            session.execute(
                select(AskMessage).where(AskMessage.thread_id == thread_id).order_by(AskMessage.id)
            )
            .scalars()
            .all()
        )
    return {
        **_thread_json(row),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "text": m.text,
                "steps": m.steps,
                "references": m.references,
                "unverified_numbers": m.unverified_numbers,
                "model": m.model,
                "cost": str(m.cost) if m.cost is not None else None,
                "status": m.status,
                "error": m.error,
                "created_at": m.created_at.isoformat() + "Z",
            }
            for m in messages
        ],
    }


@router.delete("/ask/threads/{thread_id}")
def delete_thread(request: Request, thread_id: int) -> dict[str, Any]:
    with get_session_maker()() as session:
        row = session.get(AskThread, thread_id)
        if row is None:
            raise HTTPException(404)
        session.execute(
            delete(AskMessage).where(AskMessage.thread_id == thread_id)
        )
        session.execute(
            update(AskNote)
            .where(AskNote.source_thread_id == thread_id)
            .values(source_thread_id=None, source_message_id=None)
        )
        session.delete(row)
        session.commit()
    return {"ok": True}


class MessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=_QUESTION_MAX)
    context: dict[str, Any] | None = None


@router.post("/ask/threads/{thread_id}/messages")
async def post_message(request: Request, thread_id: int, body: MessageBody) -> StreamingResponse:
    config = get_config()
    status = _enabled(request)
    if not status["enabled"]:
        # cap reached → a stored cap message, no model call.
        with get_session_maker()() as session:
            if session.get(AskThread, thread_id) is None:
                raise HTTPException(404)
            if "month" in (status.get("reason") or "") or "cap" in (status.get("reason") or ""):
                clock = _clock(request)
                msg = AskMessage(
                    thread_id=thread_id,
                    role="assistant",
                    text="",
                    status="cap",
                    error=status["reason"],
                    created_at=_naive_utc(clock.now()),
                )
                user = AskMessage(
                    thread_id=thread_id,
                    role="user",
                    text=body.text,
                    created_at=_naive_utc(clock.now()),
                )
                session.add_all([user, msg])
                session.commit()
                return StreamingResponse(
                    _cap_stream(msg.id, status["reason"]),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            raise ApiError(
                422, ErrorCode.INVALID, fields=[{"name": "ask", "message": status["reason"]}]
            )
    _rate_check(request, config)
    with get_session_maker()() as session:
        thread = session.get(AskThread, thread_id)
        if thread is None:
            raise HTTPException(404)
        clock = _clock(request)
        user = AskMessage(
            thread_id=thread_id,
            role="user",
            text=body.text,
            context=body.context,
            created_at=_naive_utc(clock.now()),
        )
        session.add(user)
        if thread.title in ("New chat", ""):
            thread.title = body.text[:60]
        message = AskMessage(
            thread_id=thread_id,
            role="assistant",
            text="",
            status="ok",
            created_at=_naive_utc(clock.now()),
        )
        session.add(message)
        session.commit()
        session.refresh(message)
        message_id = message.id

    async def stream() -> AsyncIterator[str]:
        deps = _deps(request)
        clients = getattr(request.app.state, "model_clients", None)
        if clients is None:
            clients = make_model_clients(config, getattr(request.app.state, "secrets_store", None))
        try:
            async for ev in answer(
                client=clients,
                deps=deps,
                session_factory=get_session_maker(),
                thread_id=thread_id,
                message_id=message_id,
                question=body.text,
                context=body.context,
            ):
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'])}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            ask_service.request_stop(message_id)
            raise
        except Exception:  # noqa: BLE001
            logger.exception("ask answer failed")
            yield 'event: error\ndata: {"message": "the answer failed"}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _cap_stream(message_id: int, reason: str | None) -> AsyncIterator[str]:
    yield "event: done\ndata: " + json.dumps(
        {"id": message_id, "status": "cap", "message": reason}
    ) + "\n\n"


@router.post("/ask/messages/{message_id}/stop")
def stop_message(request: Request, message_id: int) -> dict[str, Any]:
    ask_service.request_stop(message_id)
    return {"ok": True}


# ---------------------------------------------------------------------
# notes


def _note_json(n: AskNote, thread_title: str | None) -> dict[str, Any]:
    return {
        "id": n.id,
        "text": n.text,
        "references": n.references or [],
        "source_thread_id": n.source_thread_id,
        "source_message_id": n.source_message_id,
        "source_chat": thread_title,
        "created_at": n.created_at.isoformat() + "Z",
        "updated_at": n.updated_at.isoformat() + "Z",
    }


@router.get("/ask/notes")
def list_notes(request: Request) -> dict[str, Any]:
    with get_session_maker()() as session:
        rows = (
            session.execute(select(AskNote).order_by(AskNote.created_at.desc()).limit(200))
            .scalars()
            .all()
        )
        titles = {
            t.id: t.title
            for t in session.execute(
                select(AskThread).where(
                    AskThread.id.in_([n.source_thread_id for n in rows if n.source_thread_id])
                )
            )
            .scalars()
            .all()
        }
        count = session.execute(select(func.count(AskNote.id))).scalar_one()
    return {
        "notes": [_note_json(n, titles.get(n.source_thread_id or 0)) for n in rows],
        "count": count,
        "max": int(get_config().get("ask", "max_notes", default=100)),
    }


class NoteBody(BaseModel):
    text: str | None = Field(default=None, max_length=_NOTE_MAX)
    from_message: int | None = None


@router.post("/ask/notes")
def create_note(request: Request, body: NoteBody) -> dict[str, Any]:
    config = get_config()
    with get_session_maker()() as session:
        count = session.execute(select(func.count(AskNote.id))).scalar_one()
        if count >= config.get_int("ask", "max_notes", default=100):
            raise ApiError(
                422,
                ErrorCode.INVALID,
                fields=[
                    {
                        "name": "notes",
                        "message": (
                            f"At most {config.get('ask', 'max_notes', default=100)}"
                            " notes — delete one first."
                        ),
                    }
                ],
            )
        clock = _clock(request)
        text = body.text
        refs: list[str] = []
        thread_id = msg_id = None
        if body.from_message is not None:
            msg = session.get(AskMessage, body.from_message)
            if msg is None:
                raise HTTPException(404)
            thread_id, msg_id = msg.thread_id, msg.id
            refs = list(msg.references or [])
            if text is None:
                text = (msg.text or "").split("\n\n")[0][:_NOTE_MAX]
        if not text or not text.strip():
            raise ApiError(
                422, ErrorCode.INVALID, fields=[{"name": "text", "message": "A note needs text."}]
            )
        note = AskNote(
            text=text.strip()[:_NOTE_MAX],
            references=refs,
            source_thread_id=thread_id,
            source_message_id=msg_id,
            created_at=_naive_utc(clock.now()),
            updated_at=_naive_utc(clock.now()),
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        trow = session.get(AskThread, thread_id) if thread_id else None
        title = trow.title if trow else None
        return _note_json(note, title)


class NoteEdit(BaseModel):
    text: str = Field(min_length=1, max_length=_NOTE_MAX)


@router.put("/ask/notes/{note_id}")
def update_note(request: Request, note_id: int, body: NoteEdit) -> dict[str, Any]:
    with get_session_maker()() as session:
        note = session.get(AskNote, note_id)
        if note is None:
            raise HTTPException(404)
        note.text = body.text.strip()[:_NOTE_MAX]
        note.updated_at = _naive_utc(_clock(request).now())
        session.commit()
        trow = session.get(AskThread, note.source_thread_id) if note.source_thread_id else None
        title = trow.title if trow else None
        return _note_json(note, title)


@router.delete("/ask/notes/{note_id}")
def delete_note(request: Request, note_id: int) -> dict[str, Any]:
    with get_session_maker()() as session:
        note = session.get(AskNote, note_id)
        if note is None:
            raise HTTPException(404)
        session.delete(note)
        session.commit()
    return {"ok": True}


def cleanup_threads(session: Any, clock: Any, config: Any) -> int:
    """Delete threads older than ask.keep_days; notes keep their text."""
    days = config.get_int("ask", "keep_days", default=90)
    cutoff = _naive_utc(clock.now()) - __import__("datetime").timedelta(days=days)
    old = session.execute(select(AskThread.id).where(AskThread.updated_at < cutoff)).scalars().all()
    if not old:
        return 0
    session.execute(
        update(AskNote)
        .where(AskNote.source_thread_id.in_(old))
        .values(source_thread_id=None, source_message_id=None)
    )
    session.execute(delete(AskMessage).where(AskMessage.thread_id.in_(old)))
    session.execute(delete(AskThread).where(AskThread.id.in_(old)))
    session.commit()
    logger.info("ask: deleted %d thread(s) older than %d days", len(old), days)
    return len(old)
