"""Tests for the shared HTTP retry-with-backoff helper + its board wiring.

Covers the retry helper (``scrapers/http_retry.request_with_retry``):
- retries a retriable status (429/5xx) with exponential backoff then succeeds,
- retries on a network error (httpx.HTTPError) then succeeds,
- after exhausting retries returns the last response (caller raises), and
- does NOT retry a 200 (single attempt) or a non-retriable status.

Plus board wiring: the retry-less listing fetches now route through the helper
(hellowork, freework, englishjobs, remoteok, datasciencejobs, weworkremotely,
faruse), so a transient 429/5xx on a listing page is recovered instead of
failing that board's scrape.

Run: uv run python -m pytest tests/test_http_retry.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from job_search_toolkit.scrapers.http_retry import (  # noqa: E402
    DEFAULT_RETRIABLE,
    request_with_retry,
)


class _StubResp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://x/"),
                response=httpx.Response(
                    self.status_code, request=httpx.Request("GET", "https://x/")
                ),
            )


class _StubClient:
    """Records get/post calls; returns scripted responses / raises scripted errors."""

    def __init__(self, responses):
        self._responses = list(responses)  # each: _StubResp, or a callable raising
        self.calls = []

    def get(self, url, **kwargs):
        return self._dispatch("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._dispatch("POST", url, kwargs)

    def _dispatch(self, method, url, kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        step = self._responses.pop(0)
        if callable(step):
            step()
        return step if not callable(step) else _StubResp(200)


@pytest.fixture
def cfg(monkeypatch):
    """Point the helper at deterministic RunConfig retry knobs (2 retries)."""

    class FakeRC:
        http_retries = 2
        http_backoff = 0.01
        http_timeout = 5.0

    import job_search_toolkit.scrapers.http_retry as hr
    monkeypatch.setattr(hr, "get_run_config", lambda: FakeRC())
    return FakeRC


def test_403_is_in_default_retriable_set():
    """403 is deliberately retriable (transient bot-guard blips on hellowork and
    peers); guard against a future removal that would reintroduce the bug."""
    assert 403 in DEFAULT_RETRIABLE


def test_retries_403_then_succeeds(cfg, monkeypatch):
    """403 then 200 -> retried with one backoff sleep, then returns the 200."""
    import job_search_toolkit.scrapers.http_retry as hr
    sleeps = []
    monkeypatch.setattr(hr.time, "sleep", lambda s: sleeps.append(s))
    client = _StubClient([_StubResp(403), _StubResp(200)])
    resp = request_with_retry(client, "GET", "https://x/")
    assert resp.status_code == 200
    assert len(client.calls) == 2
    assert len(sleeps) == 1


def test_persistent_403_is_bounded_not_retried_forever(cfg, monkeypatch):
    """A genuinely permanent 403 is bounded by http_retries (not infinite),
    and the final 403 is returned so the caller's raise_for_status still fails."""
    import job_search_toolkit.scrapers.http_retry as hr
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    client = _StubClient([_StubResp(403), _StubResp(403), _StubResp(403)])
    resp = request_with_retry(client, "GET", "https://x/")
    assert resp.status_code == 403
    assert len(client.calls) == 3  # retries(2) + initial(1)


def test_retries_retriable_status_then_succeeds(cfg, monkeypatch):
    """429 then 200 -> retried with one backoff sleep, then returns the 200."""
    import job_search_toolkit.scrapers.http_retry as hr
    sleeps = []
    monkeypatch.setattr(hr.time, "sleep", lambda s: sleeps.append(s))
    client = _StubClient([_StubResp(429), _StubResp(200)])
    resp = request_with_retry(client, "GET", "https://x/")
    assert resp.status_code == 200
    assert len(client.calls) == 2
    assert len(sleeps) == 1  # one backoff before the retry
    assert sleeps[0] == pytest.approx(cfg.http_backoff)


def test_retries_on_network_error_then_succeeds(cfg, monkeypatch):
    import job_search_toolkit.scrapers.http_retry as hr
    sleeps = []
    monkeypatch.setattr(hr.time, "sleep", lambda s: sleeps.append(s))

    def raise_conn():
        raise httpx.ConnectError("boom")

    client = _StubClient([raise_conn, _StubResp(200)])
    resp = request_with_retry(client, "GET", "https://x/")
    assert resp.status_code == 200
    assert len(client.calls) == 2


def test_returns_last_response_after_exhausting_retries(cfg, monkeypatch):
    """503 every time -> all retries exhausted, returns the last 503 (no raise)."""
    import job_search_toolkit.scrapers.http_retry as hr
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    client = _StubClient([_StubResp(503), _StubResp(503), _StubResp(503)])
    resp = request_with_retry(client, "GET", "https://x/")
    assert resp.status_code == 503
    assert len(client.calls) == 3  # retries(2) + initial(1)


def test_no_retry_on_200(cfg, monkeypatch):
    import job_search_toolkit.scrapers.http_retry as hr
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    client = _StubClient([_StubResp(200)])
    resp = request_with_retry(client, "GET", "https://x/")
    assert resp.status_code == 200
    assert len(client.calls) == 1


def test_network_error_last_attempt_reraises(cfg, monkeypatch):
    import job_search_toolkit.scrapers.http_retry as hr
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)

    def raise_conn():
        raise httpx.ConnectError("boom")

    client = _StubClient([raise_conn, raise_conn, raise_conn])
    with pytest.raises(httpx.ConnectError):
        request_with_retry(client, "GET", "https://x/")


# --- board wiring ------------------------------------------------------------

def test_all_retry_less_boards_now_use_helper():
    """The 7 previously-retry-less boards' listing fetches route through the
    shared request_with_retry helper."""
    import inspect

    from job_search_toolkit.scrapers import (
        datasciencejobs, englishjobs, faruse, freework, hellowork, remoteok,
        weworkremotely,
    )
    wired = {
        name for name, mod in [
            ("hellowork", hellowork), ("freework", freework),
            ("englishjobs", englishjobs), ("remoteok", remoteok),
            ("datasciencejobs", datasciencejobs), ("weworkremotely", weworkremotely),
            ("faruse", faruse),
        ] if "request_with_retry" in inspect.getsource(mod)
    }
    assert {"hellowork", "freework", "englishjobs", "remoteok",
            "datasciencejobs", "weworkremotely", "faruse"} <= wired


def test_hellowork_fetch_page_retries_real_403(monkeypatch):
    """The source that actually tripped: hellowork's listing fetch hit a real
    transient HTTP 403 (observed at p=23 under full-run load). With 403 now in
    DEFAULT_RETRIABLE, the retry must recover it (not fail the scrape)."""
    from job_search_toolkit.scrapers import hellowork as hw

    class FakeCfg:
        http_retries = 1
        http_backoff = 0.0
        http_timeout = 5.0

    monkeypatch.setattr(hw, "get_run_config", lambda: FakeCfg())
    import job_search_toolkit.scrapers.http_retry as hr
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)

    class Resp:
        def __init__(self, status, text=""):
            self.status_code = status
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "e", request=httpx.Request("GET", "https://x/"),
                    response=httpx.Response(
                        self.status_code, request=httpx.Request("GET", "https://x/")
                    ))

    class Client:
        def __init__(self):
            self.n = 0

        def get(self, url, **kwargs):
            self.n += 1
            # first call is the real transient 403 (the observed trip), then 200
            return Resp(403) if self.n == 1 else Resp(200, text="<html>jobs</html>")

    c = Client()
    text = hw.fetch_page(c, "https://www.hellowork.com/...", 23)
    assert c.n == 2  # retried once after the transient 403
    assert "<html>jobs</html>" in text
