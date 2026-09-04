"""Behavioral regression: run-selection knobs reach scraper HTTP calls.

Guard against the config run-selection footgun: named run configs used to be
frozen at import time (module-level ``_CFG = load_run_config()``), so the
``RUN_CONFIG`` env var set at pipeline runtime never reached most knobs.

The fix moves each knob to ``get_run_config().<field>`` at point of use. Here
we monkeypatch the scraper's ``get_run_config`` to return a distinctive
``RunConfig`` and assert those values reach the HTTP layer.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from job_search_toolkit.run_config import RunConfig
from job_search_toolkit.scrapers import freework


def _cfg(**overrides) -> RunConfig:
    return dataclasses.replace(RunConfig(), **overrides)


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    """Records every ``get`` call (with kwargs) and serves an empty board."""

    def __init__(self, *args, **kwargs) -> None:
        self.gets: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.gets.append((url, kwargs))
        return _FakeResponse(
            "<html><body><div class='pagination'>1 / 1</div></body></html>"
        )

    def close(self) -> None:
        return None


def test_freework_http_timeout_and_radius_reach_http_call(monkeypatch, tmp_path):
    """A named run's http_timeout + freework_radius must reach the HTTP call.

    With ``get_run_config`` patched to http_timeout=42.0 / freework_radius=99,
    ``scrape``'s point-of-use timeout must be 42.0 and the search URL must be
    built with radius=99.
    """
    fake = _FakeClient()
    monkeypatch.setattr(
        freework,
        "get_run_config",
        lambda: _cfg(http_timeout=42.0, freework_radius=99),
    )
    monkeypatch.setattr(freework.httpx, "Client", lambda *a, **k: fake)

    url = freework.build_url(
        "data engineer",
        freework.DEFAULT_LOCATIONS,
        freework.DEFAULT_CONTRACTS,
        freework.DEFAULT_REMOTE,
        freework.DEFAULT_EXPERIENCE,
        freework.DEFAULT_SORT,
        freework.get_run_config().freework_radius,
    )

    out = tmp_path / "fw.json"
    freework.scrape(url, out, max_pages=1, fmt="json")

    # Board knob: radius=99 flowed from get_run_config into the search URL.
    assert "radius=99" in url
    # http_timeout reached the HTTP layer on every request.
    assert fake.gets, "scrape must issue at least one HTTP request"
    for _u, kwargs in fake.gets:
        assert kwargs.get("timeout") == 42.0
    # Sanity: the run still produced output without touching the network.
    assert out.exists()


def test_faruse_page_size_and_timeout_reach_http_call(monkeypatch, tmp_path):
    """A named run's faruse_page_size + http_timeout must reach the HTTP call.

    The faruse scraper previously froze ``PAGE_SIZE = _CFG.faruse_page_size``
    and ``_HTTP_TIMEOUT`` at import time; both must now be read at point of use.
    """
    from job_search_toolkit.scrapers import faruse

    monkeypatch.setattr(
        faruse,
        "get_run_config",
        lambda: _cfg(faruse_page_size=7, http_timeout=42.0),
    )

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.posts: list[tuple[str, dict, dict]] = []

        def post(self, url, json=None, **kwargs):
            self.posts.append((url, json, kwargs))
            return _Resp()

        def close(self):
            return None

    fake = _FakeClient()
    monkeypatch.setattr(faruse.httpx, "Client", lambda *a, **k: fake)

    out = tmp_path / "faruse.json"
    faruse.scrape(
        "https://www.faruse.com/functions/v1/search-jobs",
        out,
        max_pages=1,
        fmt="json",
        query="data engineer",
        location="france",
    )

    assert fake.posts, "scrape must issue at least one POST"
    _url, body, kwargs = fake.posts[0]
    # pageSize flowed from get_run_config into the POST body at point of use.
    assert body["pageSize"] == 7
    # http_timeout reached the HTTP layer.
    assert kwargs.get("timeout") == 42.0
    assert out.exists()
