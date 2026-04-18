"""Unit tests for app.core_audit.demand_map.relevance."""

from __future__ import annotations

from app.core_audit.demand_map.dto import ClusterType
from app.core_audit.demand_map.relevance import compute_relevance


BASE_CONFIG = {
    "services": ["экскурсии", "туры"],
    "geo_primary": ["сочи"],
    "geo_secondary": ["адлер"],
}


def test_relevance_in_zero_one():
    r = compute_relevance(
        cluster_type=ClusterType.commercial_core,
        filled_slots={"service": "экскурсии", "city": "сочи"},
        target_config=BASE_CONFIG,
    )
    assert 0.0 <= r <= 1.0


def test_relevance_full_match_hits_ceiling():
    r = compute_relevance(
        cluster_type=ClusterType.commercial_core,
        filled_slots={"service": "экскурсии", "city": "сочи"},
        target_config=BASE_CONFIG,
    )
    # 0.30 + 0.25 + 0.25 + 0.20 = 1.0 exactly.
    assert r == 1.0


def test_relevance_secondary_geo_scores_lower_than_primary():
    primary = compute_relevance(
        cluster_type=ClusterType.commercial_core,
        filled_slots={"service": "экскурсии", "city": "сочи"},
        target_config=BASE_CONFIG,
    )
    secondary = compute_relevance(
        cluster_type=ClusterType.commercial_core,
        filled_slots={"service": "экскурсии", "city": "адлер"},
        target_config=BASE_CONFIG,
    )
    assert primary > secondary


def test_relevance_competitor_brand_is_low():
    r = compute_relevance(
        cluster_type=ClusterType.competitor_brand,
        filled_slots={"service": "экскурсии", "city": "сочи"},
        target_config=BASE_CONFIG,
    )
    # r_intent = 0.0 for competitor_brand, so score capped at 0.75.
    assert r < 0.80


def test_relevance_missing_service_drops_weight():
    r = compute_relevance(
        cluster_type=ClusterType.commercial_core,
        filled_slots={"service": "unknown", "city": "сочи"},
        target_config=BASE_CONFIG,
    )
    # Without the 0.30 service bump, max achievable is 0.70.
    assert r <= 0.70 + 1e-9


def test_relevance_no_slots_only_intent_plus_template():
    r = compute_relevance(
        cluster_type=ClusterType.informational_dest,
        filled_slots={},
        target_config=BASE_CONFIG,
    )
    # r_intent = 0.5 * 0.25 = 0.125, r_template = 1.0 * 0.20 = 0.20 -> 0.325
    assert 0.30 <= r <= 0.40


def test_relevance_deterministic_same_input():
    args = dict(
        cluster_type=ClusterType.commercial_modifier,
        filled_slots={"service": "туры", "city": "сочи"},
        target_config=BASE_CONFIG,
    )
    a = compute_relevance(**args)
    b = compute_relevance(**args)
    assert a == b
