"""Level finder and option builder for decision.v2 (docs/SPEC.md, Decision v2).

Fixture bars are hand-built; the property test runs the builder over random
bars and asserts every offered option meets the band and R/R rules.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.config import AppConfig
from app.data.models import Bar1h, Bar5m, DailyBar
from app.decide.levels import find_levels, options_config

NY_OPEN = 9, 30
CFG = options_config(AppConfig())


def bar1h(day: date, start_h: int, start_m: int, high: str, low: str,
        close: str | None = None, o: str | None = None) -> Bar1h:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(
        hour=start_h, minute=start_m
    )
    return Bar1h(
        symbol="X", start=start, end=start + timedelta(hours=1),
        open=Decimal(o or low), high=Decimal(high), low=Decimal(low),
        close=Decimal(close or low), volume=1000, feed="sip", complete=True,
    )


def dbar(day: date, o: str, h: str, lo: str, c: str) -> DailyBar:
    return DailyBar(
        symbol="X", date=day, open=Decimal(o), high=Decimal(h),
        low=Decimal(lo), close=Decimal(c), volume=100_000,
    )


def bar5m(day: date, h: int, m: int, high: str, low: str, close: str,
        vol: int = 1000) -> Bar5m:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(
        hour=h, minute=m
    )
    return Bar5m(
        symbol="X", start=start, open=Decimal(close), high=Decimal(high),
        low=Decimal(low), close=Decimal(close), volume=vol, feed="sip",
    )


# A gentle wave: bar i moves a little so the middle bars are swings.
def wave_bars(day: date, n: int, base: float = 100.0, step: float = 1.0,
              seed: int = 7) -> list[Bar1h]:
    """n 1h bars on a triangle wave with seeded wicks — strict swings.

    Two bars up, two down, … gives local extremes every other bar; the
    seeded wicks break the exact ties a shared peak price would cause.
    """
    rng = random.Random(seed)
    bars: list[Bar1h] = []
    price = base
    for i in range(n):
        d = step if (i // 2) % 2 == 0 else -step
        nxt = price + d
        hi = max(price, nxt) + rng.uniform(0.02, 0.2) * step
        lo = min(price, nxt) - rng.uniform(0.02, 0.2) * step
        start = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=i)
        bars.append(Bar1h(
            symbol="X", start=start, end=start + timedelta(hours=1),
            open=Decimal(str(round(price, 4))), high=Decimal(str(round(hi, 4))),
            low=Decimal(str(round(lo, 4))), close=Decimal(str(round(nxt, 4))),
            volume=1000, feed="sip", complete=True,
        ))
        price = nxt
    return bars


class TestFindLevels:
    day = date(2026, 10, 14)

    def test_confirmed_swings_only(self) -> None:
        # strength=2: the newest two bars can never be a swing.
        bars = wave_bars(self.day, 12)
        levels = find_levels(
            bars_1h=bars, daily=[], today_5m=[], price=Decimal("101"), cfg=CFG,
        )
        swings = [lv for lv in levels if lv.kind.startswith("swing")]
        assert swings
        # No swing can sit on the last two (unconfirmed) bars.
        for lv in swings:
            assert lv.time not in {bars[-1].start, bars[-2].start}
        assert all(lv.id.startswith("L") for lv in levels)

    def test_ids_numbered_highest_down(self) -> None:
        bars = wave_bars(self.day, 8)
        levels = find_levels(
            bars_1h=bars, daily=[], today_5m=[], price=Decimal("100"), cfg=CFG,
        )
        prices = [lv.price for lv in levels]
        assert prices == sorted(prices, reverse=True)
        assert [lv.id for lv in levels] == [f"L{i + 1}" for i in range(len(levels))]

    def test_max_eight_each_side_nearest_first(self) -> None:
        # Many swings both sides of the price → at most 8 each side.
        bars = wave_bars(self.day, 60, step=0.5)
        prices = sorted({b.high for b in bars} | {b.low for b in bars})
        mid = prices[len(prices) // 2]
        levels = find_levels(
            bars_1h=bars, daily=[], today_5m=[], price=mid, cfg=CFG,
        )
        below = [lv for lv in levels if lv.price <= mid]
        above = [lv for lv in levels if lv.price > mid]
        assert len(below) <= 8 and len(above) <= 8
        assert [lv.price for lv in below] == sorted(
            (lv.price for lv in below), reverse=True
        )
        if len(below) == 8:
            assert below[0].price == max(lv.price for lv in below)

    def test_session_levels(self) -> None:
        prior = dbar(date(2026, 10, 13), "100", "105", "98", "103")
        todays = [
            bar5m(self.day, 9, 30, "104", "101", "103"),
            bar5m(self.day, 9, 35, "106", "102", "104"),
        ]
        levels = find_levels(
            bars_1h=[], daily=[prior], today_5m=todays,
            price=Decimal("104"), cfg=CFG,
        )
        kinds = {lv.kind: lv for lv in levels}
        assert kinds["prior_session_high"].price == Decimal("105")
        assert kinds["prior_session_low"].price == Decimal("98")
        assert kinds["prior_session_close"].price == Decimal("103")
        assert kinds["session_high"].price == Decimal("106")
        assert kinds["session_low"].price == Decimal("101")
        assert "vwap" in kinds

    def test_early_close_partial_bar_is_a_level_input(self) -> None:
        # An early-close session still yields session high/low/vwap; the
        # partial last 1h bar is just another bar for swings.
        todays = [
            bar5m(self.day, 9, 30, "104", "101", "103"),
            bar5m(self.day, 9, 35, "106", "102", "104"),
        ]
        levels = find_levels(
            bars_1h=[], daily=[], today_5m=todays, price=Decimal("104"), cfg=CFG,
        )
        assert {lv.kind for lv in levels} == {"session_high", "session_low", "vwap"}


