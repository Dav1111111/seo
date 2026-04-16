"""DTOs for the priority layer — pure in-memory objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ScoreBreakdown:
    """Structured output of the scorer — every component visible."""
    impact: float                       # 0-1
    confidence: float                   # 0-1
    ease: float                         # 0-1
    priority_score: float               # 0-100, final

    # Diagnostics — why this score?
    impact_parts: dict = field(default_factory=dict)
    confidence_parts: dict = field(default_factory=dict)
    ease_parts: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()         # e.g. ("seasonal_boost",)


@dataclass(frozen=True)
class PrioritizedItem:
    """One ranked recommendation in a site's priority list."""
    recommendation_id: UUID
    review_id: UUID
    page_id: UUID
    page_url: str | None
    target_intent_code: str
    category: str
    priority: str                       # critical|high|medium|low from reviewer
    reasoning_ru: str
    before_text: str | None
    after_text: str | None
    user_status: str

    priority_score: float
    impact: float
    confidence: float
    ease: float
    scored_at: datetime | None


@dataclass(frozen=True)
class WeeklyPlan:
    """Top-N diversified picks for a 'this week' UI view."""
    items: tuple[PrioritizedItem, ...]
    total_in_backlog: int               # total eligible recs before truncation
    max_per_page: int
    pages_represented: int
