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
import json
from dagster import AssetExecutionContext

from .merge import SILVER_BOARD_ASSETS
from ..silver import (
    GATE_CLASSIFY,
    GATE_TECH,
    GATE_TRANSLATE,
    GOLDEN_DIM_COMPANY_GATE,
    connect,
    fetch_jobs,
    mark_enriched,
    reset_stale,
    sql_json,
    sql_literal,
)
from ..config import get_enrichment_version


def _coerce_notes(raw) -> list[str]:
    """news_notes is a JSON list (or legacy string); normalize to list[str]."""
    notes = raw
    if isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except ValueError:
            notes = [notes]
    return [n for n in (notes or []) if n]


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
    """Retired LLM org_type research — kept as a no-op for asset selection.

    ``org_type`` (legal form) proved inaccurate and mostly ``unknown``; it is
    no longer populated. The growth-stage signal now comes from the
    deterministic ``company_type_derived`` asset. Kept so existing deps and
    the ``enrich_job`` selection don't break.
    """
    with connect() as con:
        reset_stale(con, "company")
    return dg.MaterializeResult(metadata={"researched": 0, "pending": 0})


@dg.asset(
    deps=list(SILVER_BOARD_ASSETS.values()),
    group_name="enrichment",
    description="Derive growth-stage company_type for dim_company (deterministic, no LLM)",
)
def company_type_derived(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Derive ``company_type`` for every gated ``dim_company`` row — no LLM.

    Pure SQL over stored fields, first match wins (see
    data/_tmp_company_type_spec.md): job-level ``posting_company_type``
    ('esn' only — 'end_client' is a direct employer, not a consultancy) or a
    consulting/IT-services industry → ``it_consulting``;
    >5,000 employees in a tech industry → ``big_tech``; >1,000 →
    ``corporate``; growth funding or (founded 2008-2017, <=1,000 emp) →
    ``scale_up``; Seed/Series A or (founded >=2018, <=50 emp) → ``startup``;
    else ``unknown``. Deterministic: re-running with unchanged inputs yields
    the same values.
    """
    # Gate (company_type IS NULL) — resets on staleness via reset_stale above.
    with connect() as con:
        rows = con.execute(
            f"""
            WITH sig AS (
                SELECT
                    c.company_id,
                    EXISTS (SELECT 1 FROM silver.jobs j
                            WHERE j.company_id = c.company_id
                              AND j.is_active
                              AND j.posting_company_type = 'esn'
                    ) AS is_esn,
                    lower(coalesce(c.industry::VARCHAR, '[]')) AS ind_txt,
                    c.size_employees AS emp,
                    c.year_founded AS founded,
                    c.latest_funding_type AS funding
                FROM silver.dim_company c
                WHERE {GOLDEN_DIM_COMPANY_GATE}
            )
            SELECT company_id,
                CASE
                    WHEN is_esn
                         OR contains(ind_txt, 'it services')
                         OR contains(ind_txt, 'consulting')
                         OR contains(ind_txt, 'staffing')
                        THEN 'it_consulting'
                    WHEN emp > 5000 AND (
                         contains(ind_txt, 'software')
                         OR contains(ind_txt, 'cloud')
                         OR contains(ind_txt, 'internet')
                         OR contains(ind_txt, 'semiconductor')
                         OR contains(ind_txt, 'artificial intelligence')
                         OR contains(ind_txt, 'data')
                    ) THEN 'big_tech'
                    WHEN emp > 1000 THEN 'corporate'
                    WHEN (funding IN ('Series B', 'Series C', 'Series D',
                                      'Series E', 'Series F', 'Series G',
                                      'Series H', 'Series I', 'Private Equity',
                                      'Debt', 'Post-IPO Debt', 'Secondary Market',
                                      'Corporate Round', 'Funding Round')
                          OR regexp_matches(coalesce(funding, ''),
                                            '^Series [B-I]([ -]|$)'))
                         OR (founded BETWEEN 2008 AND 2017 AND emp <= 1000)
                        THEN 'scale_up'
                    WHEN funding IN ('Seed', 'Series A', 'Grant')
                         OR regexp_matches(coalesce(funding, ''), '^Seed([ -]|$)')
                         OR (founded >= 2018 AND emp <= 50)
                        THEN 'startup'
                    ELSE 'unknown'
                END AS company_type
            FROM sig
            ORDER BY company_id
            """
        ).fetchall()
        if rows:
            values = ", ".join(
                f"({sql_literal(cid)}, {sql_literal(ctype)})"
                for cid, ctype in rows
            )
            con.execute(
                f"""
                UPDATE silver.dim_company c SET
                    company_type = v.company_type,
                    enriched_at = NOW(),
                    enrichment_version = {get_enrichment_version()}
                FROM (VALUES {values}) AS v(company_id, company_type)
                WHERE c.company_id = v.company_id
                """
            )
    return dg.MaterializeResult(metadata={"derived": len(rows), "pending": 0})


@dg.asset(
    deps=["scored_jobs"],
    group_name="enrichment",
    description="Auto-created company-news queue: enrich top-ranked companies (batched, capped)",
)
def dim_company_news_enriched(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Enrich top-ranked companies with news sentiment + INSEE size/legal.

    The queue is implicit in the selection: distinct golden companies from
    the current top-ranked fresh jobs (the ranking defines priority), ordered
    by best overall score DESC, capped at ``enrich_company_max`` (default 50).
    ``silver.jobs.company_id`` is re-keyed to the golden id, so the join to
    ``dim_company`` hits the single golden row and each company is enriched
    exactly once regardless of how many boards advertised its jobs.
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

        news_results = do_news(companies, con=con) if companies else []
        insee_results = do_insee(companies, con=con) if companies else []
        insee_by_id = {r["company_id"]: r for r in insee_results}

        from ..company_resolve import aggregate_sentiment

        updated = 0
        for r in news_results:
            cid = r["company_id"]
            insee = insee_by_id.get(cid, {})
            # Merge-safe write at the golden grain: preserve any notes and
            # sentiment already on the row (e.g. concatenated across a merged
            # group by the dedup pass) and fold the fresh fetch in — notes
            # concatenated (deduped, order-preserving), sentiment via the
            # DECIDED aggregation rule (drop inconclusive, >1 distinct ->
            # mixed) so a re-run or post-merge re-enrich never clobbers
            # merged enrichment.
            prev_notes, prev_sentiment = con.execute(
                "SELECT news_notes, news_sentiment FROM silver.dim_company "
                f"WHERE company_id = {sql_literal(cid)}"
            ).fetchone()
            notes: list[str] = _coerce_notes(prev_notes)
            for n in (r.get("notes") or []):
                if n and n not in notes:
                    notes.append(n)
            sentiment = aggregate_sentiment(
                [prev_sentiment, r.get("sentiment") or "inconclusive"]
            )
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
