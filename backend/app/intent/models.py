"""SQLAlchemy models for intent classification and coverage."""

import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, TimestampMixin


class QueryIntent(Base, TimestampMixin):
    """Intent classification for a search query (1:1 with SearchQuery)."""
    __tablename__ = "query_intents"

    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_queries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id"),
        index=True,
        nullable=False,
    )

    # Classification result
    intent_code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    matched_pattern: Mapped[str | None] = mapped_column(Text)
    is_brand: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    # Source (regex / llm / manual)
    classifier_source: Mapped[str] = mapped_column(String(20), default="regex", nullable=False)
    classifier_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)

    classified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class PageIntentScore(Base, TimestampMixin):
    """Score per (page, intent) pair — 10 rows per page."""
    __tablename__ = "page_intent_scores"
    __table_args__ = (
        UniqueConstraint("page_id", "intent_code", name="uq_page_intent"),
        Index("ix_page_intent_score", "site_id", "intent_code", "score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id"),
        nullable=False,
    )

    intent_code: Mapped[str] = mapped_column(String(30), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)  # 0.0-5.0

    # Component signals for explainability
    s1_heading: Mapped[float] = mapped_column(Float, default=0.0)
    s2_content: Mapped[float] = mapped_column(Float, default=0.0)
    s3_structure: Mapped[float] = mapped_column(Float, default=0.0)
    s4_cta: Mapped[float] = mapped_column(Float, default=0.0)
    s5_schema: Mapped[float] = mapped_column(Float, default=0.0)
    s6_eeat: Mapped[float] = mapped_column(Float, default=0.0)

    scorer_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class CoverageDecision(Base, TimestampMixin):
    """Decision tree output — what to do about a given intent cluster on a site.

    One row per (site, intent_cluster_key) — cluster_key could be intent itself
    or a subcluster within (e.g., "comm_modified__abkhazia").
    """
    __tablename__ = "coverage_decisions"
    __table_args__ = (
        Index("ix_coverage_site_status", "site_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id"),
        nullable=False,
        index=True,
    )
    intent_code: Mapped[str] = mapped_column(String(30), nullable=False)
    cluster_key: Mapped[str] = mapped_column(String(255), nullable=False)

    # Decision
    action: Mapped[str] = mapped_column(String(30), nullable=False)  # CoverageAction enum
    coverage_status: Mapped[str] = mapped_column(String(20), nullable=False)  # CoverageStatus
    justification_ru: Mapped[str | None] = mapped_column(Text)

    # Target
    target_page_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    proposed_url: Mapped[str | None] = mapped_column(String(2048))

    # Metrics
    queries_in_cluster: Mapped[int] = mapped_column(Integer, default=0)
    total_impressions: Mapped[int] = mapped_column(Integer, default=0)
    expected_lift_impressions: Mapped[int | None] = mapped_column(Integer)

    # Supporting evidence
    evidence: Mapped[dict | None] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False, index=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
