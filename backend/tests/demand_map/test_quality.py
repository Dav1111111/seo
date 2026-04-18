"""Unit tests for app.core_audit.demand_map.quality."""

from __future__ import annotations

import pytest

from app.core_audit.demand_map.dto import ClusterType, QualityTier
from app.core_audit.demand_map.quality import classify_quality_tier


def _classify(**overrides):
    base = dict(
        cluster_type=ClusterType.commercial_core,
        business_relevance=0.8,
        is_competitor_brand=False,
        in_excluded_geo=False,
        in_excluded_service=False,
    )
    base.update(overrides)
    return classify_quality_tier(**base)


@pytest.mark.parametrize("flag", ["is_competitor_brand", "in_excluded_geo", "in_excluded_service"])
def test_hard_discards_always_discarded(flag):
    tier = _classify(**{flag: True})
    assert tier == QualityTier.discarded


def test_sub_threshold_relevance_discarded():
    tier = _classify(business_relevance=0.10)
    assert tier == QualityTier.discarded


def test_commercial_core_matrix():
    assert _classify(business_relevance=0.95) == QualityTier.core
    assert _classify(business_relevance=0.70) == QualityTier.core
    assert _classify(business_relevance=0.55) == QualityTier.secondary
    assert _classify(business_relevance=0.45) == QualityTier.secondary
    assert _classify(business_relevance=0.40) == QualityTier.exploratory


def test_transactional_book_matrix():
    t = lambda r: _classify(cluster_type=ClusterType.transactional_book, business_relevance=r)
    assert t(0.9) == QualityTier.core
    assert t(0.5) == QualityTier.secondary
    assert t(0.35) == QualityTier.exploratory


def test_supporting_types_never_core():
    for ct in (
        ClusterType.commercial_modifier,
        ClusterType.local_geo,
        ClusterType.trust,
        ClusterType.activity,
    ):
        assert _classify(cluster_type=ct, business_relevance=0.95) == QualityTier.secondary
        assert _classify(cluster_type=ct, business_relevance=0.62) == QualityTier.secondary
        assert _classify(cluster_type=ct, business_relevance=0.55) == QualityTier.exploratory


def test_info_and_seasonality_always_exploratory_or_discarded():
    for ct in (
        ClusterType.informational_dest,
        ClusterType.informational_prep,
        ClusterType.seasonality,
        ClusterType.brand,
    ):
        assert _classify(cluster_type=ct, business_relevance=0.95) == QualityTier.exploratory
        assert _classify(cluster_type=ct, business_relevance=0.40) == QualityTier.exploratory
        assert _classify(cluster_type=ct, business_relevance=0.20) == QualityTier.discarded


def test_competitor_brand_type_plus_flag_still_discarded():
    tier = classify_quality_tier(
        cluster_type=ClusterType.competitor_brand,
        business_relevance=0.9,
        is_competitor_brand=True,
        in_excluded_geo=False,
        in_excluded_service=False,
    )
    assert tier == QualityTier.discarded
