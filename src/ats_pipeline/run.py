"""ATS Pipeline — Host-side orchestrator.

Fans out resume+JD to matchers (via HTTP to Docker services), applies
recommendations via DeepSeek rewriter, strips unverifiable content
deterministically, outputs N improved resumes + summary.

Usage: uv run python -m src.ats_pipeline.run data/resume.txt data/jd.txt
"""

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path("data")
OUTPUT_DIR = DATA_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LLM_API_KEY = os.environ["LLM_API_KEY"]
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

# Matcher service URLs (Docker compose)
ATS_CHECKER_URL = os.environ.get("ATS_CHECKER_URL", "http://localhost:8001")
ATSFLOW_URL = os.environ.get("ATSFLOW_URL", "http://localhost:3101")


@dataclass
class MatcherOutput:
    name: str
    method: str
    score: float
    issues: list[dict]
    recommendations: list[str]
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    improved_resume: str = ""
    latency_ms: float = 0.0
    raw: dict = field(default_factory=dict)


# ── Matcher 1: ats-resume-checker (HTTP) ────────────────────────────

async def run_ats_checker(resume_text: str, jd_text: str) -> MatcherOutput:
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{ATS_CHECKER_URL}/score",
            json={"resume_text": resume_text, "job_description": jd_text},
        )
        result = resp.json()

    return MatcherOutput(
        name="ats-resume-checker",
        method="tfidf-cosine",
        score=result["score"],
        matched_keywords=result.get("matched_keywords", []),
        missing_keywords=result.get("missing_keywords", []),
        recommendations=result.get("suggestions", []),
        issues=[{"severity": "info", "message": s} for s in result.get("suggestions", [])],
        latency_ms=(time.perf_counter() - t0) * 1000,
        raw=result,
    )


# ── Matcher 2: ATSFlow scanner (HTTP) ───────────────────────────────

async def run_atsflow(resume_text: str, jd_text: str) -> MatcherOutput:
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{ATSFLOW_URL}/analyze",
            json={"resume_text": resume_text, "job_description": jd_text},
        )
        result = resp.json()

    issues = [
        {
            "check": i.get("check", ""),
            "category": i.get("category", ""),
            "severity": i.get("severity", ""),
            "message": i.get("message", ""),
            "recommendation": i.get("recommendation", ""),
        }
        for i in result.get("issues", [])
    ]
    return MatcherOutput(
        name="atsflow-rules",
        method="30-rule-compliance",
        score=result.get("score", 0),
        issues=issues,
        recommendations=[
            f"[{i.get('impact','?')}] {i.get('check','')}: {i.get('recommendation','')}"
            for i in result.get("issues", []) if i.get("recommendation")
        ],
        latency_ms=(time.perf_counter() - t0) * 1000,
        raw=result,
    )


# ── Rewriter: DeepSeek applies recommendations ──────────────────────

REWRITER_SYSTEM = """You are a professional resume writer. Given a resume and a list of specific issues found by an ATS scanner, rewrite the resume to fix every issue.

RULES:
1. Return the COMPLETE rewritten resume in Markdown format
2. Fix every issue listed
3. Do NOT fabricate skills, technologies, certifications, job titles, companies, dates, or metrics that are not in the original resume. If the original says "improved performance", rewrite as "Improved performance" — do NOT invent "Improved performance by 30%"
4. Keep the same sections and structure as the original
5. Use strong action verbs: Designed, Built, Led, Optimized, Architected, Implemented
6. Remove weak language: "worked on", "helped with", "assisted in", "responsible for"
7. Remove personal pronouns (I, me, my)
8. Mirror keywords from the job description ONLY where they match your actual experience — do not add a skill just because the JD mentions it

Return ONLY the rewritten resume in Markdown. No explanations, no commentary."""


async def rewrite_resume(original: str, jd_text: str, matcher: MatcherOutput) -> str:
    issues_text = "\n".join(
        f"- [{i.get('severity', '?')}] {i.get('message', str(i))}"
        for i in matcher.issues[:15]
    )
    recs_text = "\n".join(f"- {r}" for r in matcher.recommendations[:10])

    user = f"""ORIGINAL RESUME:
{original}

JOB DESCRIPTION:
{jd_text[:2000]}

ISSUES FOUND BY {matcher.name} (score: {matcher.score:.0f}/100):
{issues_text}

RECOMMENDATIONS:
{recs_text}

Rewrite the resume to fix all of these issues. Return the complete rewritten resume in Markdown."""

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": REWRITER_SYSTEM},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 4000,
            },
        )
        data = resp.json()
        return data["choices"][0]["message"]["content"]


