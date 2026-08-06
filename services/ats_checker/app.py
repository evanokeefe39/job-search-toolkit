"""ATS Resume Checker service — thin FastAPI wrapper around ats-resume-checker."""

import tempfile
from pathlib import Path

from ats_resume_checker import analyze_resume
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="ats-resume-checker")


class ScoreRequest(BaseModel):
    resume_text: str
    job_description: str


@app.get("/health")
def health():
    return {"status": "ok", "service": "ats-resume-checker"}


@app.post("/score")
def score(req: ScoreRequest):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(req.resume_text)
        tmp_path = f.name

    result = analyze_resume(tmp_path, req.job_description)
    Path(tmp_path).unlink(missing_ok=True)

    return {
        "service": "ats-resume-checker",
        "method": "tfidf-cosine",
        "score": result["ats_score"],
        "match_rate": result["match_rate"],
        "matched_keywords": result["matched_keywords"],
        "missing_keywords": result["missing_keywords"],
        "suggestions": result["suggestions"],
        "resume_word_count": result["resume_word_count"],
    }
