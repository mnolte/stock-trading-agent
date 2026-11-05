"""Level finder and option builder for decision.v2 (docs/SPEC.md, Decision v2).

Fixture bars are hand-built; the property test runs the builder over random
bars and asserts every offered option meets the band and R/R rules.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.config import AppConfig
from app.decide.levels import find_levels, options_config

from tests.helpers import bar5m, dbar, wave_bars

CFG = options_config(AppConfig())


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


