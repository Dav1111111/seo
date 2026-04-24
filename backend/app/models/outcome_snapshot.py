"""Before/after tracking for applied recommendations.

Owner clicks "Применил" on a recommendation → we snapshot current
impressions/clicks/position for the affected page. Fourteen days later,
a scheduled task fills followup_metrics + delta so the owner sees
actual impact instead of guessing.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OutcomeSnapshot(Base):
    __tablename__ = "outcome_snapshots"
    __table_args__ = (
        UniqueConstraint("site_id", "recommendation_id", name="uq_outcome_rec_per_site"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sites.id"), index=True)

    # Free-form ID: for priority_scores → row id; for opportunities → Opportunity.id hex
    recommendation_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32))  # 'priority' | 'opportunity'
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    baseline_metrics: Mapped[dict] = mapped_column(JSONB, default=lambda: {})

    followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    followup_metrics: Mapped[dict] = mapped_column(JSONB, default=lambda: {})
    delta: Mapped[dict] = mapped_column(JSONB, default=lambda: {})

    note_ru: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
