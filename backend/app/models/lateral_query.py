"""SQLAlchemy model for lateral_queries.

LLM-generated adjacent query ideas — Block A of the autonomous-helper
roadmap (see audits/2026-05-11_analyzer_upgrade_and_autonomous_roadmap.md).

Companion to alembic migration a9f0c3b1d2e4.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin


class LateralQuery(Base, TimestampMixin):
    __tablename__ = "lateral_queries"
    __table_args__ = (
        UniqueConstraint("site_id", "query_norm", name="uq_lateral_queries_site_norm"),
        Index(
            "ix_lateral_queries_site_status_created",
            "site_id", "status", "created_at",
        ),
        Index("ix_lateral_queries_site_run", "site_id", "agent_run_id"),
        CheckConstraint(
            "relation IN ('direct','related','info','weak')",
            name="ck_lateral_queries_relation",
        ),
        CheckConstraint(
            "status IN ('new','accepted','rejected','promoted')",
            name="ck_lateral_queries_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_lateral_queries_confidence_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    query: Mapped[str] = mapped_column(String(500), nullable=False)
    query_norm: Mapped[str] = mapped_column(String(500), nullable=False)
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric(3, 2), nullable=False, default=0.5,
    )
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_signal: Mapped[str] = mapped_column(
        String(32), nullable=False, default="business_truth",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="new",
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
