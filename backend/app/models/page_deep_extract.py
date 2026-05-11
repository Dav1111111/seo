"""SQLAlchemy model for page_deep_extracts.

Stores Playwright-rendered snapshots of URLs — own pages and competitor
URLs alike. Companion to alembic migration e8f9c1a2b4d6.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PageDeepExtract(Base):
    __tablename__ = "page_deep_extracts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    is_competitor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    competitor_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Content
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    h1: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    headings_tree: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Interactive elements
    cta_inventory: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    forms_inventory: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    links_inventory: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    images_inventory: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Visual signals
    css_palette: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    fonts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    layout_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Performance
    performance: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    js_errors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Schema.org
    schema_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Screenshots
    screenshot_desktop_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    screenshot_mobile_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # AI-summary (optional)
    ai_summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
