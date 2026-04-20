"""Tests for app.core_audit.draft_profile.confidence."""

from __future__ import annotations

from app.core_audit.draft_profile.confidence import (
    competitor_brands_confidence,
    geo_primary_confidence,
    geo_secondary_confidence,
    overall_confidence,
    services_confidence,
)
from app.core_audit.draft_profile.dto import (
    CompetitorBrand,
    ExtractedGeo,
    ExtractedService,
    FieldConfidence,
)


def test_services_confidence_averages_non_universal_only():
    services = [
        ExtractedService(name="яхты", occurrence_count=10, pages_with=5, confidence=0.8),
        ExtractedService(name="багги", occurrence_count=6, pages_with=3, confidence=0.6),
        # Universal — ignored for confidence calc.
        ExtractedService(name="туры", occurrence_count=0, pages_with=0, confidence=1.0),
    ]
    fc = services_confidence(services)
    assert fc.field == "services"
    assert abs(fc.confidence - 0.7) < 1e-6


def test_services_confidence_zero_when_only_universals():
    services = [
        ExtractedService(name="туры", occurrence_count=0, pages_with=0, confidence=1.0),
    ]
    fc = services_confidence(services)
    assert fc.confidence == 0.0


def test_geo_primary_confidence_three_cities_is_saturated():
    geo = ExtractedGeo(primary=["сочи", "анапа", "адлер"])
    fc = geo_primary_confidence(geo)
    assert fc.confidence == 1.0
    assert fc.evidence_count == 3


def test_geo_primary_confidence_one_city_is_third():
    geo = ExtractedGeo(primary=["сочи"])
    fc = geo_primary_confidence(geo)
    assert abs(fc.confidence - 1 / 3) < 1e-6


def test_geo_secondary_confidence_two_cities_is_saturated():
    geo = ExtractedGeo(secondary=["адлер", "лазаревское"])
    fc = geo_secondary_confidence(geo)
    assert fc.confidence == 1.0


def test_competitor_brands_confidence_averages_llm_values():
    brands = [
        CompetitorBrand(name="a", confidence_ru=0.8),
        CompetitorBrand(name="b", confidence_ru=0.6),
    ]
    fc = competitor_brands_confidence(brands)
    assert abs(fc.confidence - 0.7) < 1e-6


def test_competitor_brands_confidence_empty_is_zero():
    fc = competitor_brands_confidence([])
    assert fc.confidence == 0.0
    assert fc.evidence_count == 0


def test_overall_confidence_is_weighted_average():
    fields = [
        FieldConfidence(field="services", confidence=1.0, evidence_count=1, reasoning_ru=""),
        FieldConfidence(field="geo_primary", confidence=0.0, evidence_count=0, reasoning_ru=""),
        FieldConfidence(field="geo_secondary", confidence=0.0, evidence_count=0, reasoning_ru=""),
        FieldConfidence(field="competitor_brands", confidence=0.0, evidence_count=0, reasoning_ru=""),
    ]
    out = overall_confidence(fields)
    # services weight=2 -> 2*1 = 2, total weights 2+2+1+0.5 = 5.5, 2/5.5.
    assert abs(out - (2.0 / 5.5)) < 1e-6


def test_overall_confidence_empty_returns_zero():
    assert overall_confidence([]) == 0.0
