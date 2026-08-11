"""Enrichment assets: translate, extract tech, classify, company stats.

Each stage is incremental: it queries only the rows its gate selects
(column nullability/emptiness — see silver.py), processes those through the
LLM, and UPDATEs the results back. No full-dataset JSON round-trip, no
``_enrichment`` flag checks.
"""

import dagster as dg
from dagster import AssetExecutionContext

from .merge import silver_upsert
from ..silver import (
    GATE_CLASSIFY,
    GATE_COMPANY,
    GATE_TECH,
    GATE_TRANSLATE,
    connect,
    fetch_jobs,
    mark_enriched,
    reset_stale,
    sql_json,
    sql_literal,
)

_DB_UPDATE_TAIL = " AND source_board = {}"


def _update(con, job: dict, sets: str) -> None:
    """Run an UPDATE for one row, quoting id/source_board."""
    con.execute(
        f'UPDATE silver.jobs SET {sets}, updated_at = NOW() '
        f'WHERE id = {sql_literal(job["id"])} '
        f'AND source_board = {sql_literal(job["source_board"])}'
    )


@dg.asset(
    deps=[silver_upsert],
    group_name="enrichment",
    description="Translate French descriptions to English (no-op for hiringcafe)",
)
def translated(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Translate French descriptions to English using LLM."""
    from ..enrich_canonical import translate_jobs as do_translate

    with connect() as con:
        reset_stale(con, "translate")
        rows = fetch_jobs(
            con, ["id", "source_board", "description_text"],
            GATE_TRANSLATE, order="id",
        )
        do_translate(rows)
        updated = 0
        for job in rows:
            if job.get("description_language") != "en":
                continue  # translation failed — stays pending for next run
            _update(con, job, (
                f"description_text = {sql_literal(job['description_text'])}, "
                f"description_language = 'en'"
            ))
            updated += 1
        mark_enriched(con)
    return dg.MaterializeResult(metadata={"translated": updated, "pending": len(rows)})


@dg.asset(
    deps=[translated],
    group_name="enrichment",
    description="Extract technologies and competencies using LLM",
)
def tech_extracted(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Extract technologies, competencies, seniority, role using LLM."""
    from ..enrich_canonical import extract_tech as do_extract

    with connect() as con:
        reset_stale(con, "tech")
        rows = fetch_jobs(
            con, ["id", "source_board", "title", "description_text"],
            GATE_TECH, order="id",
        )
        do_extract(rows)
        updated = 0
        for job in rows:
            if not job.get("_enrichment", {}).get("tech_extracted"):
                continue
            _update(con, job, (
                f"technologies = {sql_json(job['technologies'])}, "
                f"competencies = {sql_json(job['competencies'])}, "
                f"seniority_level = {sql_literal(job['seniority_level'])}, "
                f"role_category = {sql_literal(job['role_category'])}"
            ))
            updated += 1
        mark_enriched(con)
    return dg.MaterializeResult(metadata={"extracted": updated, "pending": len(rows)})


@dg.asset(
    deps=[tech_extracted],
    group_name="enrichment",
    description="Classify company vertical and engagement using LLM (freework only)",
)
def vertical_classified(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Classify posting company type and engagement using LLM."""
    from ..enrich_canonical import classify_jobs as do_classify

    with connect() as con:
        reset_stale(con, "classify")
        rows = fetch_jobs(
            con, ["id", "source_board", "company", "description_text"],
            GATE_CLASSIFY, order="id",
        )
        do_classify(rows)
        updated = 0
        for job in rows:
            if not job.get("_enrichment", {}).get("vertical_classified"):
                continue
            _update(con, job, (
                f"posting_company_type = {sql_literal(job['posting_company_type'])}, "
                f"end_client_name = {sql_literal(job['end_client_name'])}, "
                f"end_client_sector = {sql_literal(job['end_client_sector'])}, "
                f"engagement_type = {sql_literal(job['engagement_type'])}"
            ))
            updated += 1
        mark_enriched(con)
    return dg.MaterializeResult(metadata={"classified": updated, "pending": len(rows)})


@dg.asset(
    deps=[vertical_classified],
    group_name="enrichment",
    description="Research company stats using LLM (freework only; hiringcafe pre-enriched)",
)
def company_stats(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Research company stats for freework rows whose org_type is unknown."""
    from ..enrich_canonical import enrich_company_stats as do_research

    with connect() as con:
        reset_stale(con, "company")
        rows = fetch_jobs(
            con, ["id", "source_board", "company", "company_info", "end_client_sector"],
            GATE_COMPANY, order="id",
        )
        do_research(rows)
        updated = 0
        for job in rows:
            if not job.get("_enrichment", {}).get("company_researched"):
                continue
            _update(con, job, f"company_info = {sql_json(job['company_info'])}")
            updated += 1
        mark_enriched(con)
    return dg.MaterializeResult(metadata={"researched": updated, "pending": len(rows)})
