from __future__ import annotations

import asyncio
import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import httpx

from app.data.models import DataResult, FXRate
from app.market.clock import Clock, RealClock


class EcbFXSource:
    """ECB daily EUR/USD reference rate (EXR.D.USD.EUR.SP00.A, SDMX-CSV)."""

    URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"

    # Today's rate is published around 16:15 CET, after our daily refresh; a
    # weekend or holiday has no observation at all. Walk back this many days
    # to the most recent published rate.
    LOOKBACK_DAYS = 5

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or RealClock()

    async def _fetch_day(self, for_date: date) -> Decimal | None:
        params = {
            "startPeriod": for_date.isoformat(),
            "endPeriod": for_date.isoformat(),
            "format": "csvdata",
        }
        response = await asyncio.to_thread(
            httpx.get, self.URL, params=params, timeout=15.0
        )
        response.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(response.text)))
        if not rows:
            return None
        value = rows[-1].get("OBS_VALUE") or rows[-1].get("obs.value")
        return Decimal(str(value)) if value is not None else None

    async def fetch_fx(self, for_date: date) -> DataResult[FXRate | None]:
        try:
            day = for_date
            for _ in range(self.LOOKBACK_DAYS + 1):
                rate = await self._fetch_day(day)
                if rate is not None:
                    return DataResult(
                        data=FXRate(
                            base="EUR",
                            quote="USD",
                            usd_per_eur=rate,
                            for_date=day,
                            fetched_at=self._clock.now(),
                        ),
                        source="ecb",
                    )
                day -= timedelta(days=1)
            return DataResult(
                data=None,
                error=f"no ECB observation in the last {self.LOOKBACK_DAYS + 1} days",
                source="ecb",
            )
        except Exception as exc:  # noqa: BLE001
            return DataResult(data=None, error=str(exc), source="ecb")
