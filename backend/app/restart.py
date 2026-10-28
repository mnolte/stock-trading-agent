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
from typing import TYPE_CHECKING

from app.config import AppConfig

if TYPE_CHECKING:
    from app.engine.executor import ReconcileReport


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
