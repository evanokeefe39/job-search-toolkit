# ATS Matcher I/O Contracts (from source code analysis)

## 1. ats-resume-checker (PyPI, TF-IDF)
**Install:** `uv pip install ats-resume-checker`
**Invocation:** in-process Python
**Input:** resume file path (.pdf/.docx/.txt) + JD string
**Output:**
```json
{
  "ats_score": 31.17,           // 0-100, TF-IDF cosine similarity
  "match_rate": 27.5,            // % of JD keywords found in resume
  "matched_keywords": ["azure", "python", "sql", ...],
  "missing_keywords": ["architecture", "design", "modeling", ...],
  "resume_word_count": 317,
  "suggestions": [
    "ATS score is low. Rewrite your resume to mirror...",
    "Include more job-specific keywords..."
  ]
}
```
**Latency:** ~12s (includes NLTK lemmatization)
**Strengths:** Pure math, no API, reproducible
**Weaknesses:** No skills database — extracts common English words as "keywords". Low signal quality.
**Independent signal group:** statistical

## 2. ats-resume-scorer (PyPI, spaCy)
**Status:** BLOCKED on Windows due to numpy<2 pin. Requires Python 3.12 venv or Docker.
**Invocation:** CLI (`ats-score`) or in-process Python
**Input:** resume file + JD file or text, optional AI config via env vars
**Output:** similar shape to ats-resume-checker but richer:
```json
{
  "overall_score": 0-100,
  "sub_scores": {"keyword_match": ..., "skills_match": ..., "formatting": ..., "completeness": ...},
  "matched_keywords": [...],
  "missing_keywords": [...],
  "recommendations": [...],      // 3 detail levels: concise/normal/detailed
  "section_analysis": {...}
}
```
**Independent signal group:** nlp (correlated with statistical group)

## 3. Resume-Matcher API (Docker, FastAPI)
**Source:** github.com/srbhr/Resume-Matcher (image `srbhr/resume-matcher:latest`)
**Deployment:** `services/docker-compose.yml` — port 8000, env `LLM_PROVIDER=deepseek`, `LLM_MODEL=deepseek-chat`, `LLM_API_KEY` from `.env`
**Key endpoints (smoke-tested 2026-08-06, real PDF + real JD):**
- `GET /api/v1/health` — liveness check; does NOT call the LLM provider → `{"status":"healthy"}`
- `POST /api/v1/resumes/upload` — multipart, file field `file` (PDF/DOCX only) → `resume_id`
- `POST /api/v1/jobs/upload` — `{job_descriptions: [...], resume_id?}` → `job_id[]`
- `POST /api/v1/resumes/improve` — `{resume_id, job_id}` → tailored ResumeData + `ats_score` + `detailed_changes`
- `GET /api/v1/resumes/{resume_id}/pdf?template=swiss-single` — exported PDF bytes (`application/pdf`)

**Upload contracts** (observed 2026-08-06):
```json
// POST /api/v1/resumes/upload (multipart/form-data)
{
  "message": "File Jane_Doe_CV.pdf uploaded successfully",
  "request_id": "uuid",
  "resume_id": "uuid",
  "processing_status": "ready",
  "is_master": true
}
```
```json
// POST /api/v1/jobs/upload
{
  "job_descriptions": ["full JD text"],
  "resume_id": "uuid"            // optional; links resume for improvement
}
// → {"message": "data successfully processed", "job_id": ["uuid"], "request": {...}}
```
**Improve contract** (observed — `ats_score` and `detailed_changes` are nested under `data`):
```json
// POST /api/v1/resumes/improve
{
  "resume_id": "uuid",
  "job_id": "uuid"
}
// →
{
  "request_id": "uuid",
  "data": {
    "resume_id": "uuid",
    "job_id": "uuid",
    "resume_preview": { ... },
    "improvements": [ ... ],            // 5 items
    "markdownOriginal": "string",
    "markdownImproved": "string",
    "diff_summary": {
      "total_changes": 10,
      "skills_added": [...],
      "skills_removed": [...],
      "descriptions_modified": [...],
      "certifications_added": [...],
      "high_risk_changes": [...]
    },
    "detailed_changes": [
      {
        "field_path": "summary",
        "field_type": "summary",
        "change_type": "modified",
        "original_value": "...",
        "new_value": "..."
      }
    ],
    "refinement_stats": {
      "passes_completed": 0,
      "keywords_injected": 0,
      "ai_phrases_removed": [],
      "alignment_violations_fixed": 0,
      "initial_match_percentage": 0-100,
      "final_match_percentage": 0-100
    },
    "ats_score": {
      "overall_score": 0-100,
      "sub_scores": {
        "keyword_match": 0-100,
        "skills_coverage": 0-100,
        "section_completeness": 0-100
      },
      "missing_keywords": ["..."],
      "injectable_keywords": ["..."],
      "recommendations": ["..."]
    },
    "warnings": [],
    "refinement_attempted": true,
    "refinement_successful": true
  }
}
```
**Latency:** health ~instant; improve ~18s (DeepSeek chat call, ~32 KB response)
**Strengths:** Real LLM tailoring with a deterministic ATS scoring pass; exports a typeset PDF (template `swiss-single`) directly.
**Weaknesses:** LLM-dependent (needs a valid `LLM_API_KEY`); the exported PDF is advisory only (seam option B — the submission PDF is rendered by RenderCV from human-approved changes).
**Independent signal group:** llm-hybrid (LLM for parsing/writing, deterministic for scoring)

