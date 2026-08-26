"""Direct anonymous fetch of LinkedIn post/job pages (never profiles).

Polite by construction: a browser User-Agent, a bounded timeout, no retry on
client errors, and exponential backoff on rate limits and server errors.
"""

from __future__ import annotations

import time

import httpx

from job_search_toolkit.run_config import load_run_config as _load_run_config

# Browser-like headers so LinkedIn serves the public (non-login-wall) variant.
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}

# Statuses that mean "the resource is gone" — never retry these.
_GONE = frozenset({404, 410})
# Statuses worth retrying: rate-limited or server-side transient failures.
_RETRYABLE = frozenset({429}) | set(range(500, 600))

# Tunable fetch knobs (timeout/retries/backoff) come from RunConfig; the
# status sets above stay static.
_CFG = _load_run_config()


class FetchError(Exception):
    """Raised when a page fetch fails.

    Attributes:
        status_code: HTTP status of the failing response, or ``None`` when the
            request never reached a response (e.g. transport error).
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def fetch_page(
    url: str,
    client: httpx.Client | None = None,
    *,
    retries: int | None = None,
    backoff: float | None = None,
) -> str:
    """Fetch a LinkedIn post/job page and return its body text.

    Pre: ``url`` is an http(s) URL. When ``client`` is ``None`` a new
    ``httpx.Client(follow_redirects=True, timeout=http_timeout)`` is created
    (and closed) for this call; a provided client is used as-is and left open.
    ``retries``/``backoff``/``timeout`` default from RunConfig
    (``http_retries``/``http_backoff``/``http_timeout``) when not passed.
    Post: returns the response body on HTTP 200; raises ``FetchError`` with
    ``.status_code`` set. 404/410 raise immediately; 429/5xx are retried up to
    ``retries`` times, sleeping ``backoff * attempt`` seconds between tries and
    then raising with the last status; any other non-200 raises immediately.
    """
    r = _CFG.http_retries if retries is None else retries
    b = _CFG.http_backoff if backoff is None else backoff
    owns_client = client is None
    if owns_client:
        client = httpx.Client(follow_redirects=True, timeout=_CFG.http_timeout)
    try:
        for attempt in range(r + 1):
            response = client.get(url, headers=DEFAULT_HEADERS)
            if response.status_code == 200:
                return response.text
            if response.status_code in _GONE:
                raise FetchError(
                    f"resource gone ({response.status_code}): {url}",
                    status_code=response.status_code,
                )
            if response.status_code in _RETRYABLE:
                if attempt < r:
                    time.sleep(b * (attempt + 1))
                    continue
                raise FetchError(
                    f"fetch failed after {r} retries ({response.status_code}): {url}",
                    status_code=response.status_code,
                )
            raise FetchError(
                f"unexpected status {response.status_code}: {url}",
                status_code=response.status_code,
            )
    finally:
        if owns_client:
            client.close()
    # Unreachable: every loop iteration either returns or raises.
    raise AssertionError("unreachable")
