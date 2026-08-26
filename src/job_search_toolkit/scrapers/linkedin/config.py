"""LinkedIn adapter configuration.

Config lives in the gitignored ``job_search_preferences.yaml`` under a
``linkedin:`` section:

    linkedin:
      backend: apify            # posts backend: "apify" | "tavily"
      guest_jobs: true          # true => jobs via the free LinkedIn guest API
      country_code: fr          # search locale (ISO 3166-1 alpha-2, lowercase)
      language_code: fr         # search language (ISO 639-1, lowercase)
      technology_list: null     # optional path to a keyword file; null = built-in
      queries:
        posts: [ ... ]          # search queries targeting /posts/
        jobs:  [ ... ]          # search queries targeting /jobs/view/

Absent keys fall back to the defaults here. API tokens are read from the
environment by the discovery backends (``APIFY_TOKEN``, ``TAVILY_API_KEY``),
not from this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class LinkedInConfig:
    backend: str = "apify"            # posts backend: "apify" | "tavily"
    guest_jobs: bool = True           # jobs via LinkedIn guest API when True
    country_code: str = "fr"
    language_code: str = "fr"
    technology_list: str | None = None
    post_queries: tuple[str, ...] = field(default_factory=tuple)
    job_queries: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_preferences(cls, path: str | Path | None = None) -> "LinkedInConfig":
        """Load the ``linkedin:`` section from ``job_search_preferences.yaml``.

        ``path`` defaults to ``job_search_preferences.yaml`` in the repo root.
        A missing file or missing section yields a fully-default config.
        """
        p = Path(path) if path else Path("job_search_preferences.yaml")
        if not p.exists():
            return cls()
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        section = raw.get("linkedin") or {}

        queries = section.get("queries") or {}
        posts = tuple(str(q) for q in (queries.get("posts") or []))
        jobs = tuple(str(q) for q in (queries.get("jobs") or []))

        tech_list = section.get("technology_list")
        return cls(
            backend=str(section.get("backend", "apify")).lower(),
            guest_jobs=bool(section.get("guest_jobs", True)),
            country_code=str(section.get("country_code", "fr")).lower(),
            language_code=str(section.get("language_code", "fr")).lower(),
            technology_list=str(tech_list) if tech_list else None,
            post_queries=posts,
            job_queries=jobs,
        )
