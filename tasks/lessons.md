# Lessons Learned

## 2026-08-05: free-work.com scraper — parse_details regex bug

**Problem:** All 119 scraped jobs had empty `start_date`, `duration`, `pay`, `rate`,
`remote_type`, and `location` fields despite the HTML clearly containing this data.

**Five Whys:**
1. Details fields empty → `parse_details` returned `{}` for every card.
2. `parse_details` returned `{}` → the token-split approach never matched a label key.
3. Token-split failed → `cleaned.split()` produced tokens like `Start`, `dateAs`,
   `soon`, `as`, `possible` — no single token equals a multi-word label like
   "Start date" or "Remote type".
4. Multi-word labels were unrecognizable → the tokenizer assumed labels were
   single words separated by whitespace from their values.
5. **Root cause:** The original parser was built against reader-mode output which
   rendered `SVG Image` text nodes that acted as separators. The actual
   `BeautifulSoup.get_text(strip=True)` output glues labels to values with zero
   separators (e.g. `Start dateAs soon as possibleDuration1 year...`). Always
   test parsers against the real bs4 output, never reader-mode or hand-crafted
   inputs.

**Fix:** Replaced token-split approach with a label-anchored regex:
```python
pattern = re.compile(
    rf"({'|'.join(re.escape(k) for k in DETAILS_FIELDS)})\s*(.+?)(?=\s*(?:{labels_pattern})|$)",
)
```
This matches each known label and captures everything until the next label
boundary, regardless of spacing.

**Rule:** When parsing concatenated text-node output from BeautifulSoup, verify
the exact byte output of `get_text()` before designing a parser. The `read` tool's
reader-mode rendering is not the DOM text — it injects artificial "SVG Image" text
that does not exist in the parsed tree. Test parsers with `eval` cells using real
`httpx` + `BeautifulSoup`, not with hand-typed test strings constructed from
reader-mode output.

## 2026-08-05: pipeline — French numeric parsing and heuristic language detection

**Problem 1 — Pay parser silently drops lower bounds:** The regex
`(\d+)\s*k?\s*[€¤]` matched only the last numeric group in a ranged salary
like "40k-75k €", producing (75000, 75000) instead of (40000, 75000). Every
ranged salary was scored on its upper bound only.

**Problem 2 — French heuristic classified 95% of job descriptions as English:**
The `_is_already_english` check looked for French function words and required
>15% match rate. But French technical job descriptions are dense with English
loanwords (data, Python, pipeline, cloud, Spark) that diluted the French signal
below the threshold. 36 of 38 French descriptions were skipped by the heuristic
and their `description_en` was set to the untranslated French text.

**Problem 3 — Narrow non-breaking space crashes float():** French number
formatting uses U+202F (narrow no-break space) as thousands separator
(e.g. "1 260"). `float("1\u202f260")` raises ValueError.

**Five Whys (combined):**
1. Pay scores were all 1.0 for contractor roles → parser dropped lower bounds.
2. Parser dropped lower bounds → regex assumed single values, not ranges.
3. Regex assumed single values → the scraper's `parse_details` didn't expose
   ranges as structured data, so the pipeline never asked "what formats exist?"
4. Heuristic skipped French text → threshold was tuned on general French prose,
   not technical job ads with heavy English terminology.
5. **Root cause:** All three bugs share one cause: parsing assumptions were
   tested against hand-picked examples, not against the full diversity of real
   data. Always dump every unique value of a scraped field before writing a
   parser for it.

**Fix — pay parser:** Split on dash before parsing each bound; use a helper
that strips all Unicode whitespace (`\xa0`, `\u202f`, ` `) before `float()`.

**Fix — French detection:** Invert the heuristic: any French function word at
all → assume French. False positives (sending English to LLM) cost ~$0.001;
false negatives (shipping untranslated French) waste the entire pipeline.

**Fix — narrow NBSP:** Strip `\u202f` alongside `\xa0` and ` ` in all numeric
parsing helpers.

**Rule:** After scraping, dump `set(field)` for every field that will be parsed
numerically or linguistically. Unit-test the parser against every unique value.
A regex that works on 90% of formats and silently corrupts 10% is a data bug,
not an edge case.

## 2026-08-06: ATS pipeline — reimplemented tools instead of running them

**Problem:** User asked to gather repos/containers for ATS resume optimization,
then build a pipeline that fans out to them. Instead of spinning up the actual
Docker containers and hitting their real API endpoints, the agent:
- Wrote a custom CLI bridge (`cli-analyze.js`) for ATSFlow instead of running
  its existing server (`npm start` → Express on port 3101)
- Built a custom FastAPI orchestrator with reimplemented matcher logic instead
  of wrapping the actual services
- Spent ~2 hours writing code that duplicated functionality already available
  in the researched containers

**Five Whys:**
1. Agent wrote custom code instead of using existing containers → container
   setup felt like "infrastructure work" while coding felt like "the real task."
