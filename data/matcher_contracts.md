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
**Source:** github.com/srbhr/Resume-Matcher
**Port:** 8000
**Key endpoints from source:**
- `POST /api/refine` — takes master resume JSON + JD text → returns tailored resume JSON + ATS score
- `GET /health` — health check

**Input contract** (from `app/routers/refinement.py` + `app/services/ats.py`):
```json
// POST /api/refine
{
  "master_resume": { ... },      // ResumeData JSON schema
  "job_description": "string",
  "config": {                    // optional
    "enable_keyword_injection": true,
    "enable_ai_phrase_removal": true,
    "enable_master_alignment_check": true
  }
}
```
**Output contract** (from `app/schemas/refinement.py`):
```json
{
  "refined_data": { ... },       // tailored ResumeData
  "ats_score": {
    "overall_score": 0-100,      // weighted: kw_match*0.55 + skills*0.25 + sections*0.20
    "sub_scores": {
      "keyword_match": 0-100,
      "skills_coverage": 0-100,
      "section_completeness": 0-100
    },
    "missing_keywords": ["..."],
    "injectable_keywords": ["..."],
    "recommendations": ["..."]
  },
  "passes_completed": 3,
  "keyword_analysis": { ... },
  "alignment_report": { ... },
  "ai_phrases_removed": ["spearheaded", ...]
}
```
**Independent signal group:** llm-hybrid (LLM for parsing/writing, deterministic for scoring)

## 4. ATSFlow API (Docker/Node, Express)
**Source:** github.com/ry-ops/ATSFlow
**Port:** 3000
**Key endpoints:**
- `POST /api/analyze` — runs 30-rule ATS scan
- `GET /health`

**Input contract** (from `js/analyzer/ats-scanner.js`):
```json
// POST /api/analyze
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
