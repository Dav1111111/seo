"""Module 3 — Page Review.

Universal page-review engine. Consumes:
  - core_audit.decision_tree output (coverage_decisions with action='strengthen')
  - profile.page_requirements / schema_rules / eeat_signals / commercial_factors
  - fingerprint snapshot (content_hash, title_normalized, meta, h1, word_count)

Produces:
  - page_reviews row (page-level summary, cost, status)
  - page_review_recommendations rows (category-level findings with before/after)

Explicit design constraints (from user, fixed 2026-04-17):
  - Review findings are NEVER mixed with task generation.
  - Two result levels: page-level summary + recommendation-level findings.
  - Skip reasons are enum-logged: unchanged_hash, not_strengthen,
    missing_content, no_profile_rules, page_deleted, no_fingerprint.
  - v1 reviews only `action='strengthen'`. `create` pages have nothing to review.
  - Over-optimization signals computed in Python; LLM only writes reasoning_ru.
"""

from app.core_audit.review.dto import (
    LinkCandidate,
    PageLevelSummary,
    Recommendation,
    ReviewInput,
    ReviewResult,
)
from app.core_audit.review.enums import (
    RecCategory,
    RecPriority,
    ReviewStatus,
    SkipReason,
    UserStatus,
)

__all__ = [
    "LinkCandidate",
    "PageLevelSummary",
    "RecCategory",
    "RecPriority",
    "Recommendation",
    "ReviewInput",
    "ReviewResult",
    "ReviewStatus",
    "SkipReason",
    "UserStatus",
]
