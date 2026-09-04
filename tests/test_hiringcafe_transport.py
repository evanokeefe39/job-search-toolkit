"""HiringCafe client tests for the curl_cffi Chrome-impersonation transport.

The scraper sits behind a Cloudflare managed JS challenge that grades TLS
fingerprints; plain httpx is flagged (403). The transport is a curl_cffi
Session with ``impersonate="chrome"``. These tests verify the transport
configuration without a live network call:

- the client uses a curl_cffi Session impersonating Chrome (not plain httpx),
- ``_request`` merges the module ``HEADERS`` with per-call extras and honors the
  RunConfig http_timeout,
- a non-200 status raises (via ``raise_for_status``), a 200 passes through.

Run: uv run python -m pytest tests/test_hiringcafe_transport.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from job_search_toolkit.scrapers import hiringcafe as hc  # noqa: E402


class _FakeResp:
    """Minimal curl_cffi-like response exposing status_code / raise_for_status."""

    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            from curl_cffi import requests as creq

            raise creq.RequestsError(f"HTTP {self.status_code}")


class _FakeSession:
    """Records request() args; returns a scripted response sequence."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        self.calls.append({
            "method": method, "url": url, "headers": headers, "timeout": timeout,
        })
        resp = self.responses.pop(0)
        return resp

    def close(self):
        pass


def test_client_uses_curl_cffi_chrome_impersonation(monkeypatch):
    """The client's transport must be a curl_cffi Session impersonating Chrome,
    not plain httpx (which Cloudflare 403s on hiringcafe)."""
    created = {}

    class FakeCurlSession:
        def __init__(self, impersonate=None):
            created["impersonate"] = impersonate

        def request(self, *a, **k):
            raise AssertionError("unused")

        def close(self):
            pass

    monkeypatch.setattr(hc.curl_requests, "Session", FakeCurlSession)
    client = hc.HiringCafeClient(delay=0)
    assert created.get("impersonate") == "chrome"
    client.close()


def test_request_merges_headers_and_timeout(monkeypatch):
    """_request merges module HEADERS with per-call extras and passes the
    RunConfig http_timeout to the session."""
    fake = _FakeSession([_FakeResp(200, text="<html><body>home</body></html>")])

    client = hc.HiringCafeClient(delay=0)
    client.client = fake  # inject fake session (avoids real impersonation)

    resp = client._request("GET", "https://hiringcafe.com/", extra_headers={"x-nextjs-data": "1"})
    assert resp.text == "<html><body>home</body></html>"

    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://hiringcafe.com/"
    # merged headers: module HEADERS base + the extra header
    assert call["headers"]["x-nextjs-data"] == "1"
    assert call["headers"]["User-Agent"] == hc.HEADERS["User-Agent"]
    # timeout comes from RunConfig
    assert call["timeout"] == hc.get_run_config().http_timeout


def test_request_raises_on_non_200(monkeypatch):
    """A non-200 response propagates via raise_for_status (no silent swallow)."""
    fake = _FakeSession([_FakeResp(403, text="Just a moment...")])
    client = hc.HiringCafeClient(delay=0)
    client.client = fake

    from curl_cffi import requests as creq

    with pytest.raises(creq.RequestsError):
        client._request("GET", "https://hiringcafe.com/")
