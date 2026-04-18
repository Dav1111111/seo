"""Physical service-geo compatibility for Russian tourism.

Kills combos like "морские прогулки × красная поляна" (no sea in mountains)
at expansion time — these are not "low-relevance", they are physically
impossible and should never appear in the demand map.

Semantics: each service MAY declare a set of required geo properties.
A geo is compatible with the service if ANY of its properties intersect
the required set. Unknown services or unknown geos → permit (the matrix
is advisory, not a whitelist).
"""

from __future__ import annotations


# Known Russian tourism geos + their physical properties.
# Unknown geos = no check (permit).
GEO_PROPERTIES: dict[str, frozenset[str]] = {
    "сочи":             frozenset({"coastal", "urban", "resort"}),
    "адлер":            frozenset({"coastal", "urban", "resort"}),
    "лоо":              frozenset({"coastal", "village"}),
    "хоста":            frozenset({"coastal", "village"}),
    "кудепста":         frozenset({"coastal", "village"}),
    "лазаревское":      frozenset({"coastal", "village"}),
    "дагомыс":          frozenset({"coastal", "village"}),
    "мацеста":          frozenset({"coastal", "village"}),
    "эсто-садок":       frozenset({"mountain", "ski", "village"}),
    "красная поляна":   frozenset({"mountain", "ski", "offroad_access"}),
    "олимпийский парк": frozenset({"coastal", "urban", "resort"}),
    "абхазия":          frozenset({"coastal", "mountain", "offroad_access"}),
    "крым":             frozenset({"coastal", "mountain", "offroad_access"}),
}


# Service → set of required geo properties (any-of semantics).
# Services NOT in this dict are allowed everywhere (unknown = permit).
SERVICE_GEO_REQUIREMENTS: dict[str, frozenset[str]] = {
    # Water services — must have coastal access.
    "морские прогулки": frozenset({"coastal"}),
    "яхты":             frozenset({"coastal"}),
    "катера":           frozenset({"coastal"}),
    "рыбалка морская":  frozenset({"coastal"}),
    "дайвинг":          frozenset({"coastal"}),
    "сапсёрфинг":       frozenset({"coastal"}),

    # Off-road / mountain services — must have offroad_access or mountain.
    "багги":            frozenset({"offroad_access", "mountain"}),
    "квадроциклы":      frozenset({"offroad_access", "mountain"}),
    "джиппинг":         frozenset({"offroad_access", "mountain"}),
    "снегоходы":        frozenset({"mountain", "ski"}),

    # Ski-specific.
    "горные лыжи":      frozenset({"mountain", "ski"}),
    "сноуборд":         frozenset({"mountain", "ski"}),

    # Everything else (экскурсии, туры, вертолёт, ...) — no physical constraint.
}


def is_service_compatible_with_geo(service: str | None, geo: str | None) -> bool:
    """Return True if the service+geo combo is physically plausible.

    Unknown service OR unknown geo → permit (advisory matrix).
    Service without requirements → permit.
    Known service + known geo → require at least one overlap.
    """
    if not service or not geo:
        return True
    svc_l = service.lower()
    geo_l = geo.lower()

    requirements = SERVICE_GEO_REQUIREMENTS.get(svc_l)
    if not requirements:
        return True  # no physical constraint defined

    properties = GEO_PROPERTIES.get(geo_l)
    if properties is None:
        return True  # unknown geo — cannot judge, permit

    return bool(requirements & properties)
