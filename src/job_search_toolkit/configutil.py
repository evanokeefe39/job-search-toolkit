"""Shared config resolution + precedence helpers (single repo convention).

Reused by the tailor config (``automation.tailor.config``) and the run config
(``run_config``) so there is exactly one implementation of "where
``config.yaml`` lives" and "CLI > env > file > default" in the codebase.

Precedence (highest first):
    1. explicit CLI arg
    2. environment variable
    3. config.yaml (gitignored; template ``config.example.yaml`` is tracked)
    4. built-in default
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

PKG_DIR = Path(__file__).resolve().parent


def resolve_yaml_path(filename: str, env_var: str | None) -> Path:
    """Locate ``filename``: $ENV_VAR > XDG > ./filename > package.

    - Explicit: ``$ENV_VAR=/path/to/filename``
    - pip install: ``~/.config/job-search-toolkit/filename`` (XDG)
    - repo checkout: ``./filename`` (cwd)
    - last resort: ``filename`` next to this module
    """
    if env_var is not None:
        env_path = os.getenv(env_var)
        if env_path:
            return Path(env_path)
    xdg = (
        Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "job-search-toolkit"
        / filename
    )
    if xdg.exists():
        return xdg
    cwd_path = Path.cwd() / filename
    if cwd_path.exists():
        return cwd_path
    return PKG_DIR / filename


def resolve_config_path() -> Path:
    """Locate config.yaml (run mechanics): JOB_SEARCH_CONFIG > XDG > cwd > pkg."""
    return resolve_yaml_path("config.yaml", "JOB_SEARCH_CONFIG")


DEFAULT_CONFIG_PATH = resolve_config_path()


def resolve_tailor_preferences_path() -> Path:
    """Locate tailor_resume_preferences.yaml: TAILOR_RESUME_CONFIG > XDG > cwd > pkg."""
    return resolve_yaml_path("tailor_resume_preferences.yaml", "TAILOR_RESUME_CONFIG")


DEFAULT_TAILOR_PREFERENCES_PATH = resolve_tailor_preferences_path()


def load_config_file(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Read a config YAML into a dict; a missing or unparseable file is {}."""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def pick(cli_val: Any, env_name: str | None, file_cfg: dict, default: Any) -> Any:
    """CLI > env > config.yaml > default.

    A ``cli_val`` of ``None`` means "not provided" and falls through to
    env/config/default. ``env_name`` may be ``None`` to skip the env lookup
    (a key present only in the config file). The config-file key is the env
    var name lowercased (``LLM_MODEL`` -> ``llm_model``).
    """
    if cli_val is not None:
        return cli_val
    if env_name is not None:
        env = os.getenv(env_name)
        if env is not None and env != "":
            return env
    key = env_name.lower() if env_name else None
    if key is not None and key in file_cfg and file_cfg[key] is not None:
        return file_cfg[key]
    return default
