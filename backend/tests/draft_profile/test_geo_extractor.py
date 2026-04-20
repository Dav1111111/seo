"""Tests for app.core_audit.draft_profile.geo_extractor."""

from __future__ import annotations

from dataclasses import dataclass

from app.core_audit.draft_profile.geo_extractor import extract_geos


@dataclass
class FakePage:
    title: str | None = None
    h1: str | None = None
    content_text: str | None = None
    url: str | None = None
    path: str | None = None


def test_empty_input_returns_empty_extracted_geo():
    out = extract_geos([])
    assert out.primary == []
    assert out.secondary == []
    assert out.frequency_map == {}


def test_city_in_many_pages_promoted_to_primary():
    pages = [
        FakePage(title="Экскурсии Сочи"),
        FakePage(title="Туры Сочи и Адлер"),
        FakePage(title="Сочи зимой"),
        FakePage(title="Обзор программ Сочи"),
    ]
    out = extract_geos(pages)
    assert "сочи" in out.primary
    # Adler mentioned once — below threshold 30% unless only few pages.
    # With 4 pages, 1/4 = 0.25 < 0.30, so secondary.
    assert "адлер" in out.secondary


def test_homepage_title_promotes_city_to_primary():
    pages = [
        FakePage(title="Добро пожаловать в Сочи", path="/"),
        FakePage(title="Про нас"),
        FakePage(title="Услуги"),
        FakePage(title="Контакты", path="/contacts/"),
    ]
    out = extract_geos(pages)
    assert "сочи" in out.primary


def test_multiword_city_is_detected():
    pages = [
        FakePage(title="Отдых в Красной Поляне"),
        FakePage(title="Красная поляна летом"),
    ]
    out = extract_geos(pages)
    assert "красная поляна" in (set(out.primary) | set(out.secondary))


def test_unrecognized_tokens_not_added():
    pages = [FakePage(title="Вымышленноегородище")]
    out = extract_geos(pages)
    assert out.frequency_map == {}
    assert out.primary == []
    assert out.secondary == []


def test_queries_bump_city_frequency():
    pages = [FakePage(title="Туры по России")]
    queries = ["экскурсии сочи с гидом", "тур в сочи на 3 дня"]
    out = extract_geos(pages, queries)
    assert "сочи" in out.frequency_map
    assert out.frequency_map["сочи"] >= 2


def test_primary_threshold_tunable():
    pages = [
        FakePage(title="Сочи"),
        FakePage(title="Ничего"),
        FakePage(title="Ничего"),
        FakePage(title="Ничего"),
    ]
    # Default 30%: 1/4 = 25%, so secondary.
    out_default = extract_geos(pages)
    assert "сочи" in out_default.secondary
    # Lowered threshold: 1/4 = 25% >= 20%, so primary.
    out_loose = extract_geos(pages, primary_threshold=0.20)
    assert "сочи" in out_loose.primary


def test_cis_destination_detected():
    pages = [
        FakePage(title="Туры в Абхазию"),
        FakePage(title="Абхазия летом"),
    ]
    out = extract_geos(pages)
    all_cities = set(out.primary) | set(out.secondary)
    assert "абхазия" in all_cities
