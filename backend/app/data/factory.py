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
    FakeReleaseSource,
    FakeVixSource,
)
from app.data.finnhub import FinnhubEarningsSource
from app.data.fred import FredReleaseSource, FredVixSource
from app.data.protocols import (
    BarSource,
    CalendarSource,
    CorporateActionSource,
    EarningsSource,
    FXSource,
    NewsSource,
    QuoteSource,
    ReleaseSource,
    VixSource,
)
from app.data.rate_limiter import RateLimitConfig, RateLimiter
from app.market.clock import RealClock
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
    # VIX closes (item 060): None only where a caller builds sources by
    # hand without one — VixService then reports the fields as missing.
    vix: VixSource | None = None
    # Release dates (item 067): None where a caller builds sources by hand
    # — the release calendar then reports the FRED releases as missing.
    releases: ReleaseSource | None = None


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
            # Quotes stamped "now" so the fresh-quote gate sees them as
            # fresh in simulated mode too.
            quotes=FakeQuoteSource(RealClock()),
            news=FakeNewsSource(),
            earnings=FakeEarningsSource(),
            fx=FakeFXSource(),
            corporate_actions=FakeCorporateActionSource(),
            calendar=calendar,
            alpaca=None,
            vix=FakeVixSource(calendar),
            releases=FakeReleaseSource(),
        )

    key_id, secret = secrets.alpaca_pair() if secrets is not None else (None, None)
    finnhub_key = secrets.get("finnhub") if secrets is not None else None
    # The FRED key is optional — only the fallback for a keyless CSV that
    # stops working (docs/SPEC.md, VIX).
    fred_key = secrets.get("fred") if secrets is not None else None

    max_calls = config.get_int("alpaca", "max_calls_per_minute", default=150)
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
        vix=FredVixSource(api_key=fred_key),
        releases=FredReleaseSource(api_key=fred_key),
    )
