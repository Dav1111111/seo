"""Unit tests for app.core_audit.demand_map.expander."""

from __future__ import annotations

import types
import uuid

import pytest

from app.core_audit.demand_map.dto import (
    ClusterType,
    QualityTier,
    SeedTemplate,
    VolumeTier,
)
from app.core_audit.demand_map.expander import expand_for_site
from app.core_audit.demand_map.guardrails import (
    MAX_CLUSTERS_PER_SITE,
    GuardrailError,
)
from app.core_audit.intent_codes import IntentCode


def _profile(*templates: SeedTemplate) -> types.SimpleNamespace:
    return types.SimpleNamespace(seed_cluster_templates=tuple(templates))


CORE = SeedTemplate(
    pattern="{activity} {city}",
    cluster_type=ClusterType.commercial_core,
    intent_code=IntentCode.COMM_CATEGORY,
    default_volume_tier=VolumeTier.m,
    required_slots=("activity", "city"),
)

BRAND_T = SeedTemplate(
    pattern="{activity} {city} отзывы",
    cluster_type=ClusterType.trust,
    intent_code=IntentCode.TRUST_LEGAL,
    required_slots=("activity", "city"),
)


# ---------- basic expansion ------------------------------------------------


def test_empty_target_config_returns_empty():
    out = expand_for_site(_profile(CORE), {})
    assert out == []


def test_empty_profile_returns_empty():
    out = expand_for_site(_profile(), {"services": ["x"], "geo_primary": ["y"]})
    assert out == []


def test_basic_cartesian_expansion():
    cfg = {
        "services": ["экскурсии"],
        "geo_primary": ["сочи", "адлер"],
    }
    out = expand_for_site(_profile(CORE), cfg)
    assert len(out) == 2
    names = sorted(c.name_ru for c in out)
    assert names == ["экскурсии адлер", "экскурсии сочи"]


def test_deterministic_keys_across_runs():
    cfg = {
        "services": ["экскурсии", "туры"],
        "geo_primary": ["сочи"],
    }
    a = expand_for_site(_profile(CORE), cfg)
    b = expand_for_site(_profile(CORE), cfg)
    assert [c.cluster_key for c in a] == [c.cluster_key for c in b]
    assert [c.name_ru for c in a] == [c.name_ru for c in b]


def test_dedup_collapses_identical_slots():
    # Same template listed twice -> cluster_key dedup.
    out = expand_for_site(
        _profile(CORE, CORE),
        {"services": ["экскурсии"], "geo_primary": ["сочи"]},
    )
    assert len(out) == 1


# ---------- competitor + exclusion -----------------------------------------


def test_competitor_brand_slot_flips_to_discarded():
    cfg = {
        "services": ["экскурсии", "sputnik8"],
        "geo_primary": ["сочи"],
        "competitor_brands": ["sputnik8"],
    }
    out = expand_for_site(_profile(CORE), cfg)
    # Both services expand; sputnik8 one is flagged.
    competitor = [c for c in out if c.is_competitor_brand]
    assert len(competitor) == 1
    assert competitor[0].quality_tier == QualityTier.discarded
    assert competitor[0].cluster_type == ClusterType.competitor_brand


def test_excluded_geo_marks_discarded():
    cfg = {
        "services": ["экскурсии"],
        "geo_primary": ["сочи", "геленджик"],
        "excluded_geo": ["геленджик"],
    }
    out = expand_for_site(_profile(CORE), cfg)
    tiers = {c.name_ru: c.quality_tier for c in out}
    assert tiers["экскурсии сочи"] != QualityTier.discarded
    assert tiers["экскурсии геленджик"] == QualityTier.discarded


def test_excluded_service_marks_discarded():
    cfg = {
        "services": ["экскурсии", "туры"],
        "geo_primary": ["сочи"],
        "excluded_services": ["туры"],
    }
    out = expand_for_site(_profile(CORE), cfg)
    tiers = {c.name_ru: c.quality_tier for c in out}
    assert tiers["экскурсии сочи"] != QualityTier.discarded
    assert tiers["туры сочи"] == QualityTier.discarded


# ---------- guardrails -----------------------------------------------------


def test_geo_capped_at_limit():
    cfg = {
        "services": ["s1"],
        "geo_primary": [f"g{i}" for i in range(60)],
        "geo_secondary": [f"h{i}" for i in range(40)],
    }
    out = expand_for_site(_profile(CORE), cfg, max_per_template=100)
    # Capped at 50 unique geo combinations * 1 service = 50 clusters.
    assert len(out) == 50


