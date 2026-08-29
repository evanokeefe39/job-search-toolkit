"""Enrichment assets: translate, extract tech, classify, company stats.

Each stage is incremental: it queries only the rows its gate selects
(column nullability/emptiness — see silver.py), processes those through the
LLM, and UPDATEs the results back. No full-dataset JSON round-trip, no
``_enrichment`` flag checks.

These assets are the deferred, optional LLM pass — none of them sits on the
ranking path (``scored_jobs`` depends only on the per-board silver assets).
They are reachable via asset selection or ``pipeline run --enrich``.

Company research is dimension-scoped: ``dim_company_enriched`` runs one LLM
call per distinct company (``dim_company`` row), never per job.
"""

import dagster as dg
from dagster import AssetExecutionContext

from .merge import SILVER_BOARD_ASSETS
from ..silver import (
    DIM_COMPANY_GATE,
    GATE_CLASSIFY,
    GATE_TECH,
    GATE_TRANSLATE,
    connect,
    fetch_jobs,
    mark_enriched,
    reset_stale,
    sql_json,
    sql_literal,
)
from ..config import get_enrichment_version

_DB_UPDATE_TAIL = " AND source_board = {}"


def _update(con, job: dict, sets: str) -> None:
    """Run an UPDATE for one row, quoting id/source_board."""
    con.execute(
        f'UPDATE silver.jobs SET {sets}, updated_at = NOW() '
        f'WHERE id = {sql_literal(job["id"])} '
        f'AND source_board = {sql_literal(job["source_board"])}'
    )


@dg.asset(
    deps=list(SILVER_BOARD_ASSETS.values()),
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
    deps=list(SILVER_BOARD_ASSETS.values()),
    group_name="enrichment",
    description="Research company stats per distinct company (one LLM call per company)",
)
def dim_company_enriched(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Research org_type for distinct companies whose org_type is unknown.

    Dimension-scoped: one LLM call per ``dim_company`` row, never per job
    (4,381 rows → 1,624 companies today). Updates ``dim_company`` only — the
    ranking path (``scored_jobs``) does not depend on this asset, so ranking
    materializes without any LLM call.
    """
    from ..enrich_canonical import enrich_companies as do_research

    with connect() as con:
        reset_stale(con, "company")
        rows = con.execute(
            f"SELECT company_id, name FROM silver.dim_company "
            f"WHERE {DIM_COMPANY_GATE} ORDER BY company_id"
        ).fetchall()
        companies = [{"company_id": r[0], "name": r[1]} for r in rows]
        do_research(companies)
        updated = 0
        for c in companies:
            if not c.get("org_type"):
                continue
            con.execute(
                f"UPDATE silver.dim_company SET "
                f"org_type = {sql_literal(c['org_type'])}, "
                f"enriched_at = NOW(), "
                f"enrichment_version = {get_enrichment_version()} "
                f"WHERE company_id = {sql_literal(c['company_id'])}"
            )
            updated += 1
    return dg.MaterializeResult(metadata={"researched": updated, "pending": len(companies)})


@dg.asset(
    deps=["scored_jobs"],
    group_name="enrichment",
    description="Auto-created company-news queue: enrich top-ranked companies (batched, capped)",
)
def dim_company_news_enriched(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Enrich top-ranked companies with news sentiment + INSEE size/legal.

    The queue is implicit in the selection: distinct companies from the
    current top-ranked fresh jobs (the ranking defines priority), ordered by
    best overall score DESC, capped at ``enrich_company_max`` (default 50).
    Incremental: rows with ``news_checked_at IS NOT NULL`` are skipped, so
    repeated runs only process newly-top-ranked companies. Batched news
    (N companies per DeepSeek call) + sequential INSEE. Never on the ranking
    path — ``scored_jobs`` does not depend on this asset.
    """
    from ..company_news import enrich_companies as do_news
    from ..company_insee import enrich_companies_insee as do_insee
    from ..config import get_enrichment_version
    from job_search_toolkit.run_config import get_run_config

    cap = get_run_config().enrich_company_max
    with connect() as con:
        reset_stale(con, "company_news")
        # Distinct companies from top-ranked fresh jobs, not yet news-enriched.
        rows = con.execute(
            "SELECT c.company_id, c.name, MAX(j.overall_score) AS best_score "
            "FROM gold.ranked_jobs j "
            "JOIN silver.dim_company c ON j.company_id = c.company_id "
            "WHERE j.days_since_seen <= 6 AND j.days_since_posted <= 60 "
            "  AND c.news_checked_at IS NULL "
            "GROUP BY c.company_id, c.name "
            "ORDER BY best_score DESC "
            f"LIMIT {int(cap)}"
        ).fetchall()
        companies = [{"company_id": r[0], "name": r[1]} for r in rows]

        news_results = do_news(companies) if companies else []
        insee_results = do_insee(companies) if companies else []
        insee_by_id = {r["company_id"]: r for r in insee_results}

        updated = 0
        for r in news_results:
            cid = r["company_id"]
            notes = r.get("notes") or []
            sentiment = r.get("sentiment") or "inconclusive"
            insee = insee_by_id.get(cid, {})
            sets = (
                f"news_notes = {sql_json(notes)}, "
                f"news_sentiment = {sql_literal(sentiment)}, "
                f"news_checked_at = NOW(), "
                f"insee_employee_range = "
                f"{sql_literal(insee.get('employee_range'))}, "
                f"insee_legal_type = {sql_literal(insee.get('legal_type'))}, "
                f"insee_checked_at = NOW()"
            )
            con.execute(
                f"UPDATE silver.dim_company SET {sets} "
                f"WHERE company_id = {sql_literal(cid)}"
            )
            updated += 1

    return dg.MaterializeResult(metadata={"enriched": updated, "queued": len(companies)})
