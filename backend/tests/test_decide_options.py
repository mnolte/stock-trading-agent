"""Level finder and option builder for decision.v2 (docs/SPEC.md, Decision v2).

Fixture bars are hand-built; the property test runs the builder over random
bars and asserts every offered option meets the band and R/R rules.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.config import AppConfig
from app.data.models import Bar1h, Bar5m, DailyBar
from app.decide.levels import OPTIONS_VERSION, Level, find_levels, options_config
from app.decide.options import build_entry_options, build_stop_options

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


class TestBuildEntryOptions:
    day = date(2026, 10, 15)

    def _levels(self) -> list[Level]:
        return find_levels(
            bars_1h=wave_bars(self.day, 10), daily=[], today_5m=[],
            price=Decimal("100"), cfg=CFG,
        )

    def test_level_and_atr_stops_and_targets(self) -> None:
        # Price 100, ATR 2: band = 3–8 below reference.
        levels = find_levels(
            bars_1h=[], daily=[], today_5m=[], price=Decimal("100"), cfg=CFG,
        ) + [
            # Inject explicit levels: a swing low at 94 (stop 93.8 → 6.2 = 3.1 ATR)
            # and a swing high at 110.
            _level("L9", "94", "swing_low", "1h"),
            _level("L1", "110", "swing_high", "daily"),
        ]
        levels.sort(key=lambda lv: lv.price, reverse=True)
        levels = [
            _level(f"L{i + 1}", str(lv.price), lv.kind, lv.timeframe)
            for i, lv in enumerate(levels)
        ]
        result = build_entry_options(
            levels=levels, price=Decimal("100"), reference=Decimal("100"),
            limit=Decimal("100.05"), atr_1h=Decimal("2"), cfg=CFG,
        )
        assert result.options
        letters = [o.id for o in result.options]
        assert letters == [chr(65 + i) for i in range(len(letters))]
        for o in result.options:
            assert Decimal("1.5") <= (Decimal("100") - o.stop) / 2 <= Decimal("4")
            assert o.rr >= Decimal("1.5")
            assert o.risk == Decimal("100.05") - o.stop
        bases = [o.stop_basis for o in result.options]
        assert any("× ATR" in b for b in bases)  # the 2 × ATR stop
        assert any("swing low" in b for b in bases)

    def test_no_options_when_no_stop_in_band(self) -> None:
        from dataclasses import replace

        cfg = replace(CFG, atr_stop_multiples=())
        levels = [_level("L1", "99.9", "swing_low", "1h")]  # stop 99.7, too close
        result = build_entry_options(
            levels=levels, price=Decimal("100"), reference=Decimal("100"),
            limit=Decimal("100.05"), atr_1h=Decimal("0.1"), cfg=cfg,
        )
        assert result.options == []
        assert result.reason

    def test_merged_stops_keep_level(self) -> None:
        levels = [_level("L1", "95.9", "swing_low", "1h")]
        # level stop = 95.9 - 0.2 = 95.7 → 4.3 away = 2.15 ATR; the 2 x ATR
        # stop at 96.0 is within 0.5 ATR of 95.7 → merged, level wins.
        result = build_entry_options(
            levels=levels, price=Decimal("100"), reference=Decimal("100"),
            limit=Decimal("100.05"), atr_1h=Decimal("2"), cfg=CFG,
        )
        stops = {o.stop for o in result.options}
        assert result.options and all(o.stop == Decimal("95.7") for o in result.options)
        assert all("swing low" in o.stop_basis for o in result.options)
        assert len(stops) == 1

    def test_level_on_the_band_edge(self) -> None:
        # stop at exactly 4 × ATR below reference is kept.
        levels = [_level("L1", "92.2", "swing_low", "1h")]  # stop 92.0, distance 8 = 4 ATR
        result = build_entry_options(
            levels=levels, price=Decimal("100"), reference=Decimal("100"),
            limit=Decimal("100.05"), atr_1h=Decimal("2"), cfg=CFG,
        )
        assert any(o.stop == Decimal("92.0") for o in result.options)

    def test_levels_in_way(self) -> None:
        levels = [
            _level("L1", "120", "swing_high", "daily"),
            _level("L2", "105", "session_high", "session"),
            _level("L3", "94", "swing_low", "1h"),   # stop 93.8 → 3.1 ATR
        ]
        result = build_entry_options(
            levels=levels, price=Decimal("100"), reference=Decimal("100"),
            limit=Decimal("100.05"), atr_1h=Decimal("2"), cfg=CFG,
        )
        # Level target 110-0.2=... swing_high L1 target = 120-0.2=119.8 → rr≈9.6;
        # L2 target = 104.8 → rr < 1.5 is dropped. 2R target = 100.05+2*6.25.
        opt = next(o for o in result.options if o.target_level == "L1")
        assert opt.levels_in_way == ["L2"]


def _level(lid: str, price: str, kind: str, tf: str) -> Level:

    return Level(
        id=lid, price=Decimal(price), kind=kind, timeframe=tf,
        time=datetime(2026, 10, 13, 14, 30, tzinfo=UTC),
    )


class TestBuildStopOptions:
    day = date(2026, 10, 15)

    def test_swing_breakeven_trail(self) -> None:
        bars = wave_bars(self.day, 10, base=100.0)
        options = build_stop_options(
            bars_1h=bars,
            entry=Decimal("100"),
            opened_at=bars[0].start,
            current_stop=Decimal("90"),
            initial_stop=Decimal("95"),
            reference=Decimal("102"),
            atr_1h=Decimal("1"),
            cfg=CFG,
        )
        assert options
        ids = [o.id for o in options]
        assert ids == [f"T{i + 1}" for i in range(len(ids))]
        for o in options:
            assert o.stop > Decimal("90")
            assert Decimal("102") - o.stop >= Decimal("0.25")
        assert any(o.locks_profit for o in options)
        be = next(o for o in options if o.basis == "break-even")
        assert be.locks_profit and be.r_at_stop == Decimal("0")

    def test_nothing_above_current_stop(self) -> None:
        bars = wave_bars(self.day, 10, base=100.0)
        options = build_stop_options(
            bars_1h=bars,
            entry=Decimal("100"),
            opened_at=bars[0].start,
            current_stop=Decimal("101.9"),   # above every candidate
            initial_stop=Decimal("95"),
            reference=Decimal("102"),
            atr_1h=Decimal("1"),
            cfg=CFG,
        )
        assert options == []

    def test_naive_opened_at(self) -> None:
        """Position.opened_at from SQLite is naive UTC — comparing it with
        aware bar ends must not raise (live cycle regression)."""
        bars = wave_bars(self.day, 10, base=100.0)
        options = build_stop_options(
            bars_1h=bars,
            entry=Decimal("100"),
            opened_at=bars[0].start.replace(tzinfo=None),
            current_stop=Decimal("90"),
            initial_stop=Decimal("95"),
            reference=Decimal("102"),
            atr_1h=Decimal("1"),
            cfg=CFG,
        )
        assert any("trail" in o.basis for o in options)

    def test_locks_profit_false_below_entry(self) -> None:
        bars = wave_bars(self.day, 10, base=100.0)
        options = build_stop_options(
            bars_1h=bars,
            entry=Decimal("100"),
            opened_at=bars[0].start,
            current_stop=Decimal("95"),
            initial_stop=Decimal("95"),
            reference=Decimal("98"),       # market dipped: trail/be above ref get cut
            atr_1h=Decimal("1"),
            cfg=CFG,
        )
        for o in options:
            assert o.r_at_stop is not None and o.r_at_stop < 0
            assert not o.locks_profit


class TestProperty:
    """Over random bars every offered option meets the band and R/R rules."""

    def test_random_bars(self) -> None:
        rng = random.Random(20261015)
        day = date(2026, 10, 15)
        for _ in range(200):
            price = Decimal(str(round(rng.uniform(20, 400), 2)))
            atr = Decimal(str(round(rng.uniform(0.5, 8), 4)))
            bars = []
            p = float(price)
            for i in range(rng.randint(10, 60)):
                move = rng.uniform(-float(atr), float(atr)) * 0.6
                nxt = max(1.0, p + move)
                hi = max(p, nxt) + rng.uniform(0, float(atr) * 0.4)
                lo = min(p, nxt) - rng.uniform(0, float(atr) * 0.4)
                start = datetime.combine(
                    day, datetime.min.time(), tzinfo=UTC
                ) + timedelta(hours=i)
                bars.append(Bar1h(
                    symbol="X", start=start, end=start + timedelta(hours=1),
                    open=Decimal(str(round(p, 4))),
                    high=Decimal(str(round(hi, 4))),
                    low=Decimal(str(round(lo, 4))),
                    close=Decimal(str(round(nxt, 4))),
                    volume=1000, feed="sip", complete=True,
                ))
                p = nxt
            daily = [
                dbar(day - timedelta(days=d), str(round(p, 2)),
                     str(round(p * 1.02, 2)), str(round(p * 0.98, 2)),
                     str(round(p, 2)))
                for d in range(20, 0, -1)
            ]
            todays = [
                bar5m(day, 9, 30 + i * 5, str(round(p + 1, 2)),
                      str(round(p - 1, 2)), str(round(p, 2)))
                for i in range(4)
            ]
            levels = find_levels(
                bars_1h=bars, daily=daily, today_5m=todays,
                price=price, cfg=CFG,
            )
            assert [lv.id for lv in levels] == [
                f"L{i + 1}" for i in range(len(levels))
            ]
            reference = price - Decimal("0.5")
            limit = (price * Decimal("1.0005")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            result = build_entry_options(
                levels=levels, price=price, reference=reference,
                limit=limit, atr_1h=atr, cfg=CFG,
            )
            for o in result.options:
                distance = reference - o.stop
                assert CFG.stop_atr_min * atr <= distance <= CFG.stop_atr_max * atr
                assert o.rr >= CFG.min_reward_risk
                assert o.target > limit > o.stop
            if not result.options:
                assert result.reason


def test_options_version_marker() -> None:
    assert OPTIONS_VERSION == "v2"
