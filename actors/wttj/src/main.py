"""
Standalone Apify actor: Welcome to the Jungle (WTTJ) France job offers.

Route (crawler-sanctioned, plain fetch — no proxy needed):
  1. https://www.welcometothejungle.com/sitemaps/index.xml.gz
  2. -> job-listings.{0..8}.xml.gz (France offers contain `/fr/companies/`)
Offer pages block plain fetches with HTTP 202; each offer page is fetched
through Apify's RESIDENTIAL proxy to bypass the rate limit.

Raw records match `job_search_toolkit.pipelines.jd.adapt_wttj.normalize_wttj_job`
(keys: url, title, company, description, date_posted, employment_type,
location_raw, salary_min/max/currency/unit, content_quality, ...).
"""
import asyncio
import gzip
import json
import logging

import httpx
from apify import Actor
from crawlee.crawlers import HttpCrawler, HttpCrawlingContext

from .parse_helpers import france_job_urls, parse_offer, parse_sitemap
SITEMAP_INDEX_URL = "https://www.welcometothejungle.com/sitemaps/index.xml.gz"

logger = logging.getLogger(__name__)

# Politeness delay between offer-page requests (residential bursts get rate-limited).
_POLITENESS_DELAY = 1.0


async def _get_gzipped(client: httpx.AsyncClient, url: str) -> bytes | None:
    """GET one sitemap URL and gunzip it. None on failure."""
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning("Sitemap fetch failed for %s: HTTP %s", url, resp.status_code)
            return None
        data = resp.content
    except httpx.HTTPError as exc:
        logger.warning("Sitemap fetch error for %s: %s", url, exc)
        return None
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


async def discover_france_urls(max_items: int) -> list[str]:
    """Enumerate up to `max_items` France offer URLs from the sitemap route."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        index_data = await _get_gzipped(client, SITEMAP_INDEX_URL)
        if not index_data:
            logger.warning("Sitemap index unavailable; no WTTJ URLs discovered.")
            return []
        children = [u for u in parse_sitemap(index_data) if "job-listings." in u]
        urls: list[str] = []
        for child in children:
            if len(urls) >= max_items:
                break
            child_data = await _get_gzipped(client, child)
            if not child_data:
                continue
            urls.extend(france_job_urls(parse_sitemap(child_data)))
    return urls[:max_items]


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        max_items = int(actor_input.get("maxItems") or 200)
        location = str(actor_input.get("location") or "France")
        if location != "France":
            raise RuntimeError(
                f"Only 'France' is implemented; got location={location!r}"
            )

        # Sitemaps are crawler-sanctioned and work without a proxy.
        urls = await discover_france_urls(max_items)
        Actor.log.info("Discovered %d France offer URLs from sitemaps.", len(urls))
        if not urls:
            return

        proxy_cfg = await Actor.create_proxy_configuration(
            groups=["RESIDENTIAL"],
        )
        if proxy_cfg is None:
            Actor.log.error(
                "RESIDENTIAL proxy configuration unavailable; cannot bypass WTTJ's HTTP-202 rate limit."
            )
            raise RuntimeError("Failed to create RESIDENTIAL proxy configuration")

        pushed_count = 0

        crawler = HttpCrawler(
            proxy_configuration=proxy_cfg,
            max_request_retries=3,
        )

        @crawler.router.default_handler
        async def handle_offer(context: HttpCrawlingContext) -> None:
            nonlocal pushed_count
            if pushed_count >= max_items:
                return
            url = context.request.url
            await asyncio.sleep(_POLITENESS_DELAY)

            response = context.http_response
            status = response.status_code
            if status == 202:
                # WTTJ's rate limit: let Crawlee retry this URL.
                raise RuntimeError(f"WTTJ returned HTTP 202 (rate limit) for {url}")
            if status != 200:
                raise RuntimeError(f"WTTJ returned HTTP {status} for {url}")

            html_text = (await response.read()).decode("utf-8", errors="replace")
            record = parse_offer(html_text, url)
            await Actor.push_data(record)
            pushed_count += 1
            Actor.log.info("[%d/%d] %s | %s | %s",
                           pushed_count, max_items,
                           record.get("title"), record.get("company"),
                           record.get("location_raw"))

        await crawler.run(urls)


if __name__ == "__main__":
    asyncio.run(main())
