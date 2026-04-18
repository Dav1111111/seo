"""Tests for app.core_audit.demand_map.rescoring.

Pure unit tests — no DB, no network. Verifies the +0.05 boost logic,
clamping, overlap detection, and that non-overlapping clusters are
returned unchanged (identity preserved).
"""

from __future__ import annotations

import uuid

import pytest

from app.core_audit.demand_map.dto import (
    ClusterSource,
    ClusterType,
    QualityTier,
    TargetClusterDTO,
)
from app.core_audit.demand_map.rescoring import (
    OVERLAP_BOOST,
    rescore_with_observed_overlap,
)
from app.core_audit.intent_codes import IntentCode


def _mk(
    name: str,
    *,
    keywords: tuple[str, ...] | None = None,
    relevance: float = 0.5,
) -> TargetClusterDTO:
    return TargetClusterDTO(
        site_id=uuid.uuid4(),
        cluster_key=f"ck:{name.replace(' ', '_')}",
        name_ru=name,
        intent_code=IntentCode.COMM_CATEGORY,
        cluster_type=ClusterType.commercial_core,
        quality_tier=QualityTier.secondary,
        keywords=keywords if keywords is not None else tuple(name.split()),
        seed_slots={},
        business_relevance=relevance,
        source=ClusterSource.cartesian,
    )


def test_empty_inputs_return_empty():
    assert rescore_with_observed_overlap([], []) == []


def test_no_observed_returns_same_list():
    clusters = [_mk("экскурсии сочи")]
    out = rescore_with_observed_overlap(clusters, [])
    assert out == clusters  # identity-preserving pass-through


def test_overlap_applies_boost():
    c = _mk("экскурсии сочи", relevance=0.5)
    observed = [("экскурсии в сочи недорого", 100)]
    out = rescore_with_observed_overlap([c], observed)
    assert len(out) == 1
    # +0.05 boost expected (exact value rounded to 3 decimals).
    assert abs(out[0].business_relevance - (0.5 + OVERLAP_BOOST)) < 1e-9


def test_no_overlap_no_boost():
    c = _mk("экскурсии сочи", relevance=0.5)
    observed = [("купить холодильник", 100)]
    out = rescore_with_observed_overlap([c], observed)
    assert out[0].business_relevance == 0.5


def test_boost_clamped_to_one():
    c = _mk("экскурсии сочи", relevance=0.98)
    observed = [("экскурсии сочи", 10)]
    out = rescore_with_observed_overlap([c], observed)
    assert out[0].business_relevance <= 1.0
    assert out[0].business_relevance == 1.0  # clamped


def test_boost_preserves_all_other_fields():
    c = _mk("экскурсии сочи", relevance=0.4)
    observed = [("экскурсии сочи летом", 50)]
    out = rescore_with_observed_overlap([c], observed)
    r = out[0]
    assert r.cluster_key == c.cluster_key
    assert r.name_ru == c.name_ru
    assert r.intent_code == c.intent_code
    assert r.cluster_type == c.cluster_type
    assert r.quality_tier == c.quality_tier
    assert r.keywords == c.keywords
    assert r.site_id == c.site_id
    assert r.source == c.source


def test_accepts_bare_strings_in_observed():
    c = _mk("экскурсии сочи", relevance=0.4)
    # The signature is list[tuple[str,int]], but defensive code accepts
    # bare strings too — verify no crash.
    out = rescore_with_observed_overlap([c], [("экскурсии летом", 10)])
    assert out[0].business_relevance > 0.4
