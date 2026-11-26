"""PR 24: deploy/ scripts — the market-hours guard on both sides of the
1 November 2026 US clock change (injected clock), snapshot whitelisting
in restore.sh, and scripts are bash-clean."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "deploy.sh"
RESTORE = ROOT / "deploy" / "restore.sh"
PROVISION = ROOT / "deploy" / "provision.sh"
OLD_DIGEST = "sha256:" + "1" * 64
NEW_DIGEST = "sha256:" + "a" * 64

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


# ---------------------------------------------------------------------
# Item 084: deploy by digest — the tag is resolved, the cosign signature
# verified, and only then does anything change. The script runs against
# a sandbox copy (its .env lives next to the compose file) with fake
# docker/cosign binaries; nothing reaches a registry.


def _deploy_sandbox(tmp_path: Path) -> Path:
    """deploy.sh + restore.sh + compose file + a populated .env."""
    (tmp_path / "deploy").mkdir()
    for name in ("deploy.sh", "restore.sh"):
        shutil.copy(ROOT / "deploy" / name, tmp_path / "deploy" / name)
    shutil.copy(ROOT / "docker-compose.yml", tmp_path / "docker-compose.yml")
    (tmp_path / ".env").write_text(
        "SA_VERSION=v1.0.0\n"
        f"SA_IMAGE=ghcr.io/mnolte/stock-agent@{OLD_DIGEST}\n"
        "SA_IMAGE_VERIFIED=2026-01-01T00:00:00Z\n"
    )
    return tmp_path


def _fake_bins(tmp_path: Path, cosign_rc: int = 0) -> dict[str, str]:
    """Fake docker (answers imagetools, logs every call) and cosign."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    docker = bindir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "docker $*" >> "{log}"\n'
        'if [[ "$1" == "buildx" ]]; then\n'
        f'    printf \'"%s"\\n\' "{NEW_DIGEST}"\n'
        "fi\n"
        # A failing health check drives the rollback path.
        'if [[ "$*" == *"/healthz"* ]]; then exit "${FAKE_HEALTH_RC:-0}"; fi\n'
        "exit 0\n"
    )
    cosign = bindir / "cosign"
    cosign.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "cosign $*" >> "{log}"\n'
        f"exit {cosign_rc}\n"
    )
    docker.chmod(0o755)
    cosign.chmod(0o755)
    return {
        "SA_DOCKER": str(docker),
        "SA_COSIGN": str(cosign),
        "FAKE_LOG": str(log),
        "NEW_DIGEST": NEW_DIGEST,
    }


def _env(extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update({"SA_DEPLOY_GUARD": "0", "PATH": os.environ["PATH"]})
    env.update(extra)
    return env


def test_no_act_run_verifies_signature_before_backup_and_pull(
    tmp_path: Path,
) -> None:
    root = _deploy_sandbox(tmp_path)
    out = subprocess.run(
        ["bash", "deploy/deploy.sh", "v2.0.0"],
        cwd=root,
        env=_env({"SA_NO_ACT": "1", "SA_IMAGE_DIGEST": NEW_DIGEST}),
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    text = out.stdout
    verify_at = text.index("cosign verify")
    backup_at = text.index("app.db.backup")
    pull_at = text.index(" pull app")
    assert verify_at < backup_at < pull_at
    # .env now names the verified digest, the tag and the check time.
    env_text = (root / ".env").read_text()
    assert f"SA_IMAGE=ghcr.io/mnolte/stock-agent@{NEW_DIGEST}" in env_text
    assert "SA_VERSION=v2.0.0" in env_text
    assert "SA_IMAGE_VERIFIED=20" in env_text


def test_failed_signature_verification_changes_nothing(tmp_path: Path) -> None:
    root = _deploy_sandbox(tmp_path)
    fakes = _fake_bins(tmp_path, cosign_rc=1)
    before = (root / ".env").read_text()
    out = subprocess.run(
        ["bash", "deploy/deploy.sh", "v2.0.0"],
        cwd=root,
        env=_env(fakes),
        capture_output=True,
        text=True,
    )
    assert out.returncode != 0
    assert "signature verification failed" in out.stderr + out.stdout
    # Nothing changed: same .env, and docker was only asked for the digest.
    assert (root / ".env").read_text() == before
    calls = Path(fakes["FAKE_LOG"]).read_text()
    assert "buildx imagetools inspect" in calls
    assert "compose" not in calls


def test_unverified_needs_emergency(tmp_path: Path) -> None:
    root = _deploy_sandbox(tmp_path)
    out = subprocess.run(
        ["bash", "deploy/deploy.sh", "v2.0.0", "--unverified"],
        cwd=root,
        env=_env({"SA_NO_ACT": "1", "SA_IMAGE_DIGEST": NEW_DIGEST}),
        capture_output=True,
        text=True,
    )
    assert out.returncode != 0
    assert "--emergency" in out.stderr


def test_rollback_restores_the_previous_digest(tmp_path: Path) -> None:
    root = _deploy_sandbox(tmp_path)
    fakes = _fake_bins(tmp_path, cosign_rc=0)
    fakes["FAKE_HEALTH_RC"] = "1"  # /healthz never comes up → rollback
    fakes["SA_DEPLOY_WAIT_TRIES"] = "1"
    fakes["SA_DEPLOY_WAIT_SLEEP"] = "0"
    out = subprocess.run(
        ["bash", "deploy/deploy.sh", "v2.0.0"],
        cwd=root,
        env=_env(fakes),
        capture_output=True,
        text=True,
    )
    assert out.returncode == 1
    env_text = (root / ".env").read_text()
    assert f"SA_IMAGE=ghcr.io/mnolte/stock-agent@{OLD_DIGEST}" in env_text
    assert "SA_VERSION=v1.0.0" in env_text
    assert "SA_IMAGE_VERIFIED=2026-01-01T00:00:00Z" in env_text
    calls = Path(fakes["FAKE_LOG"]).read_text()
    assert calls.count("pull app") == 2  # the deploy pull and the rollback pull
