import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "url",
    [
        "https://api.alpaca.markets",
        "api.alpaca.markets",
    ],
)
def test_live_alpaca_url_not_present(repo_root: Path, url: str) -> None:
    """The live Alpaca base URL must not appear anywhere in the repository.

    Tracked files only: runtime artifacts under ``data/`` (logs, databases)
    legitimately mention the paper URL — ``paper-api.alpaca.markets``
    contains the live host as a substring.
    """
    tracked = subprocess.run(  # noqa: S603
        ["git", "ls-files"],  # noqa: S607
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for rel in tracked:
        path = repo_root / rel
        if not path.is_file() or "tests" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert url not in text, f"Live Alpaca URL found in {rel}"
