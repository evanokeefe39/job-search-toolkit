"""CSV company-enrichment source: ingest ``company_findings.csv`` into dim_company.

CSV-as-contract: a coding agent or a future automated Tavily/Exa agentic loop
produces a CSV of per-company findings; this asset merges it into
``silver.dim_company``. The CSV shape is the contract — the pipeline does not
care who produced it. The producer writes refs/justifications in ``sources``;
this asset never invents findings.

Header (exact):
    company_name,source_board,industry,size_employees,year_founded,
    hq_country,latest_funding_type,latest_funding_amount_usd,company_type,
    sources

Merge semantics (see data/_tmp_company_enrich_spec.md):
- Resolve each row to a dim_company row: golden id via the company_alias
  registry when known (if that id exists in dim_company), else the
  (name, source_board) dim row, else the first dim row carrying the
  normalized name, else a new row (golden id when the registry knows the
  name, otherwise a per-board key).
- Upsert through ``_upsert_dim_companies`` (COALESCE: non-NULL CSV inputs
  win, absent inputs leave stored values intact).
- Trusted ``company_type``: a valid non-empty value is written directly and
  survives ``company_type_derived`` (that asset's gate is
  ``company_type IS NULL``). An empty value leaves NULL for derivation.
- ``sources`` are unioned (deduped) into ``company_sources`` — append-only
  provenance, idempotent across re-runs.

Absent CSV → no-op with ``rows=0`` (the pipeline must never fail because no
ad-hoc enrichment CSV exists yet).
"""

import csv
import json
from pathlib import Path

import dagster as dg
from dagster import AssetExecutionContext

from ..company_resolve import golden_company_id, load_alias_registry, norm
from ..silver import (
    _upsert_dim_companies,
    company_id,
    connect,
    normalize_company_name,
    sql_literal,
)

# Contract header — any deviation fails loudly (the CSV *is* the contract).
CSV_COLUMNS = [
    "company_name",
    "source_board",
    "industry",
    "size_employees",
    "year_founded",
    "hq_country",
    "latest_funding_type",
    "latest_funding_amount_usd",
    "company_type",
    "sources",
]

ENRICHMENT_DIR = Path("data") / "company_enrichment"

# Valid self-declared company_type values (CompanyType enum, schemas.py).
TRUSTED_COMPANY_TYPES = {
    "big_tech",
    "corporate",
    "scale_up",
    "startup",
    "it_consulting",
    "unknown",
}


class CompanyEnrichmentConfig(dg.Config):
    """Optional explicit CSV path (defaults to the newest CSV in the dir)."""

    csv_path: str | None = None


def _find_csv(csv_path: str | None) -> Path | None:
    """Explicit path when given; else the newest CSV in data/company_enrichment."""
    if csv_path:
        p = Path(csv_path)
        if not p.exists():
            raise FileNotFoundError(
                f"explicit enrichment csv_path does not exist: {csv_path}"
            )
        return p
    if not ENRICHMENT_DIR.exists():
        return None
    candidates = sorted(ENRICHMENT_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _parse_optional_int(raw: str | None) -> int | None:
    v = (raw or "").strip()
    return int(v) if v else None


def _parse_industry(raw: str | None) -> list[str] | None:
    """JSON array string per the contract; a bare token becomes a 1-list."""
    v = (raw or "").strip()
    if not v:
        return None
    try:
        parsed = json.loads(v)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x).strip()] or None
    except ValueError:
        pass
    return [v]


def _resolve_company_id(con, name: str, source_board: str | None,
                        golden: str | None) -> str:
    """Pick the dim_company row this CSV row merges into (see module docstring)."""
    n = normalize_company_name(name)
    candidates = []
    if golden:
        candidates.append(golden)
    if source_board:
        candidates.append(company_id(name, source_board))
    for cid in candidates:
        hit = con.execute(
            f"SELECT 1 FROM silver.dim_company WHERE company_id = {sql_literal(cid)}"
        ).fetchone()
        if hit:
            return cid
    # No registry/board hit: any dim row with the normalized name (prefer golden).
    row = con.execute(
        f"SELECT company_id FROM silver.dim_company WHERE name = {sql_literal(n)} "
        f"ORDER BY (company_id = {sql_literal(golden or '')}) DESC, company_id LIMIT 1"
    ).fetchone()
    if row:
        return row[0]
    # Unknown company: new dim row. Golden key when the registry knows the
    # name, else a per-board key over the CSV board (or a CSV marker board).
    return golden or company_id(name, source_board or "company_findings")


