"""Activity feed + outcome tracking.

Revision ID: f7e8a9b0c1d2
Revises: e1a2b3c4d5f6
Create Date: 2026-04-21 16:00:00.000000

Two additions:

1. `analysis_events` — narrates what Celery is doing so owners see the
   platform actually working. One row per stage milestone (started /
   progress / done).

2. `outcome_snapshots` — owner marks a recommendation as applied; we
   store baseline metrics at that moment and, 14 days later, compare
   against fresh metrics to show the real-world effect.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7e8a9b0c1d2"
down_revision: Union[str, None] = "e1a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "site_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id"), nullable=False, index=True,
        ),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column(
            "ts", sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, index=True,
        ),
        sa.Column(
            "extra", postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}", nullable=False,
        ),
    )
    op.create_index(
        "ix_analysis_events_site_ts", "analysis_events", ["site_id", "ts"],
    )

    op.create_table(
        "outcome_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id"), nullable=False, index=True,
        ),
        # For priority recommendations this is priority_scores.id; for
        # growth opportunities it's the Opportunity.id (16-char hex)
        # — we don't FK it, just string-match against the source JSONB.
        sa.Column("recommendation_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),  # 'priority'|'opportunity'
        # Optional page URL used to slice metrics
        sa.Column("page_url", sa.String(2048), nullable=True),
        # Snapshot at the moment owner marked it done
        sa.Column("applied_at", sa.DateTime(), nullable=False),
        sa.Column(
            "baseline_metrics", postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}", nullable=False,
        ),
        # Filled 14 days later by scheduled task
        sa.Column("followup_at", sa.DateTime(), nullable=True),
        sa.Column(
            "followup_metrics", postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}", nullable=False,
        ),
        # Delta summary: {"impressions_pct": 42.5, "clicks_pct": 18, ...}
        sa.Column(
            "delta", postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}", nullable=False,
        ),
        sa.Column("note_ru", sa.String(1000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
    )
    op.create_index(
        "ix_outcome_snapshots_site_applied",
        "outcome_snapshots",
        ["site_id", "applied_at"],
    )
    op.create_index(
        "ix_outcome_snapshots_followup",
        "outcome_snapshots",
        ["followup_at"],
    )
    op.create_unique_constraint(
        "uq_outcome_rec_per_site",
        "outcome_snapshots",
        ["site_id", "recommendation_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_outcome_rec_per_site", "outcome_snapshots", type_="unique")
    op.drop_index("ix_outcome_snapshots_followup", table_name="outcome_snapshots")
    op.drop_index("ix_outcome_snapshots_site_applied", table_name="outcome_snapshots")
    op.drop_table("outcome_snapshots")
    op.drop_index("ix_analysis_events_site_ts", table_name="analysis_events")
    op.drop_table("analysis_events")
