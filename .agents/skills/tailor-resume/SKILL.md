---
name: tailor-resume
description: Tailor the master CV to a specific job description: start Resume-Matcher, run the CV+JD through it, present the diff log for human approval, apply approved changes to a tailored YAML copy, render the final PDF, audit against the master, and update the tracker. Use when asked to tailor a resume/CV for a job posting, improve ATS fit/scoring against a JD, or produce the cv_tailored.pdf for an application folder.
---

# Tailor Resume (per-application ATS tailoring)

Playbook for one application folder: drive Resume-Matcher (Docker, localhost:8000),
present its diff log for human approval, apply only approved changes to a tailored
RenderCV YAML copy, render the final PDF, audit against the master, update the
tracker, and tear the service down.

## Non-negotiables (pinned conventions — do not revisit)

- **Seam decision B (verbatim):** Resume-Matcher is an ADVISOR. Its exported PDF
  is NEVER the submission artifact. Its detailed_changes diff log is reviewed by
  the human; only human-approved changes are applied to a tailored copy of the
  RenderCV YAML; the final PDF is rendered from our own YAML with RenderCV.
- **Fabrication rule:** never accept any change that adds skills, metrics, or
  claims absent from the master resume. Such proposals are rejected out of hand —
  the audit in step 7 is a backstop, not the primary control.
- **Public repo:** this repo is PUBLIC on GitHub. Never write personal data
  anywhere except the gitignored paths: `resume/`, `applications/`, `tracker.csv`.
  Never commit, never push.

## Playbook

1. **Preflight — application folder.**
   - Determine the target folder `applications/YYYY-MM-DD_<company-slug>_<role-slug>/`
     (from the caller, or the tracker row with `status=tailoring`).
   - Verify the folder exists AND contains `jd.md`. If either is missing:
     `STOP and present to the human: the application folder does not exist yet — run the new-application skill first.` Do not improvise a folder or a JD.
   - Check the Docker daemon: run `docker info`. If it fails (daemon not running):
     `STOP and present to the human: Docker Desktop is not running — please start it, then tell me to continue.` Wait for the human's go-ahead before proceeding.

2. **Start and health-check Resume-Matcher.**
   - Start the service: `docker compose -f services/docker-compose.yml up -d`
   - Poll `GET http://localhost:8000/api/v1/health` until it returns HTTP 200, up
     to 120 s (e.g. every 5 s):
     ```bash
     for i in $(seq 1 24); do
       code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/health)
       [ "$code" = "200" ] && echo "healthy after $((i*5))s" && break
       sleep 5
     done
     ```
   - If still not 200 after 120 s: `STOP and present to the human: Resume-Matcher did not become healthy within 120 s (last HTTP status: <code>) — see failure handling below.`

3. **Render the master resume.**
   - `uv run rendercv render resume/cv.yaml`
   - The master PDF lands at `rendercv_output/cv.pdf` (adjacent to the YAML).
     Verify it exists; this PDF is the upload input in step 4.

4. **Drive the Resume-Matcher API.**
   - **Upload the resume** (multipart, PDF only):
     ```bash
     curl -s -F "file=@rendercv_output/cv.pdf" http://localhost:8000/api/v1/resumes/upload
     ```
     Parse `resume_id` from the response. If the server rejects the field name,
     confirm the exact multipart field from the error message or the OpenAPI docs
     at `http://localhost:8000/docs` — do not guess repeatedly.
   - **Upload the job:** read `applications/<folder>/jd.md` in full and POST
     `{"job_descriptions": [<jd text>], "resume_id": "<resume_id>"}` to
     `http://localhost:8000/api/v1/jobs/upload` → `job_id` (first element of the
     returned array). Use an eval cell so the JD text needs no shell escaping:
     ```python
     import json, urllib.request

     jd = open(r"applications/<folder>/jd.md", encoding="utf-8").read()
     body = json.dumps({"job_descriptions": [jd], "resume_id": "<resume_id>"}).encode("utf-8")
     req = urllib.request.Request(
         "http://localhost:8000/api/v1/jobs/upload",
         data=body, headers={"Content-Type": "application/json"},
     )
     print(urllib.request.urlopen(req).read().decode("utf-8"))
     ```
   - **Improve:** POST `{"resume_id": "<resume_id>", "job_id": "<job_id>"}` to
     `http://localhost:8000/api/v1/resumes/improve` (same eval-cell pattern).
     Capture the FULL JSON response — it carries `ats_score` and `detailed_changes`.
     Optionally append the raw response to `applications/<folder>/notes.md`
     (timestamped) for traceability.

