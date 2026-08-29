---
name: tailor-resume
description: Tailor the master CV to a specific job description: run scripts/tailor_resume.py with cv.yaml + jd.md, present the audit report for human approval, render the final PDF, record the funnel event in the tracker. Use when asked to tailor a resume/CV for a job posting, improve ATS fit/scoring against a JD, or produce the cv_tailored.pdf for an application folder.
---
## Requirements

Python 3.14+ and uv (package manager) are required. Install the toolkit with:
`pip install job-search-toolkit` (or `uv tool install job-search-toolkit`).


# Tailor Resume (LLM-driven, per-application)

Replacement: the `job-search-toolkit tailor run` CLI (Typer) + `src/job_search_toolkit/automation/tailor/`
(models, prompts, merge, audit, render, verify, reviewer). A single DeepSeek API call with
Pydantic-validated structured output via **pydantic-ai** (fallback: json_mode
client, `--llm-client json_mode`). The LLM returns only content fields
(summary, highlights per role, skills); `merge_content()` slots them into a
deep-copy of the master RenderCV YAML. Structured-output validation retries
automatically with the validation error fed back to the model.

Playbook for one application folder.

## Non-negotiables

- **Master protection:** `resume/cv.yaml` is NEVER modified. `merge_content()`
  deep-copies the original; all mutations hit the copy. The script refuses to
  write to the master path.
- **Fabrication rule:** never accept any change that adds skills, metrics, or
  claims absent from the master resume. The `check_fabrication()` audit is the
  primary control; human review is the backstop.
- **Public repo:** this repo is PUBLIC on GitHub. Never write personal data
  anywhere except the gitignored paths: `resume/`, `applications/`.
  Never commit, never push.
- **No Docker services.** The pipeline runs locally — no `docker compose`, no
  health checks, no port management.
- **Length target:** rich 1 page, 2 pages absolute maximum. Every bullet must
  earn its space — prune aggressively. See UP1 in `docs/ats_llm_rules.yaml`.
- **Sabbatical:** do NOT list in experience section. Mention briefly in Summary
  if relevant. Several in-flight projects may warrant experience placement later
  but are not ready. See UP2 in `docs/ats_llm_rules.yaml`.
