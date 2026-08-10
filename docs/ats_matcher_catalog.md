# ATS Matcher Catalog

Researched 2026-08-05. **Historical research record** — kept for reference;
superseded by the workspace architecture (see AGENTS.md and README.md).

> Status as of the 2026-08-06 pivot: ats-resume-checker and the ATSFlow 30-rule
> scanner were **retired** (containers removed, services deleted). The
> workspace runs Resume-Matcher only, as an advisor (seam option B); ATSFlow's
> 30-rule scan is at most a one-time manual lint of the master template.

## Status (2026-08-06)

| Tool | Docker status | Verified? | Why deferred (if not running) |
|---|---|---|---|
| ats-resume-checker | **RUNNING** (port 8001) | ✅ health + real `/score` | — |
| ATSFlow 30-rule scanner | **RUNNING** (port 3101) | ✅ health + real `/analyze` | — |
| ats-resume-scorer | **FUTURE STATE** | — | Correlated with the statistical group (better skills DB than TF-IDF, but the LLM auditor already covers skills/keyword matching semantically) |
| Resume-Matcher | **RUNNING** (port 8000, image `srbhr/resume-matcher:latest`) | ✅ full loop smoke-tested 2026-08-06: upload → job → improve (score 73) → PDF | — |
| ats-resume-improver | Skipped | — | Browser-only, no API/server mode; rule set overlaps ATSFlow |

**Decision (2026-08-06, revised):** the target is JD + resume in → tailored resume out.
Resume-Matcher delivers that end to end (upload → job → improve → PDF), verified live
with a DeepSeek key; ats_score (73 in smoke test) is the threshold signal. The LLM
panel-of-experts orchestration is dropped. ats-resume-scorer and ats-resume-improver
stay skipped; ats-checker + ATSFlow remain running but are optional lint for
sub-threshold resumes.

---

## Runnable Services

### 1. Resume-Matcher (★28,039)
- **Repo:** https://github.com/srbhr/Resume-Matcher
- **Language:** TypeScript (Next.js frontend) + Python (FastAPI backend)
- **License:** Apache 2.0
- **Run:** Docker Compose or `git clone` + build
- **Status:** RUNNING (verified 2026-08-06 — see Status section)
- **How to invoke:** `docker run -d --name resume-matcher -p 8000:8000 -v resume_matcher_data:/app/backend/data -e LLM_PROVIDER=deepseek --env-file .env srbhr/resume-matcher:latest`
- **Key endpoints (verified live 2026-08-06; note the `/resumes` router prefix):**
  - `POST /api/v1/resumes/upload` — resume file (PDF/DOC/DOCX only, content-type checked) → `resume_id`
  - `POST /api/v1/jobs/upload` — `{job_descriptions: [...], resume_id?}` → `job_id[]`
  - `POST /api/v1/resumes/improve` — `{resume_id, job_id}` → tailored ResumeData + `ats_score` (diff-based, verified)
  - `GET /api/v1/resumes/{resume_id}/pdf?template=swiss-single` — PDF export (Playwright, ~3s)
  - NOTE: `/api/v1/refine` and `/api/v1/score` from the original research do NOT exist upstream.
  - NOTE: `markdownImproved` in the response actually contains ResumeData JSON, not markdown — render markdown from `resume_preview` yourself.
- **What it does:** AI-powered resume tailoring. Upload parses the resume to JSON via
  LLM; improve extracts JD keywords, generates targeted diffs, applies them with
  verification (zero skill-section additions in smoke test), returns tailored
  ResumeData + ats_score + detailed per-change diff log. Caveat: description text can
  still gain plausible embellishment (e.g. "Delta Lake" under a Databricks bullet) —
  verify against `detailed_changes` / `warnings`, or strip with the old
  `strip_fabricated_content` logic.
- **Signal:** llm-hybrid (LLM for parsing/writing, deterministic for scoring)
- **API key needed:** Yes (`LLM_PROVIDER=deepseek`, `LLM_MODEL=deepseek-chat`, `LLM_API_KEY` — DeepSeek is first-class)
- **Dependencies:** Playwright Chromium for PDF export

### 2. ATSFlow (★1)
- **Repo:** https://github.com/ry-ops/ATSFlow
- **Language:** JavaScript (Node.js + Express)
- **License:** MIT
- **Run:** `npm install && npm start` → port 3101, or Docker
- **How to invoke:** REST API or CLI (`node cli.js`)
- **Key endpoints:** `POST /api/analyze` (AI analysis proxy),
  `POST /api/parse` (resume parsing), `POST /api/tailor`
- **CLI:** `node cli.js analyze resume.pdf --format json`
- **What it does:** 30-rule ATS compliance scanner (10 formatting + 10 structure + 10 content
  checks) with letter grading, plus AI-powered content generation via Claude.
  The rule scanner is deterministic; the content generation needs an API key.
  Also has a server mode that proxies Claude API requests.