# ── Deterministic Alignment Check (mirrors Resume-Matcher Pass 3) ───

_METRIC_PATTERNS = [
    r"[\d,]+\.?\d*\s*%",
    r"by\s+[\d,]+\.?\d*\s*%?",
    r"\$\s?[\d,]+\.?\d*",
    r"[\d,]+\s*x",
    r"[\d,]+\.?\d*\s*(?:million|billion|thousand)\b",
    r"(?:over|more than|about|approx|roughly)\s+[\d,]+\s*[kKmMbB]?\b",
    r"[\d,]+\s*(?:records?|reports?|users?|customers?|clients?|systems?|"
    r"datasets?|sources?|requests?|transactions?|rows?|tables?|pipelines?|"
    r"models?|developers?|engineers?|dashboards?|team members?|data errors?)",
]
_METRIC_RE = re.compile("|".join(_METRIC_PATTERNS), re.IGNORECASE)

# Cleanup after metric removal: collapse double spaces, drop dangling "by "
_CLEANUP = re.compile(r"\s{2,}|\bby\s+(?=[.,;:]|$)", re.IGNORECASE)


def _extract_section(text: str, header: str) -> list[str]:
    """Return the lines under a Markdown ## header."""
    lines = text.split("\n")
    in_section = False
    out: list[str] = []
    for line in lines:
        if line.startswith("## "):
            in_section = line[3:].strip().lower() == header
            continue
        if in_section and line.strip():
            out.append(line)
    return out


def _normalize_skill_token(part: str) -> str:
    """Normalize a raw skill fragment to a comparable token.

    Strips markdown bold, bullets, label prefixes, parenthetical qualifiers
    (e.g. "JavaScript (basic)" -> "javascript"), and trailing punctuation
    (e.g. "Databricks)" -> "databricks").
    """
    part = part.replace("**", "").strip()       # drop markdown bold markers
    part = re.sub(r"^[*\-\s]+", "", part.strip())
    part = re.split(r":\s*", part)[-1].strip()
    part = re.sub(r"\s*\(.*?\)\s*$", "", part)  # trailing (qualifier)
    part = re.sub(r"\(.*?\)", "", part)         # inline (expansions)
    part = re.sub(r"[\s\-_]+", " ", part).strip()
    part = re.sub(r"[^a-z0-9+#.]+$", "", part.lower())
    return part


def _extract_skills(text: str) -> set[str]:
    """Flatten the Skills section into normalized skill tokens."""
    skills: set[str] = set()
    for line in _extract_section(text, "skills"):
        parts = re.split(r"[,|•\n]", line)
        for part in parts:
            token = _normalize_skill_token(part)
            if token and len(token) > 1:
                skills.add(token)
    return skills


def _original_numbers(text: str) -> set[str]:
    """All numeric tokens in the original (dates, phone, years — the legit ones)."""
    return set(re.findall(r"\d[\d,]*", text))


def strip_fabricated_content(original: str, rewrite: str) -> tuple[str, list[str]]:
    """Deterministically remove claims the original resume can't support.

    Returns (cleaned_rewrite, decisions) where each decision is a
    human-readable log line explaining what was stripped and why.
    """
    decisions: list[str] = []

    # 1. Skills: remove any skill in the rewrite not verifiable anywhere in the original
    orig_skills = _extract_skills(original)
    rewrite_skills = _extract_skills(rewrite)
    orig_lower = original.lower()
    added = set()
    for s in rewrite_skills:
        if s in orig_skills:
            continue  # in the Skills section — verifiable
        if s in orig_lower:
            continue  # mentioned in a bullet/project — still verifiable
        added.add(s)

    if added:
        new_lines: list[str] = []
        for line in rewrite.split("\n"):
            if line.startswith("## "):
                new_lines.append(line)
                continue
            if line.strip():
                line_lower = line.lower()
                removed_here = [s for s in added if s in line_lower]
                if removed_here:
                    line_skills = [s for s in _extract_skills(line) if s]
                    if line_skills and all(s in added for s in line_skills):
                        decisions.append(
                            f"skills: dropped '{line.strip()}' (not in original: {', '.join(removed_here)})"
                        )
                        continue
                    cleaned = line
                    for s in removed_here:
                        cleaned = re.sub(rf"\s*,\s*{re.escape(s)}\b", "", cleaned, flags=re.IGNORECASE)
                        cleaned = re.sub(rf"^\s*{re.escape(s)}\b\s*[,:]?\s*", "", cleaned, flags=re.IGNORECASE)
                        cleaned = re.sub(rf"\s*[-–]\s*{re.escape(s)}\b", "", cleaned, flags=re.IGNORECASE)
                    decisions.append(
                        f"skills: removed {', '.join(removed_here)} from '{line.strip()}'"
                    )
                    line = cleaned
                if line.strip():
                    new_lines.append(line)
            else:
                new_lines.append(line)
        rewrite = "\n".join(new_lines)

    # 2. Metrics: strip quantified claims whose numbers aren't in the original
    legit_numbers = _original_numbers(original)
    new_lines = []
    for line in rewrite.split("\n"):
        if line.startswith("## ") or not line.strip():
            new_lines.append(line)
            continue
        for match in _METRIC_RE.findall(line):
            nums = re.findall(r"\d[\d,]*", str(match))
            if nums and all(n not in legit_numbers for n in nums):
                decisions.append(
                    f"metrics: stripped '{match}' from: {line.strip()[:80]}"
                )
                line = line.replace(str(match), "").strip()
        # Tidy grammar artifacts left by stripping ("reducing deployment time by",
        # double spaces)
        if _METRIC_RE.search(line) is None:
            line = _CLEANUP.sub(" ", line).strip()
        new_lines.append(line)
    rewrite = "\n".join(new_lines)

    return rewrite, decisions


