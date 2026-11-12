"""FRED VIXCLS daily closes (docs/SPEC.md, VIX).

The keyless ``fredgraph.csv`` download works today; an optional API key
(Settings → API keys) is only the fallback for when FRED starts
requiring one — it is never needed to start or resume.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from app.data.models import DataResult, VixObservation
from app.errors import safe_error
from app.market.clock import Clock, RealClock


class FredVixSource:
    """VIX daily closes from FRED series ``VIXCLS``."""

    SERIES = "VIXCLS"
    CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    API_URL = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, api_key: str | None = None, clock: Clock | None = None) -> None:
        self._api_key = api_key
        self._clock = clock or RealClock()

    def update_key(self, api_key: str) -> None:
        """Swap the fallback key in place after a Settings save."""
        self._api_key = api_key

    @staticmethod
    def _parse(rows: list[tuple[str, str]]) -> list[VixObservation]:
        """(observation_date, value) pairs → observations; blanks skipped."""
        out: list[VixObservation] = []
        for raw_date, raw_value in rows:
            value = (raw_value or "").strip()
            if not value or value == ".":
                continue
            try:
                close = Decimal(value)
            except InvalidOperation:
                continue
            try:
                day = date.fromisoformat(raw_date.strip())
            except ValueError:
                continue
            out.append(VixObservation(date=day, close=close))
        return out

    async def _fetch_csv(self, start: date, end: date) -> DataResult[list[VixObservation]]:
        response = await asyncio.to_thread(
            httpx.get,
            self.CSV_URL,
            params={
                "id": self.SERIES,
                "cosd": start.isoformat(),
                "coed": end.isoformat(),
            },
            timeout=15.0,
        )
        response.raise_for_status()
        rows = [
            (row.get("observation_date") or "", row.get(self.SERIES) or "")
            for row in csv.DictReader(io.StringIO(response.text))
        ]
        return DataResult(
            data=sorted(self._parse(rows), key=lambda o: o.date),
            source="fred",
        )

    async def _fetch_api(self, start: date, end: date) -> DataResult[list[VixObservation]]:
        assert self._api_key is not None  # noqa: S101 - caller checked
        response = await asyncio.to_thread(
            httpx.get,
            self.API_URL,
            params={
                "series_id": self.SERIES,
                "api_key": self._api_key,
                "file_type": "json",
                "observation_start": start.isoformat(),
                "observation_end": end.isoformat(),
            },
            timeout=15.0,
        )
        response.raise_for_status()
        payload = json.loads(response.text)
        observations = payload.get("observations") or []
        rows = [
            (str(o.get("date") or ""), str(o.get("value") or ""))
            for o in observations
            if isinstance(o, dict)
        ]
        return DataResult(
            data=sorted(self._parse(rows), key=lambda o: o.date),
            source="fred",
        )

    async def fetch_vix_closes(
        self, start: date, end: date
    ) -> DataResult[list[VixObservation]]:
        """Closes in [start, end]; keyless CSV first, the keyed API only
        when the CSV fails and a key is stored."""
        try:
            return await self._fetch_csv(start, end)
        except Exception as exc:  # noqa: BLE001 - the keyed path is the fallback
            if not self._api_key:
                return DataResult(data=[], error=safe_error(exc), source="fred")
            try:
                return await self._fetch_api(start, end)
            except Exception as api_exc:  # noqa: BLE001
                return DataResult(
                    data=[],
                    error=safe_error(
                        f"csv: {exc}; api: {api_exc}", extra=[self._api_key]
                    ),
                    source="fred",
                )

    async def test_key(self) -> str | None:
        """The Settings key test: one VIXCLS observation through the API.

        Returns None on success, a scrubbed error string otherwise. The
        key travels in the query string only — never logged here.
        """
        if not self._api_key:
            return "no FRED key stored"
        try:
            end = self._clock.now().date()
            result = await self._fetch_api(end - timedelta(days=10), end)
        except Exception as exc:  # noqa: BLE001
            return safe_error(exc, extra=[self._api_key])
        if result.error is not None:
            return result.error
        if not result.data:
            return "no VIXCLS observation returned"
        return None


class FredReleaseSource:
    """FRED release dates (``fred/releases/dates``, item 067).

    The release calendar needs the API key — unlike the VIX closes there
    is no keyless path. A missing key or a failed fetch is a
    ``DataResult`` error; the caller treats it as a missing calendar,
    which never blocks a cycle.
    """

    DATES_URL = "https://api.stlouisfed.org/fred/releases/dates"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def update_key(self, api_key: str) -> None:
        """Swap the key in place after a Settings save."""
        self._api_key = api_key

    async def fetch_release_dates(
        self, release_id: int, start: date, end: date
    ) -> DataResult[list[date]]:
        if not self._api_key:
            return DataResult(
                data=[], error="no FRED key stored", source="fred"
            )
        try:
            response = await asyncio.to_thread(
                httpx.get,
                self.DATES_URL,
                params={
                    "release_id": release_id,
                    "api_key": self._api_key,
                    "file_type": "json",
                    "realtime_start": start.isoformat(),
                    "realtime_end": end.isoformat(),
                    "sort_order": "asc",
                    "limit": 1000,
                },
                timeout=15.0,
            )
            response.raise_for_status()
            payload = json.loads(response.text)
        except Exception as exc:  # noqa: BLE001 - reported as a missing calendar
            return DataResult(
                data=[],
                error=safe_error(exc, extra=[self._api_key]),
                source="fred",
            )
        days: list[date] = []
        for row in payload.get("release_dates") or []:
            if not isinstance(row, dict):
                continue
            raw = str(row.get("date") or "")
            try:
                day = date.fromisoformat(raw)
            except ValueError:
                continue
            if start <= day <= end:
                days.append(day)
        return DataResult(data=sorted(set(days)), source="fred")
