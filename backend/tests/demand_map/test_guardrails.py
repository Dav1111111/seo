"""Unit tests for app.core_audit.demand_map.guardrails."""

from __future__ import annotations

import uuid

import pytest

from app.core_audit.demand_map.dto import (
    ClusterSource,
    ClusterType,
    QualityTier,
    TargetClusterDTO,
    VolumeTier,
)
from app.core_audit.demand_map.guardrails import (
    GuardrailError,
    MAX_CLUSTERS_PER_SITE,
    MAX_GEO_PERMUTATIONS,
    SOFT_CAPS_PER_TIER,
    cap_geo_permutations,
    enforce_global_cap,
    enforce_tier_caps,
)
from app.core_audit.intent_codes import IntentCode


def _make(cluster_key: str, tier: QualityTier = QualityTier.core) -> TargetClusterDTO:
    return TargetClusterDTO(
        site_id=uuid.uuid4(),
        cluster_key=cluster_key,
        name_ru=cluster_key,
        intent_code=IntentCode.COMM_CATEGORY,
        cluster_type=ClusterType.commercial_core,
        quality_tier=tier,
        keywords=(cluster_key,),
        seed_slots={},
        is_brand=False,
        is_competitor_brand=False,
        expected_volume_tier=VolumeTier.s,
        business_relevance=0.8,
        source=ClusterSource.cartesian,
    )


# ---------- global hard cap ------------------------------------------------


def test_hard_cap_raises_over_limit():
    clusters = [_make(f"k{i}") for i in range(MAX_CLUSTERS_PER_SITE + 1)]
    with pytest.raises(GuardrailError):
        enforce_global_cap(clusters, max_n=MAX_CLUSTERS_PER_SITE)


def test_hard_cap_accepts_at_limit():
    clusters = [_make(f"k{i}") for i in range(MAX_CLUSTERS_PER_SITE)]
    out = enforce_global_cap(clusters, max_n=MAX_CLUSTERS_PER_SITE)
    assert len(out) == MAX_CLUSTERS_PER_SITE


def test_hard_cap_with_small_bound():
    with pytest.raises(GuardrailError):
        enforce_global_cap([_make("a"), _make("b")], max_n=1)


# ---------- tier soft caps retiering --------------------------------------


def test_tier_core_overflow_downgrades_to_secondary():
    core_cap = SOFT_CAPS_PER_TIER[QualityTier.core]
    clusters = [_make(f"c{i}", QualityTier.core) for i in range(core_cap + 5)]
    out = enforce_tier_caps(clusters)
    # No clusters dropped.
    assert len(out) == len(clusters)
    cores = [c for c in out if c.quality_tier == QualityTier.core]
    secondaries = [c for c in out if c.quality_tier == QualityTier.secondary]
    assert len(cores) == core_cap
    assert len(secondaries) == 5


def test_tier_cascading_downgrade_to_exploratory():
    """Overflow cascades core -> secondary -> exploratory.

    Exploratory has room in this test so everything fits. No cluster ever
    gets auto-retiered into `discarded` — that tier is reserved for
    hard-rule hits only.
    """
    caps = {
        QualityTier.core: 2,
        QualityTier.secondary: 2,
        QualityTier.exploratory: 5,
    }
    clusters = [_make(f"k{i}", QualityTier.core) for i in range(6)]
    out = enforce_tier_caps(clusters, caps)
    tiers = [c.quality_tier for c in out]
    assert tiers.count(QualityTier.core) == 2
    assert tiers.count(QualityTier.secondary) == 2
    assert tiers.count(QualityTier.exploratory) == 2
    assert tiers.count(QualityTier.discarded) == 0
    assert len(out) == 6


def test_tier_overflow_past_exploratory_stays_exploratory():
    """When exploratory is also full, overflow stays in exploratory.

    The retiering cascade refuses to push clusters into `discarded`. The
    final global hard cap is the only mechanism that can drop clusters.
    """
    caps = {
        QualityTier.core: 1,
        QualityTier.secondary: 1,
        QualityTier.exploratory: 1,
    }
    clusters = [_make(f"k{i}", QualityTier.core) for i in range(5)]
    out = enforce_tier_caps(clusters, caps)
    tiers = [c.quality_tier for c in out]
    assert len(out) == 5
    assert tiers.count(QualityTier.discarded) == 0
    # One each in core/secondary, the rest collapse into exploratory.
    assert tiers.count(QualityTier.core) == 1
    assert tiers.count(QualityTier.secondary) == 1
    assert tiers.count(QualityTier.exploratory) == 3


def test_discarded_never_retiered():
    clusters = [_make(f"d{i}", QualityTier.discarded) for i in range(10)]
    out = enforce_tier_caps(clusters)
    assert all(c.quality_tier == QualityTier.discarded for c in out)


# ---------- geo cap --------------------------------------------------------


def test_geo_cap_trims_secondary_first():
    primary = [f"p{i}" for i in range(10)]
    secondary = [f"s{i}" for i in range(60)]
    p, s = cap_geo_permutations(primary, secondary, cap=MAX_GEO_PERMUTATIONS)
    assert len(p) == 10
    assert len(p) + len(s) == MAX_GEO_PERMUTATIONS


def test_geo_cap_deduplicates_across_lists():
    p, s = cap_geo_permutations(["a", "b"], ["b", "c"], cap=10)
    assert p == ["a", "b"]
    assert s == ["c"]


def test_geo_cap_primary_alone_over_limit():
    primary = [f"p{i}" for i in range(70)]
    p, s = cap_geo_permutations(primary, [], cap=MAX_GEO_PERMUTATIONS)
    assert len(p) == MAX_GEO_PERMUTATIONS
    assert s == []


def test_geo_cap_under_limit_returns_all():
    p, s = cap_geo_permutations(["a"], ["b"], cap=10)
    assert p == ["a"]
    assert s == ["b"]