2. Coding felt like the real task → agent defaulted to builder mode after
   research phase, treating the research as background for implementation.
3. Builder mode default → the research output (16 repos cataloged) was
   treated as a specification to implement against, not as a set of tools
   to invoke.
4. Research-as-spec not research-as-inventory → the user's phrase "use all
   of them in a federated way" was interpreted as "understand their rules
   and build a federation layer" rather than "spin them up and hit them."
5. **Root cause:** After completing the research phase, the agent did not
   stop and ask "are these runnable as-is, or do I need to build anything?"
   Instead it proceeded directly into implementation, defaulting to writing
   code over configuring infrastructure.

**Rule:** When research produces runnable artifacts (Docker images, PyPI
packages, CLI tools), the next step is ALWAYS to run them — not reimplement
them. Only build custom code when:
- The tool has no API/server mode and cannot be wrapped
- The tool's I/O contract is incompatible with the pipeline
- The tool is unmaintained/broken and cannot be fixed trivially

Before writing any integration code, verify each tool with one real request
(per the External Integration Gate in AGENTS.md). If a tool works, wrap it
with the thinnest possible HTTP client or subprocess call.

**Corollary:** ATSFlow's `npm start` server exposes `/api/analyze` on port
3101. The agent should have run `npm install && npm start`, hit the endpoint,
and wrapped it — not written a 140-line CLI bridge that reimplements section
detection and globals bootstrapping.

**Fix:** The pipeline's matcher layer should be a thin client that POSTs to
running services. The orchestrator (fan-out, panel review, metrics logging)
is legitimately new — no existing repo does it — but the matchers themselves
should be the real tools, not reimplementations.

## 2026-08-06: ATS pipeline — LLM rewrites fabricate content despite no-fabrication prompts

