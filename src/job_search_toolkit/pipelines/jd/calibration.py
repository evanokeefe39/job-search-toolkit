"""WS1 Epic 1.2: SQL-evidenced weight calibration.

Computes, per scoring feature, the advance rate (applied -> interview/offer)
in the HIGH feature-score band vs the LOW band directly from the DuckDB
warehouse (``silver.fact_outcome_event`` JOIN ``silver.jobs``). The suggested
weight delta is deterministic from that evidence — never LLM-proposed.

Weights change ONLY through :func:`apply_calibration`, which writes:

1. a versioned history file ``scoring_config.versions/v{N}.yaml`` (v1 is the
   baseline, recording the bundled default as ``previous_weights``), and
2. the ACTIVE weight file that ``score_engine`` loads with precedence over
   the packaged default.

Both paths resolve via env vars (``JST_ACTIVE_WEIGHTS_FILE``,
``JST_WEIGHTS_VERSIONS_DIR``) so tests never touch the repo's real files.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

FEATURES = [
    "pay",
    "flexibility",
    "low_responsibility",
    "tech_match",
    "company_quality",
]

# --- Suggestion rule constants (deterministic) -------------------------------
DELTA = 0.02          # bounded per-feature delta before renormalization
MIN_COUNT = 5         # min applied events in the HIGH band to trust the rate
HIGH_BAND = 0.6       # feature score >= HIGH_BAND -> high band
LOW_BAND = 0.4        # feature score <= LOW_BAND -> low band (middle excluded)

STAGES_ADVANCED = ("interview", "offer")

DEFAULT_ACTIVE_FILE = Path("data/scoring_active.yaml")
DEFAULT_VERSIONS_DIR = Path("data/scoring_config.versions")


def active_weights_file() -> Path:
    """Active override file score_engine loads with precedence (env-reeditable)."""
    return Path(os.environ.get("JST_ACTIVE_WEIGHTS_FILE") or DEFAULT_ACTIVE_FILE)


def versions_dir() -> Path:
    """Versioned weight history directory (env-reeditable)."""
    return Path(os.environ.get("JST_WEIGHTS_VERSIONS_DIR") or DEFAULT_VERSIONS_DIR)


# --- SQL evidence ------------------------------------------------------------

def band_evidence(db_path: Path, feature: str) -> dict:
    """Advance-rate evidence for one feature, straight from the warehouse.

    Returns counts + rates for the low and high score bands over jobs that
    have an 'applied' outcome event. Rate = advanced / applied (0 if none).
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(f"""
            WITH applied AS (
                SELECT e.job_id AS job_id,
                       CAST(json_extract_string(j.scores, '$.{feature}') AS DOUBLE) AS f
                FROM silver.fact_outcome_event e
                JOIN silver.jobs j ON j.id = e.job_id
                WHERE e.stage = 'applied'
            ),
            advanced AS (
                SELECT DISTINCT job_id FROM silver.fact_outcome_event
                WHERE stage IN {STAGES_ADVANCED!r}
            )
            SELECT CASE WHEN a.f >= {HIGH_BAND} THEN 'high'
                        WHEN a.f <= {LOW_BAND} THEN 'low'
                        ELSE NULL END AS band,
                   COUNT(*) AS applied_n,
                   COUNT(DISTINCT ad.job_id) AS advanced_n
            FROM applied a
            LEFT JOIN advanced ad ON ad.job_id = a.job_id
            WHERE a.f IS NOT NULL
            GROUP BY 1
        """).fetchall()
    finally:
        con.close()

    ev = {
        "low_count": 0, "low_advanced": 0, "low_rate": 0.0,
        "high_count": 0, "high_advanced": 0, "high_rate": 0.0,
    }
    for band, applied_n, advanced_n in rows:
        if band is None:
            continue
        key = "high" if band == "high" else "low"
        ev[f"{key}_count"] = int(applied_n)
        ev[f"{key}_advanced"] = int(advanced_n)
        ev[f"{key}_rate"] = round(advanced_n / applied_n, 4) if applied_n else 0.0
    return ev


def compute_suggestion(db_path: Path) -> dict | None:
    """Deterministic weight suggestion from SQL evidence, or None.

    Per-feature gating: a feature is eligible when its HIGH band has at least
    MIN_COUNT applied events. An eligible feature gets +DELTA when its high
    band advances more than its low band, -DELTA when less, else 0; an
    ineligible feature (not enough data) gets 0 (left at its current weight).
    If NO feature is eligible, there is not enough data — return None (never
    suggest). The resulting vector is renormalized to sum to 1.0.
    """
    evidence: dict[str, dict] = {}
    deltas: dict[str, float] = {}
    any_eligible = False
    for feature in FEATURES:
        ev = band_evidence(db_path, feature)
        evidence[feature] = ev
        if ev["high_count"] < MIN_COUNT:
            deltas[feature] = 0.0
            continue
        any_eligible = True
        if ev["high_rate"] > ev["low_rate"]:
            deltas[feature] = DELTA
        elif ev["high_rate"] < ev["low_rate"]:
            deltas[feature] = -DELTA
        else:
            deltas[feature] = 0.0
    if not any_eligible:
        return None

    from .score_engine import _load_default_weights

    current = _load_default_weights()
    proposed = {f: current[f] + deltas[f] for f in FEATURES}
    total = sum(proposed.values())
    weights = {f: round(proposed[f] / total, 6) for f in FEATURES}
    return {"weights": weights, "evidence": evidence, "deltas": deltas}


# --- Versioned history + active override -------------------------------------

def _next_version(versions_dir: Path) -> int:
    existing = [int(p.stem[1:]) for p in versions_dir.glob("v*.yaml")
                if p.stem[1:].isdigit()]
    return max(existing, default=0) + 1


def apply_calibration(db_path: Path) -> dict:
    """Apply the suggestion (gated): write version history + active file.

    Raises ``RuntimeError`` when there is not enough data — no file is
    touched in that case.
    """
    suggestion = compute_suggestion(db_path)
    if suggestion is None:
        raise RuntimeError("not enough data — no calibration")

    vdir = versions_dir()
    vdir.mkdir(parents=True, exist_ok=True)
    n = _next_version(vdir)

    from .score_engine import _load_default_weights

    previous = _load_default_weights()
    version_file = vdir / f"v{n}.yaml"
    payload = {
        "version": n,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "previous_weights": previous,
        "weights": suggestion["weights"],
        "evidence": suggestion["evidence"],
        "deltas": suggestion["deltas"],
    }
    version_file.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    active = active_weights_file()
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(
        yaml.safe_dump({"version": n, "weights": suggestion["weights"]},
                       sort_keys=False),
        encoding="utf-8",
    )
    return {"version": n, "version_file": version_file,
            "active_file": active, "weights": suggestion["weights"]}


def load_version(vdir: Path, n: int) -> dict:
    """Load one version file (``previous_weights`` enables restoration)."""
    return yaml.safe_load((vdir / f"v{n}.yaml").read_text(encoding="utf-8"))
