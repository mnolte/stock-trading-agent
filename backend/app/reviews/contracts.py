"""Review output contracts: one pydantic model per prompt version
(docs/SPEC.md, Reviews — Stories, item 079).

``ReviewService._validate`` parses the model's JSON through the model
registered for the prompt version that produced it; unknown versions
never reach here (``_REVIEW_CONTRACTS`` gates them first).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class WeeklySuggestion(BaseModel):
    """weekly.v2+ suggestion: one change plus the facts behind it."""

    text: str
    evidence: str


class WeeklyV1Output(BaseModel):
    summary: str
    observations: list[str]
    suggestion: str | None = None


class WeeklyV2Output(BaseModel):
    summary: str
    observations: list[str]
    suggestion: WeeklySuggestion | None = None


class WeeklyV3Output(BaseModel):
    """weekly.v3 (item 079): the summary and observations become one
    story of one to three paragraphs; the suggestion stays as v2."""

    story: str
    suggestion: WeeklySuggestion | None = None


class BriefingOutput(BaseModel):
    """briefing.v1–v3 share the {"text"} shape; the version decides the
    word cap and the v3 format check, not the schema."""

    text: str


class VerdictV1Output(BaseModel):
    verdict: Literal["good_call", "bad_call", "unclear"]
    reason: str


class VerdictV2Output(BaseModel):
    """verdict.v2: the label is code's draft_verdict; the model writes
    the reason and the thesis check only."""

    reason: str
    thesis_check: Literal["consistent", "contradicts", "unclear"] | None = None
    contradiction: str | None = None


OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "weekly.v1": WeeklyV1Output,
    "weekly.v2": WeeklyV2Output,
    "weekly.v3": WeeklyV3Output,
    "briefing.v1": BriefingOutput,
    "briefing.v2": BriefingOutput,
    "briefing.v3": BriefingOutput,
    "verdict.v1": VerdictV1Output,
    "verdict.v2": VerdictV2Output,
}


def parse_output(version: str, content: object) -> dict[str, object] | None:
    """The model's JSON as a plain dict when it matches the version's
    contract, else None."""
    model = OUTPUT_MODELS.get(version)
    if model is None or not isinstance(content, dict):
        return None
    try:
        parsed = model.model_validate(content)
    except ValueError:
        return None
    return parsed.model_dump()
