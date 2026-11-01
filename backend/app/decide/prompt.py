"""Prompt loading and versioning (docs/SPEC.md, Decision layer).

The configured prompt file under ``backend/prompts/`` is the system prompt.
Each decision stores the prompt version and its SHA-256 hash so a decision can
always be traced to the exact prompt text. A prompt file is immutable once it
has been used: a change means a new file (``decision.v2.md``).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

# Whitelisted shapes (hard rule 19): a prompt version is always
# ``<name>.v<n>``; run/replay/backtest folder names get the looser
# safe-name check below. Anything else never becomes a path.
PROMPT_NAME_RE = re.compile(r"^[a-z0-9_]+\.v[0-9]+$")
FOLDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class PromptError(ValueError):
    """A prompt name or file fails the whitelist/containment/hash rules."""


def is_safe_name(name: str) -> bool:
    """Folder names from requests (run, replay, backtest slugs) never
    carry separators or dots that could climb out of their base dir."""
    return FOLDER_NAME_RE.fullmatch(name) is not None


@lru_cache(maxsize=1)
def _manifest() -> dict[str, str]:
    """``prompts/manifest.json``: the SHA-256 of every prompt file. A new
    prompt version adds its line; a listed file that changes fails the
    manifest test (hard rule 8 — prompts are immutable once used)."""
    path = PROMPTS_DIR / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()}


@dataclass(frozen=True)
class PromptSpec:
    """What a prompt version means in code (docs/SPEC.md, Decision v2).

    A strategy's prompt version picks the response schema and which
    snapshot/option builder runs — ``builder`` is ``v1`` (none), ``v2``
    or ``v3``.
    """

    version: str
    json_schema: dict[str, object]  # OpenAI strict JSON schema
    uses_options: bool = False  # decision.v2+: code-built options
    builder: str = "v1"  # "v2" | "v3" | "v4" snapshot builder

    @property
    def is_v3(self) -> bool:
        return self.builder in ("v3", "v4")

    @property
    def is_v4(self) -> bool:
        return self.builder == "v4"


class UnknownPromptVersion(ValueError):
    """A prompt version with no registered contract (SPEC: known versions only)."""

    def __init__(self, version: str) -> None:
        self.version = version
        super().__init__(f"unknown_prompt_version: {version}")


# Contracts per kind (docs/SPEC.md, Decision layer — Known prompt versions
# only). Every version named in config, a strategy, a backtest or a replay
# must be registered here or startup/save refuses it.
def prompt_spec(version: str) -> PromptSpec:
    """The code contract for a prompt version; unknown versions raise."""
    from app.decide.output import (
        DECISION_JSON_SCHEMA,
        DECISION_V2_JSON_SCHEMA,
        DECISION_V3_JSON_SCHEMA,
        DECISION_V4_JSON_SCHEMA,
    )

    if version == "decision.v4":
        return PromptSpec(
            version, DECISION_V4_JSON_SCHEMA, uses_options=True, builder="v4"
        )
    if version == "decision.v3":
        return PromptSpec(
            version, DECISION_V3_JSON_SCHEMA, uses_options=True, builder="v3"
        )
    if version == "decision.v2":
        return PromptSpec(
            version, DECISION_V2_JSON_SCHEMA, uses_options=True, builder="v2"
        )
    if version == "decision.v1":
        return PromptSpec(version, DECISION_JSON_SCHEMA)
    raise UnknownPromptVersion(version)


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
    """Load ``prompts/<version>.md`` and hash its exact contents.

    The name must match the whitelist and resolve inside ``prompts/``,
    and the file must be listed in the manifest with a matching SHA-256 —
    a tampered or unlisted prompt is refused (``PromptError``).
    """
    if not PROMPT_NAME_RE.fullmatch(version):
        raise PromptError(f"invalid prompt version {version!r}")
    base = PROMPTS_DIR.resolve()
    path = (base / f"{version}.md").resolve()
    if path.parent != base:
        raise PromptError(f"prompt version {version!r} escapes prompts/")
    expected = _manifest().get(f"{version}.md")
    if expected is None:
        raise PromptError(f"prompt {version!r} is not in the manifest")
    text = path.read_text(encoding="utf-8")
    sha = hashlib.sha256(text.encode()).hexdigest()
    if sha != expected:
        raise PromptError(f"prompt {version!r} does not match its manifest hash")
    return Prompt(version=version, text=text, sha256=sha)


@lru_cache(maxsize=8)
def cached_prompt(version: str) -> Prompt:
    """Load once per process; prompt files never change at runtime."""
    return load_prompt(version)
