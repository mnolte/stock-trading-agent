from __future__ import annotations

from dataclasses import dataclass

from app.config import AppConfig
from app.data.alpaca import AlpacaDataClient
from app.data.ecb import EcbFXSource
from app.data.fake import (
    FakeBarSource,
    FakeCalendarSource,
    FakeCorporateActionSource,
    FakeEarningsSource,
    FakeFXSource,
    FakeNewsSource,
    FakeQuoteSource,
)
from app.data.finnhub import FinnhubEarningsSource
from app.data.protocols import (
    BarSource,
    CalendarSource,
    CorporateActionSource,
    EarningsSource,
    FXSource,
    NewsSource,
    QuoteSource,
)
from app.data.rate_limiter import RateLimitConfig, RateLimiter


@dataclass
class DataSources:
    """All external data sources, each behind an interface."""

    bars: BarSource
    quotes: QuoteSource
    news: NewsSource
    earnings: EarningsSource
    fx: FXSource
    corporate_actions: CorporateActionSource
    calendar: CalendarSource
    alpaca: AlpacaDataClient | None


def make_data_sources(config: AppConfig) -> DataSources:
    """Wire data sources for the configured mode.

    Simulated mode uses fakes with no network. Paper mode builds the Alpaca
    clients; the trading client is always created with ``paper=True``.
    """
    if config.env.mode == "simulated":
        calendar = FakeCalendarSource()
        return DataSources(
            bars=FakeBarSource(calendar),
            quotes=FakeQuoteSource(),
            news=FakeNewsSource(),
            earnings=FakeEarningsSource(),
            fx=FakeFXSource(),
            corporate_actions=FakeCorporateActionSource(),
            calendar=calendar,
            alpaca=None,
        )

    api_key = config.env.alpaca_api_key
    secret_key = config.env.alpaca_secret_key
    if not api_key or not secret_key:
        raise RuntimeError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY are required unless MODE=simulated"
        )

    max_calls = int(config.get("alpaca", "max_calls_per_minute", default=150))
    adjustment = str(config.get("schedule", "bar_adjustment", default="split"))
    limiter = RateLimiter(RateLimitConfig(max_calls=max_calls))
    alpaca = AlpacaDataClient(
        api_key, secret_key, rate_limiter=limiter, adjustment=adjustment
    )
    return DataSources(
        bars=alpaca,
        quotes=alpaca,
        news=alpaca,
        earnings=FinnhubEarningsSource(config.env.finnhub_api_key),
        fx=EcbFXSource(),
        corporate_actions=alpaca,
        calendar=alpaca,
        alpaca=alpaca,
    )
