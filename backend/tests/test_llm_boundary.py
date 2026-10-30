"""Provider SDKs live only in app/llm/providers (hard rule 21)."""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
PROVIDERS = APP / "llm" / "providers"

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(openai|anthropic)\s+import|import\s+(openai|anthropic)\b)",
    re.MULTILINE,
)


def test_provider_sdks_only_in_adapters() -> None:
    offenders: list[str] = []
    for path in APP.rglob("*.py"):
        if PROVIDERS in path.parents or path == PROVIDERS / "__init__.py":
            continue
        if path.parent == APP / "llm" / "providers":
            continue
        text = path.read_text(encoding="utf-8")
        if _IMPORT_RE.search(text):
            offenders.append(str(path.relative_to(APP)))
    assert not offenders, f"SDK imports outside app/llm/providers: {offenders}"
