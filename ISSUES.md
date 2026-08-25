# ISSUES.md — job_search_scraping


## Open

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

### datasciencejobs scraper: long-running, DNS failure discards ~245 pages (OPEN 2026-08-24)

**Symptom:** `datasciencejobs_jobs` is the bottleneck of the full `pipeline
run`. On 2026-08-24 it ran ~2h16m (~345 pages, per-job detail fetch), then
died at page 246 with `httpx.ConnectError: [Errno 11001] getaddrinfo failed`
(DNS). Because the scraper writes results only after finishing the whole
board, the failure threw away ~245 pages of already-fetched results. Worse,
it runs *before* the LinkedIn boards in the graph, so the full run never
reached LinkedIn — `silver.jobs` had 0 rows for `linkedin_jobs`/`linkedin_posts`.

**Action taken:** removed `datasciencejobs_jobs` from the default pipeline
(`RANKING_ASSETS` in `definitions.py`, `merge.py` deps, `assets/__init__.py`).
The `scrape datasciencejobs` CLI and its `BOARD_DIMENSIONS` row are kept so it
can be run manually; existing warehouse rows still resolve.

**Fix (batching/streaming — deferred):** the scraper writes to a single
landing file only at the end, so any mid-board failure is a total loss. Stream
results to a per-page landing table (bronze) as they arrive so partial runs
survive; then `silver_upsert` ingests whatever landed. Consider a `--max-pages`
flag to bound runtime. Re-enable in the default pipeline only when resilience
is in place. Plan: `tasks/plans/datasciencejobs-streaming-landing.md`.

### Pipeline: all-or-nothing ingest — one board's scrape failure blocks all silver/gold (OPEN 2026-08-25)

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

**Fix:** split `silver_upsert` into per-board assets (`silver_<board>`), each
ingesting only its own board's bronze, feeding a shared `scored_jobs`/gold.
Update `--boards` selection to target the per-board silver asset. Plan:
`tasks/plans/per-board-silver-upsert.md`.

### Pipeline: no resume-from-bronze — orphaned bronze forces re-scrape to ingest (OPEN 2026-08-25)

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

**Fix:** add `job-search-toolkit pipeline ingest --run-id <id> [--board <b>]`
(plus `--list-runs`) that materializes silver ingest + score/export/gold from
existing bronze without scraping. Plan:
`tasks/plans/resume-from-bronze.md`.

## Closed

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
