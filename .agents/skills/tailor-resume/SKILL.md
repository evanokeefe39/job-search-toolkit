---
name: tailor-resume
description: Tailor the master CV to a specific job description: run scripts/tailor_resume.py with cv.yaml + jd.md, present the audit report for human approval, render the final PDF, update the tracker. Use when asked to tailor a resume/CV for a job posting, improve ATS fit/scoring against a JD, or produce the cv_tailored.pdf for an application folder.
---

# Tailor Resume (LLM-driven, per-application)

Replacement: `scripts/tailor_resume.py` (thin CLI) + `pipeline/tailor/`
(models, prompts, merge, audit, render). A single DeepSeek API call with
Pydantic-validated structured output. The LLM returns only content fields
(summary, highlights per role, skills); `merge_content()` slots them into a
deep-copy of the master RenderCV YAML. Retries once on validation failure
with the error fed back into the prompt for correction.

Playbook for one application folder.

## Non-negotiables

- **Master protection:** `resume/cv.yaml` is NEVER modified. `merge_content()`
  deep-copies the original; all mutations hit the copy. The script refuses to
  write to the master path.
- **Fabrication rule:** never accept any change that adds skills, metrics, or
  claims absent from the master resume. The `check_fabrication()` audit is the
  primary control; human review is the backstop.
- **Public repo:** this repo is PUBLIC on GitHub. Never write personal data
  anywhere except the gitignored paths: `resume/`, `applications/`, `tracker.csv`.
  Never commit, never push.
- **No Docker services.** The pipeline runs locally — no `docker compose`, no
  health checks, no port management.
- **Length target:** rich 1 page, 2 pages absolute maximum. Every bullet must
  earn its space — prune aggressively for JD relevance. See UP1 in
  `docs/ats_llm_rules.yaml`.
- **Sabbatical:** do NOT list in experience section. Mention briefly in Summary
  if relevant. Several in-flight projects may warrant experience placement later
  but are not ready. See UP2 in `docs/ats_llm_rules.yaml`.

## Playbook

1. **Preflight — application folder.**
   - Determine the target folder `applications/YYYY-MM-DD_<company-slug>_<role-slug>/`
     (from the caller, or the tracker row with `status=tailoring`).
   - Verify the folder exists AND contains `jd.md`. If either is missing:
     `STOP: the application folder does not exist yet — run the new-application skill first.`

2. **Run the tailoring pipeline.**
   ```bash
   uv run python scripts/tailor_resume.py \
       --jd applications/<folder>/jd.md \
       --output applications/<folder>/cv_tailored.yaml
   ```
   - `--yaml` defaults to `resume/cv.yaml` (the master).
   - `--output` is required here (we want the canonical path, not a timestamped one).
   - Pipeline: LLM call (~15s) → Pydantic validation → content-aware trim (max 5
     highlights per role, dropped bullets logged) → merge → UP2 sabbatical strip →
     audit → RenderCV PDF.
   - If the audit reports HARD fabrications: `STOP` and present to the human.
   - If the audit reports JD-derived additions (verify with human): present for review.
   - If the audit reports clean: proceed.

3. **Human review gate.**
   - Open `applications/<folder>/cv_tailored.yaml` and review:
     - Summary: JD-targeted? No first-person? 2-3 sentences?
     - Highlights: Relevant bullets kept? Irrelevant dropped? Max 5 per role?
     - Skills: Any fabricated additions?
     - Tone: Strong action verbs? No fabricated metrics?
   - If issues found: fix `cv_tailored.yaml` directly, then re-render:
     ```bash
     uv run rendercv render applications/<folder>/cv_tailored.yaml
     ```
   - If the resume needs significant changes: re-run the pipeline with adjusted
     prompt or different model (set `LLM_MODEL` env var).

4. **Verify PDF.**
   - Confirm `applications/<folder>/cv_tailored.pdf` exists (rendered by RenderCV
     in step 2, or manually in step 3).
   - Check page count (should be 1-2 pages).
   - Check for rendering artifacts (emoji, broken Unicode, overflow).

5. **Update the tracker.**
   - In `tracker.csv`, update the row: set `status` to `ready`.
   - If no row exists, append one. Never delete rows.

## Do not

- NEVER modify `resume/cv.yaml` directly — it is the source of truth.
- NEVER use Resume-Matcher or its Docker service — deprecated.
- NEVER accept fabricated skills, metrics, or claims absent from the master.
- NEVER write personal data outside `resume/`, `applications/`, `tracker.csv`.
- NEVER commit or push anything from this flow.

## Model selection

Default: `deepseek-chat` (fast, cheap, works). To use a different model:

```bash
export LLM_MODEL="kimi-k3"
export LLM_BASE_URL="https://api.moonshot.cn/v1"
export LLM_API_KEY="sk-..."
```

The pipeline supports any OpenAI-compatible endpoint.

## Failure handling

- **LLM returns invalid JSON or wrong schema:** the retry loop re-calls once
  with temperature=0.1 and the validation error fed into the prompt. If the
  retry also fails, the script exits with the raw response for debugging.
- **Pydantic validation fails:** same retry loop covers this — no garbage
  YAML is written.
- **RenderCV render fails:** check the error — usually a schema issue
  (empty strings where null expected). Fix `cv_tailored.yaml` and re-render.
- **Audit finds fabrication:** `STOP` and present to the human. Do not submit
  a resume with fabricated content.
- **Trim drops bullets:** check the `[TRIM]` log lines — dropped bullets are
  printed with overlap scores. If critical content was dropped, manually
  edit `cv_tailored.yaml` to restore it.

## Iteration

Each run produces a new `cv_tailored.yaml`. To compare iterations, use the
timestamped output mode (omit `--output`):

```bash
uv run python scripts/tailor_resume.py --jd applications/<folder>/jd.md
# Writes: applications/<folder>/cv_tailored_20260807_143052.yaml
```