- **Signal:** rule-based (30 checks are pure regex/string matching, zero LLM)
- **API key needed:** Only for AI content generation features
- **Note:** The 30-rule scanner (`ATSScanner` class in `js/analyzer/`) was designed
  for browser `<script>` loading. The CLI bridge at `js/analyzer/cli-analyze.js`
  was created during pipeline development to expose it over stdout.

### 3. ats-resume-checker (PyPI)
- **Package:** `ats-resume-checker` (v0.2.0)
- **Install:** `pip install ats-resume-checker`
- **Language:** Python
- **License:** MIT
- **How to invoke:** `analyze_resume(resume_path, job_description_str)` → dict
- **What it does:** TF-IDF cosine similarity between resume and JD. Extracts keywords
  via NLTK lemmatization + bigrams + 30+ known multi-word phrases. Returns
  ats_score (0-100), match_rate, matched/missing keywords, suggestions.
- **Signal:** statistical (pure vector math, no NLP model beyond NLTK)
- **API key needed:** No
- **Dependencies:** PyPDF2, python-docx, scikit-learn, nltk

### 4. ats-resume-scorer (PyPI)
- **Package:** `ats-resume-scorer` (v2.0.0)
- **Install:** `pip install ats-resume-scorer`
- **Language:** Python
- **License:** MIT
- **How to invoke:** CLI (`ats-score --resume resume.pdf --jd job.txt`)
  or in-process Python, or web API mode
- **What it does:** spaCy-based NLP scoring with configurable skills database.
  Three detail levels (concise/normal/detailed). Optional AI-enhanced
  recommendations via OpenAI/Anthropic/Gemini/Ollama. Batch processing,
  parallel workers, resume comparison, web API, Docker support.