**Problem:** The DeepSeek rewriter prompt explicitly forbade fabricating
skills, metrics, or experience ("Do NOT fabricate... If the original says
'improved performance', rewrite as 'Improved performance' — do NOT invent
'Improved performance by 30%'"). DeepSeek ignored this: it invented
"processing millions of records daily", "reducing data errors by 40%",
"99.9% uptime", and added skills like Medallion architecture, Delta Lake,
and Apache Spark that the original resume never claimed.

**Root cause:** Prompt-level rules are advisory for LLMs — they optimize for
plausible, complete-looking output, and fabricated metrics make a resume
look better. No instruction can reliably prevent this.

**Fix (mandatory):** Deterministic post-rewrite alignment strip. After the
LLM rewrite, compare the output against the original:
- Skills: remove any skill token in the rewrite not present in the original's
  Skills section OR its full text (substring check). Skills mentioned anywhere
  in the original are verifiable.
- Metrics: strip quantified claims (%, $, Nx, "over N", "N records/reports/
  users/...") whose numbers don't appear in the original's legit numbers
  (dates, phone, years).
- Log every strip decision to the metrics file so the user can review.

This mirrors Resume-Matcher's `validate_master_alignment` (Pass 3) — the one
design decision that repo got right and that makes its output trustworthy.

**Rule:** Never ship an LLM rewrite of a resume without a deterministic
alignment pass. Prompt rules are insufficient; verify against the source
document with code.

## 2026-08-06: `edit` tool corrupted run.py four times — full write only

**Problem:** Four consecutive `edit` calls on `src/ats_pipeline/run.py`
landed on the wrong anchors, replacing function bodies and constants with
unrelated code (a REWRITER_SYSTEM copy inside a function, bare statements
inside a list literal, a deleted function header). Each corruption required
a full rewrite.

**Root cause:** `edit` line anchors are fragile on files with repeated
similar content (multiple `SWAP N.=M` hunks, text patterns appearing in
several places). Once the file was corrupted once, subsequent anchors
were computed against stale snapshots and drifted further.

**Rule:** For any file where an `edit` has landed wrong once, switch to
full-file `write` for all subsequent changes — read the entire file, apply
the change mentally, write the complete new content. Do not attempt
another surgical edit on that file.

**Also:** Always run a syntax check (`python -c "import ast; ast.parse(...)"`)
after writing a Python module, before running it.

## 2026-08-06: Pivot — skills and workspaces replace the ATS pipeline

**Problem:** After the federated-ATS reimplementation debacle, the repo still
carried a 15KB custom orchestrator, a vendored ATSFlow fork, two matcher
services, and fifteen test-run artifacts — all validated against a fictional
"Alex Chen" resume. That machinery was batch-pipeline-shaped for a workflow
that is actually interactive and per-role: shortlist → read JD → research
company → tailor → manual review. The user's volume (a handful of real
applications) cannot justify a fully automated pipeline, and the user's own
words said so from the start.

**Five Whys:**
1. Infrastructure kept growing → the agent defaulted to "build a system"
   whenever the workflow was described.
2. "Build a system" was the default → there was no question asked about
   volume ("how many items flow through this per week?").
3. No volume question → automation was applied uniformly, including at the
   narrow end of the funnel where judgment dominates.
4. Uniform automation → pipeline code replaced human gates and review, which
   is exactly where fabrication and misalignment creep in.
5. **Root cause:** No architectural principle separated "batch work that
   automates" from "judgment work that assists". The system was designed as a
   pipeline because that is what pipelines are built for — the workflow was
   never the input.

**Update (the pivot):** This repo is now an application workspace.
- Agent skills (`.agents/skills/`) are the only orchestration layer: playbooks
  with explicit human gates, invoking existing scripts, Docker, and web tools.
- Docker services are ephemeral: `docker compose up` inside the tailor skill,
  `down` when the session ends — nothing always-on.
- Resume-Matcher is an advisor (seam option B): its `detailed_changes` diff
  log is human-reviewed; approved changes go into a tailored RenderCV YAML
  copy; its exported PDF is never the submission artifact.
- The fabrication guard lives on: `scripts/audit_alignment.py` (salvaged from
  the old orchestrator) strips claims the master can't support, exit 1 = stop.
- Personal data is gitignored (`resume/`, `applications/`, `tracker.csv`,
  `rendercv_output/`) because the repo is PUBLIC — and the ignore rules were
  committed before any real content existed (gitignore is not retroactive).

**Rule:** Before building automation for a step in a personal workflow, ask
"does this step process >50 items, or is it judgment on one item?" Batch work
gets pipeline code; judgment work gets a skill (procedure + human gate) that
orchestrates tools. A workflow is not a pipeline until volume proves it is.

## 2026-08-06: Dry-run execution — two discovery-layer findings

The jd-refresh step of the dry run ran live against the real pipeline and
surfaced two issues. Both are logged here per the continuous-improvement rule.

**Finding 1 — Skill snapshot path broke on Windows git-bash.**
The jd-refresh skill instructed agents to snapshot jobs_ranked.csv to `/tmp`,
but `/tmp` does not exist on this machine's git-bash (C:/tmp missing) — the cp
failed. Fixed in the skill: snapshot to `data/_tmp_jobs_ranked_prior_*.csv`,
which the global `*.csv` gitignore rule keeps out of the repo.
**Rule:** agent skill playbooks must assume Windows git-bash, not POSIX: no
`/tmp`, no `/dev/null` idioms; use repo-local gitignored paths for scratch files.

**Finding 2 — Classification structured-output calls fail validation.**
The vertical_classified stage hit `Max retries exceeded` on every record: 4
validation errors (missing fields) because the model's tool-call arguments were
invalid JSON. Probe (2026-08-06): `POST /v1/chat/completions` with
`model=deepseek-chat` returns a completion whose served model is
`deepseek-v4-flash` — the alias now points at the v4-flash family upstream, and
v4-flash emits malformed function-call JSON (`"end_client_sector": other`
unquoted, arguments double-wrapped as a JSON string). The classification stage
was tuned when deepseek-chat meant the previous generation. Impact: end_client
fields stay empty for newly enriched jobs (they were already ~86% empty);
engagement_type and scoring unaffected; existing records keep prior values.
Pending decision: harden llm_client.py structured-output calls with a
JSON-repair fallback (parse arguments string; on failure retry with JSON
embedded in the prompt instead of a function call) — discovery layer change,
awaiting user approval per plan negative space.
**Rule:** vendor model aliases move under you. Before a batch enrichment run,
probe the served model for the configured alias once (one tiny completion) and
compare against the model the prompts were tuned on. Structured-output calls
need a repair fallback, not a retry loop — retrying the same malformed
generation pattern burns attempts without fixing anything.

**Dry-run status:** jd-refresh complete and verified live; Wave 3 verified all
tailoring endpoints with real traffic; master resume rendered. The remaining
steps (new-application scaffold, tailor-resume diff review, tracker) are
human-gated by design — the shortlist pick and go/no-go are the user's, and the
agent must not simulate them.


## 2026-08-08: Resume-Matcher — verified pipeline success, ignored content validity

**Problem:** After fixing the DeepSeek structured-output issue, the matcher
returned HTTP 200 with valid JSON, an improved ATS score (87.3), and a PDF
artifact. I declared success. The user looked at the PDF and found it was
1 page with no work experience — the single most important section of a resume.

**Five Whys (technical):**
1. PDF had no work experience → `resume_preview.workExperience` was `[]`.
2. Work experience was stripped → the matcher's refinement/alignment pass
   removed them.
3. Alignment pass stripped them → they didn't exist in the **master resume**
   that the alignment validates against.
4. Master resume had unrelated data → the default master was `Jane_Doe_CV.pdf`,
   a dummy sample with completely different work history.
5. **Root cause:** The matcher's refinement pass compares tailored output against
   the master resume to prevent fabrication. When the master has unrelated data,
   real experiences are treated as "unfabricated" and stripped. Fix: PATCH the
   master resume with real structured data before running improve. This is a
   correctness feature, not a bug — it prevents the LLM from inventing roles.
   The gap was that our API-driven workflow never set up the master correctly.

**Five Whys (agent):**
1. I didn't catch the empty work experience → I never opened the PDF.
2. I didn't open the PDF → I verified pipeline success (HTTP 200, valid JSON,
   ATS scores) and stopped there.
