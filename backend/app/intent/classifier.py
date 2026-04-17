"""Query intent classifier — regex-first with confidence score.

Algorithm:
  1. For each query, run all regex rules across all intents.
  2. Pick the intent with the highest weight match.
  3. Also detect brand (separate axis — TRANS_BRAND can coexist but overrides when clear).
  4. Emit (intent, confidence, matched_pattern, brand_detected).
  5. If max confidence < 0.5 → mark "ambiguous" for LLM fallback in Phase 2B.

No LLM calls here — deterministic, fast, cacheable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.intent.enums import IntentCode
from app.intent.taxonomy import INTENT_DEFINITIONS, TAXONOMY


@dataclass(frozen=True)
class ClassificationResult:
    intent: IntentCode
    confidence: float            # 0.0-1.0
    matched_pattern: str | None  # regex that matched (for explainability)
    is_brand: bool               # brand token detected independently
    is_ambiguous: bool           # confidence < threshold — candidate for LLM


AMBIGUOUS_THRESHOLD = 0.5

# Brand detection — add company names to this list per site
# In production this comes from Site.display_name and variations.
BRAND_TOKENS: dict[str, set[str]] = {
    "южный континент": {"южный континент", "южный-континент", "ЮК", "ук"},
    "grand tour spirit": {"grand tour spirit", "grand tour", "гранд тур", "гранд тур спирит", "гтс", "gts"},
}


def detect_brand(query: str, known_brands: list[str] | None = None) -> bool:
    """Detect if query contains a brand token.

    Args:
        query: normalized query text (lowercase expected)
        known_brands: site-specific brand tokens. If None, uses global BRAND_TOKENS.
    """
    q = query.lower().strip()
    tokens_to_check: set[str] = set()
    if known_brands:
        for b in known_brands:
            tokens_to_check.add(b.lower())
    else:
        for tokens in BRAND_TOKENS.values():
            for t in tokens:
                tokens_to_check.add(t.lower())

    for token in tokens_to_check:
        if token in q:
            return True
    return False


def classify_query(query: str, known_brands: list[str] | None = None) -> ClassificationResult:
    """Classify a single query into one of the intent categories.

    Order of precedence:
      1. Brand detected AND query is just brand+minor → TRANS_BRAND
      2. Highest weight regex match
      3. Fallback to COMM_CATEGORY if nothing matches but query mentions geo+service
      4. Otherwise INFO_DEST as weakest fallback with low confidence
    """
    if not query or not query.strip():
        return ClassificationResult(
            intent=IntentCode.INFO_DEST,
            confidence=0.0,
            matched_pattern=None,
            is_brand=False,
            is_ambiguous=True,
        )

    q = query.strip().lower()
    is_brand = detect_brand(q, known_brands)

    # Brand override: if query is mostly brand (brand + 1-2 extra words)
    # short queries with a brand token → TRANS_BRAND
    if is_brand and len(q.split()) <= 4:
        return ClassificationResult(
            intent=IntentCode.TRANS_BRAND,
            confidence=0.9,
            matched_pattern="brand_token",
            is_brand=True,
            is_ambiguous=False,
        )

    # Run all regex rules, collect best match
    best_weight = 0.0
    best_intent: IntentCode | None = None
    best_pattern: str | None = None

    for definition in INTENT_DEFINITIONS:
        for rule in definition.rules:
            m = rule.pattern.search(q)
            if m and rule.weight > best_weight:
                best_weight = rule.weight
                best_intent = definition.code
                best_pattern = rule.pattern.pattern

    if best_intent is not None:
        return ClassificationResult(
            intent=best_intent,
            confidence=best_weight,
            matched_pattern=best_pattern,
            is_brand=is_brand,
            is_ambiguous=best_weight < AMBIGUOUS_THRESHOLD,
        )

    # Fallback heuristic: "экскурс|тур" + geo → COMM_CATEGORY
    service_geo = re.search(
        r"(экскурс|тур|джиппинг).*(сочи|абхази|красная\s+поляна|адлер)",
        q,
        re.IGNORECASE,
    )
    if service_geo:
        return ClassificationResult(
            intent=IntentCode.COMM_CATEGORY,
            confidence=0.45,
            matched_pattern="fallback_service_geo",
            is_brand=is_brand,
            is_ambiguous=True,
        )

    # Unknown — mark as info/ambiguous for LLM follow-up
    return ClassificationResult(
        intent=IntentCode.INFO_DEST,
        confidence=0.15,
        matched_pattern=None,
        is_brand=is_brand,
        is_ambiguous=True,
    )


def classify_batch(
    queries: list[str], known_brands: list[str] | None = None
) -> list[ClassificationResult]:
    """Batch classification — convenience wrapper."""
    return [classify_query(q, known_brands) for q in queries]