def test_per_template_cap_enforced():
    cfg = {
        "services": ["s1"] * 1,
        "geo_primary": [f"g{i}" for i in range(40)],
    }
    out = expand_for_site(_profile(CORE), cfg, max_per_template=10, max_geo=60)
    assert len(out) == 10


def test_depth_cap_skips_deep_templates():
    deep = SeedTemplate(
        pattern="{activity} {city} {destination} {region}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        required_slots=("activity", "city", "destination", "region"),
    )
    cfg = {"services": ["s"], "geo_primary": ["a", "b"]}
    out = expand_for_site(_profile(deep, CORE), cfg)
    # Only CORE expands; the 4-slot template exceeds MAX_CARTESIAN_DEPTH=3.
    assert all(c.cluster_type == ClusterType.commercial_core for c in out)


def test_hard_global_cap_raises():
    # Produce far more than 500 actionable clusters by giving huge service
    # and geo vocabularies that Cartesian-cross to >> MAX_CLUSTERS_PER_SITE
    # actionable clusters. A single template with a very large per-template
    # cap is enough to blow through the global bound.
    wide_template = SeedTemplate(
        pattern="{activity} {city}",
        cluster_type=ClusterType.commercial_core,
        intent_code=IntentCode.COMM_CATEGORY,
        required_slots=("activity", "city"),
    )
    cfg = {
        "services": [f"s{i}" for i in range(30)],
        "geo_primary": [f"g{i}" for i in range(30)],
    }
    with pytest.raises(GuardrailError):
        expand_for_site(
            _profile(wide_template),
            cfg,
            max_clusters=MAX_CLUSTERS_PER_SITE,
            max_per_template=1000,
            max_geo=1000,
        )


def test_missing_required_slot_skips_template():
    no_service_cfg = {"geo_primary": ["сочи"]}  # no services
    out = expand_for_site(_profile(CORE), no_service_cfg)
    # CORE requires activity+city; service source is empty, template must skip.
    assert out == []


# ---------- soft cap retiering ---------------------------------------------


def test_soft_cap_core_overflow_retiered():
    # Force >50 core clusters by feeding many service×geo combos that all
    # land in commercial_core with relevance >= 0.70.
    services = [f"svc{i}" for i in range(10)]
    geos = [f"city{i}" for i in range(10)]
    cfg = {"services": services, "geo_primary": geos}
    out = expand_for_site(
        _profile(CORE),
        cfg,
        max_per_template=1000,
        max_geo=200,
    )
    core_count = sum(1 for c in out if c.quality_tier == QualityTier.core)
    secondary_count = sum(1 for c in out if c.quality_tier == QualityTier.secondary)
    assert core_count == 50
    assert secondary_count >= len(out) - 50 - sum(
        1 for c in out if c.quality_tier == QualityTier.discarded
    )


# ---------- shape ----------------------------------------------------------


def test_relevance_in_bounds():
    out = expand_for_site(
        _profile(CORE),
        {"services": ["a"], "geo_primary": ["b"]},
    )
    for c in out:
        assert 0.0 <= c.business_relevance <= 1.0


def test_keywords_are_lowercase_tokens():
    out = expand_for_site(
        _profile(CORE),
        {"services": ["ЭКСКУРСИИ"], "geo_primary": ["СОЧИ"]},
    )
    assert out
    kws = out[0].keywords
    assert all(kw == kw.lower() for kw in kws)


def test_site_id_override_used():
    sid = uuid.uuid4()
    out = expand_for_site(
        _profile(CORE),
        {"services": ["a"], "geo_primary": ["b"]},
        site_id=sid,
    )
    assert out and all(c.site_id == sid for c in out)


# ---------- tourism sample config (smoke test) -----------------------------


def test_tourism_profile_sample_expansion():
    from app.profiles.tourism import TOURISM_TOUR_OPERATOR

    cfg = {
        "services": ["экскурсии", "туры", "трансфер", "багги тур", "яхта аренда"],
        "geo_primary": ["сочи", "адлер", "красная поляна", "абхазия"],
        "geo_secondary": ["геленджик", "анапа"],
        "competitor_brands": ["sputnik8"],
    }
    out = expand_for_site(TOURISM_TOUR_OPERATOR, cfg)
    assert out, "expected non-empty expansion for tourism profile"
    # Within hard cap.
    assert len(out) <= MAX_CLUSTERS_PER_SITE
    # Soft caps enforced.
    tiers = {}
    for c in out:
        tiers[c.quality_tier] = tiers.get(c.quality_tier, 0) + 1
    assert tiers.get(QualityTier.core, 0) <= 50
    assert tiers.get(QualityTier.secondary, 0) <= 150
    assert tiers.get(QualityTier.exploratory, 0) <= 250
