# ATS Matcher Catalog

Researched 2026-08-05. Use this as a reference for the federated pipeline.
Every tool below is runnable — either via `pip install`, Docker, or `npm start`.

---

## Runnable Services

### 1. Resume-Matcher (★28,039)
- **Repo:** https://github.com/srbhr/Resume-Matcher
- **Language:** TypeScript (Next.js frontend) + Python (FastAPI backend)
- **License:** Apache 2.0
- **Run:** Docker Compose or `git clone` + build
- **How to invoke:** REST API on port 8000 (frontend proxies `/api` to backend)
- **Key endpoints:** `/api/v1/health`, `/api/v1/refine`, `/api/v1/score`
- **What it does:** AI-powered resume tailoring. LLM parses resume to JSON,
  injects JD keywords, removes AI buzzwords, validates against master resume,
  scores on 3 dimensions (keyword_match 55%, skills_coverage 25%, section_completeness 20%).
- **Signal:** llm-hybrid (LLM for parsing/writing, deterministic for scoring)
- **API key needed:** Yes (OpenAI-compatible, works with DeepSeek)
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
- **Status:** BLOCKED on Windows (pins numpy<2 which doesn't build on Python 3.14).
  Works in Docker or Python ≤3.12 venv.

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

## Docker Compose Architecture (proposed)

```
docker-compose.yml
├── resume-matcher    ← github.com/srbhr/Resume-Matcher (build from source)
│   Port: 8000 (host) → 3000 (container frontend, proxies /api to backend:8000)
│   Needs: .env with LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
│
├── atsflow           ← github.com/ry-ops/ATSFlow (npm start, port 3101)
│   Port: 3101 (host) → 3101 (container)
│   Optional: CLAUDE_API_KEY for AI features
│
└── [orchestrator]    ← thin FastAPI/Python script (NOT a service — runs on host
    or as separate container)
    - Calls resume-matcher:8000/api/v1/refine
    - Calls atsflow:3101/api/analyze
    - Imports ats-resume-checker in-process
    - Calls DeepSeek Flash for auditor + recruiter personas
    - Panel synthesis via DeepSeek Flash
    - Metrics logging to JSON Lines
```

The orchestrator doesn't need to be a Docker service — it's a script that
talks to the Docker services. But for reproducibility, it can be containerized
too (FastAPI app with endpoints for each stage of the pipeline).