- **Signal:** nlp (correlated with statistical group)
- **API key needed:** Only for AI-enhanced recommendations
- **Status:** FUTURE STATE (deferred 2026-08-06). BLOCKED on Windows (pins numpy<2 which doesn't build on Python 3.14); works in Docker (python:3.12-slim) or Python ≤3.12 venv.

### 5. ats-resume-improver (★2)
- **Repo:** https://github.com/simeononsecurity/ats-resume-improver
- **Language:** TypeScript (React + Vite)
- **License:** MIT
- **Run:** Docker, Vercel, Cloudflare Pages, or `npm run dev`
- **How to invoke:** Browser-only (no REST API). Could be driven headlessly
  via Puppeteer/Playwright, but not natively scriptable.
- **What it does:** 100% client-side ATS optimizer. Resume type detection
  (7 profiles), 5-dimension scoring (readability 30%, content quality 30%,
  depth 20%, completeness 10%, formatting 10%), keyword matching with 18
  skill relation groups, AI-powered optimization via OpenAI/Anthropic/Ollama.
- **Signal:** hybrid (deterministic rules + optional LLM)
- **Skippable for v1:** No API/server mode. Headless browser driving adds
  significant complexity. Its rule set overlaps with ATSFlow's 30 checks.

---

## Builders/Parsers (not matchers, but useful in pipeline)

### 6. Reactive Resume (★40,218) — FRONTRUNNER
- **Repo:** https://github.com/AmruthPillai/Reactive-Resume
- **Language:** TypeScript (monorepo: client, server, artboard, worker)
- **License:** MIT
- **Version:** v5.2.5 (10+ tagged releases, actively maintained)
- **Run:** `docker compose up -d` (Postgres only, no Redis/MinIO in v5)
- **Docker:** `amruthpillai/reactive-resume` — 5M+ pulls, GHCR, multi-arch
- **Status:** CANDIDATE — full REST API + MCP server, automatable end-to-end
- **Approach:** LLM-driven tailoring via Application Copilot (provider-agnostic)
- **Signal:** llm (OpenAI/Anthropic/Gemini/OpenRouter/Ollama/OpenAI-compatible → DeepSeek)
- **API key needed:** Yes (bring-your-own AI provider)
- **Community:** 40k stars, 4.5k forks, 117k+ Discord, active sponsors (Atlas Cloud)
- **Blog/YouTube:** Docs "Spotlight" page with community articles and videos

#### Tailoring API (source-verified 2026-08-07)

- `POST /applications/{id}/ai/match-score` → `{score, gaps, strengths}` — scores linked resume against JD
- `POST /applications/{id}/ai/tailor-resume` → `{resumeId, name}` — creates tailored resume copy
- `POST /applications/{id}/ai/draft-message` — drafts cover letter or follow-up
- MCP tools: `score_application_match`, `tailor_resume_for_application`, `draft_application_message`
- JSON Patch API for programmatic resume editing
- Import from JSON Resume, LinkedIn PDF, DOCX; export to PDF, DOCX, JSON, Markdown

#### Architectural tradeoff

Reactive Resume is builder-first. The workflow would be:
1. Import master resume (JSON or PDF) into Reactive Resume's JSON Resume format
2. Create application with JD text/URL
3. API call to tailor → new tailored resume copy
4. Export tailored JSON back to RenderCV YAML for final PDF

This adds a format conversion layer (RenderCV YAML ↔ JSON Resume) but trades it for
a professionally maintained, community-backed pipeline with 40k stars. The tailoring
quality is UNTESTED — we don't know if it does deep reframing or keyword-padding
until we smoke-test it.

#### Verdict

⭐⭐⭐⭐⭐ Beats every other candidate on community, maintenance, Docker, API maturity,
and AI provider flexibility (DeepSeek-compatible via OpenAI-compatible endpoint).
The only open question is tailoring quality — is it deep reframing or keyword-padding?
### 8. pyresparser (★958)
- **Repo:** https://github.com/OmkarPathak/pyresparser
- **Install:** `pip install pyresparser`
- **How to invoke:** `ResumeParser('/path/to/resume').get_extracted_data()`
- **What it does:** Extracts name, email, phone, skills (from curated CSV),
  education, experience duration, designations, company names using spaCy
  NER + regex. No scoring — pure extraction.

---

## Independent Signal Groups

When running multiple matchers in parallel, the panel review must account
for correlation between signals:

| Group | Matchers | What they measure | Correlation |
|---|---|---|---|
| **statistical** | ats-resume-checker (TF-IDF) | Lexical overlap between resume and JD | Baseline |
| **nlp** | ats-resume-scorer (spaCy) | Lexical + skills DB match | Correlated with statistical |
| **llm-hybrid** | Resume-Matcher API | LLM parsing + deterministic scoring | Partially correlated with LLM |
| **rule-based** | ATSFlow (30 checks) | Formatting, structure, content rules | **Independent** of all others |
| **llm** | DeepSeek auditor + recruiter | Semantic judgment | Independent of rule-based |

A panel that averages statistical (31) and rule-based (84) to get ~58 without
understanding they measure different things produces a misleading consensus.
The panel chair prompt must explicitly note which signals are independent.

---

## Docker Compose (current + future state)

```
docker-compose.yml  (services/)
├── ats-checker     ← ats-resume-checker (PyPI) in a thin FastAPI wrapper — RUNNING (optional lint)
│   Port: 8001
├── atsflow         ← ATSFlow 30-rule scanner in a thin Express wrapper — RUNNING (optional lint)
│   Port: 3101      (scanner-server.js exposes js/analyzer/ats-scanner.js over REST;
│                    upstream server.js does NOT expose the scanner over HTTP — its
│                    /api/analyze is a Claude AI proxy, not the 30-rule scan)
│
└── resume-matcher  ← github.com/srbhr/Resume-Matcher (build from source) — CORE, NEXT TO STAND UP
    Port: 8000 (backend only; the Next.js frontend is not needed for the API path)
    Needs: LLM_PROVIDER=deepseek, LLM_MODEL=deepseek-chat, LLM_API_KEY
```

Tailoring path (JD + resume in → tailored resume out):
`POST /api/v1/upload` → `POST /api/v1/jobs/upload` → `POST /api/v1/improve` →
`GET /api/v1/{resume_id}/pdf`. ats-checker and atsflow are optional pre/post lint;
the old panel-of-experts orchestrator (`src/ats_pipeline/run.py`) is deprecated for
this path. Not containerized (skipped): ats-resume-scorer (numpy<2 pin needs
python:3.12-slim), ats-resume-improver (browser-only).

---

## Deep-Reframing Research (2026-08-07)

Research triggered by Resume-Matcher's failure: keyword-padding instead of
reframing, CP1252 mojibake, 3-page bloat, empty sections. Goal: find tools that
do deep reframing — restructure/rephrase experience to match JD, not append
keyword boilerplate to existing bullets.

### Root Cause: Resume-Matcher Failure Analysis

| Issue | Evidence |
|---|---|
| CP1252 mojibake | 19× `\u00e2\u20ac\u201d` in `improve_response.json` — em-dash bytes (UTF-8 `0xE2 0x80 0x94`) decoded as Windows-1252. Master `resume/cv.yaml` is clean (zero em-dashes). Corruption enters in Resume-Matcher's LLM → PDF pipeline. |
| Keyword-padding | Appends "This project demonstrates BI/data warehousing, Power BI, SQL, and stakeholder management skills" to bullet points. No restructuring. |
| Lowercasing | "Orchestrated" → "coordinated", "Championed" → "advocated" — LLM changes tense and weakens verbs. |
| 3-page bloat | 16 bullet points for Hancock role alone, no pruning. |
| Empty sections | `education`, `skills`, `certifications` all empty — parser can't extract from RenderCV PDF. |
| Superficial diff | 11 total changes, all description modifications, zero skills/certs added — tailoring is purely cosmetic. |

---

## New Candidates: Deep Reframing Tools

### 9. Resume-Tailor-AI (★37, JaimeYeung)
- **Repo:** https://github.com/JaimeYeung/Resume-Tailor-AI
- **Language:** TypeScript (Next.js)
- **License:** Not specified
- **Run:** `npm run build && npm start` → Next.js server with REST API
- **Status:** CANDIDATE — automatable via `POST /api/generate-resume`
- **Approach:** LLM (GPT-4o) + deterministic scoring pipeline
- **Signal:** llm (GPT-4o for all 4 parallel calls) + deterministic weighted scoring
- **API key needed:** OpenAI (gpt-4o hardcoded — no DeepSeek swap)
- **Docker:** No
- **Releases:** None tagged
- **Last commit:** 2026-04-20 — stable, not dead, but no recent activity
- **Community:** 37 stars, 8 forks (10x more than any other candidate), 1 open issue (unactionable JSON parse bug report)
- **Approach:** LLM (GPT-4o) + multi-stage heuristic pipeline
- **Signal:** llm (GPT-4o for all stages)
- **Fact fidelity:** EXCEPTIONAL — the "Fact Bank" model is the strongest anti-fabrication pattern found

#### Rule Set (from `lib/prompts.ts` — source-verified 2026-08-07)

**Stage 1: Parse** — LLM extracts structured JSON from resume. Bullets copied verbatim.
Summary/profile sections ignored (they map to nothing).

**Stage 2: JD Report** — LLM categorizes JD keywords into 7 buckets:

| Bucket | What it captures | Max |
|---|---|---|
| `titleKeywords` | Job title + close variants + function words | 4 |
| `hardSkills` | Tools, software, languages, platforms, technical methods | 12 |
| `actionKeywords` | Verb+object phrases from Responsibilities ("drive cross-functional execution") | 8 |
| `businessContext` | Business scenarios and domain concepts ("roadmap", "stakeholder management") | 10 |
| `domainKeywords` | Industry/domain words ("SaaS", "B2B", "fintech") | 5 |
| `hardFilters` | Explicit requirements ("3+ years", "Bachelor degree", "SQL required") | 6 |
| `top10` | The 10 most important recruiter-searchable keywords, ranked | 10 |

Explicit rule: "Do NOT include generic soft skills (communication, collaboration,
teamwork — these have no ATS value)."

