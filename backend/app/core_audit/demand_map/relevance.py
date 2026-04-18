"""Deterministic business-relevance score for a target cluster.

Score is 0..1, four weighted signals:
  - r_service (0.30): the cluster's filled service slot is in the site's
    declared `services` list.
  - r_geo     (0.25): the cluster's geo slot hits primary (1.0) vs
    secondary (0.6) vs neither (0.0).
  - r_intent  (0.25): commercial and transactional types score high,
    informational and competitor brand low.
  - r_template(0.20): template confidence — all v1 templates = 1.0.

Rounded to 3 decimals. No randomness. Unit-testable with pure dicts.
"""

from __future__ import annotations

from typing import Mapping

from app.core_audit.demand_map.dto import ClusterType

_INTENT_STRENGTH: dict[ClusterType, float] = {
    ClusterType.commercial_core: 1.0,
    ClusterType.transactional_book: 1.0,
    ClusterType.commercial_modifier: 0.85,
    ClusterType.local_geo: 0.8,
    ClusterType.activity: 0.8,
    ClusterType.trust: 0.6,
    ClusterType.informational_dest: 0.5,
    ClusterType.seasonality: 0.5,
    ClusterType.informational_prep: 0.4,
    ClusterType.brand: 0.3,
    ClusterType.competitor_brand: 0.0,
}


def compute_relevance(
    *,
    cluster_type: ClusterType,
    filled_slots: Mapping[str, str],
    target_config: Mapping[str, object],
) -> float:
    """Return a float in [0, 1], rounded to 3 decimals."""
    # Service match (binary).
    service_val = filled_slots.get("service") or filled_slots.get("activity")
    declared_services = set(target_config.get("services", []) or [])
    r_service = 1.0 if service_val and service_val in declared_services else 0.0

    # Geo match — primary wins over secondary.
    geo_val = (
        filled_slots.get("city")
        or filled_slots.get("destination")
        or filled_slots.get("region")
        or filled_slots.get("pickup_city")
    )
    primary = set(target_config.get("geo_primary", []) or [])
    secondary = set(target_config.get("geo_secondary", []) or [])
    if geo_val and geo_val in primary:
        r_geo = 1.0
    elif geo_val and geo_val in secondary:
        r_geo = 0.6
    else:
        r_geo = 0.0

    # Intent strength.
    r_intent = _INTENT_STRENGTH.get(cluster_type, 0.5)

    # Template confidence (all v1 templates = 1.0).
    r_template = 1.0

    score = 0.30 * r_service + 0.25 * r_geo + 0.25 * r_intent + 0.20 * r_template
    # Clip into [0, 1] defensively.
    score = max(0.0, min(1.0, score))
    return round(score, 3)
