import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, TimestampMixin


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("issues.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))   # telegram, email, dashboard
    alert_type: Mapped[str] = mapped_column(String(50)) # anomaly, daily_summary, weekly_report
    payload: Mapped[dict] = mapped_column(JSONB, default=lambda: {})
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
