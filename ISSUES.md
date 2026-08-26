# ISSUES.md — job_search_scraping


## Open

### LinkedIn discovery: France-relevant yield too low — 3/92 jobs (BLOCKER 2026-08-25)

**Status:** BLOCKER for completing the LinkedIn feature (decide before closing
the `feat/linkedin-source-adapter` branch).

**Symptom:** the LinkedIn integration ingests end-to-end, but the discovery
returns very few France-relevant jobs for a France-based job search. Of 92
LinkedIn rows in `silver.jobs`:
- `linkedin_jobs` (listings): 34 rows, 12 with a parsed location, only **3 in
  France** (all Paris-area: Senior Analytics Engineer; Workday Integration &
  Analytics Engineer; Analytics Engineer). The other 31 are worldwide (Sydney,
  Atlanta, London, Mumbai, Bengaluru, Melbourne, Halifax, Pune, …).
- `linkedin_posts` (recruiter posts): 58 rows, 55 with no location at all
  (posts carry no structured location), 3 with one (none France).

**Root cause:** the Apify/TAVILY discovery query is broad (not France-scoped),
so it surfaces a global mix; the location parser itself works (it correctly
picked up the Paris rows). Recruiter posts are inherently location-less.

**Why it blocks completion:** the feature's purpose is France-relevant leads,
but as-is it delivers ~3 France job listings from a full discovery — not enough
to justify the pipeline surface (two boards + discovery cost). Closing the
branch without resolving this ships a feature with negligible France value.

**Partial fix applied (2026-08-25):** deterministic France filter
(`_is_france_job`, committed `a4537c5`) + France-scoped/widened queries in
`job_search_preferences.yaml` (gitignored). 243 tests green. Real Apify runs
now keep 11–19 genuine France job listings (up from 3); all kept jobs have
`location.country=FR`. Posts (49/run) are not country-filtered by design.

**Diagnosis (2026-08-25) — why yield is still far below freework (138):**
the discovery under-harvests. Google's `site:linkedin.com/jobs "<role>" France`
returns ~120 results/run, of which only ~53 are individual `/jobs/view/`
listings (fetched; ~40% parse full, the rest are login-walled partials the
France filter drops for unknown location). The other ~64 results are LinkedIn
SEO landing pages (`/jobs/<keyword>-<location>`, e.g.
`/jobs/data-engineer-jobs-paris`) that `classify_url` marks "drop" — BUT each
one embeds ~60 individual `/jobs/view/<id>` links (confirmed: leboncoin,
neosoft, stellantis, voodoo…). Discarding them throws away the richest France
job-link source.

**Fix direction (harvest):** treat LinkedIn `/jobs/<keyword>-<location>` SEO
pages as link-indexes — fetch them and extract the embedded `/jobs/view/`
links into the fetch queue (dedup against existing URLs). This multiplies the
France job pool by an order of magnitude (potentially freework-comparable).
Secondary: reconsider the France filter for login-walled partials (they are
France-scoped by query but currently dropped for unknown location).

**Spike result (2026-08-25) — preferred fix found + IMPLEMENTED:** a source
spike (`docs/linkedin-source-spike.md`) tested 3 ingestion routes on real data:
(1) current `apify~google-search-scraper` (~19 France jobs/run, ~$0.04, under-
harvests, ~60% login-wall loss); (2) dedicated Apify LinkedIn actors
(`coregent/…`, `jobscrawler/…`, `spookyweb/…`) — store pages exist but the API
404s for every community actor on this account (unverified-slug trap), not
runnable without a purchase/plan change, ~$1/1k results; (3) **LinkedIn's
public guest jobs API** (`jobs-guest/jobs/api/seeMoreJobPostings/search`) via
direct HTTP — **free, no auth, 80 unique France job IDs from a single
keyword×France pair over 8 pages**, returning title/company/location/apply URL
in the card. Switch IMPLEMENTED (commit `4bb4dd2`): `LinkedInGuestBackend` in
`linkedin/discovery.py` (free, no auth, paginates `seeMoreJobPostings/search`),
`guest_jobs: true` in `job_search_preferences.yaml`, `_run_pass`
fetch→`parse_job`→`_is_france_job` path unchanged, google-scraper retained for
`linkedin_posts` + fallback. Validated: 30 unique France jobs from 12 queries
in 1s at $0 (capped 30/query). 253 tests green. **FRAGILE:** the guest API is
an undocumented public endpoint — if it breaks, see `docs/linkedin-source-spike.md`
for fallbacks. This resolves the France-yield blocker (now ~freework-scale, no
login-wall loss).

