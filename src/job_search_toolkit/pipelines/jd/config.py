"""Shared configuration for the enrichment pipeline.

All data paths resolve relative to the working directory (the repo root when
run from a checkout; the user's project dir when pip-installed) into the
medallion layout: data/bronze (immutable per-run snapshots), data/silver
(file exports bridging to DuckDB), data/gold (legacy), data/warehouse (the
DuckDB jobs.db holding the silver table + gold views).
Set LLM_API_KEY and LLM_BASE_URL in the environment or a .env file.
"""

import os
from pathlib import Path

from job_search_toolkit.run_config import load_run_config

WORK_DIR = Path.cwd()

# --- Medallion data layout (see data/README.md) ---
BRONZE_DIR = WORK_DIR / "data" / "bronze"
SILVER_DIR = WORK_DIR / "data" / "silver"
WAREHOUSE_DIR = WORK_DIR / "data" / "warehouse"

# Bronze history: immutable per-run snapshots + manifest, and the DuckDB
# warehouse (silver.jobs table + gold views live in this single file).
BRONZE_RUNS = BRONZE_DIR / "runs.json"
WAREHOUSE_DB = WAREHOUSE_DIR / "jobs.db"


def ensure_data_dirs() -> None:
    """Create the medallion directories if missing (idempotent)."""
    for d in (BRONZE_DIR, SILVER_DIR, WAREHOUSE_DIR):
        d.mkdir(parents=True, exist_ok=True)

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
# Enrichment schema version — bump to force re-enrichment of all rows
# (each enrichment asset resets its stage outputs for rows at older versions).
# Rate limits + enrichment version come from RunConfig (run_config.py), which
# falls back to the legacy env vars (ENRICHMENT_VERSION / LLM_MAX_RPM /
# LLM_CONCURRENCY) when not in config.yaml.
_RUN_CFG = load_run_config()
ENRICHMENT_VERSION = _RUN_CFG.enrichment_version
LLM_MAX_RPM = _RUN_CFG.llm_max_rpm
LLM_CONCURRENCY = _RUN_CFG.llm_concurrency
