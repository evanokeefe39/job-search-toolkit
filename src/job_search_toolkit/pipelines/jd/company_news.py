"""Company news enrichment: Google+Bing RSS headlines -> batched DeepSeek signal.

Dimension-scoped (one pass per distinct company, batched into a single LLM
call per group). Free, keyless, unlimited RSS gateways — no Tavily (quota-blow
risk) and no SearXNG (engine fragility). Honesty guard: empty ``notes`` +
``inconclusive`` when there's nothing notable; no fabrication.

Two entry points:
- ``collect_headlines(company)`` — raw Google+Bing RSS titles for one company.
- ``enrich_companies(companies)`` — batched: N companies per prompt, one
  structured DeepSeek call per batch, returns ``[{company, sentiment, notes}]``.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from pydantic import BaseModel, Field

from .enrich_canonical import _get_client

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0"}

# One query per event class; merged + deduped by title per company.
QUERIES: dict[str, str] = {
    "generic": '"{c}"',
    "funding": '"{c}" funding OR raise OR investment OR "series A" OR "series B" OR valuation',
    "scandal": '"{c}" scandal OR lawsuit OR probe OR investigation OR settlement OR recall',
    "mgmt":    '"{c}" CEO OR executive OR appointment OR resign OR depart OR board',
    "people":  '"{c}" layoffs OR hiring OR headcount OR restructure OR workforce',
    "stock":   '"{c}" stock OR shares OR earnings OR "market cap" OR ipo',
}
_WHEN = " when:90d"
_MAX_HEADLINES = 15          # cap headlines fed to the LLM per company
_BATCH_SIZE = 5              # companies per LLM call (bounded by max_tokens)


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _google(company: str, q: str) -> list[dict[str, str]]:
    r = httpx.get(
        "https://news.google.com/rss/search",
        params={"q": q + _WHEN, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        headers=_UA, timeout=20,
    )
    if r.status_code != 200:
        return []
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return []
    return [{"title": _strip_html(it.findtext("title") or ""),
             "source": _strip_html(it.findtext("source") or "")}
            for it in root.findall(".//item")[:3]]


def _bing(company: str, q: str) -> list[dict[str, str]]:
    r = httpx.get("https://www.bing.com/news/search",
                  params={"q": q, "format": "rss"}, headers=_UA, timeout=20)
    if r.status_code != 200:
        return []
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return []
    return [{"title": _strip_html(it.findtext("title") or ""),
             "source": it.findtext("source") or ""}
            for it in root.findall(".//item")[:2]]


def collect_headlines(company: str) -> list[dict[str, str]]:
    """Collect and dedup headline titles for one company from both engines."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for q in QUERIES.values():
        qq = q.format(c=company)
        for fn in (_google, _bing):
            for it in fn(company, qq):
                key = it["title"].strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    items.append(it)
    return items[:_MAX_HEADLINES]


# --- Structured batched output ------------------------------------------------

class CompanyNews(BaseModel):
    company: str = Field(description="Company name exactly as provided")
    sentiment: str = Field(description="positive | negative | mixed | inconclusive")
    notes: list[str] = Field(
        description="Employee/comp/wellbeing/trajectory-relevant dot points")


class CompanyNewsBatch(BaseModel):
    results: list[CompanyNews] = Field(
        description="One entry per company, in the same order as the input")


_NEWS_SYSTEM = """You are a business analyst giving a job seeker a COARSE screen on companies.
For EACH company in the input, extract a sentiment and noteworthy dot points from ONLY its
headlines. sentiment is one of: positive | negative | mixed | inconclusive.
Each note is an event affecting employee stability, compensation, wellbeing, or the company's
6-18 month trajectory (fund raises, layoffs/hiring, scandals, lawsuits, leadership changes,
M&A, restructuring, stock moves). Skip generic product-listicle coverage.
Rules: no fabrication; empty notes + inconclusive is honest when there's nothing notable.
Return ONE result object per company, in the same order as the input. JSON only."""


