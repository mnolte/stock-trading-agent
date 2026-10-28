"""PR 24: resume after a clean planned restart (SPEC → VPS deployment →
Restarts). Only every check passing resumes; any failure stays paused."""

from __future__ import annotations

import app.config as config_module
import pytest
from app.engine.executor import ReconcileReport
from app.restart import (
    decide_restart,
    resume_after_clean_restart_enabled,
)


def _report(*, ok: bool = True, mismatches: int = 0) -> ReconcileReport:
    report = ReconcileReport()
    report.ok = ok
    report.mismatches = ["x"] * mismatches
    return report


def test_auto_means_on_when_auth_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SA_AUTH", "required")
    monkeypatch.setattr(config_module, "_config", None)
    from app.config import get_config

    assert resume_after_clean_restart_enabled(get_config())


def test_auto_means_off_when_auth_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SA_AUTH", "off")
    monkeypatch.setattr(config_module, "_config", None)
    from app.config import get_config

    assert not resume_after_clean_restart_enabled(get_config())


def test_clean_running_resumes() -> None:
    d = decide_restart(
        was_running=True,
        clean_shutdown=True,
        kill_switch_on=False,
        reconcile=_report(),
        broker_guard_confirmed=True,
    )
    assert d.resume and not d.reasons


@pytest.mark.parametrize(
    ("kw", "missing"),
    [
        ({"was_running": False}, "it was not running before"),
        ({"clean_shutdown": False}, "the previous shutdown was not clean"),
        ({"kill_switch_on": True}, "the kill switch is on"),
        ({"reconcile": _report(mismatches=2)}, "reconciliation found differences"),
        ({"reconcile": _report(ok=False)}, "reconciliation found differences"),
        ({"reconcile": None}, "reconciliation found differences"),
        ({"broker_guard_confirmed": False}, "the broker guard is unconfirmed"),
    ],
)
def test_each_check_blocks_resume(kw: dict[str, object], missing: str) -> None:
    base = {
        "was_running": True,
        "clean_shutdown": True,
        "kill_switch_on": False,
        "reconcile": _report(),
        "broker_guard_confirmed": True,
    }
    base.update(kw)
    d = decide_restart(**base)  # type: ignore[arg-type]
    assert not d.resume
    assert missing in d.reasons
