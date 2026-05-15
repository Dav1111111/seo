from __future__ import annotations

from app.collectors.tasks import (
    build_wordstat_seed_plan,
    classify_wordstat_discovery_phrase,
)


def _seeds(plan: list[dict[str, str]]) -> list[str]:
    return [item["seed"] for item in plan]


def test_seed_plan_expands_tourism_profile_beyond_primary_geo() -> None:
    plan = build_wordstat_seed_plan(
        {
            "primary_product": "багги",
            "services": ["багги", "экспедиции", "прокат"],
            "secondary_products": ["маршруты", "экспедиции"],
            "geo_primary": ["сочи", "абхазия"],
        },
        max_seeds=20,
    )
    seeds = _seeds(plan)

    assert len(seeds) == 20
    assert len(seeds) == len(set(seeds))
    assert "багги" in seeds
    assert "багги сочи" in seeds
    assert "багги абхазия" in seeds
    assert "экспедиции багги сочи" in seeds
    assert "прокат багги абхазия" in seeds
    assert "экскурсии из сочи в абхазию" in seeds
    assert "туры из сочи в абхазию" in seeds


def test_seed_plan_prioritises_primary_market_before_secondary_geos() -> None:
    plan = build_wordstat_seed_plan(
        {
            "primary_product": "багги",
            "services": ["багги", "экспедиции", "прокат"],
            "secondary_products": ["маршруты", "экспедиции"],
            "geo_primary": ["сочи", "абхазия"],
            "geo_secondary": ["крым", "кавказ", "геленджик", "архыз", "кисловодск"],
        },
        max_seeds=30,
    )
    seeds = _seeds(plan)

    assert len(seeds) == 30
    assert "экскурсии из сочи в абхазию" in seeds
    assert "туры из сочи в абхазию" in seeds
    assert "экскурсии сочи" in seeds
    assert "активный отдых сочи" in seeds
    assert "экскурсии абхазия" in seeds
    assert "джип тур абхазия" in seeds
    assert "багги крым" not in seeds


def test_seed_plan_uses_legacy_service_geo_when_primary_missing() -> None:
    plan = build_wordstat_seed_plan(
        {
            "services": ["экскурсии", "туры"],
            "geo_primary": ["сочи"],
        },
        max_seeds=10,
    )
    seeds = _seeds(plan)

    assert "экскурсии" in seeds
    assert "экскурсии сочи" in seeds
    assert "туры" in seeds
    assert "туры сочи" in seeds


def test_seed_plan_respects_limit_and_normalises_duplicates() -> None:
    plan = build_wordstat_seed_plan(
        {
            "primary_product": " Багги ",
            "services": ["БАГГИ", "Прокат", "прокат"],
            "geo_primary": ["Сочи", "сочи", "Абхазия"],
        },
        max_seeds=5,
    )
    seeds = _seeds(plan)

    assert seeds == [
        "багги",
        "багги сочи",
        "багги цена сочи",
        "багги стоимость сочи",
        "багги абхазия",
    ]


def test_wordstat_discovery_relevance_rejects_broad_homonym_noise() -> None:
    cfg = {
        "primary_product": "багги",
        "services": ["багги", "экспедиции", "прокат"],
        "secondary_products": ["маршруты", "экспедиции"],
        "geo_primary": ["сочи", "абхазия"],
        "geo_secondary": ["крым"],
    }

    assert classify_wordstat_discovery_phrase("багги сочи", cfg)[0] is True
    assert classify_wordstat_discovery_phrase("багги цена абхазия", cfg)[0] is True
    assert classify_wordstat_discovery_phrase("экскурсии из сочи в абхазию", cfg)[0] is True
    assert classify_wordstat_discovery_phrase("активный отдых сочи", cfg)[0] is True
    assert classify_wordstat_discovery_phrase("экскурсия в абхазию из сочи", cfg)[0] is True
    assert classify_wordstat_discovery_phrase("поездка в абхазию", cfg)[0] is True

    assert classify_wordstat_discovery_phrase("джинсы багги", cfg)[0] is False
    # «багги купить» is now classified as direct_product (commercial
    # intent dominates the missing geo) — the funnel-aware classifier
    # accepts it. See test_wordstat_funnel_classifier.py for the full
    # branch coverage.
    accepted, relevance, _ = classify_wordstat_discovery_phrase(
        "багги купить", cfg,
    )
    assert accepted is True
    assert relevance == "direct_product"
    assert classify_wordstat_discovery_phrase("сочи купить штаны багги", cfg)[0] is False
    assert classify_wordstat_discovery_phrase("баги", cfg)[0] is False
    assert classify_wordstat_discovery_phrase("трансформеры", cfg)[0] is False
    assert classify_wordstat_discovery_phrase("хоста сочи", cfg)[0] is False
    assert classify_wordstat_discovery_phrase("маршруты автобусов сочи", cfg)[0] is False
    assert classify_wordstat_discovery_phrase("поезд сочи маршрут", cfg)[0] is False
