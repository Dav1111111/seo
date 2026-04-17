"""PageFingerprint SQLAlchemy model (1:1 with pages)."""

import uuid
from datetime import datetime
from sqlalchemy import String, Integer, SmallInteger, Float, DateTime, ForeignKey, LargeBinary, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, TimestampMixin


class PageFingerprint(Base, TimestampMixin):
    __tablename__ = "page_fingerprints"

    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id"),
        index=True,
        nullable=False,
    )

    # URL normalization (правка 1)
    normalized_url: Mapped[str] = mapped_column(String(2048), nullable=False)

    # Extraction metadata (правка 2)
    content_text_length: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_language: Mapped[str] = mapped_column(String(5), default="ru", nullable=False)
    main_content_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_status: Mapped[str] = mapped_column(String(20), default="ok", nullable=False)
    extraction_error: Mapped[str | None] = mapped_column(Text)

    # Core fingerprint payload
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    minhash_signature: Mapped[bytes | None] = mapped_column(LargeBinary)
    minhash_num_perm: Mapped[int] = mapped_column(SmallInteger, default=128, nullable=False)
    shingle_size: Mapped[int] = mapped_column(SmallInteger, default=5, nullable=False)
    ngram_hash_vector: Mapped[bytes | None] = mapped_column(LargeBinary)
    ngram_n_features: Mapped[int] = mapped_column(Integer, default=2**18, nullable=False)
    ngram_ngram_range: Mapped[str] = mapped_column(String(10), default="3,5", nullable=False)
    ngram_format_version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    title_normalized: Mapped[str | None] = mapped_column(Text)
    h1_normalized: Mapped[str | None] = mapped_column(Text)

    # Metrics
    content_length_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_length_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    boilerplate_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Versioning по компонентам (правка 4)
    extraction_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    lemmatization_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    minhash_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    ngram_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    fingerprint_schema_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)

    # Lifecycle status (правка 3 + 8)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    skip_reason: Mapped[str | None] = mapped_column(String(30))
    last_status_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Timing
    source_crawl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fingerprinted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
