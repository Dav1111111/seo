import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, TimestampMixin


class Page(Base, TimestampMixin):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("site_id", "url"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    path: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(500))
    page_type: Mapped[str | None] = mapped_column(String(50))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    in_index: Mapped[bool] = mapped_column(Boolean, default=False)
    in_sitemap: Mapped[bool] = mapped_column(Boolean, default=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict] = mapped_column(JSONB, default=lambda: {})

    # Crawled content (from SiteCrawler)
    meta_description: Mapped[str | None] = mapped_column(String(1000))
    h1: Mapped[str | None] = mapped_column(String(500))
    content_text: Mapped[str | None] = mapped_column(Text)  # plain text content for LLM analysis
    word_count: Mapped[int | None] = mapped_column(Integer)
    internal_links: Mapped[list | None] = mapped_column(JSONB)  # list of URLs this page links to
    images_count: Mapped[int | None] = mapped_column(Integer)
    has_schema: Mapped[bool] = mapped_column(Boolean, default=False)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
