"""Tests for the pipeline LLM/enrichment config threading + env restore.

Validates the review fix: named run configs (RUN_CONFIG env) reach the LLM
client, enrichment version, and that run_pipeline restores the env on exit.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from job_search_toolkit.run_config import RunConfig


def test_run_pipeline_restores_run_config_env(monkeypatch) -> None:
    from job_search_toolkit.pipelines.jd import run as run_mod

    monkeypatch.setattr(
        run_mod.dg, "materialize", lambda *a, **k: SimpleNamespace(success=True)
    )
    monkeypatch.setenv("RUN_CONFIG", "PREV")
    monkeypatch.delenv("RUN_MAX_PAGES", raising=False)

    ok = run_mod.run_pipeline(config_name="linkedin-france", max_pages=7)
    assert ok is True
    # Restored to the pre-call value; the newly-set RUN_MAX_PAGES was removed.
    assert os.environ.get("RUN_CONFIG") == "PREV"
    assert "RUN_MAX_PAGES" not in os.environ


def test_run_pipeline_cleans_env_when_absent_before(monkeypatch) -> None:
    from job_search_toolkit.pipelines.jd import run as run_mod

    monkeypatch.setattr(
        run_mod.dg, "materialize", lambda *a, **k: SimpleNamespace(success=True)
    )
    monkeypatch.delenv("RUN_CONFIG", raising=False)
    monkeypatch.delenv("RUN_MAX_PAGES", raising=False)

    ok = run_mod.run_pipeline(config_name="x")
    assert ok is True
    assert "RUN_CONFIG" not in os.environ
    assert "RUN_MAX_PAGES" not in os.environ


def test_llm_client_uses_run_config(monkeypatch) -> None:
    from job_search_toolkit.pipelines.jd.resources import llm_client as lc

    monkeypatch.setattr(
        lc,
        "get_run_config",
        lambda *a, **k: RunConfig(
            llm_model="test-model", llm_base_url="http://cfg", llm_concurrency=3, llm_max_rpm=10
        ),
    )
    c = lc.LLMClient(api_key="test-key")
    assert c._model == "test-model"
    assert c._min_interval == 60.0 / 10
    assert str(c._client.base_url).rstrip("/") == "http://cfg"


def test_get_enrichment_version_honors_named_run(monkeypatch) -> None:
    from job_search_toolkit.pipelines.jd import config as jdconfig

    jdconfig._ENRICHMENT_VERSION_CACHE = None
    monkeypatch.setattr(
        jdconfig, "get_run_config", lambda *a, **k: RunConfig(enrichment_version=7)
    )
    assert jdconfig.get_enrichment_version() == 7
    # Cached: a second call does not re-resolve.
    jdconfig._ENRICHMENT_VERSION_CACHE = None
