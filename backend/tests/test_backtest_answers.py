"""The backtest answer sentence (docs/SPEC.md, Backtesting → GUI, item 100)."""

from __future__ import annotations

from decimal import Decimal

from app.backtests.answers import answer_sentence


def _summary(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "return_pct": 18.6,
        "spy_return_pct": 39.9,
        "max_drawdown_pct": -5.2,
        "time_in_market": 0.24,
        "spy_curve": [
            {"date": "2024-01-02", "equity": 10000},
            {"date": "2026-09-30", "equity": 13990},
        ],
    }
    base.update(over)
    return base


def _strategies(*names: str) -> list[dict[str, str]]:
    return [{"name": name, "slug": name.lower().replace(" ", "-")} for name in names]


def test_one_strategy_behind_spy() -> None:
    specs = _strategies("Live settings v3")
    answer = answer_sentence(specs, {"live-settings-v3": _summary()}, Decimal(10000))
    assert answer == (
        "Live settings v3 made +18.6% where buying and holding SPY made "
        "+39.9%: 21.3 points less, with a deepest fall of 5.2% against "
        "0.0% and money in the market 24% of the time."
    )


def test_one_strategy_ahead_of_spy() -> None:
    specs = _strategies("Live settings v3")
    summary = {
        "live-settings-v3": _summary(
            return_pct=50.1,
            spy_return_pct=39.9,
            max_drawdown_pct=-5.2,
            spy_curve=[
                {"date": "2024-01-02", "equity": 10000},
                {"date": "2024-06-01", "equity": 8110},
                {"date": "2026-09-30", "equity": 13990},
            ],
        )
    }
    answer = answer_sentence(specs, summary, Decimal(10000))
    assert answer == (
        "Live settings v3 made +50.1% where buying and holding SPY made "
        "+39.9%: 10.2 points more, with a deepest fall of 5.2% against "
        "18.9% and money in the market 24% of the time."
    )


def test_two_strategies_compare_to_the_first() -> None:
    specs = _strategies("Live settings v3", "Rules only")
    summary = {
        "live-settings-v3": _summary(),
        "rules-only": {"return_pct": 4.1},
    }
    answer = answer_sentence(specs, summary, Decimal(10000))
    assert answer is not None
    assert answer.endswith(
        " Rules only made +4.1%, 14.5 points less than Live settings v3."
    )


def test_three_strategies_and_a_tie_reads_the_same_as() -> None:
    specs = _strategies("Live", "A", "B")
    summary = {
        "live": _summary(),
        "a": {"return_pct": 30.0},
        "b": {"return_pct": 18.6},
    }
    answer = answer_sentence(specs, summary, Decimal(10000))
    assert answer == (
        "Live made +18.6% where buying and holding SPY made +39.9%: "
        "21.3 points less, with a deepest fall of 5.2% against 0.0% and "
        "money in the market 24% of the time. A made +30.0%, 11.4 points "
        "more than Live. B made +18.6%, the same as Live."
    )


def test_no_summary_means_no_sentence() -> None:
    assert answer_sentence(_strategies("Live"), None, Decimal(10000)) is None
    assert answer_sentence(_strategies("Live"), {}, Decimal(10000)) is None
    assert (
        answer_sentence(_strategies("Live"), {"other": _summary()}, Decimal(10000))
        is None
    )
    assert (
        answer_sentence(
            _strategies("Live"),
            {"live": {"return_pct": 1.0}},
            Decimal(10000),
        )
        is None
    )
