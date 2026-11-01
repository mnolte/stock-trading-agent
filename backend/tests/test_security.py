import re
import subprocess
from pathlib import Path

import pytest

# The live Alpaca host on a hostname-label boundary: a letter, digit or
# hyphen before ``api`` means a different, longer label (``paper-api.``,
# ``xapi.``); a leading ``.`` is a subdomain of the live host and fails.
LIVE_ALPACA_HOST = re.compile(r"(?<![A-Za-z0-9-])api\.alpaca\.markets")


@pytest.mark.parametrize(
    "url",
    [
        "https://api.alpaca.markets",
        "api.alpaca.markets",
    ],
)
def test_live_alpaca_url_not_present(repo_root: Path, url: str) -> None:
    """The live Alpaca base URL must not appear anywhere in the repository.

    Tracked files only. The paper host contains the live host as a
    substring of a different label, so the bare host is matched on a
    label boundary — docs may legitimately quote the paper URL, and
    runtime artifacts under ``data/`` do too.
    """
    pattern = re.compile(re.escape(url)) if "://" in url else LIVE_ALPACA_HOST
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
        assert pattern.search(text) is None, f"Live Alpaca URL found in {rel}"


@pytest.mark.parametrize(
    ("text", "live"),
    [
        ("api.alpaca.markets", True),
        ("https://api.alpaca.markets/v2", True),
        ("x.api.alpaca.markets", True),
        ("paper-api.alpaca.markets", False),
        ("xapi.alpaca.markets", False),
    ],
)
def test_live_alpaca_host_boundary(text: str, live: bool) -> None:
    """The host check fires on the live host only, not a longer label."""
    assert (LIVE_ALPACA_HOST.search(text) is not None) is live
