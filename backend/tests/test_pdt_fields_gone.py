"""Alpaca removed pattern_day_trader, daytrade_count and
daytrading_buying_power by 6 July 2026 — nothing may read them again
(docs/SPEC.md, Day trades (rule retired))."""

from pathlib import Path

REMOVED = ("pattern_day_trader", "daytrade_count", "daytrading_buying_power")


def test_removed_alpaca_daytrade_fields_are_never_referenced() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    offenders = [
        f"{path}:{line_no}"
        for path in root.rglob("*.py")
        for line_no, line in enumerate(path.read_text().splitlines(), 1)
        if any(name in line for name in REMOVED)
    ]
    assert offenders == []
