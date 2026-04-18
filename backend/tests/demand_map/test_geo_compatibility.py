"""Physical service-geo compatibility tests."""

from __future__ import annotations

from app.core_audit.demand_map import expand_for_site
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


def test_sea_services_not_emitted_in_mountain_geo():
    clusters = expand_for_site(
        TOURISM_TOUR_OPERATOR,
        {
            "services": ["морские прогулки", "яхты"],
            "geo_primary": ["красная поляна"],
        },
    )
    # None of the generated clusters should pair sea services with mountain geo
    for c in clusters:
        svc = (c.seed_slots.get("service") or c.seed_slots.get("activity") or "").lower()
        geo = (c.seed_slots.get("city") or c.seed_slots.get("destination")
               or c.seed_slots.get("region") or "").lower()
        assert not (svc in {"морские прогулки", "яхты"} and geo == "красная поляна"), (
            f"incompatible combo emitted: {svc} × {geo} in {c.name_ru}"
        )


def test_offroad_services_allowed_in_mountain_geo():
    clusters = expand_for_site(
        TOURISM_TOUR_OPERATOR,
        {
            "services": ["багги", "джиппинг"],
            "geo_primary": ["красная поляна"],
        },
    )
    # Багги + красная поляна = valid (красная поляна has offroad_access + mountain)
    combos = {(c.seed_slots.get("service") or c.seed_slots.get("activity"),
               c.seed_slots.get("city") or c.seed_slots.get("destination")
               or c.seed_slots.get("region"))
              for c in clusters}
    assert ("багги", "красная поляна") in combos or ("джиппинг", "красная поляна") in combos


def test_sea_services_allowed_in_coastal_geo():
    clusters = expand_for_site(
        TOURISM_TOUR_OPERATOR,
        {
            "services": ["морские прогулки"],
            "geo_primary": ["сочи", "адлер"],
        },
    )
    assert any(
        "морские прогулки" in c.name_ru.lower() for c in clusters
    ), "no coastal combos survived"


def test_unknown_geo_permitted():
    """Unknown geo = matrix can't judge → permit."""
    clusters = expand_for_site(
        TOURISM_TOUR_OPERATOR,
        {
            "services": ["морские прогулки"],
            "geo_primary": ["зимбабве"],  # not in GEO_PROPERTIES
        },
    )
    # Should NOT crash + should emit at least some clusters
    assert len(clusters) > 0


def test_unknown_service_permitted():
    """Unknown service = no requirements → permit."""
    clusters = expand_for_site(
        TOURISM_TOUR_OPERATOR,
        {
            "services": ["дегустация вин"],  # not in SERVICE_GEO_REQUIREMENTS
            "geo_primary": ["красная поляна"],
        },
    )
    assert len(clusters) > 0