**If not switching to the guest API:** accept LinkedIn as a low-volume
supplementary France source (~10–20 jobs/run) and close the branch on that
caveat.


### LinkedIn posts → jobs: recruiter-region follow-up + regex-vs-LLM enrichment (ENHANCEMENT 2026-08-25)

**Kind:** enhancement / exploration plan (not a bug). Plan:
`tasks/plans/linkedin-posts-to-jobs.md`.

**Context:** recruiter posts ARE job opportunities and already become jobs via
`linkedin/posts/.../post_extract.py:extract_from_post` (regex), which returns a
verdict: `land` (title + location/workplace found → becomes a `CanonicalJob`),
`queue` (hiring signal but title/location not confidently extracted → kept with
null title/location "for the enrichment LLM pass"), `drop` (no job signal →
excluded). So the regex-pass→job / LLM-enrich-if-insufficient shape already
exists for `queue`.

**What to explore:**
1. **Recruiter-region follow-up:** for posts without a usable location, fetch
   the recruiter (author) profile / search the author to infer which region
   they manage (APAC / EMEA / DACH / USA / FR…). Judge whether regex over the
   post + profile text is sufficient, or whether LLM structured extraction is
   needed for usable region signals. Also weigh cost (an extra fetch + possibly
   an LLM call per `queue` post).
2. **Confirm the `queue` LLM enrichment pass exists/runs** — today `queue`
   posts land in silver with null title/location unless something enriches
   them. Verify whether the deferred enrichment (`--enrich`) covers
   `linkedin_posts`, and if not, scope that gap.
3. **Posts→jobs quality bar:** define when a post is a usable job
   (title + at least one of location/workplace/region) vs when to drop.

**Decision needed:** regex-only vs LLM structured extraction for the
recruiter-region inference, and whether the follow-up is per-post or
per-author (cached). See the plan for the recommendation + DoD.

### CLI source-selection design review — enhancement, not a bug (OPEN 2026-08-25)

**Kind:** enhancement / design decision (no defect). A 3-expert review panel
(CLI ergonomics, Dagster idiom, complexity audit) reviewed how the CLI selects
sources, whether per-source limits are wanted, and whether a `--resume` /
separate `ingest` command is the right recovery model.

**Verdict (panel consensus):** the current CLI is already ~95% of minimal-
viable shape and should NOT be expanded. `pipeline run` (default = 9 active
boards; datasciencejobs opt-in by name via `-b datasciencejobs`), `pipeline
ingest --run-id <id> [-b board]`, `pipeline list-runs`, `pipeline gold`.
`-b/--boards` already accepts repeat/comma/space forms.

**Deliberately NOT building (with reasons):** YAML source config (would be the
repo's 4th config convention, no demand for a single-user tool); per-board
limit flags on the pipeline (leaky — only 4/10 boards page; limits are pages,
not jobs; `scrape <board> --max-pages N` and the `MAX_PAGES` env var already
cover bounded runs); a `--resume <uuid>` flag (reinvents orchestration state-
tracking; recovery is two explicit commands — `pipeline ingest --run-id <id>`
to land what succeeded + `pipeline run -b <failed>` to retry a board); named
presets; Dagster partitions/schedules now (requires a persistent
DagsterInstance/daemon this repo doesn't run — documented future path in
`docs/pipeline-streaming-research.md`).

**Real gap + cheap wins (the "capitalize on Dagster" micro-fixes):**
1. `run_pipeline` uses `raise_on_error=True` (Dagster default), so a single
   failed board aborts the whole in-process run and `scored_jobs`/gold never
   run for the boards that succeeded. Flip to `raise_on_error=False`, surface
   `get_failed_step_keys()` (which boards failed), print a recovery hint, exit
   non-zero. This makes partial failure survivable in one run.
2. Add `retry_policy=dg.RetryPolicy(max_retries=1, delay=30)` to the
   network-bound scrape assets (dominant failure class = transient HTTP/DNS;
   zero RetryPolicy exists today).
3. Fix a pre-existing leak: freework's scrape ignores `_max_pages()` (passes
   `max_pages=None`), so the one limit knob doesn't apply to it.

**When to revisit partitions:** if this becomes scheduled/multi-user or true
streaming lands (see `docs/pipeline-streaming-research.md`), model each board
as a static Dagster partition for native selective runs + backfill. Not YAML.
Full discussion + conclusion in `tasks/plans/cli-source-selection.md`.

