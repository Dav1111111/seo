"""Deterministic quality-tier classifier.

Strictly table-driven: no LLM, no probability, no learned weights. Inputs:
  - cluster_type          : what kind of intent this cluster represents
  - business_relevance    : 0..1 precomputed score
  - is_competitor_brand   : hard-discard trigger
  - in_excluded_geo       : hard-discard trigger (site opted out of this geo)
  - in_excluded_service   : hard-discard trigger (site opted out of this service)
"""

from __future__ import annotations

from app.core_audit.demand_map.dto import ClusterType, QualityTier


def classify_quality_tier(
    *,
    cluster_type: ClusterType,
    business_relevance: float,
    is_competitor_brand: bool,
    in_excluded_geo: bool,
    in_excluded_service: bool,
) -> QualityTier:
    """Return the deterministic tier for a cluster candidate."""
    # Hard discards first — never promote anything excluded.
    if is_competitor_brand or in_excluded_geo or in_excluded_service:
        return QualityTier.discarded
    if business_relevance < 0.30:
        return QualityTier.discarded

    # Money intents: commercial_core + transactional_book.
    if cluster_type in (ClusterType.commercial_core, ClusterType.transactional_book):
        if business_relevance >= 0.70:
            return QualityTier.core
        if business_relevance >= 0.45:
            return QualityTier.secondary
        return QualityTier.exploratory

    # Supporting commercial / local / trust / activity — topped at secondary.
    if cluster_type in (
        ClusterType.commercial_modifier,
        ClusterType.local_geo,
        ClusterType.trust,
        ClusterType.activity,
    ):
        if business_relevance >= 0.60:
            return QualityTier.secondary
        return QualityTier.exploratory

    # Informational, seasonality, brand — never core in Phase A.
    return QualityTier.exploratory
