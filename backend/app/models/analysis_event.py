"""Activity feed — what the platform is doing, in plain language.

Written by Celery tasks at key milestones so the owner sees the system
actually working instead of a silent dashboard.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnalysisEvent(Base):
    __tablename__ = "analysis_events"
    __table_args__ = (
        Index("ix_analysis_events_site_ts", "site_id", "ts"),
        Index("ix_analysis_events_site_run", "site_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id"), index=True,
    )
    # Logical stage: "crawl" | "webmaster" | "demand_map" |
    # "competitor_discovery" | "competitor_deep_dive" | "opportunities"
    # | "report" | "priorities" | "outcome"
    stage: Mapped[str] = mapped_column(String(50))
    # "started" | "progress" | "done" | "skipped" | "failed"
    status: Mapped[str] = mapped_column(String(20))
    # One-line human sentence in Russian: "Собираю SERP для 12 запросов…"
    message: Mapped[str] = mapped_column(String(500))
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    # Optional extras (cost, counts, URLs)
    extra: Mapped[dict] = mapped_column(JSONB, default=lambda: {})
    # Groups events from a single pipeline run so the UI can show just
    # "this run" without mixing two back-to-back clicks. Nullable for
    # historical rows and standalone (non-pipeline) events.
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
