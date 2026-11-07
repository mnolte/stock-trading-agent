"""Resume after a clean planned restart (docs/SPEC.md → VPS deployment →
Restarts).

Default is still paused at startup. The exception on the VPS
(``ops.resume_after_clean_restart``, ``auto`` = on when
``SA_AUTH=required``) resumes on its own only when all of these hold:

- it was running before (the last pause event is a resume);
- the previous shutdown was clean (no ``run.lock`` left);
- the kill switch is off;
- reconciliation found no differences;
- the broker guard is confirmed.

Either way the agent says so in a banner and a notification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.config import AppConfig
from app.market.clock import NY_TZ, FakeClock, SessionClock

if TYPE_CHECKING:
    from app.engine.executor import ReconcileReport
    from app.market.calendar import CalendarProvider


def unclean_summary(
    last_seen: datetime,
    now: datetime,
    *,
    config: AppConfig,
    calendar: CalendarProvider,
) -> dict[str, str]:
    """"Last alive 18:46:35 · down 61 min · missed 3 position checks and
    the 19:46 cycle" — the unclean-run banner/log/notification wording
    (design/screens/States.dc.html; docs/SPEC.md, Clean shutdown).

    Missed work counts the scheduled position checks and cycles that
    fell inside the downtime window, from the trading calendar; outside
    a session it says "missed nothing".
    """
    app_tz = ZoneInfo(
        str(config.get("app", "timezone", default="Europe/Amsterdam"))
    )
    clock = SessionClock(FakeClock(now), config, calendar)
    start = last_seen.astimezone(NY_TZ).date()
    end = now.astimezone(NY_TZ).date()
    checks: list[datetime] = []
    cycles: list[datetime] = []
    day = start
    while day <= end:
        session = calendar.session_for(day)
        if session is not None:
            checks += [
                t
                for t in clock.active_checks(session)
                if last_seen < t <= now
            ]
            cycles += [
                t
                for t in clock.active_cycles(session)
                if last_seen < t <= now
            ]
        day += timedelta(days=1)
    parts: list[str] = []
    if checks:
        parts.append(f"{len(checks)} position check{'s' if len(checks) != 1 else ''}")
    if cycles:
        parts.append(
            "the "
            + ", ".join(t.astimezone(app_tz).strftime("%H:%M") for t in cycles)
            + f" cycle{'s' if len(cycles) != 1 else ''}"
        )
    missed = " and ".join(parts) if parts else "nothing"
    down_minutes = max(0, int((now - last_seen).total_seconds() // 60))
    return {
        "last_alive": last_seen.astimezone(app_tz).strftime("%H:%M:%S"),
        "down": f"{down_minutes} min",
        "down_minutes": str(down_minutes),
        "missed": missed,
        "summary": (
            f"Last alive {last_seen.astimezone(app_tz):%H:%M:%S} · "
            f"down {down_minutes} min · missed {missed}"
        ),
    }


def resume_after_clean_restart_enabled(config: AppConfig) -> bool:
    """``auto`` = on when SA_AUTH=required, off locally."""
    raw = str(
        config.get("ops", "resume_after_clean_restart", default="auto")
    ).strip().lower()
    if raw == "auto":
        return config.env.sa_auth == "required"
    return raw in {"on", "true", "yes", "1"}


@dataclass
class RestartDecision:
    resume: bool
    reasons: list[str] = field(default_factory=list)


def decide_restart(
    *,
    was_running: bool,
    clean_shutdown: bool,
    kill_switch_on: bool,
    reconcile: ReconcileReport | None,
    broker_guard_confirmed: bool,
) -> RestartDecision:
    """The spec's five checks, in order; reasons for staying paused."""
    reasons: list[str] = []
    if not was_running:
        reasons.append("it was not running before")
    if not clean_shutdown:
        reasons.append("the previous shutdown was not clean")
    if kill_switch_on:
        reasons.append("the kill switch is on")
    if reconcile is None or not reconcile.ok or reconcile.mismatches:
        reasons.append("reconciliation found differences")
    if not broker_guard_confirmed:
        reasons.append("the broker guard is unconfirmed")
    return RestartDecision(resume=not reasons, reasons=reasons)