5. **Human review gate — the ONLY thing that may change the resume.**
   - Present to the human, VERBATIM, every entry of `detailed_changes` (what the
     matcher proposes, where, and why) plus the `ats_score`.
   - `STOP and present to the human: review the diff log above. Approve or reject each proposed change — I will apply only what you approve.`
   - Do NOT apply anything without explicit human approval. Do NOT apply wholesale
     ("accept all"). Any change that would add skills, metrics, or claims absent
     from the master resume is rejected by the fabrication rule before it reaches
     the human — flag it as rejected-by-rule and do not propose it.

6. **Apply approved changes to the tailored copy.**
   - Copy the master: `cp resume/cv.yaml applications/<folder>/cv_tailored.yaml`
   - Edit `applications/<folder>/cv_tailored.yaml` applying exactly the
     human-approved changes (and nothing else). Keep it valid RenderCV YAML.

7. **Audit against the master.**
   - `uv run python scripts/audit_alignment.py resume/cv.yaml applications/<folder>/cv_tailored.yaml`
   - Show its output to the human. Exit 0 = clean. If it prints strip decisions /
     exits 1: `STOP and present to the human: the audit stripped content from the tailored copy — adjudicate before continuing.` Do not proceed past this step until the human resolves it.

8. **Render the final PDF.**
   - `uv run rendercv render applications/<folder>/cv_tailored.yaml`
   - Copy the result into the folder as the convention artifact:
     `cp applications/<folder>/rendercv_output/cv_tailored.pdf applications/<folder>/cv_tailored.pdf`
   - Verify `applications/<folder>/cv_tailored.pdf` exists. This is the submission
     artifact — rendered from OUR YAML, never from Resume-Matcher.

9. **Update the tracker.**
   - In `tracker.csv`, update the row whose `folder` column matches this
     application folder: set `ats_score` to the score from step 5 and `status` to
     `ready`. If no row exists, append one (date_added, company, role, source,
     url, status=`ready`, folder, ats_score, notes). Never delete rows.

10. **Teardown.**
    - `docker compose -f services/docker-compose.yml down`
    - Never leave the service running after the session — this applies even when
      the playbook stopped early (steps 2, 5, or 7).

## Do not

- NEVER use Resume-Matcher's exported PDF (`GET /api/v1/resumes/{resume_id}/pdf`)
  as the submission artifact — seam decision B.
- NEVER auto-apply `detailed_changes` wholesale; every change needs explicit
  human approval.
- NEVER accept fabricated content: no skills, metrics, or claims absent from the
  master resume.
- NEVER leave services running after the session; always `docker compose ... down`.
- NEVER write personal data outside `resume/`, `applications/`, `tracker.csv`
  (repo is PUBLIC); never commit or push anything from this flow.
- NEVER improvise infrastructure: do not edit `services/docker-compose.yml`, do
  not restart containers manually, do not substitute another matcher or renderer.

## Failure handling

- Any unexpected failure — health never reaches 200 within 120 s, API 4xx/5xx
  that does not resolve on one identical retry, RenderCV render error, audit
  exit 1 — STOP and report to the human with the exact command and its output.
  Do not improvise infrastructure or "fix" the service by changing compose,
  env, or ports. If an API call fails with a transient error, one retry of the
  same request is allowed; then stop and report.
