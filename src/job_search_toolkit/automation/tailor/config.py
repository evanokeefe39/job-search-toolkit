"""Tailor pipeline configuration loader.

Precedence (highest first):
    1. explicit CLI args (passed in by scripts/tailor_resume.py)
    2. environment variables (LLM_MODEL, LLM_BASE_URL, LLM_API_KEY, LLM_CLIENT)
    3. config.yaml at repo root (gitignored)
    4. built-in defaults

Exported dataclass ``TailorConfig`` carries every tunable so callers never
reach for os.environ directly. ``load_config()`` merges the three sources.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PKG_DIR = Path(__file__).resolve().parent


def _resolve_config_path() -> Path:
    """Config file lookup: JOB_SEARCH_CONFIG > ~/.config/... > ./config.yaml > package.

    - Explicit: JOB_SEARCH_CONFIG=/path/to/config.yaml
    - pip install: ~/.config/job-search-toolkit/config.yaml (XDG)
    - repo checkout: ./config.yaml (cwd) — preserves the historical repo-root file
    - last resort: config.yaml next to this module (editable installs / vendored)
    """
    env_path = os.getenv("JOB_SEARCH_CONFIG")
    if env_path:
        return Path(env_path)
    xdg = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "job-search-toolkit" / "config.yaml"
    if xdg.exists():
        return xdg
    cwd_config = Path.cwd() / "config.yaml"
    if cwd_config.exists():
        return cwd_config
    return PKG_DIR / "config.yaml"


REPO_ROOT = Path.cwd()
DEFAULT_CONFIG_PATH = _resolve_config_path()

# Package-bundled tone guidance ships with the wheel; user override wins.
DEFAULT_TONE_PATH = PKG_DIR / "TONE.txt"

# Sentinel distinguishing "CLI explicitly disabled tone" from "not provided".
TONE_NONE = object()


@dataclass
class TailorConfig:
    # LLM connection
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    llm_client: str = "pydantic_ai"  # pydantic_ai | json_mode
    temperature: float = 0.2
    max_tokens: int = 8000

    # Tailoring behavior
    level: str = "relaxed"  # relaxed | moderate | aggressive
    max_highlights: int = 5
    tone_file: str | None = str(DEFAULT_TONE_PATH)
    # Bullet ranking when trimming: "impact_first" (user priority #1 —
    # competence/excellence) or "jd_relevance" (JD keyword fit first).
    highlight_preference: str = "impact_first"
    # UP3: allow cutting/merging low-value experience roles entirely.
    merge_low_value: bool = True

    # Master resume path (CLI overridable; user data, never shipped in the package)
    master_yaml: Path = Path("resume") / "cv.yaml"

    # Which sources actually contributed (for diagnostics/debugging)
    cli_overrides: dict = field(default_factory=dict)


_DEFAULTS = TailorConfig()


def _load_config_file(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _pick(cli_val, env_name: str, file_cfg: dict, default):
    """CLI > env > config.yaml > default."""
    if cli_val is not None:
        return cli_val
    env = os.getenv(env_name)
    if env is not None and env != "":
        return env
    key = env_name.lower()
    if key in file_cfg and file_cfg[key] is not None:
        return file_cfg[key]
    return default


def load_config(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    llm_client: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    level: str | None = None,
    max_highlights: int | None = None,
    highlight_preference: str | None = None,
    merge_low_value: bool | None = None,
    tone_file: str | None | object = None,
    master_yaml: Path | None = None,
) -> TailorConfig:
    """Merge config.yaml + env + explicit CLI values into a TailorConfig.

    A CLI value of ``None`` means "not provided" and falls through to
    env/config/default. ``tone_file=TONE_NONE`` means "explicitly no tone".
    """
    file_cfg = _load_config_file(config_path)

    cfg = TailorConfig(
        model=str(_pick(model, "LLM_MODEL", file_cfg, _DEFAULTS.model)),
        base_url=str(_pick(base_url, "LLM_BASE_URL", file_cfg, _DEFAULTS.base_url)),
        api_key=api_key if api_key is not None else (
            os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
        ),
        llm_client=str(_pick(llm_client, "LLM_CLIENT", file_cfg, _DEFAULTS.llm_client)),
        temperature=float(
            _pick(temperature, "LLM_TEMPERATURE", file_cfg, _DEFAULTS.temperature)
        ),
        max_tokens=int(
            _pick(max_tokens, "LLM_MAX_TOKENS", file_cfg, _DEFAULTS.max_tokens)
        ),
        level=str(_pick(level, "TAILOR_LEVEL", file_cfg, _DEFAULTS.level)),
        max_highlights=int(
            _pick(max_highlights, "TAILOR_MAX_HIGHLIGHTS", file_cfg, _DEFAULTS.max_highlights)
        ),
        highlight_preference=str(
            _pick(highlight_preference, "TAILOR_HIGHLIGHT_PREFERENCE",
                  file_cfg, _DEFAULTS.highlight_preference)
        ),
        merge_low_value=bool(
            _pick(merge_low_value, "TAILOR_MERGE_LOW_VALUE",
                  file_cfg, _DEFAULTS.merge_low_value)
        ),
        tone_file=(None if tone_file is TONE_NONE
                   else _pick(tone_file, "TAILOR_TONE_FILE", file_cfg, _DEFAULTS.tone_file)),
        master_yaml=master_yaml or _DEFAULTS.master_yaml,
    )
    cfg.cli_overrides = {
        k: v for k, v in {
            "model": model, "base_url": base_url, "api_key": api_key,
            "llm_client": llm_client, "temperature": temperature,
            "max_tokens": max_tokens, "level": level,
            "max_highlights": max_highlights,
            "highlight_preference": highlight_preference,
            "merge_low_value": merge_low_value,
            "tone_file": tone_file,
        }.items() if v is not None
    }
    return cfg