def _existing_sources(con, cid: str) -> list[str]:
    row = con.execute(
        f"SELECT company_sources FROM silver.dim_company "
        f"WHERE company_id = {sql_literal(cid)}"
    ).fetchone()
    raw = row[0] if row else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [raw]
    return [s for s in (raw or []) if s]


@dg.asset(
    deps=[],
    group_name="enrichment",
    description=(
        "Ingest the CSV company-enrichment source (company_findings.csv) into "
        "dim_company. CSV-as-contract: trusted self-declared company_type wins, "
        "empty leaves NULL for company_type_derived; sources refs append to "
        "company_sources. No-op when the CSV is absent."
    ),
)
def company_enrichment_ingested(
    context: AssetExecutionContext, config: CompanyEnrichmentConfig
) -> dg.MaterializeResult:
    """Merge per-company findings from the enrichment CSV into dim_company."""
    csv_path = _find_csv(config.csv_path)
    if csv_path is None:
        context.log.info("company enrichment: no CSV found — no-op")
        return dg.MaterializeResult(
            metadata={"rows": 0, "trusted_company_type": 0, "source": "absent"}
        )

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != CSV_COLUMNS:
            raise ValueError(
                f"{csv_path}: header does not match the enrichment CSV contract "
                f"(expected {CSV_COLUMNS}, got {reader.fieldnames})"
            )
        records = [
            {k: (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
            for r in reader
        ]

    ingested = trusted = 0
    with connect() as con:
        registry = load_alias_registry(con)
        rows = []
        for rec in records:
            name = rec["company_name"]
            if not name:
                raise ValueError(f"{csv_path}: row with empty required company_name")
            sources_raw = rec["sources"]
            if not sources_raw:
                raise ValueError(
                    f"{csv_path}: row for {name!r} missing required sources refs"
                )
            n = norm(name)
            golden = registry.get(n)
            cid = _resolve_company_id(con, name, rec["source_board"] or None, golden)

            declared = rec["company_type"].strip()
            company_type = declared if declared in TRUSTED_COMPANY_TYPES else None
            if company_type:
                trusted += 1

            new_refs = [s.strip() for s in sources_raw.split(";") if s.strip()]
            merged_refs = list(dict.fromkeys(_existing_sources(con, cid) + new_refs))

            row = {
                "company_id": cid,
                "name": n,
                "display_name": name,
                "source_board": rec["source_board"] or None,
                "industry": _parse_industry(rec["industry"]),
                "size_employees": _parse_optional_int(rec["size_employees"]),
                "year_founded": _parse_optional_int(rec["year_founded"]),
                "hq_country": rec["hq_country"] or None,
                "latest_funding_type": rec["latest_funding_type"] or None,
                "latest_funding_amount_usd": _parse_optional_int(
                    rec["latest_funding_amount_usd"]
                ),
                "company_type": company_type,
                "company_sources": merged_refs or None,
            }
            rows.append(row)
            ingested += 1
        _upsert_dim_companies(con, rows)
        # Stale-derived reset: rows whose CSV update supplied new signal
        # inputs (industry/size/funding/founded) but no trusted company_type
        # get company_type cleared so company_type_derived re-derives.
        signal_cids = [
            r["company_id"] for r in rows
            if not r["company_type"]
            and (r["industry"]
                 or r["size_employees"] is not None
                 or r["year_founded"] is not None
                 or r["latest_funding_type"]
                 or r["latest_funding_amount_usd"] is not None)
        ]
        if signal_cids:
            con.execute(
                "UPDATE silver.dim_company SET company_type = NULL "
                "WHERE company_id IN ("
                + ", ".join(sql_literal(c) for c in signal_cids) + ")"
            )

    context.log.info(
        "company enrichment: %d row(s) ingested from %s (%d trusted company_type)",
        ingested, csv_path, trusted,
    )
    return dg.MaterializeResult(
        metadata={
            "rows": ingested,
            "trusted_company_type": trusted,
            "source": str(csv_path),
        }
    )
