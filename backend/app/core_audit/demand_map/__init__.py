"""Target Demand Map — Phase A.

Deterministic Cartesian expansion of a site's `target_config` (services, geo,
activities, competitor brands) against per-vertical seed templates, producing
`TargetCluster` rows tagged by `ClusterType` and `QualityTier`.

Phase A constraints (enforced here):
  - No LLM, no Yandex Suggest, no network calls.
  - No downstream reader of the output exists yet.
  - Hard guardrails: MAX_CLUSTERS_PER_SITE, MAX_CARTESIAN_DEPTH,
    MAX_GEO_PERMUTATIONS, MAX_PER_TEMPLATE.
  - Soft per-tier caps cause retiering, not drops.

Public surface:
  - `dto` — enums + frozen dataclasses (`TargetClusterDTO`, `TargetQueryDTO`,
    `SeedTemplate` — re-exported for convenience).
  - `models` — SQLAlchemy ORM for `target_clusters` and `target_queries`.
  - `guardrails` — size caps + retiering helpers.
  - `relevance` — deterministic business relevance score 0..1.
  - `quality` — quality tier classifier.
  - `expander` — the entry point `expand_for_site(profile, target_config)`.
"""

from app.core_audit.demand_map.dto import (
    ClusterSource,
    ClusterType,
    QualityTier,
    TargetClusterDTO,
    TargetQueryDTO,
    VolumeTier,
)
from app.core_audit.demand_map.expander import expand_for_site
from app.core_audit.demand_map.guardrails import (
    MAX_CARTESIAN_DEPTH,
    MAX_CLUSTERS_PER_SITE,
    MAX_GEO_PERMUTATIONS,
    MAX_PER_TEMPLATE,
    SOFT_CAPS_PER_TIER,
)
from app.core_audit.demand_map.quality import classify_quality_tier
from app.core_audit.demand_map.relevance import compute_relevance

__all__ = [
    "ClusterSource",
    "ClusterType",
    "QualityTier",
    "TargetClusterDTO",
    "TargetQueryDTO",
    "VolumeTier",
    "expand_for_site",
    "classify_quality_tier",
    "compute_relevance",
    "MAX_CLUSTERS_PER_SITE",
    "MAX_CARTESIAN_DEPTH",
    "MAX_GEO_PERMUTATIONS",
    "MAX_PER_TEMPLATE",
    "SOFT_CAPS_PER_TIER",
]
