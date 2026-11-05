"""PR 24: deploy/ scripts — the market-hours guard on both sides of the
1 November 2026 US clock change (injected clock), snapshot whitelisting
in restore.sh, and scripts are bash-clean."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "deploy.sh"
RESTORE = ROOT / "deploy" / "restore.sh"
PROVISION = ROOT / "deploy" / "provision.sh"

def _extract_guard() -> str:
    """The real market_hours_now + check_market_hours definitions,
    lifted out of deploy/deploy.sh — the test exercises the script's
    own code, so a broken or weakened guard fails here."""
    out = subprocess.run(
        [
            "sed", "-n",
            "/^market_hours_now()/,/^}/p; /^check_market_hours()/,/^}/p",
            str(DEPLOY),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "check_market_hours" in out.stdout
    return out.stdout


def _guard(at: str, extra_env: dict[str, str] | None = None) -> int:
    """Exit status of the script's check_market_hours: 0 = allowed."""
    script = (
        'die() { echo "$*" >&2; exit 1; }\n'
        + _extract_guard()
        + "\ncheck_market_hours\n"
    )
    env = {"SA_DEPLOY_NOW": at, "PATH": "/usr/bin:/bin", **(extra_env or {})}
    out = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True
    )
    return out.returncode


# 26 Oct – 1 Nov 2026: EU moved to winter time on 25 Oct, the US stays
# on summer time until 2 Nov — the week the offsets differ.
@pytest.mark.parametrize(
    ("at", "expect"),
    [
        ("2026-10-30T13:29:00Z", 0),  # Fri 09:29 EDT — just before
        ("2026-10-30T13:30:00Z", 1),  # Fri 09:30 EDT
        ("2026-10-30T19:59:00Z", 1),  # Fri 15:59 EDT
        ("2026-10-30T20:00:00Z", 0),  # Fri 16:00 EDT
        ("2026-11-02T14:29:00Z", 0),  # Mon 09:29 EST (US on winter now)
        ("2026-11-02T14:30:00Z", 1),  # Mon 09:30 EST
        ("2026-11-02T20:59:00Z", 1),  # Mon 15:59 EST
        ("2026-11-02T21:00:00Z", 0),  # Mon 16:00 EST
        ("2026-11-01T16:00:00Z", 0),  # Sunday — no session
    ],
)
def test_market_hours_guard(at: str, expect: int) -> None:
    assert _guard(at) == expect


@pytest.mark.parametrize("extra", [{"EMERGENCY": "1"}, {"SA_DEPLOY_GUARD": "0"}])
def test_market_hours_guard_bypasses(extra: dict[str, str]) -> None:
    """--emergency and SA_DEPLOY_GUARD=0 both skip the block."""
    assert _guard("2026-11-02T14:30:00Z", extra) == 0


def test_deploy_script_uses_ny_timezone_not_fixed_offset() -> None:
    text = DEPLOY.read_text()
    assert "TZ=America/New_York" in text
    assert "date -d" in text
    assert "--emergency" in text
    # never a fixed UTC offset for the window
    assert "09:30 UTC" not in text and "15:30" not in text


def test_scripts_are_bash_clean_and_strict() -> None:
    for script in (DEPLOY, RESTORE, PROVISION):
        subprocess.run(["bash", "-n", str(script)], check=True)
        text = script.read_text()
        assert "set -euo pipefail" in text
        assert "--help" in text or "-h" in text


def test_restore_whitelists_snapshot_names() -> None:
    text = RESTORE.read_text()
    assert "stock-agent\\.db\\." in text  # dated backup shape only
    assert "integrity_check" in text
    assert "0600" in text


def test_deploy_rolls_back_and_checks_health() -> None:
    text = DEPLOY.read_text()
    for piece in (
        "app.db.backup",           # pre-deploy backup
        "alembic upgrade head",    # migrations in a one-off container
        "/healthz",
        "SA_VERSION",
        "PREVIOUS_TAG",
        "/flags/updating",         # PR 25 maintenance hooks
        "restart",
    ):
        assert piece in text, piece


def test_no_process_killed_by_name() -> None:
    # hard rule 12: stop only containers by name from our compose project
    for script in (DEPLOY, RESTORE, PROVISION):
        text = script.read_text()
        assert "pkill" not in text and "killall" not in text