# ── Orchestrator ────────────────────────────────────────────────────

async def run(resume_path: str, jd_path: str):
    resume_text = Path(resume_path).read_text(encoding="utf-8")
    jd_text = Path(jd_path).read_text(encoding="utf-8")
    run_id = str(uuid.uuid4())[:8]
    t0 = time.perf_counter()

    print(f"=== ATS Pipeline {run_id} ===\n")
    print(f"Resume: {resume_path} ({len(resume_text.split())} words)")
    print(f"JD: {jd_path} ({len(jd_text.split())} words)\n")

    # Phase 1: Run matchers in parallel (HTTP to Docker services)
    print("--- Phase 1: Running matchers ---")
    tasks = [
        run_ats_checker(resume_text, jd_text),
        run_atsflow(resume_text, jd_text),
    ]
    matchers: list[MatcherOutput] = []
    for coro in asyncio.as_completed(tasks):
        try:
            m = await coro
            matchers.append(m)
            print(f"  {m.name}: score={m.score:.0f}, {len(m.issues)} issues, {m.latency_ms:.0f}ms")
        except Exception as e:
            print(f"  ERROR: {e}")

    # Phase 2: Rewrite using each matcher's recommendations
    print("\n--- Phase 2: Rewriting ---")
    for m in matchers:
        if not m.issues and not m.recommendations:
            print(f"  {m.name}: no issues to fix, skipping")
            m.improved_resume = resume_text
            continue

        print(f"  {m.name}: rewriting...")
        try:
            improved = await rewrite_resume(resume_text, jd_text, m)
            improved, decisions = strip_fabricated_content(resume_text, improved)
            m.improved_resume = improved
            m.raw["alignment_decisions"] = decisions
            out_path = OUTPUT_DIR / f"{run_id}_{m.name}.md"
            out_path.write_text(improved, encoding="utf-8")
            print(f"    -> {out_path} ({len(improved.split())} words)")
            for d in decisions:
                print(f"    [aligned] {d}")
        except Exception as e:
            print(f"    -> ERROR: {e}")
            m.improved_resume = resume_text

    # Phase 3: Summary
    runtime = time.perf_counter() - t0
    print(f"\n--- Summary (runtime: {runtime:.1f}s) ---")
    for m in matchers:
        wc = len(m.improved_resume.split()) if m.improved_resume else 0
        delta = wc - len(resume_text.split())
        print(f"  {m.name}: {m.score:.0f}/100 -> improved resume ({wc} words, {delta:+d})")

    # Write metrics
    summary = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "runtime_s": round(runtime, 1),
        "resume_path": str(resume_path),
        "jd_path": str(jd_path),
        "matchers": [
            {
                "name": m.name, "method": m.method, "score": m.score,
                "issues_count": len(m.issues), "recommendations_count": len(m.recommendations),
                "matched_keywords": m.matched_keywords[:20],
                "missing_keywords": m.missing_keywords[:20],
                "improved_resume_path": str(OUTPUT_DIR / f"{run_id}_{m.name}.md"),
                "alignment_decisions": m.raw.get("alignment_decisions", []),
                "latency_ms": m.latency_ms,
            }
            for m in matchers
        ],
    }
    summary_path = OUTPUT_DIR / f"{run_id}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSummary: {summary_path}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m src.ats_pipeline.run <resume.txt> <jd.txt>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1], sys.argv[2]))
