---
name: company-enrich
description: Run company-news + INSEE enrichment on the top-ranked jobs (beyond the auto-50 queue). Use when asked to enrich companies with recent news, run company research on ranked jobs, get news sentiment/notes for shortlisted companies, or extend the enrichment queue past its automatic cap.
---
## Requirements

Python 3.14+ and uv (package manager). Install the toolkit with:

```bash
pip install job-search-toolkit
```

(or `uv tool install job-search-toolkit`).


# company-enrich

Enrich companies with a news signal (sentiment + employee-relevant notes) and
INSEE size/legal data. The pipeline auto-creates an enrichment queue of the
top-ranked fresh companies (capped at `enrich_company_max`, default 50) and
processes it on every `pipeline run`. This skill is for **manual extension**:
run the same enrichment on a broader set (e.g. all fresh jobs above a lower
score threshold, or specific shortlisted companies) without waiting for the
auto-queue.

## How the enrichment works

- **Sources:** Google News RSS + Bing News RSS (free, keyless, unlimited) —
  6 targeted queries per company (generic/funding/scandal/mgmt/people/stock),
  headlines merged + deduped.
- **Signal:** a single batched DeepSeek call per ~5 companies produces
  `{company, sentiment, notes[]}` where `sentiment ∈ positive|negative|mixed|inconclusive`
  and each note is an event affecting employee stability, compensation,
  wellbeing, or 6-18 month trajectory (fund raises, layoffs, scandals,
  lawsuits, leadership, M&A, stock moves).
- **INSEE:** `recherche-entreprises.api.gouv.fr` (free, keyless) adds
  `employee_range` + `legal_type`.
- **Honesty guard:** a company with no headlines → `sentiment=inconclusive`,
  empty notes. No fabrication, ever.
- Results are written to `silver.dim_company` (`news_sentiment`,
  `news_notes`, `news_checked_at`, `insee_employee_range`, `insee_legal_type`,
  `insee_checked_at`). The ranking path does **not** depend on this — it is a
  post-shortlist enrichment feeding the human's decision.

## Playbook

1. **Preflight.** Confirm you are at the repo root. The DeepSeek API key comes
   from `.env`; the pipeline fails loudly if missing. Do not improvise a fallback.

2. **Decide the enrichment scope** (what the human asked for):
   - **All top-ranked fresh companies** (covers the full >0.7 / >0.6 set, not
     just the auto-50): run the asset with a raised cap.
   - **Specific shortlisted companies** (a list the human picked): enrich those
     names only.

3. **Run the enrichment.**

   For the **auto asset** with a larger cap (e.g. 300), set the run config
   override and materialize just the news asset:

   ```bash
   RUN_CONFIG=default uv run python -c "
   import dagster as dg
   from job_search_toolkit.pipelines.jd.definitions import ALL_ASSETS
   from job_search_toolkit.run_config import get_run_config
   # cap is read from RunConfig; set it via config.yaml runs.<name> or env
   selection = dg.AssetSelection.keys('dim_company_news_enriched')
   result = dg.materialize(ALL_ASSETS, selection=selection)
   print('SUCCESS:', result.success)
   "
   ```

   The cap (`enrich_company_max`) is a `RunConfig` field — set it in
   `config.yaml` under `defaults:` or `runs.<name>:` to raise the per-run cap.

   For **specific companies**, use the library directly (dimension-scoped, one
   call per company, batched):

   ```python
   from job_search_toolkit.pipelines.jd.company_news import enrich_companies
   from job_search_toolkit.pipelines.jd.company_insee import enrich_companies_insee
   from job_search_toolkit.pipelines.jd.silver import connect, sql_json, sql_literal

   targets = [{"company_id": "…", "name": "CompanyName"}]  # from dim_company
   news = enrich_companies(targets)
   insee = enrich_companies_insee(targets)
   with connect() as con:
       for r in news:
           cid = r["company_id"]
           n = insee_by_id.get(cid, {})
           con.execute(f"""
               UPDATE silver.dim_company SET
                   news_notes = {sql_json(r['notes'])},
                   news_sentiment = {sql_literal(r['sentiment'])},
                   news_checked_at = NOW(),
                   insee_employee_range = {sql_literal(n.get('employee_range'))},
                   insee_legal_type = {sql_literal(n.get('legal_type'))},
                   insee_checked_at = NOW()
               WHERE company_id = {sql_literal(cid)}
           """)
   ```

4. **Report the results and STOP.** For each enriched company, report:
   - sentiment (positive/negative/mixed/inconclusive)
   - the notes (dot points)
   - INSEE size/legal, when present
   - call out **negatives and mixed** first — those are the "investigate
     before applying" signals.
   - call out **inconclusive** honestly as "no notable news found" (common for
     small French ESNs).

   End your turn there. The human decides what to shortlist or apply to.

## Failure handling

- If RSS or the DeepSeek call fails (network, rate limit, malformed response),
  STOP and report the exact error. The asset already handles per-batch failures
  by writing `inconclusive` (honest), but a systemic failure should be surfaced.
- If `data/warehouse/jobs.db` or `silver.dim_company` is missing/readable,
  report it. Never fabricate a sentiment or notes.

## Do not

- Never use the news signal to auto-rank or auto-apply — it feeds the human's
  shortlist decision only.
- Never touch `applications/`, `resume/`, or CRM files.
- Never commit warehouse/bronze/silver outputs (all gitignored).
- Never modify pipeline code or schemas from this skill.
