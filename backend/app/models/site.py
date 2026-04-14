import uuid
from sqlalchemy import String, Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base, TimestampMixin


class Site(Base, TimestampMixin):
    __tablename__ = "sites"
    __table_args__ = (UniqueConstraint("tenant_id", "domain"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))

    yandex_webmaster_host_id: Mapped[str | None] = mapped_column(String(255))
    yandex_metrica_counter_id: Mapped[str | None] = mapped_column(String(50))
    yandex_oauth_token: Mapped[str | None] = mapped_column(Text)  # encrypted at rest

    operating_mode: Mapped[str] = mapped_column(String(20), default="readonly")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=lambda: {})

    tenant = relationship("Tenant", back_populates="sites")
