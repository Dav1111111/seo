"""SQLAlchemy ORM for the Target Demand Map.

These tables are created by the `c4d5e6f7a8b9_add_target_demand_map`
migration. Phase A has no downstream consumer — the models exist so
Alembic's autogenerate diff does not regress next time someone runs it,
and so Phase B can read rows via ORM without another schema round trip.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class TargetCluster(Base, TimestampMixin):
    """A target cluster for a site — output of the expander.

    `cluster_key` is deterministic per (site, cluster_type, slot values) —
    re-running the expander produces the same rows (idempotent upsert).
    """

    __tablename__ = "target_clusters"
    __table_args__ = (
        UniqueConstraint("site_id", "cluster_key", name="uq_target_clusters_site_key"),
        Index("ix_target_clusters_site_tier", "site_id", "quality_tier"),
        Index("ix_target_clusters_site_intent", "site_id", "intent_code"),
        CheckConstraint(
            "cluster_type IN ('commercial_core','commercial_modifier','local_geo',"
            "'informational_dest','informational_prep','transactional_book',"
            "'trust','seasonality','brand','competitor_brand','activity')",
            name="ck_target_clusters_cluster_type",
        ),
        CheckConstraint(
            "quality_tier IN ('core','secondary','exploratory','discarded')",
            name="ck_target_clusters_quality_tier",
        ),
        CheckConstraint(
            "expected_volume_tier IN ('xs','s','m','l','xl')",
            name="ck_target_clusters_volume_tier",
        ),
        CheckConstraint(
            "source IN ('profile_seed','cartesian','llm','suggest','observed')",
            name="ck_target_clusters_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
    )
    cluster_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(500), nullable=False)
    intent_code: Mapped[str] = mapped_column(String(30), nullable=False)
    cluster_type: Mapped[str] = mapped_column(String(30), nullable=False)
    quality_tier: Mapped[str] = mapped_column(String(20), nullable=False)

    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    seed_slots: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    is_brand: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_competitor_brand: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    expected_volume_tier: Mapped[str] = mapped_column(
        String(5), nullable=False, default="s"
    )
    business_relevance: Mapped[float] = mapped_column(
        Numeric(4, 3), nullable=False, default=0
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    queries = relationship(
        "TargetQuery",
        back_populates="cluster",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class TargetQuery(Base, TimestampMixin):
    """A query/keyword candidate attached to a cluster.

    Phase A does not populate this table — it is reserved for Phase C+
    when Yandex Suggest and observed queries are wired in.
    """

    __tablename__ = "target_queries"
    __table_args__ = (
        UniqueConstraint("cluster_id", "query_text", name="uq_target_queries_cluster_q"),
        Index("ix_target_queries_cluster", "cluster_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("target_clusters.id", ondelete="CASCADE"),
        nullable=False,
    )
    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    estimated_volume_tier: Mapped[str | None] = mapped_column(String(5), default="s")
    observed_search_query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_queries.id", ondelete="SET NULL"),
        nullable=True,
    )

    cluster = relationship("TargetCluster", back_populates="queries")
