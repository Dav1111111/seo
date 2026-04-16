"""Back-compat shim — forwards to the profile-driven core classifier.

Kept for callers that haven't migrated. Defaults to the tourism/tour_operator
profile. New code should call `app.core_audit.classifier` directly with the
profile resolved from `registry.get_profile(site.vertical, site.business_model)`.
"""

from __future__ import annotations

from app.core_audit.classifier import (
    AMBIGUOUS_THRESHOLD,
    BRAND_SHORT_QUERY_MAX_TOKENS,
    ClassificationResult,
    classify_batch as _classify_batch_core,
    classify_query as _classify_core,
    detect_brand as _detect_brand_core,
)
from app.profiles.tourism import TOURISM_TOUR_OPERATOR

# Legacy export — some tests read BRAND_TOKENS directly.
BRAND_TOKENS: dict[str, set[str]] = {
    "_flattened": set(TOURISM_TOUR_OPERATOR.brand_tokens),
}


def detect_brand(query: str, known_brands: list[str] | None = None) -> bool:
    return _detect_brand_core(query, TOURISM_TOUR_OPERATOR, known_brands)


def classify_query(
    query: str,
    known_brands: list[str] | None = None,
) -> ClassificationResult:
    return _classify_core(query, TOURISM_TOUR_OPERATOR, known_brands)


def classify_batch(
    queries: list[str],
    known_brands: list[str] | None = None,
) -> list[ClassificationResult]:
    return _classify_batch_core(queries, TOURISM_TOUR_OPERATOR, known_brands)


__all__ = [
    "AMBIGUOUS_THRESHOLD",
    "BRAND_SHORT_QUERY_MAX_TOKENS",
    "BRAND_TOKENS",
    "ClassificationResult",
    "classify_batch",
    "classify_query",
    "detect_brand",
]
