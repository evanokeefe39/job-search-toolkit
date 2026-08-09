# ISSUES.md — job_search_scraping

## Closed

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