## 4. ATSFlow API (Docker/Node, Express)
**Source:** github.com/ry-ops/ATSFlow
**Deployment:** services/atsflow — `scanner-server.js` wrapper on port 3101 (upstream `server.js` also listens on 3101 but does NOT expose the 30-rule scanner over HTTP; its `/api/analyze` is a Claude AI proxy)
**Key endpoints:**
- `POST /analyze` — runs the real 30-rule scan via the upstream `ATSScanner` class (wrapper input: `{"resume_text": "...", "job_description": "..."}`)
- `GET /health`

**Input contract** (deployed wrapper takes plain text; the upstream scanner's native shape is shown for reference):
```json
// POST /analyze  (scanner-server.js wrapper)
{
  "resumeData": {
    "sections": [
      {"type": "experience", "title": "Work Experience", "content": {...}},
      {"type": "education", "title": "Education", "content": {...}},
      ...
    ],
    "rawText": "full resume text"
  },
  "options": {
    "fileFormat": "pdf",
    "industry": "software"
  }
}
```
**Output contract** (from same):
```json
{
  "version": "2.0.0",
  "timestamp": "ISO 8601",
  "executionTime": 150,
  "score": {
    "overallScore": 71,
    "grade": "B+",
    "gradeDescription": "...",
    "categoryScores": {
      "formatting": 85,
      "structure": 78,
      "content": 55
    }
  },
  "checks": {
    "total": 30,
    "passed": 22,
    "failed": 8,
    "results": [
      {
        "category": "formatting",
        "checkName": "noTables",
        "passed": true,
        "score": 100,
        "severity": "pass",
        "message": "No table-based layouts detected.",
        "recommendation": null,
        "impact": "critical"
      },
      ...
    ]
  },
  "recommendations": {
    "summary": {
      "totalRecommendations": 8,
      "criticalCount": 1,
      "highCount": 2,
      "estimatedTime": {"minutes": 75, "hours": 1.3, "formatted": "1h 15m"}
    },
    "quickWins": [...],
    "majorImprovements": [...],
    "allRecommendations": [...]
  }
}
```
**Independent signal group:** rule-based

## 5. DeepSeek Flash "ATS Auditor" (LLM)
**Invocation:** HTTP POST to `https://api.deepseek.com/v1/chat/completions`
**Model:** `deepseek-chat`
**Input:** System prompt + resume text + JD text
**Output:** Structured JSON (via system prompt instruction)
```json
{
  "score": 0-100,
  "sub_scores": {
    "keyword_alignment": 0-100,
    "skills_match": 0-100,
    "formatting_issues": 0-100,
    "section_quality": 0-100
  },
  "top_5_issues": [
    {"issue": "string", "severity": "critical|high|medium|low", "fix_suggestion": "string"}
  ],
  "matched_keywords": ["..."],
  "missing_keywords": ["..."]
}
```
**Independent signal group:** llm

## 6. DeepSeek Flash "Recruiter" (LLM)
**Invocation:** Same API, different system prompt
**Output:**
```json
{
  "would_screen": true|false,
  "score": 0-100,
  "first_impression": "string",
  "red_flags": ["..."],
  "strengths": ["..."],
  "what_to_fix": ["..."]
}
```
**Independent signal group:** llm (correlated with auditor, both use same model)
