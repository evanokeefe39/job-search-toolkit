"""LinkedIn profile-location scraper via the Apify data-slayer actor.

This is the enrichment-only path that fills a recruiter/poster's location
(``poster_location``) on ``linkedin_posts`` silver rows. It is deliberately
separate from the ranking path — the post adapter emits ``poster_name`` and
``poster_url`` from the post itself, while ``poster_location`` is only
populated here, on ``pipeline run --enrich``.

Backend: the official ``apify-client`` SDK calls the
``data-slayer/linkedin-profile-scraper`` actor (``~$0.004``/profile,
pay-per-result). No raw httpx REST is used against Apify.

Mapping rule: each input profile URL is normalized (strip scheme/www,
strip trailing slash, lowercase) and compared against each dataset item's
``username`` / ``profile_link`` slug. A URL that matches no item maps to
``None`` (private profile, or the actor returned nothing for it).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from urllib.parse import unquote, urlsplit

from apify_client import ApifyClient
from apify_client.errors import ApifyApiError

# Env var override for the profile actor id (mirrors discovery.py's
# APIFY_ACTOR_ID pattern); verified in the Apify account.
_DEFAULT_ACTOR_ID = "data-slayer/linkedin-profile-scraper"


def _run_info_dict(run: object) -> dict:
    """Coerce an apify-client ``Run`` (pydantic model) or plain dict to a dict.

    ``actor().call`` returns a ``Run`` model (not subscriptable);
    ``model_dump(by_alias=True)`` yields the API camelCase keys (e.g.
    ``defaultDatasetId``). Plain dicts pass through unchanged.
    """
    if hasattr(run, "model_dump"):
        return run.model_dump(by_alias=True)  # type: ignore[attr-defined]
    return dict(run)


def _normalize_profile_url(url: str) -> str:
    """Normalize a LinkedIn profile URL to its canonical lowercase slug.

    Strips the scheme, collapses any ``<sub>.linkedin.com`` host (``www.`` or a
    country subdomain like ``fr.``/``de.``) to ``linkedin.com``, URL-decodes the
    path, drops a trailing ``/``, and lowercases — so a profile URL scraped back
    from the actor matches the one submitted even with a country subdomain or
    percent-encoded characters (e.g. ``ch.linkedin.com/in/st%C3%A9phanie-x`` vs
    ``www.linkedin.com/in/stéphanie-x``).
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(".linkedin.com"):
        host = "linkedin.com"
    path = unquote(parts.path).rstrip("/").lower()
    return f"{host}{path}"


class LinkedInProfileScraper:
    """Scrape LinkedIn profile locations for recruiter/post authors.

    Pre: ``token``, ``APIFY_TOKEN``, or ``APIFY_API_TOKEN`` must be set, else
    RuntimeError. ``actor_id`` defaults to the data-slayer profile actor (or
    ``APIFY_PROFILE_ACTOR_ID``).
    Post: ``scrape_locations`` maps each input profile URL to the profile's
    ``location`` string, or None when the profile is private / unmatched.
    """

    def __init__(
        self,
        token: str | None = None,
        actor_id: str | None = None,
        *,
        timeout: float = 180.0,
    ) -> None:
        self.token = (
            token
            or os.environ.get("APIFY_TOKEN")
            or os.environ.get("APIFY_API_TOKEN")
        )
        if not self.token:
            raise RuntimeError(
                "Apify token not set: pass token= or export APIFY_TOKEN / APIFY_API_TOKEN"
            )
        self.actor_id = actor_id or os.environ.get(
            "APIFY_PROFILE_ACTOR_ID", _DEFAULT_ACTOR_ID
        )
        self.timeout = timeout

    def scrape_locations(
        self, profile_urls: Sequence[str]
    ) -> dict[str, str | None]:
        """Map each input profile URL to its scraped location (or None).

        Pre: ``profile_urls`` is a sequence of LinkedIn profile URLs.
        Post: returns a dict keyed by the *original* URL string (not the
        normalized form), mapping to the profile's ``location`` or None. Raises
        RuntimeError when the actor run is None or an Apify API error occurs.
        """
        urls = [u for u in profile_urls if u]
        if not urls:
            return {}

        client = ApifyClient(self.token)
        try:
            run = client.actor(self.actor_id).call(
                run_input={"linkedin_urls": list(urls)},
            )
        except ApifyApiError as exc:
            raise RuntimeError(f"Apify profile actor failed: {exc}") from exc

        if not run:
            raise RuntimeError("Apify profile actor returned no run")
        dataset_id = _run_info_dict(run).get("defaultDatasetId")
        if not dataset_id:
            raise RuntimeError(
                "Apify profile actor run had no defaultDatasetId "
                f"(run status: {run.get('status')})"
            )

        # Build slug -> location from the dataset items (username or
        # profile_link). Keys are normalized so both sides compare the slug.
        locations_by_slug: dict[str, str] = {}
        try:
            for item in client.dataset(dataset_id).iterate_items():
                location = item.get("location")
                if not location:
                    continue
                # Prefer the full profile_link; fall back to building it from
                # the username slug so normalization matches the input URLs.
                link = item.get("profile_link") or (
                    f"https://www.linkedin.com/in/{item['username']}"
                    if item.get("username")
                    else None
                )
                if not link:
                    continue
                locations_by_slug[_normalize_profile_url(str(link))] = str(location)
        except ApifyApiError as exc:
            raise RuntimeError(f"Apify profile dataset failed: {exc}") from exc

        # Map each original input URL to its scraped location, or None.
        return {
            url: locations_by_slug.get(_normalize_profile_url(url))
            for url in urls
        }
