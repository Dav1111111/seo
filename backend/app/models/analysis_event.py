"""Activity feed — what the platform is doing, in plain language.

Written by Celery tasks at key milestones so the owner sees the system
actually working instead of a silent dashboard.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnalysisEvent(Base):
    __tablename__ = "analysis_events"
    __table_args__ = (
        Index("ix_analysis_events_site_ts", "site_id", "ts"),
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
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    # Optional extras (cost, counts, URLs)
    extra: Mapped[dict] = mapped_column(JSONB, default=lambda: {})
