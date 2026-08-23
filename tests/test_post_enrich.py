"""Unit tests for job_search_toolkit.pipelines.jd.post_enrich_canonical.

Verifies the LLM gap-fill for queued LinkedIn posts: one prompt per queued
post, title/location filled from the LLM result, genuinely-unfillable rows
set to the "unknown" sentinel (never fabricated), and the post text carried
into the prompt. All
tests use a fake LLM client — no network.
"""

import asyncio

from job_search_toolkit.pipelines.jd.post_enrich_canonical import (
    POST_ENRICH_SYSTEM,
    _build_prompt,
    enrich_posts,
)
from job_search_toolkit.pipelines.jd.silver import GATE_POST_ENRICH


class FakeLLMClient:
    """In-memory stand-in for LLMClient capturing prompts and returning canned JSON."""

    def __init__(self, results: list[dict]) -> None:
        self.results = results
        self.prompts: list[str] = []

    async def batch_complete_json(
        self,
        prompts: list[str],
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> list[dict]:
        self.prompts = list(prompts)
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        return self.results


def _queued_post(text: str = "Hiring a Senior Data Engineer in Paris") -> dict:
    """A queued linkedin_posts row (title/location empty, ready for LLM)."""
    return {
        "id": "https://linkedin.com/posts/1",
        "source_board": "linkedin_posts",
        "title": "",
        "location_raw": "",
        "description_text": text,
        "company": "Acme Recruiting",
        "role_category": None,
        "seniority_level": None,
        "engagement_type": "unknown",
        "end_client_name": None,
        "end_client_sector": None,
        "competencies": [],
        "_enrichment": {},
    }


def test_gate_selects_empty_title_or_location():
    # The gate is the empty-based selector — both title-empty and
    # location-empty rows must match (the query the asset runs).
    assert "source_board = 'linkedin_posts'" in GATE_POST_ENRICH
    assert "(title = '' OR location_raw = '')" in GATE_POST_ENRICH


def test_prompt_includes_description_text():
    row = _queued_post("We're looking for a Senior Data Engineer in Berlin")
    prompt = _build_prompt(row)
    assert "We're looking for a Senior Data Engineer in Berlin" in prompt
    assert "Acme Recruiting" in prompt


def test_fillable_rows_get_title_and_location():
    row = _queued_post()
    client = FakeLLMClient([{
        "title": "Senior Data Engineer",
        "location_raw": "Paris",
        "role_category": "data_engineer",
        "seniority_level": "senior",
        "engagement_type": "consulting",
        "end_client_name": "Acme Bank",
        "end_client_sector": "fintech",
        "competencies": ["data modeling", "stakeholder management"],
    }])

    asyncio.run(enrich_posts([row], client))

    assert row["title"] == "Senior Data Engineer"
    assert row["location_raw"] == "Paris"
    assert row["role_category"] == "data_engineer"
    assert row["seniority_level"] == "senior"
    assert row["engagement_type"] == "consulting"
    assert row["end_client_name"] == "Acme Bank"
    assert row["end_client_sector"] == "fintech"
    assert row["competencies"] == ["data modeling", "stakeholder management"]
    assert row["_enrichment"]["post_enriched"] is True


def test_unfillable_rows_use_unknown_sentinel_and_not_fabricated():
    # The post genuinely has no role/location signal; the LLM returns empty
    # title/location. enrich_posts must not invent values — it sets a terminal
    # "unknown" sentinel so the empty-based gate never re-selects the row.
    row = _queued_post("Great lunch with the team today, the weather is lovely")
    client = FakeLLMClient([{
        "title": "",
        "location_raw": "",
        "role_category": None,
        "seniority_level": None,
        "engagement_type": None,
        "end_client_name": None,
        "end_client_sector": None,
        "competencies": [],
    }])

    asyncio.run(enrich_posts([row], client))

    assert row["title"] == "unknown"
    assert row["location_raw"] == "unknown"
    assert row["competencies"] == []
    # engagement_type "stays" — never overwritten with a fabricated value.
    assert row["engagement_type"] == "unknown"
    assert row["_enrichment"]["post_enriched"] is True


def test_mixed_batch_parallel_results():
    fillable = _queued_post("Hiring a Data Engineer in Lyon")
    unfillable = _queued_post("Nice view from the rooftop")
    client = FakeLLMClient([
        {"title": "Data Engineer", "location_raw": "Lyon", "competencies": []},
        {"title": "", "location_raw": "", "competencies": []},
    ])

    asyncio.run(enrich_posts([fillable, unfillable], client))

    assert fillable["title"] == "Data Engineer"
    assert fillable["location_raw"] == "Lyon"
    assert unfillable["title"] == "unknown"
    assert unfillable["location_raw"] == "unknown"
    # One prompt per queued row, in order.
    assert len(client.prompts) == 2


def test_uses_batch_complete_json_with_post_text():
    row = _queued_post("Hiring a Staff ML Engineer in Toulouse")
    client = FakeLLMClient([{"title": "Staff ML Engineer", "location_raw": "Toulouse"}])

    asyncio.run(enrich_posts([row], client))

    assert client.system == POST_ENRICH_SYSTEM
    assert len(client.prompts) == 1
    assert "Hiring a Staff ML Engineer in Toulouse" in client.prompts[0]
    assert client.temperature == 0.2


def test_empty_rows_noop():
    client = FakeLLMClient([{"title": "x"}])
    asyncio.run(enrich_posts([], client))
    assert client.prompts == []
