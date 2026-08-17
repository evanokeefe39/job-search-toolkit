"""Pure URL helpers for LinkedIn post/job URLs.

No I/O, no state: every function derives its answer from the URL alone using
``urllib.parse.urlsplit`` so query strings and fragments never leak into
host/path decisions. Hostname case and locale subdomains (``fr.``, ``www.``,
``mu.``, ``ie.``, ``de.``, ``uk.``) are tolerated.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_ACTIVITY_RE = re.compile(r"-activity-(\d+)")
_URN_ACTIVITY_RE = re.compile(r"urn:li:activity:(\d+)")
_POSTS_MARKER = "/posts/"


def normalize_host(url: str) -> str:
    """Return the canonical host of ``url``.

    Pre: ``url`` is an http(s) URL (scheme/host present).
    Post: lowercase, ``www.`` stripped, any ``<sub>.linkedin.com`` collapsed to
    ``linkedin.com``; any other host is returned lowercased as-is.
    """
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "linkedin.com"
    return host


def classify_url(url: str) -> str:
    """Classify a URL as a post, a job listing, or droppable.

    Pre: ``url`` is an http(s) URL.
    Post: returns ``"post"`` for ``linkedin.com/posts/``, ``"job"`` for
    ``linkedin.com/jobs/view/``, and ``"drop"`` for everything else
    (non-LinkedIn hosts, ``/jobs/`` index/search pages, bare paths).
    """
    if normalize_host(url) != "linkedin.com":
        return "drop"
    path = urlsplit(url).path
    if path.startswith("/posts/"):
        return "post"
    if path.startswith("/jobs/view/"):
        return "job"
    if path.startswith("/jobs/"):
        return "drop"
    return "drop"


def post_activity_id(url: str) -> str | None:
    """Extract the numeric LinkedIn activity id from a post URL.

    Pre: ``url`` is an http(s) URL (or a bare ``urn:li:activity:...`` string).
    Post: returns the digits after ``-activity-`` or ``urn:li:activity:``,
    or ``None`` when neither pattern is present.
    """
    match = _ACTIVITY_RE.search(url) or _URN_ACTIVITY_RE.search(url)
    return match.group(1) if match else None


def job_id_from_url(url: str) -> str | None:
    """Extract the trailing numeric job id from a ``/jobs/view/...-<id>`` URL.

    Pre: ``url`` is an http(s) URL.
    Post: returns the digit run after the last ``-`` in the path, or ``None``
    when the path has no trailing digit run.
    """
    path = urlsplit(url).path
    dash = path.rfind("-")
    if dash == -1:
        return None
    tail = path[dash + 1 :]
    return tail if tail.isdigit() else None


def author_slug_from_post_url(url: str) -> str | None:
    """Extract the author vanity slug from a ``/posts/<slug>_...`` URL.

    Pre: ``url`` is an http(s) URL.
    Post: returns the segment between ``/posts/`` and the first ``_``, or
    ``None`` when the path contains no ``/posts/`` marker.
    """
    path = urlsplit(url).path
    start = path.find(_POSTS_MARKER)
    if start == -1:
        return None
    segment = path[start + len(_POSTS_MARKER) :].split("_", 1)[0]
    return segment or None