**Stage 3: Version Selection** — if user supplied multiple title versions per
experience, LLM picks the best for this JD. Based entirely on title function match,
not bullet content.

**Stage 4: Bullet Rewrite (MINIMAL)** — the gold-standard prompt. Rules in strict
priority order:
1. RETURN UNCHANGED: any bullet not needing a keyword change — copy EXACTLY
2. VARIANT FIX: replace only the variant word with JD's exact phrasing ("A/B testing" → "A/B test")
3. KEYWORD INSERT: for MISSING keywords only, find most relevant bullet, insert naturally with minimal edit
4. NEVER fabricate facts, numbers, company names, tools, or any detail not in the original
5. NEVER end any bullet with a period
6. Return EXACT SAME NUMBER of bullets per experience — do not merge, split, or drop

**Stage 4b: Bullet Rewrite (AGGRESSIVE)** — for business context keywords, proactively
work them in even if it requires small rewrites. For hard skills, only insert where
evidenced.

**Stage 5: Skills Boost** — add missing keywords to existing skill categories.
Skip business concepts (they go in bullets, not skills section).

#### API (source-verified 2026-08-07)

5 API routes under `app/api/`:
- `POST /api/generate-resume` — `{factBank: FactBank, jdText: string}` → `{resume: GeneratedResume}`
- `POST /api/boost-ats` — further ATS optimization pass
- `GET /api/download-pdf` — PDF export
- `POST /api/parse-resume` — resume parsing
- `POST /api/scrape-jd` — JD scraping from URL

**`/api/generate-resume` pipeline** (source-read from `route.ts`):
1. Parallel: version selection + JD report (2× GPT-4o calls)
2. Parallel: bullet rewrite + skills (2× GPT-4o calls)
3. Assemble `GeneratedResume` with `jdKeywordCoverage` (before/after scores, matched/missing)
4. Weighted scoring: hardSkills=2pts, titleKeywords=1.5pts, businessContext=1pt
5. One-page constraint: iterative trim loop (up to 15 passes), drops least-ATS-valuable bullets

**Input contract:** `FactBank` requires experiences with multiple `Version[]` arrays —
each version has its own title and bullets. This is the Fact Bank model: user writes
variants, AI selects. No variant means empty versions array, which is unsupported.

#### Verdict

FRONTRUNNER. 37 stars/8 forks = 10x community traction over all other candidates
combined. Working REST API that accepts structured JSON and returns `GeneratedResume`
with before/after keyword coverage scores. Rule set is the gold standard (7-bucket JD
categorization, minimal-edit rewrite, one-page trim). ⭐⭐⭐⭐ Smoke test first.
4 GPT-4o calls per run (~$0.02 API cost). Needs OpenAI key + Fact Bank conversion layer.
Single weakness: gpt-4o hardcoded — cannot swap to DeepSeek.


