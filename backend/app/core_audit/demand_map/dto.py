"""In-memory DTOs + enums for the Target Demand Map.

Kept deliberately separate from `models.py` (ORM) so pure-Python callers
(expander, tests) never require a DB connection or SQLAlchemy import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping
from uuid import UUID

from app.core_audit.intent_codes import IntentCode


class ClusterType(str, Enum):
    """Structural kind of a target cluster.

    Drives proposer logic downstream (Phase B+) — for Phase A it only shapes
    relevance weights and the quality-tier classifier.
    """

    commercial_core = "commercial_core"
    commercial_modifier = "commercial_modifier"
    local_geo = "local_geo"
    informational_dest = "informational_dest"
    informational_prep = "informational_prep"
    transactional_book = "transactional_book"
    trust = "trust"
    seasonality = "seasonality"
    brand = "brand"
    competitor_brand = "competitor_brand"
    activity = "activity"


class QualityTier(str, Enum):
    """Deterministic quality bucket for a cluster."""

    core = "core"                 # primary revenue driver, fully relevant
    secondary = "secondary"       # supporting cluster, partial relevance
    exploratory = "exploratory"   # informational / seasonal / low confidence
    discarded = "discarded"       # competitor brand, excluded geo, below threshold


class VolumeTier(str, Enum):
    xs = "xs"
    s = "s"
    m = "m"
    l = "l"
    xl = "xl"


class ClusterSource(str, Enum):
    profile_seed = "profile_seed"
    cartesian = "cartesian"
    llm = "llm"
    suggest = "suggest"
    observed = "observed"


@dataclass(frozen=True)
class SeedTemplate:
    """A vertical-level template that produces clusters after slot filling.

    `required_slots` lists slot names that MUST have a value for the template
    to expand (empty -> skip). `optional_slots` are filled when available but
    do not gate expansion. `seasonal_months` annotates seasonality clusters
    so downstream Phase B/C can time-gate recommendations.
    """

    pattern: str
    cluster_type: ClusterType
    intent_code: IntentCode
    default_volume_tier: VolumeTier = VolumeTier.s
    required_slots: tuple[str, ...] = ()
    optional_slots: tuple[str, ...] = ()
    seasonal_months: tuple[int, ...] = ()


@dataclass(frozen=True)
class TargetClusterDTO:
    """Pure-Python mirror of a `target_clusters` row (pre-persistence)."""

    site_id: UUID
    cluster_key: str
    name_ru: str
    intent_code: IntentCode
    cluster_type: ClusterType
    quality_tier: QualityTier
    keywords: tuple[str, ...]
    seed_slots: Mapping[str, str] = field(default_factory=dict)
    is_brand: bool = False
    is_competitor_brand: bool = False
    expected_volume_tier: VolumeTier = VolumeTier.s
    business_relevance: float = 0.0
    source: ClusterSource = ClusterSource.cartesian


@dataclass(frozen=True)
class TargetQueryDTO:
    """Pure-Python mirror of a `target_queries` row (pre-persistence)."""

    cluster_key: str
    query_text: str
    source: ClusterSource
    estimated_volume_tier: VolumeTier = VolumeTier.s