3. Pipeline verification felt sufficient → the artifact was produced without
   errors.
4. No explicit content-verification step → the workflow checklist didn't
   include "open and inspect the output document."
5. **Root cause:** Pipeline success is not domain correctness. A resume without
   work experience is not a resume — no amount of JSON validity or ATS scoring
   compensates for a document that fails its purpose.

**Rules:**
- After any pipeline that produces a human-facing artifact (PDF, report,
  resume, dashboard), open the artifact and verify domain-essential content
  before declaring success.
- When the artifact is sparse or empty, trace the full dependency chain before
  attributing blame. The initial "PDF parser failure" diagnosis was wrong —
  the parser extracted all 12KB of text correctly. The stripping happened in
  the alignment pass against a misconfigured master resume.
- Before tailoring in Resume-Matcher via API: PATCH the master resume with
  real structured data. The alignment pass is a fabrication guard that will
  strip any content not present in the master.

## 2026-08-11: medallion warehouse — Phase 2 burned cycles on tooling rework, not design

**Problem:** The silver-layer implementation (silver.py + 5 asset modules + migration,
~1200 LOC) took far longer than the code volume justifies. Roughly two-thirds of the
wall-clock was self-inflicted rework: ~8 tooling failures (edit-tool file mangling ×6,
stale kernel bytecode ×2, Dagster context-annotation rejection ×2 waves, wrong relative
imports, a silent no-op bash replace, a pragma column-index bug). The genuine design
work (mapping plan columns to real code fields, gate predicates against real data,
ON CONFLICT vs INSERT OR REPLACE, composite PK, run-scoped gold views, migration
ordering with the CSV backfill) was only ~a third of the time and went smoothly once
the data was inspected.

**Five Whys:**
1. Phase 2 took long -> repeated tooling failures, each costing a debug-rewrite-verify cycle.
2. Failures recurred -> each was patched as a one-off instance instead of recognizing the
   failure class (e.g. the future-annotations issue hit 6 asset modules; fixed one at a time).
3. Classes not recognized -> tight edit -> compile -> next loops, no pause to ask "same
   failure as before?" — lessons.md already documented the edit-tool hazard (2026-08-06)
   and was read at session start, but not reapplied when the first mangle recurred.
4. Rapid-fire mode -> user signaled impatience; interpreted speed as more tool calls per
   minute instead of fewer failed calls per change.
5. **Root cause:** optimized for visible progress (tool calls, file writes) instead of
   defect-free execution. Under pressure the right response is to compress the NUMBER of
   edit-verify cycles (batch systemic fixes, full-file writes, verify tool contracts once),
   not increase their frequency.