---

### 10. ResuMeshAI (★0, jayb71)
- **Repo:** https://github.com/jayb71/ResuMeshAI
- **Language:** Python
- **License:** Not specified
- **Run:** Python + Ollama (qwen3:30b) + external hiring-agent clone
- **Status:** CANDIDATE — smoke test needed to confirm it runs
- **Approach:** LLM single-pass rewrite with Pydantic structured output → RenderCV YAML
- **Signal:** llm (qwen3:30b via Ollama, model swappable)
- **Fact fidelity:** Strong guardrails in prompt — "ZERO PHANTOM EXPERIENCE", strict array matching, preserve exact company names/dates

#### Rule Set (from `app/llm/prompts.py` — source-verified 2026-08-07)

**JD Analyzer:** extracts structured requirements from JD text.

**Company Research:** web search for company profile (separate agent).

**ATS Tailor (main prompt):**
- CRITICAL GUARDRAIL: zero phantom experience — exact same number of items and company names as input
- Summary: 2-3 sentences, no first-person, weave 3-5 JD keywords naturally
- Bullets: strong past-tense action verbs, quantify with metrics, 1-2 lines each, 3-5 per role
- Job titles: may rephrase to align with target role only if it reflects actual work
- STRICT FACTUAL INTEGRITY: preserve exact company names, dates, education, true skill scope
- Output: raw JSON matching RenderCVFullSchema Pydantic model

**Feedback Improvement (second pass):**
- Re-align wording and keyword density toward existing real-world strengths
- Never fabricate missing skills/links/projects
- No placeholders like "[Insert Link Here]"
- Project dates: omit entirely if unknown, never output empty strings

#### RenderCV Output Schema (Pydantic)

```python
class RenderCVFullSchema(BaseModel):
    cv: RenderCVCurriculumVitae  # name, headline, location, email, phone, website, social_networks
        sections: RenderCVSections  # summary[], skills[SkillEntry], experience[ExperienceEntry],
                                    # projects[ProjectEntry], education[EducationEntry]
```

**Direct mapping to `cv_tailored.yaml`** — the Pydantic model IS the RenderCV schema.
No conversion needed.

#### Verdict

Highest automation fit: structured output matches RenderCV YAML directly. Single-pass
LLM rewrite with strong guardrails. BUT: requires Ollama + ~30B model (qwen3:30b),
external hiring-agent clone dependency, and two manual patches per README. Also:
single-pass LLM rewrites can still hallucinate despite guardrails — needs
`audit_alignment.py` as backstop.

**Smoke test needed:** confirm it runs under our Windows + Python 3.14 constraints,
and evaluate output quality on test fixture (Power BI JD + master YAML).

---

### 11. career-tailor (★2, chriestensonb)
- **Repo:** https://github.com/chriestensonb/career-tailor
- **Language:** Python (Pydantic AI agents)
- **License:** MIT
- **Run:** `uvx career-tailor tailor jd.md` (CLI)
- **Status:** CANDIDATE — CLI-based, pip-installable
- **Approach:** LLM (Claude/OpenAI) two-agent pipeline: JD parser + tailor
- **Signal:** llm (Anthropic Claude or OpenAI)
- **Fact fidelity:** Explicit rule: "Never fabricate facts — only reframe what exists in the profile"

#### Rule Set (from `src/resume_builder/agent.py` — source-verified 2026-08-07)

**JD Parser Agent:**
- Extract required_skills, preferred_skills, keywords, responsibilities
- Leave fields null rather than guessing

**Tailor Agent:**
- Keep ALL jobs from the profile in newest-first order — no gaps
- For each job, merge responsibilities and achievements into bullets:
  - **Drop bullets that are irrelevant to the JD**
  - **Rewrite remaining bullets to mirror JD language where truthful**
  - **Order bullets most-relevant-to-JD first**
- Summary: compelling 2-3 sentence professional summary targeting the role
- Skills: include all relevant skills ordered by JD relevance
- keywords_matched: list JD keywords that appear in the tailored resume
- Never fabricate facts — only reframe what exists in the profile

#### Verdict

Strong candidate for the **reframing + smart pruning** model. The "drop irrelevant,
rewrite remaining, reorder by JD relevance" pipeline is exactly what Resume-Matcher
lacks. CLI-based, Pydantic AI structured output. BUT: output is NOT RenderCV YAML —
would need conversion. Also uses Claude/OpenAI models (no DeepSeek support documented),
though Pydantic AI supports model swapping.

**Smoke test needed:** confirm it runs with DeepSeek model, evaluate output quality
on test fixture, assess YAML conversion path.

---

