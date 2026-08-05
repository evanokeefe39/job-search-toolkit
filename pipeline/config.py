"""Shared configuration for the enrichment pipeline.

All paths are relative to the repo root.
Set LLM_API_KEY and LLM_BASE_URL in the environment or a .env file.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- I/O paths ---
RAW_JOBS = REPO_ROOT / "freework_jobs.json"
ENRICHED_JOBS = REPO_ROOT / "freework_jobs_enriched.json"

# --- LLM configuration ---
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# --- Rate limiting ---
LLM_MAX_RPM = int(os.getenv("LLM_MAX_RPM", "30"))
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "5"))

# --- Stage toggles (field names to check for idempotency) ---
STAGE_FIELDS = {
    "translate": "description_en",
    "extract_tech": "extracted_technologies",
    "classify_vertical": "company_vertical",
    "company_stats": "company_stats",
    "score": "scores",
}