def _format_prompt(companies: list[tuple[dict[str, str], list[dict[str, str]]]]) -> str:
    """Build the batched user prompt from pre-collected headlines."""
    blocks = []
    for c, items in companies:
        if items:
            lines = "\n".join(f'- [{it["source"]}] {it["title"]}' for it in items)
            blocks.append(f"### {c['name']}\n{lines}")
        else:
            blocks.append(f"### {c['name']}\n(no headlines)")
    return "\n\n".join(blocks)


def enrich_companies(companies: list[dict[str, str]],
                     *, con: Any = None) -> list[dict[str, Any]]:
    """Batched company-news enrichment.

    ``companies``: list of ``{"company_id", "name"}`` (dim_company row shape).
    ``con`` (optional): DuckDB connection — when given, each company_id is
    resolved to its golden id via the ``silver.company_alias`` registry so
    results always key the golden record (post-dedup dim ids are golden
    already; the registry covers rows keyed by a pre-dedup id).
    Returns ``[{"company_id", "company", "sentiment", "notes"}]``, one entry
    per input, in the same order. Honesty guard: a company with no headlines
    yields ``sentiment="inconclusive", notes=[]`` without any LLM call.
    """
    if con is not None:
        from .company_resolve import load_alias_registry, norm
        registry = load_alias_registry(con)
        companies = [
            {**c, "company_id": registry.get(norm(c["name"]), c["company_id"])}
            for c in companies
        ]
    client = _get_client()

    out: list[dict[str, Any]] = []
    # Collect headlines once per company up front.
    with_headlines: list[tuple[dict[str, str], list[dict[str, str]]]] = []
    no_headlines: list[dict[str, str]] = []
    for c in companies:
        items = collect_headlines(c["name"])
        if items:
            with_headlines.append((c, items))
        else:
            no_headlines.append(c)
    for c in no_headlines:
        out.append({"company_id": c["company_id"], "company": c["name"],
                    "sentiment": "inconclusive", "notes": []})

    # group into batches
    for i in range(0, len(with_headlines), _BATCH_SIZE):
        batch = with_headlines[i:i + _BATCH_SIZE]
        if not batch:
            continue
        prompt = _format_prompt(batch)
        try:
            resp = client.chat.completions.create(
                model=_get_model(),
                response_model=CompanyNewsBatch,
                messages=[{"role": "system", "content": _NEWS_SYSTEM},
                          {"role": "user", "content": prompt}],
                max_tokens=800,
            )
            # Contract: CompanyNewsBatch.results is input-ordered. Join
            # positionally (batch index) so a dropped/misaligned row is loud,
            # not silently nulled via a name lookup. Fall back to a name match
            # only for cosmetic renames; a count mismatch raises.
            results = resp.results
            if len(results) != len(batch):
                raise ValueError(
                    f"Batch result count {len(results)} != input count {len(batch)}"
                )
            for idx, r in enumerate(results):
                expected = batch[idx][0]
                cid = (
                    expected["company_id"]
                    if r.company.strip().lower() == expected["name"].strip().lower()
                    else next(
                        (c["company_id"] for c, _ in batch
                         if r.company.strip().lower() == c["name"].strip().lower()),
                        None,
                    )
                )
                if cid is None:
                    raise ValueError(
                        f"Batch result for unexpected company {r.company!r} "
                        f"(expected {expected['name']!r})"
                    )
                out.append({
                    "company_id": cid,
                    "company": r.company,
                    "sentiment": r.sentiment,
                    "notes": r.notes,
                })
        except Exception as e:
            logger.warning("Batched news enrichment failed: %s", e)
            for c, _ in batch:
                out.append({"company_id": c["company_id"], "company": c["name"],
                            "sentiment": "inconclusive", "notes": []})
    return out


def _get_model() -> str:
    from job_search_toolkit.run_config import get_run_config
    return get_run_config().llm_model