### OMP edit tool: silent file corruption via boundary-echo auto-repair (OPEN 2026-08-12)

**Symptom:** The `edit` tool repeatedly mangles files during large or repeated
edits — duplicate function/constant blocks, docstrings truncated mid-string,
code inserted inside an unfinished `CREATE TABLE` call, and payload lines
silently dropped. Each corruption required a read-repair cycle; one file
(`silver.py`) was corrupted four times in a single session before switching to
full-file `write`.

**Observed failure modes (Kimball schema session, 2026-08-12):**
- **Boundary-echo auto-repair drops payload lines.** Repeated warnings of the
  form *"Auto-repaired a replacement boundary echo at line N: dropped M
  trailing payload line(s) identical to the surviving line(s) just below the
  range. The range was one line short of the content you retyped."* The guard
  intends to catch off-by-one ranges that restate keepers — but it also dropped
  genuinely new lines (`_LINEAGE_KEYS` reassignment, `dim_rows` init, a
  `scored_jobs` export, docstring closers), silently changing file semantics.
- **Narrow SWAP leaves the old block alive.** Replacing `ensure_jobs_table`
  while including `upsert_run` in the payload left the *old* `upsert_run`
  below the new one (duplicate def, Python takes the last). A later SWAP that
  was intended to cover the old block only partially consumed it.
- **Mid-construct anchoring.** The line-anchor format made it possible to
  anchor an insertion *inside* an open `con.execute("CREATE TABLE ... ("`
  call, producing syntactically broken code the LSP flagged only later.
- **Stale-tag rejections are the good half.** The tool rejects hunks anchored
  on ranges the model never displayed ("you must re-read first") — this is the
  intended safety and worked; the failures came from *freshly re-read but
  off-by-one* ranges, not stale ones.

