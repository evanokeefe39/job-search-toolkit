"""Unit tests for linkedin.profile — LinkedInProfileScraper (poster locations).

Covers slug-normalization matching and the missing-token guard. ApifyClient is
faked at the module seam; no network. Run: uv run pytest tests/test_profile.py
"""

import pytest

from job_search_toolkit.linkedin import profile as profile_module
from job_search_toolkit.linkedin.profile import (
    LinkedInProfileScraper,
    _normalize_profile_url,
)


class _FakeActor:
    def __init__(self, items):
        self._items = items

    def call(self, run_input):
        return {"defaultDatasetId": "d1", "status": "SUCCEEDED"}


class _FakeDataset:
    def __init__(self, items):
        self._items = items

    def iterate_items(self):
        return iter(self._items)


class _FakeProfileClient:
    def __init__(self, items):
        self._items = items

    def actor(self, _actor_id):
        return _FakeActor(self._items)

    def dataset(self, _dataset_id):
        return _FakeDataset(self._items)


def test_normalize_profile_url_canonicalizes():
    assert _normalize_profile_url("https://www.linkedin.com/in/chris-french-data") == (
        "linkedin.com/in/chris-french-data"
    )
    assert _normalize_profile_url("https://linkedin.com/in/chris-french-data/") == (
        "linkedin.com/in/chris-french-data"
    )


def test_normalize_profile_url_country_subdomain_and_decode():
    assert _normalize_profile_url(
        "https://ch.linkedin.com/in/st%C3%A9phanie-caloz-674482141"
    ) == "linkedin.com/in/stéphanie-caloz-674482141"
    assert _normalize_profile_url(
        "https://www.linkedin.com/in/stéphanie-caloz-674482141"
    ) == "linkedin.com/in/stéphanie-caloz-674482141"
    assert _normalize_profile_url("https://de.linkedin.com/in/basdohmen/") == (
        "linkedin.com/in/basdohmen"
    )


def test_scrape_locations_maps_slugs(monkeypatch):
    items = [
        {"username": "chris-french-data", "location": "Greater Scranton Area"},
        {"username": "balazs-szanto", "location": "Szolnok, Hungary"},
    ]
    monkeypatch.setattr(
        profile_module, "ApifyClient", lambda token: _FakeProfileClient(items)
    )
    scraper = LinkedInProfileScraper(token="test", actor_id="x")
    out = scraper.scrape_locations(
        [
            "https://www.linkedin.com/in/chris-french-data",
            "https://www.linkedin.com/in/balazs-szanto",
            "https://www.linkedin.com/in/not-there",
        ]
    )
    assert out["https://www.linkedin.com/in/chris-french-data"] == "Greater Scranton Area"
    assert out["https://www.linkedin.com/in/balazs-szanto"] == "Szolnok, Hungary"
    assert out["https://www.linkedin.com/in/not-there"] is None


def test_scrape_locations_empty_input_no_call(monkeypatch):
    called = {"n": 0}

    def fake_client(token):
        called["n"] += 1
        return _FakeProfileClient([])

    monkeypatch.setattr(profile_module, "ApifyClient", fake_client)
    scraper = LinkedInProfileScraper(token="test", actor_id="x")
    assert scraper.scrape_locations([]) == {}
    assert called["n"] == 0


def test_scrape_locations_missing_token_raises(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="token"):
        LinkedInProfileScraper(token=None, actor_id="x")
