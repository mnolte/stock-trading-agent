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
    """The live Alpaca base URL must not appear anywhere in the repository."""
    excludes = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "__pycache__",
        ".pytest_cache",
        "tests",
        "*.db",
    }
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in excludes for part in path.parts):
            continue
        if path.suffix == ".pyc":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert url not in text, f"Live Alpaca URL found in {path.relative_to(repo_root)}"
