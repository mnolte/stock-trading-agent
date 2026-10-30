"""The shared ``llm_cache`` store (docs/SPEC.md, Backtesting, Model calls).

Every model answer — live decisions, shadow calls, backtest calls — is
stored once, keyed by model, temperature, prompt hash and the input
snapshot's hash, with the cycle's scheduled time. Only the backtest
*reads* the cache: a rerun over the same inputs replays the stored answer
and costs nothing.

The store lives in the live database and is shared by every caller; each
write commits immediately (hard rule 13 — no lock held across I/O).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.data.cache import _naive_utc
from app.db.models import LLMCache
from app.market.clock import Clock


def input_hash(user: str) -> str:
    """SHA-256 of the exact user message (the serialised input snapshot)."""
    return hashlib.sha256(user.encode()).hexdigest()


class LLMCacheStore:
    """Read/write access to the shared ``llm_cache`` table."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Clock,
    ) -> None:
        self._sessions = session_factory
        self._clock = clock

    def lookup(
        self,
        *,
        model: str,
        temperature: float | None = None,
        prompt_hash: str,
        user: str,
        provider: str = "openai",
        effort: str | None = None,
    ) -> dict[str, Any] | None:
        """The stored answer for this exact call, or None."""
        with self._sessions() as session:
            row = session.execute(
                select(LLMCache).where(
                    LLMCache.provider == provider,
                    LLMCache.model == model,
                    LLMCache.effort.is_(None) if effort is None
                    else LLMCache.effort == effort,
                    LLMCache.temperature == (temperature or 0),
                    LLMCache.prompt_hash == prompt_hash,
                    LLMCache.input_hash == input_hash(user),
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "response": row.response,
                "tokens_in": row.tokens_in,
                "tokens_out": row.tokens_out,
            }

    def store(
        self,
        *,
        model: str,
        temperature: float | None = None,
        prompt_version: str,
        prompt_hash: str,
        user: str,
        cycle_time: datetime | None,
        response: str,
        tokens_in: int | None,
        tokens_out: int | None,
        provider: str = "openai",
        effort: str | None = None,
    ) -> None:
        """Persist one answer; a duplicate key (a racing rerun) is ignored."""
        with self._sessions() as session:
            session.add(
                LLMCache(
                    provider=provider,
                    model=model,
                    effort=effort,
                    temperature=temperature or 0,
                    prompt_version=prompt_version,
                    prompt_hash=prompt_hash,
                    input_hash=input_hash(user),
                    cycle_time=_naive_utc(cycle_time) if cycle_time else None,
                    response=response,
                    tokens=(tokens_in or 0) + (tokens_out or 0),
                    tokens_in=tokens_in or 0,
                    tokens_out=tokens_out or 0,
                    created_at=_naive_utc(self._clock.now()),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()


def cache_hit_count(
    session_factory: sessionmaker[Session],
    *,
    model: str,
    temperature: float,
    prompt_hash: str,
    start: datetime,
    end: datetime,
    provider: str = "openai",
    effort: str | None = None,
) -> int:
    """Estimate input: cached rows whose cycle time falls in the period."""
    with session_factory() as session:
        from sqlalchemy import func

        return session.execute(
            select(func.count(LLMCache.id)).where(
                LLMCache.provider == provider,
                LLMCache.model == model,
                LLMCache.effort.is_(None) if effort is None
                else LLMCache.effort == effort,
                LLMCache.temperature == temperature,
                LLMCache.prompt_hash == prompt_hash,
                LLMCache.cycle_time >= _naive_utc(start),
                LLMCache.cycle_time <= _naive_utc(end),
            )
        ).scalar_one()
