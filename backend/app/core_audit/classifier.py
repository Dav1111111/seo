"""Profile-driven query classifier.

Algorithm:
  1. Detect brand using profile.brand_tokens + optional per-site known_brands.
  2. If brand AND query is short (<=4 tokens) → TRANS_BRAND.
  3. Otherwise iterate profile.intent_rules, pick highest weight match.
  4. If no rule matched AND profile.fallback_commercial_pattern matches → COMM_CATEGORY.
  5. Otherwise INFO_DEST fallback with low confidence.

Deterministic, no LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core_audit.intent_codes import IntentCode
from app.core_audit.profile_protocol import SiteProfile

AMBIGUOUS_THRESHOLD = 0.5
BRAND_SHORT_QUERY_MAX_TOKENS = 4


@dataclass(frozen=True)
class ClassificationResult:
    intent: IntentCode
    confidence: float
    matched_pattern: str | None
    is_brand: bool
    is_ambiguous: bool


def detect_brand(
    query: str,
    profile: SiteProfile,
    known_brands: list[str] | None = None,
) -> bool:
    """Detect brand tokens in query. Short tokens use word-boundary lookaround
    to avoid matching substrings (e.g. "ук" in "рука").
    """
    q = query.lower().strip()
    tokens: set[str] = {t.lower() for t in profile.brand_tokens if t}
    if known_brands:
        tokens.update(b.lower() for b in known_brands if b)

    for token in tokens:
        pattern = r"(?<!\w)" + re.escape(token) + r"(?!\w)"
        if re.search(pattern, q, flags=re.IGNORECASE | re.UNICODE):
            return True
    return False


def classify_query(
    query: str,
    profile: SiteProfile,
    known_brands: list[str] | None = None,
) -> ClassificationResult:
    """Classify a single query using the supplied profile."""
    if not query or not query.strip():
        return ClassificationResult(
            intent=IntentCode.INFO_DEST,
            confidence=0.0,
            matched_pattern=None,
            is_brand=False,
            is_ambiguous=True,
        )

    q = query.strip().lower()
    is_brand = detect_brand(q, profile, known_brands)

    if is_brand and len(q.split()) <= BRAND_SHORT_QUERY_MAX_TOKENS:
        return ClassificationResult(
            intent=IntentCode.TRANS_BRAND,
            confidence=0.9,
            matched_pattern="brand_token",
            is_brand=True,
            is_ambiguous=False,
        )

    best_weight = 0.0
    best_intent: IntentCode | None = None
    best_pattern: str | None = None

    for rule in profile.intent_rules:
        if rule.pattern.search(q) and rule.weight > best_weight:
            best_weight = rule.weight
            best_intent = rule.intent
            best_pattern = rule.pattern.pattern

    if best_intent is not None:
        return ClassificationResult(
            intent=best_intent,
            confidence=best_weight,
            matched_pattern=best_pattern,
            is_brand=is_brand,
            is_ambiguous=best_weight < AMBIGUOUS_THRESHOLD,
        )

    if profile.fallback_commercial_pattern and profile.fallback_commercial_pattern.search(q):
        return ClassificationResult(
            intent=IntentCode.COMM_CATEGORY,
            confidence=0.45,
            matched_pattern="fallback_service_geo",
            is_brand=is_brand,
            is_ambiguous=True,
        )

    return ClassificationResult(
        intent=IntentCode.INFO_DEST,
        confidence=0.15,
        matched_pattern=None,
        is_brand=is_brand,
        is_ambiguous=True,
    )


def classify_batch(
    queries: list[str],
    profile: SiteProfile,
    known_brands: list[str] | None = None,
) -> list[ClassificationResult]:
    return [classify_query(q, profile, known_brands) for q in queries]
