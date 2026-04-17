import uuid
from datetime import date
from sqlalchemy import String, Integer, Boolean, Text, Date, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, TimestampMixin


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("issues.id"), index=True)

    # Basic info
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(50))  # meta_rewrite, new_page, content_add, schema_add, faq_add, etc.
    priority: Mapped[int] = mapped_column(Integer, index=True)  # 1-100, higher = more important
    estimated_impact: Mapped[str | None] = mapped_column(String(20))  # high, medium, low
    estimated_effort: Mapped[str | None] = mapped_column(String(20))  # XS, S, M, L, XL

    # Status tracking
    status: Mapped[str] = mapped_column(String(30), default="backlog", index=True)
    # Statuses: backlog, planned, in_progress, done, measuring, completed, failed, cancelled
    assigned_week: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[date | None] = mapped_column(Date)

    # Context — what the task is about
    target_query: Mapped[str | None] = mapped_column(String(1000))  # specific query this targets
    target_cluster: Mapped[str | None] = mapped_column(String(255))  # or a cluster
    target_page_url: Mapped[str | None] = mapped_column(String(2048))  # which page to modify/create

    # Ready-to-use generated content from AI
    generated_content: Mapped[dict | None] = mapped_column(JSONB)
    # Schema: {
    #   "new_title": "...",
    #   "new_description": "...",
    #   "new_h1": "...",
    #   "article_draft": "...",  # for new content tasks
    #   "schema_jsonld": {...},  # for schema.org tasks
    #   "faq_items": [...],
    # }

    # Impact measurement (after task completion)
    effect_tracked: Mapped[bool] = mapped_column(Boolean, default=False)
    effect_result: Mapped[dict | None] = mapped_column(JSONB)
    # Schema: {
    #   "position_before": 15.3, "position_after": 8.2,
    #   "impressions_before": 50, "impressions_after": 120,
    #   "clicks_before": 1, "clicks_after": 5,
    #   "measured_at": "2026-05-01"
    # }
