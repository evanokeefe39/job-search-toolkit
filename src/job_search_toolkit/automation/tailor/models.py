"""Pydantic response models for resume tailoring."""

from pydantic import BaseModel, Field


class HighlightsEntry(BaseModel):
    index: int = Field(ge=0)
    highlights: list[str] = Field(default_factory=list)
    # NOTE: cross-company merging (merged_into) was removed 2026-08-10 —
    # folding one employer's bullets under another's header is a
    # fabrication-adjacent mislabel the audit cannot catch. Low-value
    # handling is cut-only: an EMPTY highlights list drops the role.


class SkillEntry(BaseModel):
    label: str = Field(min_length=1)
    details: str = Field(min_length=1)


class TailorResponse(BaseModel):
    summary: str = Field(min_length=50, max_length=1000)
    experiences: list[HighlightsEntry] = Field(min_length=1)
    skills: list[SkillEntry] = Field(min_length=1)


class ReviewerResponse(BaseModel):
    """Adversarial reviewer output: one critique + at most one targeted revision."""

    critique: str = Field(min_length=10)
    revision: TailorResponse | None = None