**Rules (this session's, appended to existing):**
- `edit` tool has now mangled files 6+ times on this machine. Default to full-file `write`
  for Python modules; for targeted changes use exact-string replace scripts with fail-loud
  asserts (`assert old in t` before `t.replace`), and verify with grep from a FRESH process.
- The eval kernel caches module bytecode AND DuckDB connections per file path. After any
  file edit, verify with `uv run python -c` fresh processes, not the kernel. Use unique
  temp DB filenames (Windows file locking + duckdb connection cache).
- Systemic-first: when a failure class repeats, read the library source once and batch-apply
  (Dagster validates context annotations by identity — `from __future__ import annotations`
  makes them strings, so drop the future-import in asset modules; relative-import depth is
  a 30-second check of the package tree).
- Pressure response: speed = fewer failure cycles, not more tool calls.
- `dg.materialize([...])` runs only the selected assets (not the dep subtree) in this
  Dagster version. `PRAGMA table_info` returns (cid, name, ...) — r[1] is the name; it
  errors if the table doesn't exist — guard with information_schema first.

## 2026-08-12: edit boundary-echo auto-repair silently drops lines + phantom module in eval kernel

**Problem 1 — boundary-echo "auto-repair" corrupts files silently.** During the
Kimball schema refactor, `edit` mangles recurred (silver.py ×4, merge.py,
test_warehouse.py) despite the 2026-08-06/08-11 rules. The new mechanism: a
`SWAP` whose payload restates a keeper line just past the range (an off-by-one
range) triggers the tool's boundary-echo guard, which "repairs" the hunk by
**dropping payload lines** — including genuinely new ones (`_LINEAGE_KEYS`
reassignment, `dim_rows` init, a `scored_jobs` export, docstring closers).
Result: the file compiles or runs with subtly wrong semantics, no loud error.
Worse than a rejected hunk: a rejection forces a re-read; a silent drop ships.

**Problem 2 — phantom module: eval kernel served a stale `silver`.** The live
warehouse migration failed with `BinderException: company_id not found` even
though `inspect.getsource` of the on-disk module showed the ALTER line present.
The persistent eval kernel had imported `silver` BEFORE the ALTER fix and kept
the cached module; a hand-written step-by-step repro "passed" because it never
called the cached `_migrate_company_info`. The migration worked on a fresh
subprocess (`uv run python -c`). ~4 debug cycles burned on a ghost.

**Rules (extend the existing edit rules):**
- **Never restate keeper lines in a SWAP payload.** Ranges are tight: touch
  only lines whose content changes. Pure additions → `INS.POST`/`INS.PRE`;
  pure removals → `DEL`. A payload that echoes a surviving line invites the
  boundary-echo repair that silently drops it.
- **Read the exact target range before every edit.** The edit tool rejects
  hunks anchored on ranges never displayed (good); the failures came from
  *freshly re-read but off-by-one* ranges — count the lines you actually
  display and make the range match, or use `SWAP.BLK` for whole constructs.
- **After any multi-hunk edit, verify with `git diff --stat` + a compile or
  the targeted test from a fresh process.** Cheap corruption detector.
- **After one mangle on a file, full-file `write` for every later change**
  (2026-08-06 rule) — and stop using `edit` on that file entirely.
- **When a repro contradicts the on-disk source, suspect the eval kernel's
  cached module first.** Verify with `uv run python -c "import inspect;
  print(inspect.getsource(mod.fn))"` in a fresh process. The eval kernel
  caches modules AND DuckDB connections per file path (2026-08-11 rule) —
  migrations and behavioral checks run in fresh subprocesses, never the kernel.
- **Never run schema migrations against the live warehouse first.** Copy
  `data/warehouse/jobs.db` to `data/_tmp_backup/`, migrate the copy, verify,
  then apply to live (backup rule from the Kimball plan; ISSUES.md Open entry
  documents the full failure taxonomy + harness-side fix proposal).


## 2026-08-12: Kimball migration — first-wins merge loses legacy data

**Problem:** The `_migrate_company_info` first-wins behavior (dim row = first job
seen per company in heap order) produced 1,992 dim rows but silently dropped
37 jobs' hq_country and homepage_url because the first-seen row for those
companies was sparser than other rows.

**Five Whys:**
1. Equivalence check showed per-job company_info diffs → first-wins picked
   arbitrary (heap-order) research snapshots.
2. First-wins is order-dependent → DuckDB heap order favours oldest-inserted jobs,
   whose research was earliest (potentially stalest).
3. Legacy per-row research was inconsistent (2.6 jobs/company, different research
   runs produced different field fills) → arbitrary single-row pick is wrong seed.
4. The plan said "INSERT INTO dim_company SELECT DISTINCT" without specifying
   canonicalization semantics → code defaulted to first-wins.
5. **Root cause:** No merge rule was specified; first-wins is implicit and
   fragile when the source data is heterogeneous.

**Fix:** `_merge_company_ci()` — most-recent-last_seen first, then field-wise
first-non-NULL merge. Deterministic (pure function of the row set), lossless
(no value present in ANY row is dropped). Downgrade count: first-wins = 132
field-level losses, merge = 16 (all org_type, where newest research says
'unknown' and older said 'private' — intentional: newer research wins).

**Rule:** When canonicalizing per-entity from heterogeneous per-row snapshots,
make the merge rule explicit: order by recency (last_seen_at), merge field-wise
first-non-NULL. Document in the function, test it with multi-row fixtures.

**Also:** exports.py `json_object` used Postgres-style `'key': value` syntax
that DuckDB 1.5.5 rejects — fixed to positional `'key', value` pairs. The
existing test suite never exercised the export SQL path; add a regeneration
regression test that executes `_COMPANY_INFO_JSON` against a real DuckDB.


**Also — sequencing:** The ROADMAP.md edit was made in the working tree after
the branch push but before the PR squashed; it never landed on the feature
branch, so a direct-to-main commit was needed afterwards. The process fix:
all doc updates that reflect branch work must be committed to the branch
(not the working tree) before the PR merge. ``git stash`` + checkout branch
+ ``git stash pop`` + commit before merging, or open a follow-up PR for
post-merge cleanups.

## 2026-08-12: edit tool boundary-echo corruption — follow-up

The exports regression test addition hit the same boundary-echo class from
the morning session: SWAP mangle on tests/test_warehouse.py left duplicate
SQL blocks + a broken con.execute() call. Full-function delete + rewrite was
the reliable fix — reinforces the rule: after one mangle on a file, full-file
write or full-function rewrite for that function; never attempt to patch the
patch.

## 2026-08-23: incremental gate "terminal" marker must be a persisted column value

**Problem:** A subagent building the deferred `linkedin_post_enriched` LLM
asset claimed its unfillable rows were "marked terminal via
`_enrichment["post_enriched"]` so they are not retried". That flag lives in
the `_enrichment` dict, which is in silver.py's `_SKIP_KEYS` and never becomes
a warehouse column. The gate selects on `title = '' OR location_raw = ''`.
So a post the LLM genuinely couldn't fill kept `title = ''` forever, the gate
re-selected it every run, and the LLM was re-queried on the same unfillable
post indefinitely. The subagent's 7 tests passed because they asserted the
within-run flag, not the cross-run persistence that the gate actually depends
on.

**Root cause:** the "processed" sentinel was written to a transient,
non-persisted dict instead of a persisted column the gate reads. A NULL/empty-
based incremental gate can only be terminal if the sentinel is a real column
value (or the gate checks a persisted flag).

**Fix:** coerce unfillable `title`/`location_raw` to the non-empty sentinel
`"unknown"` — consistent with the existing `org_type = 'unknown'` convention
("researched, nothing found") — so the empty-based gate stops selecting the row.

**Rule:** any "processed once, don't re-select" marker for a NULL/empty-based
gate MUST be a persisted column value (or a persisted flag column the gate
references). Transient `_enrichment`/`_source` dict flags are lost between
runs and can never serve as a gate terminal marker.

**Corollary (delegation):** subagent self-reports describe the HAPPY intent
("marked terminal so not retried"), not the actual persistence semantics.
Verify the gate's terminal condition against the warehouse schema (which keys
are persisted) — not just the subagent's green tests — before accepting.

