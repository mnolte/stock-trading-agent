from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import finnhub

from app.data.models import DataResult, EarningsEvent, normalise_earnings_hour
from app.market.clock import Clock, RealClock


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
            # One call for the whole calendar: omitting the symbol returns
            # every report in the range, so a screened universe of ~70
            # symbols costs one request, not one per symbol — per-symbol
            # bursts overrun the free-tier rate limit and come back 429.
            resp: dict[str, Any] = await asyncio.to_thread(
                self._client.earnings_calendar,
                start.isoformat(),
                end.isoformat(),
                None,
            )
            for row in resp.get("earningsCalendar", []):
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
            return DataResult(data=events, source="finnhub")
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=[], error=str(exc), source="finnhub")
