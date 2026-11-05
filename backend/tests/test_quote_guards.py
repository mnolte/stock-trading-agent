"""Quote-quality guards: crossed books and future trade stamps (audit fixes)."""

from datetime import datetime, timedelta
from decimal import Decimal

from app.data.models import Quote
from app.market.clock import UTC

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


def _quote(bid: str, ask: str, last: str, trade_ts: datetime | None = None) -> Quote:
    return Quote(
        symbol="MSFT",
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=Decimal(last),
        timestamp=NOW,
        feed="iex",
        trade_timestamp=trade_ts or NOW,
        fetched_at=NOW,
    )


def test_crossed_book_is_not_plausible() -> None:
    # bid > ask is a data error, not a tight spread.
    quote = _quote("100.10", "99.90", "100.00")
    assert quote.quote_plausible(Decimal("1")) is False


def test_locked_book_is_plausible() -> None:
    quote = _quote("100.00", "100.00", "100.00")
    assert quote.quote_plausible(Decimal("1")) is True


def test_future_trade_timestamp_is_suspect() -> None:
    quote = _quote(
        "100.00", "100.05", "100.02",
        trade_ts=NOW + timedelta(hours=1),
    )
    reason = quote.price_reason(
        now=NOW,
        max_age_seconds=900,
        bar_close=Decimal("100.00"),
        max_deviation_pct=Decimal("1"),
    )
    assert reason == "price_suspect"


def test_old_trade_timestamp_is_stale() -> None:
    quote = _quote(
        "100.00", "100.05", "100.02",
        trade_ts=NOW - timedelta(minutes=16),
    )
    reason = quote.price_reason(
        now=NOW,
        max_age_seconds=900,
        bar_close=Decimal("100.00"),
        max_deviation_pct=Decimal("1"),
    )
    assert reason == "price_stale"


def test_deviation_boundary_inclusive_plausible_exclusive_suspect() -> None:
    """Exactly max_deviation_pct from the bar close is not suspect (the
    check is strict >); just over it is. The plausible band is <=."""
    at_edge = _quote("99.00", "99.20", "99.00", trade_ts=NOW)
    assert (
        at_edge.price_reason(
            now=NOW, max_age_seconds=900,
            bar_close=Decimal("100.00"), max_deviation_pct=Decimal("1"),
        )
        is None
    )
    over = _quote("98.99", "99.20", "98.99", trade_ts=NOW)
    assert (
        over.price_reason(
            now=NOW, max_age_seconds=900,
            bar_close=Decimal("100.00"), max_deviation_pct=Decimal("1"),
        )
        == "price_suspect"
    )
    # Bid and ask exactly 1% from last: still plausible; 1% over: not.
    assert _quote("99.00", "101.00", "100.00").quote_plausible(Decimal("1"))
    assert not _quote("98.99", "101.00", "100.00").quote_plausible(Decimal("1"))