### 12. cv4offer (★0, gicaking)
- **Repo:** https://github.com/gicaking/cv4offer-tailor
- **Language:** Python
- **License:** MIT
- **Run:** `pip install cv4offer` → `cv4offer generate posting.md --master my_cv.tex`
- **Status:** RESEARCH REFERENCE — LaTeX-only, but the content-selection model is a valuable design pattern
- **Approach:** LLM (Claude) content SELECTION from user-authored content pools
- **Signal:** llm (Anthropic Claude)
- **Fact fidelity:** MAXIMUM — AI never writes new content, only selects from user-authored variants

#### Rule Set (from `cv4offer/tailor.py` — source-verified 2026-08-07)

**Master CV uses tagged content pools** in LaTeX comments:
```
% %%SKILL:eng_python :: \item Python (Django, FastAPI)
% %%BULLET:acme_1_v1 :: \item Led migration, reducing deploy time by \textbf{70\%}
% %%BULLET:acme_1_v2 :: \item Architected migration from monolith to 12 microservices...
% %%BULLET:acme_1_v3 :: \item Monolith to microservices: deploy time \textbf{-70\%}
% %%FROZEN_SECTION:start:education  ...  %%FROZEN_SECTION:end:education
```

**Selection rules:**
- Select 12-18 skills from pool, distributed across 4 groups
- Select best bullet variant (v1/v2/v3) per experience + how many to include
- Write new summary (3-5 sentences) with metrics, ending with career development direction
- Select best achievement variant based on industry fit
- Active voice always, metrics over adjectives
- Never copy job posting phrases verbatim — rephrase with concrete evidence
- 2-page enforcement: auto-trim or expand
- Frozen sections: education, references never touched
- Visual quality checker: orphan lines, overfull text, page fill %

#### Verdict

The **content-selection model** is the strongest anti-hallucination pattern found.
AI never writes new content — only selects from user-authored variants. This is exactly
what the user's "feed in all my raw experiences" ideal needs: user writes multiple
versions per experience, AI picks the best combination for each JD. BUT: requires
LaTeX master with tagged content pools. Converting our RenderCV YAML to this tag
system = significant upfront cost. **Tag system is a design pattern worth adopting,**
but the LaTeX dependency is a blocker.

---

### 13. tailor-resume (★0, narendranathe)
- **Repo:** https://github.com/narendranathe/tailor-resume
- **Language:** Python (stdlib + Claude Code skill)
- **License:** MIT
- **Run:** `pip install tailor-resume` → `/tailor-resume` (Claude Code skill)
- **Status:** RESEARCH REFERENCE — gap analyzer is valuable, but tailoring depends on Claude Code
- **Approach:** Deterministic gap analysis + Claude Code for rewriting
- **Signal:** heuristic (gap analysis) + llm (Claude for rewriting)

#### Rule Set (from source — source-verified 2026-08-07)

**JD Gap Analyzer** (`jd_gap_analyzer.py`) — deterministic, zero LLM:

10 signal categories, each with a keyword taxonomy:

| Category | Example keywords |
|---|---|
| testing_ci_cd | test, pytest, CI/CD, github actions, azure devops, pipeline, deploy |
| data_quality_observability | data quality, schema enforcement, monitoring, great expectations |
| orchestration | airflow, dagster, prefect, dag, workflow, backfill, idempotent |
| semantic_layer_governance | semantic layer, dbt, lineage, catalog, governance, rbac |
| architecture_finops | architecture, cost, finops, delta lake, iceberg, partition, pruning |
| streaming_realtime | streaming, real-time, kafka, kinesis, flink, event, latency |
| ml_ai_platform | ml, machine learning, model, mlflow, llm, rag, embedding, vector |
| cloud_infra | azure, aws, gcp, kubernetes, docker, terraform, iac |
| leadership_ownership | lead, mentor, ownership, cross-functional, stakeholder, strategy |
| sql_data_modeling | sql, data model, star schema, normalization, olap, data warehouse |

