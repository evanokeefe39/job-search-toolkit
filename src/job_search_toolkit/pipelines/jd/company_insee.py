"""INSEE company size/legal enrichment via recherche-entreprises.api.gouv.fr.

Free, keyless, no rate limit (sequential or light concurrency ~0.2-0.4s/call).
Returns employee-range + legal-type for a company name, or None when the
company isn't found. Honesty guard: a miss returns None (the caller records
nothing), never a fabricated guess.

Endpoint: https://recherche-entreprises.api.gouv.fr/search?q=<name>
Tranche d'effectif: A(0), B(1-2), C(3-5), D(6-9), E(10-19), F(20-49),
G(50-99), H(100-199), I(200-249), J(250-499), K(500-999), L(1000-1999),
M(2000-4999), N(5000-9999), O(10000+). Nature juridique: 5xxx = SAS/SARL/etc.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://recherche-entreprises.api.gouv.fr/search"
_UA = {"User-Agent": "Mozilla/5.0"}

# INSEE tranche d'effectif -> human-readable employee range
_EFFECTIF_RANGE: dict[str, str] = {
    "00": "0", "01": "1-2", "02": "3-5", "03": "6-9", "11": "10-19",
    "12": "20-49", "21": "50-99", "22": "100-199", "31": "200-249",
    "32": "250-499", "41": "500-999", "42": "1000-1999", "51": "2000-4999",
    "52": "5000-9999", "53": "10000+",
}


def _normalize_legal(code: str | None) -> str | None:
    """Map an INSEE nature-juridique code to a coarse legal type."""
    if not code:
        return None
    code = code.strip()
    # 5xxx are sociétés (SAS/SARL/SA...). 1xxx/2xxx entrepreneurs/individuels.
    if code.startswith("5"):
        return "société"
    if code.startswith("1") or code.startswith("2"):
        return "independent"
    return code


def insee_lookup(company: str, *, timeout: float = 15.0) -> dict[str, Any] | None:
    """Look up a company's employee range + legal type from INSEE.

    Returns ``{"employee_range", "legal_type", "siren"}`` or None when not
    found (honest miss). Callers pace their own requests (sequential or
    light concurrency) to respect the public API.
    """
    try:
        r = httpx.get(_BASE, params={"q": company, "per_page": 1}, headers=_UA,
                      timeout=timeout)
        if r.status_code != 200:
            logger.warning("INSEE lookup %s -> HTTP %s", company, r.status_code)
            return None
        data = r.json()
    except Exception as e:
        logger.warning("INSEE lookup %s failed: %s", company, e)
        return None
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    # Field names are snake_case on this API: nature_juridique (top level) and
    # tranche_effectif_salarie (the legal-unit employee band). The siege dict
    # holds the establishment-level band (annee_tranche_effectif_salarie) but
    # not the code itself; prefer the legal-unit values.
    effectif = top.get("tranche_effectif_salarie")
    legal = top.get("nature_juridique")
    return {
        "employee_range": _EFFECTIF_RANGE.get(effectif, effectif),
        "legal_type": _normalize_legal(legal),
        "siren": top.get("siren"),
    }


def enrich_companies_insee(companies: list[dict[str, str]],
                          *, sleep: float = 0.15) -> list[dict[str, Any]]:
    """Sequential INSEE lookup for a list of ``{"company_id", "name"}`` rows.

    Sequential (polite, ~0.2-0.4s each) — the INSEE public API rate-limits
    bursts. Returns ``[{"company_id", "employee_range", "legal_type"}]`` for
    found companies only (misses are omitted — honest).
    """
    out: list[dict[str, Any]] = []
    for c in companies:
        res = insee_lookup(c["name"])
        if res:
            out.append({"company_id": c["company_id"], **res})
        time.sleep(sleep)
    return out
