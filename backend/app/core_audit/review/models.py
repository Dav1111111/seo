"""SQLAlchemy models for Module 3 persistence.

Two tables:
  - page_reviews: 1 row per (page, composite_hash, reviewer_version).
    Idempotency: if a completed review exists for the same composite_hash,
    the service skips with SkipReason.unchanged_hash.
  - page_review_recommendations: N rows per review, one per finding.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin


class PageReview(Base, TimestampMixin):
    __tablename__ = "page_reviews"
    __table_args__ = (
        UniqueConstraint(
            "page_id", "composite_hash", "reviewer_version",
            name="uq_page_reviews_page_hash_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    coverage_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coverage_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )

    target_intent_code: Mapped[str] = mapped_column(String(30), nullable=False)
    composite_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    reviewer_model: Mapped[str] = mapped_column(String(50), nullable=False, default="python-only")
    reviewer_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    skip_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Cost accounting (0 for python-only runs)
    cost_usd: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Snapshots (for UI + audit without re-deriving)
    top_queries_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    page_level_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class PageReviewRecommendation(Base, TimestampMixin):
    __tablename__ = "page_review_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("page_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    category: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False)

    before_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_ru: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_impact: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    user_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    user_status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_status_changed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Module 4 — Prioritization scores (nullable until scorer runs)
    priority_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    impact_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    ease_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scorer_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
