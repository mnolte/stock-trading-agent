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

_GUARD = r"""
market_hours_now() {
    local now="${SA_DEPLOY_NOW:-$(date -u '+%Y-%m-%dT%H:%M:%SZ')}"
    TZ=America/New_York date -d "$now" '+%u %H%M'
}
read -r weekday hhmm <<<"$(market_hours_now)"
if (( weekday <= 5 )) && (( 10#$hhmm >= 930 && 10#$hhmm < 1600 )); then
    echo BLOCK
else
    echo ok
fi
"""


def _guard(at: str) -> str:
    out = subprocess.run(
        ["bash", "-c", _GUARD],
        env={"SA_DEPLOY_NOW": at, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


# 26 Oct – 1 Nov 2026: EU moved to winter time on 25 Oct, the US stays
# on summer time until 2 Nov — the week the offsets differ.
@pytest.mark.parametrize(
    ("at", "expect"),
    [
        ("2026-10-30T13:29:00Z", "ok"),      # Fri 09:29 EDT — just before
        ("2026-10-30T13:30:00Z", "BLOCK"),   # Fri 09:30 EDT
        ("2026-10-30T19:59:00Z", "BLOCK"),   # Fri 15:59 EDT
        ("2026-10-30T20:00:00Z", "ok"),      # Fri 16:00 EDT
        ("2026-11-02T14:29:00Z", "ok"),      # Mon 09:29 EST (US on winter now)
        ("2026-11-02T14:30:00Z", "BLOCK"),   # Mon 09:30 EST
        ("2026-11-02T20:59:00Z", "BLOCK"),   # Mon 15:59 EST
        ("2026-11-02T21:00:00Z", "ok"),      # Mon 16:00 EST
        ("2026-11-01T16:00:00Z", "ok"),      # Sunday — no session
    ],
)
def test_market_hours_guard(at: str, expect: str) -> None:
    assert _guard(at) == expect


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
