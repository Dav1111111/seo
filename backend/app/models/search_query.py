import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Text, UniqueConstraint
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

    # Studio v2 etap 4 — relevance classification.
    # Legacy values: own / adjacent / disputed / spam / unclassified.
    # Funnel-aware values (added 2026-05-16):
    #   direct_product / funnel_warm / funnel_top / out_of_market / spam
    # set_by:  rules / llm / user (user wins forever, never overwritten).
    # CHECK constraint extended in alembic
    # b2c3d4e5f6a7_search_queries_funnel_relevance.
    relevance: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unclassified",
    )
    relevance_set_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    relevance_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    relevance_reason_ru: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Studio v2 — harmful query diagnosis (LLM cause + fix recommendations).
    # JSONB shape documented in alembic d5e6f7a8b9c0.
    harmful_diagnosis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    harmful_diagnosed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
