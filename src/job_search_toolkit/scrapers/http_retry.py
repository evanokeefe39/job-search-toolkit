"""Shared HTTP retry-with-backoff for board scrapers.

Several board scrapers (hellowork, freework, englishjobs, faruse, remoteok,
weworkremotely, datasciencejobs, hiringcafe) made a single bare request and
gave up on a transient failure — a 429/5xx (or a mid-scrape rate blip) would
fail that board's scrape. This module centralizes the retry-with-backoff that
builtin.py / wttj.py / linkedin already do inline, so every board shares one
implementation driven by the same ``RunConfig`` knobs (``http_retries`` /
``http_backoff`` / ``http_timeout``).

Design:
- ``request_with_retry(client, method, url, *, retriable, **kwargs)`` dispatches
  to ``client.get`` / ``client.post`` (the semantic method matching ``method``)
  up to ``http_retries + 1`` times.
- A response whose status is in ``retriable`` (or any ``httpx.HTTPError``) is
  retried with exponential backoff ``http_backoff * (attempt + 1)``; the final
  attempt's result is returned (200 or the last non-retriable status) — the
  caller still calls ``raise_for_status()`` / parses as today, so behavior on a
  persistent failure is unchanged (the existing per-board handling applies).
- Returns the final response so each board keeps its own parse shape (text /
  json / XML / elementtree) — this module does not parse.
"""

from __future__ import annotations

import time

import httpx

from job_search_toolkit.run_config import get_run_config

# Statuses that are safe to retry: rate-limited, transient, or a bot-guard
# blip. 403 is included deliberately: several boards (hellowork observed live)
# return a *transient* 403 under concurrent-load / anti-bot rate guarding that
# clears within a backoff window, so retrying it recovers the scrape. A 403
# from a genuinely permanent block or auth failure is bounded by http_retries
# (default 2) and then the caller's raise_for_status() still fails it — it is
# not retried forever. 429 + 5xx mirror builtin/_RETRIABLE and linkedin.
DEFAULT_RETRIABLE = frozenset({403, 429, 500, 502, 503, 504})


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retriable: frozenset[int] = DEFAULT_RETRIABLE,
    **kwargs,
) -> httpx.Response:
    """Issue ``client.get/post(url, **kwargs)`` with retry/backoff.

    Retries retriable statuses and network errors (``httpx.HTTPError``) up to
    ``RunConfig.http_retries`` times, sleeping ``http_backoff * (attempt+1)``
    seconds between attempts. Returns the final response (200 or the last
    non-retriable result); a network error on the last attempt re-raises.
    ``kwargs`` (e.g. ``json=``, ``params=``, ``timeout=``) are forwarded
    verbatim to the method-specific client call; when no ``timeout`` is given
    it defaults to ``RunConfig.http_timeout``.
    """
    cfg = get_run_config()
    if "timeout" not in kwargs:
        kwargs["timeout"] = cfg.http_timeout
    retries = cfg.http_retries
    backoff = cfg.http_backoff
    http_call = getattr(client, method.lower())  # client.get / client.post

    for attempt in range(retries + 1):
        try:
            resp = http_call(url, **kwargs)
        except httpx.HTTPError:
            if attempt >= retries:
                raise
            time.sleep(backoff * (attempt + 1))
            continue
        if resp.status_code in retriable and attempt < retries:
            time.sleep(backoff * (attempt + 1))
            continue
        return resp
    # unreachable: loop always returns on attempt==retries or raises
    raise AssertionError("retry loop exhausted without a result")
