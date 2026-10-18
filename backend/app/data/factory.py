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
from app.secrets_service import MISSING_KEY, SecretsStore


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


def make_data_sources(
    config: AppConfig, secrets: SecretsStore | None = None
) -> DataSources:
    """Wire data sources for the configured mode.

    Simulated mode uses fakes with no network. Paper mode builds the Alpaca
    clients; the trading client is always created with ``paper=True`` and
    the keys come from the encrypted secrets table.
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

    key_id, secret = secrets.alpaca_pair() if secrets is not None else (None, None)
    finnhub_key = secrets.get("finnhub") if secrets is not None else None

    max_calls = int(config.get("alpaca", "max_calls_per_minute", default=150))
    adjustment = str(config.get("schedule", "bar_adjustment", default="split"))
    limiter = RateLimiter(RateLimitConfig(max_calls=max_calls))
    # Missing keys are no reason to refuse startup: the app comes up
    # paused, every Alpaca call fails as an ordinary error and the banner
    # points at Settings (docs/SPEC.md, API keys).
    alpaca = AlpacaDataClient(
        key_id or MISSING_KEY,
        secret or MISSING_KEY,
        rate_limiter=limiter,
        adjustment=adjustment,
    )
    return DataSources(
        bars=alpaca,
        quotes=alpaca,
        news=alpaca,
        earnings=FinnhubEarningsSource(finnhub_key or MISSING_KEY),
        fx=EcbFXSource(),
        corporate_actions=alpaca,
        calendar=alpaca,
        alpaca=alpaca,
    )