- **Competence first:** impact/excellence outranks JD keyword fit when trimming
  (user priority #1). Concrete metrics, strong verbs, and scale beat generic
  JD-keyword matches.
- **No cross-company relabeling:** one role's content is never moved under
  another's company header. Low-value roles are cut or compressed, never merged
  across employers.

## Playbook

1. **Preflight — application folder.**
   - Determine the target folder `applications/YYYY-MM-DD_<company-slug>_<role-slug>/`
     (from the caller, or the tracker record with stage `tailoring` —
     `job-search-toolkit tracker current --job 'applications/<folder>'`).
   - Verify the folder exists AND contains `inputs/jd.md`. If either is missing:
     `STOP: the application folder does not exist yet — run the new-application skill first.`

2. **Run the tailoring pipeline.**
   ```bash
   job-search-toolkit tailor run \
       --yaml resume/cv.yaml \
       --jd applications/<folder>/inputs/jd.md \
       --output applications/<folder>/outputs/cv_tailored.yaml
   # Opt-in additions:
   #   --verify        verify the rendered PDF text layer after render
   #                   (blocks the ready transition on failure)
   #   --with-review   bounded drafter-reviewer: one critique + one
   #                   targeted revision after the first pass
   ```
   - `--yaml` defaults to `resume/cv.yaml` (the master).
   - `--output` is required here (we want the canonical path, not a timestamped one).
   - Config precedence: CLI args > env vars > `config.yaml` (repo root)
     > defaults. Override via flags or config: `--level aggressive`,
     `--model kimi-k3`, `--llm-client json_mode`,
     `--highlight-preference jd_relevance`, `--max-highlights 7`.
   - **Tone:** the package-bundled `TONE.txt` is injected as a TONE OF VOICE
     section by default; override with `--tone <file>` or disable with `--no-tone`.
   - Pipeline: LLM call (~15s) → Pydantic validation → impact-first trim (keeps
     the most impressive bullets per role: metrics, strong verbs, scale — max 5
     default; JD relevance secondary; dropped bullets logged with impact + JD
     scores) → merge → UP2 sabbatical strip → [aggressive role filter] → audit
     → RenderCV PDF.
   - Low-value roles may be CUT in relaxed/moderate (UP3, `--no-merge-low-value`
     to disable). Aggressive mode fixes the role set deterministically
     (Hancock + Modis) and never relabels content across companies.
   - If the audit reports HARD fabrications: `STOP` and present to the human.
   - If the audit reports JD-derived additions (verify with human): present for review.
   - If the audit reports clean: proceed.

3. **Human review gate.**
   - Open `applications/<folder>/outputs/cv_tailored.yaml` and review:
     - Summary: JD-targeted? No first-person? 2-3 sentences?
     - Highlights: Most impressive bullets kept? Irrelevant dropped? Max 5 per role?
     - Skills: Any fabricated additions?
     - Tone: Strong action verbs? No fabricated metrics?
     - Roles: Is each company's content still under its own header?
   - If issues found: fix `cv_tailored.yaml` directly, then re-render:
     ```bash
     uv run rendercv render applications/<folder>/outputs/cv_tailored.yaml
     ```
   - If the resume needs significant changes: re-run the pipeline with adjusted
     prompt or different model (`--model` / config.yaml).

4. **Verify PDF.**
   - Confirm `applications/<folder>/outputs/cv_tailored.pdf` exists (rendered by
     RenderCV in step 2, or manually in step 3).
   - Run the verification gate (required before `ready`):
     ```bash
     job-search-toolkit tailor verify \
         --pdf applications/<folder>/outputs/cv_tailored.pdf \
         --yaml resume/cv.yaml \
         --jd applications/<folder>/inputs/jd.md
     ```
   - A **FAIL** result BLOCKS the `ready` transition: do NOT record `ready`
     until verify passes. Fix the source (`cv_tailored.yaml`) or the render,
     re-render, and re-run `tailor verify`.
   - It checks: contact literals in the text layer, mojibake/private-use
     glyphs, section reading order, page count vs `verify_page_target`, and
     JD keyword coverage (covered / supported-missing / genuine-gap).
   - A healthy PDF prints `[VERIFY] OK — text layer intact.` and exits 0.

5. **Record the funnel event.**
   - Record the stage `ready` event via the tracker CLI (keyed on the folder
     slug; append-only and idempotent on identical events):

```bash
job-search-toolkit tracker record --job 'applications/YYYY-MM-DD_company-slug_role-slug' --stage 'ready' --ts '<today ISO-8601>'
```

   - If a real `ats_score` was produced, add it to the same event as a note:
     `--note 'ats_score=<n>'`. Never guess the number.
   - Never delete records or rewrite history; corrections are new events with
     an explanatory note.

## Do not

- NEVER modify `resume/cv.yaml` directly — it is the source of truth.
- NEVER use Resume-Matcher or its Docker service — deprecated.
- NEVER accept fabricated skills, metrics, or claims absent from the master.
- NEVER write personal data outside `resume/`, `applications/`.
- NEVER commit or push anything from this flow.
- NEVER move one role's content under another role's company name.

## Model selection

Default: `deepseek-chat` via pydantic-ai (`OpenAIChatModel`). Configurable via
CLI (`--model`, `--base-url`, `--llm-client`) or config.yaml — CLI > env >
config.yaml > defaults. To use a different model:

```bash
job-search-toolkit tailor run --yaml resume/cv.yaml --jd applications/<folder>/inputs/jd.md \
    --model kimi-k3 --base-url https://api.moonshot.cn/v1 --output applications/<folder>/outputs/cv_tailored.yaml
```

The pipeline supports any OpenAI-compatible endpoint. If pydantic-ai's
tool-calling structured output regresses on a provider, fall back to the
proven json_mode client: `--llm-client json_mode` (or `LLM_CLIENT=json_mode`,
or `llm_client: json_mode` in config.yaml).

## Failure handling

- **LLM returns invalid JSON or wrong schema:** pydantic-ai retries
  automatically with the validation error fed back; if it still fails, the
  CLI exits with the raw response for debugging.
- **Pydantic validation fails:** same retry path covers this — no garbage
  YAML is written.
- **RenderCV render fails:** check the error — usually a schema issue
  (empty strings where null expected). Fix `cv_tailored.yaml` and re-render.
- **Audit finds fabrication:** `STOP` and present to the human. Do not submit
  a resume with fabricated content.
- **Trim drops bullets:** check the `[TRIM]` log lines — dropped bullets are
  printed with master-overlap, impact, and JD-relevance scores. If critical
  content was dropped, manually edit `cv_tailored.yaml` to restore it.

## Iteration

Each run produces a new `cv_tailored.yaml`. To compare iterations, use the
timestamped output mode (omit `--output`):

```bash
job-search-toolkit tailor run --yaml resume/cv.yaml --jd applications/<folder>/inputs/jd.md
# Writes: applications/<folder>/outputs/cv_tailored_20260807_143052.yaml
```
