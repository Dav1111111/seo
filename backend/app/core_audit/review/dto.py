"""In-memory DTOs for Module 3 review pipeline.

These flow between the reviewer orchestrator, Python checks, LLM runners,
and the persistence layer. Separating them from ORM models keeps the core
engines DB-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.core_audit.intent_codes import IntentCode
from app.core_audit.review.enums import (
    RecCategory,
    RecPriority,
    ReviewStatus,
    SkipReason,
)


@dataclass(frozen=True)
class ReviewInput:
    """Immutable snapshot of what we feed into a review.

    Built by the context_builder from fingerprint + coverage_decision +
    top queries. Fed to Python checks and LLM runners.
    """
    page_id: UUID
    site_id: UUID
    coverage_decision_id: UUID | None
    target_intent: IntentCode

    # Page snapshot (from fingerprint)
    path: str
    url: str
    title: str | None
    meta_description: str | None
    h1: str | None
    content_text: str | None
    word_count: int
    has_schema: bool
    images_count: int
    content_hash: str
    composite_hash: str                       # sha256(content_hash + title + meta + h1)

    # User demand context
    top_queries: tuple[str, ...] = ()         # up to 5

    # Current intent scores (from PageIntentScore for this intent)
    current_score: float = 0.0                # 0-5
    s1_heading: float = 0.0
    s2_content: float = 0.0
    s3_structure: float = 0.0
    s4_cta: float = 0.0
    s5_schema: float = 0.0
    s6_eeat: float = 0.0


@dataclass(frozen=True)
class Recommendation:
    """Single actionable finding. One Recommendation → one DB row."""
    category: RecCategory
    priority: RecPriority
    reasoning_ru: str
    before: str | None = None
    after: str | None = None
    estimated_impact: dict | None = None


@dataclass(frozen=True)
class PageLevelSummary:
    """Aggregate review verdict at the page level.

    Persisted in page_reviews.page_level_summary as JSONB so UI can render
    a one-card summary without joining recommendations.
    """
    verdict_ru: str                           # short human-readable summary
    current_score: float                      # score at review time
    estimated_score_after: float | None       # if all critical/high rec applied
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0

    # Over-optimization signals (Python-computed, not LLM)
    title_keyword_count: int = 0
    title_char_length: int = 0
    h1_equals_title: bool = False
    keyword_density: float = 0.0              # fraction, e.g. 0.023 = 2.3%

    # Structural gap tally
    missing_h2_blocks: tuple[str, ...] = ()
    missing_eeat_signals: tuple[str, ...] = ()
    missing_commercial_factors: tuple[str, ...] = ()


@dataclass
class ReviewResult:
    """Result of one review pass (one page, one intent).

    Outcome branches:
      - status=completed → recommendations populated, summary populated
      - status=skipped → skip_reason populated, recommendations empty
      - status=failed → error populated
    """
    page_id: UUID
    site_id: UUID
    target_intent: IntentCode
    composite_hash: str
    status: ReviewStatus
    reviewer_model: str                       # "python-only" | "claude-haiku-4-5" | ...
    reviewer_version: str

    summary: PageLevelSummary | None = None
    recommendations: list[Recommendation] = field(default_factory=list)

    skip_reason: SkipReason | None = None
    error: str | None = None

    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
