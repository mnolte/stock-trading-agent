"""README.md stays in step with the repo.

Fails when a key in ``backend/config.example.yaml`` or a variable the
settings class reads is missing from README.md, or when a relative link
or image path in README.md does not resolve.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _dotted(tree: dict[str, object], prefix: str = "") -> list[str]:
    out = []
    for key, value in tree.items():
        path = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            out.append(path)
            out.extend(_dotted(value, path))
        else:
            out.append(path)
    return out


def test_config_keys_in_readme() -> None:
    """Every config.example.yaml key (dotted path) appears in the README."""
    text = _readme()
    tree = yaml.safe_load((ROOT / "backend" / "config.example.yaml").read_text())
    # Dict-node paths are the parents that can cover a leaf (a per-symbol
    # map, a per-model table, a named block). Values like `gpt-4.1-mini`
    # contain dots, so ancestors come from the tree, not from rpartition.
    nodes: list[str] = []
    def _nodes(tree: dict[str, object], prefix: str = "") -> None:
        for key, value in tree.items():
            path = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            if isinstance(value, dict):
                nodes.append(path)
                _nodes(value, path)
    _nodes(tree)
    missing = []
    for key in _dotted(tree):
        if key in text:
            continue
        covered = False
        for node in nodes:
            if key.startswith(node + ".") and (
                f"`{node}`" in text or f"{node}.*" in text
            ):
                covered = True
                break
        if not covered:
            missing.append(key)
    assert not missing, f"config keys missing from README.md: {missing}"


def test_env_vars_in_readme() -> None:
    """Every env alias in EnvSettings appears in the README."""
    text = _readme()
    src = (ROOT / "backend" / "app" / "config.py").read_text()
    aliases = set(re.findall(r'alias="(SA_[A-Z_]+|MODE)"', src))
    missing = sorted(a for a in aliases if a not in text)
    assert not missing, f"env vars missing from README.md: {missing}"


def test_readme_links_resolve() -> None:
    """Relative markdown links and images in README.md resolve to files."""
    text = _readme()
    targets = re.findall(r"!?\[[^\]]*\]\(([^)\s]+)\)", text)
    bad = []
    for target in targets:
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#", 1)[0]
        if not path:
            continue
        if not (ROOT / path).exists():
            bad.append(target)
    assert not bad, f"README.md links that do not resolve: {bad}"
