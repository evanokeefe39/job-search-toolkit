"""
Pure WTTJ parsing helpers, copied verbatim from
``job_search_toolkit/scrapers/wttj.py`` (the actor runs standalone on Apify
cloud and cannot import the repo package). Raw record dicts match
``job_search_toolkit.pipelines.jd.adapt_wttj.normalize_wttj_job``.
"""

from __future__ import annotations

import gzip
import json
from xml.etree import ElementTree

from bs4 import BeautifulSoup

BASE_URL = "https://www.welcometothejungle.com"
SITEMAP_INDEX_URL = f"{BASE_URL}/sitemaps/index.xml.gz"

# Offer URL shape: /fr/companies/<company>/jobs/<slug>[_<location>]
FRANCE_MARKER = "/fr/companies/"
ENGLISH_MARKER = "/en/companies/"

_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _maybe_gunzip(data: bytes) -> bytes:
    """Decompress gzip payloads, pass plain XML through."""
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def parse_sitemap(data: bytes) -> list[str]:
    """Parse a (possibly gzipped) sitemap XML body into its <loc> URL list."""
    root = ElementTree.fromstring(_maybe_gunzip(data))
    locs = [el.text.strip() for el in root.iter(f"{_NS}loc") if el.text]
    # Namespaces vary in the wild; fall back to local-name matching.
    if not locs:
        locs = [el.text.strip() for el in root.iter() if el.tag.endswith("}loc") and el.text]
    return locs


def france_job_urls(locs: list[str]) -> list[str]:
    """Keep deduplicated France offer URLs (`/fr/companies/`, never `/en/`)."""
    seen: set[str] = set()
    out: list[str] = []
    for url in locs:
        if FRANCE_MARKER not in url or ENGLISH_MARKER in url:
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def extract_jsonld(html_text: str) -> list[dict]:
    """Return every parsed application/ld+json block on the page."""
    soup = BeautifulSoup(html_text, "html.parser")
    blocks = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        blocks.append(data)
    return blocks


def _og_meta(html_text: str, name: str) -> str | None:
    """Return the `content` of `<meta property=\"og:<name>\">` if present."""
    soup = BeautifulSoup(html_text, "html.parser")
    tag = soup.find("meta", attrs={"property": f"og:{name}"})
    if tag is None:
        return None
    content = tag.get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None


def parse_offer(html_text: str, url: str) -> dict:
    """
    Parse one offer page into a RAW record dict.

    Full path: JSON-LD JobPosting fields + og meta mirror.
    Partial path (no JobPosting block): og:title / og:description only,
    `content_quality="partial"` (mirrors linkedin.parse_job).
    """
    raw: dict = {"url": url}

    posting = next(
        (b for b in extract_jsonld(html_text) if b.get("@type") == "JobPosting"),
        None,
    )
    og_title = _og_meta(html_text, "title")
    og_description = _og_meta(html_text, "description")

    if posting is not None:
        org = posting.get("hiringOrganization") or {}
        locations = posting.get("jobLocation") or []
        first_addr = (
            locations[0].get("address", {}) if isinstance(locations, list) and locations else {}
        )
        location_parts = [
            first_addr.get(k) or ""
            for k in ("addressLocality", "addressRegion", "addressCountry")
        ]
        salary_value = ((posting.get("baseSalary") or {}).get("value") or {})
        unit = salary_value.get("unitText")

        raw.update({
            "content_quality": "full",
            "jobposting": True,
            "title": posting.get("title"),
            "company": org.get("name"),
            "description": posting.get("description"),
            "date_posted": posting.get("datePosted"),
            "employment_type": posting.get("employmentType"),
            "location_raw": ", ".join(p for p in location_parts if p) or None,
            "salary_min": salary_value.get("minValue"),
            "salary_max": salary_value.get("maxValue"),
            "salary_currency": (posting.get("baseSalary") or {}).get("currency"),
            "salary_unit": unit,
        })
    else:
        raw.update({
            "content_quality": "partial",
            "jobposting": False,
            "title": og_title,
            "company": None,
            "description": og_description,
            "date_posted": None,
            "employment_type": None,
            "location_raw": None,
            "salary_min": None,
            "salary_max": None,
            "salary_currency": None,
            "salary_unit": None,
        })

    raw["og_title"] = og_title
    raw["og_description"] = og_description
    return raw