**Root cause analysis (Five Whys, from session):** the failures cluster on
large multi-construct SWAPs where the model restates keeper lines (off-by-one
range), and on re-editing a file after the first corruption instead of
switching to full-file `write` (the repo's own `tasks/lessons.md` rule:
*"for any file where an edit has landed wrong once, switch to full-file write
for all subsequent changes"*). The model's behavior, not the tool's checks, was
the primary defect — but the boundary-echo "repair" silently dropping payload
lines turns a rejectable mistake into silent corruption, which is the
harness-level gap.

**Research — how the tool works (2026-08-12):**
- OMP's "Hashline" edit format anchors edits to content hashes per line
  (`[file#TAG]`), not line numbers — stable against line shifts and cheaper
  than full-file str_replace (Bölük's "Harness Problem" benchmark: Grok Code
  Fast 1 success 6.7% → 68.3%; ~61% fewer output tokens on Grok 4 Fast).
  Sources: `blog.can.ac/2026/02/12/the-harness-problem/`, yuv.ai/blog
  (oh-my-pi-omp-explained), betterstack.com/community/guides/ai/
  oh-my-pi-ai-coding-agent/.
- The "boundary echo" guard is the token-efficiency tradeoff: because payloads
  are ranges, not full files, the harness cannot distinguish "model restated a
  keeper (off-by-one)" from "model wants to keep that line" — so it guesses,
  and guessing wrong silently deletes content.

**Status: agent-side fixes implemented 2026-08-12; harness fix still open.**
The agent-behavior and repo-habit fixes below are now codified in
`tasks/lessons.md` (2026-08-12 entry) and applied for the remainder of the
session — the live-warehouse migration and remaining edits used fresh
subprocesses and full-file writes, with no further corruption. A related
session hazard (eval kernel caching a stale module, causing a phantom
`BinderException`) is also logged in that entry.

**Possible fixes:**
- **Agent behavior (implemented 2026-08-12):** one corruption → full-file
  `write`; read the exact target range before every edit; never restate
  keeper lines in a SWAP payload (keep ranges tight, use `INS.POST`/`DEL` for
  pure additions/removals); verify with compile/tests after each edit.
- **Harness (issue for OMP, still open):** boundary-echo repair should
  **reject loudly** (or re-anchor) instead of silently dropping payload lines —
  silent deletion is worse than a rejected hunk; surface the dropped-line
  count in the result so the model can re-issue. Consider a `--dry-run` diff
  preview for large SWAPs, and warning when a single edit spans multiple
  top-level constructs.
- **Repo (implemented 2026-08-12):** keep `tasks/lessons.md`'s full-file-write
  rule; post-edit `git diff --stat` habit for large files (cheap corruption
  detector); verify behavioral changes in fresh subprocesses, never the
  persistent eval kernel.

## Closed

### datasciencejobs scraper: long-running, DNS failure discards ~245 pages (RESOLVED 2026-08-25)

**Symptom:** `datasciencejobs_jobs` is the bottleneck of the full `pipeline
run`. On 2026-08-24 it ran ~2h16m (~345 pages, per-job detail fetch), then
died at page 246 with `httpx.ConnectError: [Errno 11001] getaddrinfo failed`
(DNS). Because the scraper writes results only after finishing the whole
board, the failure threw away ~245 pages of already-fetched results. Worse,
it runs *before* the LinkedIn boards in the graph, so the full run never
reached LinkedIn — `silver.jobs` had 0 rows for `linkedin_jobs`/`linkedin_posts`.

**Action taken (2026-08-24):** removed `datasciencejobs_jobs` from the default
pipeline (`RANKING_ASSETS` in `definitions.py`, `merge.py` deps,
`assets/__init__.py`). The `scrape datasciencejobs` CLI and its
`BOARD_DIMENSIONS` row are kept so it can be run manually; existing warehouse
rows still resolve.

**Fix (implemented, branch `feat/linkedin-source-adapter`):** the scraper now
writes per-page results as it goes (per-job `fetch_detail` failures skip just
that job; a page failure logs and breaks keeping prior pages; a first-page
failure yields `[]`) and finalizes whatever completed before returning — so a
partial run survives into bronze instead of being a total loss. 6 tests. Plan:
`tasks/plans/datasciencejobs-streaming-landing.md`. Re-enable in the default
pipeline only once resilience is confirmed end-to-end (bounded `--max-pages`).

### Pipeline: all-or-nothing ingest — one board's scrape failure blocks all silver/gold (RESOLVED 2026-08-25)

**Symptom:** `silver_upsert` lists every board scrape asset as a `deps`
dependency, so it never runs until *all* boards scrape successfully. A single
board failure (e.g. `datasciencejobs` DNS at page 246) aborts the run before
any ingest — no board reaches `silver.jobs`/`gold.*`, and the retry re-scrapes
*all* bronze even though only one board failed. The 2026-08-24 failure left
both LinkedIn boards empty (0 rows) purely because they ran after the failing
board in the graph.

**Root cause:** the merge step is a single all-board asset. It should be one
asset per board so each source flows bronze → silver independently; a failed
board then blocks only its own row, and other boards reach silver/gold.

**Fix (implemented, branch `feat/linkedin-source-adapter`):** split
`silver_upsert` into per-board assets (`silver_<board>`, via
`make_silver_asset`), each depending only on its own scrape and ingesting only
its own bronze, feeding a shared `scored_jobs`/gold. `--boards` now targets the
per-board silver assets. datasciencejobs stays opt-in only. Verified: 235
tests, fault-isolation + `--boards`-exclusion tests, and a live LinkedIn subset
run. Plan: `tasks/plans/per-board-silver-upsert.md`.

### Pipeline: no resume-from-bronze — orphaned bronze forces re-scrape to ingest (RESOLVED 2026-08-25)

**Symptom:** `silver_upsert` reads bronze entries keyed to `context.run_id`.
If a run dies *after* scraping but *before* ingest, the landed bronze is
orphaned (keyed to a dead run) and there is no CLI to ingest it — recovery
means re-scraping (re-burning Apify credits). Observed 2026-08-25: a LinkedIn
subset run scraped 20 jobs + 45 posts into bronze (run `4e28442a`) then hung
on the DBeaver write lock; recovery re-ran the pipeline and re-scraped LinkedIn
(run `ec3f038b`, 22 + 39), leaving `4e28442a`'s bronze unused in
`data/bronze/` + `runs.json`.

**Root cause:** no way to run `silver_upsert` + downstream against an existing
bronze snapshot under a chosen run id.

**Fix (implemented, branch `feat/linkedin-source-adapter`):** added
`job-search-toolkit pipeline ingest --run-id <id> [--board <b>]` (plus
`pipeline list-runs`), which materializes `silver_ingest` + score/export/gold
from existing bronze without scraping. Also fixed two robustness bugs it
surfaced: `scored_jobs` now ensures its output columns exist and fetches only
columns present in `silver.jobs` (so a fresh/partial warehouse scores instead
of failing). Verified: live `pipeline ingest --run-id 4e28442a-a9aa-...`
recovered 31 orphaned LinkedIn rows with zero new bronze/Apify calls. Plan:
`tasks/plans/resume-from-bronze.md`.

### LinkedIn adapter: deterministic tech scan is a hardcoded list (RESOLVED 2026-08-17)

**Symptom:** The planned LinkedIn source adapter (see
`tasks/plans/linkedin-source-adapter.md`) extracts `technologies` from post/JD
text with a deterministic keyword scan. The proof-of-concept used a hardcoded
Python list of ~25 tokens (`Microsoft Fabric`, `PySpark`, `SQL`, `Azure`, …)
matched with a `\b` regex against the extracted text.

**Why it matters:** a hardcoded list can't follow the user's actual stack
without code changes. The repo's convention is that user preferences live in
`job_search_preferences.yaml` (gitignored), not in code.

**Checklist before wiring the adapter:**
- [x] Confirm the scan is a hardcoded regex/list (it is, as of the spike).
- [x] Replace with a configurable source: a plain-text list (one technology per
      line) referenced from `job_search_preferences.yaml`, loaded once at adapter
      start.
- [x] Match word-boundary, case-insensitive, multi-word tokens intact
      (`Microsoft Fabric` must match as one token, not `Fabric` + `Microsoft`).
- [x] Expand the base list into adjacent/fuzzy variants before compiling one
      regex: multi-word aliases (`Spark` → `PySpark`, `Spark SQL`), case and
      hyphen/space variants (`Power BI` → `power-bi`), plurals and abbreviations
      (`Data Factory` → `ADF`). Sort alternation longest-first; emit the
      canonical keyword, never the variant.
- [x] Fall back to a small built-in default list when no file is provided.

**Resolution:** `job_search_toolkit.linkedin.tech_scan.TechnologyScanner`
implements the file-backed, fuzzy-expanding, word-boundary scanner above
(built-in `DEFAULT_TECHNOLOGIES`/`DEFAULT_SYNONYMS` defaults, `from_file`
synonym lines, longest-first alternation, canonical-only emission). Covered by
`tests/test_tech_scan.py`.

### Resume-Matcher: PDF parser drops work experience (RESOLVED 2026-08-08)

**Resolution:** Not a PDF parser issue. The matcher's refinement/alignment pass
compares the tailored resume against a **master resume**. The default master was
`Jane_Doe_CV.pdf` (a dummy sample with unrelated work experience). The alignment
treated our real experiences as "unfabricated" (not present in the master) and
stripped them all.

Fix: PATCH the master resume with the real YAML data before running improve:
```
PATCH /api/v1/resumes/{master_id}
{"workExperience": [...], "technicalSkills": [...], "summary": "..."}
```
Then re-run improve. Result: 5 work experiences preserved, ATS 82.6, 65KB PDF
with all roles and tailored bullets.

**What we learned about Resume-Matcher's architecture:**
- Uploaded resumes are not automatically the master — the first upload with the
  matcher's UI sets the master; API-uploaded resumes need explicit master setup
- The `PATCH` endpoint writes to `processed_resume`; the improve flow reads from
  `processed_data` which IS the PATCH-ed data (confirmed working)
- The alignment/refinement pass (not the diff LLM) strips experiences that don't
  exist in the master — this is a correctness feature for fabrication prevention
- Structured data injection via PATCH works; the PDF parser bypass is viable
- **Best practice:** PATCH the master with real structured data before tailoring,
  or upload through the matcher's UI which handles master setup automatically

### Resume-Matcher: DeepSeek models fail structured output (RESOLVED 2026-08-07)

**Resolution:** Both deepseek-v4-pro and deepseek-chat (v4-flash-0731) support
`response_format: {"type": "json_object"}` correctly via raw API. Resume-Matcher
integration test with deepseek-chat succeeded — 10s, valid JSON, no truncation.
The original failures were likely transient (matcher client-detection bug or
older v4-flash build). v4-pro is disqualified for structured-output tasks due to
reasoning_content consuming ~65% of the token budget; v4-flash is the recommended
model for resume tailoring.

Fallback models if needed: `openai/gpt-5.6-luna` (OpenRouter, $0.10/$0.60,
Intel 52.3) or `z-ai/glm-5.2` (OpenRouter, $0.206/$0.647, Intel 52.6).
Full model comparison in AGENTS.md CI log (2026-08-07).

### IG pipeline: Superseded by datalake (CLOSED 2026-08-07)

**Resolution:** Neither `ig-pipeline/` nor `datalake/` directories exist in the
repo. The repo was pivoted to an application workspace in commit `eec7cf9` and
those directories were cleaned out. Issue is moot.
