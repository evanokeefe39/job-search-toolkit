# ISSUES.md — job_search_scraping

## Open

### Resume-Matcher: PDF parser drops work experience from RenderCV-rendered PDFs (systemic)

**Date:** 2026-08-08
**Severity:** Blocks tailor-resume skill — matcher produces 1-page PDFs with no
work experience section, making the output unusable as a resume.

Resume-Matcher's PDF parser cannot extract structured work experience from
RenderCV-rendered PDFs. Two input formats tested:

| Input PDF | ATS Score | Work Experiences Parsed | Result |
|---|---|---|---|
| LinkedIn export (`Profile (1).pdf`) | 69.6 | 0 | 1-page PDF: name, summary, nothing else |
| RenderCV classic (`Evan_O'Keefe_CV.pdf`) | 87.3 | 0 | 1-page PDF: name, summary, education, 4 skills |

In both cases the parser extracted the full text into `markdownOriginal` (12KB,
all 4 work roles with 41 bullet points), but could not map it to structured
`workExperience` objects. The LLM saw and acted on the work experience (46 changes
in `detailed_changes`), but the PDF renderer outputs from the structured model
which has `workExperience: []`.

#### Five Whys

1. **PDF has no work experience** → `resume_preview.workExperience` is `[]`.
2. **Structured model is empty** → PyMuPDF text extraction produces text in
   page-coordinate order, not logical reading order. Dates, locations, and page
   footers appear before/within section content, breaking the parser's heuristics.
3. **Text extraction order differs from reading order** → the RenderCV classic
   template positions elements at coordinates that don't serialize sequentially.
   The parser expects `[header] [date] [company] [role] [descriptions]` but gets
   `[date] [duration] [header] [company+role] [descriptions] [page footer]`.
4. **Parser heuristics assume a specific format** → Resume-Matcher's parser was
   designed for its own templates. It expects predictable section markers and
   field ordering that external PDFs don't guarantee.
5. **Root cause:** The tailor-resume skill (seam option B) injects externally-
   formatted PDFs into a parser that was built for the matcher's own templates.
   There is no intermediate normalization step between RenderCV PDF output and
   matcher PDF input. The text is all there (12KB markdown) but the structured
   extraction fails because the parser's layout assumptions don't match RenderCV's
   typographic conventions.

#### Candidates to test

- **DOCX upload instead of PDF:** The matcher API supports DOCX upload
  (`POST /api/v1/resumes/upload` accepts PDF/DOCX). Word processing formats
  preserve logical section structure and should parse correctly. RenderCV
  outputs Typst → convert to DOCX via Pandoc.
- **Different RenderCV theme:** The `engineering` theme may produce a text
  extraction order that the parser handles. Lower confidence than DOCX.
- **Build the resume inside Resume-Matcher's UI:** Uses the matcher as an
  end-to-end tool (its own parser + its own renderer), eliminating the format
  mismatch. Abandons the RenderCV YAML as source of truth.
- **Parse the RenderCV YAML directly:** Skip PDF parsing entirely — extract
  structured work experience from the YAML source and feed it to the matcher
  via a custom integration. Requires understanding the matcher's internal
  resume data model.

#### Notes

- The LLM improvement step works correctly — it sees the raw markdown text and
  produces useful `detailed_changes`. The failure is strictly in the parser →
  renderer pipeline.
- Education and skills sections parsed correctly (simpler structure). Only work
  experience fails — it's the most structurally complex section.
- Until resolved, the matcher's improve analysis (ATS score, summary, keyword
  gaps) is usable; the matcher's PDF output is not.

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
