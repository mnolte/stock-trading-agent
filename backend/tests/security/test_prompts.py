"""PR 28 step 4 — prompt names are whitelisted, contained and hashed.

``load_prompt`` refuses a version that fails the regex, escapes
``prompts/`` or whose file doesn't match ``manifest.json``; the manifest
test is the tripwire that enforces prompt immutability (hard rule 8).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from pathlib import Path

import app.decide.prompt as prompt_mod
import pytest
from app.decide.prompt import (
    PROMPTS_DIR,
    PromptError,
    is_safe_name,
    load_prompt,
)


@pytest.fixture(autouse=True)
def _clean_manifest_cache() -> Generator[None, None, None]:
    """The manifest is lru-cached; a monkeypatched PROMPTS_DIR must not
    leak its cache into the next test."""
    prompt_mod._manifest.cache_clear()
    yield
    prompt_mod._manifest.cache_clear()


def test_traversal_version_refused() -> None:
    with pytest.raises(PromptError):
        load_prompt("../../README")
    with pytest.raises(PromptError):
        load_prompt("decision")


def test_manifest_covers_every_prompt_file() -> None:
    """A listed prompt whose file changed — or a new file with no line —
    fails here first, before any load."""
    manifest = json.loads((PROMPTS_DIR / "manifest.json").read_text())
    on_disk = {p.name for p in PROMPTS_DIR.glob("*.md")}
    assert set(manifest) == on_disk
    for name, sha in manifest.items():
        actual = hashlib.sha256((PROMPTS_DIR / name).read_bytes()).hexdigest()
        assert actual == sha, f"{name} changed — prompts are immutable once used"


def test_every_prompt_loads() -> None:
    for path in PROMPTS_DIR.glob("*.md"):
        assert load_prompt(path.stem).text


def test_tampered_prompt_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file whose content no longer matches the manifest is refused."""
    fake_dir = tmp_path / "prompts"
    fake_dir.mkdir()
    (fake_dir / "decision.v9.md").write_text("tampered text")
    (fake_dir / "manifest.json").write_text(
        json.dumps({"decision.v9.md": hashlib.sha256(b"original").hexdigest()})
    )
    monkeypatch.setattr(prompt_mod, "PROMPTS_DIR", fake_dir)
    prompt_mod._manifest.cache_clear()
    with pytest.raises(PromptError, match="manifest hash"):
        load_prompt("decision.v9")


def test_unlisted_prompt_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dir = tmp_path / "prompts"
    fake_dir.mkdir()
    (fake_dir / "decision.v9.md").write_text("new version, no manifest line")
    (fake_dir / "manifest.json").write_text("{}")
    monkeypatch.setattr(prompt_mod, "PROMPTS_DIR", fake_dir)
    prompt_mod._manifest.cache_clear()
    with pytest.raises(PromptError, match="not in the manifest"):
        load_prompt("decision.v9")


def test_settings_prompt_version_is_422(client) -> None:
    """The API answer for a bad version is 422, never a path read or 500."""
    r = client.post(
        "/api/settings",
        json={
            "settings": {"decision": {"prompt": "../../README"}},
            "reason": "probe",
        },
    )
    assert r.status_code == 422


def test_folder_names() -> None:
    assert is_safe_name("live")
    assert is_safe_name("b-bigger-model-2")
    assert not is_safe_name("../etc")
    assert not is_safe_name("a/b")
    assert not is_safe_name("..")
