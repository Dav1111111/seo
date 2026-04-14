import uuid
from sqlalchemy import String, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, TimestampMixin


class SeasonalityPattern(Base, TimestampMixin):
    __tablename__ = "seasonality_patterns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    pattern_name: Mapped[str] = mapped_column(String(255))
    month_weights: Mapped[dict] = mapped_column(JSONB, default=lambda: {})
    query_clusters: Mapped[list] = mapped_column(JSONB, default=lambda: [])
    holiday_adjustments: Mapped[dict] = mapped_column(JSONB, default=lambda: {})
    source: Mapped[str] = mapped_column(String(50))
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
