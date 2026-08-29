"""Unit tests for company_news batched enrichment join logic.

Tests the input-ordered batch-result contract without network/LLM calls:
- results are joined positionally (input order), not by name lookup
- a cosmetic company-name rename still maps to the right company_id
- a count mismatch (dropped row) raises loudly, not silently nulling
- no-headline companies yield honest inconclusive without an LLM call
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_search_toolkit.pipelines.jd.company_news import (
    CompanyNews,
    enrich_companies,
)


class _FakeResults:
    """Return a fixed CompanyNewsBatch per call."""

    def __init__(self, results: list[dict]):
        self._results = results

    @property
    def results(self) -> list[CompanyNews]:
        return [CompanyNews(**r) for r in self._results]


class _FakeCreate:
    def __init__(self, results: list[dict]):
        self._results = results
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return _FakeResults(self._results)


class _FakeCompletions:
    def __init__(self, results: list[dict]):
        self._create = _FakeCreate(results)

    def create(self, **kwargs):
        return self._create.create(**kwargs)


class _FakeClient:
    def __init__(self, results: list[dict]):
        self.chat = SimpleNamespace(completions=_FakeCompletions(results))


def _patch_client(results: list[dict], monkeypatch,
                 headlines_by_company: dict[str, list[dict]] | None = None):
    """Patch the LLM client AND headline collection for hermetic tests.

    ``headlines_by_company`` maps company name -> its headlines; companies
    absent from it are treated as no-headline. Defaults to giving every
    company a single headline so the LLM path runs.
    """
    fake = _FakeClient(results)

    def _fake_headers(name: str):
        if headlines_by_company is not None:
            return headlines_by_company.get(name, [])
        return [{"title": f"headline about {name}", "source": "test"}]

    monkeypatch.setattr(
        "job_search_toolkit.pipelines.jd.company_news._get_client",
        lambda: fake,
    )
    monkeypatch.setattr(
        "job_search_toolkit.pipelines.jd.company_news.collect_headlines",
        _fake_headers,
    )
    return fake.chat.completions._create


def test_positional_join_maps_in_order(monkeypatch):
    """Results map positionally to the input companies (input-ordered contract)."""
    calls = _patch_client([
        {"company": "GitLab", "sentiment": "negative", "notes": ["n1"]},
        {"company": "Zoox", "sentiment": "negative", "notes": ["n2"]},
    ], monkeypatch)
    out = enrich_companies([
        {"company_id": "c1", "name": "GitLab"},
        {"company_id": "c2", "name": "Zoox"},
    ])
    assert [o["company_id"] for o in out] == ["c1", "c2"]
    assert [o["sentiment"] for o in out] == ["negative", "negative"]


def test_name_rename_falls_back_to_match(monkeypatch):
    """A cosmetic rename in the model output still maps to the right company_id."""
    _patch_client([
        # model returned a slightly different name — must still map to c1
        {"company": "GitLab Inc", "sentiment": "negative", "notes": ["n1"]},
    ], monkeypatch)
    out = enrich_companies([{"company_id": "c1", "name": "GitLab"}])
    assert out[0]["company_id"] == "c1"


def test_count_mismatch_is_loud_and_honest(monkeypatch, caplog):
    """A dropped row is logged loudly and degrades to honest inconclusive.

    The count mismatch must NOT silently null a company_id. It is detected,
    logged, and the batch degrades to honest inconclusive (never fabricates
    a partial result for the dropped row).
    """
    import logging
    _patch_client([
        # only 1 result for a 2-company batch — mismatch detected
        {"company": "GitLab", "sentiment": "negative", "notes": ["n1"]},
    ], monkeypatch)
    with caplog.at_level(logging.WARNING):
        out = enrich_companies([
            {"company_id": "c1", "name": "GitLab"},
            {"company_id": "c2", "name": "Zoox"},
        ])
    # both companies degrade to honest inconclusive (never a nulled cid)
    assert len(out) == 2
    assert all(o["company_id"] for o in out)
    assert all(o["sentiment"] == "inconclusive" for o in out)
    assert any("count" in rec.message for rec in caplog.records)


def test_no_headline_is_inconclusive_without_llm_call(monkeypatch):
    """No-headline companies return inconclusive WITHOUT calling the LLM."""
    calls = _patch_client([], monkeypatch, headlines_by_company={})
    out = enrich_companies([{"company_id": "c1", "name": "Zzz Nonexistent Co"}])
    assert out == [{"company_id": "c1", "company": "Zzz Nonexistent Co",
                    "sentiment": "inconclusive", "notes": []}]
    assert calls.calls == 0  # no LLM call for no-headline companies