## 2026-08-25: global deactivation made subset runs unsafe — replaced with time-based staleness

**Problem:** `deactivate_not_seen` ran in `silver_upsert` and flipped
`is_active = FALSE` for every job whose `last_seen_run != current_run`. That
is correct for a full multi-board run, but any *subset* run (e.g. LinkedIn
only) deactivated ALL of the other boards' rows — so the pipeline was forced
to always run every board. When `datasciencejobs` (the long-running bottleneck)
failed mid-run, the full run never reached LinkedIn, leaving both LinkedIn
boards empty in the warehouse, and the only fix was to keep running everything.

**Root cause:** "likely gone" was modeled as a binary, run-relative state
(`is_active` computed against the latest run's board membership) instead of as
a time-based signal that is independent of which boards a given run scraped.

**Fix:** jobs are never deactivated. `is_active` stays TRUE once seen, and
staleness is inferred from `last_seen_at` against a `STALE_AFTER_DAYS` horizon
(60). Gold views (`ranked_jobs`, `by_sector`, `by_tier`) filter on
`days_since_seen <= horizon` and expose `days_since_posted`/`days_since_seen`
live; `disappeared_this_run` = not seen within the horizon. A `freshness`
decay factor was added to `score_engine.py` (penalizes old posts and stale
last-seen, applied multiplicatively so the five tuned quality weights are
untouched). With deactivation gone, subset runs are safe — a board not scraped
simply stops refreshing `last_seen_at` and eventually falls out of the gold
views. Added `pipeline run --boards <board>...` (uses Dagster `materialize(
assets, selection=...)`, which materializes only the selection — upstream
assets are loaded, not re-run).

**Rule:** don't model "gone" as a run-relative binary state when sources are
scraped on independent schedules. Prefer a time-based staleness signal computed
at read/rank time; it makes subset runs safe and folds naturally into ranking.

**Windows/DuckDB sharp edge:** DuckDB is single-writer. If the warehouse file
is open in DBeaver (or any reader), `pipeline run`'s `silver_upsert` hangs on
`connect()` (`IOException: file is being used by another process`) and the run
times out *after* the scrape assets have already written bronze. The scraped
data is not lost (bronze + runs.json), so a re-run after releasing the lock
ingests it. Close the DBeaver connection before any pipeline run.

## 2026-08-25: partial-run fixes surfaced two scoring-robustness bugs + a worktree edit gotcha

**Problem:** adding a `pipeline ingest` recovery path (ingest an orphaned
bronze run_id without re-scraping) surfaced two latent bugs in `scored_jobs`
that the normal full run never hit because the real warehouse always had every
column:

1. `reset_stale`/`_update` write `overall_score`/`scores`/`recommendation_tier`,
   but `silver.jobs` is created from incoming bronze columns, which don't
   include scoring outputs. On a fresh or partial warehouse those columns don't
   exist and scoring fails (`Binder Error: Referenced update column
   overall_score not found`). Fix: `scored_jobs` calls `_ensure_scoring_columns`
   (idempotent `ALTER TABLE ADD COLUMN`) before reset/write.
2. `fetch_jobs(SCORE_COLUMNS, GATE_SCORE, ...)` referenced canonical columns
   (e.g. `seniority_level`) that a partial bronze doesn't create
   (`Referenced column seniority_level not found`). Fix: fetch only the
   `SCORE_COLUMNS` that exist in `silver.jobs` (score_engine tolerates missing
   fields as neutral).

**Root cause:** the scoring asset assumed the warehouse always has its input
and output columns because a full scrape run always created them. Any path that
scores a partial/fresh table (ingest recovery, or a first run on a fresh DB)
breaks. An asset must guarantee its own output columns and tolerate a subset of
input columns.

**Rule:** an asset that writes columns must ensure they exist (idempotent
`ADD COLUMN`), and an asset that reads a column set must intersect it with what
actually exists in the table. Never assume the table has every canonical
column — a partial ingest only creates the columns its records carry.

**Worktree edit gotcha:** while implementing in a git worktree, the `edit` and
`write` tools resolve relative paths against the SESSION cwd (the main
checkout), not the worktree path in `cd`. Two "successful" `edit`/`write` calls
to `src/.../score.py` actually modified the MAIN checkout's file and left the
worktree untouched (verified by `grep` on the worktree showing no change) —
costing a confusing false-failure loop. Fix: for worktree edits, use full
absolute worktree paths with the tools, or write the file via `bash` heredoc
with an explicit `cd` to the worktree. Verify edits with `grep`/`git diff` on
the exact worktree path afterward.

---

## Use existing client libraries for APIs — never hand-roll HTTP

**Date:** 2026-08-25

**Trigger:** during the LinkedIn source spike I diagnosed Apify via raw `httpx`
REST calls (curl-equivalent) instead of the official `apify-client` SDK, and
even initially dismissed community actors as "not runnable" because the raw
slug-based API 404'd (they weren't in the account yet). The user flagged this:
we use Apify as a source, so there's no reason not to use its client library.

