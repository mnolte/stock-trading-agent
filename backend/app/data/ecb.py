from __future__ import annotations

import asyncio
import csv
import io
from datetime import date
from decimal import Decimal

import httpx

from app.data.models import DataResult, FXRate
from app.market.clock import Clock, RealClock


class EcbFXSource:
    """ECB daily EUR/USD reference rate (EXR.D.USD.EUR.SP00.A, SDMX-CSV)."""

    URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or RealClock()

    async def fetch_fx(self, for_date: date) -> DataResult[FXRate | None]:
        params = {
            "startPeriod": for_date.isoformat(),
            "endPeriod": for_date.isoformat(),
            "format": "csvdata",
        }
        try:
            response = await asyncio.to_thread(
                httpx.get, self.URL, params=params, timeout=15.0
            )
            response.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(response.text)))
            if not rows:
                return DataResult(data=None, error="no ECB observation for date", source="ecb")
            latest = rows[-1]
            value = latest.get("OBS_VALUE") or latest.get("obs.value")
            if value is None:
                return DataResult(
                    data=None, error="OBS_VALUE missing in ECB response", source="ecb"
                )
            return DataResult(
                data=FXRate(
                    base="EUR",
                    quote="USD",
                    usd_per_eur=Decimal(str(value)),
                    fetched_at=self._clock.now(),
                ),
                source="ecb",
            )
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=None, error=str(exc), source="ecb")
