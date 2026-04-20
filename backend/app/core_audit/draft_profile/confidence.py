"""Per-field confidence scoring for the Draft Profile (Phase F).

Scores are in [0, 1] and are intended for display in the Phase G wizard
UI. The overall confidence is a weighted average so a sparse-signal
field (e.g. competitor_brands) doesn't sink the whole score when the
site is otherwise well-covered.
"""

from __future__ import annotations

from typing import Sequence

from app.core_audit.draft_profile.dto import (
    CompetitorBrand,
    ExtractedGeo,
    ExtractedService,
    FieldConfidence,
)


# Weights for overall confidence aggregation. Order matters only in
# that fields missing from the dict get a default weight of 1.0.
_WEIGHTS = {
    "services": 2.0,
    "geo_primary": 2.0,
    "geo_secondary": 1.0,
    "competitor_brands": 0.5,
}


def services_confidence(services: Sequence[ExtractedService]) -> FieldConfidence:
    """Average confidence across the extracted services."""
    non_universal = [s for s in services if s.occurrence_count > 0]
    if not non_universal:
        conf = 0.0
    else:
        conf = sum(s.confidence for s in non_universal) / len(non_universal)
    return FieldConfidence(
        field="services",
        confidence=float(max(0.0, min(1.0, conf))),
        evidence_count=sum(s.occurrence_count for s in non_universal),
        reasoning_ru=(
            f"Из {len(services)} кандидатов с подтверждёнными упоминаниями "
            f"на страницах: {len(non_universal)}."
        ),
    )


def geo_primary_confidence(geo: ExtractedGeo) -> FieldConfidence:
    """`primary_city_count / 3`, capped at 1.0."""
    n = len(geo.primary)
    conf = min(1.0, n / 3.0)
    return FieldConfidence(
        field="geo_primary",
        confidence=float(conf),
        evidence_count=n,
        reasoning_ru=(
            f"Найдено {n} основных локаций (порог 30% страниц или "
            f"упоминание в заголовке главной/контактов)."
        ),
    )


def geo_secondary_confidence(geo: ExtractedGeo) -> FieldConfidence:
    """`secondary_city_count / 2`, capped at 1.0."""
    n = len(geo.secondary)
    conf = min(1.0, n / 2.0)
    return FieldConfidence(
        field="geo_secondary",
        confidence=float(conf),
        evidence_count=n,
        reasoning_ru=(
            f"Найдено {n} вторичных локаций, встречающихся на страницах или в запросах."
        ),
    )


def competitor_brands_confidence(
    brands: Sequence[CompetitorBrand],
) -> FieldConfidence:
    """LLM's own confidence averaged across returned brands."""
    if not brands:
        return FieldConfidence(
            field="competitor_brands",
            confidence=0.0,
            evidence_count=0,
            reasoning_ru="LLM не предложил брендов конкурентов.",
        )
    avg = sum(b.confidence_ru for b in brands) / len(brands)
    return FieldConfidence(
        field="competitor_brands",
        confidence=float(max(0.0, min(1.0, avg))),
        evidence_count=len(brands),
        reasoning_ru=(
            f"LLM предложил {len(brands)} брендов конкурентов со средней "
            f"уверенностью {avg:.2f}."
        ),
    )


def overall_confidence(fields: Sequence[FieldConfidence]) -> float:
    """Weighted average of the per-field confidences."""
    if not fields:
        return 0.0
    total_w = 0.0
    total_wv = 0.0
    for fc in fields:
        w = _WEIGHTS.get(fc.field, 1.0)
        total_w += w
        total_wv += w * float(fc.confidence)
    if total_w <= 0:
        return 0.0
    return float(max(0.0, min(1.0, total_wv / total_w)))


__all__ = [
    "services_confidence",
    "geo_primary_confidence",
    "geo_secondary_confidence",
    "competitor_brands_confidence",
    "overall_confidence",
]
