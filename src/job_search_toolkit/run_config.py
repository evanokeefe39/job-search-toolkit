"""Run-configuration loader for the pipeline, scrapers, and LinkedIn adapter.

Holds run *mechanics*: timeouts, retries, page sizes, limits, LLM rate limits.
Search *criteria* (roles, locations, LinkedIn queries) stay in the gitignored
``job_search_preferences.yaml``; API secrets stay in ``.env``.

Precedence (highest first):
    1. CLI args (``max_pages``, selected via ``config_name``)
    2. named run config (``runs.<name>`` in config.yaml), merged over ``defaults``
    3. config.yaml ``defaults`` section
    4. environment fallback (backward compat for keys that had env vars)
    5. built-in defaults

Loading a named config with ``config_name`` is just:
    cfg = load_run_config("linkedin-france")
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from job_search_toolkit.configutil import DEFAULT_CONFIG_PATH, load_config_file


@dataclass(frozen=True)
class RunConfig:
    """Tunable run parameters, all with built-in fallbacks.

    Static protocol constants (endpoints, headers, status-code sets, regexes)
    deliberately stay in the modules that use them; only genuinely tunable
    knobs live here.
    """

    # HTTP / fetch (board scrapers + LinkedIn page fetch)
    http_timeout: float = 30.0
    http_retries: int = 2
    http_backoff: float = 1.5
    max_pages: int | None = None  # None = unlimited

    # LinkedIn guest jobs API
    guest_page_size: int = 10  # cards per guest-API page
    guest_start_step: int = 25  # start offset increments
    guest_max_results: int = 100  # total job cap per discovery pass

    # Tavily discovery backend
    tavily_max_results: int = 10
    tavily_rate_limit_sleep: float = 1.0

    # Apify discovery backend
    apify_timeout: float = 180.0
    apify_poll_interval: float = 5.0

    # LinkedIn profile scraper
    profile_timeout: float = 180.0

    # LLM enrichment pipeline
    llm_max_rpm: int = 30
    llm_concurrency: int = 5
    enrichment_version: int = 1
    enrich_company_max: int = 50  # cap on the auto-created company-news queue per run

    # LLM connection (pipeline; env LLM_PROVIDER/LLM_MODEL/LLM_BASE_URL fallback)
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com/v1"

    # Per-board page/limit knobs
    faruse_page_size: int = 50
    freework_radius: int = 30
    hiringcafe_max_pages: int = 50
    # WTTJ (sitemap crawl) / Built In France (listing crawl) — both opt-in and
    # result-capped so a run never balloons (see tasks/plans/new-sources-*.md).
    wttj_max_jobs: int = 200  # max offer pages fetched per run
    builtin_max_pages: int = 5  # max listing pages crawled per run


_DEFAULTS = RunConfig()

# Fields whose value may also come from an env var (backward compatibility).
# Precedence for these: config file > env > default.
_ENV_FALLBACK = {
    "max_pages": "MAX_PAGES",
    "llm_max_rpm": "LLM_MAX_RPM",
    "llm_concurrency": "LLM_CONCURRENCY",
    "enrichment_version": "ENRICHMENT_VERSION",
    "llm_provider": "LLM_PROVIDER",
    "llm_model": "LLM_MODEL",
    "llm_base_url": "LLM_BASE_URL",
}

_INT_FIELDS = {
    "http_retries",
    "guest_page_size",
    "guest_start_step",
    "guest_max_results",
    "tavily_max_results",
    "llm_max_rpm",
    "llm_concurrency",
    "enrichment_version",
    "enrich_company_max",
    "faruse_page_size",
    "freework_radius",
    "hiringcafe_max_pages",
    "wttj_max_jobs",
    "builtin_max_pages",
}
_FLOAT_FIELDS = {
    "http_timeout",
    "http_backoff",
    "tavily_rate_limit_sleep",
    "apify_timeout",
    "apify_poll_interval",
    "profile_timeout",
}
_STR_FIELDS = {"llm_provider", "llm_model", "llm_base_url"}


def _merged_section(file_cfg: dict, config_name: str) -> dict:
    """Merge ``defaults`` + ``runs.<name>`` (name wins) into one dict."""
    defaults = file_cfg.get("defaults") or {}
    named = (file_cfg.get("runs") or {}).get(config_name) or {}
    merged = dict(defaults)
    for key, value in named.items():
        if value is not None:
            merged[key] = value
    return merged


def _coerce(value: Any, cast: Callable[[Any], Any], default: Any, field: str) -> Any:
    """Apply ``cast`` to ``value``, warning and falling back on failure.

    A config typo (e.g. ``max_pages: "50x"``) warns instead of silently being
    ignored, so drift is not silent.
    """
    try:
        return cast(value)
    except (TypeError, ValueError):
        warnings.warn(f"config: ignoring invalid value for {field!r}: {value!r}")
        return default


def _field_value(field: str, merged: dict, default: Any, cast: Callable[[Any], Any]) -> Any:
    """Resolve one field: config file > env fallback > built-in default.

    Config-file keys are the field names directly (not derived from env-var
    names), since many tunables have no environment variable.
    """
    if field in merged and merged[field] is not None:
        return _coerce(merged[field], cast, default, field)
    env_name = _ENV_FALLBACK.get(field)
    if env_name is not None:
        env = os.getenv(env_name)
        if env is not None and env != "":
            return _coerce(env, cast, default, field)
    return default


def get_run_config(config_path: Path = DEFAULT_CONFIG_PATH) -> "RunConfig":
    """Resolve the run selected by the RUN_CONFIG env var (default: 'default').

    Called at point-of-use (not import time) so the pipeline's ``--config
    <name>`` selection reaches every consumer. Direct CLIs honor ``RUN_CONFIG``
    too when exported.
    """
    return load_run_config(os.getenv("RUN_CONFIG") or "default", config_path=config_path)


def load_run_config(
    config_name: str = "default",
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    max_pages: int | None = None,
) -> RunConfig:
    """Merge config.yaml ``defaults`` + ``runs.<name>`` + env + CLI into a RunConfig.

    ``config_name`` selects a named run config (``runs.<name>``); unknown names
    silently yield just the ``defaults`` section (no error). ``max_pages`` is a
    direct CLI override (``None`` = not provided); 0 means unlimited.
    """
    file_cfg = load_config_file(config_path)
    merged = _merged_section(file_cfg, config_name)

    if config_name != "default" and config_name not in (file_cfg.get("runs") or {}):
        warnings.warn(f"config: unknown run {config_name!r}; using defaults")

    def val(field: str, cast: Callable[[Any], Any]) -> Any:
        return _field_value(field, merged, getattr(_DEFAULTS, field), cast)

    # max_pages: CLI > config file > env > default; 0/empty = unlimited (None).
    resolved_max_pages: int | None
    if max_pages is not None:
        resolved_max_pages = max_pages or None
    else:
        resolved_max_pages = val("max_pages", int) or None

    kwargs: dict[str, Any] = {"max_pages": resolved_max_pages}
    for field in _INT_FIELDS:
        kwargs[field] = val(field, int)
    for field in _FLOAT_FIELDS:
        kwargs[field] = val(field, float)
    for field in _STR_FIELDS:
        kwargs[field] = str(val(field, str))
    return RunConfig(**kwargs)
