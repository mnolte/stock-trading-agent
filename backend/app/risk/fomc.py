"""The FOMC window (docs/SPEC.md, Economic releases — item 067).

No new entries in a cycle that starts between ``events.fomc.
block_minutes_before`` minutes before and ``block_minutes_after`` minutes
after the 14:00 New York rate decision on a configured FOMC date. The
same test runs in the rules pre-filter (cycle start) and at order time
(current time), so a supervised approval inside the window is refused.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from app.market.clock import NY_TZ

if TYPE_CHECKING:
    from app.config import AppConfig


def _parse_hhmm(raw: object, default: time) -> time:
    if isinstance(raw, str):
        try:
            return datetime.strptime(raw, "%H:%M").time()  # noqa: DTZ007
        except ValueError:
            pass
    return default


def _parse_date(raw: object) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None


def fomc_dates(config: AppConfig) -> list[date]:
    """The configured FOMC decision dates, sorted, malformed entries dropped."""
    raw = config.get("events", "fomc", "dates", default=[]) or []
    days = [d for d in (_parse_date(v) for v in raw) if d is not None]
    return sorted(days)


def fomc_decision_at(day: date, config: AppConfig) -> datetime:
    """The decision's New York datetime on ``day``."""
    hhmm = _parse_hhmm(
        config.get("events", "fomc", "time", default="14:00"), time(14, 0)
    )
    return datetime.combine(day, hhmm, tzinfo=NY_TZ)


def in_fomc_window(at: datetime, config: AppConfig) -> str | None:
    """The window detail when ``at`` (New York) sits inside an FOMC
    window, else None. ``events.fomc.enabled: false`` never blocks."""
    if not bool(config.get("events", "fomc", "enabled", default=True)):
        return None
    ny = at.astimezone(NY_TZ)
    before = int(
        config.get("events", "fomc", "block_minutes_before", default=30)
    )
    after = int(
        config.get("events", "fomc", "block_minutes_after", default=45)
    )
    for day in fomc_dates(config):
        decision = fomc_decision_at(day, config)
        start = decision - timedelta(minutes=before)
        end = decision + timedelta(minutes=after)
        if start <= ny <= end:
            return (
                f"FOMC decision {decision:%H:%M} on {day:%d %b}: "
                f"no entries {start:%H:%M}–{end:%H:%M} New York"
            )
    return None
