"""LinkedIn source adapter — backend-agnostic recruiter-post + job discovery.

Discover (Apify / Tavily) → filter → direct fetch → JSON-LD parse → dedup →
deterministic tech scan → candidate pool (human gate).

Re-exports are filled in as the modules land; see ``models.py`` for the
record contracts shared across the sub-modules.
"""

from __future__ import annotations

from job_search_toolkit.linkedin.models import (
    ContentQuality,
    JobRecord,
    Location,
    PostRecord,
)

__all__ = ["ContentQuality", "JobRecord", "Location", "PostRecord"]
