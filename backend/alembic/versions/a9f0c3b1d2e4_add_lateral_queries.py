"""lateral_queries: LLM-generated adjacent query ideas (Block A roadmap).

Stores ideas the helper proposes weekly — queries that *aren't* yet in
Webmaster/Wordstat for the site but plausibly should be (lateral
expansion off business_truth × competitor brands × observed clusters).

Each row = one query idea per site. UPSERT semantics: re-running the
expander refreshes `relation`/`confidence`/`rationale`/`agent_run_id` for
rows still in `new` status, but never overwrites owner decisions
(`accepted`/`rejected`/`promoted`). Persistence helper enforces this.

Revision ID: a9f0c3b1d2e4
Revises: e8f9c1a2b4d6
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a9f0c3b1d2e4"
down_revision: Union[str, None] = "e8f9c1a2b4d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lateral_queries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
            comment="LLM run that last produced/refreshed this row; cost lives in agent_runs",
        ),
        sa.Column("query", sa.String(500), nullable=False),
        sa.Column(
            "query_norm",
            sa.String(500),
            nullable=False,
            comment="lower+stripped, used for cross-run dedup",
        ),
        sa.Column(
            "relation",
            sa.String(16),
            nullable=False,
            comment="direct | related | info | weak",
        ),
        sa.Column(
            "confidence",
            sa.Numeric(3, 2),
            nullable=False,
            server_default="0.50",
        ),
        sa.Column(
            "rationale",
            sa.Text,
            nullable=True,
            comment="Short LLM explanation — why this query is relevant to the business",
        ),
        sa.Column(
            "source_signal",
            sa.String(32),
            nullable=False,
            server_default="business_truth",
            comment="business_truth | competitor_serp | wordstat_related | composite",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="new",
            comment="new | accepted | rejected | promoted",
        ),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "relation IN ('direct','related','info','weak')",
            name="ck_lateral_queries_relation",
        ),
        sa.CheckConstraint(
            "status IN ('new','accepted','rejected','promoted')",
            name="ck_lateral_queries_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_lateral_queries_confidence_range",
        ),
        sa.UniqueConstraint(
            "site_id", "query_norm", name="uq_lateral_queries_site_norm",
        ),
    )

    op.create_index(
        "ix_lateral_queries_site_status_created",
        "lateral_queries",
        ["site_id", "status", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_lateral_queries_site_run",
        "lateral_queries",
        ["site_id", "agent_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lateral_queries_site_run", table_name="lateral_queries",
    )
    op.drop_index(
        "ix_lateral_queries_site_status_created", table_name="lateral_queries",
    )
    op.drop_table("lateral_queries")
