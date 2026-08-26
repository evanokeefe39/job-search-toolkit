"""Unit tests for the run-config + shared configutil loaders.

Covers merge precedence (CLI > named run > defaults > env > builtin), the
``tailor:``-section backward compatibility, and 0-as-unlimited max_pages.
"""

from __future__ import annotations

import pathlib

import pytest

from job_search_toolkit.configutil import DEFAULT_CONFIG_PATH, load_config_file, pick
from job_search_toolkit.run_config import RunConfig, load_run_config

_DEFAULTS = RunConfig()


def _write_config(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    p = tmp_path / "config.yaml"
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture
def run_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    return _write_config(
        tmp_path,
        """
defaults:
  http_timeout: 40
  guest_max_results: 100
  apify_timeout: 180.0
  llm_max_rpm: 30
runs:
  default: {}
  linkedin-france:
    guest_max_results: 200
    apify_timeout: 300.0
""",
    )


def test_defaults_when_no_config(tmp_path: pathlib.Path) -> None:
    cfg = load_run_config("default", tmp_path / "absent.yaml")
    assert cfg == _DEFAULTS


def test_defaults_section_applies(run_yaml: pathlib.Path) -> None:
    cfg = load_run_config("default", run_yaml)
    assert cfg.http_timeout == 40
    assert cfg.guest_max_results == 100
    assert cfg.apify_timeout == 180.0
    # Fields absent from config keep built-in defaults.
    assert cfg.http_retries == _DEFAULTS.http_retries


def test_named_run_overrides_defaults(run_yaml: pathlib.Path) -> None:
    cfg = load_run_config("linkedin-france", run_yaml)
    assert cfg.guest_max_results == 200
    assert cfg.apify_timeout == 300.0
    # Defaults-section value preserved where the named run is silent.
    assert cfg.http_timeout == 40


def test_unknown_run_falls_back_to_defaults(run_yaml: pathlib.Path) -> None:
    cfg = load_run_config("nope", run_yaml)
    assert cfg.guest_max_results == 100
    assert cfg.http_timeout == 40


def test_cli_max_pages_overrides_config(run_yaml: pathlib.Path) -> None:
    assert load_run_config("default", run_yaml, max_pages=15).max_pages == 15


def test_max_pages_zero_means_unlimited(run_yaml: pathlib.Path) -> None:
    cfg = load_run_config("default", run_yaml, max_pages=0)
    assert cfg.max_pages is None


def test_max_pages_from_config_zero_is_unlimited(tmp_path: pathlib.Path) -> None:
    p = _write_config(tmp_path, "defaults:\n  max_pages: 0\n")
    assert load_run_config("default", p).max_pages is None


# --- configutil.pick precedence ---

def test_pick_precedence_cli_over_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MAX_RPM", "60")
    assert pick(90, "LLM_MAX_RPM", {"llm_max_rpm": 30}, 30) == 90


def test_pick_env_over_file(monkeypatch) -> None:
    # env vars are returned as strings (the caller applies the type cast).
    monkeypatch.setenv("LLM_MAX_RPM", "60")
    assert pick(None, "LLM_MAX_RPM", {"llm_max_rpm": 30}, 30) == "60"


def test_pick_file_over_default() -> None:
    assert pick(None, "LLM_MAX_RPM", {"llm_max_rpm": 30}, 90) == 30


def test_pick_default_fallback() -> None:
    assert pick(None, "LLM_MAX_RPM", {}, 90) == 90


def test_pick_none_env_name_skips_file_lookup() -> None:
    # env_name=None means there is no config key to derive; run-config fields
    # with no env var use `_field_value` (covered by test_defaults_section_applies),
    # not `pick`. `pick` returns the default here.
    assert pick(None, None, {"guest_max_results": 200}, 100) == 100


# --- configutil file loading ---

def test_load_config_file_missing_returns_empty(tmp_path: pathlib.Path) -> None:
    assert load_config_file(tmp_path / "absent.yaml") == {}


def test_load_config_file_unparseable_returns_empty(tmp_path: pathlib.Path) -> None:
    p = _write_config(tmp_path, "key: [1, 2\n")  # unclosed flow sequence -> ParseError
    assert load_config_file(p) == {}


def test_default_config_path_is_a_path() -> None:
    assert isinstance(DEFAULT_CONFIG_PATH, pathlib.Path)


# --- tailor section backward compatibility ---

def test_tailor_section_and_flat_fallback(tmp_path: pathlib.Path, monkeypatch) -> None:
    from job_search_toolkit.automation.tailor.config import load_config

    # Nested `tailor:` section wins.
    nested = _write_config(tmp_path, "tailor:\n  llm_max_tokens: 4000\n  tailor_level: moderate\n")
    cfg = load_config(nested)
    assert cfg.max_tokens == 4000
    assert cfg.level == "moderate"
    assert cfg.model == "deepseek-chat"  # untouched default

    # Flat top-level keys still load (backward compat).
    flat = _write_config(tmp_path, "llm_max_tokens: 5000\n")
    cfg = load_config(flat)
    assert cfg.max_tokens == 5000

    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)
    monkeypatch.delenv("TAILOR_LEVEL", raising=False)