Each category has suggested angles for closing gaps (e.g., "Describe a test suite
you built with incident reduction metrics").

**ATS Score:** 50% keyword overlap + 50% category coverage average.

**ATS Relevance Gate:** score bands → honest ceiling (≥80→97+, 60-79→90+, <50→decline).

**Claude Code Skill:** the actual rewriting is done by Claude in a chat session,
guided by the skill's instructions. No standalone LLM prompt for rewriting.

#### Verdict

The 10-category signal taxonomy is the best deterministic gap analyzer found.
Suggested angles per category = actionable guidance. BUT: the tool is a Claude Code
wrapper — tailoring depends on a Claude chat session, not a programmatic API.
**Adopt the gap analyzer taxonomy as a pre-check** in our pipeline, but not the
full tool.

---

### 14. resume-tailor-mcp (★0, NmaaAlhawary)
- **Repo:** https://github.com/NmaaAlhawary/MCP-Resume-Tailor
- **Language:** Python (MCP server)
- **License:** MIT
- **Run:** `pip install resume-tailor-mcp` → MCP server
- **Status:** RESEARCH REFERENCE — useful components, not a full solution
- **Approach:** MCP server (persistence + ATS gap + export) + Claude for rewriting
- **Signal:** deterministic (ATS gap) + llm (Claude for rewriting)

#### Key Features

- **Persistent master CV:** stored as JSON, set up once, reused
- **SSRF-guarded job fetch:** fetches JD from URL, blocks private IPs
- **Deterministic ATS gap:** keyword math with synonym awareness (K8s↔Kubernetes, JS↔JavaScript)
- **ATS-safe export:** single column, standard fonts, real selectable text, Unicode-safe
- **Master-CV backup:** `.bak` copy before overwrite

#### Verdict

Persistence + deterministic ATS gap + clean export = useful foundation components.
But the rewriting IS Claude (the MCP client), not a tailored prompt. Output format
is PDF/DOCX, not RenderCV YAML. **Adopt the synonym-aware gap check and master-CV
backup pattern** — not the full MCP server.

---

## Cross-Tool Rule Catalog

### Rules present in 3+ tools (high-confidence, proven):

| Rule | Resume-Tailor-AI | ResuMeshAI | career-tailor | cv4offer | tailor-resume |
|---|---|---|---|---|---|
| Never fabricate facts/metrics | ✅ Rule 4 | ✅ "STRICT FACTUAL INTEGRITY" | ✅ "Never fabricate" | ✅ Selection model | — |
| Same bullet count per role | ✅ Rule 6 | — | — | ✅ (selects variants) | — |
| No trailing periods on bullets | ✅ Rule 5 | — | — | — | — |
| Strong action verbs (past tense) | — | ✅ "Led, Built, Designed" | — | ✅ "Active voice" | — |
| Quantify with metrics | — | ✅ | — | ✅ "Metrics over adjectives" | — |
| Drop JD-irrelevant bullets | — | — | ✅ | ✅ (selective) | — |
| Reorder bullets: JD-relevant first | — | — | ✅ | — | — |
| Summary: 2-3 sentences, no first-person | — | ✅ | ✅ | ✅ | — |
| Skills ordered by JD relevance | — | — | ✅ | ✅ (selects from pool) | — |
| Do NOT add target company as employer | — | ✅ "ZERO PHANTOM" | — | — | — |
| Preserve exact dates/company names | — | ✅ | — | ✅ (frozen sections) | — |

### Unique rules (one tool only, worth adopting):

| Rule | Source | Value |
|---|---|---|
| 7-bucket JD keyword categorization | Resume-Tailor-AI | Gold standard for structured JD analysis |
| Minimal-edit rewrite (variant fix + keyword insert) | Resume-Tailor-AI | Prevents unnecessary rewrites |
| Aggressive mode for business context keywords | Resume-Tailor-AI | Proactive keyword insertion where truthful |
| ATS-weighted trim (drop least-keyword-rich bullets) | Resume-Tailor-AI | Smart one-page compression |
| 10-category signal taxonomy for gap analysis | tailor-resume | Best deterministic pre-check |
| Content-selection model (AI picks, user writes) | cv4offer | Strongest anti-hallucination pattern |
| Frozen sections (education/references never touched) | cv4offer | Prevents accidental edits |
| Visual quality checker (orphans, overfull, page fill) | cv4offer | Catches LaTeX issues pdflatex misses |
| Synonym-aware keyword matching (K8s↔Kubernetes) | resume-tailor-mcp | Better gap scores |
| ATS relevance gate (score bands + honest ceiling) | tailor-resume | Honest expectations for candidates |

---

## Updated Status (2026-08-07)

| Tool | Runnable? | Automation fit | Reframing depth | Fact fidelity | Output format |
|---|---|---|---|---|---|
| Resume-Matcher | ✅ (Docker) | ✅ API | LOW — keyword-padding | MEDIUM — embellishes | PDF only |
| Resume-Tailor-AI | ✅ (API) | ✅ POST /api/generate-resume | HIGH — minimal-edit + trim | MAXIMUM — Fact Bank | JSON → needs YAML map |
| ResuMeshAI | ❓ (needs smoke test) | ✅ Python API | HIGH — full rewrite | HIGH — guardrails | RenderCV YAML |
| career-tailor | ✅ (CLI) | ✅ CLI | HIGH — reframing+pruning | HIGH — explicit rule | MD + JSON + CSS |
| cv4offer | ✅ (CLI) | ✅ CLI | MEDIUM — selection | MAXIMUM — selection | LaTeX → PDF |
| tailor-resume | ✅ (CLI) | ❌ (Claude Code dep) | N/A — not standalone | N/A | LaTeX → PDF |
| resume-tailor-mcp | ✅ (MCP) | ✅ MCP tools | N/A — Claude does it | N/A | PDF/DOCX |

---


## Recommendation

Per the CI lesson (AGENTS.md 2026-08-06): research on runnable tools ends with
RUNNING them. Smoke-test runnable candidates first; build custom only if all fail.

### Step 1: Smoke-test runnable candidates (do this next)

**Candidate A — Resume-Tailor-AI** (★37, REST API, GPT-4o):
- Start: `git clone` + `npm run build && npm start` → Next.js on port 3000
- POST `{factBank: FactBank, jdText}` to `/api/generate-resume` → `{resume: GeneratedResume}`
- Cost: 4 GPT-4o calls per run (~$0.02). Needs OpenAI key.
- Must convert RenderCV YAML → FactBank format (experiences need `Version[]` arrays
  — single-version per role works as degenerate case).
- Output `GeneratedResume` JSON needs mapping back to RenderCV YAML.
- 37 stars, 8 forks — 10x community traction. Gold-standard rule set.

**Candidate B — career-tailor** (★2, CLI, `uvx career-tailor`):
- Runnable as-is: `uvx career-tailor tailor jd.md`. v0.1.0 released 2026-06-10.
- Uses Pydantic AI (Anthropic/OpenAI). Output: Markdown+JSON — needs YAML conversion.
- 8 issues (5 open) — all roadmap items from active solo author.

**Candidate C — ResuMeshAI** (★0, Python, Ollama+qwen3:30b, RenderCV-native):
- qwen3:30b is ~19GB Ollama pull — report RAM/cost before launching.
- Output: Pydantic `RenderCVFullSchema` → direct `cv_tailored.yaml` mapping.
- No releases, no issues, no stars — single-commit project as of 2026-08-07.

### Step 2: Only build custom if all three candidates fail

Build a custom DeepSeek prompt pipeline combining the best rules from 5 tools:

1. **JD Analysis** — Resume-Tailor-AI's 7-bucket categorization
2. **Gap Pre-Check** — tailor-resume's 10-category signal taxonomy (deterministic)
3. **LLM Rewrite** — system prompt with:
   - Resume-Tailor-AI's minimal-edit rules (variant fix, keyword insert, return unchanged)
   - career-tailor's smart pruning (drop irrelevant, rewrite remaining, reorder by JD relevance)
   - ResuMeshAI's guardrails (zero phantom experience, strict factual integrity)
   - Resume-Tailor-AI's formatting rules (no trailing periods, same bullet count)
4. **Structured Output** — JSON Schema matching RenderCV YAML structure
5. **Fabrication Audit** — `scripts/audit_alignment.py`

This is the "Direct LLM Integration" path from `docs/ats_resume_knowledge_2026.md`
line 176, now with a specific rule set from 5 source-verified prompts.

### Long-term design patterns (adopt regardless of which path wins)

| Pattern | Source | Why |
|---|---|---|
| 7-bucket JD keyword categorization | Resume-Tailor-AI | Structured JD analysis > flat keyword lists |
| Minimal-edit bullet rewrite | Resume-Tailor-AI | Prevents unnecessary rewrites |
| Smart pruning (drop + reorder) | career-tailor | Concise without losing content |
| 10-category signal taxonomy | tailor-resume | Best deterministic pre-check found |
| Content-selection model (Fact Bank) | cv4offer | Long-term: zero-hallucination guarantee |
| Synonym-aware keyword matching | resume-tailor-mcp | Better gap scores (K8s↔Kubernetes) |
| Master-CV backup before tailoring | resume-tailor-mcp | Safety net for destructive edits |

### What to retire

**Resume-Matcher is deprecated.** It cannot be fixed — the architecture (PDF
roundtrip, keyword-padding, CP1252 corruption) is fundamentally wrong for our
workflow. Remove from `services/docker-compose.yml` when the replacement is
operational.


---

## Docker Images (Docker Hub + GHCR search, 2026-08-07)

Only 3 resume-tailoring containers exist across Docker Hub and GitHub Container
Registry. None are official or verified.

| Image | Pulls | Status | Notes |
|---|---|---|---|
| `srbhr/resume-matcher` | 9,169 | Active | Our current (failing) tool — 1 Docker Hub star |
| `qrqr/resume-tailor` | 1,660 | Active | "Local resume and job-post tailoring app powered by Codex." Last updated 2026-03-17. Source repo 404s — rules uninspectable. Codex auth required. Docker Compose. Low-confidence, but the only Docker-native alternative to Resume-Matcher. |
| `narendranathe/tailor-resume` | — | GHCR only | Standalone image claimed implemented per closed issue #33 (commit 0c78c52). Python 3.12-slim + texlive-latex-base, multi-arch. Not on Docker Hub — available at `ghcr.io/narendranathe/tailor-resume`. Unverified. |

**Key takeaway:** There is no well-maintained, high-pull-count Docker image for
resume tailoring besides the failing `srbhr/resume-matcher`. The candidate tools
(Resume-Tailor-AI, career-tailor, ResuMeshAI) all require local setup with no
Docker option. This is a gap in the ecosystem.
