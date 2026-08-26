# LinkedIn jobs ingestion sources — spike report (2026-08-25)

Goal: test a range of LinkedIn job-ingestion sources and judge which yields the
most France-relevant results most robustly, weighing cost. Trigger: the
`linkedin_jobs` board under-yields (~19 France jobs/run) and the working
hypothesis was that using `apify~google-search-scraper` (Google discovery)
instead of a dedicated LinkedIn actor is the problem.

## Sources tested

| # | Source | How it works | France yield (real test) | Robustness | Cost |
|---|--------|-------------|--------------------------|-----------|------|
| 1 | `apify~google-search-scraper` (current) | Google `site:linkedin.com/jobs "<role>" France` discovery → fetch each `/jobs/view/` page → parse JSON-LD → France filter | **~19 jobs/run** (12 queries) | Works; under-harvests; ~60% of pages login-walled/partial (dropped for unknown location); SEO landing pages (`/jobs/<kw>-<loc>`) classified "drop" even though each embeds ~60 job links | ~$0.04/run (Apify compute) |
| 2 | Dedicated Apify actors (`coregent/linkedin-jobs-scraper`, `jobscrawler/pro-linkedin-jobs-scraper`, `spookyweb/linkedin-jobs`) | Actor scrapes LinkedIn job search → returns structured rows (title, company, location, salary, recruiter, applyUrl) | **Not runnable** — store pages exist but the Apify API returns 404 for every community actor (`GET /v2/acts/{id}`, `POST .../runs`, `by-user`) with the current token | Maintained/purchased actors are the robust paid option, but inaccessible from this account without a plan/purchase change | ~$1 / 1,000 results (coregent) |
| 3 | **LinkedIn guest jobs API (direct HTTP)** | `linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=<role>&location=France&start=N` — public, no auth; paginated 10 cards/page | **80 unique France job IDs from 8 pages** (single "Data Engineer"×France pair); cards return title, company, `Lille, Hauts-de-France, France`, full `/jobs/view/<id>` URL | Free, no rate-limit observed (8 rapid calls, all 200); `location=France` scopes results; job detail via existing `parse_job` per `/jobs/view/<id>` | **Free** (direct HTTP, no Apify) |

## Findings

1. **The current Google-search approach is the wrong tool.** It relies on
   Google's limited index of LinkedIn's login-gated `/jobs/view/` pages, so it
   surfaces few individual listings, loses ~60% to login walls, and discards
   the SEO landing pages that hold the most job links.
2. **Dedicated Apify actors are the structurally ideal answer** (bulk,
   structured, recruiter + salary fields) but are **not runnable from the
   current Apify account** — every community actor slug 404s on the API. This
   is exactly the unverified-slug trap recorded in the earlier session notes.
   Using them requires identifying a purchasable actor and either a paid actor
   or an account/plan change. Pay-per-event pricing (~$1/1k results) is cheap
   per job.
3. **The LinkedIn guest jobs API is the best available source**: direct HTTP,
   no auth, no actor, no cost, and it yields an order of magnitude more
   France-scoped jobs (80/run per keyword×location vs ~19/run). It returns the
   location in the search card, so the France signal no longer depends on a
   successful full-page parse. The existing `fetch → parse_job → _is_france_job`
   pipeline applies unchanged downstream.

## Recommendation

**Switch LinkedIn job discovery from `apify~google-search-scraper` to the
direct LinkedIn guest jobs API** (`seeMoreJobPostings/search`), paginating per
keyword×location (the existing `job_search_preferences.yaml` queries map to
keyword + `France`/city). Concretely:

- New `DiscoveryBackend` (or replace the google backend for jobs) that calls
  `jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=<role>&location=France&start=<N>`
  for each role, paginating (start=0,25,50,…) to a cap, emitting the card
  fields (job_id, title, company, location_raw, apply_url) as `SearchResult`s.
- Reuse the existing `_run_pass` fetch→`parse_job`→`_is_france_job`→dedup path
  unchanged; the France filter still guarantees only `country=FR` jobs enter
  silver, now from a much larger, already-France-scoped candidate set.
- Keep `apify~google-search-scraper` only for `linkedin_posts` (recruiter
  posts aren't on the jobs-guest endpoint) and as a jobs fallback if the guest
  API breaks.

Expected effect: `linkedin_jobs` France yield rises from ~19 to hundreds per
run (12 roles × ~80 each, deduped), comfortably exceeding freework's 138 —
comparable as the user requested — at **zero marginal cost**.

## Caveats / risks

- The guest jobs API is an **undocumented public endpoint**; LinkedIn can
  change or block it (same ToS fragility category as the Google-discovery
  approach the ROADMAP already tolerates for manual review). No cookie/login,
  so exposure is limited to public job-search pages.
- If the guest API breaks, the **paid dedicated actors are the fallback** but
  need a runnable/purchasable actor identified first (all candidates 404 on
  this account).
- Pagination depth and total-result caps per query need tuning; LinkedIn may
  cap per-query results.

## DoD for the switch (when implemented)

- [ ] Guest-API discovery backend emitting `SearchResult`s (job_id, title,
      company, location_raw, apply_url) from the paginated endpoint.
- [ ] `_run_pass` fetch/parse/France-filter path unchanged and reused.
- [ ] Unit tests with fixture HTML for pagination + card parsing + France
      scoping.
- [ ] Full suite green.
- [ ] Real run: `linkedin_jobs` France yield reported (target ≫ freework's
      138, was 19); cost = $0.