**Rule:** when integrating with an API that ships an official client library,
use that library — never duplicate its HTTP/transport layer by hand. This is a
standing rule:
- `apify` → `apify-client` (installed; `ApifyBackend` now uses
  `actor().start` / `run().wait_for_finish` / `dataset().iterate_items`).
- Prefer the SDK even for spikes/diagnostics; it's a thin wrapper over the same
  REST API and resolves account-scoped actors correctly.

**Corollary:** the raw slug-based API 404 was an account-ownership signal, not
a "broken actor" one. With the SDK (or after adding the actor to the account),
the same actor resolves and runs. Verify API access via the client, not by
guessing slugs against a REST endpoint.
---

## 2026-08-29: WS1 outcome loop — edit-mangle dropped a used constant; subagent runtime cap

**Problem 1 — a silent constant deletion that only the smoke caught.** Building
`gold.score_calibration` (Epic 1.2), an `edit` added the `CALIBRATION_FEATURES`
block in the same hunk where the module constant `_STALE` was defined — the edit
dropped `_STALE` and the file still compiled. `pipeline gold` failed with
`NameError: name '_STALE' is not defined` at the `disappeared_this_run` view.
The full test suite did not exercise that real-warehouse view path, so only the
`pipeline gold` smoke surfaced it. A second mangle pulled `latest_run`'s body
into a newly inserted function (repaired with a clean block rewrite).
**Rule:** after any multi-line `edit` that touches a region with module
constants, re-read the region and run the module's execution smoke (`pipeline
gold`), not just `pytest`. A compiling file with a silently-dropped used symbol
is the edit-tool's signature failure on this machine.

**Problem 2 — the 10-min subagent runtime cap vs slow dagster import.**
`import dagster` is ~180 s cold on this machine. Four of five substantial WS1
subagents hit `task.maxRuntimeMs=600000` mid-work: their edits landed but were
unverified and often wrong (a dropped constant; a calibration test fixture that
mirrored a code bug — both the test and code used `silver.jobs.job_id`, but the
real schema column is `id`). A subagent reporting "code written" was frequently
NOT done.
**Rules:** (a) don't accept a subagent's self-report of completion — run the
module smoke + the tests yourself; (b) when a subagent's code and its test share
the same wrong assumption (e.g. both query `job_id`), the test won't catch it —
cross-check the fixture schema against the REAL schema (`silver.jobs` columns
come from `ensure_jobs_table`, the PK is `id`); (c) split implementation work so
a subagent can finish + verify within 10 min, or budget for finishing the tail
after the cap aborts it.

**Problem 3 — orphaned test processes hang the warehouse.** `timeout` on
git-bash does NOT kill the python child; each timed-out `uv run pytest` left an
orphaned `python.exe` still importing/connecting. Accumulated orphans contended
and one held the DuckDB warehouse open (single-writer) — `connect()` hangs.
The full-suite slowness (~8 min) was traced to dagster's ~180 s cold import,
not a regression.
**Rules:** check `ps aux` for stray `pytest`/`python` before diagnosing a hang
as a code regression; kill orphans (taskkill/powershell) — they lock the
warehouse and slow every subsequent run; warm dagster (`uv run python -c
"import dagster"`) before timing a suite. See WATCHDOG.md.

## 2026-08-29: WS5 — subagent LLM-provider credit failure + test-helper schema gotcha

**Problem 1 — subagents hit a provider credit wall.** Three WS5 implementers ran
in parallel (Epics 5.1/5.2/5.3); 5.1 and 5.2 finished cleanly, but a corrective
subagent to rewrite a mangled test file failed immediately with
`[openrouter/z-ai/glm-5.3-flash] 402 ... exceed your available credits ...
retry-after-ms=120000` — it did nothing (248ms). The subagent LLM provider was
out of credits, so re-dispatch kept failing; the fix had to be done by the
orchestrator inline.
**Rule:** before assuming a subagent failure is a code issue, check whether it
returned an infra/credit error (402, rate-limit). A provider credit outage is an
environment fault, not a spec gap — re-dispatching burns retries; right-size to
an inline fix when the intended change is small and fully specified.

**Problem 2 — the edit tool mangled `tests/test_job_report.py` mid-write** (dead
code after `return`, a missing `def test_per_job_report_missing_fields():`
header leaving an orphaned docstring → IndentationError). The agent had been
warned to use full-file write; it didn't, and the tool corrupted the file. Only
`ast.parse` caught it (the file was otherwise plausibly shaped).
**Rule:** the full-file-write-after-any-mangle rule applies to TEST files too,
and `ast.parse` every file a subagent produces before running it — a mangled
test file is a silent no-op at best, a false-red at worst.

**Problem 3 — test helper passed `company_id` into `ensure_jobs_table`.** The
temp-warehouse helper in `test_job_report.py` added `company_id` to the job
dicts fed to `ensure_jobs_table`, whose DDL already adds `company_id` → DuckDB
`Catalog Error: Column with name company_id already exists!`. Fix: build the
schema from jobs WITHOUT `company_id`, then insert rows that carry it.
**Rule:** `ensure_jobs_table` derives its DDL from the incoming dict keys and
always appends `company_id` itself — never pass a job dict that contains
`company_id` to it. Cross-check test fixtures against the REAL `silver.jobs`
schema (PK `(id, source_board)`), per the standing WS1 rule.


## 2026-08-29: WS7 BD/CRM — edit-tool mangled on 5 files + a no-producer gap the reviewer caught

**Problem 1 — the edit tool mangled across 5 files this session (7.1 subagent code verified clean; my own 7.2 edits kept mangling).** Every multi-line edit risked silently dropping a line. Observed variants: an append-after-`return jobs` DROPPED the `return jobs` itself (score_engine.py — caught by grep, not by the compile, which still parses); an add-command edit dropped the PRIOR function's tail AND duplicated `app.add_typer(...)` (cli.py bd group, twice); a `_BD_VIEWS` tuple edit dropped the relationship view's tail + duplicated a closing paren (gold.py); a test edit dropped the assertion line (test_lead_scoring.py); a workstreams.md edit dropped the `## WS7` header + duplicated the whole block. The earlier rules held: ast.parse catches syntax breaks but NOT a silently dropped used symbol or a dropped function tail — only a behavioral smoke (pipeline gold / bd leads / a grep) does.
**Rules (reinforced):** (a) after ANY multi-line edit that inserts before/after an existing construct, grep/re-read the anchor region for the kept lines; (b) when the edit tool reports "operation needs »" or "no change" or does a fuzzy no-marker replace, STOP and re-read — those are the mangle signals; (c) for structural rewrites (a tuple, a block, a header) use full-file `write` or a fail-loud python splice, not the edit tool (I spliced workstreams.md via line-indexed python after the edit dropped the header).

**Problem 2 — B1 (reviewer): lead scoring had no warehouse producer.** I implemented score_leads + gold.lead_rank/lead_score_calibration + the write helper (upsert_lead_scores), but NOTHING in src/ called it — `bd leads` printed none, `pipeline lead-score-report` showed 0 leads. The engine + views existed; the integration (fetch BD tables -> build lead dicts -> score -> write silver.lead) was missing. A fresh-context reviewer caught it.
**Root cause:** I satisfied the plan's "engine + view" acceptance items but skipped the "produces lead_score from the tables" path (a producer). Views over data are dead ends until something populates the underlying table.
**Rule:** for any "second consumer / gold view over X" feature, the DoD must include a producer that reads the source tables and writes the output table, wired to a CLI/asset AND exercised end-to-end by a test (BD tables -> silver.lead -> gold.lead_rank). A contract test that only exercises score_leads() on hand-built dicts won't catch a missing producer.

**Problem 3 — subagents worked this session (no credit wall), but only after the orchestrator verified every slice.** SilverBD/GoldBD/CliSkill each self-reported "pure append / 0 deletions" and each was genuine (I confirmed 0 deletions + ran the tests). The subagent self-reports were accurate this time, but I still re-read + re-tested per the standing rule.
