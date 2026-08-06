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
# Provider: "deepseek" or "gemini". Each uses the same OpenAI-compatible client.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")

_PROVIDERS = {
    "deepseek": {
        "api_key": os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        "model": os.getenv("LLM_MODEL", "deepseek-chat"),
    },
    "gemini": {
        "api_key": os.getenv("GEMINI_API_KEY", ""),
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.0-flash",
    },
}

_provider = _PROVIDERS.get(LLM_PROVIDER, _PROVIDERS["deepseek"])
LLM_API_KEY = _provider["api_key"]
LLM_BASE_URL = _provider["base_url"]
LLM_MODEL = _provider["model"]

# --- Rate limiting ---
LLM_MAX_RPM = int(os.getenv("LLM_MAX_RPM", "30"))
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "5"))
