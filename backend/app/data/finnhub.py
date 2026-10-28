from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

import finnhub

from app.data.models import DataResult, EarningsEvent, normalise_earnings_hour
from app.logging_config import ev
from app.market.clock import Clock, RealClock

log = logging.getLogger("data.earnings")

# Finnhub answers /calendar/earnings with at most 1500 rows and silently
# keeps only the tail of a wider window — a real report early in the
# range goes missing, the stored next-earnings jumps to the following
# quarter and the blackout never fires (2026-10-28: a 120-day fetch
# dropped every report before mid-November, SBUX 10-29 included).
PAGE_LIMIT = 1500
# A chunk this size stays under the cap outside peak weeks; a chunk that
# still comes back at the cap is split in half and fetched again.
CHUNK_DAYS = 7


class FinnhubEarningsSource:
    """Finnhub earnings calendar (/calendar/earnings)."""

    def __init__(self, api_key: str, clock: Clock | None = None) -> None:
        self._client = finnhub.Client(api_key=api_key)
        self._clock = clock or RealClock()

    def update_key(self, api_key: str) -> None:
        """Swap the key in place after a Settings save."""
        self._client = finnhub.Client(api_key=api_key)

    async def fetch_earnings(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> DataResult[list[EarningsEvent]]:
        events: list[EarningsEvent] = []
        fetched_at = self._clock.now()
        if not symbols:
            return DataResult(data=events, source="finnhub")
        wanted = set(symbols)
        try:
            # One call per chunk for the whole calendar: omitting the
            # symbol returns every report in the range, so a screened
            # universe of ~70 symbols costs a handful of chunked requests,
            # not one per symbol — per-symbol bursts overrun the free-tier
            # rate limit and come back 429.
            chunk = start
            while chunk <= end:
                chunk_end = min(chunk + timedelta(days=CHUNK_DAYS - 1), end)
                events.extend(
                    await self._fetch_chunk(wanted, chunk, chunk_end, fetched_at)
                )
                chunk = chunk_end + timedelta(days=1)
            return DataResult(data=events, source="finnhub")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=[], error=str(exc), source="finnhub")

    async def _fetch_chunk(
        self,
        wanted: set[str],
        start: date,
        end: date,
        fetched_at: datetime,
    ) -> list[EarningsEvent]:
        resp: dict[str, Any] = await asyncio.to_thread(
            self._client.earnings_calendar,
            start.isoformat(),
            end.isoformat(),
            None,
        )
        rows = resp.get("earningsCalendar", [])
        if len(rows) >= PAGE_LIMIT:
            if end > start:
                mid = start + (end - start) / 2
                first = await self._fetch_chunk(wanted, start, mid, fetched_at)
                second = await self._fetch_chunk(
                    wanted, mid + timedelta(days=1), end, fetched_at
                )
                return first + second
            # A single day over the cap cannot be split further — keep the
            # tail Finnhub sent and say so instead of failing the refresh.
            ev(
                log, logging.WARNING, "data.earnings_capped",
                f"earnings calendar for {start} returned the {PAGE_LIMIT}-row "
                "cap; the response may be incomplete",
                start=start.isoformat(), end=end.isoformat(),
            )
        events: list[EarningsEvent] = []
        for row in rows:
            symbol = str(row.get("symbol") or "")
            if symbol not in wanted:
                continue
            day = row.get("date")
            if isinstance(day, str):
                day = date.fromisoformat(day)
            hour_raw = str(row.get("hour") or "")
            events.append(
                EarningsEvent(
                    symbol=symbol,
                    date=day,
                    time_of_day=normalise_earnings_hour(hour_raw),
                    hour_raw=hour_raw,
                    fetched_at=fetched_at,
                )
            )
        return events
