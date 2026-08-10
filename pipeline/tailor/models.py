"""Pydantic response models for resume tailoring."""

from pydantic import BaseModel, Field


class HighlightsEntry(BaseModel):
    index: int = Field(ge=0)
    highlights: list[str] = Field(min_length=1)


class SkillEntry(BaseModel):
    label: str = Field(min_length=1)
    details: str = Field(min_length=1)


class TailorResponse(BaseModel):
    summary: str = Field(min_length=50, max_length=1000)
    experiences: list[HighlightsEntry] = Field(min_length=1)
    skills: list[SkillEntry] = Field(min_length=1)
