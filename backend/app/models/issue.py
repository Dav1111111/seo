import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, TimestampMixin


class Issue(Base, TimestampMixin):
    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    issue_type: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(20), index=True)
    confidence: Mapped[float] = mapped_column(Numeric(3, 2))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    affected_entity_type: Mapped[str | None] = mapped_column(String(50))
    affected_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evidence: Mapped[dict] = mapped_column(JSONB, default=lambda: {})
    recommendation: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text)
