"""Tests for the spec-label CI gate (.github/scripts/check-spec-label.sh).

Each case builds a temporary git repo, copies the gate script and
spec-paths.txt in (untracked, so they don't appear in the diff), makes a
baseline commit and a "PR" commit, then runs the script with the same env
vars the workflow sets: BASE_SHA, HEAD_SHA, HAS_LABEL.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-spec-label.sh"

# The gate script lives outside the app tree: it is not shipped in the
# production image, so these tests skip there (still run in CI's own
# checkout and locally).
pytestmark = pytest.mark.skipif(
    not SCRIPT.exists() or shutil.which("git") is None,
    reason="spec-label gate script or git not available in this environment",
)


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )
    return out.stdout.strip()


def run_gate(
    tmp_path: Path,
    pr_files: dict[str, str],
    label: bool,
    base_files: dict[str, str] | None = None,
    renames: list[tuple[str, str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "repo"
    (repo / ".github" / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / ".github" / "scripts" / "check-spec-label.sh")
    shutil.copy(REPO_ROOT / ".github" / "spec-paths.txt", repo / ".github" / "spec-paths.txt")

    _git(repo, "init", "-q")
    for rel, content in {"backend/app/x.py": "x = 1\n", **(base_files or {})}.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        _git(repo, "add", rel)
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    for rel, content in pr_files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        _git(repo, "add", rel)
    for old, new in renames or []:
        (repo / new).parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "mv", old, new)
    _git(repo, "commit", "-qm", "pr")
    head = _git(repo, "rev-parse", "HEAD")

    env = {
        k: v
        for k, v in os.environ.items()
        if k != "GITHUB_STEP_SUMMARY"
    } | {
        "BASE_SHA": base,
        "HEAD_SHA": head,
        "HAS_LABEL": "true" if label else "false",
    }
    return subprocess.run(
        ["bash", ".github/scripts/check-spec-label.sh"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def test_code_only_without_label_passes(tmp_path: Path) -> None:
    r = run_gate(tmp_path, {"backend/app/y.py": "y = 2\n"}, label=False)
    assert r.returncode == 0, r.stdout + r.stderr


def test_spec_change_without_label_fails(tmp_path: Path) -> None:
    r = run_gate(tmp_path, {"docs/SPEC.md": "spec\n"}, label=False)
    assert r.returncode == 1
    assert "no 'spec change' label" in r.stdout


def test_spec_change_with_label_and_changelog_passes(tmp_path: Path) -> None:
    r = run_gate(
        tmp_path,
        {"docs/SPEC.md": "spec\n", "docs/CHANGES.md": "entry\n"},
        label=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_spec_change_with_label_but_no_changelog_fails(tmp_path: Path) -> None:
    r = run_gate(tmp_path, {"docs/SPEC.md": "spec\n"}, label=True)
    assert r.returncode == 1
    assert "docs/CHANGES.md" in r.stdout


def test_design_screen_without_design_changelog_fails(tmp_path: Path) -> None:
    r = run_gate(tmp_path, {"design/screens/Main.dc.html": "<html/>\n"}, label=True)
    assert r.returncode == 1
    assert "design/CHANGES.md" in r.stdout


def test_new_prompt_with_label_and_changelog_passes(tmp_path: Path) -> None:
    r = run_gate(
        tmp_path,
        {
            "backend/prompts/decision.v3.md": "prompt\n",
            "docs/CHANGES.md": "entry\n",
        },
        label=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_config_example_without_label_fails(tmp_path: Path) -> None:
    r = run_gate(tmp_path, {"backend/config.example.yaml": "k: v\n"}, label=False)
    assert r.returncode == 1


def test_label_without_spec_files_warns_but_passes(tmp_path: Path) -> None:
    r = run_gate(tmp_path, {"backend/app/y.py": "y = 2\n"}, label=True)
    assert r.returncode == 0
    assert "::warning::" in r.stdout


def test_rename_out_of_design_counts_via_old_path(tmp_path: Path) -> None:
    r = run_gate(
        tmp_path,
        {},
        label=False,
        base_files={"design/notes.md": "old\n"},
        renames=[("design/notes.md", "notes.md")],
    )
    assert r.returncode == 1


def test_nested_design_path_matches_design_glob(tmp_path: Path) -> None:
    r = run_gate(tmp_path, {"design/screens/sub/x.html": "<html/>\n"}, label=False)
    assert r.returncode == 1


def test_editing_the_gate_itself_needs_the_label(tmp_path: Path) -> None:
    # A comment-only edit still changes the file — the gate guards itself.
    comment = (REPO_ROOT / ".github" / "spec-paths.txt").read_text() + "# touch\n"
    r = run_gate(tmp_path, {".github/spec-paths.txt": comment}, label=False)
    assert r.returncode == 1
