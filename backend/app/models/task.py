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
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(50))
    priority: Mapped[int] = mapped_column(Integer, index=True)
    estimated_impact: Mapped[str | None] = mapped_column(String(20))
    estimated_effort: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="backlog", index=True)
    assigned_week: Mapped[date | None] = mapped_column(Date)
    effect_tracked: Mapped[bool] = mapped_column(Boolean, default=False)
    effect_result: Mapped[dict | None] = mapped_column(JSONB)
