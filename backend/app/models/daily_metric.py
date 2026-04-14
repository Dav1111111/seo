import uuid
from datetime import date
from sqlalchemy import String, Integer, Numeric, Date, ForeignKey, BigInteger, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, TimestampMixin


class DailyMetric(Base, TimestampMixin):
    __tablename__ = "daily_metrics"
    __table_args__ = (UniqueConstraint("site_id", "date", "metric_type", "dimension_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    metric_type: Mapped[str] = mapped_column(String(50))
    dimension_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    ctr: Mapped[float | None] = mapped_column(Numeric(5, 4))
    avg_position: Mapped[float | None] = mapped_column(Numeric(6, 2))

    visits: Mapped[int] = mapped_column(Integer, default=0)
    pageviews: Mapped[int] = mapped_column(Integer, default=0)
    bounce_rate: Mapped[float | None] = mapped_column(Numeric(5, 4))
    avg_duration: Mapped[float | None] = mapped_column(Numeric(8, 2))

    pages_indexed: Mapped[int | None] = mapped_column(Integer)
    pages_in_search: Mapped[int | None] = mapped_column(Integer)

    extra: Mapped[dict] = mapped_column(JSONB, default=lambda: {})
