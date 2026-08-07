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

### 6. Reactive Resume (★40,041)
- **Repo:** https://github.com/AmruthPillai/Reactive-Resume
- **Run:** Docker Compose (Postgres + Redis + MinIO + Browserless Chrome)
- **What it does:** Resume builder with ATS-friendly PDF output. Not a
  matcher/tailor — produces clean, parser-friendly documents.

### 7. OpenResume (★8,802)
- **Repo:** https://github.com/xitanggg/open-resume
- **Run:** Docker or `npm run dev`
- **What it does:** Resume builder + parser. The parser shows exactly what
  an ATS extracts (name, email, phone, sections). Uses PDF.js for text
  extraction and a feature-scoring system for field identification.
  No scoring/matching — purely extraction.

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
