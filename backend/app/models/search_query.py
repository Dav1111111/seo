import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, TimestampMixin


class SearchQuery(Base, TimestampMixin):
    __tablename__ = "search_queries"
    __table_args__ = (UniqueConstraint("site_id", "query_text"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    query_text: Mapped[str] = mapped_column(String(1000))
    yandex_query_id: Mapped[str | None] = mapped_column(String(255))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_branded: Mapped[bool] = mapped_column(Boolean, default=False)
    cluster: Mapped[str | None] = mapped_column(String(255))
    wordstat_volume: Mapped[int | None] = mapped_column(Integer)
    wordstat_trend: Mapped[dict | None] = mapped_column(JSONB)
    wordstat_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
