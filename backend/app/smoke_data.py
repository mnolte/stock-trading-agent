"""Read-only smoke test against the real paper account.

Run from the repo root: ``MODE=paper make smoke-data``. It fills the 5m bar
cache for every configured symbol and does one read per external source,
printing a single summary line each. It never places an order: the only
Alpaca calls are account, calendar and market-data reads.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

from app.api.main import calendar_load_range, run_migrations
from app.config import AppConfig, get_config
from app.data.factory import make_data_sources
from app.data.service import MarketDataService
from app.market.calendar import CalendarStore
from app.market.clock import NY_TZ, Clock, RealClock


def _line(source: str, summary: str, error: str | None = None) -> int:
    if error is None:
        print(f"{source}: {summary}")
        return 0
    print(f"{source}: UNAVAILABLE ({error})")
    return 1


async def run(config: AppConfig, clock: Clock) -> int:
    Path(config.env.sa_db_path).parent.mkdir(parents=True, exist_ok=True)
    run_migrations()
    sources = make_data_sources(config)
    if sources.alpaca is None:
        _line("account", "", "simulated mode has no Alpaca client")
        return 2

    failures = 0
    paper = await sources.alpaca.check_paper_account()
    failures += _line(
        "account",
        f"paper account {paper.data.get('account_number', '?')} verified",
        paper.error,
    )
    if paper.error is not None:
        return failures

    today = clock.now().astimezone(NY_TZ).date()
    store = CalendarStore(sources.calendar)
    start, end = calendar_load_range(config, today)
    calendar = await store.load(start, end)
    failures += _line(
        "calendar",
        f"{len(calendar.data)} trading days loaded ({start}..{end})",
        calendar.error,
    )

    market_data = MarketDataService(sources, store, config)
    universe = market_data.universe()

    bars = await market_data.refresh_history(today)
    inserted = sum(result.data for result in bars.values())
    cached = sum(market_data.bars_cache.count(symbol) for symbol in bars)
    bar_errors = [f"{s}: {r.error}" for s, r in bars.items() if r.error]
    failures += _line(
        "bars_5m",
        f"{cached} bars in cache across {len(bars)} symbols ({inserted} new)",
        "; ".join(bar_errors) or None,
    )

    quote = await sources.quotes.fetch_quotes(["AAPL"])
    if quote.error is None and quote.data:
        q = quote.data["AAPL"]
        failures += _line(
            "quote", f"AAPL bid {q.bid} ask {q.ask} last {q.last} ({q.feed})"
        )
    else:
        failures += _line("quote", "", quote.error or "no quote returned")

    day_start = datetime.combine(today, time.min, tzinfo=NY_TZ)
    news = await sources.news.fetch_news(universe, day_start, clock.now())
    failures += _line("news", f"{len(news.data)} items today", news.error)

    earnings = await sources.earnings.fetch_earnings(
        universe, today, today + timedelta(days=90)
    )
    failures += _line(
        "earnings", f"{len(earnings.data)} events in the next 90 days", earnings.error
    )

    fx = await sources.fx.fetch_fx(today)
    if fx.error is None and fx.data is not None:
        failures += _line(
            "fx", f"EUR/USD {fx.data.usd_per_eur} (ECB {fx.data.for_date})"
        )
    else:
        failures += _line("fx", "", fx.error or "no rate returned")

    return failures


def main() -> int:
    config = get_config()
    if config.env.mode != "paper":
        print("smoke-data needs the real paper account; run with MODE=paper")
        return 2
    failures = asyncio.run(run(config, RealClock()))
    if failures:
        print(f"smoke-data: {failures} source(s) unavailable")
        return 1
    print("smoke-data: all sources OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
