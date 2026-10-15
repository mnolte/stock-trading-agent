"""Prompt loading and versioning (docs/SPEC.md, Decision layer).

The configured prompt file under ``backend/prompts/`` is the system prompt.
Each decision stores the prompt version and its SHA-256 hash so a decision can
always be traced to the exact prompt text. A prompt file is immutable once it
has been used: a change means a new file (``decision.v2.md``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@dataclass(frozen=True)
class Prompt:
    """A versioned prompt file."""

    version: str  # e.g. "decision.v1"
    text: str
    sha256: str

    @property
    def short_hash(self) -> str:
        return self.sha256[:8]


def load_prompt(version: str) -> Prompt:
    """Load ``prompts/<version>.md`` and hash its exact contents."""
    path = PROMPTS_DIR / f"{version}.md"
    text = path.read_text(encoding="utf-8")
    return Prompt(version=version, text=text, sha256=hashlib.sha256(text.encode()).hexdigest())


@lru_cache(maxsize=8)
def cached_prompt(version: str) -> Prompt:
    """Load once per process; prompt files never change at runtime."""
    return load_prompt(version)
